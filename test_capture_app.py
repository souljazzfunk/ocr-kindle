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
    # The same command resumes at the failed book (2026-10-11: a resume skipped Tokyo once its record said app_open).
    seen.clear()
    with pytest.raises(SystemExit):
        capture_app.main([str(books), '--downloads', str(tmp_path)], env={'KINDLE_APP_OK': '1'}, book=book)
    assert seen == ['For 91 Days In Tokyo']


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


LIBRARY = ('frontmost=true\nwindow|Kindle|489,1187|1024,843\n'
           'element|AXTextField|AXSearchField||Search Kindle||506,1229|975,27\n'
           'element|AXGroup||Library Grid View|||489,1273|1024,710\n'
           'element|AXButton|||For 91 Days In Tokyo, Powell, Michael, New, |Book Downloaded.  Reading is 1 percent completed|1332,1279|147,220\n'
           'element|AXButton|||For 91 Days in Bolivia, Powell, Michael, |Book Not Downloaded|1332,1536|147,220\n')
READER = 'frontmost=true\nwindow|Kindle|489,1187|1024,843\nelement|AXGroup|||page||489,1273|1024,710\n'


class App:
    """A fake System Events: the dump it answers, and what pressing a cover does."""

    def __init__(self, screens):
        self.screens, self.calls = list(screens), []

    def __call__(self, text):
        self.calls.append(text)
        if 'entire contents' in text and 'AXPress' not in text and 'keystroke' not in text:
            return 0, self.screens.pop(0) if len(self.screens) > 1 else self.screens[0], ''
        return 0, 'pressed' if 'AXPress' in text else 'searched', ''


def test_a_book_is_opened_by_pressing_its_cover_in_the_library_as_the_probe_showed():
    # Probe of 2026-10-11 17:46: the kindle:// URL leaves the library; each cover is a button named after the book.
    app = App([LIBRARY, LIBRARY, READER])
    clock = iter(range(0, 1000, 2))
    assert capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None,
                                 clock=lambda: next(clock), sleep=lambda s: None) == ''
    presses = [c for c in app.calls if 'AXPress' in c]
    assert len(presses) == 1 and '"For 91 Days In Tokyo"' in presses[0] and not any('keystroke' in c for c in app.calls)


def test_a_cover_off_screen_is_searched_for_first():
    app = App([LIBRARY, LIBRARY.replace('For 91 Days in Bolivia, Powell, Michael, ', 'Japan Travel Planner, Someone, '), READER])
    clock = iter(range(0, 1000, 2))
    assert capture_app.open_book('B3', 'Japan Travel Planner', script=app, run=lambda c: None,
                                 clock=lambda: next(clock), sleep=lambda s: None) == ''
    assert any('keystroke return' in c and '"Japan Travel Planner"' in c for c in app.calls)
    app = App([LIBRARY, LIBRARY])
    problem = capture_app.open_book('B9', 'Drive', script=app, run=lambda c: None, clock=lambda: next(clock), sleep=lambda s: None)
    assert problem.startswith('no cover for this title') and 'Search Kindle' in problem


def test_a_cover_not_downloaded_is_pressed_again_and_waited_for_as_laiken_said():
    # Laiken, 2026-10-11 17:21: もう 1 回クリックすればダウンロードが始まる.
    app = App([LIBRARY] * 40 + [READER])
    clock = iter(range(0, 2000, 2))
    assert capture_app.open_book('B4', 'For 91 Days in Bolivia', script=app, run=lambda c: None,
                                 clock=lambda: next(clock), sleep=lambda s: None) == ''
    assert sum('AXPress' in c for c in app.calls) == 2
    app = App([LIBRARY])
    problem = capture_app.open_book('B4', 'For 91 Days in Bolivia', script=app, run=lambda c: None,
                                    clock=lambda: next(clock), sleep=lambda s: None)
    assert 'pressing it again for its download' in problem and 'Book Not Downloaded' in problem
    # A downloaded book that stays in the library is not pressed twice: something else is wrong.
    app = App([LIBRARY])
    problem = capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None,
                                    clock=lambda: next(clock), sleep=lambda s: None)
    assert sum('AXPress' in c for c in app.calls) == 1 and 'Book Downloaded' in problem


def test_a_reader_already_open_is_reported_not_pressed():
    app = App([READER])
    problem = capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None, sleep=lambda s: None)
    assert problem.startswith('the Kindle app is not showing its library') and not any('AXPress' in c for c in app.calls)


def test_a_probe_records_what_the_new_app_shows_before_and_after_opening_a_book(tmp_path):
    # Laiken chose to fit the new Kindle app (2026-10-11); its steps are written from this dump, not guessed.
    calls, ebooks = [], tmp_path / 'eBooks'
    ebooks.mkdir()
    (ebooks / 'B2_EBOK').mkdir()
    app = App([LIBRARY, LIBRARY, LIBRARY, READER.replace('page', 'page 1')])
    folder = capture_app.probe('B2', 'For 91 Days In Tokyo', tmp_path / 'probe', script=app, run=calls.append,
                               sleep=lambda s: None, ebooks=ebooks)
    assert 'Library Grid View' in (folder / 'ui-library.txt').read_text()
    assert 'page 1' in (folder / 'ui-book.txt').read_text() and (folder / 'open.txt').read_text() == 'opened'
    assert json.loads((folder / 'probe.json').read_text())['downloaded'] == ['B2_EBOK']
    shots = ['screencapture', '-x', *(str(folder / f'screen-{n}.png') for n in (1, 2, 3))]  # every display
    assert shots in calls and any('AXPress' in c for c in app.calls)


def test_the_ui_dump_resolves_elements_before_reading_them():
    # 2026-10-11: read through the reference chain, every element's role failed to coerce; resolved first, they read.
    assert 'set elems to entire contents of w' in capture_app.UI_DUMP and 'set e to contents of e' in capture_app.UI_DUMP
    assert 'my txt(role of e)' in capture_app.UI_DUMP and 'on txt(x)' in capture_app.UI_DUMP


def test_a_probe_needs_laikens_word_too(tmp_path):
    with pytest.raises(SystemExit):
        capture_app.main([str(tmp_path / 'books.tsv'), '--probe', 'B2'], env={})
    seen = []
    import capture_app as module
    module_probe = module.probe
    try:
        (tmp_path / 'books.tsv').write_text('B2\tFor 91 Days In Tokyo\n')
        module.probe = lambda asin, title, folder, **k: seen.append((asin, title, folder)) or folder
        capture_app.main([str(tmp_path / 'books.tsv'), '--probe', 'B2', '--downloads', str(tmp_path)], env={'KINDLE_APP_OK': '1'})
    finally:
        module.probe = module_probe
    assert seen == [('B2', 'For 91 Days In Tokyo', tmp_path / 'kindle-probe' / 'B2')]
