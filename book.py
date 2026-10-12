"""A book is a folder, and its state is derived from the files in it. Nothing else keeps progress.

    <root>/<ASIN>/book.json             the book: asin, title, domain (from books.tsv or a library scan)
    <root>/<ASIN>/start.json            when its first capture began
    <root>/<ASIN>/stage.<stage>.json    a stage's output: the stage version that made it, when, and its input
    <root>/<ASIN>/failure.<stage>.json  a stage that failed on this book: the checks, the version, and whether to retry
    <root>/<ASIN>/pages/                the capture (page images, capture.json) and what the converter writes beside it
    <root>/pause.<job>.json             a job paused for every book (a systemic failure), with when it resumes
    <root>/cases/<id>/                  the evidence of a parked book or a paused job, for replay (cases.py)

Book.state() is Ready(stage), Parked(failure) or Done. A stage's version is the sha256 of its own source files.
A book that passed a stage stays passed when that code changes: passing it again would redo every published book
whenever the converter changes. A book parked on a check is tried again only when the functions that decide that
check change (TOUCH). The failure records their digest as its version, with touch set. A failure from before a check
named its own code has no touch flag and holds the stage version; it is tried once more when that stage's code
changes, and the new failure names the check. Each stage writes its record or its failure, never both: the other is
removed first, so a crash in between leaves the stage Ready, not wrong.
"""

import ast
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cases

HERE = Path(__file__).resolve().parent
ROOT = Path.home() / 'Downloads' / 'kindle'
RECAPTURES = 1  # a capture found unusable is captured once more, then the book is parked


@dataclass(frozen=True)
class Stage:
    name: str
    job: str  # the kc_run.py job (one launchd job each) that runs it
    sources: tuple  # files and folders under the checkout whose content is the stage's version


CONVERTER = ('img2txt.py', 'layout.py', 'check_book.py', 'proofread.py')
STAGES = (
    Stage('capture', 'capture', ('channel.py', 'extension')),
    Stage('convert', 'convert', CONVERTER),
    Stage('publish', 'convert', CONVERTER),
)
STAGE = {stage.name: stage for stage in STAGES}
JOBS = {job: tuple(s.name for s in STAGES if s.job == job) for job in dict.fromkeys(s.job for s in STAGES)}


@dataclass(frozen=True)
class Ready:
    stage: str
    recapture: bool = False


@dataclass(frozen=True)
class Parked:
    stage: str
    failure: dict


@dataclass(frozen=True)
class Done:
    pass


BOOK, SYSTEMIC = 'book', 'systemic'


def gemini_reset(reason, now):
    """When a layout failure is tried again. The reason names the earliest reset as `Resets at <unix>` when every
    provider is out. INVALID_ARGUMENT with no such time is the request (B005J3IEZQ: additional_properties), which
    the same code sends again, so the job waits for new convert code. A quota with no explicit time waits for
    midnight Pacific. Anything else is tried again in half an hour."""
    text = reason or ''
    mark = 'Resets at '
    if mark in text:
        digits = ''
        for ch in text[text.index(mark) + len(mark):]:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            return int(digits)
    if 'INVALID_ARGUMENT' in text:
        return None
    if any(mark in text for mark in ('429', 'RESOURCE_EXHAUSTED', 'PerDay', 'quota')):
        pacific = datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles'))
        return (pacific.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
    return now + 1800


@dataclass(frozen=True)
class Rule:
    kind: str  # BOOK parks this book and the job goes on; SYSTEMIC pauses the job for every book
    recapture: bool = False  # the capture is unusable: capture the book once more before parking it
    breaker: bool = True  # the same check on two books in a row pauses the job until the stage's code changes
    final: bool = False  # a property of the book, not of the code: a new stage version does not retry it
    resume: object = None  # SYSTEMIC: (reason, now) -> when to try again; None waits for new code only
    blame: str | None = None  # the stage whose code the breaker waits on, when not the stage that found it
    waits_on: str | None = None  # the version a retry waits on, when not the stage's: 'code' is every .py here


def after(seconds):
    return lambda reason, now: now + seconds


# Every failure a stage can report, by check name. A check not listed is the book's own failure.
RULES = {
    'pages': Rule(BOOK, recapture=True, blame='capture'),
    'text': Rule(BOOK, recapture=True, blame='capture'),
    'start_position': Rule(BOOK, recapture=True, blame='capture'),
    'end_position': Rule(BOOK, recapture=True, blame='capture'),
    'capture_stall': Rule(BOOK, recapture=True),
    # The capture stopped where the extension could not page on: the same code stops at the same place, so the book waits
    # for new capture code. 英語教育幻想 (2026-10-12) stopped at 2320 paging back.
    'capture_stop': Rule(BOOK, blame='capture'),
    'kindle_app_only': Rule(BOOK, breaker=False, final=True),
    'app_folder': Rule(BOOK, breaker=False, final=True),
    'app_open': Rule(BOOK, breaker=False, final=True),
    'app_capture': Rule(BOOK, breaker=False, final=True),
    'signed_out': Rule(SYSTEMIC, resume=after(1800)),
    'extension_unreachable': Rule(SYSTEMIC, resume=after(600)),
    'extension_version': Rule(SYSTEMIC, resume=after(600)),
    'extension_folder': Rule(SYSTEMIC, resume=after(600)),
    'gemini': Rule(SYSTEMIC, resume=gemini_reset),
    'crash': Rule(BOOK, waits_on='code'),  # an exception can come from any module, so any fix retries it
}
BOOK_FAULT = Rule(BOOK)

# The functions whose body decides a check. A parked book or a paused job is tried again only when one of them
# changes. A name the checkout does not define stands for the whole file (function_source), so a stub checkout
# still notices an edit of that file. None means the whole file even when it defines other functions.
TOUCH = {
    'headings': (('check_book.py', 'check_folder'),),
    'heading_noise': (('check_book.py', 'check_folder'), ('proofread.py', 'find_all')),
    'decorative_digits': (('check_book.py', 'check_folder'),),
    'chapter_titles': (('check_book.py', 'check_folder'),),
    'contents_chapters': (
        ('check_book.py', 'contents_unheaded'),
        ('layout.py', 'contents_for_check'),
        ('layout.py', 'contents_covered'),
        ('layout.py', 'contents_key'),
        ('layout.py', '_tiles'),
        ('layout.py', 'livetext_contents'),
        ('layout.py', 'contents_entries'),
        ('layout.py', 'split_contents'),
        ('layout.py', 'listed'),
        ('layout.py', 'comparable'),
        ('layout.py', 'page_lines'),
    ),
    'code_blocks': (('check_book.py', 'check_folder'), ('layout.py', 'code_pages')),
    'proofread': (('proofread.py', None),),
    'layout': (('layout.py', 'mark_layout'), ('check_book.py', 'check_folder')),
    'gemini': (
        ('layout.py', 'mark_layout'),
        ('layout.py', 'gemini_schema'),
        ('layout.py', 'converter_version'),
        ('layout.py', 'failure_kind'),
        ('layout.py', 'block_until'),
    ),
}


def classify(checks):
    """One rule for a failure that names several checks: systemic if any is, else the book's, retried as a recapture
    only when every check says the capture was unusable."""
    rules = [RULES.get(check, BOOK_FAULT) for check in checks] or [BOOK_FAULT]
    systemic = next((rule for rule in rules if rule.kind == SYSTEMIC), None)
    return systemic or Rule(BOOK, recapture=all(r.recapture for r in rules), breaker=any(r.breaker for r in rules),
                            final=all(r.final for r in rules), waits_on=next((r.waits_on for r in rules if r.waits_on), None),
                            blame=next((r.blame for r in rules if r.blame), None))


def source_digest(root, sources):
    digest = hashlib.sha256()
    for source in sources:
        path = Path(root) / source
        files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
        for file in files:
            if file.name.startswith('test_') or file.name.endswith('.test.js') or not file.exists():
                continue
            digest.update(file.relative_to(root).as_posix().encode() + b'\0' + file.read_bytes() + b'\0')
    return digest.hexdigest()[:12]


def function_source(root, file, name):
    """The source of name in file, or the whole file when name is None or this checkout does not define it."""
    path = Path(root) / file
    if not path.is_file():
        return b''
    raw = path.read_bytes()
    if name is None:
        return raw
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        return raw
    text = raw.decode('utf-8')
    chunks = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(text, node)
            if segment:
                chunks.append(segment)
    return '\n'.join(chunks).encode() if chunks else raw


def function_digest(root, owners):
    digest = hashlib.sha256()
    for file, name in owners:
        digest.update(file.encode() + b'\0' + (name or '').encode() + b'\0')
        digest.update(function_source(root, file, name) + b'\0')
    return digest.hexdigest()[:12]


def versions(root=HERE):
    """Each stage's version, 'code' (every module), and 'touch': each check's own functions."""
    return {**{stage.name: source_digest(root, stage.sources) for stage in STAGES},
            'code': source_digest(root, sorted(p.name for p in Path(root).glob('*.py'))),
            'touch': {check: function_digest(root, owners) for check, owners in TOUCH.items()}}


def touch_of(current, checks, stage):
    """The digest a retry of these checks waits on: the blamed stage, every module, or the checks' own functions."""
    rule = classify(list(checks))
    if rule.waits_on == 'code':
        return current.get('code')
    if rule.blame:
        return current.get(rule.blame)
    touches = current.get('touch') or {}
    found = []
    for check in checks:
        if check not in touches:
            return current.get(stage)
        found.append(touches[check])
    if len(found) == 1:
        return found[0]
    if not found:
        return current.get(stage)
    return hashlib.sha256('\0'.join(sorted(found)).encode()).hexdigest()[:12]


def same_block(record, current, stage):
    """Whether record was made by the code that still decides its checks.

    touch means version is the digest of those checks' functions. Without it, version is the stage's, from a
    failure recorded before a check named its own code."""
    stored = record.get('version')
    checks = record.get('checks') or []
    if record.get('touch'):
        return stored == touch_of(current, checks, stage)
    rule = classify(checks)
    waited = record.get('waits_on') or rule.waits_on or rule.blame or stage
    return stored is not None and stored == current.get(waited)


def now_id():
    """When an output was made, to the microsecond: a later stage names the output it was made from by this."""
    return datetime.now().isoformat(timespec='microseconds')


def write_json(path, data):
    """Write through a temporary file and rename, so a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    with os.fdopen(handle, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None


class Book:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.asin = self.folder.name

    @property
    def info(self):
        return read_json(self.folder / 'book.json') or {}

    @property
    def title(self):
        return self.info.get('title') or (self.record('capture') or {}).get('title') or None

    @property
    def domain(self):
        return self.info.get('domain') or 'read.amazon.com'

    @property
    def pages(self):
        """Where the capture is: a migrated book keeps the folder an earlier run captured into."""
        return Path((self.record('capture') or {}).get('pages') or self.folder / 'pages')

    def record(self, stage):
        return read_json(self.folder / f'stage.{stage}.json')

    def failure(self, stage):
        return read_json(self.folder / f'failure.{stage}.json')

    def succeed(self, stage, data, upstream=None):
        """Record a stage's output (data carries the version that made it); returns the record."""
        (self.folder / f'failure.{stage}.json').unlink(missing_ok=True)
        record = {**data, 'upstream': upstream, 'at': now_id()}
        write_json(self.folder / f'stage.{stage}.json', record)
        return record

    def fail(self, stage, data, upstream=None):
        (self.folder / f'stage.{stage}.json').unlink(missing_ok=True)
        record = {**data, 'upstream': upstream, 'at': now_id()}
        write_json(self.folder / f'failure.{stage}.json', record)
        return record

    def start(self, now):
        """When the book's first capture began, written once by the capture job: a book's time is counted from it."""
        path = self.folder / 'start.json'
        if not path.exists():
            write_json(path, {'at': now})
        return read_json(path)['at']

    def attempt(self):
        """How many captures of this book its records count (0 before the first)."""
        return max((r or {}).get('attempt', 0) for r in (self.record('capture'), self.failure('capture')))

    def state(self, current=None):
        current = current or versions()
        upstream = None
        for stage in STAGES:
            failure = self.failure(stage.name)
            rule = classify((failure or {}).get('checks', []))
            if failure and failure.get('upstream') == upstream:
                if rule.final or same_block(failure, current, stage.name):
                    return Ready('capture', recapture=True) if failure.get('retry') else Parked(stage.name, failure)
                if rule.blame == 'capture' and stage.name != 'capture':
                    return Ready('capture', recapture=True)  # new capture code: the pages are what failed
            record = self.record(stage.name)
            if not record or record.get('upstream') != upstream:
                return Ready(stage.name)
            upstream = record['at']
        return Done()


def read_tsv(path):
    """(asin, title, domain) per `ASIN<TAB>title[<TAB>domain]` line; `#` lines are skipped."""
    books = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.startswith('#'):
            asin, title, domain = (line.split('\t') + ['', ''])[:3]
            books.append((asin.strip(), title.strip(), domain.strip() or 'read.amazon.com'))
    return books


# read.amazon.co.jp lists the Japanese purchases but opens each one as its sample; read.amazon.com opens the whole
# book (英語教育幻想, 2026-10-12). Every listed book is read on .com, whichever library listed it.
READER = 'read.amazon.com'


def read_library(path):
    """(asin, title, domain) per book of a `library-<domain>.json` written by `channel.py library`; domain is the
    reader that opens the whole book, not the library's. Samples and manga are left out, as in the 2026-10-10 list."""
    return [(b['asin'], (b.get('title') or '').strip(), READER) for b in (read_json(path) or {}).get('books') or []
            if b.get('asin') and b.get('resourceType') != 'EBOOK_SAMPLE' and not b.get('manga')]


def read_lists(lists):
    """Every listed book once, in list order: the lists in the order given, each in its own order."""
    seen = {}
    for path in lists:
        path = Path(path)
        if path.exists():
            for asin, title, domain in (read_library(path) if path.suffix == '.json' else read_tsv(path)):
                seen.setdefault(asin, (asin, title, domain))
    return list(seen.values())


def migrate(book, entry, now_versions):
    """Carry a books.progress.json entry over once, as files of the book's folder: a capture an earlier run converted
    is this book's capture (its conversion is redone, since no record says which code made it), and a book Cloud
    Reader cannot open stays parked for the Kindle app."""
    if book.record('capture') or book.failure('capture') or not entry:
        return
    source = Path(entry['source']) if entry.get('source') else None
    folder = source and (source.with_suffix('') if source.suffix == '.zip' else source)
    if folder and folder.is_dir():
        book.succeed('capture', {'pages': str(folder), 'title': entry.get('title') or None, 'attempt': 1,
                                 'via': entry.get('capture') or 'migrated', 'version': None})
    elif 'kindle_app_only' in entry.get('failures', []):
        book.fail('capture', {'checks': ['kindle_app_only'], 'error': entry.get('error'), 'attempt': 1,
                              'retry': False, 'version': now_versions['capture']})


class Library:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def book(self, asin):
        return Book(self.root / asin)

    def add(self, lists, progress=None, write=True):
        """Make a folder for every listed book not yet here (and carry over its progress entry); with write False,
        leave a book with no folder out instead. Returns the books in list order, then any other book folder by ASIN."""
        progress = progress or {}
        current = versions()
        listed = []
        for asin, title, domain in read_lists(lists):
            book = self.book(asin)
            if not (book.folder / 'book.json').exists():
                if not write:
                    continue
                migrate(book, progress.get(asin), current)  # first: book.json marks the book as carried over
                write_json(book.folder / 'book.json', {'asin': asin, 'title': title, 'domain': domain})
            listed.append(book)
        names = {b.asin for b in listed}
        rest = sorted(p.parent.name for p in self.root.glob('*/book.json') if p.parent.name not in names)
        return listed + [self.book(asin) for asin in rest]

    def pause(self, job):
        return read_json(self.root / f'pause.{job}.json')

    def set_pause(self, job, data):
        write_json(self.root / f'pause.{job}.json', {**data, 'at': time.time()})

    def clear_pause(self, job):
        (self.root / f'pause.{job}.json').unlink(missing_ok=True)

    def active_pause(self, job, current, now=None):
        """The job's pause while it holds; a pause whose time has come, whose stage has new code, or whose checks the
        RULES now blame on another job's stage is removed."""
        pause = self.pause(job)
        if not pause:
            return None
        now = time.time() if now is None else now
        due = pause.get('until') is not None and now >= pause['until']
        new_code = pause.get('version') is not None and not same_block(pause, current, pause.get('stage') or job)
        elsewhere = any(STAGE[rule.blame].job != job for rule in (RULES.get(c, BOOK_FAULT) for c in pause.get('checks', []))
                        if rule.blame)
        if due or new_code or elsewhere:
            self.clear_pause(job)
            return None
        return pause

    def last_outcome(self, stage, books, skip):
        """The newest result of stage on another book: ('failure' | 'record', its data), or None."""
        outcomes = []
        for book in books:
            if book.asin == skip:
                continue
            for kind, data in (('failure', book.failure(stage)), ('record', book.record(stage))):
                if data and data.get('at'):
                    outcomes.append((data['at'], kind, data, book.asin))
        return max(outcomes, default=None)


def leave_case(library, book, stage, checks, error, attempt, kind, current, now, config):
    """The case of a parked book or a paused job, copied to Drive. Evidence, not state: a case that cannot be written
    is reported in the log and the failure stays recorded."""
    try:
        case = cases.write_case(book, stage, {'checks': list(checks), 'error': error, 'attempt': attempt, 'kind': kind},
                                library.root, versions=current, now=now)
    except Exception as e:
        print(f'No case written for {book.asin} {stage}: {type(e).__name__}: {e}', flush=True)
        return None
    if case:
        cases.publish_case(case, config or {})
    return case


def record_failure(library, books, book, stage, checks, error, upstream=None, attempt=1, current=None, now=None,
                   config=None):
    """Record what a stage found wrong, by the RULES table, and leave a case (cases.py) unless the book is captured
    again. config holds the Drive folders the case is copied to. Returns 'paused' or 'parked' or 'retry'."""
    current = current or versions()
    now = time.time() if now is None else now
    rule = classify(checks)
    job = STAGE[stage].job
    if rule.kind == SYSTEMIC:
        library.set_pause(job, {'stage': stage, 'checks': list(checks), 'reason': error, 'book': book.asin,
                                'waits_on': stage, 'version': touch_of(current, checks, stage), 'touch': True,
                                'until': rule.resume(error, now) if rule.resume else None})
        leave_case(library, book, stage, checks, error, attempt, SYSTEMIC, current, now, config)
        return 'paused'
    retry = rule.recapture and attempt <= RECAPTURES
    previous = library.last_outcome(stage, books, skip=book.asin) if rule.breaker else None
    version = touch_of(current, checks, stage)
    book.fail(stage, {'checks': list(checks), 'error': error, 'attempt': attempt, 'retry': retry,
                      'version': version, 'touch': True}, upstream)
    if previous and previous[1] == 'failure' and previous[2].get('version') == version:
        repeated = sorted(c for c in set(checks) & set(previous[2].get('checks', [])) if RULES.get(c, BOOK_FAULT).breaker)
        if repeated:
            first = RULES.get(repeated[0], BOOK_FAULT)
            blamed = first.blame or stage
            waits_on = first.waits_on or blamed
            library.set_pause(STAGE[blamed].job, {
                'stage': blamed, 'checks': repeated, 'book': book.asin, 'waits_on': waits_on,
                'version': touch_of(current, repeated, blamed), 'touch': True, 'until': None,
                'reason': f'{", ".join(repeated)} failed on {previous[3]} and {book.asin} in a row; waiting for new '
                          f'{"" if waits_on == "code" else blamed + " "}code'})
            leave_case(library, book, stage, checks, error, attempt, SYSTEMIC, current, now, config)
            return 'paused'
    if retry:
        return 'retry'
    leave_case(library, book, stage, checks, error, attempt, BOOK, current, now, config)
    return 'parked'
