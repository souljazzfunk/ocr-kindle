#!/usr/bin/env python3
"""
Work through a list of books: capture, convert and check each one, redo it when the capture was unusable,
and go on to the next by the checks' verdict, with no one asked in between.

    venv/bin/python capture_books.py books.tsv

books.tsv has one `ASIN<TAB>title` per line (`#` lines are skipped). Progress goes to books.progress.json next
to it: a book marked done or failed is not run again, so an interrupted run resumes at the next book.
At the end the summary lists what was done and what failed and why; the exit code is 1 when anything failed.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import brave
import capture_book

# A failure in these checks means the ZIP itself is unusable, so the book is captured again (once).
CAPTURE_CHECKS = {'pages', 'text', 'start_position', 'end_position'}
RECAPTURES = 1


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


def run_book(asin, title, options, convert=None, again=False):
    """again: an earlier run's capture was unusable, so the extension (which saved the ASIN) must capture it anew."""
    convert = convert or capture_book.convert
    for attempt in range(1, RECAPTURES + 2):
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
    args = parser.parse_args()

    problem = brave.prompt_problem()
    if problem:
        sys.exit(f'Not starting: {problem}')
    progress_path = args.list.with_suffix('.progress.json')
    progress = load_progress(progress_path)
    options = {'model': args.model, 'domain': args.domain, 'downloads': args.downloads}
    for asin, title in read_list(args.list):
        if progress.get(asin, {}).get('verdict') in ('done', 'failed'):
            continue
        print(f'=== {title or asin} ({asin})', flush=True)
        try:
            again = bool(CAPTURE_CHECKS & set(progress.get(asin, {}).get('failures', [])))
            verdict, data = run_book(asin, title, options, again=again)
        except Exception as e:  # a crash in one book must not stop the list, and must be in the summary
            verdict, data = 'failed', {'error': str(e), 'blocking_failures': ['crash'], 'warnings': []}
        progress[asin] = {'title': title, 'verdict': verdict, 'failures': data['blocking_failures'],
                          'warnings': data['warnings'], 'error': data.get('error'), 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'=== {verdict}: {title or asin}' + (f' ({", ".join(data["blocking_failures"])})' if verdict == 'failed' else ''), flush=True)

    done = [p['title'] or a for a, p in progress.items() if p['verdict'] == 'done']
    failed = {p['title'] or a: p['failures'] for a, p in progress.items() if p['verdict'] == 'failed'}
    print(f'\n{len(done)} done, {len(failed)} failed')
    for title, failures in failed.items():
        print(f'  failed {title}: {", ".join(failures)}')
    sys.exit(bool(failed))


if __name__ == '__main__':
    main()
