import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import book
import capture_book
import kc_run

NOON = datetime(2026, 10, 11, 12, 0, tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()


class Mac:
    """The extension and the converter, answering from scripts: per ASIN, the failing checks of each call in turn
    (no entry, or [], is a clean result)."""

    def __init__(self, monkeypatch):
        self.captures, self.converts, self.errors, self.log = {}, {}, {}, []
        monkeypatch.setattr(capture_book, 'capture', self.capture)
        monkeypatch.setattr(capture_book, 'convert_folder', self.convert_folder)
        monkeypatch.setattr(capture_book, 'publish', lambda folder, title, replaces=None: self.log.append(('publish', title)))
        monkeypatch.setattr(capture_book, 'start_wiki_ingest', lambda data: None)

    def capture(self, asin, title, domain, folder, recapture=False):
        self.log.append(('capture', asin, recapture))
        answers = self.captures.get(asin, [])
        checks = answers.pop(0) if answers else []
        if checks:
            return None, {'blocking_failures': checks, 'error': self.errors.get(asin, f'{checks[0]} on {asin}')}
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'page_0001.png').write_bytes(b'png')
        return title, None

    def convert_folder(self, asin, title, model, source, recapture=False):
        self.log.append(('convert', asin, Path(source)))
        answers = self.converts.get(asin, [])
        checks = answers.pop(0) if answers else []
        if checks == 'crash':
            raise RuntimeError('img2txt.py died')
        data = {'blocking_failures': checks, 'folder': str(source), 'error': self.errors.get(asin),
                'checks': [{'name': c, 'ok': False, 'blocking': True, 'detail': f'{c} on {asin}'} for c in checks]}
        (Path(source) / 'report.json').write_text(json.dumps(data))
        return data


@pytest.fixture
def mac(monkeypatch):
    return Mac(monkeypatch)


@pytest.fixture
def home(tmp_path):
    """A checkout with the stages' sources, and a list of four books."""
    checkout = tmp_path / 'checkout'
    (checkout / 'extension').mkdir(parents=True)
    for name in ('channel.py', 'extension/background.js', 'capture_book.py', *book.CONVERTER):
        (checkout / name).write_text(f'# {name}\n')
    (tmp_path / 'books.tsv').write_text('# asin\ttitle\nB1\tOne\nB2\tTwo\nB3\tThree\nB4\tFour\n')
    return tmp_path


def step(home, job, now=NOON, lists=None, config=None):
    return kc_run.step(job, root=home / 'kindle', lists=lists or [home / 'books.tsv'], checkout=home / 'checkout',
                       sleep=lambda s: None, now=lambda: now, config=config or {})


def state(home, asin):
    return book.Library(home / 'kindle').book(asin).state(book.versions(home / 'checkout'))


def edit(home, name):
    path = home / 'checkout' / name
    path.write_text(path.read_text() + '# fixed\n')


def test_each_step_runs_one_stage_of_the_next_ready_book_in_list_order(home, mac):
    library = home / 'library-read.amazon.co.jp.json'
    library.write_text(json.dumps({'books': [{'asin': 'B5', 'title': 'Five'}, {'asin': 'B1', 'title': 'Again'}]}))
    lists = [home / 'books.tsv', library]
    assert [step(home, 'capture', lists=lists) for _ in range(3)] == [
        'captured: B1', 'captured: B2', 'waiting for the convert job']
    assert json.loads((home / 'kindle' / 'B5' / 'book.json').read_text()) == {
        'asin': 'B5', 'title': 'Five', 'domain': 'read.amazon.com'}
    assert [step(home, 'convert', lists=lists) for _ in range(3)] == ['converted: B1', 'published: B1', 'converted: B2']
    assert mac.log[-3:] == [('convert', 'B1', home / 'kindle' / 'B1' / 'pages'), ('publish', 'One'),
                            ('convert', 'B2', home / 'kindle' / 'B2' / 'pages')]
    assert state(home, 'B1') == book.Done() and state(home, 'B2') == book.Ready('publish')


def test_a_book_that_fails_its_checks_is_parked_and_the_list_goes_on(home, mac):
    # 2026-10-11: one stall stopped the whole list from 03:11 until 再開して at 11:16.
    mac.converts['B1'] = [['headings']]
    step(home, 'capture'), step(home, 'capture')
    assert step(home, 'convert') == 'parked: B1'
    assert step(home, 'convert') == 'converted: B2'
    parked = state(home, 'B1')
    assert parked.stage == 'convert' and parked.failure['checks'] == ['headings']
    assert parked.failure['error'] == 'headings: headings on B1'


def test_a_parked_book_leaves_a_case_on_drive_with_its_report_and_pages(home, mac):
    mac.converts['B1'] = [['headings']]
    step(home, 'capture')
    assert step(home, 'convert', config={'KINDLE_CASES_FOLDER': str(home / 'Kindle_cases')}) == 'parked: B1'
    copied = sorted((home / 'Kindle_cases').iterdir())
    assert [p.name for p in copied] == [f'{datetime.fromtimestamp(NOON).strftime("%Y%m%dT%H%M%S")}-B1-convert']
    assert sorted(p.name for p in copied[0].iterdir()) == ['case.json', 'page_0001.png', 'report.json']
    assert json.loads((copied[0] / 'case.json').read_text())['checks'] == ['headings']


def test_an_unusable_capture_is_captured_once_more_then_the_book_is_parked(home, mac):
    mac.converts['B1'] = [['start_position'], ['start_position', 'pages']]
    assert step(home, 'capture') == 'captured: B1'
    assert step(home, 'convert') == 'retry: B1'
    assert state(home, 'B1') == book.Ready('capture', recapture=True)
    assert step(home, 'capture') == 'captured: B1'
    assert mac.log[-1] == ('capture', 'B1', True)
    assert step(home, 'convert') == 'parked: B1'
    assert step(home, 'capture') == 'captured: B2'
    assert [entry[:2] for entry in mac.log].count(('capture', 'B1')) == 2


def test_a_stalled_capture_is_tried_once_more_then_parked(home, mac):
    mac.captures['B1'] = [['capture_stall'], ['capture_stall']]
    assert [step(home, 'capture') for _ in range(3)] == ['retry: B1', 'parked: B1', 'captured: B2']
    assert mac.log[:2] == [('capture', 'B1', False), ('capture', 'B1', True)]


def test_the_same_check_on_two_books_in_a_row_pauses_the_job_until_its_code_changes(home, mac):
    mac.converts.update({'B1': [['headings']], 'B2': [['headings']]})
    for _ in range(3):
        step(home, 'capture')
    assert step(home, 'convert') == 'parked: B1'
    assert step(home, 'convert') == 'paused: B2'
    assert step(home, 'convert') == 'paused: headings failed on B1 and B2 in a row; waiting for new convert code'
    assert [e for e in mac.log if e[0] == 'convert'] == [('convert', 'B1', home / 'kindle' / 'B1' / 'pages'),
                                                         ('convert', 'B2', home / 'kindle' / 'B2' / 'pages')]
    # A fix to the converter resumes the job, and the books it parked are converted again first, in list order.
    edit(home, 'check_book.py')
    assert step(home, 'convert') == 'converted: B1'
    assert step(home, 'convert') == 'published: B1'
    assert step(home, 'convert') == 'converted: B2'


def test_a_convert_pause_left_by_capture_failures_under_older_rules_is_lifted_on_the_next_step(home, mac):
    # 2026-10-12 14:38: capture_stop and start_position on two books paused converting until new convert code, though
    # the rules now blame the capture; five captured books waited behind it.
    for _ in range(2):
        step(home, 'capture')
    book.write_json(home / 'kindle' / 'pause.convert.json', {
        'stage': 'convert', 'checks': ['capture_stop', 'start_position'], 'book': 'B2', 'waits_on': 'convert',
        'version': book.versions(home / 'checkout')['convert'], 'until': None, 'at': NOON,
        'reason': 'capture_stop, start_position failed on B1 and B2 in a row; waiting for new convert code'})
    assert step(home, 'convert') == 'converted: B1'
    assert not (home / 'kindle' / 'pause.convert.json').exists()


def test_contents_chapters_on_two_books_in_a_row_parks_each_book_and_the_convert_job_goes_on(home, mac):
    # 2026-10-14: contents_chapters paused converting three times in two days (F149 F150), though its failures were
    # each book's own: The History of Jazz's capture ended before Acknowledgments and Snow Crash's chapter headings
    # were laid out as "T E R". Race and every other book waited behind the pause.
    mac.converts.update({'B1': [['contents_chapters']], 'B2': [['contents_chapters']]})
    for _ in range(2):
        step(home, 'capture')
    assert [step(home, 'convert') for _ in range(2)] == ['parked: B1', 'parked: B2']
    assert step(home, 'capture') == 'captured: B3'
    assert step(home, 'convert') == 'converted: B3'
    # A pause the older rules left for it is lifted on the next step.
    book.write_json(home / 'kindle' / 'pause.convert.json', {
        'stage': 'convert', 'checks': ['contents_chapters'], 'book': 'B2', 'waits_on': 'convert',
        'version': book.versions(home / 'checkout')['convert'], 'until': None, 'at': NOON,
        'reason': 'contents_chapters failed on B1 and B2 in a row; waiting for new convert code'})
    assert step(home, 'convert') == 'published: B3'
    assert not (home / 'kindle' / 'pause.convert.json').exists()
    # The parked books are converted again once the contents check changes.
    edit(home, 'layout.py')
    assert step(home, 'convert') == 'converted: B1'


def test_kindle_app_only_parks_only_that_book_and_new_extension_code_does_not_retry_it(home, mac):
    mac.captures.update({'B1': [['kindle_app_only']], 'B2': [['kindle_app_only']]})
    # Two app-only books in a row are two books, not a broken browser: the job goes on.
    assert [step(home, 'capture') for _ in range(3)] == ['parked: B1', 'parked: B2', 'captured: B3']
    edit(home, 'extension/background.js')
    assert step(home, 'capture') == 'captured: B4'
    assert state(home, 'B1').failure['checks'] == ['kindle_app_only']


def test_a_book_parked_on_a_capture_is_tried_again_once_the_extension_changes(home, mac):
    mac.captures['B1'] = [['capture_refused']]
    assert step(home, 'capture') == 'parked: B1'
    assert step(home, 'capture') == 'captured: B2'
    edit(home, 'channel.py')
    assert step(home, 'capture') == 'captured: B1'


def test_an_unreachable_extension_pauses_captures_for_every_book_and_resumes_by_itself(home, mac):
    mac.captures['B1'] = [['extension_unreachable']]
    assert step(home, 'capture') == 'paused: B1'
    assert step(home, 'capture', now=NOON + 60) == 'paused: extension_unreachable on B1'
    assert step(home, 'capture', now=NOON + 601) == 'captured: B1'
    assert [e[:2] for e in mac.log] == [('capture', 'B1'), ('capture', 'B1')]
    assert state(home, 'B1') == book.Ready('convert')  # a systemic failure parks no book


def test_a_gemini_400_waits_for_new_code(home, mac):
    # 2026-10-12 19:09 JST, B005J3IEZQ: response_schema carried additional_properties, every layout request
    # returned 400 INVALID_ARGUMENT, and the convert job paused. Half an hour later the same request would fail
    # again; "400 pages left" in some other error is not this. The book stays ready, and new layout.py resumes it.
    assert book.gemini_reset('Layout failed with 400 pages left: 503 UNAVAILABLE', NOON) == NOON + 1800
    reason = ('Layout failed with 459 pages left: Sonnet: claude -p exited 1; Gemini: 400 INVALID_ARGUMENT '
              'Unknown name "additional_properties"')
    assert book.gemini_reset(reason, NOON) is None
    mac.converts['B1'] = [['gemini']]
    mac.errors['B1'] = reason
    step(home, 'capture')
    assert step(home, 'convert') == 'paused: B1'
    assert state(home, 'B1') == book.Ready('convert')
    pause = json.loads((home / 'kindle' / 'pause.convert.json').read_text())
    assert pause['until'] is None and pause['checks'] == ['gemini'] and pause['waits_on'] == 'convert'
    assert step(home, 'convert', now=NOON + 1800).startswith('paused:')
    edit(home, 'layout.py')
    assert step(home, 'convert') == 'converted: B1'


def test_a_gemini_quota_failure_waits_for_the_reset_and_converts_the_same_pages_with_no_new_capture(home, mac):
    mac.converts['B1'] = [['gemini']]
    mac.errors['B1'] = 'Gemini layout failed with 10 pages left: 429 PerDay'
    step(home, 'capture')
    assert step(home, 'convert') == 'paused: B1'
    reset = datetime(2026, 10, 12, 0, 0, tzinfo=ZoneInfo('America/Los_Angeles')).timestamp()
    assert json.loads((home / 'kindle' / 'pause.convert.json').read_text())['until'] == reset
    assert step(home, 'convert', now=reset - 1).startswith('paused: Gemini layout failed')
    assert step(home, 'capture') == 'captured: B2'  # captures go on while conversion waits
    assert step(home, 'convert', now=reset) == 'converted: B1'
    assert [e for e in mac.log if e[1] == 'B1'] == [('capture', 'B1', False), ('convert', 'B1', home / 'kindle' / 'B1' / 'pages'),
                                                    ('convert', 'B1', home / 'kindle' / 'B1' / 'pages')]


def test_a_passed_book_stays_done_when_the_converter_changes_and_a_capture_is_not_redone(home, mac):
    # A change to the converter used to make every passed book Ready again. B1 has passed; it stays done, and the
    # capture it already has is not taken again when the extension changes either.
    step(home, 'capture'), step(home, 'convert'), step(home, 'convert')
    assert state(home, 'B1') == book.Done()
    edit(home, 'img2txt.py')
    edit(home, 'layout.py')
    edit(home, 'check_book.py')
    edit(home, 'proofread.py')
    assert state(home, 'B1') == book.Done()
    assert step(home, 'convert') == 'nothing to do'
    assert mac.log.count(('publish', 'One')) == 1
    edit(home, 'channel.py')
    assert state(home, 'B1') == book.Done()
    assert step(home, 'capture') == 'captured: B2'


def test_a_crash_parks_the_book_with_its_reason(home, mac):
    mac.converts['B1'] = ['crash']
    step(home, 'capture')
    assert step(home, 'convert') == 'parked: B1'
    assert state(home, 'B1').failure['error'] == 'RuntimeError: img2txt.py died'
    # The exception may come from any module, so a fix to one outside the converter retries the book too.
    edit(home, 'capture_book.py')
    assert step(home, 'convert') == 'converted: B1'


def test_the_convert_job_makes_no_book_folders(home, mac):
    assert step(home, 'convert') == 'nothing to do'
    assert not (home / 'kindle' / 'B1').exists()


def test_books_progress_json_is_carried_over_once_and_stale_results_are_redone(home, mac):
    downloads = home / 'Downloads'
    for title in ('One', 'Three'):
        (downloads / title).mkdir(parents=True)
        (downloads / title / 'report.json').write_text(json.dumps({'blocking_failures': []}))
    (home / 'books.progress.json').write_text(json.dumps({
        'B1': {'title': 'One', 'verdict': 'done', 'failures': [], 'source': str(downloads / 'One.zip')},
        'B2': {'title': 'Two', 'verdict': 'failed', 'failures': ['kindle_app_only'], 'error': 'kindle app required'},
        'B3': {'title': 'Three', 'verdict': 'failed', 'failures': ['headings'], 'source': str(downloads / 'Three')}}))
    assert step(home, 'capture') == 'captured: B4'
    # The done book's capture is kept and converted again under code that can say which version made it.
    assert state(home, 'B1') == book.Ready('convert')
    assert book.Library(home / 'kindle').book('B1').pages == downloads / 'One'
    assert state(home, 'B2').failure['checks'] == ['kindle_app_only']
    assert step(home, 'convert') == 'converted: B1'
    assert mac.log[-1] == ('convert', 'B1', downloads / 'One')
    assert step(home, 'capture') == 'nothing to do'


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True).stdout


def test_main_is_pulled_each_step_and_local_changes_pause_the_job_with_gits_message(home, mac, tmp_path):
    upstream, checkout, writer = tmp_path / 'upstream.git', home / 'checkout', tmp_path / 'writer'
    git(tmp_path, 'init', '-q', '--bare', '-b', 'main', str(upstream))
    git(checkout, 'init', '-q', '-b', 'main')
    git(checkout, 'remote', 'add', 'origin', str(upstream))
    for cwd in (checkout,):
        git(cwd, 'config', 'user.email', 't@t'), git(cwd, 'config', 'user.name', 't')
    git(checkout, 'add', '.'), git(checkout, 'commit', '-qm', 'base'), git(checkout, 'push', '-q', 'origin', 'main')
    git(tmp_path, 'clone', '-q', str(upstream), str(writer))
    git(writer, 'config', 'user.email', 't@t'), git(writer, 'config', 'user.name', 't')
    (writer / 'img2txt.py').write_text('# merged fix\n')
    git(writer, 'commit', '-qam', 'fix'), git(writer, 'push', '-q', 'origin', 'main')
    (checkout / 'img2txt.py').write_text('# an edit on the Mac\n')
    paused = step(home, 'capture')
    assert paused.startswith('paused: git pull --ff-only failed:') and 'img2txt.py' in paused
    assert mac.log == []
    git(checkout, 'checkout', '--', 'img2txt.py')
    assert step(home, 'capture') == 'captured: B1'
    assert (checkout / 'img2txt.py').read_text() == '# merged fix\n'
    assert not (home / 'kindle' / 'pause.capture.json').exists()


def test_the_launchd_jobs_run_one_step_of_each_job_and_are_started_again(home):
    import plistlib
    root = Path(kc_run.__file__).parent / 'launchd'
    jobs = {}
    for path in sorted(root.glob('*.plist')):
        plist = plistlib.loads(path.read_bytes())
        jobs[plist['ProgramArguments'][-1]] = plist
        assert plist['ProgramArguments'][1:-1] == [str(Path(plist['WorkingDirectory']) / 'kc_run.py')]
        assert plist['KeepAlive'] is True and plist['ThrottleInterval'] == 10
        assert Path(plist['StandardOutPath']).parent.name == 'ocr-kindle'
    assert sorted(jobs) == sorted(book.JOBS)


def test_a_book_s_time_from_its_first_capture_to_drive_is_recorded_once_and_a_slow_one_is_named(home, mac):
    # 2026-10-12: 英語教育幻想 took 66.7 minutes from its first capture to Drive over six captures; nothing counted it.
    mac.captures['B1'] = [['capture_stall']]
    assert step(home, 'capture', now=NOON) == 'retry: B1'
    assert step(home, 'capture', now=NOON + 600) == 'captured: B1'
    assert step(home, 'convert', now=NOON + 900) == 'converted: B1'
    assert step(home, 'convert', now=NOON + 4000) == 'published slowly (66 min): B1'
    published = book.Library(home / 'kindle').book('B1').record('publish')
    assert published['seconds'] == 4000 and published['slow']
    # A converter change does not publish the book again. A new capture does, and the time stays the first one.
    edit(home, 'layout.py')
    assert state(home, 'B1') == book.Done()
    one = book.Library(home / 'kindle').book('B1')
    captured = one.record('capture')
    one.succeed('capture', {**captured, 'attempt': 2})
    assert step(home, 'convert', now=NOON + 90000) == 'converted: B1'
    assert step(home, 'convert', now=NOON + 90100) == 'published slowly (66 min): B1'
    assert step(home, 'capture', now=NOON + 90200) == 'captured: B2'
    step(home, 'convert', now=NOON + 90300)
    assert step(home, 'convert', now=NOON + 90400) == 'published: B2'


def test_a_published_book_s_report_and_time_are_copied_to_drive_for_the_cloud_to_read(home, mac):
    # 2026-10-12: each result of 英語教育幻想 was read by asking a Mac session, six times for one book.
    drive = home / 'Drive' / 'Kindle_md'
    drive.mkdir(parents=True)
    config = {'GOOGLE_DRIVE_FOLDER': str(drive)}
    step(home, 'capture', now=NOON, config=config)
    step(home, 'convert', now=NOON + 300, config=config)
    assert step(home, 'convert', now=NOON + 600, config=config) == 'published: B1'
    copied = json.loads((home / 'Drive' / 'Kindle_reports' / 'B1.json').read_text())
    assert (copied['asin'], copied['title'], copied['publish']['seconds'], copied['publish']['slow']) == \
        ('B1', 'One', 600, False)
    assert copied['report']['blocking_failures'] == []
    assert capture_book.publish_report('B1', 'One', {}, {}, {'GOOGLE_DRIVE_FOLDER': str(home / 'gone' / 'md')}) is None


def test_a_book_parked_for_its_capture_by_the_checks_is_captured_again_once_the_extension_changes(home, mac):
    # 2026-10-12: 英語教育幻想 failed start_position and capture_stop; parked under the converter's version, the fixed
    # extension would never have captured it again.
    mac.converts['B1'] = [['start_position', 'capture_stop']]
    assert step(home, 'capture') == 'captured: B1'
    assert step(home, 'convert') == 'parked: B1'
    edit(home, 'layout.py')
    assert step(home, 'capture') == 'captured: B2'
    assert state(home, 'B1').failure['checks'] == ['start_position', 'capture_stop']
    edit(home, 'extension/background.js')
    assert step(home, 'capture') == 'captured: B1'
    assert mac.log[-1] == ('capture', 'B1', True)


def test_the_capture_job_stays_at_most_two_new_books_ahead_of_the_convert_job(home, mac):
    # 2026-10-12: How to Write a Lot took 85 minutes from its first capture to Drive for about 5 minutes of work; the
    # rest it waited behind captured books for the convert job. A book is timed from its first capture, so the capture
    # job captures the next new book only while at most one new book waits for convert or publish.
    assert [step(home, 'capture') for _ in range(3)] == ['captured: B1', 'captured: B2', 'waiting for the convert job']
    assert step(home, 'convert') == 'converted: B1'
    assert step(home, 'capture') == 'waiting for the convert job'
    assert step(home, 'convert') == 'published: B1'
    assert step(home, 'capture') == 'captured: B3'


def test_a_paused_convert_job_does_not_hold_the_capture_job(home, mac):
    # 2026-10-14 11:2x JST: contents_chapters on Building a Second Brain and Snow Crash paused the convert job, and the
    # capture job then waited for it ("waiting for the convert job") with the new capture of So You Want to Talk
    # About Race still to do. A paused convert job catches up only with new code; the capture job goes on meanwhile.
    (home / 'books.tsv').write_text((home / 'books.tsv').read_text() + 'B5\tFive\nB6\tSix\n')
    mac.converts.update({'B1': [['contents_chapters']], 'B2': [['contents_chapters']]})
    assert [step(home, 'capture') for _ in range(2)] == ['captured: B1', 'captured: B2']
    assert step(home, 'convert') == 'parked: B1'
    assert step(home, 'convert') == 'paused: B2'
    assert [step(home, 'capture') for _ in range(3)] == ['captured: B3', 'captured: B4', 'captured: B5']
    edit(home, 'layout.py')
    assert step(home, 'capture') == 'waiting for the convert job'


def test_a_new_book_is_converted_before_published_books_are_converted_again(home, mac):
    # 2026-10-12: after #107 changed the converter, the convert job converted and published again Japan Travel Planner,
    # For 91 Days in Sri Lanka, Zero to Sold and others (07:44-07:45Z) before How to Write a Lot, a new book (07:47Z).
    # A converter change no longer reconverts a published book. A new capture of one still waits behind a new book.
    step(home, 'capture'), step(home, 'convert'), step(home, 'convert')
    assert state(home, 'B1') == book.Done()
    edit(home, 'proofread.py')
    assert state(home, 'B1') == book.Done()
    one = book.Library(home / 'kindle').book('B1')
    one.succeed('capture', {**one.record('capture'), 'attempt': 2})
    assert step(home, 'capture') == 'captured: B2'
    assert [step(home, 'convert') for _ in range(4)] == ['converted: B2', 'published: B2', 'converted: B1', 'published: B1']


def test_each_convert_step_leaves_every_book_s_state_on_drive_for_the_cloud_to_read(home, mac):
    # 2026-10-12: the cloud found For 91 Days in Tokyo and three other books still in their 10-11 form on Drive, with
    # no report and no case, and could not tell whether they were queued, parked or not in the list at all.
    drive = home / 'Drive' / 'Kindle_md'
    drive.mkdir(parents=True)
    config = {'GOOGLE_DRIVE_FOLDER': str(drive)}
    mac.converts['B1'] = [['start_position', 'capture_stop']]
    step(home, 'capture', config=config)
    step(home, 'capture', config=config)
    assert step(home, 'convert', config=config) == 'parked: B1'
    books = json.loads((home / 'Drive' / 'Kindle_reports' / '_books.json').read_text())
    assert [(b['asin'], b['title'], b['state']) for b in books['books']] == [
        ('B1', 'One', 'parked at convert: start_position, capture_stop'), ('B2', 'Two', 'ready to convert'),
        ('B3', 'Three', 'ready to capture'), ('B4', 'Four', 'ready to capture')]
    assert step(home, 'convert', config=config) == 'converted: B2'
    books = json.loads((home / 'Drive' / 'Kindle_reports' / '_books.json').read_text())
    assert books['books'][1]['state'] == 'ready to publish'


def test_a_parked_check_and_its_pause_rerun_only_when_that_check_s_code_changes(home, mac):
    # Two contents_chapters failures park their books (F151: the check does not pause the job). A change to some other
    # check's code leaves them parked. A change to the contents check's code converts them again,
    # while a book parked on headings stays parked, and a book that already passed stays done.
    # proofread.py holds heading_noise's find_all and contents_chapters' not_a_title (F134): only the first changes.
    proofread = home / 'checkout' / 'proofread.py'
    proofread.write_text('def find_all():\n    return 1\n\n\ndef not_a_title():\n    return 1\n')
    step(home, 'capture'), step(home, 'convert'), step(home, 'convert')
    assert state(home, 'B1') == book.Done()
    mac.converts.update({'B2': [['headings']], 'B3': [['contents_chapters']], 'B4': [['contents_chapters']]})
    assert [step(home, 'capture') for _ in range(3)] == ['captured: B2', 'captured: B3', 'waiting for the convert job']
    assert step(home, 'convert') == 'parked: B2'
    assert step(home, 'capture') == 'captured: B4'
    assert step(home, 'convert') == 'parked: B3'
    assert step(home, 'convert') == 'parked: B4'
    proofread.write_text(proofread.read_text().replace('return 1', 'return 2', 1))
    assert state(home, 'B1') == book.Done()
    assert state(home, 'B2').failure['checks'] == ['headings']
    assert [state(home, b).failure['checks'] for b in ('B3', 'B4')] == [['contents_chapters']] * 2
    edit(home, 'layout.py')
    assert state(home, 'B1') == book.Done()
    assert state(home, 'B2').failure['checks'] == ['headings']
    assert step(home, 'convert') == 'converted: B3'
    assert step(home, 'convert') == 'published: B3'
    assert step(home, 'convert') == 'converted: B4'
    assert state(home, 'B2').failure['checks'] == ['headings']


def test_a_book_published_again_under_a_new_title_leaves_one_copy_on_drive(tmp_path):
    # 2026-10-12: Drive held 11 books twice, the old copy under the short title of an earlier list ("Zero to Sold.md"
    # beside "Zero to Sold: How to Start, Run, and Sell a Bootstrapped Business.md"). Another folder's file stays.
    drive, folder = tmp_path / 'Kindle_md', tmp_path / 'B1'
    drive.mkdir()
    folder.mkdir()
    (folder / 'Zero to Sold: How to Start.md').write_text('# Zero to Sold\n')
    (drive / 'Zero to Sold.md').write_text('old\n')
    (tmp_path / 'Elsewhere.md').write_text('keep\n')
    config = {'GOOGLE_DRIVE_FOLDER': str(drive)}
    copied = capture_book.publish(folder, 'Zero to Sold: How to Start', config, replaces=str(drive / 'Zero to Sold.md'))
    assert sorted(p.name for p in drive.iterdir()) == ['Zero to Sold: How to Start.md'] and copied == drive / copied.name
    capture_book.publish(folder, 'Zero to Sold: How to Start', config, replaces=str(tmp_path / 'Elsewhere.md'))
    capture_book.publish(folder, 'Zero to Sold: How to Start', config, replaces=str(drive / 'Zero to Sold: How to Start.md'))
    assert (tmp_path / 'Elsewhere.md').exists() and (drive / 'Zero to Sold: How to Start.md').exists()
