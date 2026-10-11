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


def test_capture_refuses_before_opening_brave(monkeypatch, tmp_path):
    monkeypatch.setattr(capture_book, 'folder_problem', lambda: 'branch is extension-dom-probe, not main')
    monkeypatch.setattr(capture_book, 'open_in_browser', lambda *a: pytest.fail('opened Brave'))
    with pytest.raises(RuntimeError, match='not main'):
        capture_book.convert('B1', downloads=tmp_path)


def test_version_check_names_a_brave_that_was_not_reloaded(tmp_path):
    assert not capture_book.version_check(tmp_path, expected='1.4').ok  # no capture.json: the 1.3 ZIPs of 2026-10-10
    (tmp_path / 'capture.json').write_text(json.dumps({'extension_version': '1.3'}))
    check = capture_book.version_check(tmp_path, expected='1.4')
    assert not check.ok and 'reload the extension' in check.detail
    (tmp_path / 'capture.json').write_text(json.dumps({'extension_version': '1.4'}))
    assert capture_book.version_check(tmp_path, expected='1.4').ok


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
