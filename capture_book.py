#!/usr/bin/env python3
"""
Capture one book from Kindle Cloud Reader in Brave and turn it into checked Markdown, in the order CLAUDE.md sets.

    venv/bin/python capture_book.py B07HNMJV57 --title "How to Write a Lot"

1. Opens the book in Brave with `#autocapture` (never the Kindle app, never Chrome, never a Brave restart).
2. Waits for the extension's ZIP in ~/Downloads, then closes only that book's tab.
3. Runs img2txt.py, then check_book.py; runs each automatic fix it names and checks again, up to ROUNDS times.
4. Writes report.json next to the book and exits 1 when a blocking check still fails.

It handles one book; `capture_books.py` runs a list and decides from the checks whether to go on or redo.
`--zip` skips the capture and starts from an existing ZIP or folder; `--recapture` captures a book again whose
first ZIP was unusable (the extension otherwise refuses to auto-start a saved book).
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from check_book import Check, check_folder

BRAVE = 'Brave Browser'
ROUNDS = 3
ZIP_WAIT_S = 3600
SETTLE_S = 5
TAB_TRIES = 3
PYTHON = sys.executable
HERE = Path(__file__).resolve().parent


def run(command, **kwargs):
    print('$', ' '.join(map(str, command)), flush=True)
    return subprocess.run(command, check=True, **kwargs)


def open_in_brave(asin, domain, recapture=False):
    run(['open', '-a', BRAVE, f'https://{domain}/?asin={asin}#{"recapture" if recapture else "autocapture"}'])


def close_book_tab(asin):
    """Close only the tabs showing this book; the rest of Brave stays as it is."""
    script = f'tell application "{BRAVE}" to close (every tab of every window whose URL contains "asin={asin}")'
    subprocess.run(['osascript', '-e', script], capture_output=True)


def book_tabs(asin):
    """How many Brave tabs show this book (read only), or None when Brave could not be asked."""
    script = f'tell application "{BRAVE}" to get URL of every tab of every window'
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if result.returncode:
        print(f'Could not list Brave tabs: {result.stderr.strip()}', flush=True)
        return None
    return result.stdout.count(f'asin={asin}')


def ensure_tab_closed(asin, count=book_tabs, close=close_book_tab, tries=TAB_TRIES, sleep=time.sleep):
    """The extension closes its own tab after the download; make sure, close what is left, and say whether it worked.

    Tabs stayed open before because nothing checked: close_book_tab ignored osascript's errors, and captures
    not started by this script had no one to close them at all.
    """
    for _ in range(tries):
        left = count(asin)
        if left == 0:
            return True
        if left:
            close(asin)
        sleep(2)
    return count(asin) == 0


def wait_for_zip(downloads, before, timeout=ZIP_WAIT_S, clock=time.monotonic, sleep=time.sleep):
    """The first new .zip whose size has stopped changing, or None when none appears in time."""
    deadline = clock() + timeout
    while clock() < deadline:
        new = [p for p in Path(downloads).glob('*.zip') if p not in before and not p.name.endswith('.crdownload')]
        if new:
            path = max(new, key=lambda p: p.stat().st_mtime)
            size = path.stat().st_size
            sleep(SETTLE_S)
            if path.exists() and path.stat().st_size == size and size > 0:
                return path
            continue
        sleep(2)
    return None


def plan_fixes(checks):
    """Commands to run for the failed checks that have one, each at most once."""
    seen = []
    for c in checks:
        if not c.ok and c.fix and c.fix not in seen:
            seen.append(c.fix)
    return seen


def correct(folder, title, rounds=ROUNDS, runner=run):
    """Check, fix what can be fixed by the machine, and check again. Returns (checks, rounds used)."""
    checks = check_folder(folder, title)
    for round_no in range(1, rounds + 1):
        fixes = plan_fixes(checks)
        if not fixes:
            break
        for fix in fixes:
            runner([PYTHON, str(HERE / fix[0]), *fix[1:]])
        checks = check_folder(folder, title)
    return checks, round_no


def report(folder, checks, extra):
    data = {**extra, 'checks': [asdict(c) for c in checks],
            'blocking_failures': [c.name for c in checks if not c.ok and c.blocking],
            'warnings': [c.name for c in checks if not c.ok and not c.blocking]}
    (folder / 'report.json').write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    for c in checks:
        mark = 'ok  ' if c.ok else ('FAIL' if c.blocking else 'warn')
        print(f'{mark} {c.name:18} {c.detail}')
    print(f'Report: {folder / "report.json"}')
    return data


def convert(asin, title=None, model='vision', domain='read.amazon.com', downloads=Path.home() / 'Downloads',
            source=None, recapture=False):
    """Capture (unless source is given), convert, check and fix one book. Returns the report data."""
    started = time.time()
    tab_closed = None  # no tab when converting an existing ZIP
    if source is None:
        before = set(downloads.glob('*.zip'))
        open_in_brave(asin, domain, recapture)
        source = wait_for_zip(downloads, before)
        tab_closed = ensure_tab_closed(asin)
        if source is None:
            raise RuntimeError(f'No ZIP appeared in {downloads} within {ZIP_WAIT_S // 60} minutes; the book tab was closed. '
                               'The extension badge says skip when it refused to start, and the tab console says why.')
    run([PYTHON, str(HERE / 'img2txt.py'), str(source), '--model', model] + (['--title', title] if title else []))
    folder = source.with_suffix('') if source.suffix == '.zip' else source
    checks, rounds = correct(folder, title)
    if tab_closed is not None:
        # Blocking, but not a capture defect: the runner records it and goes on without recapturing.
        checks.append(Check('tab_closed', tab_closed, f'book tab {"closed" if tab_closed else "still open"} after the capture'))
    return report(folder, checks, {'asin': asin, 'source': str(source), 'recapture': recapture, 'rounds': rounds,
                                   'tab_closed': tab_closed, 'seconds': round(time.time() - started)})


def main():
    parser = argparse.ArgumentParser(description='Capture one Kindle book in Brave and produce checked Markdown')
    parser.add_argument('asin', help='The book\'s ASIN (?asin= in the Cloud Reader URL)')
    parser.add_argument('--title', help='Book title for the output file name (from the inventory)')
    parser.add_argument('--model', default='vision', help='img2txt --model (default: vision, no Gemini quota)')
    parser.add_argument('--domain', default='read.amazon.com')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--zip', type=Path, help='Skip the capture and start from this ZIP or folder')
    parser.add_argument('--recapture', action='store_true', help='Capture again a book the extension already saved')
    args = parser.parse_args()
    try:
        data = convert(args.asin, args.title, args.model, args.domain, args.downloads, args.zip, args.recapture)
    except RuntimeError as e:
        sys.exit(str(e))
    sys.exit(bool(data['blocking_failures']))


if __name__ == '__main__':
    main()
