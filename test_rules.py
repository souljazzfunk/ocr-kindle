import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('rules', ROOT / 'scripts' / 'rules.py')
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)

SOURCES = {
    'channel.py': 'def get_job(): ...\n',
    'test_channel.py': 'def test_only_the_chosen_browser_gets_jobs(served):\n    ...\n',
    'extension/job.test.js': "test('the worker names its browser', () => {});\n"
                             'test("a job ends", () => {});\n'
                             'test(`a tab reloads`, () => {});\n',
}
GOOD = '`channel.py`、`test_channel.py::test_only_the_chosen_browser_gets_jobs`（F072）'


def problems(rule, how):
    table = f'| ルール | 何が守らせるか |\n|---|---|\n| {rule} | {how} |\n'
    return [msg for _, msg in rules.lint(table, list(SOURCES), SOURCES.get)]


def test_a_short_rule_with_its_code_a_named_test_and_a_ledger_id_passes():
    assert problems('取り込みは選んだブラウザだけ', GOOD) == []


def test_a_named_test_in_the_extension_passes_whichever_quotes_it_is_written_with():
    for name in ('the worker names its browser', 'a job ends', 'a tab reloads'):
        assert problems('窓を閉じる', f'`extension/job.test.js::{name}`（F008）') == []


def test_a_row_that_names_no_test_fails():
    assert problems('取り込みは選んだブラウザだけ', '`channel.py`（F072）') == [
        '仕組みの欄にテストの名前がない (`test_x.py::test_name` か `extension/x.test.js::名前`)。'
        '決まりを確かめるテストがなければ「宿題」と書く']


def test_a_named_test_that_does_not_exist_fails():
    assert problems('取り込みは選んだブラウザだけ', '`test_channel.py::test_brave_gets_jobs`（F072）') == [
        'テストがない: test_channel.py::test_brave_gets_jobs']
    assert problems('窓を閉じる', '`extension/job.test.js::the window closes`（F008）') == [
        'テストがない: extension/job.test.js::the window closes']


def test_a_named_test_in_a_file_that_does_not_exist_fails():
    assert problems('取り込みは選んだブラウザだけ', '`test_browser.py::test_only_chrome`（F072）') == [
        '仕組みのファイルがリポジトリにない: test_browser.py']


def test_a_rule_or_a_mechanism_that_grew_too_long_fails():
    assert problems('あ' * 81, GOOD) == ['ルールが 81 字 (上限 80)。1 文にし、細部は仕組みのコードとテストへ']
    how = GOOD + 'Close Book の直後は表紙の AXPress が通らないことがある。' * 4
    assert problems('先頭へ戻る', how) == [
        f'仕組みの欄が {len(how)} 字 (上限 220)。パスと台帳の id だけにし、経緯は台帳とコミットへ']


def test_a_time_or_a_person_in_a_row_fails():
    assert problems('先頭へ戻る', GOOD + '。19:26 に確かめた') == [
        '時刻や日付 (19:26) がある。経緯は台帳の summary とコミットへ']
    assert problems('Laiken が決めた順に取り込む', GOOD) == ['人の発言がある。発言は台帳の summary へ']


def test_a_row_without_a_ledger_id_fails():
    assert problems('取り込みは選んだブラウザだけ', '`channel.py`、`test_channel.py::test_only_the_chosen_browser_gets_jobs`') == [
        '仕組みの欄に台帳の id (F0xx) がない。なぜこのルールがあるかは台帳が持つ']


def test_a_homework_row_needs_no_code_or_test():
    assert problems('拡張が効いたと言うのは実機で通ったときだけ', '宿題（F012）') == []


def test_the_rule_table_in_rules_md_passes():
    files = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.splitlines()

    def read(path):
        f = ROOT / path
        return f.read_text(encoding='utf-8') if f.is_file() else None

    assert list(rules.lint((ROOT / 'RULES.md').read_text(encoding='utf-8'), files, read)) == []
