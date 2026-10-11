import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('guard', Path(__file__).parent / '.claude/hooks/guard_capture.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

BOOK = "open -a \"Google Chrome\" 'https://read.amazon.com/?asin=B07HNMJV57#autocapture'"
NEXT = "open -a \"Google Chrome\" 'https://read.amazon.com/?asin=B0ZEROSOLD#autocapture'"


@pytest.mark.parametrize('command', [
    'osascript kindle2img.applescript ltr ~/Downloads/book',
    'venv/bin/python capture_app.py books.tsv',
    'screencapture -x /tmp/page.png',
    'osascript -e \'tell application "Kindle" to activate\'',
    'open -a Kindle',
    'open "kindle://book?action=open&asin=B07HNMJV57"',
])
def test_kindle_app_needs_laikens_ask(command):
    assert 'Cloud Reader extension' in guard.check(command)
    assert guard.check(f'KINDLE_APP_OK=1 {command}') is None


@pytest.mark.parametrize('command', [
    'osascript -e \'quit app "Brave Browser"\'',
    'osascript -e \'tell application "Brave Browser" to quit\'',
    'killall "Brave Browser"',
    'pkill -x Brave',
    'osascript -e \'quit app "Google Chrome"\'',
    'killall "Google Chrome"',
])
def test_the_browsers_are_never_restarted(command):
    assert 'Never quit or restart Chrome or Brave' in guard.check(command)


def test_cloud_reader_opens_in_chrome():
    # Laiken, 2026-10-11: the extension is in Chrome too, and captures move there.
    assert guard.check("open -a 'Google Chrome' 'https://read.amazon.com/?asin=B07HNMJV57#autocapture'") is None


def test_books_open_in_brave_one_after_another():
    # The next-book decision is check_book.py's, so the hook lets every capture through.
    assert guard.check(BOOK) is None
    assert guard.check(NEXT) is None
    assert guard.check(f'{BOOK.replace("#autocapture", "#recapture")}') is None


def test_ordinary_commands_pass():
    for command in ['venv/bin/python img2txt.py ~/Downloads/x.zip --model vision', 'git pull',
                    'osascript -e \'tell application "Brave Browser" to close (tabs of window 1 whose URL contains "B07HNMJV57")\'']:
        assert guard.check(command) is None


def test_hook_wiring_blocks_through_stdin_and_exit_code():
    """The hook as settings.json runs it: JSON on stdin, exit 2 with the reason on stderr."""
    import json
    import subprocess
    import sys
    event = json.dumps({'session_id': 's', 'tool_name': 'Bash', 'tool_input': {'command': 'osascript kindle2img.applescript ltr'}})
    result = subprocess.run([sys.executable, str(Path(__file__).parent / '.claude/hooks/guard_capture.py')], input=event, capture_output=True, text=True)
    assert result.returncode == 2 and 'Cloud Reader extension' in result.stderr
    settings = json.loads((Path(__file__).parent / '.claude/settings.json').read_text())
    assert any('guard_capture.py' in h['command'] for m in settings['hooks']['PreToolUse'] for h in m['hooks'] if m['matcher'] == 'Bash')
