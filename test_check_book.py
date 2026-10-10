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


def book(tmp_path, pages, sources='vision', md=None, name='How to Write a Lot'):
    folder = tmp_path / name
    folder.mkdir()
    for n, text in enumerate(pages, 1):
        (folder / f'page_{n:04d}.png').write_bytes(b'png')
        if text is not None:
            (folder / f'page_{n:04d}.txt').write_text(text)
    (folder / SOURCES).write_text(''.join(f'page_{n:04d}\t{sources}\n' for n in range(1, len(pages) + 1)))
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
    assert not checks['headings'].ok and checks['headings'].fix[:2] == ['img2txt.py', str(folder)] and '--force' in checks['headings'].fix
    assert not checks['proofread'].ok and checks['proofread'].fix == ['proofread.py', str(folder), '--apply', 'dash_as_hyphen']
    assert checks['pages'].ok


def test_decorative_digit_headings_and_gaps_are_caught(tmp_path):
    folder = book(tmp_path, [BODY, BODY2], md='# Z\n\n# Chapter &\n\n' + BODY)
    (folder / 'page_0002.png').rename(folder / 'page_0004.png')
    (folder / 'page_0002.txt').rename(folder / 'page_0004.txt')
    checks = by_name(check_folder(folder, title='How to Write a Lot'))
    assert not checks['decorative_digits'].ok and '2 headings' in checks['decorative_digits'].detail
    assert checks['decorative_digits'].fix == ['img2txt.py', str(folder), '--force', '--model', 'vision', '--title', 'How to Write a Lot']
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
    folder = book(tmp_path, [BODY + 'appliance-a unit.'], md=BODY + 'appliance-a unit.')
    commands = []

    def fake_run(command):
        commands.append(command[1:])
        # The fixes "work": the book gets a heading and the dash is restored.
        (folder / 'page_0001.txt').write_text(BODY + 'appliance—a unit.')
        (folder / f'{folder.name}.md').write_text('# One\n\n' + BODY + 'appliance—a unit.')

    checks, rounds = capture_book.correct(folder, None, runner=fake_run)
    assert [c[0].split('/')[-1] for c in commands] == ['img2txt.py', 'proofread.py']
    assert rounds == 2 and all(c.ok or not c.blocking for c in checks)


def test_correct_gives_up_after_the_round_limit(tmp_path):
    folder = book(tmp_path, [BODY], md=BODY)
    calls = []
    checks, rounds = capture_book.correct(folder, None, rounds=2, runner=lambda c: calls.append(c))
    assert rounds == 2 and len(calls) == 2
    assert [c.name for c in checks if not c.ok and c.blocking] == ['headings']


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
    folder = book(tmp_path, [cover, opening, mid], md='\n\n'.join([cover, opening, mid]))
    checks = by_name(check_folder(folder, title='How to Write a Lot'))
    assert not checks['heading_noise'].ok and checks['heading_noise'].detail.startswith('4 headings')
    assert not checks['chapter_titles'].ok and checks['chapter_titles'].detail.startswith('1 chapter')
    assert not checks['decorative_digits'].ok and '1 chapter references' in checks['decorative_digits'].detail
    assert checks['proofread'].fix[3] == 'decorative_digit'
    assert '--force' in checks['heading_noise'].fix and '--force' in checks['chapter_titles'].fix


def test_real_titles_are_not_noise(tmp_path):
    page = '# 2 Specious Barriers to Writing a Lot\n\n## SPECIOUS BARRIER 1\n\n' + BODY + BODY2
    folder = book(tmp_path, [page], md=page)
    checks = by_name(check_folder(folder))
    assert checks['heading_noise'].ok and checks['chapter_titles'].ok and checks['decorative_digits'].ok
