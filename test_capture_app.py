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
    app = App([READER + 'element|AXButton||||Next Page||500,2000|20,20\n'])
    reason = capture_app.go_to_start(app, sleep=lambda s: None)
    assert reason.startswith('the reader shows no toolbar') and 'page' in reason

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
        if 'set value of e to 0' in text:
            return 0, 'set', ''
        if 'entire contents' in text and 'AXPress' not in text and 'keystroke' not in text:
            return 0, self.screens.pop(0) if len(self.screens) > 1 else self.screens[0], ''
        if 'click at' in text:
            return 0, 'pressed', ''  # the fake answers every action alike
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
    # A downloaded book that stays in the library is pressed once more: in the run of 2026-10-11 18:12 the first
    # press went to a window that was not frontmost ("Claude" was). Kindle is activated before every press.
    app = App([LIBRARY])
    problem = capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None,
                                    clock=lambda: next(clock), sleep=lambda s: None)
    # The second try is a real click on the cover's centre (runs of 18:12 and 18:24: AXPress right after Close Book
    # left the library on screen); the cover at 1332,1279 of 147x220 is clicked at 1405,1389.
    assert sum('AXPress' in c for c in app.calls) == 1 and 'Book Downloaded' in problem and 'clicking its centre)' in problem
    assert any('click at {1405, 1389}' in c for c in app.calls)
    activations = [i for i, c in enumerate(app.calls) if 'frontmost is true' in c]
    presses = [i for i, c in enumerate(app.calls) if 'AXPress' in c or 'click at {1405' in c]
    assert len(activations) == 2 and all(a < p for a, p in zip(activations, presses)) and 'frontmost before' in problem


# The reader's toolbar as the dump of 2026-10-11 18:04 recorded it: the names are in the description column.
CHROME = (READER + 'element|AXButton|||Close Book||524,194|40,40\n'
          'element|AXButton|||Table of Contents||564,194|40,40\n'
          'element|AXSlider|||Reading Progress Bar|Location {at} , 1%|533,954|980,46\n'
          'element|AXStaticText|||Location {at} of 2594 , 1%||974,999|100,10\n')


def test_the_recent_book_whose_cover_has_no_frame_is_opened_from_the_tab_bar():
    # Probe of 2026-10-11 18:42: the recently read book's cover is "0,1152|0,0"; the tab bar holds a button for it.
    recent = (LIBRARY.replace('|1332,1279|147,220', '|0,1152|0,0')
              + 'element|AXRadioButton|AXTabButton||For 91 Days In Tokyo|0|730,1958|46,70\n'
              + 'element|AXRadioButton|AXTabButton||LIBRARY|1|866,1984|251,45\n')
    app = App([recent, recent, READER])
    clock = iter(range(0, 1000, 2))
    assert capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None,
                                 clock=lambda: next(clock), sleep=lambda s: None) == ''
    presses = [c for c in app.calls if 'AXPress' in c]
    assert len(presses) == 1 and 'AXRadioButton' in presses[0] and '"For 91 Days In Tokyo"' in presses[0]
    app = App([recent])
    problem = capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None,
                                    clock=lambda: next(clock), sleep=lambda s: None)
    assert any('click at {753, 1993}' in c for c in app.calls) and 'at 0,1152 0,0' in problem and "tab bar's button at 730,1958" in problem


def test_an_end_of_book_sheet_and_a_menu_over_it_are_dismissed_before_close_book():
    # Run of 2026-10-11 18:59: the book read to its end showed the end-of-book sheet with a share menu over it, and
    # clicks on the page brought no toolbar. Escape twice (and the sheet's Close) come before the clicks are repeated.
    app = App([READER, READER, READER, READER, CHROME.format(at=2594), LIBRARY, LIBRARY, READER])
    clock = iter(range(0, 1000, 2))
    assert capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None,
                                 clock=lambda: next(clock), sleep=lambda s: None) == ''
    escapes = [i for i, c in enumerate(app.calls) if 'key code 53' in c]
    close_book = next(i for i, c in enumerate(app.calls) if '"Close Book"' in c)
    assert len(escapes) == 2 and all(e < close_book for e in escapes)


def test_a_reader_left_open_is_closed_with_its_close_book_button_never_file_close():
    # Probe of 2026-10-11 17:56: the app reopens into the last book; File > Close (Cmd+W) quits the app.
    # With the toolbar hidden the tree holds only groups (probe of 18:04), and in the pilot one click did not show it.
    app = App([READER, READER, CHROME.format(at=12), LIBRARY, LIBRARY, READER])
    clock = iter(range(0, 1000, 2))
    assert capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None,
                                 clock=lambda: next(clock), sleep=lambda s: None) == ''
    presses = [c for c in app.calls if 'AXPress' in c]
    assert '"Close Book"' in presses[0] and '"For 91 Days In Tokyo"' in presses[1] and len(presses) == 2
    assert sum('click at' in c for c in app.calls) == 2
    app = App([READER])
    problem = capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None, sleep=lambda s: None)
    assert 'neither its library nor the reader toolbar' in problem and sum('click at' in c for c in app.calls) == 6  # 3 clicks, Escape x2, 3 clicks
    assert not any('menu bar item "File"' in c or 'keystroke "w"' in c for c in app.calls)
    app = App([READER, CHROME.format(at=12), READER])
    problem = capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None, sleep=lambda s: None)
    assert problem.startswith('the Kindle app is not showing its library (Close Book: pressed)')


def test_the_start_is_reached_by_clicking_the_progress_bar_track_and_read_from_the_footer():
    # Probe of 2026-10-11 17:56: the toolbar holds the Reading Progress Bar; the footer says "Location N of M , P%".
    # Run of 19:02: setting the slider's value moved nothing, so the track is clicked at its left end (533+2, 954+23).
    app = App([READER, CHROME.format(at=12), CHROME.format(at=1), READER])
    assert capture_app.go_to_start(app, sleep=lambda s: None) == ''
    assert sum('click at {535, 977}' in c for c in app.calls) == 1 and sum('click at' in c for c in app.calls) == 3
    # A book already at Location 1 is not moved (run of 2026-10-11 18:19 opened on the cover).
    app = App([CHROME.format(at=1), READER])
    assert capture_app.go_to_start(app, sleep=lambda s: None) == ''
    assert sum('click at' in c for c in app.calls) == 1
    # The footer's total came back as 0 in the probe of 18:04; the start is judged by N alone.
    app = App([CHROME.format(at=12), CHROME.format(at=1).replace('of 2594', 'of 0'), READER])
    assert capture_app.go_to_start(app, sleep=lambda s: None) == ''
    app = App([READER, READER, READER, READER])
    assert 'no toolbar after clicks' in capture_app.go_to_start(app, sleep=lambda s: None)
    # When the click leaves the book where it was, the knob is dragged; here the drag cannot run, and both are named.
    app = App([CHROME.format(at=12), CHROME.format(at=12), CHROME.format(at=12)])
    drags = []
    problem = capture_app.go_to_start(app, sleep=lambda s: None, drag=lambda *a: drags.append(a))
    assert 'clicked the track start' in problem and 'dragged the knob' in problem and 'Location 12 of 2594' in problem
    assert drags and drags[0][:4] == (533 + 980 * 1 // 100, 977, 534, 977)
    # A drag that cannot run (no Quartz, no event tap) is named rather than raised.
    def no_drag(*a):
        raise RuntimeError('no event tap')
    problem = capture_app.go_to_start(App([CHROME.format(at=12)] * 3), sleep=lambda s: None, drag=no_drag)
    assert 'drag failed: no event tap' in problem


def test_the_knob_is_dragged_from_its_percentage_to_the_track_start():
    slider = {'role': 'AXSlider', 'title': '', 'description': 'Reading Progress Bar', 'value': 'Location 185 , 13%',
              'position': '533,954', 'size': '980,46'}
    drags = []
    assert capture_app.drag_knob_to_start(slider, lambda t: (0, '', ''), lambda s: None,
                                          drag=lambda *a: drags.append(a)) == 'dragged the knob'
    assert drags[0][:4] == (533 + 980 * 13 // 100, 977, 534, 977)


def test_a_probe_records_what_the_new_app_shows_before_and_after_opening_a_book(tmp_path):
    # Laiken chose to fit the new Kindle app (2026-10-11); its steps are written from this dump, not guessed.
    calls, ebooks = [], tmp_path / 'eBooks'
    ebooks.mkdir()
    (ebooks / 'B2_EBOK').mkdir()
    sheet = READER + 'element|AXButton||Close|||988,1771|24,9\n'
    chrome = READER + 'element|AXSlider||||12|500,2000|1000,20\n'
    app = App([LIBRARY, LIBRARY, LIBRARY, sheet, sheet, READER, chrome])
    folder = capture_app.probe('B2', 'For 91 Days In Tokyo', tmp_path / 'probe', script=app, run=calls.append,
                               sleep=lambda s: None, ebooks=ebooks)
    assert 'Library Grid View' in (folder / 'ui-library.txt').read_text()
    assert 'Close' in (folder / 'ui-book.txt').read_text()
    # The sheet is closed and the page clicked once, and each state is kept (probe of 2026-10-11 17:51).
    assert 'Close' not in (folder / 'ui-reader.txt').read_text() and 'AXSlider' in (folder / 'ui-chrome.txt').read_text()
    assert (folder / 'open.txt').read_text().startswith('opened\nsheet: pressed\npage: pressed')
    assert any('"Close"' in c and 'AXPress' in c for c in app.calls) and any('click at' in c for c in app.calls)
    assert ['screencapture', '-x', *(str(folder / f'chrome-{n}.png') for n in (1, 2, 3))] in calls
    assert json.loads((folder / 'probe.json').read_text())['downloaded'] == ['B2_EBOK']
    # Runs of 2026-10-11 19:02 and 19:11: the slider ignored everything, so the probe also records what the
    # Table of Contents and More options buttons open.
    assert (folder / 'ui-toc.txt').exists() and (folder / 'ui-more.txt').exists()
    assert 'Table of Contents: pressed' in (folder / 'open.txt').read_text() and 'More options: pressed' in (folder / 'open.txt').read_text()
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
