"""A failure that parks a book or pauses a job leaves a case: the evidence the cloud needs to replay it without the Mac.

    <root>/cases/<YYYYmmddTHHMMSS>-<ASIN>-<stage>/
        case.json     asin, title, domain, stage, kind (book | systemic), checks, error, attempt, versions, commit,
                      files (what was copied) and skipped (page images left out to stay under MAX_BYTES)
        trace.jsonl, dom-probe.json, capture.json, positions.json, report.json   when the book's pages folder has them
        page_NNNN.png at most 3: the page a failed check names, then the first and the last; a named page's .txt too

publish_case copies a case to KINDLE_CASES_FOLDER (default Kindle_cases beside GOOGLE_DRIVE_FOLDER), which Drive syncs.
A case dropped into fixtures/cases/ is replayed by test_cases.py. Standard library only.
"""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = ('trace.jsonl', 'dom-probe.json', 'capture.json', 'positions.json', 'report.json')
MAX_PAGES = 3
MAX_BYTES = 5_000_000
PAGE = re.compile(r'page_\d{4}')
NUMBERED = re.compile(r'\d+/\d+')


def git_head(checkout):
    try:
        done = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return None


def latest(root, asin, stage):
    """case.json of the newest case for this book and stage, or None."""
    folder = Path(root) / 'cases'
    names = sorted(p.name for p in folder.glob(f'*-{asin}-{stage}') if p.is_dir() and not p.name.startswith('.'))
    return read_json(folder / names[-1] / 'case.json') if names else None


def edge_pages(pages, checks):
    """The first numbered screen for start_position and the last for end_position, by positions.json."""
    positions = read_json(pages / 'positions.json') or {}
    numbered = [name for name, text in sorted(positions.items()) if NUMBERED.search(text or '')]
    return ([numbered[0]] if 'start_position' in checks and numbered else []) + (
        [numbered[-1]] if 'end_position' in checks and numbered else [])


def named_pages(pages, checks):
    """Every page_NNNN that a failed check of report.json names in its detail."""
    return [name for check in (read_json(pages / 'report.json') or {}).get('checks') or []
            if check.get('name') in checks and not check.get('ok') for name in PAGE.findall(check.get('detail') or '')]


def chosen_pages(pages, focus):
    images = sorted(p.stem for p in pages.glob('page_*.png'))
    return [name for name in dict.fromkeys(focus + images[:1] + images[-1:]) if (pages / f'{name}.png').is_file()][:MAX_PAGES]


def write_case(book, stage, failure, root, *, versions, now=None, checkout=HERE):
    """Write the case of a failure ({checks, error, attempt, kind}) of book at stage under <root>/cases/. Returns its
    folder, or None when the newest case of this book and stage has the same checks and versions."""
    checks = list(failure.get('checks') or [])
    previous = latest(root, book.asin, stage)
    if previous and sorted(previous.get('checks') or []) == sorted(checks) and previous.get('versions') == versions:
        return None
    moment = time.time() if now is None else now
    case_id = f'{datetime.fromtimestamp(moment).strftime("%Y%m%dT%H%M%S")}-{book.asin}-{stage}'
    final = Path(root) / 'cases' / case_id
    tmp = final.with_name(f'.{case_id}.tmp')  # renamed when complete, so no reader sees half a case
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    pages = book.pages
    files, skipped, size = [], [], 0
    for name in EVIDENCE:
        if (pages / name).is_file():
            shutil.copy2(pages / name, tmp / name)
            size += (tmp / name).stat().st_size
            files.append(name)
    named = named_pages(pages, checks) if pages.is_dir() else []
    for page in chosen_pages(pages, named + edge_pages(pages, checks)) if pages.is_dir() else []:
        image = pages / f'{page}.png'
        if size + image.stat().st_size > MAX_BYTES:
            skipped.append(image.name)
            continue
        shutil.copy2(image, tmp / image.name)
        size += image.stat().st_size
        files.append(image.name)
        text = image.with_suffix('.txt')
        if page in named and text.is_file():
            shutil.copy2(text, tmp / text.name)
            size += text.stat().st_size
            files.append(text.name)

    case = {'id': case_id, 'asin': book.asin, 'title': book.title, 'domain': book.domain, 'stage': stage,
            'kind': failure.get('kind'), 'checks': checks, 'error': failure.get('error'),
            'attempt': failure.get('attempt'), 'versions': versions, 'commit': git_head(checkout),
            'files': files, 'skipped': skipped}
    (tmp / 'case.json').write_text(json.dumps(case, ensure_ascii=False, indent=1), encoding='utf-8')
    shutil.rmtree(final, ignore_errors=True)  # a second case of this book and stage in the same second
    os.replace(tmp, final)
    return final


def cases_folder(config):
    if config.get('KINDLE_CASES_FOLDER'):
        return Path(config['KINDLE_CASES_FOLDER'])
    drive = config.get('GOOGLE_DRIVE_FOLDER')
    return Path(drive).parent / 'Kindle_cases' if drive else None


def publish_case(case_dir, config):
    """Copy a case to the Drive folder for cases; returns where, or None. Never raises: the local case stays."""
    destination = cases_folder(config)
    if destination is None:
        return None
    try:
        if not destination.parent.is_dir():
            print(f'Case {Path(case_dir).name} not copied: {destination.parent} does not exist', flush=True)
            return None
        destination.mkdir(exist_ok=True)
        copied = Path(shutil.copytree(case_dir, destination / Path(case_dir).name, dirs_exist_ok=True))
    except Exception as e:  # Drive not mounted, full, or offline: the case stays on the Mac
        print(f'Case {Path(case_dir).name} not copied to {destination}: {type(e).__name__}: {e}', flush=True)
        return None
    print(f'Case {copied.name} copied to {destination}', flush=True)
    return copied


@dataclass(frozen=True)
class Case:
    folder: Path
    info: dict  # case.json
    trace: list  # trace.jsonl, one dict per line: {observation, action}
    files: dict  # file name -> path, for every file of the case


def load(case_dir):
    folder = Path(case_dir)
    trace_path = folder / 'trace.jsonl'
    trace = ([json.loads(line) for line in trace_path.read_text(encoding='utf-8').splitlines() if line.strip()]
             if trace_path.is_file() else [])
    return Case(folder, read_json(folder / 'case.json'), trace,
                {p.name: p for p in sorted(folder.iterdir()) if p.is_file()})
