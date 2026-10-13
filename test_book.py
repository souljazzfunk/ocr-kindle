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


def test_a_checks_code_is_the_functions_that_decide_it(tmp_path):
    # A change to one check's function retries that check only. The rest of the file, and a check that names
    # other functions, stay as they were.
    owners = {}
    for pairs in book.TOUCH.values():
        for file, name in pairs:
            owners.setdefault(file, [])
            if name and name not in owners[file]:
                owners[file].append(name)
    for file, names in owners.items():
        path = tmp_path / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(f'def {name}():\n    return 1\n' for name in names) or 'x = 1\n')
    first = book.versions(tmp_path)
    layout = (tmp_path / 'layout.py').read_text().replace('def contents_covered():\n    return 1\n',
                                                          'def contents_covered():\n    return 2\n', 1)
    (tmp_path / 'layout.py').write_text(layout)
    changed = book.versions(tmp_path)
    assert changed['touch']['contents_chapters'] != first['touch']['contents_chapters']
    assert changed['touch']['headings'] == first['touch']['headings']
    assert changed['touch']['gemini'] == first['touch']['gemini']
    check = (tmp_path / 'check_book.py').read_text().replace('def check_folder():\n    return 1\n',
                                                             'def check_folder():\n    return 2\n', 1)
    (tmp_path / 'check_book.py').write_text(check)
    headings = book.versions(tmp_path)
    assert headings['touch']['headings'] != first['touch']['headings']
    assert headings['touch']['contents_chapters'] == changed['touch']['contents_chapters']


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
    assert book.gemini_reset('Layout failed. Resets at 123. 429 PerDay', late) == 123


def test_a_contents_park_is_retried_when_the_rewriting_of_laid_out_pages_changes(tmp_path):
    # 2026-10-13: 近代中国史 stayed parked on 「参考文献」 because img2txt.main did not write its laid-out Vision
    # page again. The fix is in img2txt.main, so that function is part of what decides contents_chapters.
    owners = {}
    for pairs in book.TOUCH.values():
        for file, name in pairs:
            owners.setdefault(file, [])
            if name and name not in owners[file]:
                owners[file].append(name)
    for file, names in owners.items():
        (tmp_path / file).write_text('\n'.join(f'def {name}():\n    return 1\n' for name in names) or 'x = 1\n')
    first = book.versions(tmp_path)
    (tmp_path / 'img2txt.py').write_text((tmp_path / 'img2txt.py').read_text().replace(
        'def main():\n    return 1\n', 'def main():\n    return 2\n', 1))
    changed = book.versions(tmp_path)
    assert changed['touch']['contents_chapters'] != first['touch']['contents_chapters']
    assert changed['touch']['headings'] == first['touch']['headings']


def test_a_contents_park_is_retried_when_proofreading_stops_taking_titles_back(tmp_path):
    # 2026-10-13 11:19 JST: 近代中国史 parked again because proofread's not_a_title made `# 参考文献` plain text. A
    # change there must retry the book parked on contents_chapters, as a change in img2txt.main does.
    owners = {}
    for pairs in book.TOUCH.values():
        for file, name in pairs:
            owners.setdefault(file, [])
            if name and name not in owners[file]:
                owners[file].append(name)
    for file, names in owners.items():
        (tmp_path / file).write_text('\n'.join(f'def {name}():\n    return 1\n' for name in names) or 'x = 1\n')
    first = book.versions(tmp_path)
    (tmp_path / 'proofread.py').write_text((tmp_path / 'proofread.py').read_text().replace(
        'def not_a_title():\n    return 1\n', 'def not_a_title():\n    return 2\n', 1))
    assert book.versions(tmp_path)['touch']['contents_chapters'] != first['touch']['contents_chapters']
