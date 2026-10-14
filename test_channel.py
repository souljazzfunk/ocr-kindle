import base64
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

import channel
import img2txt
from check_book import check_folder


@pytest.fixture
def served(tmp_path):
    (tmp_path / 'extension').mkdir()
    (tmp_path / 'extension' / 'manifest.json').write_text(json.dumps({'version': '2.0'}))
    ch = channel.Channel(port=0, root=tmp_path, downloads=tmp_path / 'Downloads').start()
    (tmp_path / 'Downloads').mkdir()
    yield ch
    ch.stop()


def call(ch, method, path, body=b'', headers=None):
    request = urllib.request.Request(f'http://127.0.0.1:{ch.port}{path}', data=body if method == 'POST' else None,
                                     headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def take(ch, version='2.0', browser='chrome', worker='w1'):
    """Poll as the worker does until a job is offered; returns it."""
    for _ in range(100):
        _, reply = call(ch, 'GET', f'/job?v={version}&b={browser}&w={worker}')
        if reply['job']:
            return reply['job']
        time.sleep(0.02)
    raise AssertionError('no job offered')


def page(ch, job, n, png, position='', text=''):
    return call(ch, 'POST', f'/page?job={job["id"]}&n={n}', png, {
        'X-Position': position, 'X-Text': base64.b64encode(text.encode()).decode(),
        'X-Sha': hashlib.sha256(png).hexdigest()})[0]


def event(ch, job, **fields):
    return call(ch, 'POST', '/event', json.dumps({'job': job['id'], **fields}).encode())[0]


def in_background(work):
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault('result', work()), daemon=True)
    thread.start()
    return thread, out


LONG = 'The group became known as the Homebrew Computer Club, and it met in a garage.'


def test_a_capture_opened_mid_book_lands_as_page_files_in_reading_order(served, tmp_path):
    folder = tmp_path / 'Downloads' / 'Four Pages'
    thread, out = in_background(lambda: served.capture('B0FOUR', 'read.amazon.com', folder, False))
    job = take(served)
    assert job == {'id': job['id'], 'kind': 'capture', 'asin': 'B0FOUR', 'domain': 'read.amazon.com', 'recapture': False}
    _, reply = call(served, 'GET', '/job?v=2.0&b=chrome&w=w1')
    assert reply['version'] == '2.0'
    # Opened at page 3, paged back to 1 (screens 0, -1, -2), then forward past them to page 4 (screen 1).
    assert event(served, job, type='title', title='Four Pages', probe={'counts': {'textLayer': 1}}) == 200
    assert page(served, job, 0, b'png-c', '3/4', 'short') == 200
    assert page(served, job, -1, b'png-b', '2/4') == 200
    assert page(served, job, -2, b'png-a', '1/4', LONG) == 200
    assert event(served, job, type='trace', observation={'position': {'n': 1, 'm': 4}}, action='turn:prev') == 200
    assert page(served, job, 1, b'png-d', '4/4') == 200
    assert event(served, job, type='done', start='opened at 3/4; 2 earlier screens saved back to page 1', stopped='',
                 extension_version='2.0', positions={'0': '3/4', '-1': '2/4', '-2': '1/4', '1': '4/4'}) == 200
    thread.join(5)

    assert out['result'] == channel.CaptureResult('done', '', 'Four Pages', 4)
    assert [(folder / f'page_{i:04d}.png').read_bytes() for i in range(1, 5)] == [b'png-a', b'png-b', b'png-c', b'png-d']
    assert (folder / 'page_0001.txt').read_text() == LONG
    assert not (folder / 'page_0003.txt').exists()  # under 40 characters: left for OCR
    assert json.loads((folder / 'positions.json').read_text()) == {
        'page_0001': '1/4', 'page_0002': '2/4', 'page_0003': '3/4', 'page_0004': '4/4'}
    capture = json.loads((folder / 'capture.json').read_text())
    seconds = capture.pop('seconds')
    assert capture == {'extension_version': '2.0', 'stopped': '', 'start': 'opened at 3/4; 2 earlier screens saved back to page 1'}
    assert seconds['screens'] == 4 and 0 <= seconds['reach'] < 5 and 0 <= seconds['capture'] < 5
    assert (folder / img2txt.SOURCES).read_text() == f'page_0001\t{img2txt.TEXT_LAYER}\n'
    assert json.loads((folder / 'dom-probe.json').read_text()) == {'counts': {'textLayer': 1}}
    trace = json.loads((folder / 'trace.jsonl').read_text())
    assert 0 <= trace.pop('t') <= seconds['capture'] + 0.1
    assert trace == {'observation': {'position': {'n': 1, 'm': 4}}, 'action': 'turn:prev'}
    assert not (folder / channel.STAGING).exists()
    checks = {c.name: c.ok for c in check_folder(folder)}
    assert checks['pages'] and checks['start_position'] and checks['end_position'] and checks['capture_stop']
    _, reply = call(served, 'GET', '/job?v=2.0&b=chrome&w=w1')
    assert reply['job'] is None


def test_a_page_that_never_arrived_is_named_in_stopped(served, tmp_path):
    folder = tmp_path / 'Downloads' / 'Gap'
    thread, out = in_background(lambda: served.capture('B0GAP', 'read.amazon.com', folder))
    job = take(served)
    page(served, job, 0, b'one', '1/2')
    event(served, job, type='done', start='', stopped='', extension_version='2.0', positions={'0': '1/2', '1': '2/2'})
    thread.join(5)
    assert out['result'].reason == 'screens [1] never reached the channel'
    assert 'never reached' in json.loads((folder / 'capture.json').read_text())['stopped']


def test_bad_pages_and_other_jobs_are_turned_away(served, tmp_path):
    thread, out = in_background(lambda: served.capture('B0', 'read.amazon.com', tmp_path / 'Downloads' / 'B0'))
    job = take(served)
    sha_of_other = call(served, 'POST', f'/page?job={job["id"]}&n=0', b'png', {'X-Sha': 'deadbeef'})[0]
    stale = page(served, {'id': 'an-old-job'}, 0, b'png')
    with pytest.raises(RuntimeError, match='already runs job'):
        served.capture('B1', 'read.amazon.com', tmp_path / 'Downloads' / 'B1')  # one job at a time
    event(served, job, type='refused', reason='stop')
    thread.join(5)
    assert (sha_of_other, stale) == (400, 409)


def test_a_refused_capture_returns_the_extensions_reason(served, tmp_path):
    thread, out = in_background(lambda: served.capture('B00S1MW0KM', 'read.amazon.com', tmp_path / 'Downloads' / 'Tokyo'))
    job = take(served)
    event(served, job, type='refused', reason='kindle app required: Cloud Reader cannot open this book')
    thread.join(5)
    assert out['result'] == channel.CaptureResult('refused', 'kindle app required: Cloud Reader cannot open this book')


def test_a_capture_with_no_page_and_no_event_for_the_stall_time_is_stalled(served, tmp_path):
    folder = tmp_path / 'Downloads' / 'Stuck'
    thread, out = in_background(lambda: served.capture('B0', 'read.amazon.com', folder, stall_s=1))
    job = take(served)
    page(served, job, 0, b'png', '41/540')
    thread.join(5)
    assert out['result'].status == 'stalled' and out['result'].pages == 1
    assert (folder / channel.STAGING / '0.png').exists()  # kept for a look; the next capture moves the folder aside


def test_no_worker_is_unreachable_and_a_worker_on_another_version_is_named(served, tmp_path):
    result = served.capture('B0', 'read.amazon.com', tmp_path / 'Downloads' / 'B0', reach_s=0.3)
    assert result.status == 'unreachable' and result.reason.startswith('no worker asked for work')
    thread, out = in_background(lambda: served.capture('B0', 'read.amazon.com', tmp_path / 'Downloads' / 'B0', reach_s=1))
    time.sleep(0.2)
    call(served, 'GET', '/job?v=1.10&b=chrome')  # a worker that should have reloaded to 2.0 and did not
    thread.join(5)
    assert out['result'].status == 'unreachable' and 'a worker on extension 1.10 asked' in out['result'].reason


def test_an_earlier_capture_in_the_folder_is_moved_aside_not_mixed_in(served, tmp_path):
    folder = tmp_path / 'Downloads' / 'Book'
    folder.mkdir()
    (folder / 'page_0300.png').write_bytes(b'old')
    thread, out = in_background(lambda: served.capture('B0', 'read.amazon.com', folder))
    job = take(served)
    page(served, job, 0, b'new', '1/1')
    event(served, job, type='done', start='', stopped='', extension_version='2.0', positions={'0': '1/1'})
    thread.join(5)
    assert sorted(p.name for p in folder.glob('*.png')) == ['page_0001.png']
    [before] = [p for p in folder.parent.iterdir() if p.name.startswith('Book (before ')]
    assert (before / 'page_0300.png').read_bytes() == b'old'


def test_a_library_scan_writes_what_the_page_showed(served, tmp_path):
    thread, out = in_background(lambda: served.library('read.amazon.co.jp'))
    job = take(served)
    assert job['kind'] == 'library' and job['domain'] == 'read.amazon.co.jp'
    books = [{'asin': 'B07HNMJV57', 'title': 'How to Write a Lot'}]
    event(served, job, type='library', domain='read.amazon.co.jp', books=books, probe={'counts': {}})
    thread.join(5)
    assert out['result'] == {'domain': 'read.amazon.co.jp', 'books': books, 'probe': {'counts': {}}}
    written = json.loads((tmp_path / 'Downloads' / 'library-read.amazon.co.jp.json').read_text())
    assert written['books'] == books


def test_the_text_layer_names_match_img2txt():
    assert (channel.SOURCES, channel.TEXT_LAYER) == (img2txt.SOURCES, img2txt.TEXT_LAYER)


def test_only_the_chosen_browser_gets_jobs(served, tmp_path):
    # 2026-10-12: the Brave copy of the extension took the job after Laiken moved the capture to Chrome.
    thread, out = in_background(lambda: served.capture('B0', 'read.amazon.com', tmp_path / 'Downloads' / 'B0', reach_s=1))
    time.sleep(0.2)
    for query in ('v=2.0&b=brave', 'v=2.0'):
        _, reply = call(served, 'GET', f'/job?{query}')
        assert reply == {'version': '2.0', 'job': None}
    thread.join(5)
    assert out['result'].status == 'unreachable' and out['result'].reason.startswith('no worker asked for work')
    assert 'is chrome open' in out['result'].reason


def test_a_job_goes_to_one_worker_only(served, tmp_path):
    # 2026-10-12: Chrome and Brave both took 英語教育幻想 and their pages mixed in one folder.
    thread, out = in_background(lambda: served.capture('B0', 'read.amazon.com', tmp_path / 'Downloads' / 'B0'))
    job = take(served, worker='w1')
    _, other = call(served, 'GET', '/job?v=2.0&b=chrome&w=w2')
    _, again = call(served, 'GET', '/job?v=2.0&b=chrome&w=w1')
    assert other['job'] is None and again['job'] == job
    event(served, job, type='refused', reason='stop')
    thread.join(5)


def test_the_screen_where_a_capture_stopped_is_kept_beside_the_trace(served, tmp_path):
    # 2026-10-12: 英語教育幻想 stopped paging back at 784 of 2566 and nothing recorded what the reader showed there.
    folder = tmp_path / 'Downloads' / 'Stuck'
    thread, out = in_background(lambda: served.capture('B0', 'read.amazon.com', folder))
    job = take(served)
    page(served, job, 0, b'png', '784/2566')
    probe = {'turns': [{'label': 'Previous page', 'present': True, 'disabled': True, 'shown': True}]}
    assert event(served, job, type='probe', label='stopped', probe=probe) == 200
    event(served, job, type='done', start='', stopped='could not page back past page 784 of 2566 by button or key',
          extension_version='2.0', positions={'0': '784/2566'})
    thread.join(5)
    assert json.loads((folder / 'dom-probe-stopped.json').read_text()) == probe


def test_the_display_is_kept_awake_for_the_whole_capture_and_let_go_after(served, tmp_path, monkeypatch):
    held = []

    class Keeper:
        def __init__(self, args):
            held.append(['on', args])

        def terminate(self):
            held.append(['off'])

        def wait(self):
            pass

    monkeypatch.setattr(channel.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(channel.subprocess, 'Popen', Keeper)
    folder = tmp_path / 'Downloads' / 'Awake'
    thread, out = in_background(lambda: served.capture('B0', 'read.amazon.com', folder))
    job = take(served)
    page(served, job, 0, b'png', '1/1')
    assert held == [['on', ['caffeinate', '-d', '-i']]]  # on while the pages come
    event(served, job, type='done', start='', stopped='', extension_version='2.0', positions={'0': '1/1'})
    thread.join(5)
    assert out['result'].status == 'done' and held[-1] == ['off']
