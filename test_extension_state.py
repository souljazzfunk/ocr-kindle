import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import capture_book
import extension_state

ROOT = Path(__file__).parent


def fake_git(branch='main', dirty='', behind='', fetch=0):
    answers = {'fetch': SimpleNamespace(returncode=fetch, stdout='', stderr='offline'),
               'rev-parse': SimpleNamespace(returncode=0, stdout=branch + '\n', stderr=''),
               'status': SimpleNamespace(returncode=0, stdout=dirty, stderr=''),
               'diff': SimpleNamespace(returncode=0, stdout=behind, stderr='')}
    return lambda *args: answers[args[0]]


def test_main_with_no_local_edits_is_accepted():
    assert extension_state.folder_problem(git=fake_git()) is None


def test_the_unmerged_branch_brave_ran_on_2026_10_10_is_refused():
    problem = extension_state.folder_problem(git=fake_git(branch='extension-dom-probe', behind='extension/content.js\n'))
    assert 'branch is extension-dom-probe' in problem and 'git checkout main && git pull' in problem


def test_an_ad_hoc_local_edit_is_refused():
    problem = extension_state.folder_problem(git=fake_git(dirty=' M extension/content.js\n'))
    assert 'local changes in extension/: M extension/content.js' in problem


def test_capture_refuses_before_offering_the_job(monkeypatch, tmp_path):
    monkeypatch.setattr(capture_book, 'folder_problem', lambda: 'branch is extension-dom-probe, not main')
    monkeypatch.setattr(capture_book, 'channel', lambda: pytest.fail('offered the job'))
    data = capture_book.convert('B1', downloads=tmp_path)
    assert data['blocking_failures'] == ['extension_folder'] and data['error'].endswith('not main')


def test_a_capture_by_an_extension_that_did_not_reload_is_refused(tmp_path, monkeypatch):
    from channel import CaptureResult
    made_by = []

    def capture(asin, domain, folder, recapture):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'capture.json').write_text(json.dumps({'extension_version': made_by[-1]}))
        return CaptureResult('done', '', 'Drive', 1)

    monkeypatch.setattr(capture_book, 'folder_problem', lambda: None)
    monkeypatch.setattr(capture_book, 'channel', lambda: type('C', (), {'capture': staticmethod(capture)})())
    made_by.append('1.3')
    title, failure = capture_book.capture('B1', 'Drive', 'read.amazon.com', tmp_path / 'Drive')
    assert title is None and failure['blocking_failures'] == ['extension_version']
    assert 'captured by extension 1.3' in failure['error'] and 'did not reload itself' in failure['error']
    made_by.append(capture_book.repo_version())
    assert capture_book.capture('B1', 'Drive', 'read.amazon.com', tmp_path / 'Drive') == ('Drive', None)


def test_ci_requires_a_version_bump_when_the_extension_changes(tmp_path):
    def sh(*args):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)
    sh('git', 'init', '-q', '-b', 'main')
    sh('git', 'config', 'user.email', 't@t'); sh('git', 'config', 'user.name', 't')
    (tmp_path / 'extension').mkdir()
    (tmp_path / 'extension' / 'manifest.json').write_text(json.dumps({'version': '1.3'}))
    (tmp_path / 'extension' / 'content.js').write_text('a')
    sh('git', 'add', '.'); sh('git', 'commit', '-qm', 'base')
    sh('git', 'checkout', '-qb', 'change')
    (tmp_path / 'extension' / 'content.js').write_text('b')
    sh('git', 'commit', '-qam', 'change')
    script = str(ROOT / 'scripts' / 'check_extension_version.py')
    failed = subprocess.run([sys.executable, script, 'main'], cwd=tmp_path, capture_output=True, text=True)
    assert failed.returncode == 1 and 'still 1.3' in failed.stderr
    (tmp_path / 'extension' / 'manifest.json').write_text(json.dumps({'version': '1.4'}))
    sh('git', 'commit', '-qam', 'bump')
    assert subprocess.run([sys.executable, script, 'main'], cwd=tmp_path).returncode == 0
