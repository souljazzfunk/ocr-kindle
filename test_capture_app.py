import json

import pytest

import capture_app


def write_list(tmp_path, progress):
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tDrive\nB2\tFor 91 Days In Tokyo\nB3\tJapan Travel Planner\n')
    (tmp_path / 'books.progress.json').write_text(json.dumps(progress))
    return books


APP_ONLY = {'verdict': 'failed', 'failures': ['kindle_app_only'], 'warnings': []}


def test_it_will_not_start_without_laikens_word(tmp_path):
    books = write_list(tmp_path, {'B2': APP_ONLY})
    with pytest.raises(SystemExit) as stop:
        capture_app.main([str(books)], env={}, book=lambda *a: pytest.fail('drove the Kindle app'))
    assert 'KINDLE_APP_OK=1' in str(stop.value.code)


def test_only_the_app_only_books_are_captured_and_recorded(tmp_path, capsys):
    progress = {'B1': {'verdict': 'done', 'failures': [], 'warnings': []}, 'B2': APP_ONLY, 'B3': APP_ONLY}
    books = write_list(tmp_path, progress)
    seen = []

    def book(asin, title, downloads):
        seen.append(title)
        return {'asin': asin, 'blocking_failures': [], 'warnings': [], 'source': str(downloads / title)}

    capture_app.main([str(books), '--downloads', str(tmp_path)], env={'KINDLE_APP_OK': '1'}, book=book)
    assert seen == ['For 91 Days In Tokyo', 'Japan Travel Planner']
    saved = json.loads((tmp_path / 'books.progress.json').read_text())
    assert saved['B2']['verdict'] == 'done' and saved['B2']['capture'] == 'kindle_app'
    assert saved['B1'] == progress['B1']
    # A second run finds nothing left to do.
    capture_app.main([str(books), '--downloads', str(tmp_path)], env={'KINDLE_APP_OK': '1'}, book=book)
    assert seen == ['For 91 Days In Tokyo', 'Japan Travel Planner']


def test_the_first_book_that_fails_stops_the_run(tmp_path):
    books = write_list(tmp_path, {'B2': APP_ONLY, 'B3': APP_ONLY})
    seen = []

    def book(asin, title, downloads):
        seen.append(title)
        return {'asin': asin, 'blocking_failures': ['headings'], 'warnings': []}

    with pytest.raises(SystemExit) as stop:
        capture_app.main([str(books), '--downloads', str(tmp_path)], env={'KINDLE_APP_OK': '1'}, book=book)
    assert stop.value.code == 2 and seen == ['For 91 Days In Tokyo']
    saved = json.loads((tmp_path / 'books.progress.json').read_text())
    assert saved['B2']['failures'] == ['headings'] and saved['B3'] == APP_ONLY


def test_a_book_is_opened_paged_to_the_start_captured_then_converted(tmp_path):
    steps = []

    def take(folder):
        steps.append('capture')
        (folder / 'page_0001.png').write_bytes(b'')
        return [folder / 'page_0001.png']

    def convert(asin, title, source):
        steps.append(('convert', source.name))
        return {'asin': asin, 'blocking_failures': [], 'warnings': [], 'source': str(source)}

    data = capture_app.run_book('B2', 'For 91 Days In Tokyo', tmp_path, convert=convert,
                                open_=lambda a, t: steps.append('open') or '', start=lambda: steps.append('start') or '',
                                take=take)
    assert steps == ['open', 'start', 'capture', ('convert', 'For 91 Days In Tokyo')] and not data['blocking_failures']
    # Pages already in the folder would be numbered on from, so the book is not captured over them.
    again = capture_app.run_book('B2', 'For 91 Days In Tokyo', tmp_path, convert=convert,
                                 open_=lambda a, t: pytest.fail('opened'), take=take)
    assert again['blocking_failures'] == ['app_folder']


def test_a_book_the_app_does_not_open_or_cannot_page_back_is_named_with_what_the_app_showed(tmp_path):
    def nope(*a, **k):
        pytest.fail('captured')
    opened = capture_app.run_book('B2', 'Tokyo', tmp_path, open_=lambda a, t: 'front window: "Library"', take=nope)
    assert opened['blocking_failures'] == ['app_open'] and 'Library' in opened['error']
    calls = []

    def script(text):
        calls.append(text)
        return (1, '', 'not found') if 'click' in text else (0, 'File, Edit, Go: Next Page, Previous Page', '')

    reason = capture_app.go_to_start(script)
    assert 'Next Page, Previous Page' in reason and sum('click' in c for c in calls) == len(capture_app.START_ITEMS)


def test_the_book_window_is_found_by_its_title(tmp_path):
    shown = iter(['Library', 'For 91 Days in Tokyo – Kindle'])
    clock = iter(range(0, 100, 2))
    problem = capture_app.open_book('B2', 'For 91 Days In Tokyo', script=lambda s: (0, next(shown), ''),
                                    run=lambda c: None, clock=lambda: next(clock), sleep=lambda s: None)
    assert problem == ''
