#!/usr/bin/env python3
"""
Work through a list of books: capture, convert and check each one, redo it when the capture was unusable,
and go on to the next by the checks' verdict, with no one asked in between.

    venv/bin/python capture_books.py books.tsv

books.tsv has one `ASIN<TAB>title` per line (`#` lines are skipped). Progress goes to books.progress.json next
to it: a book marked done or failed is not run again, so an interrupted run resumes at the next book. A book the
list stopped on (a browser or extension failure, STOP_CHECKS) is run again, as #recapture, unless a good capture of it
was saved after it stopped: then that ZIP is converted with no new capture. A book that failed its Markdown checks is
converted again from its ZIP, once, when the converter's code has changed since.
While one book converts, the next is captured in the browser (one capture and one conversion at a time); books are recorded
in list order, and a stop waits for the book already captured to be converted and recorded.
At the end the summary lists what was done and what failed and why; the exit code is 1 when anything failed.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import browser
import capture_book
import check_book
import locks
from check_book import CAPTURE

# A failure in these checks means the ZIP itself is unusable, so the book is captured again (once).
CAPTURE_CHECKS = {'pages', 'text', 'start_position', 'end_position'}
# A capture that ended with no ZIP and no reason (the tab vanished, 2026-10-11 For 91 Days In Tokyo) is also tried
# once more in a fresh tab before the list stops on it.
NO_ZIP_CHECKS = {'tab_gone', 'no_zip', 'capture_stall'}
RECAPTURES = 1
# Failures that come from the browser or the extension, not from one book: every later book would fail the same
# way, so the list stops at the first book that still has one (after its recapture). On 2026-10-10 a run went on
# while each ZIP opened a save dialog, and 1.3 was called fixed before any capture on the Mac had shown it.
# A capture that never made a ZIP (refused, cut off by a turn cap, a tab left from an earlier run, an hour with no ZIP)
# stops it too: on 2026-10-10 a resumed run opened a second tab on a book the first was still paging through.
STOP_CHECKS = {'download', 'extension_version', 'tab_closed', 'start_position', 'end_position',
               'capture_refused', 'capture_stop', 'tab_before', 'no_zip', 'tab_gone', 'capture_stall'}


HERE = Path(__file__).resolve().parent


def converter_version(here=HERE):
    """A digest of the converter's code. A book that failed its Markdown checks under other code is converted again from
    its ZIP: on 2026-10-11 Dilla Time and Building a Second Brain passed once fixes were merged, but their entries
    still said failed, so the list never went back to them."""
    digest = hashlib.sha256()
    for path in sorted(Path(here).glob('*.py')):
        if not path.name.startswith('test_'):
            digest.update(path.name.encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()[:12]


def stale_failure(entry, converter):
    """Whether entry failed on the converter (not the capture, not the Kindle-app-only refusal) under other code."""
    failures = set(entry.get('failures', []))
    return (entry.get('verdict') == 'failed' and not failures & (STOP_CHECKS | {capture_book.APP_ONLY})
            and entry.get('converter') != converter)


def stale_pass(entry, converter, downloads, title, check=None):
    """The saved capture of a book that passed under older code but fails a check of the code now, to convert again;
    else None. Python Crash Course passed before code_blocks existed (2026-10-11), and a done book was never looked at
    again. Only the converter's checks count: a capture check an old ZIP fails would send the book back to the browser."""
    if entry.get('verdict') != 'done' or entry.get('converter') == converter:
        return None
    source = earlier_capture(entry, downloads, title)
    folder = source and (source.with_suffix('') if source.suffix == '.zip' else source)
    if not folder or not folder.is_dir():
        return None
    failed = {c.name for c in (check or check_book.check_folder)(folder, title) if c.blocking and not c.ok}
    if not failed or failed & (CAPTURE_CHECKS | STOP_CHECKS | {'pages', 'text'}):
        return None
    entry['failures'] = sorted(failed)
    return source


def update_checkout(git=None, settle=lambda: None):
    """Pull main before each book, so a fix merged mid-list is used without anyone asking for it.

    Returns 'same', 'python' (this run restarts to load it), 'extension' (the browser must reload it first) or an error.
    On 2026-10-10 each merge needed Laiken to type "pull main" to a session on the Mac.
    settle() finishes the book still converting before the pull, so no conversion runs on half old, half new code.
    """
    git = git or (lambda *args: subprocess.run(['git', '-C', str(HERE), *args], capture_output=True, text=True))
    with locks.git_checkout(HERE):  # a capture checking the extension folder fetches too
        if git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip() != 'main':
            return 'same'  # capture_book.py refuses a checkout that is not main and says why
        before = git('rev-parse', 'HEAD').stdout.strip()
        if git('fetch', '-q', 'origin', 'main').returncode == 0 and git('rev-parse', 'FETCH_HEAD').stdout.strip() == before:
            return 'same'
    settle()  # not under the lock: the books it waits for check the extension folder
    with locks.git_checkout(HERE):
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
    if capture and capture <= CAPTURE_CHECKS | NO_ZIP_CHECKS and attempt <= RECAPTURES:
        return 'recapture'
    return 'failed'


def comparable(text):
    return re.sub(r'[^a-z0-9]', '', (text or '').lower())


def saved_capture(downloads, title, since, version=None, any_version=False):
    """The newest ZIP of this book saved after since (epoch seconds) by the extension on main, run to the end; else None.
    any_version: a ZIP from an older extension will do (a book converted again after a converter fix).

    On 2026-10-10 Facing the Mountain was captured well on 1.7 after the list had stopped on it, but its entry still
    said stopped, so the resumed list paged through all 673 screens again.
    """
    version = version or capture_book.repo_version()
    key = comparable(title)
    if not key:
        return None
    for path in sorted(Path(downloads).glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True):
        stem = comparable(re.sub(r' \(\d+\)$', '', path.stem))  # the browser's uniquified copies
        if path.stat().st_mtime <= since or not stem or not (stem.startswith(key) or key.startswith(stem)):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                made = json.loads(archive.read(CAPTURE))
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError):
            continue
        if (any_version or made.get('extension_version') == version) and not made.get('stopped'):
            return path
    return None


def earlier_capture(entry, downloads, title):
    """The ZIP an earlier run converted (its recorded source, else the newest one of this book that ran to the end)."""
    source = Path(entry.get('source') or '')
    if source.suffix == '.zip' and source.is_file():
        return source
    return saved_capture(downloads, title, 0, any_version=True)


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


class Converting:
    """Held around a conversion: one at a time, and started is set once it begins, so the next capture can start."""

    def __init__(self, slot):
        self.slot, self.started = slot, threading.Event()

    def __enter__(self):
        self.slot.acquire()
        self.started.set()

    def __exit__(self, *exc):
        self.slot.release()


class Job:
    """One book worked on in its own thread: its capture, then its conversion while the next book is captured."""

    def __init__(self, asin, title, work, converting):
        self.asin, self.title, self.converting, self.outcome = asin, title, converting, None
        self.thread = threading.Thread(target=self._run, args=(work,), daemon=True)
        self.thread.start()

    def _run(self, work):
        try:
            self.outcome = work()
        except Exception as e:  # a crash in one book must not stop the list, and must be in the summary
            self.outcome = 'failed', {'error': str(e), 'blocking_failures': ['crash'], 'warnings': []}

    def captured(self, poll=1):
        """Wait until the browser is free of this book: its conversion began, or it ended (refused, failed, done)."""
        while self.thread.is_alive() and not self.converting.started.wait(poll):
            pass

    def result(self):
        self.thread.join()
        return self.outcome


def main():
    parser = argparse.ArgumentParser(description='Capture, convert and check a list of Kindle books in order')
    parser.add_argument('list', type=Path, help='ASIN<TAB>title per line')
    parser.add_argument('--model', default='vision')
    parser.add_argument('--domain', default='read.amazon.com')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--keep-going', action='store_true',
                        help='Record a book whose Markdown failed its checks and go on (default: stop there)')
    args = parser.parse_args()

    problem = browser.prompt_problem()
    if problem:
        sys.exit(f'Not starting: {problem}')
    try:
        # One run per list: two runs would each write back the whole progress file over the other's results.
        locks.keep(args.list.with_suffix('.lock'), wait=False)
    except locks.Busy:
        sys.exit(f'Not starting: another run is working through {args.list.name}. Run this once it has ended.')
    progress_path = args.list.with_suffix('.progress.json')
    progress = load_progress(progress_path)
    browser_tab, slot = threading.Lock(), threading.Lock()
    options = {'model': args.model, 'domain': args.domain, 'downloads': args.downloads, 'capturing': browser_tab}
    converter = converter_version()
    jobs = []  # started in list order: at most one converting and one in the browser

    def record(job):
        """Write one finished book to the progress file. Returns why the list must stop, or None."""
        verdict, data = job.result()
        asin, title = job.asin, job.title
        stop = STOP_CHECKS & set(data['blocking_failures'])
        if stop:
            verdict = 'stopped'  # not the book's fault: the next run tries it again
        gemini = capture_book.GEMINI_CHECK in data['blocking_failures']
        if gemini:
            verdict = 'waiting'  # Gemini failed; every later book would fail its layout too
        progress[asin] = {'title': title, 'verdict': verdict, 'failures': data['blocking_failures'],
                          'warnings': data['warnings'], 'error': data.get('error'), 'at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                          'converter': converter}
        if data.get('source'):
            progress[asin]['source'] = data['source']
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'=== {verdict}: {title or asin}' + (f' ({", ".join(data["blocking_failures"])})' if verdict != 'done' else ''), flush=True)
        if gemini:
            return (f'Stopped the list: Gemini failed on {title or asin}: {data.get("error")}\nOnce the cause is gone, '
                    'run the same command: this book is converted from its folder with no new capture.')
        if stop:
            return (f'Stopped the list: {", ".join(sorted(stop))} failed on {title or asin}. These come from the browser or the '
                    'extension, so every later book would fail too. Fix the cause, then run the same command to resume.')
        # Laiken, 2026-10-10: try one book, and run the rest only once a book comes out right. Every later book goes
        # through the same converter, so a book that fails its Markdown checks stops the list until the fix is in.
        if verdict == 'failed' and not args.keep_going and capture_book.APP_ONLY not in data['blocking_failures']:
            return (f'Stopped the list: {title or asin} failed {", ".join(data["blocking_failures"])}. Fix the converter, '
                    'reconvert this book from its ZIP until it passes, then run the same command to go on.')
        return None

    def settle(keep=0):
        """Record finished books in list order until at most keep are left; stop the list once every book has ended."""
        while len(jobs) > keep:
            stop = record(jobs.pop(0))
            if stop:
                for job in jobs:  # a book already captured is converted and recorded, not thrown away
                    record(job)
                print(f'\n{stop}', flush=True)
                sys.exit(2)

    def make_room():
        """Wait until the next book may use the browser: the books before have ended, but for one that is converting."""
        settle(keep=1)
        if jobs:
            jobs[0].captured()
            if not jobs[0].thread.is_alive():
                settle()

    for asin, title in read_list(args.list):
        update = update_checkout(settle=settle)
        if update == 'python':
            print('=== main changed: restarting with it (the list resumes here)', flush=True)
            os.environ.pop(locks.HELD, None)  # exec lets go of the list lock, so the new run must take it again
            os.execv(sys.executable, [sys.executable, *sys.argv])
        if update == 'extension':
            print('\nStopped the list: main changed the extension. Ask Laiken to reload it once in ' + browser.NAME + ', then run the '
                  'same command to resume.', flush=True)
            sys.exit(2)
        if update != 'same':
            print(f'=== {update}; going on with the code already here', flush=True)
        entry = progress.get(asin, {})
        waiting = entry.get('verdict') == 'waiting' and entry.get('source')
        stale = stale_failure(entry, converter)
        earlier = stale and earlier_capture(entry, args.downloads, title)
        if entry.get('verdict') == 'done' and entry.get('converter') != converter:
            stale = True
            try:
                earlier = stale_pass(entry, converter, args.downloads, title)
            except Exception as e:  # a folder the checks cannot read is left as it was
                print(f'=== could not check {title or asin} again: {e}', flush=True)
                earlier = None
        if stale and not earlier:
            entry['converter'] = converter  # nothing to convert again: look once per converter change
            progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding='utf-8')
        if (entry.get('verdict') == 'done' and not earlier) or (
                entry.get('verdict') == 'failed' and not STOP_CHECKS & set(entry.get('failures', [])) and not earlier):
            continue
        make_room()
        print(f'=== {title or asin} ({asin})', flush=True)
        converting = Converting(slot)
        book_options = {**options, 'converting': converting}
        if earlier:
            print(f'=== converting {earlier.name} again: it {"failed" if entry["verdict"] == "failed" else "now fails"} '
                  f'{", ".join(entry["failures"])} under {"older" if entry["verdict"] == "failed" else "this"} code',
                  flush=True)
            # Its capture was used before, so a capture defect found now is redone as for any unusable ZIP.
            work = (lambda a=asin, t=title, o={**book_options, 'source': earlier}: run_book(a, t, o, again=True))
        else:
            # Any earlier attempt may have saved the ASIN in the extension, which then refuses #autocapture.
            again = asin in progress and not waiting
            # A book stopped by a Gemini failure was captured well: convert its folder again, without the browser.
            if waiting:
                book_options['source'] = Path(entry['source'])
            saved = again and entry.get('at') and saved_capture(
                args.downloads, title, time.mktime(time.strptime(entry['at'], '%Y-%m-%dT%H:%M:%S')))
            if saved:
                print(f'=== converting {saved.name}, captured after the list stopped on this book', flush=True)
                book_options, again = {**book_options, 'source': saved}, False
            work = (lambda a=asin, t=title, o=book_options, g=again: run_book(a, t, o, again=g))
        jobs.append(Job(asin, title, work, converting))
    settle()

    done = [p['title'] or a for a, p in progress.items() if p['verdict'] == 'done']
    failed = {p['title'] or a: p['failures'] for a, p in progress.items() if p['verdict'] == 'failed'}
    print(f'\n{len(done)} done, {len(failed)} failed')
    for title, failures in failed.items():
        print(f'  failed {title}: {", ".join(failures)}')
    sys.exit(bool(failed))


if __name__ == '__main__':
    main()
