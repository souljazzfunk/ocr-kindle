#!/usr/bin/env python3
"""
Capture the books Cloud Reader cannot open ("Kindle App Is Required") from the Kindle for Mac app, then convert and
check them the same way as every other book.

    KINDLE_APP_OK=1 venv/bin/python capture_app.py books.tsv

Laiken chose this on 2026-10-11 for the books capture_books.py recorded as kindle_app_only, and only for them: every
other book still comes from the Cloud Reader extension in Chrome. The command needs KINDLE_APP_OK=1 (the guard hook
asks for it too). Each book is opened in the app, paged back to its start through the app's Go menu, captured with
kindle2img.applescript into ~/Downloads/<title>/, then converted by capture_book.convert. The first book is the pilot:
the run stops at the first book that does not pass, and the same command resumes there.

While a book is captured the Kindle window must stay in front, so the Mac's screen is busy until the run ends.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import capture_book
import locks
import capture_books

HERE = Path(__file__).resolve().parent
OK_ENV = 'KINDLE_APP_OK'
OPEN_WAIT_S = 60
SETTLE_S = 5  # the library after Close Book, before the first press
# A book not yet on this Mac stays in the library on the first open; opening it again starts its download
# (Laiken, 2026-10-11 17:21 JST), which takes longer than a plain open.
DOWNLOAD_WAIT_S = 300
# The reader's footer, shown with its toolbar after a click on the page (probe of 2026-10-11 17:56).
LOCATION = re.compile(r'Location (\d+) of (\d+)')
PROGRESS_BAR = 'Reading Progress Bar'


def osascript(script, runner=subprocess.run):
    result = runner(['osascript', '-e', script], capture_output=True, text=True)
    return result.returncode, (result.stdout or '').strip(), (result.stderr or '').strip()


def elements(script=osascript):
    """The window's elements from the UI dump: dicts with role, subrole, title, description, value, position, size."""
    _, out, _ = script(UI_DUMP)
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
    sleep(2)
    return pressed


def reveal_chrome(script=osascript, sleep=time.sleep):
    """Click the middle of the page so the reader shows its toolbar and progress (the probe saw neither in the dump
    taken right after the open; the footer showed only "1%")."""
    _, out, err = script(CLICK_PAGE)
    sleep(2)
    return out or err


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
    script('tell application "Amazon Kindle" to activate')
    sleep(2)
    found, problem = ensure_library(script, sleep)
    if problem:
        return problem
    if cover_of(title, found) is None:
        _, searched, _ = script(SEARCH.format(title=_quote(title)))
        sleep(3)
        found = elements(script)
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
    sleep(SETTLE_S)
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
        wait = DOWNLOAD_WAIT_S if len(presses) == 2 and cover and 'Not Downloaded' in cover['value'] else OPEN_WAIT_S
        deadline = clock() + wait
        while clock() < deadline:
            sleep(2)
            found = elements(script)
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
    """The toolbar is in the tree. The reader's controls carry their names in the description, not the title
    (dump of 2026-10-11 18:04: element|AXSlider|||Reading Progress Bar|Location 1 , 1%|…), and the run of 18:09
    clicked the shown toolbar away three times because the title alone was read."""
    return any(e['role'] == 'AXSlider' and PROGRESS_BAR in (e['title'], e['description']) for e in found)


def location_of(found):
    """(location, of) from the footer's "Location N of M , P%", or None. M came back as 0 in the probe of 2026-10-11
    18:04 ("Location 1 of 0 , 1%"), so only N says whether the start was reached."""
    for e in found:
        m = LOCATION.search(e['description']) or LOCATION.search(e['value']) or LOCATION.search(e['title'])
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def show_chrome(script=osascript, sleep=time.sleep, tries=3, found=None):
    """Click the page until the toolbar is in the element tree. With the toolbar hidden the reader's tree holds only
    groups and the window buttons (probe of 2026-10-11 18:04), and in the pilot run just before it one click did not
    bring it (Close Book: none), so each click is checked and repeated. Returns what the app shows."""
    found = elements(script) if found is None else found
    for _ in range(tries):
        if chrome_shown(found):
            break
        reveal_chrome(script, sleep)
        found = elements(script)
    return found


FRONTMOST = ('tell application "Amazon Kindle" to activate\n'
             'tell application "System Events" to get name of first process whose frontmost is true')


def bring_front(script=osascript, sleep=time.sleep):
    """Activate Kindle and say which process is frontmost afterwards. In the run of 2026-10-11 18:12 the cover was
    pressed while "Claude" was frontmost and the library stayed, so every press is preceded by this."""
    _, front, err = script(FRONTMOST)
    sleep(1)
    return front or err


def dismiss_overlays(script=osascript, sleep=time.sleep):
    """Close a menu or sheet over the reader: Escape twice, then the sheet's Close button if one is in the tree."""
    for _ in range(2):
        script('tell application "System Events" to tell process "Kindle" to key code 53')  # Escape
        sleep(1)
    return dismiss_sheet(script, sleep)


def ensure_library(script=osascript, sleep=time.sleep):
    """Bring the app to its library: a book left open (the app reopens into the last book, 2026-10-11 17:56) is closed
    with the reader's Close Book button. File > Close quits the app, so it is never used.
    Returns (what the app shows, '' or why the library did not come)."""
    found = elements(script)
    if in_library(found):
        return found, ''
    bring_front(script, sleep)
    found = show_chrome(script, sleep, found=found)
    if not chrome_shown(found):
        # The end of a book leaves an end-of-book sheet (rating, recommend, follow the author), and a share menu
        # had opened over it (run of 2026-10-11 18:59, the book read to 99%); both hide the toolbar.
        dismiss_overlays(script, sleep)
        found = show_chrome(script, sleep)
    if not chrome_shown(found):
        return found, 'the Kindle app shows neither its library nor the reader toolbar after clicks on the page; it shows: ' + describe(found)
    _, pressed, _ = script(PRESS_TITLED.format(title='"Close Book"'))
    sleep(3)
    found = elements(script)
    if in_library(found):
        return found, ''
    return found, f'the Kindle app is not showing its library (Close Book: {pressed}); it shows: ' + describe(found)


def slider_of(found):
    return next((e for e in found if e['role'] == 'AXSlider' and PROGRESS_BAR in (e['title'], e['description'])), None)


def _frame(e):
    x, y = (int(v) for v in e['position'].split(','))
    w, h = (int(v) for v in e['size'].split(','))
    return x, y, w, h


def click_track_start(slider, script=osascript, sleep=time.sleep, drag=None):
    """Click the progress bar's track at its left end."""
    if slider is None:
        return 'no slider'
    x, y, w, h = _frame(slider)
    script(CLICK_AT.format(x=x + 2, y=y + h // 2))
    return 'clicked the track start'


def drag_knob_to_start(slider, script=osascript, sleep=time.sleep, drag=None):
    """Drag the knob from where the slider's percentage puts it to the left end, with a real mouse drag."""
    if slider is None:
        return 'no slider'
    x, y, w, h = _frame(slider)
    m = re.search(r'(\d+)%', slider['value'])
    knob = x + w * int(m.group(1)) // 100 if m else x + w // 2
    try:
        (drag or _quartz_drag)(knob, y + h // 2, x + 1, y + h // 2, sleep)
    except Exception as e:  # pyobjc missing, or no event tap
        return f'drag failed: {e}'
    return 'dragged the knob'


def _quartz_drag(x0, y0, x1, y1, sleep=time.sleep):
    from Quartz import (CGEventCreateMouseEvent, CGEventPost, kCGEventLeftMouseDown, kCGEventLeftMouseDragged,
                        kCGEventLeftMouseUp, kCGHIDEventTap, kCGMouseButtonLeft)
    for kind, x, y in ((kCGEventLeftMouseDown, x0, y0), (kCGEventLeftMouseDragged, (x0 + x1) // 2, y1),
                       (kCGEventLeftMouseDragged, x1, y1), (kCGEventLeftMouseUp, x1, y1)):
        CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kind, (x, y), kCGMouseButtonLeft))
        sleep(0.3)


def toc_first(slider, script=osascript, sleep=time.sleep, drag=None):
    """Open the Table of Contents and click its topmost entry. Probe of 2026-10-11 19:20 (Bolivia): the panel slides
    in at the left with the title (AXHeading), the author, a "Close menu" X, then the entries ("Cover", "Beginning")
    as AXStaticText, not buttons. Laiken, 19:26: any topmost entry leads to the start, not only "Cover". Checked on
    the Mac at 19:26: AXPress on the "Cover" text moved Bolivia from Location 185 to Location 1, while synthetic
    mouse events are unreliable in this app; so the first static text after the X is pressed, and only clicked at
    its centre when the press is refused."""
    _, pressed, _ = script(PRESS_TITLED.format(title='"Table of Contents"'))
    if pressed != 'pressed':
        return 'no Table of Contents button'
    sleep(2)
    found = elements(script)
    closes = [i for i, e in enumerate(found) if 'Close menu' in (e['title'], e['description'])]
    rows = [e for e in found[closes[-1] + 1:] if e['role'] == 'AXStaticText' and not LOCATION.search(e['description'])
            and not LOCATION.search(e['value'])] if closes else []
    if not rows:
        script(PRESS_TITLED.format(title='"Close menu"'))
        return 'no entry in the table of contents'
    name = rows[0]['description'] or rows[0]['title']
    _, pressed, _ = script(PRESS_TEXT.format(title=_quote(name)))
    if pressed == 'pressed':
        return f'pressed "{name}" at the top of the table of contents'
    x, y, w, h = _frame(rows[0])
    script(CLICK_AT.format(x=x + w // 2, y=y + h // 2))
    return f'clicked "{name}" at the top of the table of contents'


PAGE_BACK = 'tell application "System Events" to tell process "Kindle" to key code 123'  # ← previous page; kindle2img
# turns forward with 124


def page_back(slider, script=osascript, sleep=time.sleep, drag=None, batch=20, cap=3000):
    """Press ← until the footer reads Location 1 or stops falling. Laiken, 2026-10-11 19:27: when no reader tool
    works, page back one screen at a time rather than stop. The toolbar is hidden before each batch so the keys
    reach the page, then shown again to read the footer."""
    found = show_chrome(script, sleep)
    where = location_of(found)
    if where is None:
        return 'no location in the footer to page back from'
    presses = 0
    while where[0] > 1 and presses < cap:
        bring_front(script, sleep)
        if chrome_shown(found):
            reveal_chrome(script, sleep)  # the same click hides the toolbar
        for _ in range(batch):
            script(PAGE_BACK)
            sleep(0.3)
            presses += 1
        sleep(1)
        found = show_chrome(script, sleep)
        now = location_of(found)
        if now is None or now[0] >= where[0]:
            return f'paged back {presses} times and the footer stayed at Location {where[0]}'
        where = now
    return f'paged back {presses} times'


def go_to_start(script=osascript, sleep=time.sleep, drag=None):
    """Back to the start of the open book: close the details sheet, show the toolbar, click and then drag the
    Reading Progress Bar to its start, else open the Table of Contents and press its topmost entry, else page back
    with ← until the start; read the footer's
    "Location N of M", then hide the toolbar again.
    Returns '' or why it could not. Written from the probes of 2026-10-11 17:56 and 19:20, not guessed."""
    dismiss_sheet(script, sleep)
    found = show_chrome(script, sleep)
    if not chrome_shown(found):
        return 'the reader shows no toolbar after clicks on the page; it shows: ' + describe(found)
    where = location_of(found)
    tried = ['not needed']
    # Setting the slider's value is not honoured (run of 2026-10-11 19:02: "slider: set" and the knob stayed at
    # 13%, Location 185), so the slider's track is clicked at its left end, then the knob dragged there; neither
    # moved it either (run of 19:11), so the Table of Contents' topmost entry comes next, and paging back with ←
    # is the last resort (Laiken, 19:27).
    for move in (click_track_start, drag_knob_to_start, toc_first, page_back):
        if where is not None and where[0] <= 1:
            break
        bring_front(script, sleep)
        tried.append(move(slider_of(found), script, sleep, drag))
        sleep(3)
        found = show_chrome(script, sleep)
        where = location_of(found)
    if where is None or where[0] > 1:
        return (f'could not go to the start (tried: {", ".join(tried)}, footer: '
                f'{"Location %d of %d" % where if where else "no location"})')
    bring_front(script, sleep)
    reveal_chrome(script, sleep)  # the same click hides the toolbar again
    if chrome_shown(elements(script)):
        return 'the toolbar stayed over the page after a click to hide it'
    return ''


def capture(folder, script_path=HERE / 'kindle2img.applescript', run=capture_book.run):
    run(['osascript', str(script_path), 'ltr', str(folder)])
    return sorted(folder.glob('page_*.png'))


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


def probe(asin, title, folder, script=osascript, run=capture_book.run, sleep=time.sleep, ebooks=EBOOKS):
    """Open the book in the app and record what the app shows: a screenshot, the UI dump before and after the open,
    and whether the book is downloaded. Returns the folder; the files are what a fix is written from."""
    folder.mkdir(parents=True, exist_ok=True)
    script('tell application "Amazon Kindle" to activate')
    sleep(3)
    _, before, err = script(UI_DUMP)
    (folder / 'ui-library.txt').write_text(before or err, encoding='utf-8')
    # The kindle:// URL does not open a book in the new app (probe of 2026-10-11 17:46); the cover is pressed instead.
    problem = open_book(asin, title, script=script, run=run, sleep=sleep)
    (folder / 'open.txt').write_text(problem or 'opened', encoding='utf-8')
    sleep(3)
    # One file per display: with one file screencapture records only the main display, and on 2026-10-11 the Kindle
    # window was on the other one (the Window menu offered "Move to BenQ EL2870U"), so screen.png showed no Kindle.
    run(['screencapture', '-x', *(str(folder / f'screen-{n}.png') for n in (1, 2, 3))])
    _, after, err = script(UI_DUMP)
    (folder / 'ui-book.txt').write_text(after or err, encoding='utf-8')
    # The reader as it is used: the details sheet closed, then the page clicked once for its toolbar.
    notes = ['sheet: ' + dismiss_sheet(script, sleep)]
    _, reader, err = script(UI_DUMP)
    (folder / 'ui-reader.txt').write_text(reader or err, encoding='utf-8')
    notes.append('page: ' + reveal_chrome(script, sleep))
    run(['screencapture', '-x', *(str(folder / f'chrome-{n}.png') for n in (1, 2, 3))])
    _, chrome, err = script(UI_DUMP)
    (folder / 'ui-chrome.txt').write_text(chrome or err, encoding='utf-8')
    # The ways to the start that do not go through the slider (the slider ignored a value, a click and a drag in the
    # runs of 2026-10-11 19:02 and 19:11): what the Table of Contents and More options buttons open.
    for name, button in (('toc', 'Table of Contents'), ('more', 'More options')):
        if not chrome_shown(elements(script)):
            reveal_chrome(script, sleep)
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


def run_book(asin, title, downloads, convert=capture_book.convert, open_=open_book, start=go_to_start, take=capture):
    folder = downloads / title
    if list(folder.glob('page_*.png')):
        # kindle2img.applescript numbers on from the highest page, so pages already here would be captured twice.
        return {'asin': asin, 'blocking_failures': ['app_folder'], 'warnings': [],
                'error': f'{folder} already holds page images; move them away to capture this book again'}
    for step in (lambda: open_(asin, title), start):
        problem = step()
        if problem:
            return {'asin': asin, 'blocking_failures': ['app_open'], 'warnings': [], 'error': problem}
    folder.mkdir(parents=True, exist_ok=True)
    pages = take(folder)
    if not pages:
        return {'asin': asin, 'blocking_failures': ['app_capture'], 'warnings': [], 'error': 'no page was captured'}
    return convert(asin, title, source=folder)


def main(argv=None, env=os.environ, book=run_book):
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
    for asin, title in todo:
        print(f'=== {title} ({asin}) from the Kindle app', flush=True)
        try:
            data = book(asin, title, args.downloads)
        except Exception as e:  # recorded like any other failure, so the summary names it
            data = {'asin': asin, 'blocking_failures': ['crash'], 'warnings': [], 'error': str(e)}
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
            print(f'\nStopped: {title} did not pass. Fix the cause, then run the same command to resume.')
            sys.exit(2)
    print(f'\nAll {len(todo)} Kindle-app-only books done.')


if __name__ == '__main__':
    main()
