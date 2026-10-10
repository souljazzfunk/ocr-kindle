#!/usr/bin/env python3
"""
Work through a list of books: capture, convert and check each one, redo it when the capture was unusable,
and go on to the next by the checks' verdict, with no one asked in between.

    venv/bin/python capture_books.py books.tsv

books.tsv has one `ASIN<TAB>title` per line (`#` lines are skipped). Progress goes to books.progress.json next
to it: a book marked done or failed is not run again, so an interrupted run resumes at the next book. A book the
list stopped on (a Brave or extension failure, STOP_CHECKS) is run again, as #recapture, unless a good capture of it
was saved after it stopped: then that ZIP is converted with no new capture.
At the end the summary lists what was done and what failed and why; the exit code is 1 when anything failed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import brave
import capture_book
from check_book import CAPTURE

# A failure in these checks means the ZIP itself is unusable, so the book is captured again (once).
CAPTURE_CHECKS = {'pages', 'text', 'start_position', 'end_position'}
RECAPTURES = 1
# Failures that come from the browser or the extension, not from one book: every later book would fail the same
# way, so the list stops at the first book that still has one (after its recapture). On 2026-10-10 a run went on
# while each ZIP opened a save dialog, and 1.3 was called fixed before any capture on the Mac had shown it.
# A capture that never made a ZIP (refused, cut off by a turn cap, a tab left from an earlier run, an hour with no ZIP)
# stops it too: on 2026-10-10 a resumed run opened a second tab on a book the first was still paging through.
STOP_CHECKS = {'download', 'extension_version', 'tab_closed', 'start_position', 'end_position',
               'capture_refused', 'capture_stop', 'tab_before', 'no_zip', 'tab_gone'}


HERE = Path(__file__).resolve().parent


def update_checkout(git=None):
    """Pull main before each book, so a fix merged mid-list is used without anyone asking for it.

    Returns 'same', 'python' (this run restarts to load it), 'extension' (Brave must reload it first) or an error.
    On 2026-10-10 each merge needed Laiken to type "pull main" to a session on the Mac.
    """
    git = git or (lambda *args: subprocess.run(['git', '-C', str(HERE), *args], capture_output=True, text=True))
    if git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip() != 'main':
        return 'same'  # capture_book.py refuses a checkout that is not main and says why
    before = git('rev-parse', 'HEAD').stdout.strip()
    pulled = git('pull', '--ff-only', '-q', 'origin', 'main')
    if pulled.returncode:
        return f'could not pull main: {pulled.stderr.strip()}'
    after = git('rev-parse', 'HEAD').stdout.strip()
    if before == after:
        return 'same'
    changed = git('diff', '--name-only', before, after).stdout.split()
    if any(path.startswith('extension/') and not path.endswith('.test.js') for path in changed):
        return 'extension'
    return 'python'


def read_list(path):
    books = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            asin, _, title = line.partition('\t')
            books.append((asin.strip(), title.strip()))
    return books


def load_progress(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def decide(data, attempt):
    """'done', 'recapture' or 'failed' from one capture_book result and the attempt number (1-based)."""
    failures = data['blocking_failures']
    if not failures:
        return 'done'
    # A tab left open is recorded but is no reason to capture again.
    capture = set(failures) - {'tab_closed'}
    if capture and capture <= CAPTURE_CHECKS and attempt <= RECAPTURES:
        return 'recapture'
    return 'failed'


def comparable(text):
    return re.sub(r'[^a-z0-9]', '', (text or '').lower())


def saved_capture(downloads, title, since, version=None):
    """The newest ZIP of this book saved after since (epoch seconds) by the extension on main, run to the end; else None.

    On 2026-10-10 Facing the Mountain was captured well on 1.7 after the list had stopped on it, but its entry still
    said stopped, so the resumed list paged through all 673 screens again.
    """
    version = version or capture_book.repo_version()
    key = comparable(title)
    if not key:
        return None
    for path in sorted(Path(downloads).glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True):
        stem = comparable(re.sub(r' \(\d+\)$', '', path.stem))  # Brave's uniquified copies
        if path.stat().st_mtime <= since or not stem or not (stem.startswith(key) or key.startswith(stem)):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                made = json.loads(archive.read(CAPTURE))
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError):
            continue
        if made.get('extension_version') == version and not made.get('stopped'):
            return path
    return None


def run_book(asin, title, options, convert=None, again=False):
    """again: an earlier run's capture was unusable, so the extension (which saved the ASIN) must capture it anew."""
    convert = convert or capture_book.convert
    for attempt in range(1, RECAPTURES + 2):
        if attempt > 1:
            options = {key: value for key, value in options.items() if key != 'source'}  # that ZIP was unusable
        data = convert(asin, title, recapture=again or attempt > 1, **options)
        verdict = decide(data, attempt)
        if verdict != 'recapture':
            return verdict, data
    return 'failed', data  # not reached: decide() stops recapturing at RECAPTURES


def main():
    parser = argparse.ArgumentParser(description='Capture, convert and check a list of Kindle books in order')
    parser.add_argument('list', type=Path, help='ASIN<TAB>title per line')
    parser.add_argument('--model', default='vision')
    parser.add_argument('--domain', default='read.amazon.com')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--keep-going', action='store_true',
                        help='Record a book whose Markdown failed its checks and go on (default: stop there)')
    args = parser.parse_args()

    problem = brave.prompt_problem()
    if problem:
        sys.exit(f'Not starting: {problem}')
    progress_path = args.list.with_suffix('.progress.json')
    progress = load_progress(progress_path)
    options = {'model': args.model, 'domain': args.domain, 'downloads': args.downloads}
    for asin, title in read_list(args.list):
        update = update_checkout()
        if update == 'python':
            print('=== main changed: restarting with it (the list resumes here)', flush=True)
            os.execv(sys.executable, [sys.executable, *sys.argv])
        if update == 'extension':
            print('\nStopped the list: main changed the extension. Ask Laiken to reload it once in Brave, then run the '
                  'same command to resume.', flush=True)
            sys.exit(2)
        if update != 'same':
            print(f'=== {update}; going on with the code already here', flush=True)
        entry = progress.get(asin, {})
        waiting = entry.get('verdict') == 'waiting' and entry.get('source')
        if entry.get('verdict') == 'done' or (
                entry.get('verdict') == 'failed' and not STOP_CHECKS & set(entry.get('failures', []))):
            continue
        print(f'=== {title or asin} ({asin})', flush=True)
        try:
            # Any earlier attempt may have saved the ASIN in the extension, which then refuses #autocapture.
            again = asin in progress and not waiting
            # A book stopped by a Gemini failure was captured well: convert its folder again, without Brave.
            book_options = {**options, 'source': Path(entry['source'])} if waiting else options
            saved = again and entry.get('at') and saved_capture(
                args.downloads, title, time.mktime(time.strptime(entry['at'], '%Y-%m-%dT%H:%M:%S')))
            if saved:
                print(f'=== converting {saved.name}, captured after the list stopped on this book', flush=True)
                book_options, again = {**options, 'source': saved}, False
            verdict, data = run_book(asin, title, book_options, again=again)
        except Exception as e:  # a crash in one book must not stop the list, and must be in the summary
            verdict, data = 'failed', {'error': str(e), 'blocking_failures': ['crash'], 'warnings': []}
        stop = STOP_CHECKS & set(data['blocking_failures'])
        if stop:
            verdict = 'stopped'  # not the book's fault: the next run tries it again
        gemini = capture_book.GEMINI_CHECK in data['blocking_failures']
        if gemini:
            verdict = 'waiting'  # Gemini failed; every later book would fail its layout too
        progress[asin] = {'title': title, 'verdict': verdict, 'failures': data['blocking_failures'],
                          'warnings': data['warnings'], 'error': data.get('error'), 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
        if gemini:
            progress[asin]['source'] = data['source']
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'=== {verdict}: {title or asin}' + (f' ({", ".join(data["blocking_failures"])})' if verdict != 'done' else ''), flush=True)
        if gemini:
            print(f'\nStopped the list: Gemini failed on {title or asin}: {data.get("error")}\nOnce the cause is gone, '
                  'run the same command: this book is converted from its folder with no new capture.')
            sys.exit(2)
        if stop:
            print(f'\nStopped the list: {", ".join(sorted(stop))} failed on {title or asin}. These come from Brave or the '
                  'extension, so every later book would fail too. Fix the cause, then run the same command to resume.')
            sys.exit(2)
        # Laiken, 2026-10-10: try one book, and run the rest only once a book comes out right. Every later book goes
        # through the same converter, so a book that fails its Markdown checks stops the list until the fix is in.
        if verdict == 'failed' and not args.keep_going:
            print(f'\nStopped the list: {title or asin} failed {", ".join(data["blocking_failures"])}. Fix the converter, '
                  'reconvert this book from its ZIP until it passes, then run the same command to go on.')
            sys.exit(2)

    done = [p['title'] or a for a, p in progress.items() if p['verdict'] == 'done']
    failed = {p['title'] or a: p['failures'] for a, p in progress.items() if p['verdict'] == 'failed'}
    print(f'\n{len(done)} done, {len(failed)} failed')
    for title, failures in failed.items():
        print(f'  failed {title}: {", ".join(failures)}')
    sys.exit(bool(failed))


if __name__ == '__main__':
    main()
