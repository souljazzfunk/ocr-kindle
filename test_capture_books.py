import json
import sys

import pytest

import capture_books


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
    monkeypatch.setattr(sys, 'argv', ['capture_books.py', str(books)])
    with pytest.raises(SystemExit) as exit_info:
        capture_books.main()
    progress = json.loads((tmp_path / 'books.progress.json').read_text())
    assert progress['B2']['verdict'] == 'failed' and progress['B2']['error'] == 'no ZIP'
    assert progress['B3']['verdict'] == 'done' and progress['B3']['warnings'] == ['title']
    assert exit_info.value.code == 1 and '2 done, 1 failed' in capsys.readouterr().out


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
