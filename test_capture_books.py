import json
import os
import sys

import pytest

import capture_books

update_checkout = capture_books.update_checkout  # the real one; tests below stub it on the module


@pytest.fixture(autouse=True)
def brave_saves_without_asking(monkeypatch):
    monkeypatch.setattr(capture_books.brave, 'prompt_problem', lambda: None)
    monkeypatch.setattr(capture_books, 'update_checkout', lambda **_: 'same')


def result(failures, warnings=()):
    return {'blocking_failures': list(failures), 'warnings': list(warnings)}


def test_decide_goes_on_when_clean_and_redoes_an_unusable_capture_once():
    assert capture_books.decide(result([]), 1) == 'done'
    assert capture_books.decide(result([], ['title', 'text_layer']), 1) == 'done'  # warnings do not stop the list
    assert capture_books.decide(result(['pages']), 1) == 'recapture'
    assert capture_books.decide(result(['pages']), 2) == 'failed'
    assert capture_books.decide(result(['headings']), 1) == 'failed'  # the fixes already ran; a person must look


def test_run_book_recaptures_then_stops():
    calls = []

    def convert(asin, title, recapture, **options):
        calls.append(recapture)
        return result(['pages'] if not recapture else [])

    verdict, _ = capture_books.run_book('B1', 'One', {}, convert=convert)
    assert verdict == 'done' and calls == [False, True]

    calls.clear()
    verdict, _ = capture_books.run_book('B1', 'One', {}, convert=lambda *a, **k: (calls.append(1), result(['text']))[1])
    assert verdict == 'failed' and len(calls) == 2


def test_read_list_and_resume(tmp_path):
    books = tmp_path / 'books.tsv'
    books.write_text('# asin\ttitle\nB1\tOne\nB2\tTwo\n\nB3\tThree\n')
    assert capture_books.read_list(books) == [('B1', 'One'), ('B2', 'Two'), ('B3', 'Three')]
    progress = tmp_path / 'books.progress.json'
    progress.write_text(json.dumps({'B1': {'title': 'One', 'verdict': 'done', 'failures': [], 'warnings': []}}))
    assert capture_books.load_progress(progress)['B1']['verdict'] == 'done'


def test_main_runs_the_rest_of_the_list_and_keeps_going_after_a_failure(tmp_path, monkeypatch, capsys):
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tOne\nB2\tTwo\nB3\tThree\n')
    (tmp_path / 'books.progress.json').write_text(json.dumps({'B1': {'title': 'One', 'verdict': 'done', 'failures': [], 'warnings': []}}))

    def convert(asin, title=None, recapture=False, **options):
        if asin == 'B2':
            raise RuntimeError('no ZIP')
        return {'blocking_failures': [], 'warnings': ['title']}

    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books), '--keep-going'])
    with pytest.raises(SystemExit) as exit_info:
        capture_books.main()
    progress = json.loads((tmp_path / 'books.progress.json').read_text())
    assert progress['B2']['verdict'] == 'failed' and progress['B2']['error'] == 'no ZIP'
    assert progress['B3']['verdict'] == 'done' and progress['B3']['warnings'] == ['title']
    assert exit_info.value.code == 1 and '2 done, 1 failed' in capsys.readouterr().out


def test_a_book_whose_markdown_fails_stops_the_list_until_the_converter_is_fixed(tmp_path, monkeypatch, capsys):
    # 2026-10-10: Automate and Building a Second Brain failed their heading checks and the list went on to a third.
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tAutomate\nB2\tTwo\n')
    started = []

    def convert(asin, title=None, recapture=False, **options):
        started.append(asin)
        return {'blocking_failures': ['heading_noise'], 'warnings': []}

    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    with pytest.raises(SystemExit) as exit_info:
        capture_books.main()
    assert exit_info.value.code == 2 and started == ['B1']
    assert 'Stopped the list: Automate failed heading_noise' in capsys.readouterr().out


def test_a_capture_that_missed_the_start_or_end_is_redone_and_a_tab_left_open_is_not():
    assert capture_books.decide(result(['start_position']), 1) == 'recapture'
    assert capture_books.decide(result(['end_position', 'tab_closed']), 1) == 'recapture'
    assert capture_books.decide(result(['tab_closed']), 1) == 'failed'


def test_a_book_an_earlier_run_captured_badly_is_captured_anew_from_the_start(tmp_path, monkeypatch):
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tOne\nB2\tTwo\n')
    (tmp_path / 'books.progress.json').write_text(json.dumps({
        'B1': {'title': 'One', 'verdict': 'pending', 'failures': ['start_position'], 'warnings': []}}))
    calls = []

    def convert(asin, title=None, recapture=False, **options):
        calls.append((asin, recapture))
        return result([])

    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    with pytest.raises(SystemExit):
        capture_books.main()
    assert calls == [('B1', True), ('B2', False)]


def test_the_list_stops_at_a_browser_or_extension_failure(tmp_path, monkeypatch, capsys):
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tAI for Educators\nB2\tTwo\n')
    started = []

    def convert(asin, title=None, recapture=False, **options):
        started.append((asin, recapture))
        # The 2026-10-10 dialog: the ZIP came, but not through the extension's download.
        return {'blocking_failures': ['download'], 'warnings': []}

    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    with pytest.raises(SystemExit) as exit_info:
        capture_books.main()
    assert exit_info.value.code == 2 and started == [('B1', False)]
    assert 'Stopped the list: download failed on AI for Educators' in capsys.readouterr().out
    assert json.loads((tmp_path / 'books.progress.json').read_text())['B1']['verdict'] == 'stopped'


def test_a_partial_capture_is_recaptured_before_the_list_stops(tmp_path, monkeypatch):
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tAI for Educators\nB2\tTwo\n')
    started = []

    def convert(asin, title=None, recapture=False, **options):
        started.append((asin, recapture))
        return {'blocking_failures': ['start_position'], 'warnings': []}  # opened at page 75 of 120, twice

    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    with pytest.raises(SystemExit) as exit_info:
        capture_books.main()
    assert exit_info.value.code == 2 and started == [('B1', False), ('B1', True)]


def test_a_capture_that_made_no_zip_stops_the_list(tmp_path, monkeypatch, capsys):
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tAutomate\nB2\tTwo\n')
    started = []

    def convert(asin, title=None, recapture=False, **options):
        started.append(asin)
        return {'blocking_failures': ['capture_refused'], 'warnings': [], 'error': 'could not page back to page 1'}

    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    with pytest.raises(SystemExit) as exit_info:
        capture_books.main()
    assert exit_info.value.code == 2 and started == ['B1']
    progress = json.loads((tmp_path / 'books.progress.json').read_text())
    assert progress['B1']['error'] == 'could not page back to page 1'


def test_a_book_the_list_stopped_on_is_tried_again_as_a_recapture(tmp_path, monkeypatch):
    # 2026-10-10: extension 1.5 stopped paging back on Automate (a cap too tight), and the run recorded it as failed.
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tAutomate\nB2\tTwo\nB3\tThree\n')
    (tmp_path / 'books.progress.json').write_text(json.dumps({
        'B1': {'title': 'Automate', 'verdict': 'failed', 'failures': ['capture_refused'], 'warnings': []},
        'B2': {'title': 'Two', 'verdict': 'failed', 'failures': ['headings'], 'warnings': []}}))
    started = []

    def convert(asin, title=None, recapture=False, **options):
        started.append((asin, recapture))
        return {'blocking_failures': [], 'warnings': []}

    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    with pytest.raises(SystemExit):
        capture_books.main()
    # B1 again with #recapture (the extension may have saved it), B2's own failure stays, B3 is new.
    assert started == [('B1', True), ('B3', False)]


def fake_git(heads, changed='', branch='main', pull_code=0, fetched=''):
    heads = iter(heads)

    def git(*args):
        out = {'--abbrev-ref': branch}.get(args[1], '') if args[0] == 'rev-parse' else ''
        if args[:2] == ('rev-parse', 'HEAD'):
            out = next(heads)
        if args[:2] == ('rev-parse', 'FETCH_HEAD'):
            out = fetched
        if args[0] == 'diff':
            out = changed
        return type('R', (), {'stdout': out, 'stderr': 'diverged', 'returncode': pull_code if args[0] == 'pull' else 0})()
    return git


def test_main_is_pulled_before_each_book_and_says_what_a_change_needs():
    assert update_checkout(fake_git(['a', 'a'])) == 'same'
    assert update_checkout(fake_git(['a', 'b'], 'img2txt.py\ntest_img2txt.py')) == 'python'
    assert update_checkout(fake_git(['a', 'b'], 'extension/pagemeta.js\nextension/manifest.json')) == 'extension'
    assert update_checkout(fake_git(['a', 'b'], 'extension/pagemeta.test.js')) == 'python'
    assert update_checkout(fake_git(['a'], branch='vision-headings-fix')) == 'same'
    assert update_checkout(fake_git(['a'], pull_code=1)).startswith('could not pull main')


def test_an_extension_change_on_main_stops_the_list_before_the_next_book(tmp_path, monkeypatch, capsys):
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tOne\n')
    monkeypatch.setattr(capture_books, 'update_checkout', lambda **_: 'extension')
    monkeypatch.setattr(capture_books.capture_book, 'convert', lambda *a, **k: pytest.fail('captured with a stale extension'))
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    with pytest.raises(SystemExit) as exit_info:
        capture_books.main()
    assert exit_info.value.code == 2 and 'reload it once' in capsys.readouterr().out


def test_a_gemini_failure_stops_the_list_and_the_book_resumes_from_its_folder(tmp_path, monkeypatch, capsys):
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tFacing the Mountain\nB2\tTwo\n')
    started = []

    def convert(asin, title=None, recapture=False, **options):
        started.append((asin, recapture, options.get('source')))
        if len(started) == 1:
            return {'blocking_failures': ['gemini'], 'warnings': [], 'source': '/books/Facing the Mountain'}
        return {'blocking_failures': [], 'warnings': []}

    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books), '--keep-going'])
    with pytest.raises(SystemExit) as exit_info:
        capture_books.main()
    assert exit_info.value.code == 2 and started == [('B1', False, None)]
    assert json.loads((tmp_path / 'books.progress.json').read_text())['B1']['verdict'] == 'waiting'
    assert 'Gemini failed on Facing the Mountain' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        capture_books.main()
    # No new capture: the folder from the first run is converted again.
    assert started[1] == ('B1', False, capture_books.Path('/books/Facing the Mountain')) and started[2][0] == 'B2'


def test_a_good_capture_saved_after_the_stop_is_converted_instead_of_captured_again(tmp_path):
    # 2026-10-10: Facing the Mountain was captured to the end on 1.7 after the list stopped on it, but its entry still
    # said stopped (capture_stop on 1.6), so the resumed list started paging through 673 screens again.
    import zipfile
    def save(name, made, at):
        path = tmp_path / name
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('capture.json', json.dumps(made))
        os.utime(path, (at, at))
        return path
    save('Facing the Mountain.zip', {'extension_version': '1.6', 'stopped': 'stuck at 540'}, 1000)
    good = save('Facing the Mountain (1).zip', {'extension_version': '1.7', 'stopped': ''}, 3000)
    save('Facing the Mountain (2).zip', {'extension_version': '1.7', 'stopped': 'turn cap'}, 4000)
    save('Drive.zip', {'extension_version': '1.7', 'stopped': ''}, 5000)
    assert capture_books.saved_capture(tmp_path, 'Facing the Mountain', 2000, version='1.7') == good
    assert capture_books.saved_capture(tmp_path, 'Facing the Mountain', 3500, version='1.7') is None
    assert capture_books.saved_capture(tmp_path, 'Facing the Mountain', 2000, version='1.8') is None


def test_a_recapture_after_a_saved_zip_failed_does_not_reuse_that_zip():
    calls = []

    def convert(asin, title, recapture, **options):
        calls.append((recapture, options.get('source')))
        return {'blocking_failures': ['pages'] if len(calls) == 1 else [], 'warnings': []}

    verdict, _ = capture_books.run_book('B0', 'Book', {'source': 'old.zip'}, convert=convert)
    assert verdict == 'done' and calls == [(False, 'old.zip'), (True, None)]


def test_a_capture_with_no_zip_is_tried_once_more_then_stops_the_list():
    assert capture_books.decide(result(['tab_gone']), 1) == 'recapture'
    assert capture_books.decide(result(['no_zip', 'tab_closed']), 1) == 'recapture'
    assert capture_books.decide(result(['tab_gone']), 2) == 'failed'
    assert 'tab_gone' in capture_books.STOP_CHECKS  # a second vanished tab stops the list: later books would follow


def test_a_book_that_failed_under_older_code_is_converted_again_from_its_zip_once(tmp_path, monkeypatch, capsys):
    # 2026-10-11: Dilla Time and Building a Second Brain passed once the fixes were merged, but their entries still
    # said failed, so the list never went back to them.
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tDilla Time\nB2\tFor 91 Days in Bolivia\nB3\tAutomate\n')
    zip_path = tmp_path / 'Dilla Time.zip'
    zip_path.write_bytes(b'')
    (tmp_path / 'books.progress.json').write_text(json.dumps({
        'B1': {'title': 'Dilla Time', 'verdict': 'failed', 'failures': ['layout'], 'warnings': [], 'source': str(zip_path)},
        'B2': {'title': 'For 91 Days in Bolivia', 'verdict': 'failed', 'failures': ['kindle_app_only'], 'warnings': []},
        'B3': {'title': 'Automate', 'verdict': 'failed', 'failures': ['crash'], 'warnings': []}}))
    started = []

    def convert(asin, title=None, recapture=False, **options):
        started.append((asin, options.get('source')))
        return result([])

    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books), '--downloads', str(tmp_path), '--keep-going'])
    with pytest.raises(SystemExit):
        capture_books.main()
    # Dilla from its ZIP with no capture; the app-only book is left alone; Automate has no ZIP, so nothing runs.
    assert started == [('B1', zip_path)]
    progress = json.loads((tmp_path / 'books.progress.json').read_text())
    assert progress['B1']['verdict'] == 'done' and progress['B3']['verdict'] == 'failed'
    assert 'converting Dilla Time.zip again: it failed layout under older code' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        capture_books.main()
    assert started == [('B1', zip_path)]  # the same code finds nothing new to convert


def test_which_failed_books_are_converted_again_and_an_unusable_old_zip_is_captured_anew():
    entry = {'verdict': 'failed', 'failures': ['chapter_titles'], 'warnings': []}
    assert capture_books.stale_failure(entry, 'abc')
    assert not capture_books.stale_failure({**entry, 'converter': 'abc'}, 'abc')
    assert not capture_books.stale_failure({**entry, 'failures': ['capture_stop']}, 'abc')  # rerun as stopped anyway
    calls = []

    def convert(asin, title=None, recapture=False, **options):
        calls.append(options.get('source'))
        return result(['pages'] if len(calls) == 1 else [])

    verdict, _ = capture_books.run_book('B1', 'One', {'source': 'old.zip'}, convert=convert, again=True)
    assert verdict == 'done' and calls == ['old.zip', None]


def test_the_converter_version_follows_the_code_and_not_the_tests(tmp_path):
    (tmp_path / 'img2txt.py').write_text('a = 1\n')
    (tmp_path / 'test_img2txt.py').write_text('t = 1\n')
    first = capture_books.converter_version(tmp_path)
    (tmp_path / 'test_img2txt.py').write_text('t = 2\n')
    assert capture_books.converter_version(tmp_path) == first
    (tmp_path / 'img2txt.py').write_text('a = 2\n')
    assert capture_books.converter_version(tmp_path) != first


def test_a_second_run_on_the_same_list_does_not_start(tmp_path, monkeypatch, capsys):
    # Two runs would each write back the whole progress file over the other's results (2026-10-11).
    import subprocess
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tDrive\n')
    holder = subprocess.Popen([sys.executable, '-c', 'import sys, time, locks; locks.keep(sys.argv[1]); print("held", flush=True); time.sleep(30)',
                               str(tmp_path / 'books.lock')], stdout=subprocess.PIPE, text=True,
                              cwd=os.path.dirname(capture_books.__file__), env={k: v for k, v in os.environ.items() if k != 'KINDLE_LOCKS_HELD'})
    try:
        assert holder.stdout.readline().strip() == 'held'
        monkeypatch.delenv('KINDLE_LOCKS_HELD', raising=False)
        monkeypatch.setattr(capture_books.capture_book, 'convert', lambda *a, **k: pytest.fail('ran beside another run'))
        monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
        with pytest.raises(SystemExit) as stop:
            capture_books.main()
        assert 'another run is working through books.tsv' in str(stop.value.code)
    finally:
        holder.kill()
        holder.wait()


def pipelined_convert(log, release, failures=lambda asin: []):
    """A convert that captures under brave, then converts under converting until release[asin] is set."""
    import threading
    lock = threading.Lock()

    def convert(asin, title=None, recapture=False, brave=None, converting=None, **options):
        with brave:
            with lock:
                log.append(('capture', asin))
        with converting:
            with lock:
                log.append(('convert', asin))
            release.setdefault(asin, threading.Event()).wait(5)
            with lock:
                log.append(('converted', asin))
        return result(failures(asin))
    return convert


def test_the_next_book_is_captured_while_this_one_converts(tmp_path, monkeypatch):
    # Laiken, 2026-10-11: use the time in parallel. Python Crash Course spent 45 Sonnet rounds converting with Brave idle.
    import threading
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tOne\nB2\tTwo\nB3\tThree\n')
    log, release = [], {asin: threading.Event() for asin in ('B1', 'B2', 'B3')}
    monkeypatch.setattr(capture_books.capture_book, 'convert', pipelined_convert(log, release))
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    done = []
    runner = threading.Thread(target=lambda: done.append(pytest.raises(SystemExit, capture_books.main)), daemon=True)
    runner.start()

    def wait_for(entry):
        for _ in range(500):
            if entry in log:
                return
            threading.Event().wait(0.01)
        pytest.fail(f'{entry} never happened: {log}')

    wait_for(('capture', 'B2'))  # B2 is in Brave while B1 still converts
    assert ('converted', 'B1') not in log and ('convert', 'B2') not in log  # one conversion at a time
    assert ('capture', 'B3') not in log  # and one book in Brave
    release['B1'].set()
    wait_for(('convert', 'B2'))
    wait_for(('capture', 'B3'))
    release['B2'].set()
    release['B3'].set()
    runner.join(5)
    progress = json.loads((tmp_path / 'books.progress.json').read_text())
    assert [progress[a]['verdict'] for a in ('B1', 'B2', 'B3')] == ['done'] * 3


def test_a_stop_found_while_the_next_book_was_captured_converts_that_book_then_stops(tmp_path, monkeypatch, capsys):
    import threading
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tOne\nB2\tTwo\nB3\tThree\n')
    log, release = [], {'B1': threading.Event(), 'B2': threading.Event()}
    convert = pipelined_convert(log, release, failures=lambda asin: ['extension_version'] if asin == 'B1' else [])
    monkeypatch.setattr(capture_books.capture_book, 'convert', convert)
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    threading.Timer(0.3, release['B1'].set).start()
    release['B2'].set()
    with pytest.raises(SystemExit) as exit_info:
        capture_books.main()
    assert exit_info.value.code == 2 and ('capture', 'B3') not in log
    progress = json.loads((tmp_path / 'books.progress.json').read_text())
    assert progress['B1']['verdict'] == 'stopped' and progress['B2']['verdict'] == 'done' and 'B3' not in progress
    assert 'Stopped the list: extension_version failed on One' in capsys.readouterr().out


def test_a_conversion_in_progress_ends_before_main_is_pulled_under_it():
    settled = []
    assert update_checkout(fake_git(['a', 'a'], fetched='a'), settle=lambda: settled.append(1)) == 'same'
    assert settled == []  # nothing new on main: the conversion goes on beside the next capture
    assert update_checkout(fake_git(['a', 'b'], 'img2txt.py', fetched='b'), settle=lambda: settled.append(1)) == 'python'
    assert settled == [1]
