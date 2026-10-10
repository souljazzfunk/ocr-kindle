import json
import sys

import pytest

import capture_books

update_checkout = capture_books.update_checkout  # the real one; tests below stub it on the module


@pytest.fixture(autouse=True)
def brave_saves_without_asking(monkeypatch):
    monkeypatch.setattr(capture_books.brave, 'prompt_problem', lambda: None)
    monkeypatch.setattr(capture_books, 'update_checkout', lambda: 'same')


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


def fake_git(heads, changed='', branch='main', pull_code=0):
    heads = iter(heads)

    def git(*args):
        out = {'--abbrev-ref': branch}.get(args[1], '') if args[0] == 'rev-parse' else ''
        if args[:2] == ('rev-parse', 'HEAD'):
            out = next(heads)
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
    monkeypatch.setattr(capture_books, 'update_checkout', lambda: 'extension')
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
