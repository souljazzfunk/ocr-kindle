import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import book
import cases

HERE = Path(__file__).resolve().parent
FIXTURES = sorted(p for p in (HERE / 'fixtures' / 'cases').iterdir() if p.is_dir())
NOON = datetime(2026, 10, 11, 12, 0, 0).timestamp()
VERSIONS = {'capture': 'cap1', 'convert': 'conv1', 'publish': 'conv1', 'code': 'code1'}


def replay(trace):
    done = subprocess.run(['node', str(HERE / 'scripts' / 'replay_trace.mjs'), str(trace)], capture_output=True,
                          text=True, check=True)
    return done.stdout.splitlines()


def test_there_is_a_case_to_replay():
    assert [p.name for p in FIXTURES][:1] == ['20261011T120000-B0SYNTH001-capture']


@pytest.mark.parametrize('folder', FIXTURES, ids=[p.name for p in FIXTURES])
def test_every_capture_case_replays_through_policy_js_to_the_actions_it_recorded(folder):
    case = cases.load(folder)
    if case.info['stage'] != 'capture' or not case.trace:
        pytest.skip('no capture trace to replay')
    assert replay(case.files['trace.jsonl']) == [line['action'] for line in case.trace]


def test_the_synthetic_case_loads_with_its_record_trace_and_files():
    case = cases.load(HERE / 'fixtures' / 'cases' / '20261011T120000-B0SYNTH001-capture')
    assert (case.info['asin'], case.info['checks'], case.info['kind']) == (
        'B0SYNTH001', ['end_position', 'capture_stop'], 'book')
    assert (len(case.trace), case.trace[0]['action'], case.trace[-1]['action']) == (
        39, 'save', 'fail:the reader stayed on page 5 for 30 turns')
    assert sorted(case.files) == ['capture.json', 'case.json', 'page_0001.png', 'page_0003.png', 'positions.json',
                                  'trace.jsonl']


def test_a_trace_whose_recorded_action_differs_from_policy_js_does_not_replay_to_it(tmp_path):
    lines = (HERE / 'fixtures' / 'cases' / '20261011T120000-B0SYNTH001-capture' / 'trace.jsonl').read_text().splitlines()
    edited = [json.loads(line) for line in lines]
    edited[-1]['action'] = 'done:page 9 of 9'
    trace = tmp_path / 'trace.jsonl'
    trace.write_text(''.join(json.dumps(line) + '\n' for line in edited))
    assert replay(trace)[-1] == 'fail:the reader stayed on page 5 for 30 turns'


@pytest.fixture
def library(tmp_path):
    return book.Library(tmp_path / 'kindle')


def make_book(library, asin='B1', pages=5, image_bytes=b'png', positions=None, report=None):
    b = library.book(asin)
    book.write_json(b.folder / 'book.json', {'asin': asin, 'title': 'One', 'domain': 'read.amazon.co.jp'})
    folder = b.folder / 'pages'
    folder.mkdir(parents=True)
    for i in range(1, pages + 1):
        (folder / f'page_{i:04d}.png').write_bytes(image_bytes)
        (folder / f'page_{i:04d}.txt').write_text(f'text {i}')
    (folder / 'trace.jsonl').write_text('{"observation": {}, "action": "save"}\n')
    (folder / 'capture.json').write_text('{"stopped": ""}')
    (folder / 'dom-probe.json').write_text('{}')
    (folder / 'positions.json').write_text(json.dumps(positions or {f'page_{i:04d}': f'{i}/9' for i in range(1, pages + 1)}))
    if report:
        (folder / 'report.json').write_text(json.dumps(report))
    return b


def case_dirs(library):
    return sorted(p.name for p in (library.root / 'cases').iterdir())


def head():
    return subprocess.run(['git', '-C', str(HERE), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip() or None


def test_a_parked_capture_leaves_a_case_with_its_evidence_and_edge_pages_copied_to_drive(library, tmp_path):
    drive = tmp_path / 'My Drive'
    (drive / 'Kindle_md').mkdir(parents=True)
    positions = {'page_0001': '1/9', 'page_0002': '2/9', 'page_0003': '3/9', 'page_0004': '4/9', 'page_0005': ''}
    b = make_book(library, positions=positions)

    outcome = book.record_failure(library, [b], b, 'capture', ['end_position'], 'last page 4 of 9', attempt=2,
                                  current=VERSIONS, now=NOON, config={'GOOGLE_DRIVE_FOLDER': str(drive / 'Kindle_md')})

    assert outcome == 'parked'
    assert case_dirs(library) == ['20261011T120000-B1-capture']
    case = library.root / 'cases' / '20261011T120000-B1-capture'
    assert sorted(p.name for p in case.iterdir()) == ['capture.json', 'case.json', 'dom-probe.json', 'page_0001.png',
                                                      'page_0004.png', 'page_0005.png', 'positions.json', 'trace.jsonl']
    assert json.loads((case / 'case.json').read_text()) == {
        'id': '20261011T120000-B1-capture', 'asin': 'B1', 'title': 'One', 'domain': 'read.amazon.co.jp',
        'stage': 'capture', 'kind': 'book', 'checks': ['end_position'], 'error': 'last page 4 of 9', 'attempt': 2,
        'versions': VERSIONS, 'commit': head(),
        'files': ['trace.jsonl', 'dom-probe.json', 'capture.json', 'positions.json', 'page_0004.png', 'page_0001.png',
                  'page_0005.png'],
        'skipped': []}
    copied = drive / 'Kindle_cases' / '20261011T120000-B1-capture'
    assert sorted(p.name for p in copied.iterdir()) == sorted(p.name for p in case.iterdir())


def test_a_convert_check_that_names_a_page_puts_that_page_and_its_text_in_the_case(library):
    report = {'checks': [{'name': 'layout', 'ok': False, 'blocking': True,
                          'detail': '3 Vision pages without a layout from Gemini, e.g. page_0003'},
                         {'name': 'headings', 'ok': True, 'blocking': True, 'detail': 'page_0002 has one'}]}
    b = make_book(library, report=report)

    assert book.record_failure(library, [b], b, 'convert', ['layout'], None, current=VERSIONS, now=NOON) == 'parked'

    case = cases.load(library.root / 'cases' / '20261011T120000-B1-convert')
    assert case.info['files'] == ['trace.jsonl', 'dom-probe.json', 'capture.json', 'positions.json', 'report.json',
                                  'page_0003.png', 'page_0003.txt', 'page_0001.png', 'page_0005.png']
    assert case.files['page_0003.txt'].read_text() == 'text 3'


def test_images_that_would_take_a_case_past_5_mb_are_skipped_and_named(library):
    positions = {'page_0001': '1/9', 'page_0002': '2/9', 'page_0003': ''}
    b = make_book(library, pages=3, image_bytes=b'x' * 2_000_000, positions=positions)

    book.record_failure(library, [b], b, 'capture', ['end_position'], None, attempt=2, current=VERSIONS, now=NOON)

    case = cases.load(library.root / 'cases' / '20261011T120000-B1-capture')
    assert (case.info['files'][-2:], case.info['skipped']) == (['page_0002.png', 'page_0001.png'], ['page_0003.png'])
    assert sum(p.stat().st_size for p in case.folder.iterdir()) < 5_000_000


def test_the_same_failure_with_the_same_versions_leaves_one_case_and_new_code_leaves_another(library):
    b = make_book(library)
    book.record_failure(library, [b], b, 'convert', ['headings', 'layout'], 'x', current=VERSIONS, now=NOON)
    book.record_failure(library, [b], b, 'convert', ['layout', 'headings'], 'y', current=VERSIONS, now=NOON + 60)
    assert case_dirs(library) == ['20261011T120000-B1-convert']

    book.record_failure(library, [b], b, 'convert', ['headings', 'layout'], 'x', current={**VERSIONS, 'convert': 'conv2'},
                        now=NOON + 120)
    book.record_failure(library, [b], b, 'convert', ['headings'], 'x', current={**VERSIONS, 'convert': 'conv2'},
                        now=NOON + 180)
    assert case_dirs(library) == ['20261011T120000-B1-convert', '20261011T120200-B1-convert',
                                  '20261011T120300-B1-convert']


def test_a_systemic_pause_leaves_a_systemic_case_even_with_no_pages(library):
    b = library.book('B1')
    book.write_json(b.folder / 'book.json', {'asin': 'B1', 'title': 'One', 'domain': 'read.amazon.com'})

    assert book.record_failure(library, [b], b, 'capture', ['extension_unreachable'], 'no worker asked',
                               current=VERSIONS, now=NOON) == 'paused'

    case = cases.load(library.root / 'cases' / '20261011T120000-B1-capture')
    assert (case.info['kind'], case.info['checks'], case.info['files'], sorted(case.files)) == (
        'systemic', ['extension_unreachable'], [], ['case.json'])


def test_a_breaker_pause_leaves_a_systemic_case_for_the_book_that_tripped_it(library):
    one, two = make_book(library, 'B1'), make_book(library, 'B2')
    assert book.record_failure(library, [one, two], one, 'convert', ['headings'], None, current=VERSIONS, now=NOON) == 'parked'
    assert book.record_failure(library, [one, two], two, 'convert', ['headings'], None, current=VERSIONS,
                               now=NOON + 1) == 'paused'

    assert case_dirs(library) == ['20261011T120000-B1-convert', '20261011T120001-B2-convert']
    assert cases.load(library.root / 'cases' / '20261011T120001-B2-convert').info['kind'] == 'systemic'


def test_a_capture_that_is_tried_again_leaves_no_case_until_it_parks(library):
    b = make_book(library)
    assert book.record_failure(library, [b], b, 'capture', ['pages'], None, attempt=1, current=VERSIONS, now=NOON) == 'retry'
    assert not (library.root / 'cases').exists()
    assert book.record_failure(library, [b], b, 'capture', ['pages'], None, attempt=2, current=VERSIONS, now=NOON) == 'parked'
    assert case_dirs(library) == ['20261011T120000-B1-capture']


def test_publish_case_goes_to_kindle_cases_folder_and_never_raises(library, tmp_path, capsys):
    b = make_book(library)
    case = cases.write_case(b, 'convert', {'checks': ['headings']}, library.root, versions=VERSIONS, now=NOON)

    assert cases.publish_case(case, {}) is None
    assert cases.publish_case(case, {'GOOGLE_DRIVE_FOLDER': str(tmp_path / 'no drive' / 'Kindle_md')}) is None
    target = tmp_path / 'cases here'
    assert cases.publish_case(case, {'KINDLE_CASES_FOLDER': str(target), 'GOOGLE_DRIVE_FOLDER': '/elsewhere'}) == \
        target / '20261011T120000-B1-convert'
    blocked = tmp_path / 'a file'
    blocked.write_text('')
    capsys.readouterr()
    assert cases.publish_case(case, {'KINDLE_CASES_FOLDER': str(blocked)}) is None
    assert capsys.readouterr().out.startswith(f'Case 20261011T120000-B1-convert not copied to {blocked}: FileExistsError')
    assert (case / 'case.json').exists()
