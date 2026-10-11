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
# The app reopens a book where it was last read; its Go menu has an item that jumps to the start.
START_ITEMS = ('Beginning', 'Cover', 'Start')


def osascript(script, runner=subprocess.run):
    result = runner(['osascript', '-e', script], capture_output=True, text=True)
    return result.returncode, (result.stdout or '').strip(), (result.stderr or '').strip()


def window_title(script=osascript):
    code, out, _ = script('tell application "System Events" to tell process "Kindle" to get name of window 1')
    return out if code == 0 else ''


def open_book(asin, title, script=osascript, run=capture_book.run, clock=time.monotonic, sleep=time.sleep):
    """Open the book in the app and wait for its window; when it stays in the library, open it once more so the app
    downloads the book, and wait for that. Returns '' or why it could not."""
    key = capture_books.comparable(title)
    shown = ''
    for wait in (OPEN_WAIT_S, DOWNLOAD_WAIT_S):
        run(['open', f'kindle://book?action=open&asin={asin}'])
        deadline = clock() + wait
        while clock() < deadline:
            shown = window_title(script)
            name = capture_books.comparable(shown)
            if name and key and (name.startswith(key[:20]) or key.startswith(name)):
                return ''
            sleep(2)
    return (f'the Kindle app did not show this book within {OPEN_WAIT_S} s, nor within {DOWNLOAD_WAIT_S} s of opening '
            f'it again to download it (front window: "{shown}")')


def go_to_start(script=osascript):
    """Jump to the start through the Go menu. When no known item is there, say which items there are, so the next fix
    comes from what the app shows rather than from a guess."""
    for item in START_ITEMS:
        code, _, _ = script('tell application "Amazon Kindle" to activate\n'
                            'tell application "System Events" to tell process "Kindle" to '
                            f'click (first menu item of menu 1 of menu bar item "Go" of menu bar 1 whose name starts with "{item}")')
        if code == 0:
            time.sleep(2)
            return ''
    code, out, err = script('tell application "System Events" to tell process "Kindle" to '
                            'get name of every menu item of menu 1 of every menu bar item of menu bar 1')
    return f'no start item in the Kindle Go menu; the menus hold: {out or err}'


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


def probe(asin, folder, script=osascript, run=capture_book.run, sleep=time.sleep, ebooks=EBOOKS):
    """Open the book in the app and record what the app shows: a screenshot, the UI dump before and after the open,
    and whether the book is downloaded. Returns the folder; the files are what a fix is written from."""
    folder.mkdir(parents=True, exist_ok=True)
    script('tell application "Amazon Kindle" to activate')
    sleep(3)
    _, before, err = script(UI_DUMP)
    (folder / 'ui-library.txt').write_text(before or err, encoding='utf-8')
    # On 2026-10-11 a plain `open kindle://` left the app behind the window Laiken was typing in; -a names it.
    run(['open', '-a', 'Amazon Kindle', f'kindle://book?action=open&asin={asin}'])
    sleep(10)
    script('tell application "Amazon Kindle" to activate')
    sleep(2)
    # One file per display: with one file screencapture records only the main display, and on 2026-10-11 the Kindle
    # window was on the other one (the Window menu offered "Move to BenQ EL2870U"), so screen.png showed no Kindle.
    run(['screencapture', '-x', *(str(folder / f'screen-{n}.png') for n in (1, 2, 3))])
    _, after, err = script(UI_DUMP)
    (folder / 'ui-book.txt').write_text(after or err, encoding='utf-8')
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
        folder = probe(args.probe, args.downloads / 'kindle-probe' / args.probe)
        print(f'Probe written to {folder}: screen-<display>.png, ui-library.txt, ui-book.txt, probe.json')
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
