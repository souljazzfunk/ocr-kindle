#!/usr/bin/env python3
"""
Capture one book from Kindle Cloud Reader through the extension channel (channel.py) and turn it into checked Markdown,
in the order CLAUDE.md sets.

    venv/bin/python capture_book.py B07HNMJV57 --title "How to Write a Lot"

1. Offers the book to the extension, which opens it in Chrome, sends every page into ~/Downloads/<title>/ and closes its
   own window (never the Kindle app, never a browser restart).
2. Runs img2txt.py, then check_book.py; runs each automatic fix it names and checks again, up to ROUNDS times.
3. Writes report.json next to the book and exits 1 when a blocking check still fails.

It handles one book by hand; `kc_run.py` runs the same steps (`capture`, `convert_folder`, `publish`) one at a time for
every listed book, from the book folders (book.py). `--zip` skips the capture and starts from an existing ZIP or folder.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import json

from check_book import Check, check_folder, markdown_file
import locks
from channel import Channel, safe_name, set_aside
from extension_state import capture_version, folder_problem, repo_version
from book import write_json
from img2txt import GEMINI_ERROR, GEMINI_FAILED_EXIT, load_config, page_images

ROUNDS = 3
STALL = 'capture_stall'
UNREACHABLE = 'extension_unreachable'
APP_ONLY = 'kindle_app_only'  # Cloud Reader shows "Kindle App Is Required"; the Kindle app is used only on Laiken's word
SIGNED_OUT = 'signed_out'
EXTENSION_VERSION = 'extension_version'
EXTENSION_FOLDER = 'extension_folder'
# The extension's reason for a refusal, by its start; any other refusal is capture_refused (extension/policy.js).
REFUSALS = (('kindle app required', APP_ONLY), ('signed out', SIGNED_OUT), ('sample', 'sample_only'))
GEMINI_CHECK = 'gemini'
# The longest each step of one book may take, per screen or page so a long book keeps the same pace as a short one.
# 2026-10-12: the redesign was planned and shipped without timing a single step, so nobody could say it got faster.
# About twice 英語教育幻想's pace on extension 2.3: 0.37 to 0.47 s a screen, OCR 0.39 s and the checks 0.23 s a page.
# The Kindle app turned 1.26 to 1.29 s a screen, so it would fail this budget.
# book: seconds from a book's first capture to Drive, retries included (kc_run.publish).
BUDGETS = {'reach': 60, 'capture_per_screen': 1.0, 'ocr_per_page': 1.0, 'checks_per_page': 0.5, 'book': 20 * 60}
PYTHON = sys.executable
HERE = Path(__file__).resolve().parent
# The LLM wiki's watcher (llm-wiki/scripts/watch.sh). It takes only books whose report.json passed.
WIKI_WATCH = Path(os.environ.get('WIKI_WATCH', Path.home() / 'Documents/GitHub.nosync/llm-wiki/scripts/watch.sh'))
_channel = None


def channel():
    """The process's one channel, started on first use."""
    global _channel
    if _channel is None:
        _channel = Channel().start()
    return _channel


def run(command, **kwargs):
    print('$', ' '.join(map(str, command)), flush=True)
    return subprocess.run(command, check=True, **kwargs)


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


def speed(seconds, budgets=BUDGETS):
    """The speed check over report.json's seconds: each step against its budget. A slow book still goes to Drive; the
    check names the step, so the next fix starts from it."""
    per = {'reach': (seconds.get('reach'), 1), 'capture_per_screen': (seconds.get('capture'), seconds.get('screens')),
           'ocr_per_page': (seconds.get('ocr'), seconds.get('pages')), 'checks_per_page': (seconds.get('checks'), seconds.get('pages'))}
    taken = {step: total / count for step, (total, count) in per.items() if total is not None and count}
    slow = [f'{step} {taken[step]:.2f} s > {budgets[step]:g} s' for step in taken if taken[step] > budgets[step]]
    shown = ', '.join(f'{step} {value:.2f} s' for step, value in taken.items())
    return Check('speed', not slow, ('slow: ' + '; '.join(slow) if slow else shown or 'no timings'), blocking=False, fix=None)


def not_captured(asin, check, reason, recapture):
    """The result for a book whose capture handed over no usable pages; book.RULES says what it means for the list."""
    print(f'FAIL {check:18} {reason}', flush=True)
    return {'asin': asin, 'recapture': recapture, 'blocking_failures': [check], 'warnings': [], 'error': reason}


def report(folder, checks, extra):
    data = {**extra, 'checks': [asdict(c) for c in checks],
            'blocking_failures': [c.name for c in checks if not c.ok and c.blocking],
            'warnings': [c.name for c in checks if not c.ok and not c.blocking]}
    write_json(folder / 'report.json', data)
    for c in checks:
        mark = 'ok  ' if c.ok else ('FAIL' if c.blocking else 'warn')
        print(f'{mark} {c.name:18} {c.detail}')
    print(f'Report: {folder / "report.json"}')
    return data


def publish(folder, title, config=None):
    """Copy the checked Markdown to GOOGLE_DRIVE_FOLDER. Only this step writes there: on 2026-10-11 a comparison run of
    img2txt.py on AI for Educators replaced the book on Drive."""
    drive = (config if config is not None else load_config()).get('GOOGLE_DRIVE_FOLDER')
    book = markdown_file(folder, title)
    if not drive or not Path(drive).is_dir() or not book.exists():
        return None
    print(f'Copied {book.name} to {drive}', flush=True)
    return Path(shutil.copy2(book, drive))


def publish_report(asin, title, report, published, config):
    """Copy a published book's report and its publish record to KINDLE_REPORTS_FOLDER (default Kindle_reports beside
    GOOGLE_DRIVE_FOLDER) as <ASIN>.json, so the cloud reads results from Drive instead of asking a Mac session: on
    2026-10-12 one book took six such requests. Returns where, or None. Never raises: the book is already on Drive."""
    drive = config.get('GOOGLE_DRIVE_FOLDER')
    folder = Path(config['KINDLE_REPORTS_FOLDER']) if config.get('KINDLE_REPORTS_FOLDER') else \
        Path(drive).parent / 'Kindle_reports' if drive else None
    if folder is None or not folder.parent.is_dir():
        return None
    target = folder / f'{asin}.json'
    try:
        folder.mkdir(exist_ok=True)
        target.write_text(json.dumps({'asin': asin, 'title': title, 'publish': published, 'report': report},
                                     ensure_ascii=False, indent=1), encoding='utf-8')
    except OSError as e:
        print(f'Report of {asin} not copied to {folder}: {type(e).__name__}: {e}', flush=True)
        return None
    return target


def start_wiki_ingest(data, watch=WIKI_WATCH, spawn=subprocess.Popen):
    """Start the wiki's watcher for a book that passed, detached, so the note is written while the next book captures
    instead of at the watcher's next hourly run. The watcher queues a rerun when one is already going."""
    if data['blocking_failures'] or not watch.is_file():
        return None
    print(f'Wiki: started {watch}', flush=True)
    return spawn(['/bin/bash', str(watch)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, start_new_session=True)


def convert(asin, title=None, model='vision', domain='read.amazon.com', downloads=Path.home() / 'Downloads',
            source=None, recapture=False):
    """Capture (unless source is given), convert, check and fix one book, and publish it when it passed. Returns the
    report data."""
    started = time.time()
    if source is None:
        source = Path(downloads) / safe_name(title or asin)
        title, failure = capture(asin, title, domain, source, recapture)
        if failure:
            return failure
    data = convert_folder(asin, title, model, source, recapture)
    data['total_seconds'] = round(time.time() - started)
    if not data['blocking_failures']:
        publish(Path(data['folder']), title)
    start_wiki_ingest(data)
    return data


def capture(asin, title, domain, folder, recapture=False):
    """Capture one book through the channel into folder: (its title, None), or (None, the failure) when no usable
    capture came. A capture made by another extension version is set aside, never converted."""
    problem = folder_problem()
    if problem:
        return None, not_captured(asin, EXTENSION_FOLDER, f'Chrome loads the extension from this checkout, and it is '
                                  f'not main: {problem}', recapture)
    result = channel().capture(asin, domain, folder, recapture)
    if result.status == 'stalled':
        return None, not_captured(asin, STALL, f'the capture stopped moving: {result.reason}', recapture)
    if result.status == 'unreachable':
        return None, not_captured(asin, UNREACHABLE, f'the extension never took the job: {result.reason}', recapture)
    if result.status == 'refused':
        check = next((name for start, name in REFUSALS if result.reason.startswith(start)), 'capture_refused')
        return None, not_captured(asin, check, f'the extension did not capture: {result.reason}', recapture)
    made_by, expected = capture_version(folder), repo_version()
    if made_by != expected:
        set_aside(folder)
        return None, not_captured(asin, EXTENSION_VERSION, f'captured by extension {made_by or "(no capture.json)"}, '
                                  f'the checkout has {expected}; the extension did not reload itself', recapture)
    return title or (result.title if result.title not in ('', asin, 'kindle-book') else None), None


def convert_folder(asin, title, model, source, recapture=False):
    """OCR, check and fix one captured book (a folder, or a ZIP of extension 1.x) and write its report.json. Returns
    the report data; a Gemini failure returns the gemini check with its reason and writes no report."""
    source = Path(source)
    folder = source.with_suffix('') if source.suffix == '.zip' else source
    with locks.holding(locks.book_lock(source)):
        try:
            started = time.time()
            run([PYTHON, str(HERE / 'img2txt.py'), str(source), '--model', model] + (['--title', title] if title else []))
            ocr = time.time()
            checks, rounds = correct(folder, title)
        except subprocess.CalledProcessError as e:
            if e.returncode != GEMINI_FAILED_EXIT:
                raise
            # Not the book's fault, and the capture is good: the folder is converted again once Gemini answers.
            written = folder / GEMINI_ERROR
            reason = written.read_text(encoding='utf-8') if written.exists() else 'a Gemini request failed'
            print(f'FAIL {GEMINI_CHECK:18} {reason}', flush=True)
            return {'asin': asin, 'source': str(folder), 'folder': str(folder), 'recapture': recapture,
                    'blocking_failures': [GEMINI_CHECK], 'warnings': [], 'error': reason}
        captured = folder / 'capture.json'
        seconds = {**(json.loads(captured.read_text()).get('seconds') or {} if captured.exists() else {}),
                   'pages': len(page_images(folder)), 'ocr': round(ocr - started, 1), 'checks': round(time.time() - ocr, 1)}
        data = report(folder, checks + [speed(seconds)], {'asin': asin, 'source': str(source), 'recapture': recapture,
                                                          'rounds': rounds, 'seconds': seconds})
    return {**data, 'folder': str(folder)}


def main():
    parser = argparse.ArgumentParser(description='Capture one Kindle book in the browser and produce checked Markdown')
    parser.add_argument('asin', help='The book\'s ASIN (?asin= in the Cloud Reader URL)')
    parser.add_argument('--title', help='Book title for the output file name (from the inventory)')
    parser.add_argument('--model', default='vision', help='img2txt --model (default: vision, no Gemini quota)')
    parser.add_argument('--domain', default='read.amazon.com')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    parser.add_argument('--zip', type=Path, help='Skip the capture and start from this ZIP or folder')
    parser.add_argument('--recapture', action='store_true', help='Mark the capture as a second one (recorded in report.json)')
    args = parser.parse_args()
    data = convert(args.asin, args.title, args.model, args.domain, args.downloads, args.zip, args.recapture)
    sys.exit(bool(data['blocking_failures']))


if __name__ == '__main__':
    main()
