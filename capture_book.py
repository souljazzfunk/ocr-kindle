#!/usr/bin/env python3
"""
Capture one book from Kindle Cloud Reader in Brave and turn it into checked Markdown, in the order CLAUDE.md sets.

    venv/bin/python capture_book.py B07HNMJV57 --title "How to Write a Lot"

1. Opens the book in Brave with `#autocapture` (never the Kindle app, never Chrome, never a Brave restart).
2. Waits for the extension's ZIP in ~/Downloads, then closes only that book's tab.
3. Runs img2txt.py, then check_book.py; runs each automatic fix it names and checks again, up to ROUNDS times.
4. Writes report.json next to the book and exits 1 when a blocking check still fails.

It handles one book and never opens another. `--zip` skips the capture and starts from an existing ZIP or folder.
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from check_book import check_folder

BRAVE = 'Brave Browser'
ROUNDS = 3
ZIP_WAIT_S = 3600
SETTLE_S = 5
PYTHON = sys.executable
HERE = Path(__file__).resolve().parent


def run(command, **kwargs):
    print('$', ' '.join(map(str, command)), flush=True)
    return subprocess.run(command, check=True, **kwargs)


def open_in_brave(asin, domain):
    run(['open', '-a', BRAVE, f'https://{domain}/?asin={asin}#autocapture'])


def close_book_tab(asin):
    """Close only the tabs showing this book; the rest of Brave stays as it is."""
    script = f'tell application "{BRAVE}" to close (every tab of every window whose URL contains "asin={asin}")'
    subprocess.run(['osascript', '-e', script], capture_output=True)


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


def main():
    parser = argparse.ArgumentParser(description='Capture one Kindle book in Brave and produce checked Markdown')
    parser.add_argument('asin', help='The book\'s ASIN (?asin= in the Cloud Reader URL)')
    parser.add_argument('--title', help='Book title for the output file name (from the inventory)')
    parser.add_argument('--model', default='vision', help='img2txt --model (default: vision, no Gemini quota)')
    parser.add_argument('--domain', default='read.amazon.com')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--zip', type=Path, help='Skip the capture and start from this ZIP or folder')
    args = parser.parse_args()

    started = time.time()
    source = args.zip
    if source is None:
        before = set(args.downloads.glob('*.zip'))
        open_in_brave(args.asin, args.domain)
        source = wait_for_zip(args.downloads, before)
        close_book_tab(args.asin)
        if source is None:
            sys.exit(f'No ZIP appeared in {args.downloads} within {ZIP_WAIT_S // 60} minutes; the book tab was closed. '
                     'Check the extension badge next time (skip = refused to start; the tab console says why).')
    run([PYTHON, str(HERE / 'img2txt.py'), str(source), '--model', args.model] + (['--title', args.title] if args.title else []))
    folder = source.with_suffix('') if source.suffix == '.zip' else source
    checks, rounds = correct(folder, args.title)
    data = report(folder, checks, {'asin': args.asin, 'source': str(source), 'rounds': rounds, 'seconds': round(time.time() - started)})
    sys.exit(bool(data['blocking_failures']))


if __name__ == '__main__':
    main()
