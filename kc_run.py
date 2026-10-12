#!/usr/bin/env python3
"""One step of the book list per run: pull main, run one stage of the next Ready book, record the result, exit 0.

    venv/bin/python kc_run.py capture     # the capture job: one book through the extension channel
    venv/bin/python kc_run.py convert     # the convert job: one book converted and checked, or one published

launchd keeps both jobs running (launchd/) and starts each again after it exits, so code pulled from main is used
from the next step with no restart of anything. The two jobs never write the same files: the capture job writes a
book's pages, book.json and its capture records; the convert job writes what the converter makes beside the pages and
the convert and publish records. Which book and which stage come next is derived from the book folders (book.py);
a book that fails is parked and the job goes on, and a failure that would fail every book pauses the job until it
can resume by itself (book.RULES).
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

import book as books
import capture_book
import locks
from book import JOBS, ROOT, Library, Parked, Ready, read_json, record_failure

HERE = Path(__file__).resolve().parent
IDLE_S = 60  # with nothing to do, or paused, a step waits this long before it exits and launchd starts the next


AHEAD = 2


def pull_main(checkout=HERE, git=None):
    """Pull main. Returns None, or why the checkout cannot follow main (local changes stand in the way)."""
    git = git or (lambda *args: subprocess.run(['git', '-C', str(checkout), *args], capture_output=True, text=True))
    with locks.git_checkout(checkout):  # a capture's check of the extension folder fetches too
        if git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip() != 'main':
            return None  # capture_book.capture refuses a checkout that is not main, and says why
        pulled = git('pull', '--ff-only', '-q', 'origin', 'main')
        if pulled.returncode == 0:
            return None
        if git('status', '--porcelain').stdout.strip():
            return f'git pull --ff-only failed: {pulled.stderr.strip()}'
    print(f'could not pull main ({pulled.stderr.strip()}); going on with the code here', flush=True)
    return None


def failed_checks(data):
    return '; '.join(f'{c["name"]}: {c["detail"]}' for c in data.get('checks', []) if not c['ok'] and c['blocking'])


def capture(library, listed, book, ready, current, args):
    attempt = book.attempt() + 1 if ready.recapture else 1
    book.start(args.now)
    folder = book.folder / 'pages'
    title, failure = capture_book.capture(book.asin, book.title, book.domain, folder, recapture=ready.recapture)
    if failure:
        return record_failure(library, listed, book, 'capture', failure['blocking_failures'], failure.get('error'),
                              attempt=attempt, current=current, now=args.now, config=args.config)
    book.succeed('capture', {'pages': str(folder), 'title': title, 'attempt': attempt, 'via': 'extension',
                             'version': current['capture']})
    return 'captured'


def convert(library, listed, book, ready, current, args):
    captured = book.record('capture')
    data = capture_book.convert_folder(book.asin, book.title or book.asin, args.model, book.pages,
                                       recapture=captured.get('attempt', 1) > 1)
    if data['blocking_failures']:
        return record_failure(library, listed, book, 'convert', data['blocking_failures'],
                              data.get('error') or failed_checks(data), upstream=captured['at'],
                              attempt=captured.get('attempt', 1), current=current, now=args.now, config=args.config)
    book.succeed('convert', {'report': str(Path(data['folder']) / 'report.json'), 'version': current['convert']},
                 upstream=captured['at'])
    return 'converted'


def publish(library, listed, book, ready, current, args):
    converted = book.record('convert')
    copied = capture_book.publish(book.pages, book.title or book.asin, replaces=(book.record('publish') or {}).get('drive'))
    capture_book.start_wiki_ingest(read_json(converted['report']))
    # From the first capture to Drive, counted once: 英語教育幻想 took 66.7 minutes over six captures (2026-10-12).
    # A book published again after a converter change keeps the time of its first publish.
    before, started = book.record('publish') or {}, read_json(book.folder / 'start.json') or {}
    seconds = before.get('seconds', round(args.now - started['at']) if 'at' in started else None)
    slow = seconds is not None and seconds > capture_book.BUDGETS['book']
    book.succeed('publish', {'drive': str(copied) if copied else None, 'version': current['publish'],
                             'seconds': seconds, 'slow': slow}, upstream=converted['at'])
    capture_book.publish_report(book.asin, book.title, read_json(converted['report']), book.record('publish'),
                                args.config)
    return f'published slowly ({seconds // 60} min)' if slow else 'published'


STEPS = {'capture': capture, 'convert': convert, 'publish': publish}


def default_lists(root, downloads):
    return [Path(root) / 'books.tsv', *sorted(Path(downloads).glob('library-*.json'))]


def progress_of(lists):
    """books.progress.json beside each list, read only to carry a book over the first time its folder is made."""
    progress = {}
    for path in lists:
        progress.update(read_json(Path(path).with_suffix('.progress.json')) or {})
    return progress


def step(job, root=ROOT, lists=None, downloads=Path.home() / 'Downloads', model='vision', checkout=HERE,
         sleep=time.sleep, now=time.time, config=None):
    """Run one step of job. Returns what it did, for the log. config (default config.env) names the Drive folders.

    After each convert step every book's state goes to Drive (_books.json beside the reports): on 2026-10-12 the cloud
    could not tell whether four books still in their 10-11 form were queued, parked or not listed at all."""
    config = capture_book.load_config() if config is None else config
    done = _step(job, root, lists, downloads, model, checkout, sleep, now, config)
    if job == 'convert':
        lists = lists or default_lists(root, downloads)
        listed = Library(root).add(lists, {}, write=False)
        current = books.versions(checkout)
        capture_book.publish_states([{'asin': b.asin, 'title': b.title, 'state': describe(b.state(current)),
                                      'drive': Path((b.record('publish') or {}).get('drive') or '').name or None}
                                     for b in listed], done, config)
    return done


def describe(state):
    if isinstance(state, Ready):
        return f'ready to {state.stage}' + (' again' if state.recapture else '')
    if isinstance(state, Parked):
        return f'parked at {state.stage}: ' + ', '.join(state.failure.get('checks') or [])
    return 'done'


def _step(job, root, lists, downloads, model, checkout, sleep, now, config):
    library = Library(root)
    library.root.mkdir(parents=True, exist_ok=True)
    problem = pull_main(checkout)
    if problem:
        library.set_pause(job, {'stage': None, 'checks': ['git'], 'reason': problem, 'version': None, 'until': None})
        sleep(IDLE_S)
        return f'paused: {problem}'
    if (library.pause(job) or {}).get('checks') == ['git']:
        library.clear_pause(job)  # the pull works again
    lists = lists or default_lists(root, downloads)
    # Only the capture job makes book folders: it owns book.json and the capture records a migration writes.
    listed = library.add(lists, progress_of(lists), write=job == 'capture')
    current = books.versions(checkout)
    moment = now()
    pause = library.active_pause(job, current, moment)
    if pause:
        sleep(IDLE_S)
        return f'paused: {pause["reason"]}'
    options = argparse.Namespace(model=model, now=moment, config=config)
    states = [(book, book.state(current)) for book in listed]
    # A book is timed from its first capture to Drive. How to Write a Lot (2026-10-12) took 85 minutes for 5 minutes of
    # work, waiting behind captured books for the convert job; so a new capture waits while two new books do.
    waiting = sum(isinstance(s, Ready) and s.stage not in JOBS['capture'] and (b.folder / 'start.json').exists()
                  and not b.record('publish') for b, s in states)
    if job == 'capture' and waiting >= AHEAD:
        sleep(IDLE_S)
        return 'waiting for the convert job'
    # A book never published goes before one published already that is ready again (a new capture). After #107 the
    # convert job redid published books for minutes while How to Write a Lot waited (2026-10-12).
    for book, state in sorted(states, key=lambda bs: bool(bs[0].record('publish'))):
        if isinstance(state, Ready) and state.stage in JOBS[job]:
            print(f'=== {state.stage}{" again" if state.recapture else ""}: {book.title or book.asin} ({book.asin})',
                  flush=True)
            try:
                outcome = STEPS[state.stage](library, listed, book, state, current, options)
            except Exception as e:  # recorded like any other failure, so the book is parked with the reason
                outcome = record_failure(library, listed, book, state.stage, ['crash'], f'{type(e).__name__}: {e}',
                                         upstream=_upstream(book, state.stage), current=current, now=moment,
                                         config=config)
            return f'{outcome}: {book.asin}'
    sleep(IDLE_S)
    return 'nothing to do'


def _upstream(book, stage):
    names = [s.name for s in books.STAGES]
    before = names[names.index(stage) - 1] if names.index(stage) else None
    return (book.record(before) or {}).get('at') if before else None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run one step of the Kindle book list')
    parser.add_argument('job', choices=sorted(JOBS))
    parser.add_argument('--root', type=Path, default=ROOT, help=f'where the book folders live (default {ROOT})')
    parser.add_argument('--list', type=Path, action='append',
                        help='books.tsv or library-<domain>.json; repeatable (default <root>/books.tsv and '
                             '<downloads>/library-*.json)')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--model', default='vision')
    args = parser.parse_args(argv)
    print(step(args.job, args.root, args.list, args.downloads, args.model), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
