#!/usr/bin/env python3
"""
Capture the books Cloud Reader cannot open ("Kindle App Is Required") from the Kindle for Mac app, then convert and
check them the same way as every other book.

    KINDLE_APP_OK=1 venv/bin/python capture_app.py books.tsv

Laiken chose this on 2026-10-11 for the books capture_books.py recorded as kindle_app_only, and only for them: every
other book still comes from the Cloud Reader extension in Chrome. The command needs KINDLE_APP_OK=1 (the guard hook
asks for it too). Each book is opened in the app, paged back to its start through the app's Go menu, captured with
kindle2img.applescript into ~/Downloads/<title>/, then converted by capture_book.convert while the next book is
already open and turning in the app (Laiken, 2026-10-11 20:27: the app sat idle for the 2 minutes of conversion). The
app holds one book at a time and one book converts at a time; results are recorded in list order. The first book is the
pilot: the run stops at the first book that does not pass (a book already captured is converted and recorded first),
and the same command resumes there.

While a book is captured the Kindle window must stay in front, so the Mac's screen is busy until the run ends.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import ax
import capture_book
import locks
import capture_books

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
LOCATION = re.compile(r'Location (\d+) of (\d+)')
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


def cover_of(title, found):
    """The library button of this book: its description starts with the title (then the author, New or RED)."""
    key = capture_books.comparable(title)
    for e in found:
        if e['role'] == 'AXButton' and key and capture_books.comparable(e['description']).startswith(key[:30]):
            return e
    return None


def tab_of(title, found):
    """The tab bar's button for the recently read book (an AXRadioButton named after it), or None."""
    key = capture_books.comparable(title)
    for e in found:
        if e['role'] == 'AXRadioButton' and key and capture_books.comparable(e['title'] or e['description']) == key:
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
SEARCH = '''
tell application "System Events" to tell process "Kindle"
    set elems to entire contents of window 1
    repeat with e in elems
        set e to contents of e
        try
            if role of e is "AXTextField" and subrole of e is "AXSearchField" then
                set focused of e to true
                set value of e to {title}
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


def chrome_shown(found):
    """The toolbar is in the tree: its Close Book button, or the Reading Progress Bar. The reader's controls carry
    their names in the description, not the title (dump of 2026-10-11 18:04), and the run of 18:09 clicked the shown
    toolbar away three times because the title alone was read. The slider was the only sign until the run of 20:15:
    the Accessibility API's tree (ax.py) listed Close Book ... Location 1 of 2344 but no slider, and the book was
    refused as "no toolbar"; so the Close Book button counts too."""
    return any((e['role'] == 'AXSlider' and PROGRESS_BAR in (e['title'], e['description']))
               or (e['role'] == 'AXButton' and CLOSE_BOOK in (e['title'], e['description'])) for e in found)


def location_of(found):
    """(location, of) from the footer's "Location N of M , P%", or None. M came back as 0 in the probe of 2026-10-11
    18:04 ("Location 1 of 0 , 1%"), so only N says whether the start was reached."""
    for e in found:
        m = LOCATION.search(e['description']) or LOCATION.search(e['value']) or LOCATION.search(e['title'])
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


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
        if not name or name.startswith('Location ') or LOCATION.search(e['value']):
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


PAGE_BACK = 'tell application "System Events" to tell process "Kindle" to key code 123'  # ← previous page; kindle2img
# turns forward with 124


def page_back(script=osascript, sleep=time.sleep, clock=time.monotonic, batch=20, cap=3000):
    """Press ← until the footer reads Location 1 or stops falling. Laiken, 2026-10-11 19:27: when no reader tool
    works, page back one screen at a time rather than stop. The toolbar is hidden before each batch so the keys
    reach the page, then shown again to read the footer."""
    found = show_chrome(script, sleep, clock)
    where = location_of(found)
    if where is None:
        return 'no location in the footer to page back from'
    presses = 0
    while where[0] > 1 and presses < cap:
        bring_front(script, sleep)
        if chrome_shown(found):
            hide_chrome(script, sleep, clock)
        for _ in range(batch):
            script(PAGE_BACK)
            sleep(0.15)
            presses += 1
        found = show_chrome(script, sleep, clock)
        now = location_of(found)
        if now is None or now[0] >= where[0]:
            return f'paged back {presses} times and the footer stayed at Location {where[0]}'
        where = now
    return f'paged back {presses} times'


def go_to_start(script=osascript, sleep=time.sleep, clock=time.monotonic, scroll=None, press=None):
    """Back to the start of the open book: close the details sheet, show the toolbar, open the Table of Contents and
    press its topmost entry, else page back with ← until the start; read the footer's "Location N of M", then hide
    the toolbar again. Returns '' or why it could not. Written from the probes of 2026-10-11 17:56 and 19:20.
    The Reading Progress Bar is not used: writing its value, clicking its track and dragging its knob all left the
    book where it was (runs of 2026-10-11 19:02 and 19:11)."""
    began = clock()
    dismiss_sheet(script, sleep)
    # A book can take a while to render after it opens: Iceland showed a blank cream page for over 30 s on
    # 2026-10-11 20:11 and three clicks over 6 s found no toolbar; so the clicks go on for about half a minute.
    found = show_chrome(script, sleep, clock, tries=RENDER_TRIES)
    if not chrome_shown(found):
        return 'the reader shows no toolbar after clicks on the page; it shows: ' + describe(found)
    where = location_of(found)
    tried = []
    for move in (toc_first, page_back):
        if where is not None and where[0] <= 1:
            break
        bring_front(script, sleep)
        started = clock()
        how = move(script, sleep, clock, scroll=scroll, press=press) if move is toc_first else move(script, sleep, clock)
        found = show_chrome(script, sleep, clock)
        where = location_of(found)
        tried.append(f'{how} ({clock() - started:.0f} s)')
        if move is toc_first and where is None and how.startswith(('pressed', 'clicked')):
            # On the cover the toolbar shows no "Location N of M" (Sri Lanka, 2026-10-11 20:45: only the scrubber's
            # marks), so the pressed top entry is the start.
            where = (1, 0)
            break
    if where is None or where[0] > 1:
        return (f'could not go to the start (tried: {", ".join(tried)}, footer: '
                f'{"Location %d of %d" % where if where else "no location"})')
    print(f'start: Location {where[0]} after {clock() - began:.0f} s: {", ".join(tried) or "not needed"}', flush=True)
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


def capture(folder, script_path=HERE / 'kindle2img.applescript', run=capture_book.run, script=osascript,
            sleep=time.sleep):
    """Page through the book with kindle2img.applescript. When the end-of-book sheet is up afterwards, the last image
    is that sheet, not a page, so it is dropped, and the sheet is closed with Escape (never Enter or space: the
    focus sits on the rating's stars)."""
    run(['osascript', str(script_path), 'ltr', str(folder)])
    pages = sorted(folder.glob('page_*.png'))
    if pages and end_sheet_shown(elements(script)):
        pages.pop().unlink()
        dismiss_overlays(script, sleep)
        print(f'end: the end-of-book sheet was closed and its image dropped; {len(pages)} pages', flush=True)
    return pages


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


def app_books(books, progress):
    """The books of the list Cloud Reader refused as app-only and no app run has finished yet: a book an earlier app
    run failed (app_open, app_capture, a check) is tried again, so the same command resumes at it. On 2026-10-11 the
    resume skipped For 91 Days In Tokyo, whose record then said app_open instead of kindle_app_only."""
    return [(asin, title) for asin, title in books
            if capture_book.APP_ONLY in progress.get(asin, {}).get('failures', [])
            or (progress.get(asin, {}).get('capture') == 'kindle_app' and progress[asin].get('verdict') != 'done')]


def capture_pages(asin, title, downloads, open_=open_book, start=go_to_start, take=capture):
    """Open one book in the app, page it to its start and capture it: (folder, None), or (None, the failure)."""
    folder = downloads / title
    if list(folder.glob('page_*.png')):
        # kindle2img.applescript numbers on from the highest page, so pages already here would be captured twice.
        return None, {'asin': asin, 'blocking_failures': ['app_folder'], 'warnings': [],
                      'error': f'{folder} already holds page images; move them away to capture this book again'}
    for step in (lambda: open_(asin, title), start):
        problem = step()
        if problem:
            return None, {'asin': asin, 'blocking_failures': ['app_open'], 'warnings': [], 'error': problem}
    folder.mkdir(parents=True, exist_ok=True)
    pages = take(folder)
    if not pages:
        return None, {'asin': asin, 'blocking_failures': ['app_capture'], 'warnings': [], 'error': 'no page was captured'}
    return folder, None


def run_book(asin, title, downloads, convert=capture_book.convert, open_=open_book, start=go_to_start, take=capture):
    """One book from the app to its report, in this thread (the pilot path; main() converts in the background)."""
    folder, failure = capture_pages(asin, title, downloads, open_=open_, start=start, take=take)
    return failure or convert(asin, title, source=folder)


class Converting:
    """One captured book converting in its own thread while the app turns the next; outcome once it has ended."""

    def __init__(self, asin, title, work):
        self.asin, self.title, self.outcome = asin, title, None
        self.thread = threading.Thread(target=self._run, args=(work,), daemon=True)
        self.thread.start()

    def _run(self, work):
        try:
            self.outcome = work()
        except Exception as e:  # recorded like any other failure, so the summary names it
            self.outcome = {'asin': self.asin, 'blocking_failures': ['crash'], 'warnings': [], 'error': str(e)}

    def result(self):
        self.thread.join()
        return self.outcome


def main(argv=None, env=os.environ, capture=capture_pages, convert=capture_book.convert):
    parser = argparse.ArgumentParser(description='Capture the Kindle-app-only books of a list from the Kindle app')
    parser.add_argument('list', type=Path, help='ASIN<TAB>title per line, the same list capture_books.py ran')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--probe', metavar='ASIN', help='only open this book and record what the app shows')
    args = parser.parse_args(argv)
    if env.get(OK_ENV) != '1':
        sys.exit(f'Not starting: the Kindle app is used only when Laiken asks for it; run with {OK_ENV}=1.')
    if args.probe:
        titles = dict(capture_books.read_list(args.list))
        folder = probe(args.probe, titles.get(args.probe, args.probe), args.downloads / 'kindle-probe' / args.probe)
        print(f'Probe written to {folder}: open.txt, screen-<display>.png, chrome-<display>.png, ui-library.txt, ui-book.txt, '
              'ui-reader.txt, ui-chrome.txt, probe.json')
        return
    try:
        # One run per list: two runs would each write back the whole progress file over the other's results.
        locks.keep(args.list.with_suffix('.lock'), wait=False)
    except locks.Busy:
        sys.exit(f'Not starting: another run is working through {args.list.name}. Run this once it has ended.')
    progress_path = args.list.with_suffix('.progress.json')
    progress = capture_books.load_progress(progress_path)
    todo = app_books(capture_books.read_list(args.list), progress)
    print(f'{len(todo)} Kindle-app-only books', flush=True)
    converter = capture_books.converter_version()

    def record(asin, title, data):
        """Write one finished book to the progress file. Returns why the run must stop, or None."""
        verdict = 'failed' if data['blocking_failures'] else 'done'
        progress[asin] = {'title': title, 'verdict': verdict, 'failures': data['blocking_failures'],
                          'warnings': data['warnings'], 'error': data.get('error'), 'capture': 'kindle_app',
                          'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'converter': converter}
        if data.get('source'):
            progress[asin]['source'] = data['source']
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'=== {verdict}: {title}' + (f' ({", ".join(data["blocking_failures"])}: {data.get("error") or ""})'
                                          if data['blocking_failures'] else ''), flush=True)
        if data['blocking_failures']:
            # Laiken, 2026-10-10: one book first, the rest only once a book comes out right.
            return f'\nStopped: {title} did not pass. Fix the cause, then run the same command to resume.'
        return None

    def stop(reason):
        print(reason, flush=True)
        sys.exit(2)

    converting = None  # the book captured before this one, converting while the app turns this one
    for asin, title in todo:
        if converting is not None and not converting.thread.is_alive():
            # It ended during the last capture: a book that failed stops the run before the app opens another.
            reason = record(converting.asin, converting.title, converting.result())
            converting = None
            if reason:
                stop(reason)
        print(f'=== {title} ({asin}) from the Kindle app', flush=True)
        try:
            folder, failure = capture(asin, title, args.downloads)
        except Exception as e:  # recorded like any other failure, so the summary names it
            folder, failure = None, {'asin': asin, 'blocking_failures': ['crash'], 'warnings': [], 'error': str(e)}
        if converting is not None:  # one conversion at a time, recorded in list order
            reason = record(converting.asin, converting.title, converting.result())
            converting = None
            if reason:
                if folder is not None:  # the book just captured is converted and recorded, not thrown away
                    record(asin, title, convert(asin, title, source=folder))
                stop(reason)
        if failure:
            stop(record(asin, title, failure))
        print(f'=== converting {title} in the background', flush=True)
        converting = Converting(asin, title, lambda a=asin, t=title, f=folder: convert(a, t, source=f))
    if converting is not None:
        reason = record(converting.asin, converting.title, converting.result())
        if reason:
            stop(reason)
    print(f'\nAll {len(todo)} Kindle-app-only books done.')


if __name__ == '__main__':
    main()
