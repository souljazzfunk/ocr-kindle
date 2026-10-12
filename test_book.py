import json
from datetime import datetime
from zoneinfo import ZoneInfo

import book


def test_both_lists_make_one_ordered_list_with_each_book_once(tmp_path):
    (tmp_path / 'books.tsv').write_text('# asin\ttitle\nB1\tOne\n\nB2\t二\tread.amazon.co.jp\n')
    (tmp_path / 'library-read.amazon.co.jp.json').write_text(json.dumps(
        {'books': [{'asin': 'B3', 'title': ' Three '}, {'asin': 'B1', 'title': 'Other'}, {'title': 'no asin'},
                   {'asin': 'B4', 'title': 'Sample', 'resourceType': 'EBOOK_SAMPLE'}, {'asin': 'B5', 'manga': True}]}))
    assert book.read_lists([tmp_path / 'books.tsv', tmp_path / 'library-read.amazon.co.jp.json', tmp_path / 'gone.tsv']) == [
        ('B1', 'One', 'read.amazon.com'), ('B2', '二', 'read.amazon.co.jp'), ('B3', 'Three', 'read.amazon.com')]


def test_a_book_folder_goes_from_ready_to_done_through_its_stages(tmp_path):
    (tmp_path / 'books.tsv').write_text('B1\tOne\n')
    library = book.Library(tmp_path / 'kindle')
    [one] = library.add([tmp_path / 'books.tsv'])
    current = {'capture': 'c1', 'convert': 'v1', 'publish': 'v1'}
    assert one.state(current) == book.Ready('capture') and one.title == 'One' and one.domain == 'read.amazon.com'
    captured = one.succeed('capture', {'pages': str(one.folder / 'pages'), 'attempt': 1, 'version': 'c1'})
    converted = one.succeed('convert', {'version': 'v1'}, upstream=captured['at'])
    assert one.state(current) == book.Ready('publish')
    one.succeed('publish', {'version': 'v1'}, upstream=converted['at'])
    assert one.state(current) == book.Done()
    # A new capture makes the conversion of the old one stale, whatever its version.
    one.succeed('capture', {'pages': str(one.folder / 'pages'), 'attempt': 2, 'version': 'c1'})
    assert one.state(current) == book.Ready('convert')
    assert sorted(p.name for p in one.folder.iterdir()) == ['book.json', 'stage.capture.json', 'stage.convert.json',
                                                            'stage.publish.json']


def test_a_stage_keeps_its_record_or_its_failure_never_both(tmp_path):
    one = book.Book(tmp_path / 'B1')
    one.succeed('capture', {'version': 'c1'})
    one.fail('capture', {'checks': ['capture_refused'], 'version': 'c1', 'retry': False})
    assert one.record('capture') is None and one.failure('capture')['checks'] == ['capture_refused']
    one.succeed('capture', {'version': 'c1'})
    assert one.failure('capture') is None and one.record('capture')['version'] == 'c1'
    assert not list(tmp_path.glob('B1/.*.tmp'))


def test_the_stage_version_follows_its_own_code_and_not_the_tests_or_another_stage(tmp_path):
    (tmp_path / 'extension').mkdir()
    for name in ('channel.py', 'extension/job.js', 'extension/job.test.js', 'test_img2txt.py', 'capture_book.py',
                 *book.CONVERTER):
        (tmp_path / name).write_text('a = 1\n')
    first = book.versions(tmp_path)
    assert first['convert'] == first['publish'] != first['capture']
    for name in ('extension/job.test.js', 'test_img2txt.py', 'capture_book.py'):
        (tmp_path / name).write_text('a = 2\n')
    stages = lambda: {k: v for k, v in book.versions(tmp_path).items() if k != 'code'}
    assert stages() == {k: v for k, v in first.items() if k != 'code'}
    assert book.versions(tmp_path)['code'] != first['code']  # capture_book.py is code, in no stage
    (tmp_path / 'layout.py').write_text('a = 2\n')
    assert book.versions(tmp_path)['convert'] != first['convert'] and book.versions(tmp_path)['capture'] == first['capture']
    (tmp_path / 'extension' / 'job.js').write_text('a = 2\n')
    assert book.versions(tmp_path)['capture'] != first['capture']


def test_a_failure_naming_several_checks_takes_the_strictest_rule():
    assert book.classify(['pages', 'start_position']).recapture
    assert not book.classify(['pages', 'headings']).recapture  # the fixes ran; another capture would not help
    assert book.classify(['headings', 'gemini']).kind == book.SYSTEMIC
    assert book.classify(['kindle_app_only']).final and not book.classify(['kindle_app_only', 'headings']).final


def test_gemini_waits_for_its_daily_reset_at_midnight_pacific_and_other_errors_half_an_hour():
    pacific = ZoneInfo('America/Los_Angeles')
    late = datetime(2026, 10, 11, 23, 30, tzinfo=pacific).timestamp()
    assert book.gemini_reset('429 RESOURCE_EXHAUSTED PerDay', late) == datetime(2026, 10, 12, tzinfo=pacific).timestamp()
    assert book.gemini_reset('connection reset', late) == late + 1800
