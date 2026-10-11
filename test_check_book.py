import json
import os
import subprocess
import sys
from pathlib import Path

import capture_book
from check_book import check_folder
from img2txt import SOURCES

BODY = ('The group became known as the Homebrew Computer Club, and it encapsulated the Whole Earth fusion between '
        'the counterculture and technology. ')
BODY2 = ('Wozniak went with Allen Baum to the first meeting, held in a garage in Menlo Park, and came home with the '
         'idea of building a terminal of his own. ')


def book(tmp_path, pages, sources='vision', md=None, name='How to Write a Lot', laid=True):
    """A converted book folder; laid says whether Gemini has read the layout of its Vision pages."""
    folder = tmp_path / name
    folder.mkdir()
    for n, text in enumerate(pages, 1):
        (folder / f'page_{n:04d}.png').write_bytes(b'png')
        if text is not None:
            (folder / f'page_{n:04d}.txt').write_text(text)
    (folder / SOURCES).write_text(''.join(f'page_{n:04d}\t{sources}\n' for n in range(1, len(pages) + 1)))
    if laid:
        (folder / 'layout.json').write_text(json.dumps({f'page_{n:04d}': {} for n in range(1, len(pages) + 1)}))
    if md is not None:
        (folder / f'{name}.md').write_text(md)
    return folder


def by_name(checks):
    return {c.name: c for c in checks}


def test_clean_book_passes(tmp_path):
    folder = book(tmp_path, [BODY + 'First.', BODY2 + 'Second.'], sources='textlayer', md='# One\n\n' + BODY)
    (folder / 'positions.json').write_text(json.dumps({'page_0001': '1/2', 'page_0002': '2/2'}))
    checks = by_name(check_folder(folder))
    assert all(c.ok for c in checks.values()), {k: c.detail for k, c in checks.items() if not c.ok}


def test_the_defects_of_how_to_write_a_lot_are_each_named_with_a_fix(tmp_path):
    # Vision only, no headings, 7 read as Z, a dash as a hyphen, the default name: what the first run produced.
    folder = book(tmp_path, [BODY + 'Z\n\nDiscussion-this is it.', BODY2 + 'More.'],
                  md='Z\n\n' + BODY + 'Discussion-this is it.', name='kindle-book')
    checks = by_name(check_folder(folder))
    assert not checks['title'].ok and not checks['title'].blocking
    assert not checks['text_layer'].ok and not checks['text_layer'].blocking
    # Reading the pages again would give the same headings, so none is tried.
    assert not checks['headings'].ok and checks['headings'].fix is None
    assert not checks['proofread'].ok and checks['proofread'].fix == ['proofread.py', str(folder), '--apply', 'dash_as_hyphen']
    assert checks['pages'].ok


def test_decorative_digit_headings_and_gaps_are_caught(tmp_path):
    folder = book(tmp_path, [BODY, BODY2], md='# Z\n\n# Chapter &\n\n' + BODY)
    (folder / 'page_0002.png').rename(folder / 'page_0004.png')
    (folder / 'page_0002.txt').rename(folder / 'page_0004.txt')
    checks = by_name(check_folder(folder, title='How to Write a Lot'))
    assert not checks['decorative_digits'].ok and '2 headings' in checks['decorative_digits'].detail
    assert checks['decorative_digits'].fix is None
    assert not checks['pages'].ok and checks['pages'].fix is None
    assert checks['title'].ok  # a title was given


def test_missing_text_and_text_layer_hint(tmp_path):
    folder = book(tmp_path, [BODY, None], md='# A\n\n' + BODY)
    (folder / 'dom-probe.json').write_text('{}')
    checks = by_name(check_folder(folder))
    assert checks['text'].detail == '1 pages without text' and checks['text'].fix == ['img2txt.py', str(folder)]
    assert 'dom-probe.json' in checks['text_layer'].detail


def test_cli_exit_code_follows_blocking_checks(tmp_path):
    folder = book(tmp_path, [BODY], md=BODY)  # no headings: blocking
    env = {**os.environ, 'KINDLE_OCR_CONFIG': '/nonexistent/config.env'}
    result = subprocess.run([sys.executable, 'check_book.py', str(folder), '--json'], capture_output=True, text=True, env=env, cwd=Path(__file__).parent)
    assert result.returncode == 1, result.stderr
    assert {c['name']: c['ok'] for c in json.loads(result.stdout)}['headings'] is False


def test_correct_runs_each_fix_once_and_stops_when_clean(tmp_path, monkeypatch):
    folder = book(tmp_path, [BODY + 'appliance-a unit.'], md='# One\n\n' + BODY + 'appliance-a unit.', laid=False)
    commands = []

    def fake_run(command):
        commands.append(command[1:])
        # The fixes "work": Gemini reads the layout and the dash is restored.
        (folder / 'layout.json').write_text(json.dumps({'page_0001': {}}))
        (folder / 'page_0001.txt').write_text(BODY + 'appliance—a unit.')
        (folder / f'{folder.name}.md').write_text('# One\n\n' + BODY + 'appliance—a unit.')

    checks, rounds = capture_book.correct(folder, None, runner=fake_run)
    # Proofreading goes last: re-reading pages after it would undo its fixes (AI for Educators, 2026-10-10).
    assert [c[0].split('/')[-1] for c in commands] == ['img2txt.py', 'proofread.py']
    assert rounds == 2 and all(c.ok or not c.blocking for c in checks)


def test_correct_gives_up_after_the_round_limit(tmp_path):
    folder = book(tmp_path, [BODY], md=BODY, laid=False)
    calls = []
    checks, rounds = capture_book.correct(folder, None, rounds=2, runner=lambda c: calls.append(c))
    assert rounds == 2 and len(calls) == 2
    assert [c.name for c in checks if not c.ok and c.blocking] == ['layout', 'headings']


def test_wait_for_zip_returns_the_new_settled_archive(tmp_path):
    before = {tmp_path / 'old.zip'}
    (tmp_path / 'old.zip').write_bytes(b'x')
    ticks = iter(range(0, 100, 1))
    assert capture_book.wait_for_zip(tmp_path, before, timeout=5, clock=lambda: next(ticks), sleep=lambda s: None) is None
    (tmp_path / 'New Book.zip').write_bytes(b'zip')
    ticks = iter(range(0, 100, 1))
    assert capture_book.wait_for_zip(tmp_path, before, timeout=5, clock=lambda: next(ticks), sleep=lambda s: None) == tmp_path / 'New Book.zip'


def test_report_lists_failures_and_warnings(tmp_path):
    folder = book(tmp_path, [BODY], md=BODY, name='kindle-book')
    data = capture_book.report(folder, check_folder(folder), {'asin': 'X'})
    assert data['blocking_failures'] == ['headings'] and 'title' in data['warnings']
    assert json.loads((folder / 'report.json').read_text())['asin'] == 'X'


def positions(folder, pairs):
    (folder / 'positions.json').write_text(json.dumps({f'page_{i:04d}': p for i, p in enumerate(pairs, 1)}))


def test_a_capture_that_began_mid_book_is_caught(tmp_path):
    # AI for Educators: the reader reopened at page 75 of 120 and the capture ran from there to the end.
    folder = book(tmp_path, [BODY] * 4, md='# A\n\n' + BODY)
    positions(folder, ['75/120', '75/120', '76/120', '120/120'])
    checks = by_name(check_folder(folder))
    assert not checks['start_position'].ok and checks['start_position'].blocking
    assert 'page 75 of 120' in checks['start_position'].detail
    assert checks['end_position'].ok and checks['positions'].ok


def test_a_capture_that_stopped_early_is_caught_but_end_matter_is_allowed(tmp_path):
    folder = book(tmp_path, [BODY] * 3, md='# A\n\n' + BODY)
    positions(folder, ['', '1/120', '60/120'])  # an unnumbered cover, then the book
    checks = by_name(check_folder(folder))
    assert checks['start_position'].ok and not checks['end_position'].ok
    positions(folder, ['', '1/120', '118/120'])  # the last two pages are end matter the reader does not number
    assert by_name(check_folder(folder))['end_position'].ok


def test_without_positions_the_probe_gives_the_start_and_a_warning_remains(tmp_path):
    folder = book(tmp_path, [BODY] * 3, md='# A\n\n' + BODY)
    # dom-probe.json as extension-dom-probe wrote it: probes on pages 1 and 3 with the footer text.
    (folder / 'dom-probe.json').write_text(json.dumps([
        {'page': 1, 'titleCandidates': [{'class': 'footer-label position', 'text': 'Page 75 of 120\u2002●\u200259%'}]},
        {'page': 3, 'titleCandidates': [{'class': 'footer-label position', 'text': 'Page 76 of 120\u2002●\u200260%'}]}]))
    checks = by_name(check_folder(folder))
    assert not checks['start_position'].ok
    assert not checks['positions'].ok and not checks['positions'].blocking
    assert checks['end_position'].ok and checks['end_position'].detail == 'end not recorded'


def test_tab_left_open_is_closed_and_reported(tmp_path):
    counts = iter([1, 0])
    closed = []
    assert capture_book.ensure_tab_closed('B1', count=lambda a: next(counts), close=closed.append, sleep=lambda s: None)
    assert closed == ['B1']
    closed.clear()
    assert not capture_book.ensure_tab_closed('B1', count=lambda a: 1, close=closed.append, sleep=lambda s: None)
    assert closed == ['B1'] * capture_book.TAB_TRIES
    # Brave could not be asked: nothing is closed blindly, and the tab is not reported closed.
    assert not capture_book.ensure_tab_closed('B1', count=lambda a: None, close=closed.append, sleep=lambda s: None)


def test_heading_noise_missed_chapters_and_digits_in_the_text_are_caught(tmp_path):
    # How to Write a Lot after the first heading rule: cover words, a photo, a mid-sentence line as headings,
    # chapter numbers and titles left as plain text, and Z for 7 in a chapter reference.
    cover = '# How\n\n# Write\n\n# Iot'
    opening = '2\n\nSpecious Barriers to Writing a Lot\n\n' + BODY + BODY2
    mid = '# about the rates and slopes, people!\n\n' + BODY + 'In Chapter Z, we turn to books.'
    folder = book(tmp_path, [cover, opening, mid], md='\n\n'.join([cover, opening, mid]), laid=False)
    checks = by_name(check_folder(folder, title='How to Write a Lot'))
    assert not checks['heading_noise'].ok and checks['heading_noise'].detail.startswith('4 headings')
    assert not checks['chapter_titles'].ok and checks['chapter_titles'].detail.startswith('1 chapter')
    assert ' / ' in checks['chapter_titles'].detail  # the opening itself, for a fix from evidence
    assert not checks['decorative_digits'].ok and '1 chapter references' in checks['decorative_digits'].detail
    assert checks['proofread'].fix[3] == 'decorative_digit'
    # Noise is made plain text again; a missed chapter is reported, since reading again repeats it.
    assert checks['heading_noise'].fix[:4] == ['proofread.py', str(folder), '--apply', 'heading_noise']
    assert checks['chapter_titles'].fix is None


def test_real_titles_are_not_noise(tmp_path):
    page = '# 2 Specious Barriers to Writing a Lot\n\n## SPECIOUS BARRIER 1\n\n' + BODY + BODY2
    folder = book(tmp_path, [page], md=page)
    checks = by_name(check_folder(folder))
    assert checks['heading_noise'].ok and checks['chapter_titles'].ok and checks['decorative_digits'].ok


def test_a_capture_cut_off_by_a_turn_cap_is_caught(tmp_path):
    folder = book(tmp_path, [BODY + BODY2], md=BODY + BODY2)
    (folder / 'capture.json').write_text(json.dumps({'extension_version': '1.5', 'stopped': 'the reader stayed on page 517 for 30 turns'}))
    checks = by_name(check_folder(folder))
    assert not checks['capture_stop'].ok and 'page 517' in checks['capture_stop'].detail
    (folder / 'capture.json').write_text(json.dumps({'extension_version': '1.5', 'stopped': ''}))
    assert by_name(check_folder(folder))['capture_stop'].ok


def test_a_skip_notice_ends_the_wait_at_once(tmp_path):
    before = {tmp_path / 'B07VSXS4NK.skip.json'}
    (tmp_path / 'B07VSXS4NK.skip.json').write_text('{}')  # from an earlier run: not this capture's
    (tmp_path / 'B0OTHER.skip.json').write_text('{}')
    ticks = iter(range(0, 100, 1))
    wait = lambda: capture_book.wait_for_zip(tmp_path, before, timeout=5, clock=lambda: next(ticks), sleep=lambda s: None,
                                            asin='B07VSXS4NK')
    assert wait() is None
    (tmp_path / 'B07VSXS4NK.skip (1).json').write_text(json.dumps({'reason': 'could not page back to page 1'}))
    ticks = iter(range(0, 100, 1))
    assert wait() == tmp_path / 'B07VSXS4NK.skip (1).json'


def test_a_tab_left_on_the_book_is_closed_before_opening_another_or_the_book_is_not_started(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(capture_book, 'folder_problem', lambda: None)
    monkeypatch.setattr(capture_book, 'open_in_brave', lambda *a: opened.append(a))
    monkeypatch.setattr(capture_book, 'ensure_tab_closed', lambda asin: False)  # Brave would not close it
    data = capture_book.convert('B07VSXS4NK', 'Automate', downloads=tmp_path, recapture=True)
    assert data['blocking_failures'] == ['tab_before'] and opened == []


def test_a_refused_capture_fails_with_the_extensions_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_book, 'folder_problem', lambda: None)
    monkeypatch.setattr(capture_book, 'ensure_tab_closed', lambda asin: True)

    def open_in_brave(asin, domain, recapture):
        (tmp_path / f'{asin}.skip.json').write_text(json.dumps({'reason': 'tab 7 is already capturing B07VSXS4NK'}))

    monkeypatch.setattr(capture_book, 'open_in_brave', open_in_brave)
    data = capture_book.convert('B07VSXS4NK', 'Automate', downloads=tmp_path)
    assert data['blocking_failures'] == ['capture_refused'] and 'already capturing' in data['error']


def test_a_gemini_failure_is_reported_with_its_reason_and_the_folder_to_convert_again(tmp_path, monkeypatch):
    import subprocess
    folder = tmp_path / 'Facing the Mountain'
    folder.mkdir()

    def run(command, **kwargs):
        (folder / capture_book.GEMINI_ERROR).write_text('Gemini layout failed with 10 pages left: 429 PerDay')
        raise subprocess.CalledProcessError(capture_book.GEMINI_FAILED_EXIT, command)

    monkeypatch.setattr(capture_book, 'run', run)
    data = capture_book.convert('B0X', 'Facing the Mountain', source=folder)
    assert data['blocking_failures'] == ['gemini'] and data['source'] == str(folder)
    assert data['error'].endswith('429 PerDay')


def test_a_book_tab_gone_with_no_zip_ends_the_wait_in_minutes_not_an_hour(tmp_path):
    # 2026-10-11: For 91 Days In Tokyo's tab vanished a second after opening; the list waited the full hour.
    now = [0]

    def clock():
        return now[0]

    def sleep(s):
        now[0] += s

    counts = iter([None, 0, 1, 1, 0])  # not asked yet, not open yet, capturing, then gone for good
    tabs = lambda asin: next(counts, 0)
    assert capture_book.wait_for_zip(tmp_path, set(), timeout=3600, clock=clock, sleep=sleep, asin='B00S1MW0KM',
                                     tabs=tabs) == capture_book.TAB_GONE
    assert now[0] < 300


def test_a_book_tab_still_open_keeps_the_wait_going(tmp_path):
    now = [0]

    def sleep(s):
        now[0] += s

    assert capture_book.wait_for_zip(tmp_path, set(), timeout=600, clock=lambda: now[0], sleep=sleep, asin='B0',
                                     tabs=lambda asin: 1) is None
    assert now[0] >= 600


def test_a_book_cloud_reader_cannot_open_is_the_book_s_failure_not_the_browser_s(tmp_path, monkeypatch):
    # 2026-10-11: For 91 Days In Tokyo opened to "Kindle App Is Required" and the list waited an hour.
    import capture_books
    monkeypatch.setattr(capture_book, 'folder_problem', lambda: None)
    monkeypatch.setattr(capture_book, 'ensure_tab_closed', lambda asin: True)

    def open_in_brave(asin, domain, recapture):
        (tmp_path / f'{asin}.skip.json').write_text(json.dumps({'reason': 'kindle app required: Cloud Reader cannot open this book'}))

    monkeypatch.setattr(capture_book, 'open_in_brave', open_in_brave)
    data = capture_book.convert('B00S1MW0KM', 'For 91 Days In Tokyo', downloads=tmp_path)
    assert data['blocking_failures'] == ['kindle_app_only']
    assert 'kindle_app_only' not in capture_books.STOP_CHECKS
    assert capture_books.decide(data, 1) == 'failed'


def test_book_tabs_are_closed_one_by_one_from_the_last():
    # 2026-10-11: Brave ignored `close (every tab ... whose URL contains ...)` and a book tab stayed open.
    script = capture_book.close_tab_script('B005RR0XRG')
    assert 'whose URL' not in script
    assert 'repeat with i from (count of tabs of w) to 1 by -1' in script
    assert 'if URL of tab i of w contains "asin=B005RR0XRG" then close tab i of w' in script


def test_a_book_that_passed_starts_the_wiki_watcher_and_a_failed_one_does_not(tmp_path):
    watch = tmp_path / 'watch.sh'
    watch.write_text('')
    started = []
    spawn = lambda command, **kwargs: started.append((command, kwargs['start_new_session'])) or 'proc'
    assert capture_book.start_wiki_ingest({'blocking_failures': ['headings']}, watch, spawn) is None
    assert capture_book.start_wiki_ingest({'blocking_failures': []}, tmp_path / 'missing.sh', spawn) is None
    assert capture_book.start_wiki_ingest({'blocking_failures': []}, watch, spawn) == 'proc'
    assert started == [(['/bin/bash', str(watch)], True)]


def test_only_the_runner_copies_a_book_to_drive_and_a_trial_conversion_does_not(tmp_path):
    # 2026-10-11: img2txt.py --layout sonnet on AI for Educators, run only to compare, replaced the book on Drive.
    from img2txt import write_book
    drive = tmp_path / 'Kindle_md'
    drive.mkdir()
    folder = tmp_path / 'AI for Educators'
    folder.mkdir()
    (folder / 'page_0001.png').write_bytes(b'')
    (folder / 'page_0001.txt').write_text('# One\n\n' + BODY, encoding='utf-8')
    write_book(folder, 'AI for Educators')
    assert (folder / 'AI for Educators.md').exists() and not list(drive.iterdir())
    copied = capture_book.publish(folder, 'AI for Educators', {'GOOGLE_DRIVE_FOLDER': str(drive)})
    assert copied == drive / 'AI for Educators.md' and copied.read_text(encoding='utf-8').startswith('# One')
    assert capture_book.publish(folder, 'AI for Educators', {}) is None
    assert capture_book.publish(folder, 'AI for Educators', {'GOOGLE_DRIVE_FOLDER': str(tmp_path / 'gone')}) is None


def stalled_wait(tmp_path, titles, seconds=3600):
    now = [0]
    def sleep(s):
        now[0] += s
    beats = iter(titles)
    last = ['']
    def beat(asin):
        last[0] = next(beats, last[0])
        return last[0]
    result = capture_book.wait_for_zip(tmp_path, set(), timeout=seconds, clock=lambda: now[0], sleep=sleep, asin='B07J4521M3',
                                       tabs=lambda a: 1, beats=beat)
    return result, now[0]


def test_a_capture_whose_heartbeat_stops_is_ended_in_minutes_not_an_hour(tmp_path):
    # 2026-10-11: Python Crash Course gave no ZIP in an hour and nothing showed whether it had stopped.
    result, waited = stalled_wait(tmp_path, ['page 40/540 visible', 'page 41/540 visible'])
    assert isinstance(result, capture_book.Stalled) and result == 'page 41/540 visible'
    assert waited < capture_book.STALL_S + 10


def test_a_hidden_tab_that_still_turns_slowly_is_redone_rather_than_waited_on(tmp_path):
    titles = [f'page {n // 30}/540 hidden' for n in range(1000)]  # one new page a minute
    result, waited = stalled_wait(tmp_path, titles)
    assert isinstance(result, capture_book.Stalled) and 'hidden' in result and waited < capture_book.STALL_S + 10


def test_a_long_book_that_keeps_turning_is_waited_on_past_the_hour(tmp_path):
    titles = [f'page {n}/3000 visible' for n in range(10000)]
    result, waited = stalled_wait(tmp_path, titles, seconds=600)
    assert result is None and waited >= capture_book.ZIP_WAIT_LONG_S


def test_without_a_heartbeat_the_wait_is_as_before(tmp_path):
    result, waited = stalled_wait(tmp_path, [], seconds=600)
    assert result is None and 600 <= waited < 620


def test_index_letter_is_not_a_chapter_opening(tmp_path):
    # Python Crash Course: the index letter Z, read as 7, then an entry and the colophon.
    page = ('# INDEX\n\n7\n\nZen of Python, 30-31 ZeroDivisionError, 194\n\nPython Crash Course, 2nd Edition is set in '
            'New Baskerville, Futura, Dogma, and The Sans Mono Condensed. ' + BODY)
    folder = book(tmp_path, [page], md=page)
    assert by_name(check_folder(folder))['chapter_titles'].ok


def test_a_numbered_section_with_an_epigraph_is_not_a_missed_chapter(tmp_path):
    page = ('# ONE\n\n' + BODY + '\n\n2\n\n> "It was science fiction more than computer science."\n\n' + BODY)
    folder = book(tmp_path, [page], md=page)
    assert by_name(check_folder(folder))['chapter_titles'].ok


def test_a_code_book_without_code_blocks_is_caught_and_its_pages_read_again(tmp_path):
    # Python Crash Course (2026-10-11): no fence in the book; comments became titles, listings bullets or prose.
    session = ['>>> age = 18', '>>> age == 18', 'True', 'import this', 'def greet_user():']
    folder = book(tmp_path, [BODY] * 5, name='Python Crash Course',
                  md='# Say hello to everyone.\n\nprint ("Hello Python people!")\n\n'
                     '>>age = 18 >> age == 18 True\n\n- >>> print("Hello Python interpreter!")\n\n' + BODY)
    for n in range(1, 6):
        (folder / f'page_{n:04d}.lines.json').write_text(json.dumps([[0.1, 0.1 * i, 0.02, s] for i, s in enumerate(session)]))
    checks = by_name(check_folder(folder))
    assert not checks['code_blocks'].ok and checks['code_blocks'].fix is None
    assert checks['code_blocks'].detail.startswith('3 of 3 code lines outside code blocks')
    assert not checks['layout'].ok and '(5 to read again for code)' in checks['layout'].detail
    assert checks['layout'].fix == ['img2txt.py', str(folder), '--model', 'vision']
    # Read again with code: the listings are fenced and the pages are not asked a second time.
    (folder / 'layout.json').write_text(json.dumps({f'page_{n:04d}': {'code': [[0, 1, 2]]} for n in range(1, 6)}))
    (folder / 'Python Crash Course.md').write_text('```\n# Say hello to everyone.\nprint("Hello Python people!")\n```\n\n'
                                                   '```\n>>> age = 18\n>>> age == 18\nTrue\n```\n\n## Comments\n\n' + BODY)
    checks = by_name(check_folder(folder))
    assert checks['code_blocks'].ok and checks['layout'].ok and checks['headings'].detail.startswith('1 headings')
    # A book without code gets no such check.
    assert 'code_blocks' not in by_name(check_folder(book(tmp_path, [BODY], md='# One\n\n' + BODY)))
