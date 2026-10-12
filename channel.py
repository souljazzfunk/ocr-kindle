#!/usr/bin/env python3
"""The one channel between the browser extension and Python: a small HTTP server on 127.0.0.1.

The extension's worker asks GET /job for work, opens the reader itself, and posts every page as it is taken
(POST /page) plus what it saw and did (POST /event). Pages are written to the book folder at once, so nothing waits in
the tab's memory. The server holds at most one job: that is the only guard against two captures of one book.

    venv/bin/python channel.py library read.amazon.com     # record the library page and its books

capture_book.py drives captures through Channel.capture. GET /job also returns the extension version on disk, and a
worker running another version reloads itself (extension/job.js).
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get('KC_PORT', 8765))
REACH_S = 90  # an offered job no worker asked for in this long: Chrome is closed or the extension is not loaded
STALL_S = 300
# Heading-only pages keep part of their text in the image (e.g. layer "1" for a page reading "幻想1 アメリカ…"),
# so short layers are left for img2txt.py to OCR.
MIN_TEXT_CHARS = 40
# The one browser whose extension gets jobs. Laiken has the extension in Chrome and Brave; on 2026-10-12 the Brave copy
# took the jobs after the move to Chrome (2026-10-11). A worker says which browser it runs in (extension/job.js).
BROWSER = os.environ.get('KINDLE_BROWSER', 'chrome')
STAGING = '.staging'
TRACE = 'trace.jsonl'
# img2txt.py's names, kept here so this module needs nothing outside the standard library (test_channel.py checks).
SOURCES, TEXT_LAYER = 'ocr_sources.tsv', 'textlayer'


@dataclass
class Job:
    kind: str  # 'capture' or 'library'
    domain: str
    folder: Path
    asin: str = ''
    recapture: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    offered: float = 0.0  # when the channel put it up
    taken: float | None = None  # when a worker first received it
    worker: str = ''  # the one worker that has it; any other worker is offered nothing
    active: float = 0.0  # the last page or event
    pages: dict = field(default_factory=dict)  # screen number (signed, see extension/policy.js) -> {position, text}
    title: str = ''
    outcome: 'CaptureResult | None' = None
    library: dict | None = None

    def public(self):
        return {'id': self.id, 'kind': self.kind, 'asin': self.asin, 'domain': self.domain, 'recapture': self.recapture}


@dataclass
class CaptureResult:
    status: str  # 'done', 'refused', 'stalled' or 'unreachable'
    reason: str = ''
    title: str = ''
    pages: int = 0


def page_name(i):
    return f'page_{i:04d}'


def finish_folder(folder, pages, done, seconds=None):
    """Rename the staged screens into page_NNNN in reading order and write positions.json and capture.json as the
    ZIP of extension 1.x held them, so img2txt.py and check_book.py read the folder unchanged. Returns stopped."""
    staging = folder / STAGING
    expected = {int(n) for n in (done.get('positions') or {})}
    missing = sorted(expected - set(pages))
    stopped = done.get('stopped') or ''
    if missing:
        stopped = '; '.join(filter(None, [stopped, f'screens {missing} never reached the channel']))
    positions, sources = {}, []
    for i, n in enumerate(sorted(pages), 1):
        name = page_name(i)
        (staging / f'{n}.png').replace(folder / f'{name}.png')
        positions[name] = pages[n]['position']
        text = pages[n]['text']
        if len(text) >= MIN_TEXT_CHARS:
            (folder / f'{name}.txt').write_text(text, encoding='utf-8')
            sources.append(name)
    if sources:
        with open(folder / SOURCES, 'a', encoding='utf-8') as f:
            f.writelines(f'{name}\t{TEXT_LAYER}\n' for name in sources)
    if pages:
        (folder / 'positions.json').write_text(json.dumps(positions, indent=1), encoding='utf-8')
        (folder / 'capture.json').write_text(json.dumps({'extension_version': done.get('extension_version'),
                                                         'stopped': stopped, 'start': done.get('start', ''),
                                                         'seconds': seconds or {}}),
                                             encoding='utf-8')
    shutil.rmtree(staging, ignore_errors=True)
    return stopped


def set_aside(folder):
    """Move a folder that holds anything to `<name> (before <time>)`, never mixing it with new pages or deleting it."""
    folder = Path(folder)
    if folder.is_dir() and any(folder.iterdir()):
        folder.rename(folder.with_name(f'{folder.name} (before {time.strftime("%Y%m%dT%H%M%S")})'))


def make_room(folder):
    set_aside(folder)
    (folder / STAGING).mkdir(parents=True, exist_ok=True)


class Channel:
    def __init__(self, port=PORT, root=HERE, downloads=Path.home() / 'Downloads', clock=time.monotonic, browser=BROWSER):
        self.port, self.root, self.downloads, self.clock, self.browser = port, Path(root), Path(downloads), clock, browser
        self.job = None
        self.lock = threading.Condition()
        self.polled = None  # when a worker last asked for work, and the version it runs
        self.worker_version = ''
        self.server = None

    def disk_version(self):
        return json.loads((self.root / 'extension' / 'manifest.json').read_text(encoding='utf-8'))['version']

    def start(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', self.port), Handler)
        self.server.daemon_threads = True
        self.server.channel = self
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def capture(self, asin, domain, folder, recapture=False, *, stall_s=STALL_S, reach_s=REACH_S):
        """Offer one capture and block until the extension says done or refused, or it stalls or never comes."""
        job = self._run(Job('capture', domain, Path(folder), asin=asin, recapture=recapture), stall_s, reach_s)
        return job.outcome

    def library(self, domain, *, stall_s=STALL_S, reach_s=REACH_S):
        """Record the library page: its books (pagemeta.parseLibrary) and probe, also written to library-<domain>.json."""
        job = self._run(Job('library', domain, self.downloads / f'library-{domain}'), stall_s, reach_s)
        if job.outcome.status != 'done':
            raise RuntimeError(f'library scan {job.outcome.status}: {job.outcome.reason}')
        return job.library

    def _run(self, job, stall_s, reach_s):
        with self.lock:
            if self.job:
                raise RuntimeError(f'the channel already runs job {self.job.id} ({self.job.kind} {self.job.asin})')
            self.job = job
            try:
                if job.kind == 'capture':
                    make_room(job.folder)
                else:
                    job.folder.mkdir(parents=True, exist_ok=True)  # for its trace.jsonl
                offered = job.offered = self.clock()
                while job.outcome is None:
                    now = self.clock()
                    if job.taken is None and now - offered >= reach_s:
                        seen = (f'a worker on extension {self.worker_version} asked, but never for this job'
                                if self.polled and self.polled >= offered else 'no worker asked for work')
                        job.outcome = CaptureResult('unreachable', f'{seen} in {reach_s} s (disk version '
                                                    f'{self.disk_version()}); is {self.browser} open with the extension loaded?')
                    elif job.taken is not None and now - job.active >= stall_s:
                        job.outcome = CaptureResult('stalled', f'no page and no event for {stall_s:g} s after '
                                                    f'{len(job.pages)} screens', job.title, len(job.pages))
                    else:
                        self.lock.wait(0.5)
                return job
            finally:
                self.job = None
                self.lock.notify_all()

    # The handlers below run on the server's threads.

    def get_job(self, worker_version, worker_browser='', worker_id=''):
        """A worker in another browser than self.browser is told the version (so it stays current) but never a job, and
        a job goes to one worker only: on 2026-10-12 two workers paged the same book into one folder."""
        with self.lock:
            now = self.clock()
            if worker_browser != self.browser:
                return {'version': self.disk_version(), 'job': None}
            self.polled, self.worker_version = now, worker_version
            job = self.job
            # A worker on another version reloads first (extension/job.js); the job counts as taken once a worker on
            # the disk version has it.
            if job and job.taken is None and worker_version == self.disk_version():
                job.taken = job.active = now
                job.worker = worker_id
                self.lock.notify_all()
            mine = job and job.taken is not None and job.worker == worker_id
            return {'version': self.disk_version(), 'job': job.public() if mine or (job and job.taken is None) else None}

    def _current(self, job_id):
        if not self.job or self.job.id != job_id or self.job.outcome is not None:
            raise LookupError(f'no job {job_id}')
        return self.job

    def put_page(self, job_id, n, png, position, text, sha):
        if hashlib.sha256(png).hexdigest() != sha:
            raise ValueError('X-Sha does not match the body')
        with self.lock:
            job = self._current(job_id)
            (job.folder / STAGING / f'{n}.png').write_bytes(png)
            job.pages[n] = {'position': position, 'text': text}
            job.active = self.clock()
            self.lock.notify_all()

    def put_event(self, job_id, event):
        with self.lock:
            job = self._current(job_id)
            job.active = now = self.clock()
            kind = event.get('type')
            if kind == 'trace':
                # t: seconds since the worker took the job, so a slow stretch of a capture shows in its trace.
                line = {'t': round(now - job.taken, 2), **{k: v for k, v in event.items() if k not in ('type', 'job')}}
                with open(job.folder / TRACE, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(line, ensure_ascii=False) + '\n')
            elif kind == 'title':
                job.title = event.get('title') or ''
                if event.get('probe') is not None:
                    (job.folder / 'dom-probe.json').write_text(json.dumps(event['probe'], indent=1), encoding='utf-8')
            elif kind == 'probe':
                label = re.sub(r'[^a-z-]', '', str(event.get('label') or 'probe'))
                (job.folder / f'dom-probe-{label}.json').write_text(json.dumps(event.get('probe'), indent=1), encoding='utf-8')
            elif kind == 'done':
                stopped = finish_folder(job.folder, job.pages, event, {
                    'reach': round(job.taken - job.offered, 1), 'capture': round(now - job.taken, 1),
                    'screens': len(job.pages)})
                job.outcome = CaptureResult('done', stopped, job.title, len(job.pages))
            elif kind == 'refused':
                job.outcome = CaptureResult('refused', event.get('reason') or 'no reason given', job.title, len(job.pages))
            elif kind == 'library':
                job.library = {k: v for k, v in event.items() if k not in ('type', 'job')}
                path = self.downloads / f'library-{job.domain}.json'
                path.write_text(json.dumps(job.library, ensure_ascii=False, indent=1), encoding='utf-8')
                job.outcome = CaptureResult('done', '', '', len(job.library.get('books') or []))
            else:
                raise ValueError(f'unknown event type {kind!r}')
            self.lock.notify_all()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != '/job':
            return self.reply(404, {'ok': False})
        query = parse_qs(url.query)
        self.reply(200, self.server.channel.get_job(query.get('v', [''])[0], query.get('b', [''])[0], query.get('w', [''])[0]))

    def do_POST(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        channel = self.server.channel
        try:
            if url.path == '/page':
                text = base64.b64decode(self.headers.get('X-Text') or '').decode('utf-8')
                channel.put_page(query.get('job', ''), int(query['n']), body, self.headers.get('X-Position') or '', text,
                                 self.headers.get('X-Sha') or '')
            elif url.path == '/event':
                event = json.loads(body)
                channel.put_event(event.get('job', ''), event)
            else:
                return self.reply(404, {'ok': False})
        except LookupError as e:
            return self.reply(409, {'ok': False, 'reason': str(e)})
        except (ValueError, KeyError) as e:
            return self.reply(400, {'ok': False, 'reason': str(e)})
        self.reply(200, {'ok': True})


def safe_name(title):
    return re.sub(r'[\\/:*?"<>|~]', '_', title).strip() or 'kindle-book'


def main():
    parser = argparse.ArgumentParser(description='Run one job through the extension channel')
    parser.add_argument('kind', choices=['library'])
    parser.add_argument('domain', nargs='?', default='read.amazon.com')
    parser.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    args = parser.parse_args()
    channel = Channel(downloads=args.downloads).start()
    try:
        found = channel.library(args.domain)
    except RuntimeError as e:
        raise SystemExit(str(e))
    finally:
        channel.stop()
    print(f'{len(found.get("books") or [])} books; wrote {args.downloads / f"library-{args.domain}.json"}')


if __name__ == '__main__':
    main()
