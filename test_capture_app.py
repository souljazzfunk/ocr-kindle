import re
from pathlib import Path
import json

import pytest

import book
import capture_app


def write_list(tmp_path, app_only=('B2', 'B3')):
    """A list of three books in their folders; the capture job parked app_only as kindle_app_only."""
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tDrive\nB2\tFor 91 Days In Tokyo\nB3\tJapan Travel Planner\n')
    library = book.Library(tmp_path / 'kindle')
    library.add([books])
    for asin in app_only:
        library.book(asin).fail('capture', {'checks': ['kindle_app_only'], 'error': 'kindle app required', 'attempt': 1,
                                            'retry': False})
    return books, library


def run(books, capture, *extra):
    return capture_app.main([str(books), '--root', str(books.parent / 'kindle'), *extra], env={'KINDLE_APP_OK': '1'},
                            capture=capture, locked=lambda: False, wake=lambda: None)


def test_it_will_not_start_without_laikens_word(tmp_path):
    books, _ = write_list(tmp_path)
    with pytest.raises(SystemExit) as stop:
        capture_app.main([str(books)], env={}, capture=lambda *a: pytest.fail('drove the Kindle app'))
    assert 'KINDLE_APP_OK=1' in str(stop.value.code)


def fake_app(seen, refuse=None):
    """A fake app: notes the title and writes one page, or answers refuse(title)'s failure."""
    def capture(asin, title, folder):
        seen.append(title)
        failure = refuse and refuse(title)
        if failure:
            return None, failure
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'page_0001.png').write_bytes(b'')
        return folder, None
    return capture


def test_only_the_app_only_books_are_captured_and_handed_to_the_convert_job(tmp_path):
    books, library = write_list(tmp_path)
    seen = []
    run(books, fake_app(seen))
    assert seen == ['For 91 Days In Tokyo', 'Japan Travel Planner']
    tokyo = library.book('B2')
    assert tokyo.state() == book.Ready('convert') and tokyo.failure('capture') is None
    assert tokyo.record('capture')['via'] == 'kindle_app' and tokyo.pages == tokyo.folder / 'pages'
    assert library.book('B1').state() == book.Ready('capture')  # the extension captures it
    # A second run finds nothing left to do.
    run(books, fake_app(seen))
    assert seen == ['For 91 Days In Tokyo', 'Japan Travel Planner']


def test_only_the_books_of_the_given_list_are_captured(tmp_path):
    # 2026-10-15: app-ja.tsv named 5 books, and the run counted 11 because every other parked folder came along.
    books, library = write_list(tmp_path)
    other = tmp_path / 'other.tsv'
    other.write_text('B9\tFor 91 Days In Palermo\n')
    library.add([other])
    library.book('B9').fail('capture', {'checks': ['kindle_app_only'], 'error': 'kindle app required', 'attempt': 1,
                                        'retry': False})
    seen = []
    run(books, fake_app(seen))
    assert seen == ['For 91 Days In Tokyo', 'Japan Travel Planner']
    assert library.book('B9').failure('capture')['checks'] == ['kindle_app_only']


def test_a_book_the_app_does_not_open_is_parked_the_run_goes_on_and_the_same_command_resumes_it(tmp_path):
    books, library = write_list(tmp_path)
    seen = []
    refuse = lambda title: title == 'For 91 Days In Tokyo' and {'blocking_failures': ['app_open'],
                                                                 'error': 'front window: "Library"'}
    with pytest.raises(SystemExit) as stop:
        run(books, fake_app(seen, refuse))
    # 2026-10-15: a book not downloaded to the app stopped the run before the books after it (F168).
    assert stop.value.code == 2 and seen == ['For 91 Days In Tokyo', 'Japan Travel Planner']
    parked = library.book('B2').state()
    assert parked.stage == 'capture' and parked.failure['checks'] == ['app_open'] and 'Library' in parked.failure['error']
    assert library.book('B3').record('capture')['via'] == 'kindle_app'
    # 2026-10-11: a resume skipped Tokyo once its record said app_open instead of kindle_app_only.
    seen.clear()
    run(books, fake_app(seen))
    assert seen == ['For 91 Days In Tokyo']


def test_a_dark_display_is_woken_and_the_run_goes_on(tmp_path):
    # 2026-10-15 17:41 JST: the display had been off since 14:20, macOS marked the session locked, and the app showed
    # only "Kindle, Kindle, ..." so all five books were parked as app_open. There was no password lock (F172).
    books, library = write_list(tmp_path)
    dark, seen = [True], []
    capture_app.main([str(books), '--root', str(tmp_path / 'kindle')], env={'KINDLE_APP_OK': '1'},
                     capture=fake_app(seen), locked=lambda: dark[0], wake=lambda: dark.clear() or dark.append(False))
    assert seen == ['For 91 Days In Tokyo', 'Japan Travel Planner']


def test_a_locked_screen_stops_the_run_before_any_book_is_opened_or_parked(tmp_path):
    books, library = write_list(tmp_path)
    woken = []
    with pytest.raises(SystemExit) as stop:
        capture_app.main([str(books), '--root', str(tmp_path / 'kindle')], env={'KINDLE_APP_OK': '1'},
                         capture=lambda *a: pytest.fail('drove a locked screen'), locked=lambda: True,
                         wake=lambda: woken.append(1))
    assert woken == [1] and 'lock screen' in str(stop.value.code)
    assert library.book('B2').failure('capture')['checks'] == ['kindle_app_only']


def test_the_display_is_kept_awake_while_the_app_turns_the_pages_and_let_go_after(tmp_path, monkeypatch):
    # 2026-10-15: nothing touched the Mac from 14:20 JST, the display slept and the Mac locked itself, and the 17:41 run
    # found no Kindle window for any book (F172). The extension's capture already held caffeinate (F145); the app's did not.
    import channel
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
    books, _ = write_list(tmp_path)
    seen = []

    def capture(asin, title, folder):
        held.append(['page', title])
        return fake_app(seen)(asin, title, folder)

    run(books, capture)
    assert held == [['on', ['caffeinate', '-d', '-i']], ['page', 'For 91 Days In Tokyo'],
                    ['page', 'Japan Travel Planner'], ['off']]


def test_the_lock_is_read_from_the_console_session(tmp_path):
    plist = lambda locked: (
        '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>IOConsoleUsers</key><array><dict>'
        '<key>kCGSSessionOnConsoleKey</key><true/><key>CGSSessionScreenIsLocked</key><' + locked + '/></dict></array>'
        '</dict></plist>')
    answer = lambda out: lambda *a, **k: type('Done', (), {'returncode': 0, 'stdout': out.encode()})()
    assert capture_app.screen_locked(run=answer(plist('true')))
    assert not capture_app.screen_locked(run=answer(plist('false')))
    unlocked = plist('false').replace('<key>CGSSessionScreenIsLocked</key><false/>', '')
    assert not capture_app.screen_locked(run=answer(unlocked))


def test_a_failure_other_than_opening_the_book_still_stops_the_run(tmp_path):
    books, _ = write_list(tmp_path)
    seen = []
    with pytest.raises(SystemExit) as stop:
        run(books, fake_app(seen, lambda title: {'blocking_failures': ['app_capture'], 'error': 'no page was captured'}))
    assert stop.value.code == 2 and seen == ['For 91 Days In Tokyo']


def test_a_book_is_opened_paged_to_the_start_and_captured(tmp_path):
    steps = []

    def take(folder, reading):
        steps.append('capture')
        (folder / 'page_0001.png').write_bytes(b'')
        return [folder / 'page_0001.png'], 'the end-of-book sheet'

    started = lambda shown: steps.append('start') or shown.update(start='Location 1 of 2389') or ''
    folder, failure = capture_app.capture_pages('B2', 'For 91 Days In Tokyo', tmp_path / 'pages',
                                                open_=lambda a, t: steps.append('open') or '', start=started, take=take)
    assert steps == ['open', 'start', 'capture'] and folder == tmp_path / 'pages' and failure is None
    # What the app showed at the start and the end is the capture's evidence for check_book (F167).
    shown = json.loads((folder / 'capture.json').read_text())
    assert shown == {'via': 'kindle_app', 'start': 'Location 1 of 2389', 'end': 'the end-of-book sheet', 'stopped': ''}
    # Pages already in the folder would be numbered on from, so the book is not captured over them.
    folder, again = capture_app.capture_pages('B2', 'For 91 Days In Tokyo', tmp_path / 'pages',
                                              open_=lambda a, t: pytest.fail('opened'), take=take)
    assert folder is None and again['blocking_failures'] == ['app_folder']


def test_the_toolbar_is_brought_by_resting_the_pointer_on_the_top_edge_never_by_a_click():
    # Sri Lanka, 2026-10-11 20:34: four clicks on the page brought no toolbar; one pointer move to the window's top
    # edge brought it at once (20:36). Clicks turn the page (a test's clicks moved the book from 3% to 30%, 20:40),
    # so with a pointer the script never clicks the page to show the toolbar.
    moves = []
    top, page = (489 + 1024 // 2, 1187 + capture_app.TOP_HOVER_PX), (489 + 512, 1187 + 843 // 2)
    app = App([READER, CHROME.format(at=1)])
    found = capture_app.show_chrome(app, sleep=lambda s: None, clock=app.clock, move=lambda x, y: moves.append((x, y)))
    assert capture_app.chrome_shown(found)
    assert moves == [page, top] and not any('click at' in c for c in app.calls)
    # The toolbar hidden again by the pointer, without a click.
    app = App([CHROME.format(at=1), READER])
    found = capture_app.hide_chrome(app, sleep=lambda s: None, clock=app.clock, move=lambda x, y: moves.append((x, y)))
    assert not capture_app.chrome_shown(found) and moves[-1] == page and not any('click at' in c for c in app.calls)
    # The run of 20:40 waited 14 s on one move and gave up: now the move is made afresh each try, and no click follows.
    moves.clear()
    app = App([READER] * 6 + [CHROME.format(at=1)])
    found = capture_app.show_chrome(app, sleep=lambda s: None, clock=app.clock, move=lambda x, y: moves.append((x, y)))
    assert capture_app.chrome_shown(found) and moves == [page, top, page, top] and not any('click at' in c for c in app.calls)
    # A fake script has no pointer: the tests above that count clicks are unchanged, and no Quartz is needed on Linux.
    assert capture_app.window_frame(App([READER])) == (489, 1187, 1024, 843)
    assert capture_app.hover_window(App(['frontmost=true\n']), move=lambda x, y: pytest.fail('moved')) == 'no window'


SCROLLED_TOC = (Path(__file__).parent / 'fixtures' / 'kindle' / 'ax-toc-sri-lanka-scrolled.txt').read_text()
TOP_TOC = (SCROLLED_TOC.replace('After One Month in Sri Lanka', 'Cover').replace('Nuwara Eliya', 'Beginning')
           .replace("Horton Plains and World's End", 'Ayubowan, Sri Lanka!'))
COVER = SCROLLED_TOC.split('element|AXButton|||Close menu||798')[0]  # the reader on its cover: toolbar, no footer


def test_the_table_of_contents_is_scrolled_to_its_top_and_its_button_row_pressed_through_the_api(capsys):
    # Sri Lanka, 2026-10-11 20:44: the panel opened at the current chapter; the script pressed the untitled group
    # above the rows. Scroll-wheel up over the panel brought "Cover" to the top; AXPress on that button through
    # the Accessibility API put the book on its cover, whose toolbar shows no "Location N of M" (20:45).
    scrolls, presses = [], []
    app = App([COVER, SCROLLED_TOC, SCROLLED_TOC, TOP_TOC, TOP_TOC, TOP_TOC, COVER])
    how = capture_app.toc_first(app, sleep=lambda s: None, clock=app.clock,
                                scroll=lambda x, y, n: scrolls.append((x, y, n)),
                                press=lambda role, name: presses.append((role, name)) or True)
    assert how == 'pressed "Cover" at the top of the table of contents'
    assert presses == [('AXButton', 'Cover')] and not any('AXPress' in c and '"Cover"' in c for c in app.calls)
    # The pointer rests inside the panel (below its first row) and scrolls until the topmost row has not changed
    # over two scrolls (Japan Travel Planner, 20:58: one unchanged read while the panel still moved left it at 1.5).
    assert scrolls[0] == (325 + 473 // 2, 1276 + 4 * 43, capture_app.TOC_SCROLL_LINES) and len(scrolls) == 3
    # A pressed entry with no readable footer afterwards is not the start: that rule let Japan Travel Planner pass
    # at section 1.5 on 2026-10-11 20:58 (its footer says "Page N", which was not read then). The cover does show
    # the footer (Sri Lanka, 20:51), so the run fails honestly instead.
    app = App([COVER, SCROLLED_TOC, SCROLLED_TOC, TOP_TOC, TOP_TOC, TOP_TOC, COVER, COVER, COVER])
    problem = capture_app.go_to_start(app, sleep=lambda s: None, clock=app.clock, scroll=lambda x, y, n: None,
                                      press=lambda role, name: True)
    assert problem.startswith('could not go to the start') and 'no location' in problem
    # What the footer showed instead goes into the error (イスラム飲酒紀行, 2026-10-15: no form of it was read).
    assert problem.endswith('numbers on screen: ' + capture_app.numbers_shown(capture_app.elements(App([COVER])))
                            + ')')
    footer = [{'role': 'AXSlider', 'title': '', 'description': '', 'value': '位置No. 12/3456 , 1%'}]
    assert capture_app.numbers_shown(footer) == '位置No. 12/3456 , 1%'
    assert sum('"Table of Contents"' in c and 'AXPress' in c for c in app.calls) == 1
    # A page-numbered book (Japan Travel Planner, probe of 21:06): "Page 114 , 100%" on the slider is the position.
    pages = (Path(__file__).parent / 'fixtures' / 'kindle' / 'ax-reader-pages-japan.txt').read_text()
    assert capture_app.location_of(capture_app.elements(App([pages]))) == (114, 0)
    at_page_1 = pages.replace('Page 114', 'Page 1').replace('|114|', '|1|')
    app = App([at_page_1, READER])
    shown = {}
    assert capture_app.go_to_start(app, sleep=lambda s: None, clock=app.clock, shown=shown) == ''
    assert 'start: Location 1 after' in capsys.readouterr().out and shown == {'start': 'Location 1', 'reading': capture_app.LTR}
    # Without the API's press (a fake script), the button row is pressed by AppleScript as a button, not a text.
    app = App([SCROLLED_TOC, COVER])
    how = capture_app.toc_first(app, sleep=lambda s: None, clock=app.clock)
    assert how == 'pressed "After One Month in Sri Lanka" at the top of the table of contents'  # no scroll without a pointer
    assert any('AXButton' in c and '"After One Month in Sri Lanka"' in c and 'AXPress' in c for c in app.calls)


def test_the_end_of_book_sheet_is_closed_and_its_image_dropped_after_a_capture(tmp_path):
    END_SHEET = (READER + 'element|AXStaticText|||Overall rating||600,1400|200,20\n'
                 'element|AXButton|||Recommend this book||600,1700|200,40\n')
    # Japan Travel Planner, 2026-10-11 20:59: page_0072.png was the rating sheet, with the focus on its first star.
    for n in (1, 2, 3):
        (tmp_path / f'page_000{n}.png').write_bytes(b'')
    app = App([END_SHEET, READER])
    pages, ended = capture_app.capture(tmp_path, run=lambda cmd: None, script=app, sleep=lambda s: None, clock=app.clock)
    assert [p.name for p in pages] == ['page_0001.png', 'page_0002.png'] and not (tmp_path / 'page_0003.png').exists()
    assert ended
    assert sum('key code 53' in c for c in app.calls) == 2 and not any('key code 36' in c or 'key code 49' in c for c in app.calls)
    # A book that ends on a plain page keeps every image.
    (tmp_path / 'page_0003.png').write_bytes(b'')
    app = App([READER])
    pages, ended = capture_app.capture(tmp_path, run=lambda cmd: None, script=app, sleep=lambda s: None, clock=app.clock)
    assert len(pages) == 3 and not ended
    assert not capture_app.end_sheet_shown(capture_app.elements(App([CHROME.format(at=1)])))


def test_a_book_the_app_does_not_open_or_cannot_page_back_is_named_with_what_the_app_showed(tmp_path):
    def nope(*a, **k):
        pytest.fail('captured')
    _, opened = capture_app.capture_pages('B2', 'Tokyo', tmp_path, open_=lambda a, t: 'front window: "Library"', take=nope)
    assert opened['blocking_failures'] == ['app_open'] and 'Library' in opened['error']
    app = App([READER + 'element|AXButton||||Next Page||500,2000|20,20\n'])
    reason = capture_app.go_to_start(app, sleep=lambda s: None, clock=app.clock)
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
        self.screens, self.calls, self.t = list(screens), [], 0

    def clock(self):
        self.t += 1
        return self.t

    def __call__(self, text):
        self.calls.append(text)
        if 'set value of e to 0' in text:
            return 0, 'set', ''
        if 'entire contents' in text and 'AXPress' not in text and 'keystroke' not in text:
            return 0, self.screens.pop(0) if len(self.screens) > 1 else self.screens[0], ''
        if 'click at' in text and 'keystroke' not in text:
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


# イスラム飲酒紀行, recorded on the Mac 2026-10-15 12:24: the cover's name and value as the library showed them (the
# frames here are made up), and the reader's lines of fixtures/kindle/ax-reader-toolbar-islam.txt, whose footer
# shows the position as a bare "1" over the knob and nothing says "Location".
JA_LIBRARY = ('frontmost=true\nwindow|Kindle|340,173|1370,880\n'
              'element|AXGroup||Library Grid View|||340,300|1370,700\n'
              'element|AXButton|||イスラム飲酒紀行 (ＳＰＡ！ＢＯＯＫＳ) (Japanese Edition), 高野 秀行, RED, |Book Downloaded.|360,320|147,220\n'
              'element|AXButton|||For 91 Days In Tokyo, Powell, Michael, New, |Book Downloaded.|520,320|147,220\n')
JA_READER = ('frontmost=true\nwindow|Kindle|340,173|1370,880\n'
             'element|AXButton|||Close Book||360,195|40,40\n'
             'element|AXButton|||Table of Contents||400,195|40,40\n'
             'element|AXHeading|||イスラム飲酒紀行 (ＳＰＡ！ＢＯＯＫＳ) (Japanese Edition)||859,208|331,14\n'
             'element|AXButton|||More options||1648,195|40,40\n'
             "element|AXButton|||Bird's Eye View||360,964|37,37\n"
             'element|AXStaticText|||{at}||431,956|5,12\n'
             'element|AXStaticText|||688||718,956|18,12\n'
             'element|AXGenericElement||||Image|732,167|585,843\n')


def test_a_japanese_book_is_found_by_its_title_and_its_footer_read_from_the_bare_number_over_the_knob():
    found = capture_app.elements(App([JA_LIBRARY]))
    cover = capture_app.cover_of('イスラム飲酒紀行', found)
    assert cover is not None and cover['description'].startswith('イスラム飲酒紀行')
    assert capture_app.cover_of('ｲｽﾗﾑ飲酒紀行 (SPA!BOOKS)', found) is cover  # full-width and half-width forms fold
    assert capture_app.cover_of('外国語学習に成功する人', found) is None
    assert capture_app.cover_of('For 91 Days In Tokyo', found)['description'].startswith('For 91')
    for at in (1, 2, 4):
        assert capture_app.location_of(capture_app.elements(App([JA_READER.format(at=at)]))) == (at, 0)
    # At the start already: the book is not moved, and the run goes on to the capture.
    app = App([JA_READER.format(at=1), READER])
    assert go(app) == '' and not any('"Table of Contents"' in c for c in app.calls)
    # Numbers elsewhere (a page's own digits) are not the footer.
    stray = JA_READER.format(at=3).replace('431,956', '431,500').replace('718,956', '718,500')
    assert capture_app.location_of(capture_app.elements(App([stray]))) is None


def test_a_cover_off_screen_is_searched_for_first():
    japan = LIBRARY.replace('For 91 Days in Bolivia, Powell, Michael, ', 'Japan Travel Planner, Someone, ')
    app = App([LIBRARY, LIBRARY, LIBRARY, japan, READER])
    clock = iter(range(0, 1000, 2))
    assert capture_app.open_book('B3', 'Japan Travel Planner', script=app, run=lambda c: None,
                                 clock=lambda: next(clock), sleep=lambda s: None) == ''
    assert any('keystroke return' in c and '"Japan Travel Planner"' in c for c in app.calls)
    # The field's value set through AX showed "Clear text" yet left the grid unfiltered (保守とは何だろうか,
    # 2026-10-15 22:09), and keystroke cannot type Japanese through the input method: the title is pasted instead.
    search = next(c for c in app.calls if 'keystroke return' in c)
    assert 'set the clipboard to "Japan Travel Planner"' in search and 'keystroke "v" using command down' in search
    assert 'set value of e to' not in search
    app = App([LIBRARY, LIBRARY])
    problem = capture_app.open_book('B9', 'Drive', script=app, run=lambda c: None, clock=lambda: next(clock), sleep=lambda s: None)
    assert problem.startswith('no cover for this title') and 'Search Kindle' in problem


def test_a_cover_not_downloaded_is_pressed_again_and_waited_for_as_laiken_said():
    # Laiken, 2026-10-11 17:21: もう 1 回クリックすればダウンロードが始まる.
    app = App([LIBRARY] * 40 + [READER])
    clock = iter(range(0, 20000, 2))
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
    assert len(activations) == 3 and all(a < p for a, p in zip(activations[1:], presses)) and 'frontmost before' in problem


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
    problem = capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None, sleep=lambda s: None,
                                    clock=app.clock)
    assert 'neither its library nor the reader toolbar' in problem and sum('click at' in c for c in app.calls) == 6  # 3 clicks, Escape x2, 3 clicks
    assert not any('menu bar item "File"' in c or 'keystroke "w"' in c for c in app.calls)
    app = App([READER, CHROME.format(at=12), READER])
    problem = capture_app.open_book('B2', 'For 91 Days In Tokyo', script=app, run=lambda c: None, sleep=lambda s: None,
                                    clock=app.clock)
    assert problem.startswith('the Kindle app is not showing its library (Close Book: pressed)')


TOC = (CHROME + 'element|AXButton|||Close menu||866,1187|585,843\n'
       'element|AXHeading|||For 91 Days in Bolivia||638,1223|140,17\n'
       'element|AXStaticText|||Powell, Michael||665,1250|86,15\n'
       'element|AXButton|||Close menu||832,1214|33,33\n'
       'element|AXStaticText|||Cover||575,1287|31,15\n'
       'element|AXStaticText|||Beginning||575,1330|59,15\n')


def go(app, script=None):
    return capture_app.go_to_start(script or app, sleep=lambda s: None, clock=app.clock)


def test_the_start_is_reached_through_the_table_of_contents_and_read_from_the_footer(capsys):
    # Probe of 2026-10-11 19:20 (Bolivia at Location 185): the TOC panel lists "Cover" and "Beginning" as static
    # texts after the "Close menu" X; AXPress on the first moved the book to Location 1 on the Mac at 19:26. The
    # slider is not touched: its value, a track click and a drag all moved nothing (runs of 19:02 and 19:11).
    app = App([READER, CHROME.format(at=12), TOC.format(at=12), CHROME.format(at=1), CHROME.format(at=1), READER])
    assert go(app) == ''
    assert any('"Table of Contents"' in c and 'AXPress' in c for c in app.calls)
    assert sum('"AXStaticText"' in c and '"Cover"' in c and 'AXPress' in c for c in app.calls) == 1
    assert not any('key code 123' in c or 'set value' in c or 'click at {5' in c for c in app.calls)
    assert sum('click at' in c for c in app.calls) == 2  # one to show the toolbar, one to hide it
    assert 'start: Location 1 after' in capsys.readouterr().out
    # A book already at Location 1 is not moved (run of 2026-10-11 18:19 opened on the cover).
    app = App([CHROME.format(at=1), READER])
    assert go(app) == ''
    assert sum('click at' in c for c in app.calls) == 1 and not any('"Table of Contents"' in c for c in app.calls)
    # The footer's total came back as 0 in the probe of 18:04; the start is judged by N alone.
    at1 = CHROME.format(at=1).replace('of 2594', 'of 0')
    app = App([CHROME.format(at=12), TOC.format(at=12), at1, at1, READER])
    assert go(app) == ''
    # A page that renders slowly (Iceland, 2026-10-11 20:11: blank for over 30 s) is clicked for about half a minute.
    app = App([READER])
    assert 'no toolbar after clicks' in go(app)
    assert sum('click at' in c for c in app.calls) == capture_app.RENDER_TRIES
    # The Accessibility API's tree (run of 2026-10-11 20:15) holds the toolbar's buttons and the footer but no
    # slider; the Close Book button is enough to know the toolbar is shown.
    no_slider = READER + ('element|AXButton|||Close Book||337,1215|40,40\n'
                          'element|AXStaticText|||Location 1 of 2344 , 1%||940,2019|122,11\n')
    app = App([no_slider, READER])
    assert go(app) == '' and sum('click at' in c for c in app.calls) == 1
    app = App([READER] * 20 + [CHROME.format(at=1), READER])
    assert go(app) == ''


def test_the_topmost_entry_is_pressed_whatever_its_name_and_clicked_when_the_press_is_refused():
    # Laiken, 2026-10-11 19:26: any topmost entry leads to the start, not only "Cover".
    app = App([CHROME.format(at=12), TOC.format(at=12).replace('|Cover|', '|Chapter 1|'), CHROME.format(at=1),
               CHROME.format(at=1), READER])
    assert go(app) == ''
    assert sum('"AXStaticText"' in c and '"Chapter 1"' in c for c in app.calls) == 1
    assert not any('"Powell, Michael"' in c for c in app.calls)  # the author line above the X is not an entry
    # A refused press falls back to a click at the row's centre (575+15, 1287+7).
    app = App([CHROME.format(at=12), TOC.format(at=12), CHROME.format(at=1), CHROME.format(at=1), READER])
    inner = app.__call__

    def script(text):
        if '"AXStaticText"' in text:
            app.calls.append(text)
            return 0, 'none', ''
        return inner(text)
    assert go(app, script) == ''
    assert sum('click at {590, 1294}' in c for c in app.calls) == 1


def test_when_the_table_of_contents_does_not_move_the_book_pages_are_turned_back_one_at_a_time(capsys):
    # Laiken, 2026-10-11 19:27: when nothing else works, page back with ← rather than stop. The footer is read after
    # each batch of 20 presses, with the toolbar hidden while the keys are sent.
    at = CHROME.format(at=185)
    app = App([at, TOC.format(at=185), at, at,            # the TOC entry leaves Location 185
               at, READER, READER, CHROME.format(at=100),  # batch 1: hidden, keys, shown again
               READER, READER, CHROME.format(at=1),        # batch 2
               CHROME.format(at=1), READER])
    assert go(app) == ''
    assert sum('key code 123' in c for c in app.calls) == 40
    out = capsys.readouterr().out
    assert 'start: Location 1 after' in out and 'pressed "Cover"' in out and 'paged back 40 times' in out
    # ← presses that leave the footer where it is are counted and named, and the loop stops; a table of contents
    # without an entry is closed again and named too.
    toc = TOC.format(at=185)
    empty = toc[:toc.index('element|AXStaticText|||Cover')]
    app = App([at, empty, at, at, READER, READER, at, at])
    problem = go(app)
    assert 'no entry in the table of contents' in problem and 'paged back 20 times and the footer stayed at Location 185' in problem
    assert 'Location 185 of 2594' in problem and sum('key code 123' in c for c in app.calls) == 20
    assert any('"Close menu"' in c for c in app.calls)


def test_a_probe_records_what_the_new_app_shows_before_and_after_opening_a_book(tmp_path):
    # Laiken chose to fit the new Kindle app (2026-10-11); its steps are written from this dump, not guessed.
    calls, ebooks = [], tmp_path / 'eBooks'
    ebooks.mkdir()
    (ebooks / 'B2_EBOK').mkdir()
    sheet = READER + 'element|AXButton||Close|||988,1771|24,9\n'
    chrome = READER + 'element|AXSlider||||12|500,2000|1000,20\n'
    app = App([LIBRARY, LIBRARY, LIBRARY, sheet, sheet, READER, chrome])
    folder = capture_app.probe('B2', 'For 91 Days In Tokyo', tmp_path / 'probe', script=app, run=calls.append,
                               sleep=lambda s: None, ebooks=ebooks, clock=app.clock)
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


def test_the_run_path_waits_for_elements_not_for_fixed_seconds():
    # Laiken, 2026-10-11 19:59: opening a book took minutes; fixed sleeps of 2-5 s stood at a dozen places (main
    # 0699510) on top of a 114 s tree read. A wait is wait_for(ready, ...) until the element appears; only probe(),
    # which records screens for a person to read, may pause for a fixed time.
    src = Path(capture_app.__file__).read_text(encoding='utf-8')
    run_path = src[:src.index('def probe(')]
    fixed = re.findall(r'sleep\((?:[2-9]|\d{2,})\b', run_path)
    assert not fixed, f'{len(fixed)} fixed sleeps on the run path; wait with wait_for(ready, script, sleep, clock) instead'


def test_an_app_capture_the_checks_refuse_is_parked_for_the_app_and_captured_again_beside_the_old_pages(tmp_path):
    # 2026-10-15: two app captures failed start_position and end_position, and the capture job sent イスラム飲酒紀行 to
    # the extension, which cannot open an app-only book; it moved the 254 pages aside and was refused.
    books, library = write_list(tmp_path, app_only=('B2',))
    tokyo = library.book('B2')
    pages = tokyo.folder / 'pages'
    pages.mkdir()
    (pages / 'page_0001.png').write_bytes(b'')
    captured = tokyo.succeed('capture', {'pages': str(pages), 'title': 'For 91 Days In Tokyo', 'attempt': 1,
                                         'via': 'kindle_app', 'version': book.versions()['capture']})
    tokyo.fail('convert', {'checks': ['start_position', 'end_position'], 'error': 'start not recorded', 'attempt': 1,
                           'version': book.versions()['convert']}, upstream=captured['at'])
    state = tokyo.state()
    assert isinstance(state, book.Parked) and state.stage == 'convert'  # not Ready('capture'): the extension cannot
    seen = []
    run(books, fake_app(seen))
    assert seen == ['For 91 Days In Tokyo']
    assert tokyo.state() == book.Ready('convert') and tokyo.record('capture')['attempt'] == 2
    assert [p.name for p in tokyo.folder.glob('pages (before *)')] and (pages / 'page_0001.png').exists()


def test_an_app_book_the_extension_refused_after_moving_its_app_pages_aside_is_captured_again_in_the_app(tmp_path):
    # 2026-10-15, before F167: the capture job sent イスラム飲酒紀行 (an app capture) to the extension, which moved its 254
    # pages to "pages (before ...)" and was refused, so its record lost via kindle_app. Pages without positions.json
    # came from the app: the extension writes one beside every capture.
    books, library = write_list(tmp_path, app_only=())
    tokyo, planner = library.book('B2'), library.book('B3')
    for one, positions in ((tokyo, False), (planner, True)):
        before = one.folder / 'pages (before 20261015T130101)'
        before.mkdir()
        (before / 'page_0001.png').write_bytes(b'')
        if positions:
            (before / 'positions.json').write_text('[]')
        one.fail('capture', {'checks': ['capture_refused'], 'error': 'refused', 'attempt': 2, 'retry': False,
                             'version': book.versions()['capture']})
    seen = []
    run(books, fake_app(seen))
    assert seen == ['For 91 Days In Tokyo']  # Japan Travel Planner's old pages are the extension's: it stays for it
    assert tokyo.state() == book.Ready('convert') and tokyo.record('capture')['via'] == 'kindle_app'


def test_a_book_that_ends_without_the_sheet_is_ended_when_the_footer_stops_moving(tmp_path):
    # アジャイルデータモデリング and イスラム飲酒紀行 (2026-10-15 14:04, 14:14): both captured to their last page, yet
    # no end-of-book sheet came up, so capture.json said end "" and end_position would fail them again.
    for n in (1, 2, 3):
        (tmp_path / f'page_000{n}.png').write_bytes(b'')
    app = App([READER, READER, CHROME.format(at=2590), READER, CHROME.format(at=2590), READER])
    pages, ended = capture_app.capture(tmp_path, run=lambda cmd: None, script=app, sleep=lambda s: None,
                                       clock=app.clock)
    assert len(pages) == 3 and ended == 'Location 2590 of 2594, where → moved the footer no further'
    assert any('key code 124' in c for c in app.calls)
    # A footer that still moves is no end: the capture stopped short of it.
    app = App([READER, READER, CHROME.format(at=1200), READER, CHROME.format(at=1205), READER])
    _, ended = capture_app.capture(tmp_path, run=lambda cmd: None, script=app, sleep=lambda s: None, clock=app.clock)
    assert ended == ''
    folder = tmp_path / 'book'
    _, failure = capture_app.capture_pages('B2', 'Tokyo', folder, open_=lambda a, t: '', start=lambda shown: '',
                                           take=lambda f, reading: ([f / 'page_0001.png'], 'Location 9 of 9, where → moved the footer no further'))
    assert failure is None and json.loads((folder / 'capture.json').read_text())['end'].startswith('Location 9 of 9')


class Knob(App):
    """A book whose footer shows its position as a bare number over the knob, beside a mark that stays put. A vertical
    Japanese book (外国語学習に成功する人 and 英語独習法, 2026-10-15 22:09) turns to the next page with ←, and its
    position stood at the right of the row: the leftmost number, 699 and 163, never moved while ← was pressed."""

    def __init__(self, at, last, rtl=True, mark=699, near=0):
        super().__init__([READER])
        self.at, self.last, self.rtl, self.mark, self.near, self.chrome = at, last, rtl, mark, near, True

    def screen(self):
        if not self.chrome:
            return READER
        left, right = (self.mark, self.at) if self.rtl else (self.at, self.mark)
        row = [f'element|AXStaticText|||{left}||431,956|18,12\n', f'element|AXStaticText|||{right}||1600,956|18,12\n']
        if abs(self.at - self.mark) <= self.near:  # the knob's number covers the mark: only the position is in the tree
            row = [f'element|AXStaticText|||{self.at}||1000,956|18,12\n']
        return ('frontmost=true\nwindow|Kindle|340,173|1370,880\n'
                'element|AXButton|||Close Book||360,195|40,40\n'
                'element|AXButton|||Table of Contents||400,195|40,40\n'
                "element|AXButton|||Bird's Eye View||360,964|37,37\n" + ''.join(row))

    def __call__(self, text):
        self.calls.append(text)
        on, back = ('key code 123', 'key code 124') if self.rtl else ('key code 124', 'key code 123')
        if on in text:
            self.at = min(self.last, self.at + 1)
        elif back in text:
            self.at = max(1, self.at - 1)
        elif 'click at' in text:
            self.chrome = not self.chrome
            return 0, 'pressed', ''
        elif 'entire contents' in text and 'AXPress' not in text and 'keystroke' not in text:
            return 0, self.screen(), ''
        return 0, 'pressed' if 'AXPress' in text else 'searched', ''


def test_a_vertical_book_is_paged_back_with_the_right_arrow_and_captured_right_to_left(tmp_path, capsys):
    # 2026-10-15 22:09: both books failed "paged back 20 times and the footer stayed at Location 699" (and 163): ←
    # turned them on, not back, and the number read was the mark. One ← tells which way the book turns and which
    # number moved; the book is then paged back with → and captured with kindle2img's rtl.
    app = Knob(at=163, last=300)
    shown = {}
    assert capture_app.go_to_start(app, sleep=lambda s: None, clock=app.clock, shown=shown) == ''
    assert app.at == 1 and shown['start'] == 'Location 1' and shown['reading'].name == 'rtl'
    assert sum('key code 124' in c for c in app.calls) > 100
    assert 'reading: rtl' in capsys.readouterr().out
    (tmp_path / 'page_0001.png').write_bytes(b'')
    runs = []

    def run(cmd):
        runs.append(cmd)
        app.at = app.last
    app.calls.clear()
    pages, ended = capture_app.capture(tmp_path, shown['reading'], run=run, script=app, sleep=lambda s: None,
                                       clock=app.clock)
    assert runs[0][2] == 'rtl' and len(pages) == 1
    assert ended == 'Location 300, where ← moved the footer no further'
    assert any('key code 123' in c for c in app.calls) and not any('key code 124' in c for c in app.calls)
    # A horizontal book with the same footer keeps ← for back and its leftmost number.
    app = Knob(at=30, last=300, rtl=False)
    shown = {}
    assert capture_app.go_to_start(app, sleep=lambda s: None, clock=app.clock, shown=shown) == ''
    assert app.at == 1 and shown['reading'].name == 'ltr'


def test_a_vertical_book_whose_knob_covers_the_mark_is_read_from_the_right_once_both_numbers_show(capsys):
    # 2026-10-15 23:19, 保守とは何だろうか: "reading: rtl, the position is knob number 1 of 1", then "paged back 20 times
    # and the footer stayed at Location 458". The knob stood over the mark, so the row held one number; once → moved
    # the knob away the mark came back at the left, and the leftmost number, the mark, was read as the position.
    app = Knob(at=457, last=600, mark=458, near=5)
    shown = {}
    assert capture_app.go_to_start(app, sleep=lambda s: None, clock=app.clock, shown=shown) == ''
    assert app.at == 1 and shown['reading'].name == 'rtl' and shown['reading'].pick == -1
    assert 'reading: rtl, the position is knob number 1 of 1' in capsys.readouterr().out
    # A horizontal book in the same spot keeps its leftmost number.
    app = Knob(at=457, last=600, rtl=False, mark=458, near=5)
    shown = {}
    assert capture_app.go_to_start(app, sleep=lambda s: None, clock=app.clock, shown=shown) == ''
    assert app.at == 1 and shown['reading'].name == 'ltr' and shown['reading'].pick == 0


def test_a_vertical_book_opened_at_its_start_with_the_knob_over_the_mark_is_still_read_right_to_left(capsys):
    # 2026-10-15 23:40, 保守とは何だろうか after F175: "start: Location 1 after 5 s: not needed", no "reading:" line,
    # then "end: Location 1, where → moved the footer no further; 1 pages". At the start the knob stood over the mark,
    # so the row held one number; ← moved the knob off it and the row held two, which was taken for nothing moved.
    app = Knob(at=1, last=600, mark=1)
    shown = {}
    assert capture_app.go_to_start(app, sleep=lambda s: None, clock=app.clock, shown=shown) == ''
    assert app.at == 1 and shown['reading'].name == 'rtl' and shown['reading'].pick == -1
    assert 'reading: rtl' in capsys.readouterr().out
    app = Knob(at=1, last=600, rtl=False, mark=1)
    shown = {}
    assert capture_app.go_to_start(app, sleep=lambda s: None, clock=app.clock, shown=shown) == ''
    assert app.at == 1 and shown['reading'].name == 'ltr' and shown['reading'].pick == 0


def test_a_done_app_book_asked_again_in_recapture_tsv_is_captured_again_in_the_app_only_once(tmp_path, monkeypatch):
    # 保守とは何だろうか (2026-10-15): the app captured 1 page, the checks of the time passed it and it went to Drive.
    # A passed book is not redone when the checks change (F036), so Laiken's ask goes through recapture.tsv; the
    # capture job's extension cannot open an app-only book, so the book stays Done for it and the app captures it.
    books, library = write_list(tmp_path, app_only=())
    tokyo = library.book('B2')
    pages = tokyo.folder / 'pages'
    pages.mkdir()
    (pages / 'page_0001.png').write_bytes(b'')
    current = book.versions()
    upstream = None
    for stage, extra in (('capture', {'pages': str(pages), 'title': 'For 91 Days In Tokyo', 'attempt': 1,
                                      'via': 'kindle_app'}), ('convert', {}), ('publish', {'drive': 'tokyo.md'})):
        upstream = tokyo.succeed(stage, {**extra, 'version': current[stage]}, upstream=upstream)['at']
    asks = tmp_path / 'recapture.tsv'
    monkeypatch.setattr(book, 'RECAPTURE', asks)
    asks.write_text(f'B2\t{book.now_id()}\tthe app captured 1 page\n')
    assert isinstance(tokyo.state(), book.Done)  # the capture job leaves it to the app
    seen = []
    run(books, fake_app(seen))
    assert seen == ['For 91 Days In Tokyo']
    assert tokyo.state() == book.Ready('convert') and tokyo.record('capture')['attempt'] == 2
    assert [p.name for p in tokyo.folder.glob('pages (before *)')]
    run(books, fake_app(seen))
    assert seen == ['For 91 Days In Tokyo']  # the ask is answered by the new capture
