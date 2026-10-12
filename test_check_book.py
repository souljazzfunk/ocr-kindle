import json
import os
import subprocess
import sys
from pathlib import Path

import capture_book
from check_book import Check, check_folder
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


def test_a_book_that_failed_its_checks_is_not_copied_to_drive(tmp_path, monkeypatch):
    # 2026-10-11: _convert_folder called publish() whatever the report said, so a failed book replaced its copy on Drive.
    drive = tmp_path / 'Kindle_md'
    drive.mkdir()
    folder = tmp_path / 'Book'
    folder.mkdir()
    (folder / 'Book.md').write_text('# One\n\n' + BODY, encoding='utf-8')
    monkeypatch.setattr(capture_book, 'load_config', lambda: {'GOOGLE_DRIVE_FOLDER': str(drive)})
    monkeypatch.setattr(capture_book, 'run', lambda *a, **k: None)
    monkeypatch.setattr(capture_book, 'correct', lambda f, t: ([Check('headings', False, '0 headings')], 1))
    monkeypatch.setattr(capture_book, 'start_wiki_ingest', lambda data: None)
    data = capture_book.convert('B0', 'Book', source=folder)
    assert data['blocking_failures'] == ['headings']
    assert not list(drive.iterdir())
    monkeypatch.setattr(capture_book, 'correct', lambda f, t: ([Check('headings', True, '1 heading')], 1))
    assert capture_book.convert('B0', 'Book', source=folder)['blocking_failures'] == []
    assert [p.name for p in drive.iterdir()] == ['Book.md']


def test_a_book_converted_by_hand_also_leaves_its_report_on_drive(tmp_path, monkeypatch):
    # 2026-10-12: 英語教育幻想, converted with capture_book.py, reached Drive with no report for the cloud to read.
    drive = tmp_path / 'Kindle_md'
    drive.mkdir()
    folder = tmp_path / 'Book'
    folder.mkdir()
    (folder / 'Book.md').write_text('# One\n\n' + BODY, encoding='utf-8')
    monkeypatch.setattr(capture_book, 'load_config', lambda: {'GOOGLE_DRIVE_FOLDER': str(drive)})
    monkeypatch.setattr(capture_book, 'run', lambda *a, **k: None)
    monkeypatch.setattr(capture_book, 'start_wiki_ingest', lambda data: None)
    monkeypatch.setattr(capture_book, 'correct', lambda f, t: ([Check('headings', False, '0 headings')], 1))
    capture_book.convert('B0', 'Book', source=folder)
    assert not (tmp_path / 'Kindle_reports').exists()
    monkeypatch.setattr(capture_book, 'correct', lambda f, t: ([Check('headings', True, '1 heading')], 1))
    capture_book.convert('B0', 'Book', source=folder)
    copied = json.loads((tmp_path / 'Kindle_reports' / 'B0.json').read_text(encoding='utf-8'))
    assert (copied['asin'], copied['title'], copied['publish'], copied['report']['blocking_failures']) == \
        ('B0', 'Book', None, [])


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


def offer(monkeypatch, result, seen=None, version=None):
    """capture_book.convert with the channel answering result for the capture (made by extension version, if given)."""
    from channel import CaptureResult
    monkeypatch.setattr(capture_book, 'folder_problem', lambda: None)

    def capture(asin, domain, folder, recapture):
        if seen is not None:
            seen.append((asin, domain, folder, recapture))
        folder.mkdir(parents=True, exist_ok=True)
        if result[0] == 'done':
            (folder / 'page_0001.png').write_bytes(b'png')
        if version:
            (folder / 'capture.json').write_text(json.dumps({'extension_version': version, 'stopped': ''}))
        return CaptureResult(*result)

    monkeypatch.setattr(capture_book, 'channel', lambda: type('C', (), {'capture': staticmethod(capture)})())


def test_a_refused_capture_fails_with_the_extensions_reason(tmp_path, monkeypatch):
    offer(monkeypatch, ('refused', 'could not page back past page 3 of 4'))
    data = capture_book.convert('B07VSXS4NK', 'Automate', downloads=tmp_path)
    assert data['blocking_failures'] == ['capture_refused'] and 'page 3 of 4' in data['error']


def test_a_book_cloud_reader_cannot_open_is_the_book_s_failure_not_the_browser_s(tmp_path, monkeypatch):
    # 2026-10-11: For 91 Days In Tokyo opened to "Kindle App Is Required" and the list waited an hour.
    offer(monkeypatch, ('refused', 'kindle app required: Cloud Reader cannot open this book'))
    data = capture_book.convert('B00S1MW0KM', 'For 91 Days In Tokyo', downloads=tmp_path)
    assert data['blocking_failures'] == ['kindle_app_only']
    offer(monkeypatch, ('refused', 'signed out of Cloud Reader'))
    assert capture_book.convert('B00S1MW0KM', 'For 91 Days In Tokyo', downloads=tmp_path)['blocking_failures'] == ['signed_out']


def test_a_book_the_reader_opens_as_its_sample_fails_as_sample_only(tmp_path, monkeypatch):
    # 2026-10-12: read.amazon.co.jp redirected the purchased 英語教育幻想 to /sample/B07G4SH6BV.
    offer(monkeypatch, ('refused', 'sample: the reader showed the sample, not the purchased book (read.amazon.co.jp)'))
    data = capture_book.convert('B07G4SH6BV', '英語教育幻想', downloads=tmp_path)
    assert data['blocking_failures'] == ['sample_only'] and 'read.amazon.co.jp' in data['error']


def test_a_stalled_or_unreached_capture_stops_with_its_own_check(tmp_path, monkeypatch):
    offer(monkeypatch, ('stalled', 'no page and no event for 5 minutes after 41 screens'))
    data = capture_book.convert('B07J4521M3', 'Python Crash Course', downloads=tmp_path)
    assert data['blocking_failures'] == ['capture_stall'] and '41 screens' in data['error']
    offer(monkeypatch, ('unreachable', 'no worker asked for work in 90 s'))
    data = capture_book.convert('B07J4521M3', 'Python Crash Course', downloads=tmp_path)
    assert data['blocking_failures'] == ['extension_unreachable']


def test_a_capture_lands_in_a_folder_named_by_the_list_title_and_its_version_is_checked(tmp_path, monkeypatch):
    seen, ran = [], []
    offer(monkeypatch, ('done', '', 'Zero to Sold_ How to Start', 3), seen)
    monkeypatch.setattr(capture_book, 'run', lambda command, **k: ran.append(command))
    monkeypatch.setattr(capture_book, 'correct', lambda f, t: ([], 1))
    monkeypatch.setattr(capture_book, 'start_wiki_ingest', lambda data: None)
    monkeypatch.setattr(capture_book, 'publish', lambda folder, title: None)
    data = capture_book.convert('B0ZERO', 'Zero to Sold: How to Start/Run', downloads=tmp_path)
    assert seen == [('B0ZERO', 'read.amazon.com', tmp_path / 'Zero to Sold_ How to Start_Run', False)]
    assert data['blocking_failures'] == ['extension_version'] and ran == []  # the fake capture wrote no capture.json
    # A capture by another version is set aside, never converted or mixed with the next capture.
    assert [p.name.split(' (before')[0] for p in tmp_path.iterdir()] == ['Zero to Sold_ How to Start_Run']
    assert not (tmp_path / 'Zero to Sold_ How to Start_Run').exists()
    # With no title from the list, the reader's title names the Markdown; the folder keeps the ASIN.
    offer(monkeypatch, ('done', '', 'Zero to Sold_ How to Start', 3), seen, version=capture_book.repo_version())
    capture_book.convert('B0ZERO', None, downloads=tmp_path)
    assert seen[-1][2] == tmp_path / 'B0ZERO' and ran[-1][-2:] == ['--title', 'Zero to Sold_ How to Start']


def test_a_page_vision_finds_no_lines_on_is_read_by_live_text(tmp_path):
    # 2026-10-12: Vision returned 0 lines on both vertical pages of 英語教育幻想; Live Text read them.
    import img2txt
    page = tmp_path / 'page_0001.png'
    page.write_bytes(b'')
    assert img2txt.vision_transcribe(page, read_lines=lambda image: [], read_livetext=lambda image: '英語教育は') == (
        '英語教育は', 'livetext')
    try:
        img2txt.vision_transcribe(page, read_lines=lambda image: [], read_livetext=lambda image: '')
        raise AssertionError('an empty page was marked done')
    except RuntimeError as e:
        assert str(e) == img2txt.NO_TEXT
    assert img2txt.vision_transcribe(page, read_lines=lambda image: [(0.1, 0.1, 0.02, 'Hello world')],
                                     read_livetext=lambda image: 'unused')[1] == 'vision'


def test_a_vertical_page_vision_reads_only_a_few_pieces_of_is_read_by_live_text(tmp_path):
    # 2026-10-12: on 英語教育幻想's page 863 Vision read one 「ジ」, on page 2 only the English quote above the vertical
    # body; both pages went out as those few characters, and the proofreader then blanked the English one.
    import img2txt
    page = tmp_path / 'page_0011.png'
    page.write_bytes(b'')
    body = '†日米同盟\n\nここからは私見です。この文書で英語の位置がどう語られているかを見ていきます。' * 3
    assert img2txt.vision_transcribe(page, read_lines=lambda image: [(0.5, 0.4, 0.02, 'ジ')],
                                     read_livetext=lambda image: body) == (body, 'livetext')
    quote = 'The English language is a tool for communication with the world.'
    assert img2txt.vision_transcribe(page, read_lines=lambda image: [(0.1, 0.1, 0.02, quote)],
                                     read_livetext=lambda image: quote + '\n\n' + body) == (quote + '\n\n' + body, 'livetext')


def test_a_full_english_page_is_not_sent_to_live_text(tmp_path):
    import img2txt
    page = tmp_path / 'page_0001.png'
    page.write_bytes(b'')
    line = 'The group became known as the Homebrew Computer Club, and it met in a garage in Menlo Park.'
    boxes = [(0.1, 0.05 * k, 0.02, line) for k in range(1, 8)]
    assert img2txt.vision_transcribe(page, read_lines=lambda image: boxes,
                                     read_livetext=lambda image: (_ for _ in ()).throw(AssertionError('Live Text read a full page')))[1] == 'vision'


def test_live_text_failures_carry_its_message():
    import subprocess
    import img2txt
    failed = lambda args, **kw: subprocess.CompletedProcess(args, 1, '', 'cannot read page.png')
    try:
        img2txt.livetext('page.png', run=failed, binary='livetext')
        raise AssertionError('no error')
    except RuntimeError as e:
        assert str(e) == 'livetext: cannot read page.png'


def test_the_live_text_tool_builds_on_a_mac(tmp_path):
    import shutil
    import sys
    import pytest
    import img2txt
    if sys.platform != 'darwin' or not shutil.which('swiftc'):
        pytest.skip('Live Text needs macOS and swiftc')
    source = tmp_path / 'livetext.swift'
    source.write_text(img2txt.LIVETEXT_SOURCE.read_text())
    assert img2txt.livetext_binary(source) == tmp_path / 'livetext' and (tmp_path / 'livetext').exists()


def test_a_chapter_the_contents_lists_without_a_heading_is_caught(tmp_path):
    # 2026-10-12: 英語教育幻想 passed every check with 幻想5 and 幻想6 left as paragraphs.
    contents = '# 目次\n\n幻想5 それぞれの国の文化や言語には独特さがある\n\n幻想6 英語ができれば世界中だれとでも意思疎通できる'
    chapter5 = '# 幻想5 それぞれの国の文化や言語には独特さがある\n\nここからは私見です。'
    chapter6 = '幻想6 英語ができれば世界中だれとでも意思疎通できる\n\nここからは私見です。'
    md = '\n\n'.join([contents, chapter5, chapter6])
    folder = book(tmp_path, [contents, chapter5, chapter6], sources='livetext', md=md, name='英語教育幻想')
    check = by_name(check_folder(folder))['contents_chapters']
    assert not check.ok and check.blocking and check.detail == '1 contents entries without a heading: "幻想英語ができれば世界中だれとでも意思疎通できる"'
    (folder / '英語教育幻想.md').write_text(md.replace('\n\n幻想6 英語', '\n\n# 幻想6 英語'))
    assert by_name(check_folder(folder))['contents_chapters'].ok


def test_contents_lines_read_as_one_and_chapter_numerals_misread_still_find_their_headings(tmp_path):
    # 2026-10-12: 代替医療解剖 and 近代中国史 were parked by contents_chapters with every heading in place: Live Text
    # read several contents lines as one ("中国という世界黄河と黄土長江と江南", the last cut mid-title) and 第Ⅲ章 as 第山章.
    contents = '# 目次\n\n第山章ホメオパシーの真実\n\n中国という世界黄河と黄土長江と江\n\n第i章鍼の真実\n\n人口の中国史'
    body = ['# 第Ⅲ章 ホメオパシーの真実\n\nここからは本文です。', '## 中国という世界\n\nここからは本文です。',
            '## 黄河と黄土\n\nここからは本文です。', '## 長江と江南\n\nここからは本文です。',
            '# 第Ⅰ章 鍼の真実\n\nここからは本文です。', '人口の中国史\n\nここからは本文です。']
    md = '\n\n'.join([contents, *body])
    folder = book(tmp_path, [contents, *body], sources='livetext', md=md, name='近代中国史')
    check = by_name(check_folder(folder))['contents_chapters']
    assert not check.ok and check.detail == '1 contents entries without a heading: "人口の中国史"'
    (folder / '近代中国史.md').write_text(md.replace('\n\n人口の中国史\n\nここから', '\n\n## 人口の中国史\n\nここから'))
    assert by_name(check_folder(folder))['contents_chapters'].ok


def test_a_number_over_a_japanese_sentence_is_not_a_chapter_opening(tmp_path):
    # 2026-10-12: 代替医療解剖 was parked for "2 / 「王様は裸だ」と言うことを許されない。それを言えば…": a title holds no 。.
    body = '本文です。' * 30
    md = f'# 第1章\n\n{body}\n\n2\n\n「王様は裸だ」と言うことを許されない。それを言えば、効果が台無しになる。\n\n{body}'
    folder = book(tmp_path, [md], sources='livetext', md=md, name='代替医療解剖')
    assert by_name(check_folder(folder))['chapter_titles'].ok


def test_each_step_is_timed_in_the_report_and_a_slow_one_fails_the_speed_check(tmp_path, monkeypatch):
    # 2026-10-12: the redesign shipped with no step timed, so whether a book got faster could not be answered.
    drive = tmp_path / 'Kindle_md'
    drive.mkdir()
    folder = tmp_path / 'Book'
    folder.mkdir()
    for n in range(1, 11):
        (folder / f'page_{n:04d}.png').write_bytes(b'')
    (folder / 'Book.md').write_text('# One\n\n' + BODY, encoding='utf-8')
    (folder / 'capture.json').write_text(json.dumps({'seconds': {'reach': 3, 'capture': 40, 'screens': 10}}))
    clock = iter([100.0, 100.0, 130.0, 131.0, 140.0])
    monkeypatch.setattr(capture_book.time, 'time', lambda: next(clock))
    monkeypatch.setattr(capture_book, 'load_config', lambda: {'GOOGLE_DRIVE_FOLDER': str(drive)})
    monkeypatch.setattr(capture_book, 'run', lambda *a, **k: None)
    monkeypatch.setattr(capture_book, 'correct', lambda f, t: ([Check('headings', True, '1 headings')], 1))
    monkeypatch.setattr(capture_book, 'start_wiki_ingest', lambda data: None)
    data = capture_book.convert('B0', 'Book', source=folder)
    report = json.loads((folder / 'report.json').read_text())
    assert report['seconds'] == {'reach': 3, 'capture': 40, 'screens': 10, 'pages': 10, 'ocr': 30.0, 'checks': 1.0}
    speed = by_name(Check(**c) for c in report['checks'])['speed']
    assert not speed.ok and speed.detail == 'slow: capture_per_screen 4.00 s > 1 s; ocr_per_page 3.00 s > 1 s'
    # A slow book is still a correct one: it goes to Drive, with speed among its warnings.
    assert data['warnings'] == ['speed'] and (drive / 'Book.md').exists()
    assert capture_book.speed({'reach': 3, 'capture': 10, 'screens': 10, 'pages': 10, 'ocr': 5, 'checks': 1}).ok
