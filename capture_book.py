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
import brave
from extension_state import folder_problem, repo_version, zip_version
from img2txt import DAILY_LIMIT_EXIT

BRAVE = 'Brave Browser'
ROUNDS = 3
ZIP_WAIT_S = 3600
SETTLE_S = 5
TAB_TRIES = 3
DAILY_LIMIT_CHECK = 'gemini_daily_limit'
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


def skip_notices(downloads, asin):
    """The extension's <ASIN>.skip.json files (uniquified copies too): an automatic capture it would not run."""
    return set(Path(downloads).glob(f'{asin}.skip*.json'))


def wait_for_zip(downloads, before, timeout=ZIP_WAIT_S, clock=time.monotonic, sleep=time.sleep, asin=None):
    """The first new .zip whose size has stopped changing, a new skip notice for asin, or None when neither appears."""
    deadline = clock() + timeout
    while clock() < deadline:
        skipped = skip_notices(downloads, asin) - before if asin else set()
        if skipped:
            return max(skipped, key=lambda p: p.stat().st_mtime)
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
    """Commands to run for the failed checks that have one, each at most once, re-reading pages before proofreading:
    a re-read rewrites the pages and would undo the proofreader's fixes (2026-10-10, AI for Educators)."""
    seen = []
    for c in checks:
        if not c.ok and c.fix and c.fix not in seen:
            seen.append(c.fix)
    return sorted(seen, key=lambda fix: fix[0] == 'proofread.py')


def correct(folder, title, rounds=ROUNDS, runner=run):
    """Check, fix what can be fixed by the machine, and check again. Returns (checks, rounds used).

    A full re-OCR (img2txt.py --force) runs at most once per book: a second one reads the same pages the same way,
    and on a 999-screen book each costs about ten minutes. It also undoes the text fixes, so those run after it.
    """
    checks = check_folder(folder, title)
    reocr_done = False
    for round_no in range(1, rounds + 1):
        fixes = [f for f in plan_fixes(checks) if not (reocr_done and '--force' in f)]
        if not fixes:
            break
        fixes.sort(key=lambda f: '--force' not in f)  # re-OCR first, then the fixes to its text
        for fix in fixes:
            runner([PYTHON, str(HERE / fix[0]), *fix[1:]])
            reocr_done = reocr_done or '--force' in fix
        checks = check_folder(folder, title)
    return checks, round_no


def version_check(folder, expected=None):
    """A fresh capture must come from the extension version on main; anything else means Brave was not reloaded."""
    expected = expected or repo_version()
    made_by = zip_version(folder)
    return Check('extension_version', made_by == expected,
                 f'ZIP made by extension {made_by or "1.3 or older (no capture.json)"}, main is {expected}'
                 + ('' if made_by == expected else '; reload the extension in Brave once'))


def download_check(zip_path, record=brave.download_record):
    """The ZIP must have been saved by the extension through chrome.downloads and completed, per Brave's history."""
    found = record(zip_path)
    if found is None:
        return Check('download', False, f'{Path(zip_path).name} not in Brave\'s download history')
    by_extension, complete = found
    detail = ('saved by the extension' if by_extension else 'saved by a page link, not the extension') + (
        ', complete' if complete else ', not complete')
    return Check('download', by_extension and complete, detail)


def not_captured(asin, check, reason, recapture):
    """The result for a book whose capture never produced a ZIP. These failures stop the list (capture_books.py)."""
    print(f'FAIL {check:18} {reason}', flush=True)
    return {'asin': asin, 'recapture': recapture, 'blocking_failures': [check], 'warnings': [], 'error': reason}


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
        problem = folder_problem()
        if problem:
            raise RuntimeError(f'Brave loads the extension from this checkout, and it is not main: {problem}')
        # A tab left from an earlier run may still be capturing this book; a second one must not start beside it
        # (2026-10-10: a resumed run opened Automate the Boring Stuff again while the first tab was still paging).
        if not ensure_tab_closed(asin):
            return not_captured(asin, 'tab_before', 'a Brave tab on this book was already open and could not be closed',
                                recapture)
        before = set(downloads.glob('*.zip')) | skip_notices(downloads, asin)
        open_in_brave(asin, domain, recapture)
        source = wait_for_zip(downloads, before, asin=asin)
        tab_closed = ensure_tab_closed(asin)
        if source is None:
            return not_captured(asin, 'no_zip', f'no ZIP in {downloads} within {ZIP_WAIT_S // 60} minutes; the book tab '
                                f'was {"closed" if tab_closed else "left open"}', recapture)
        if source.suffix == '.json':
            reason = json.loads(source.read_text(encoding='utf-8')).get('reason', 'no reason given')
            return not_captured(asin, 'capture_refused', f'the extension did not capture: {reason}', recapture)
    folder = source.with_suffix('') if source.suffix == '.zip' else source
    try:
        run([PYTHON, str(HERE / 'img2txt.py'), str(source), '--model', model] + (['--title', title] if title else []))
        checks, rounds = correct(folder, title)
    except subprocess.CalledProcessError as e:
        if e.returncode != DAILY_LIMIT_EXIT:
            raise
        # Not the book's fault, and the capture is good: capture_books.py waits and converts this folder again.
        print(f'FAIL {DAILY_LIMIT_CHECK:18} Gemini\'s requests for today are used up', flush=True)
        return {'asin': asin, 'source': str(folder), 'recapture': recapture, 'blocking_failures': [DAILY_LIMIT_CHECK],
                'warnings': [], 'error': 'Gemini daily request limit; resume after midnight Pacific (16:00 or 17:00 JST)'}
    if tab_closed is not None:
        # Blocking, but not a capture defect: the runner records it and goes on without recapturing.
        checks.append(Check('tab_closed', tab_closed, f'book tab {"closed" if tab_closed else "still open"} after the capture'))
        checks.append(version_check(folder))
        checks.append(download_check(source))
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
