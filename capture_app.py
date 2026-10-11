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
    presses = []
    while len(presses) < 2:
        presses.append(bring_front(script, sleep))
        _, pressed, _ = script(PRESS_COVER.format(title=_quote(title)))
        cover = cover_of(title, found)
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
            + (' (and after pressing it again)' if cover and 'Not Downloaded' not in cover['value'] and len(presses) == 2 else '')
            + '; the cover says: ' + (cover['value'] if cover else 'gone')
            + '; frontmost before the presses: ' + ', '.join(presses))


def describe(found):
    """A short account of what is on screen, for an error message."""
    names = [e['title'] or e['description'] for e in found if e['title'] or e['description']]
    return ', '.join(names[:12]) + (' …' if len(names) > 12 else '')


SET_SLIDER = '''
tell application "System Events" to tell process "Kindle"
    set elems to entire contents of window 1
    repeat with e in elems
        set e to contents of e
        try
            if role of e is "AXSlider" and (title of e is "{bar}" or description of e is "{bar}") then
                set value of e to 0
                return "set"
            end if
        end try
    end repeat
    return "none"
end tell
'''.format(bar=PROGRESS_BAR)


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
        return found, 'the Kindle app shows neither its library nor the reader toolbar after clicks on the page; it shows: ' + describe(found)
    _, pressed, _ = script(PRESS_TITLED.format(title='"Close Book"'))
    sleep(3)
    found = elements(script)
    if in_library(found):
        return found, ''
    return found, f'the Kindle app is not showing its library (Close Book: {pressed}); it shows: ' + describe(found)


def go_to_start(script=osascript, sleep=time.sleep):
    """Back to the start of the open book: close the details sheet, show the toolbar, drag the Reading Progress Bar
    to 0, read the footer's "Location N of M", then hide the toolbar again so the pages are captured bare.
    Returns '' or why it could not. Written from the probe of 2026-10-11 17:56, not guessed."""
    dismiss_sheet(script, sleep)
    found = show_chrome(script, sleep)
    if not chrome_shown(found):
        return 'the reader shows no toolbar after clicks on the page; it shows: ' + describe(found)
    where = location_of(found)
    set_ = 'not needed'
    if where is None or where[0] > 1:  # a book already at Location 1 (run of 2026-10-11 18:19) is not moved
        bring_front(script, sleep)
        _, set_, _ = script(SET_SLIDER)
        sleep(3)
        found = elements(script)
        where = location_of(found)
    if set_ not in ('set', 'not needed') or where is None or where[0] > 1:
        return (f'could not go to the start (slider: {set_}, footer: '
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
