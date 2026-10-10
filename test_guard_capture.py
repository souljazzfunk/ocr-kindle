import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('guard', Path(__file__).parent / '.claude/hooks/guard_capture.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

BOOK = "open -a \"Brave Browser\" 'https://read.amazon.com/?asin=B07HNMJV57#autocapture'"
NEXT = "open -a \"Brave Browser\" 'https://read.amazon.com/?asin=B0ZEROSOLD#autocapture'"


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('GUARD_CAPTURE_STATE', str(tmp_path))


@pytest.mark.parametrize('command', [
    'osascript kindle2img.applescript ltr ~/Downloads/book',
    'screencapture -x /tmp/page.png',
    'osascript -e \'tell application "Kindle" to activate\'',
    'open -a Kindle',
    'open "kindle://book?action=open&asin=B07HNMJV57"',
])
def test_kindle_app_needs_laikens_ask(command):
    assert 'Cloud Reader extension' in guard.check(command, 's')
    assert guard.check(f'KINDLE_APP_OK=1 {command}', 's') is None


@pytest.mark.parametrize('command', [
    'osascript -e \'quit app "Brave Browser"\'',
    'osascript -e \'tell application "Brave Browser" to quit\'',
    'killall "Brave Browser"',
    'pkill -x Brave',
])
def test_brave_is_never_restarted(command):
    assert 'Never quit or restart Brave' in guard.check(command, 's')


def test_cloud_reader_opens_in_brave_not_chrome():
    assert 'Brave' in guard.check("open -a 'Google Chrome' 'https://read.amazon.com/?asin=B07HNMJV57'", 's')


def test_one_book_per_session_without_ok():
    assert guard.check(BOOK, 's') is None
    assert 'wait for Laiken' in guard.check(NEXT, 's')
    assert 'wait for Laiken' in guard.check(BOOK, 's')  # the same book twice is a duplicate capture
    assert guard.check(f'NEXT_BOOK_OK=1 {NEXT}', 's') is None
    assert guard.check(NEXT, 'another-session') is None


def test_ordinary_commands_pass():
    for command in ['venv/bin/python img2txt.py ~/Downloads/x.zip --model vision', 'git pull',
                    'osascript -e \'tell application "Brave Browser" to close (tabs of window 1 whose URL contains "B07HNMJV57")\'']:
        assert guard.check(command, 's') is None
