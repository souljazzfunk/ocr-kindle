#!/usr/bin/env python3
"""
Capture the books Cloud Reader cannot open ("Kindle App Is Required") from the Kindle for Mac app, then convert and
check them the same way as every other book.

    KINDLE_APP_OK=1 venv/bin/python capture_app.py books.tsv

Laiken chose this on 2026-10-11 for the books capture_books.py recorded as kindle_app_only, and only for them: every
other book still comes from the Cloud Reader extension in Brave. The command needs KINDLE_APP_OK=1 (the guard hook
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
import capture_books

HERE = Path(__file__).resolve().parent
OK_ENV = 'KINDLE_APP_OK'
OPEN_WAIT_S = 60
# The app reopens a book where it was last read; its Go menu has an item that jumps to the start.
START_ITEMS = ('Beginning', 'Cover', 'Start')


def osascript(script, runner=subprocess.run):
    result = runner(['osascript', '-e', script], capture_output=True, text=True)
    return result.returncode, (result.stdout or '').strip(), (result.stderr or '').strip()


def window_title(script=osascript):
    code, out, _ = script('tell application "System Events" to tell process "Kindle" to get name of window 1')
    return out if code == 0 else ''


def open_book(asin, title, script=osascript, run=capture_book.run, clock=time.monotonic, sleep=time.sleep):
    """Open the book in the app and wait for its window. Returns '' or why it could not."""
    run(['open', f'kindle://book?action=open&asin={asin}'])
    key = capture_books.comparable(title)
    deadline = clock() + OPEN_WAIT_S
    shown = ''
    while clock() < deadline:
        shown = window_title(script)
        name = capture_books.comparable(shown)
        if name and key and (name.startswith(key[:20]) or key.startswith(name)):
            return ''
        sleep(2)
    return f'the Kindle app did not show this book within {OPEN_WAIT_S} s (front window: "{shown}")'


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


def app_books(books, progress):
    """The books of the list Cloud Reader refused as app-only and no app run has finished yet."""
    return [(asin, title) for asin, title in books
            if capture_book.APP_ONLY in progress.get(asin, {}).get('failures', [])]


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
    args = parser.parse_args(argv)
    if env.get(OK_ENV) != '1':
        sys.exit(f'Not starting: the Kindle app is used only when Laiken asks for it; run with {OK_ENV}=1.')
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
