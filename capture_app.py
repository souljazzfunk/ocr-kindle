#!/usr/bin/env python3
"""
Capture the books Cloud Reader cannot open ("Kindle App Is Required") from the Kindle for Mac app, into their book
folders, so the convert job (kc_run.py convert) converts and checks them the same way as every other book.

    KINDLE_APP_OK=1 venv/bin/python capture_app.py books.tsv

Laiken chose this on 2026-10-11 for the books the capture job parked as kindle_app_only, and only for them: every
other book still comes from the Cloud Reader extension in Chrome. The command needs KINDLE_APP_OK=1 (the guard hook
asks for it too). Each book is opened in the app, paged back to its start, captured with kindle2img.applescript into
<book folder>/pages/ and recorded as captured; the convert job picks it up while the app turns the next book. The run
stops at the first book the app does not open or capture, and the same command resumes there.

While a book is captured the Kindle window must stay in front, so the Mac's screen is busy until the run ends.
"""

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import NamedTuple

import ax
import capture_book
import book as books
import check_book
from channel import awake, set_aside

HERE = Path(__file__).resolve().parent
OK_ENV = 'KINDLE_APP_OK'
POLL_S = 0.3  # the element tree is read this often until what is waited for appears (Laiken, 2026-10-11 19:59:
# opening a book took minutes; fixed sleeps of 2-5 s stood at a dozen places)
RENDER_TRIES = 12  # clicks on the page, 2 s apart, while a book renders after it opens
PRESS_WAIT_S = 8  # the library after the first press of a cover, before the cover's centre is clicked instead
OPEN_WAIT_S = 45
# A book not yet on this Mac stays in the library on the first open; opening it again starts its download
# (Laiken, 2026-10-11 17:21 JST), which takes longer than a plain open.
DOWNLOAD_WAIT_S = 300
# The reader's footer, shown with its toolbar after a click on the page (probe of 2026-10-11 17:56).
# "Location N of M , P%" in most books; some use page numbers: "Page N , P%" on the slider, "Page N, Current
# Location" on the page's link, no "of" (Japan Travel Planner, probe of 2026-10-11 21:06).
LOCATION = re.compile(r'\b(?:Location|Page) (\d+)(?: of (\d+))?')
PROGRESS_BAR = 'Reading Progress Bar'
CLOSE_BOOK = 'Close Book'


def osascript(script, runner=subprocess.run):
    result = runner(['osascript', '-e', script], capture_output=True, text=True)
    return result.returncode, (result.stdout or '').strip(), (result.stderr or '').strip()


def read_tree(script=osascript):
    """The dump text: through the Accessibility API in-process where pyobjc has it (ms), else the AppleScript dump
    (114 s for 234 lines on 2026-10-11 20:02). A fake script in the tests is always used as given."""
    if script is osascript and ax.usable():
        out = ax.dump()
        if out is not None:
            return out
    return script(UI_DUMP)[1]


def elements(script=osascript):
    """The window's elements from the UI dump: dicts with role, subrole, title, description, value, position, size."""
    out = read_tree(script)
    found = []
    for line in out.splitlines():
        parts = line.split('|')
        if parts[0] == 'element' and len(parts) >= 8:
            found.append(dict(zip(('role', 'subrole', 'title', 'description', 'value', 'position', 'size'), parts[1:8])))
    return found


def comparable(text):
    """Letters and digits of any script, full-width forms folded: [a-z0-9] alone made every Japanese title an empty key
    that matched no cover (イスラム飲酒紀行, 2026-10-15 12:24: its cover was first in the library and not found)."""
    return ''.join(c for c in unicodedata.normalize('NFKC', text or '').lower() if c.isalnum())


def cover_of(title, found):
    """The library button of this book: its description starts with the title (then the author, New or RED)."""
    key = comparable(title)
    for e in found:
        if e['role'] == 'AXButton' and key and comparable(e['description']).startswith(key[:30]):
            return e
    return None


def tab_of(title, found):
    """The tab bar's button for the recently read book (an AXRadioButton named after it), or None."""
    key = comparable(title)
    for e in found:
        if e['role'] == 'AXRadioButton' and key and comparable(e['title'] or e['description']) == key:
            return e
    return None


def framed(e):
    return e is not None and e['size'] not in ('0,0', '')


def in_library(found):
    return any(e['title'] == 'Library Grid View' or e['description'] == 'Library Grid View' for e in found)


def _quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


PRESS_TITLED = '''
tell application "System Events" to tell process "Kindle"
    set elems to entire contents of window 1
    repeat with e in elems
        set e to contents of e
        try
            if role of e is "AXButton" and (title of e is {title} or description of e is {title}) then
                perform action "AXPress" of e
                return "pressed"
            end if
        end try
    end repeat
    return "none"
end tell
'''
PRESS_TEXT = PRESS_TITLED.replace('role of e is "AXButton"', 'role of e is "AXStaticText"')
CLICK_AT = '''
tell application "System Events" to tell process "Kindle"
    click at {{{x}, {y}}}
    return "clicked"
end tell
'''
CLICK_PAGE = '''
tell application "System Events" to tell process "Kindle"
    set {x, y} to position of window 1
    set {w, h} to size of window 1
    click at {x + (w div 2), y + (h div 2)}
    return "clicked"
end tell
'''


def dismiss_sheet(script=osascript, sleep=time.sleep):
    """Close the book-details sheet the reader opens over a book (probe of 2026-10-11 17:51: a Close button, the
    title, the author and Share, over the page). Returns what was done."""
    _, pressed, _ = script(PRESS_TITLED.format(title='"Close"'))
    if pressed != 'pressed':
        script('tell application "System Events" to tell process "Kindle" to key code 53')  # Escape
    return pressed


def reveal_chrome(script=osascript, sleep=time.sleep):
    """Click the middle of the page so the reader shows its toolbar and progress (the probe saw neither in the dump
    taken right after the open; the footer showed only "1%")."""
    _, out, err = script(CLICK_PAGE)
    return out or err


def wait_for(ready, script=osascript, sleep=time.sleep, clock=time.monotonic, timeout=5.0, found=None):
    """Read the app's elements every POLL_S until ready(found) or the timeout; returns the last reading."""
    found = elements(script) if found is None else found
    deadline = clock() + timeout
    while not ready(found) and clock() < deadline:
        sleep(POLL_S)
        found = elements(script)
    return found


def window_frame(script=osascript):
    """The Kindle window's (x, y, w, h) from the dump's window line, or None."""
    for line in read_tree(script).splitlines():
        parts = line.split('|')
        if parts[0] == 'window' and len(parts) >= 4:
            try:
                x, y = (int(v) for v in parts[2].split(','))
                w, h = (int(v) for v in parts[3].split(','))
            except ValueError:
                continue
            return x, y, w, h
    return None


TOP_HOVER_PX = 60  # below the window's top edge: over the toolbar's row (Close Book at y = top + 28 on 2026-10-11 20:36)


def hover(x, y, api=None):
    """Move the pointer (no click) to the screen point through Quartz. Returns what was done, or why not."""
    try:
        import Quartz as api_module
    except ImportError:
        return 'no Quartz'
    api = api or api_module
    event = api.CGEventCreateMouseEvent(None, api.kCGEventMouseMoved, (x, y), 0)
    api.CGEventPost(api.kCGHIDEventTap, event)
    return f'moved to {x},{y}'


def hover_window(script=osascript, where='top', move=hover):
    """Move the pointer over the reader: 'top' (the toolbar's row) or 'page' (the middle). In Kindle 7.40 the toolbar
    appears when the pointer rests near the top edge and goes once it leaves (Sri Lanka, 2026-10-11 20:36: four clicks
    on the page brought no toolbar; one pointer move to the top did), so this comes before any click."""
    frame = window_frame(script)
    if frame is None:
        return 'no window'
    x, y, w, h = frame
    return move(x + w // 2, y + (TOP_HOVER_PX if where == 'top' else h // 2))


PRESS_COVER = '''
tell application "System Events" to tell process "Kindle"
    set elems to entire contents of window 1
    repeat with e in elems
        set e to contents of e
        try
            if role of e is "AXButton" and description of e starts with {title} then
                perform action "AXPress" of e
                return "pressed"
            end if
        end try
    end repeat
    return "none"
end tell
'''
PRESS_TAB = '''
tell application "System Events" to tell process "Kindle"
    set elems to entire contents of window 1
    repeat with e in elems
        set e to contents of e
        try
            if role of e is "AXRadioButton" and (title of e is {title} or description of e is {title}) then
                perform action "AXPress" of e
                return "pressed"
            end if
        end try
    end repeat
    return "none"
end tell
'''
# The title is pasted, not set as the field's value: the value set through AX showed "Clear text" and left the grid
# unfiltered (保守とは何だろうか, 2026-10-15 22:09), and keystroke cannot type Japanese through the input method.
SEARCH = '''
tell application "System Events" to tell process "Kindle"
    set elems to entire contents of window 1
    repeat with e in elems
        set e to contents of e
        try
            if role of e is "AXTextField" and subrole of e is "AXSearchField" then
                set the clipboard to {title}
                set focused of e to true
                set {{x, y}} to position of e
                set {{w, h}} to size of e
                click at {{x + (w div 2), y + (h div 2)}}
                delay 0.3
                keystroke "a" using command down
                keystroke "v" using command down
                delay 0.5
                keystroke return
                return "searched"
            end if
        end try
    end repeat
    return "none"
end tell
'''


def open_book(asin, title, script=osascript, run=capture_book.run, clock=time.monotonic, sleep=time.sleep):
    """Open the book from the library by pressing its cover, searching for it first when it is not on screen, and
    wait until the library grid is gone. A cover not yet downloaded starts its download on the press (Laiken,
    2026-10-11 17:21 JST); it is pressed once more and waited for. Returns '' or why it could not.

    Written from the probe of 2026-10-11 17:46: the new app's kindle:// URL leaves the library in front; each cover is
    an AXButton whose description starts with the title and whose value says Book Downloaded or Book Not Downloaded.
    """
    bring_front(script, sleep)
    found, problem = ensure_library(script, sleep, clock)
    if problem:
        return problem
    has_cover = lambda f: cover_of(title, f) is not None
    found = wait_for(has_cover, script, sleep, clock, found=found)
    if cover_of(title, found) is None:
        _, searched, _ = script(SEARCH.format(title=_quote(title)))
        found = wait_for(has_cover, script, sleep, clock)
        if cover_of(title, found) is None:
            return f'no cover for this title in the Kindle library (search: {searched}); it shows: ' + describe(found)
    # Two presses at most: the second is for a download that has not started (Laiken, 17:21), or for a first press
    # that only focused the window (run of 18:12: Kindle was not frontmost, the library stayed).
    # The second press on a downloaded book is a real click on the cover's centre: in the runs of 2026-10-11 18:12
    # and 18:24, right after Close Book, AXPress on the cover left the library on screen with Kindle in front.
    # The recently read book's cover reports a 0x0 frame (probe of 2026-10-11 18:42: "0,1152|0,0" while the other
    # covers had 122x183), so neither AXPress nor a click at its centre opens it; that book is opened through the
    # tab bar's button named after it, which has a real frame.
    presses = []
    while len(presses) < 2:
        presses.append(bring_front(script, sleep))
        cover = cover_of(title, found)
        target = cover if framed(cover) else tab_of(title, found) or cover
        if presses[1:] and framed(target) and 'Not Downloaded' not in cover['value']:
            x, y = (int(v) for v in target['position'].split(','))
            w, h = (int(v) for v in target['size'].split(','))
            script(CLICK_AT.format(x=x + w // 2, y=y + h // 2))
        elif target['role'] == 'AXRadioButton':
            script(PRESS_TAB.format(title=_quote(title)))
        else:
            script(PRESS_COVER.format(title=_quote(title)))
        # The first press is given a short wait: right after Close Book it can fail (runs of 2026-10-11 18:12 and
        # 18:24), and the click that follows opens the book; a download gets the long wait.
        wait = (DOWNLOAD_WAIT_S if len(presses) == 2 and cover and 'Not Downloaded' in cover['value']
                else OPEN_WAIT_S if len(presses) == 2 else PRESS_WAIT_S)
        found = wait_for(lambda f: not in_library(f), script, sleep, clock, timeout=wait)
        if not in_library(found):
            return ''
        cover = cover_of(title, found)
        if cover is None:
            break
    return (f'the Kindle app still shows its library {OPEN_WAIT_S} s after pressing the cover of this book'
            + (' (and after pressing it again for its download)' if cover and 'Not Downloaded' in cover['value'] else '')
            + (' (and after clicking its centre)' if cover and 'Not Downloaded' not in cover['value'] and len(presses) == 2 else '')
            + '; the cover says: ' + (f"{cover['value']} at {cover['position']} {cover['size']}" if cover else 'gone')
            + (f"; the tab bar's button at {tab_of(title, found)['position']} {tab_of(title, found)['size']}" if tab_of(title, found) else '; no tab-bar button for it')
            + '; frontmost before the presses: ' + ', '.join(presses))


def describe(found):
    """A short account of what is on screen, for an error message."""
    names = [e['title'] or e['description'] for e in found if e['title'] or e['description']]
    return ', '.join(names[:12]) + (' …' if len(names) > 12 else '')


def numbers_shown(found):
    """The texts on screen that hold a digit or a percent, for an error when no footer form is read: the first
    Japanese book (イスラム飲酒紀行, 2026-10-15 11:31) failed with "footer: no location" and nothing to fix it from."""
    texts = []
    for e in found:
        for text in (e['title'], e['description'], e['value']):
            if re.search(r'[0-9０-９%％]', text) and text not in texts:
                texts.append(text)
    return '; '.join(texts[:12]) or 'none'


def chrome_shown(found):
    """The toolbar is in the tree: its Close Book button, or the Reading Progress Bar. The reader's controls carry
    their names in the description, not the title (dump of 2026-10-11 18:04), and the run of 18:09 clicked the shown
    toolbar away three times because the title alone was read. The slider was the only sign until the run of 20:15:
    the Accessibility API's tree (ax.py) listed Close Book ... Location 1 of 2344 but no slider, and the book was
    refused as "no toolbar"; so the Close Book button counts too."""
    return any((e['role'] == 'AXSlider' and PROGRESS_BAR in (e['title'], e['description']))
               or (e['role'] == 'AXButton' and CLOSE_BOOK in (e['title'], e['description'])) for e in found)


class Reading(NamedTuple):
    """How a book turns: kindle2img's direction, the key codes that page back and on, and which bare number on the
    footer's knob row is the position (its index, left to right)."""
    name: str
    back: int
    next: int
    pick: int


LTR = Reading('ltr', 123, 124, 0)


def key(code):
    return f'tell application "System Events" to tell process "Kindle" to key code {code}'


def location_of(found, reading=LTR):
    """(position, of) from the footer's text, else from the bare number over the knob, or None."""
    return footer_text(found) or knob_label(found, reading.pick)


def footer_text(found):
    """(position, of) from the footer's "Location N of M , P%" or a page-numbered book's "Page N , P%", or None. M
    came back as 0 in the probe of 2026-10-11 18:04 ("Location 1 of 0 , 1%") and is absent with page numbers, so
    only N says whether the start was reached."""
    partial = None  # "Location N , P%" (the slider) or "Page N": kept only when no "N of M" is in the tree
    for e in found:
        m = LOCATION.search(e['description']) or LOCATION.search(e['value']) or LOCATION.search(e['title'])
        if m and m.group(2):
            return int(m.group(1)), int(m.group(2))
        if m and partial is None:
            partial = (int(m.group(1)), 0)
    return partial


BIRDS_EYE = "Bird's Eye View"


def knob_label(found, pick=0):
    """(N, 0) from a footer that shows its position as a bare number over the slider's knob, or None. A Japanese book
    (イスラム飲酒紀行, recorded 2026-10-15 12:24) has no "Location" or "Page" text and no slider in the tree: only "1"
    at the left of the footer row, beside the Bird's Eye View button, and "688", a mark further along that stayed put
    while → and ← moved the first through 1, 2 and 4. So the leftmost bare number on that row is the position, unless
    learn_reading found another one moving (a vertical book's stands at the right)."""
    row = knob_row(found)
    return (row[pick], 0) if row else None


def knob_row(found):
    """The bare numbers on the footer's row beside the Bird's Eye View button, left to right."""
    anchor = next((e for e in found if e['role'] == 'AXButton' and BIRDS_EYE in (e['title'], e['description'])), None)
    if anchor is None:
        return []
    _, y, _, h = _frame(anchor)
    row = [e for e in found if e['role'] == 'AXStaticText' and re.fullmatch(r'\d+', e['title'] or e['description'] or '')
           and y - h <= _frame(e)[1] <= y + h]
    return [int(e['title'] or e['description']) for e in sorted(row, key=lambda e: _frame(e)[0])]


HOVER_WAIT_S = 3  # after each pointer move to the top edge, before the move is repeated


def show_chrome(script=osascript, sleep=time.sleep, clock=time.monotonic, tries=3, found=None, move=None):
    """Bring the toolbar into the element tree by resting the pointer on the window's top edge, with a fresh move
    each try (away to the page, then to the top). Never by a click: a click on the page turns the page (Sri Lanka,
    2026-10-11 20:40: the clicks of a test moved the book from 3% to 30%), and in a two-column spread the window's
    centre is the gutter where a click does nothing (Laiken 20:39). The pointer brought the toolbar at once at
    20:36 but not in the run at 20:40 (one move, 14 s), so the move is repeated. The clicks remain only for a
    script without a pointer (the fakes in the tests). Returns what the app shows."""
    found = elements(script) if found is None else found
    move = move or (hover if script is osascript else None)
    for _ in range(tries):
        if chrome_shown(found):
            break
        if move:
            hover_window(script, 'page', move)
            hover_window(script, 'top', move)
            found = wait_for(chrome_shown, script, sleep, clock, timeout=HOVER_WAIT_S)
        else:
            reveal_chrome(script, sleep)
            found = wait_for(chrome_shown, script, sleep, clock, timeout=2)
    return found


def hide_chrome(script=osascript, sleep=time.sleep, clock=time.monotonic, move=None):
    """Take the toolbar away again: the pointer back to the page's middle, else the click that toggles it."""
    move = move or (hover if script is osascript else None)
    if move:
        hover_window(script, 'page', move)
        found = wait_for(lambda f: not chrome_shown(f), script, sleep, clock, timeout=2)
        if not chrome_shown(found):
            return found
    reveal_chrome(script, sleep)
    return wait_for(lambda f: not chrome_shown(f), script, sleep, clock, timeout=2)


FRONTMOST = ('tell application "Amazon Kindle" to activate\n'
             'tell application "System Events" to get name of first process whose frontmost is true')


def bring_front(script=osascript, sleep=time.sleep):
    """Activate Kindle and say which process is frontmost afterwards. In the run of 2026-10-11 18:12 the cover was
    pressed while "Claude" was frontmost and the library stayed, so every press is preceded by this."""
    _, front, err = script(FRONTMOST)
    sleep(0.5)
    return front or err


def dismiss_overlays(script=osascript, sleep=time.sleep):
    """Close a menu or sheet over the reader: Escape twice, then the sheet's Close button if one is in the tree."""
    for _ in range(2):
        script('tell application "System Events" to tell process "Kindle" to key code 53')  # Escape
        sleep(1)
    return dismiss_sheet(script, sleep)


def ensure_library(script=osascript, sleep=time.sleep, clock=time.monotonic):
    """Bring the app to its library: a book left open (the app reopens into the last book, 2026-10-11 17:56) is closed
    with the reader's Close Book button. File > Close quits the app, so it is never used.
    Returns (what the app shows, '' or why the library did not come)."""
    found = elements(script)
    if in_library(found):
        return found, ''
    bring_front(script, sleep)
    found = show_chrome(script, sleep, clock, found=found)
    if not chrome_shown(found):
        # The end of a book leaves an end-of-book sheet (rating, recommend, follow the author), and a share menu
        # had opened over it (run of 2026-10-11 18:59, the book read to 99%); both hide the toolbar.
        dismiss_overlays(script, sleep)
        found = show_chrome(script, sleep, clock)
    if not chrome_shown(found):
        return found, 'the Kindle app shows neither its library nor the reader toolbar after clicks on the page; it shows: ' + describe(found)
    _, pressed, _ = script(PRESS_TITLED.format(title='"Close Book"'))
    found = wait_for(in_library, script, sleep, clock, timeout=10)
    if in_library(found):
        return found, ''
    return found, f'the Kindle app is not showing its library (Close Book: {pressed}); it shows: ' + describe(found)


def _frame(e):
    x, y = (int(v) for v in e['position'].split(','))
    w, h = (int(v) for v in e['size'].split(','))
    return x, y, w, h


MENU_CLOSE = 'Close menu'


def menu_open(found):
    return any(MENU_CLOSE in (e['title'], e['description']) for e in found)


TOC_ROW_MIN_W = 200  # a panel row spans the panel (473 px on 2026-10-11 20:45); the toolbar's buttons are 40 px and
# the footer's "Location 185" button 100 px (fixtures/kindle/applescript-toc-bolivia.txt)


def toc_entries(found):
    """The Table of Contents panel's entries after its "Close menu" X (the title and author stand above the X):
    static texts (Bolivia, 19:20, fixtures/kindle/applescript-toc-bolivia.txt) or wide buttons named after the
    chapter (Sri Lanka, 2026-10-11 20:45: AXButton rows 473 px wide in an untitled group), footer lines aside."""
    closes = [i for i, e in enumerate(found) if MENU_CLOSE in (e['title'], e['description'])]
    if not closes:
        return []
    rows = []
    for e in found[closes[-1] + 1:]:
        name = e['description'] or e['title']
        if not name or name.startswith(('Location ', 'Page ')) or LOCATION.search(e['value']):
            continue
        if e['role'] == 'AXStaticText' or (e['role'] == 'AXButton' and _frame(e)[2] >= TOC_ROW_MIN_W):
            rows.append(e)
    return rows


TOC_SCROLL_LINES = 40
TOC_SCROLLS = 20
TOC_SETTLE_S = 0.5  # after a scroll, before the rows are read: the panel animates (Japan Travel Planner, 20:58)


def toc_top(script, sleep, clock, found, scroll):
    """Scroll the panel to its top: it opens at the current chapter (Sri Lanka at 30%, 2026-10-11 20:45, showed
    "After One Month in Sri Lanka" first; 40 lines of scroll-wheel up over the panel brought "Cover" to the top).
    The panel is read only once its rows stand still (it slides in), and the scrolling stops only once the topmost
    row has not changed over two scrolls: one read right after a scroll saw no change while the panel was still
    moving, and Sri Lanka started at "Beginning" (one row short) and Japan Travel Planner at section 1.5 (20:58).
    Returns the panel as last read."""
    def top_of(f):
        rows = toc_entries(f)
        return (rows[0]['description'] or rows[0]['title'], rows[0]['position']) if rows else None

    last = top_of(found)
    found = wait_for(lambda f: top_of(f) == last and f is not found, script, sleep, clock, timeout=2)
    unchanged = 0
    for _ in range(TOC_SCROLLS):
        rows = toc_entries(found)
        if not rows:
            return found
        x, y, w, h = _frame(rows[0])
        before = top_of(found)
        scroll(x + w // 2, y + 4 * h, TOC_SCROLL_LINES)
        sleep(TOC_SETTLE_S)
        found = elements(script)
        unchanged = unchanged + 1 if top_of(found) == before else 0
        if unchanged >= 2:
            return found
    return found


def toc_first(script=osascript, sleep=time.sleep, clock=time.monotonic, scroll=None, press=None):
    """Open the Table of Contents, scroll it to its top and press its topmost entry. Probe of 2026-10-11 19:20
    (Bolivia): the panel slides in at the left with the title (AXHeading), the author, a "Close menu" X, then the
    entries ("Cover", "Beginning") as AXStaticText; Sri Lanka (20:45) lists them as AXButton rows. Laiken, 19:26:
    any topmost entry leads to the start, not only "Cover". Checked on the Mac at 19:26: AXPress on the "Cover"
    text moved Bolivia from Location 185 to Location 1; at 20:45 AXPress through the Accessibility API on the
    "Cover" button put Sri Lanka on its cover while AppleScript did not find the button. Synthetic mouse events
    are unreliable in this app, so a click at the row's centre comes only when the presses are refused."""
    _, pressed, _ = script(PRESS_TITLED.format(title='"Table of Contents"'))
    if pressed != 'pressed':
        return 'no Table of Contents button'
    found = wait_for(menu_open, script, sleep, clock)
    if script is osascript:
        scroll = scroll or ax.scroll
        press = press or ax.press
    if scroll:
        found = toc_top(script, sleep, clock, found, scroll)
    rows = toc_entries(found)
    if not rows:
        script(PRESS_TITLED.format(title=_quote(MENU_CLOSE)))
        return 'no entry in the table of contents'
    name = rows[0]['description'] or rows[0]['title']
    if press and press(rows[0]['role'], name):
        how = f'pressed "{name}" at the top of the table of contents'
    else:
        _, pressed, _ = script((PRESS_TITLED if rows[0]['role'] == 'AXButton' else PRESS_TEXT).format(title=_quote(name)))
        if pressed == 'pressed':
            how = f'pressed "{name}" at the top of the table of contents'
        else:
            x, y, w, h = _frame(rows[0])
            script(CLICK_AT.format(x=x + w // 2, y=y + h // 2))
            how = f'clicked "{name}" at the top of the table of contents'
    wait_for(lambda f: not menu_open(f), script, sleep, clock)
    return how


def learn_reading(script, sleep, clock, found):
    """Which way a book turns and which knob number is its position, for a footer that shows only bare numbers: press
    ← once and compare the row, then → if nothing moved. A vertical Japanese book turns on with ← and stands its
    position at the row's right (外国語学習に成功する人 and 英語独習法, 2026-10-15 22:09: "paged back 20 times and the
    footer stayed at Location 699", the mark). A press that turned the book on is undone. Returns the reading and the
    footer as read last; LTR and the footer given when nothing moved."""
    before = knob_row(found)
    for code in (123, 124):
        bring_front(script, sleep)
        hide_chrome(script, sleep, clock)
        script(key(code))
        sleep(0.5)
        shown = show_chrome(script, sleep, clock)
        after = knob_row(shown)
        moved = [i for i, (a, b) in enumerate(zip(before, after)) if a != b] if len(after) == len(before) else []
        if len(before) == 1 and len(after) == 2 and before[0] in after:
            # The knob left the mark it stood over (保守とは何だろうか at its start, 2026-10-15 23:40): the number that
            # is not the old one is the knob.
            moved = [after.index(before[0]) ^ 1]
            before = [before[0]] * 2
        if not moved:
            continue
        i = moved[0]
        on = after[i] > before[i]
        # A lone number is the knob over the mark (保守とは何だろうか, 2026-10-15 23:19: "knob number 1 of 1", then the
        # mark came back at the left and was read as the position): its side follows the way the book turns.
        pick = (0 if on == (code == 124) else -1) if len(after) == 1 else i if i == 0 else i - len(after)
        reading = LTR._replace(pick=pick) if on == (code == 124) else Reading('rtl', 124, 123, pick)
        if on:
            bring_front(script, sleep)
            hide_chrome(script, sleep, clock)
            script(key(reading.back))
            sleep(0.5)
            shown = show_chrome(script, sleep, clock)
        print(f'reading: {reading.name}, the position is knob number {i + 1} of {len(after)}', flush=True)
        return reading, shown
    return LTR, found


def page_back(script=osascript, sleep=time.sleep, clock=time.monotonic, batch=20, cap=3000, reading=LTR):
    """Press ← until the footer reads Location 1 or stops falling. Laiken, 2026-10-11 19:27: when no reader tool
    works, page back one screen at a time rather than stop. The toolbar is hidden before each batch so the keys
    reach the page, then shown again to read the footer."""
    found = show_chrome(script, sleep, clock)
    where = location_of(found, reading)
    if where is None:
        return 'no location in the footer to page back from'
    presses = 0
    while where[0] > 1 and presses < cap:
        bring_front(script, sleep)
        if chrome_shown(found):
            hide_chrome(script, sleep, clock)
        for _ in range(batch):
            script(key(reading.back))
            sleep(0.15)
            presses += 1
        found = show_chrome(script, sleep, clock)
        now = location_of(found, reading)
        if now is None or now[0] >= where[0]:
            return f'paged back {presses} times and the footer stayed at Location {where[0]}'
        where = now
    return f'paged back {presses} times'


def go_to_start(script=osascript, sleep=time.sleep, clock=time.monotonic, scroll=None, press=None, shown=None):
    """Back to the start of the open book: close the details sheet, show the toolbar, open the Table of Contents and
    press its topmost entry, else page back with ← until the start; read the footer's "Location N of M", then hide
    the toolbar again. Returns '' or why it could not, and puts the footer read at the start into shown['start'].
    Written from the probes of 2026-10-11 17:56 and 19:20.
    The Reading Progress Bar is not used: writing its value, clicking its track and dragging its knob all left the
    book where it was (runs of 2026-10-11 19:02 and 19:11)."""
    began = clock()
    dismiss_sheet(script, sleep)
    # A book can take a while to render after it opens: Iceland showed a blank cream page for over 30 s on
    # 2026-10-11 20:11 and three clicks over 6 s found no toolbar; so the clicks go on for about half a minute.
    found = show_chrome(script, sleep, clock, tries=RENDER_TRIES)
    if not chrome_shown(found):
        return 'the reader shows no toolbar after clicks on the page; it shows: ' + describe(found)
    reading = LTR
    if footer_text(found) is None and knob_row(found):
        reading, found = learn_reading(script, sleep, clock, found)
    where = location_of(found, reading)
    tried = []
    for move in (toc_first, page_back):
        if where is not None and where[0] <= 1:
            break
        bring_front(script, sleep)
        started = clock()
        how = move(script, sleep, clock, scroll=scroll, press=press) if move is toc_first else move(script, sleep, clock, reading=reading)
        found = show_chrome(script, sleep, clock)
        where = location_of(found, reading)
        tried.append(f'{how} ({clock() - started:.0f} s)')
        # A pressed entry with no readable footer is not taken as the start: that rule (#81) let Japan Travel
        # Planner pass at section 1.5 (2026-10-11 20:58). The cover does show the footer (Sri Lanka, 20:51).
    if where is None or where[0] > 1:
        return (f'could not go to the start (tried: {", ".join(tried)}, footer: '
                f'{"Location %d of %d" % where if where else "no location; numbers on screen: " + numbers_shown(found)})')
    print(f'start: Location {where[0]} after {clock() - began:.0f} s: {", ".join(tried) or "not needed"}', flush=True)
    if shown is not None:
        shown['start'] = f'Location {where[0]}' + (f' of {where[1]}' if where[1] else '')
        shown['reading'] = reading
    bring_front(script, sleep)
    if chrome_shown(hide_chrome(script, sleep, clock)):
        return 'the toolbar stayed over the page after a click to hide it'
    return ''


END_SHEET = ('Overall rating', 'Recommend this book', 'Review this book')


def end_sheet_shown(found):
    """Kindle's end-of-book sheet (rating stars, "Recommend this book") over the last page. Recorded on Japan Travel
    Planner, 2026-10-11 20:59: the capture's → keys had moved the focus onto the first star."""
    return any(any(mark in (e['title'], e['description'], e['value']) or (e['title'] or e['description']).startswith(mark)
                   for mark in END_SHEET) for e in found)


SHEET_LATE_S = 3  # after the script ends, before the tree is read again for a sheet that came up late
END_PRESSES = 5


def footer_end(script=osascript, sleep=time.sleep, clock=time.monotonic, reading=LTR):
    """The end as the footer shows it: read it, press → a few times and read it again. A footer that stays put is the
    last page, said as "Location N of M, where → moved the footer no further"; one that moves, or none read, is ''.
    The app's footer gives no usable total ("of 0", a bare knob number), so only the stop says where the book ends."""
    before = location_of(show_chrome(script, sleep, clock), reading)
    bring_front(script, sleep)
    hide_chrome(script, sleep, clock)
    for _ in range(END_PRESSES):
        script(key(reading.next))
        sleep(0.3)
    after = location_of(show_chrome(script, sleep, clock), reading)
    hide_chrome(script, sleep, clock)
    if before is None or after is None or after[0] != before[0]:
        return ''
    return f'Location {after[0]}' + (f' of {after[1]}' if after[1] else '') + f', where {"→" if reading.next == 124 else "←"} moved the footer no further'


def capture(folder, reading=LTR, script_path=HERE / 'kindle2img.applescript', run=capture_book.run, script=osascript,
            sleep=time.sleep, clock=time.monotonic):
    """Page through the book with kindle2img.applescript. When the end-of-book sheet is up afterwards, the last image
    is that sheet, not a page, so it is dropped, and the sheet is closed with Escape (never Enter or space: the
    focus sits on the rating's stars). Returns the pages and the evidence of the end, or '' when there is none.
    No sheet came up at the end of アジャイルデータモデリング or イスラム飲酒紀行 (2026-10-15, F170), nor once a
    second read later: then the footer that → no longer moves is the evidence."""
    run(['osascript', str(script_path), reading.name, str(folder)])
    pages = sorted(folder.glob('page_*.png'))
    if not pages:
        return pages, ''
    ended = end_sheet_shown(elements(script))
    if not ended:
        sleep(SHEET_LATE_S)
        ended = end_sheet_shown(elements(script))
    if ended:
        pages.pop().unlink()
        dismiss_overlays(script, sleep)
        print(f'end: the end-of-book sheet was closed and its image dropped; {len(pages)} pages', flush=True)
        return pages, 'the end-of-book sheet'
    end = footer_end(script, sleep, clock, reading)
    print(f'end: {end or "not shown: no sheet, and the footer still moved or was not read"}; {len(pages)} pages', flush=True)
    return pages, end


# What the app's window holds, read through System Events: role, title, description and value of every element, plus
# the menu bar. The elements are resolved first (`set elems to entire contents of w`, `contents of e`): read through
# the `repeat with e in entire contents of w` reference chain, every property failed with "Can't make «class role»
# ... into type Unicode text" (2026-10-11), which looked like an opaque window and was only the script. The new Kindle app (7.40, "Amazon Kindle") shows no book title in its window name and has no Go
# menu, so the capture steps are written from this dump rather than guessed (Laiken chose the new app, 2026-10-11).
UI_DUMP = '''
on txt(x)
    if x is missing value then return ""
    try
        set AppleScript's text item delimiters to ","
        return x as text
    on error
        return "?"
    end try
end txt
tell application "System Events" to tell process "Kindle"
    set out to "frontmost=" & (frontmost as text) & linefeed
    set out to out & "windows=" & (count of windows) & linefeed
    set wins to every window
    repeat with w in wins
        set w to contents of w
        set out to out & "window|" & my txt(name of w) & "|" & my txt(position of w) & "|" & my txt(size of w) & linefeed
        set elems to entire contents of w
        repeat with e in elems
            set e to contents of e
            try
                set out to out & "element|" & my txt(role of e) & "|" & my txt(subrole of e) & "|" & my txt(title of e) & "|" & my txt(description of e) & "|" & my txt(value of e) & "|" & my txt(position of e) & "|" & my txt(size of e) & linefeed
            on error m
                set out to out & "element|?|" & m & linefeed
            end try
        end repeat
    end repeat
    repeat with b in menu bar items of menu bar 1
        set b to contents of b
        set out to out & "menu|" & my txt(name of b) & linefeed
        try
            repeat with i in menu items of menu 1 of b
                set i to contents of i
                set out to out & "item|" & my txt(name of b) & "|" & my txt(name of i) & "|" & my txt(enabled of i) & linefeed
            end repeat
        end try
    end repeat
    return out
end tell
'''
EBOOKS = Path.home() / 'Library/Containers/com.amazon.Lassen/Data/Library/eBooks'


def probe(asin, title, folder, script=osascript, run=capture_book.run, sleep=time.sleep, ebooks=EBOOKS,
          clock=time.monotonic):
    """Open the book in the app and record what the app shows: a screenshot, the UI dump before and after the open,
    and whether the book is downloaded. Returns the folder; the files are what a fix is written from."""
    folder.mkdir(parents=True, exist_ok=True)
    script('tell application "Amazon Kindle" to activate')
    sleep(3)
    _, before, err = script(UI_DUMP)
    (folder / 'ui-library.txt').write_text(before or err, encoding='utf-8')
    # The kindle:// URL does not open a book in the new app (probe of 2026-10-11 17:46); the cover is pressed instead.
    problem = open_book(asin, title, script=script, run=run, sleep=sleep, clock=clock)
    (folder / 'open.txt').write_text(problem or 'opened', encoding='utf-8')
    sleep(3)
    # One file per display: with one file screencapture records only the main display, and on 2026-10-11 the Kindle
    # window was on the other one (the Window menu offered "Move to BenQ EL2870U"), so screen.png showed no Kindle.
    run(['screencapture', '-x', *(str(folder / f'screen-{n}.png') for n in (1, 2, 3))])
    _, after, err = script(UI_DUMP)
    (folder / 'ui-book.txt').write_text(after or err, encoding='utf-8')
    # The reader as it is used: the details sheet closed, then the page clicked once for its toolbar.
    notes = ['sheet: ' + dismiss_sheet(script, sleep)]
    sleep(2)
    _, reader, err = script(UI_DUMP)
    (folder / 'ui-reader.txt').write_text(reader or err, encoding='utf-8')
    notes.append('page: ' + reveal_chrome(script, sleep))
    sleep(2)
    run(['screencapture', '-x', *(str(folder / f'chrome-{n}.png') for n in (1, 2, 3))])
    _, chrome, err = script(UI_DUMP)
    (folder / 'ui-chrome.txt').write_text(chrome or err, encoding='utf-8')
    # The ways to the start that do not go through the slider (the slider ignored a value, a click and a drag in the
    # runs of 2026-10-11 19:02 and 19:11): what the Table of Contents and More options buttons open.
    for name, button in (('toc', 'Table of Contents'), ('more', 'More options')):
        show_chrome(script, sleep, clock)
        _, pressed, _ = script(PRESS_TITLED.format(title=_quote(button)))
        sleep(2)
        run(['screencapture', '-x', *(str(folder / f'{name}-{n}.png') for n in (1, 2, 3))])
        _, opened, err = script(UI_DUMP)
        (folder / f'ui-{name}.txt').write_text(opened or err, encoding='utf-8')
        notes.append(f'{button}: {pressed}')
        dismiss_overlays(script, sleep)
    (folder / 'open.txt').write_text('\n'.join([problem or 'opened', *notes]), encoding='utf-8')
    downloaded = sorted(p.name for p in ebooks.glob(f'*{asin}*')) if ebooks.exists() else []
    (folder / 'probe.json').write_text(json.dumps({'asin': asin, 'downloaded': downloaded, 'ebooks': str(ebooks),
                                                   'at': time.strftime('%Y-%m-%dT%H:%M:%S')}, indent=1), encoding='utf-8')
    return folder


def app_pages_aside(folder):
    """Whether an earlier capture's pages set aside in folder came from the app: the extension writes positions.json
    beside every capture, the app none."""
    return any(list(old.glob('page_*.png')) and not (old / check_book.POSITIONS).exists()
               for old in folder.glob('pages (before *)'))


def app_books(listed):
    """The listed books Cloud Reader refused as app-only, or that an earlier app run parked: the same command resumes
    at a book the app did not open (on 2026-10-11 a resume skipped For 91 Days In Tokyo once its record said app_open).
    An app capture the checks refused is captured again here, since the capture job's extension cannot open it, and so
    is one the extension refused after moving its app pages aside (F169: イスラム飲酒紀行, 2026-10-15, before F167)."""
    def app_only(book):
        failure = book.failure('capture') or {}
        state = book.state()
        again = isinstance(state, books.Parked) and state.stage != 'capture' and \
            (book.record('capture') or {}).get('via') == 'kindle_app'
        return capture_book.APP_ONLY in failure.get('checks', []) or failure.get('via') == 'kindle_app' or again or \
            ('capture_refused' in failure.get('checks', []) and app_pages_aside(book.folder))
    return [book for book in listed if app_only(book)]


def capture_pages(asin, title, folder, open_=open_book, start=go_to_start, take=capture):
    """Open one book in the app, page it to its start and capture it into folder: (folder, None), or (None, the failure).
    The app writes no positions.json, so capture.json keeps what it showed at the start and the end for check_book
    (F167: three app captures of 2026-10-15 failed "start not recorded" and "end not recorded")."""
    if list(folder.glob('page_*.png')):
        # kindle2img.applescript numbers on from the highest page, so pages already here would be captured twice.
        return None, {'asin': asin, 'blocking_failures': ['app_folder'], 'warnings': [],
                      'error': f'{folder} already holds page images; move them away to capture this book again'}
    shown = {}
    for step in (lambda: open_(asin, title), lambda: start(shown=shown)):
        problem = step()
        if problem:
            return None, {'asin': asin, 'blocking_failures': ['app_open'], 'warnings': [], 'error': problem}
    folder.mkdir(parents=True, exist_ok=True)
    pages, ended = take(folder, shown.get('reading', LTR))
    if not pages:
        return None, {'asin': asin, 'blocking_failures': ['app_capture'], 'warnings': [], 'error': 'no page was captured'}
    (folder / check_book.CAPTURE).write_text(json.dumps({'via': 'kindle_app', 'start': shown.get('start', ''),
                                                        'end': ended, 'stopped': ''}),
                                            encoding='utf-8')
    return folder, None


def screen_locked(run=subprocess.run):
    """Whether the console session says its screen is locked. macOS says so while the display is asleep, too: then the
    app shows only "Kindle, Kindle, ..." to the Accessibility API and every book would be parked as app_open (F172)."""
    try:
        done = run(['ioreg', '-n', 'Root', '-d1', '-a'], capture_output=True)
        users = plistlib.loads(done.stdout).get('IOConsoleUsers', []) if done.returncode == 0 else []
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False  # not a Mac
    return any(u.get('CGSSessionScreenIsLocked') for u in users)


def wake_display(run=subprocess.run):
    """Turn the display on as a keypress would (caffeinate -u), and give the session a moment to say it is unlocked."""
    try:
        run(['caffeinate', '-u', '-t', '2'])
    except OSError:
        pass  # not a Mac


def main(argv=None, env=os.environ, capture=capture_pages, locked=screen_locked, wake=wake_display):
    parser = argparse.ArgumentParser(description='Capture the Kindle-app-only books of a list from the Kindle app')
    parser.add_argument('list', type=Path, help='ASIN<TAB>title per line, a list the capture job reads')
    parser.add_argument('--root', type=Path, default=books.ROOT, help='where the book folders live')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads', help='where --probe writes')
    parser.add_argument('--probe', metavar='ASIN', help='only open this book and record what the app shows')
    args = parser.parse_args(argv)
    if env.get(OK_ENV) != '1':
        sys.exit(f'Not starting: the Kindle app is used only when Laiken asks for it; run with {OK_ENV}=1.')
    with awake():  # the display stays on while the app turns the pages (F145, F172)
        if locked():
            wake()
        if locked():
            sys.exit('Not starting: the display is on but the Mac still shows its lock screen, so the Kindle app cannot '
                     'be read or turned. Unlock it, then run the same command.')
        capture_list(args, capture)


def capture_list(args, capture):
    if args.probe:
        titles = {asin: title for asin, title, _ in books.read_tsv(args.list)}
        folder = probe(args.probe, titles.get(args.probe, args.probe), args.downloads / 'kindle-probe' / args.probe)
        print(f'Probe written to {folder}: open.txt, screen-<display>.png, chrome-<display>.png, ui-library.txt, ui-book.txt, '
              'ui-reader.txt, ui-chrome.txt, probe.json')
        return
    # Library.add returns the listed books and then every other folder; only the list's books are this run's.
    asins = {asin for asin, _, _ in books.read_tsv(args.list)}
    todo = app_books([b for b in books.Library(args.root).add([args.list], write=False) if b.asin in asins])
    print(f'{len(todo)} Kindle-app-only books', flush=True)
    current = books.versions()
    unopened = []
    for book in todo:
        title = book.title or book.asin
        attempt = book.attempt() + 1 if book.record('capture') else 1
        print(f'=== {title} ({book.asin}) from the Kindle app' + (' again' if attempt > 1 else ''), flush=True)
        if attempt > 1:
            set_aside(book.folder / 'pages')  # the refused capture's pages stay beside the new ones
        try:
            folder, failure = capture(book.asin, title, book.folder / 'pages')
        except Exception as e:  # recorded like any other failure, so the next run says why
            folder, failure = None, {'blocking_failures': ['crash'], 'error': str(e)}
        if failure:
            book.fail('capture', {'checks': failure['blocking_failures'], 'error': failure.get('error'), 'attempt': attempt,
                                  'retry': False, 'via': 'kindle_app', 'version': current['capture']})
            if failure['blocking_failures'] == ['app_open']:  # this book only: one not downloaded holds no other back
                print(f'=== skipped {title}: app_open: {failure.get("error") or ""}', flush=True)
                unopened.append(title)
                continue
            print(f'\nStopped: {title}: {", ".join(failure["blocking_failures"])}: {failure.get("error") or ""}. '
                  'Fix the cause, then run the same command to resume.', flush=True)
            sys.exit(2)
        book.succeed('capture', {'pages': str(folder), 'title': title, 'attempt': attempt, 'via': 'kindle_app',
                                 'version': current['capture']})
        print(f'=== captured {title}; the convert job converts it', flush=True)
    if unopened:
        print(f'\nNot opened in the app: {", ".join(unopened)}. Download them in the app, then run the same command.',
              flush=True)
        sys.exit(2)
    print(f'\nAll {len(todo)} Kindle-app-only books captured.')


if __name__ == '__main__':
    main()
