#!/usr/bin/env python3
"""PreToolUse hook for Bash: keep Kindle captures on the order Laiken set (see CLAUDE.md).

1. Pages come from the Cloud Reader extension in Brave. The Kindle for Mac app is driven only when Laiken's own
   message asks for it; the command then carries KINDLE_APP_OK=1.
2. Brave is never quit, killed or restarted, and Chrome is not used for read.amazon.*.
3. One book per session: a second `#autocapture` open needs Laiken's OK for that book, shown as NEXT_BOOK_OK=1.

Exit 2 blocks the command and shows the reason to Claude.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

KINDLE_APP = re.compile(r'kindle2img\.applescript|\bscreencapture\b|tell application "Kindle"|open\s+-a\s+["\']?Kindle\b|kindle://')
BRAVE_RESTART = re.compile(r'(quit|kill|pkill|killall)\b[^\n;|&]*Brave|Brave[^\n;|&]*\bquit\b', re.I)
CHROME_READER = re.compile(r'Google Chrome[^\n;|&]*read\.amazon\.')
AUTOCAPTURE = re.compile(r'asin=([A-Z0-9]{10})[^\s\'"]*#autocapture')


def state_file(session_id):
    return Path(os.environ.get('GUARD_CAPTURE_STATE', tempfile.gettempdir())) / f'ocr-kindle-autocapture-{session_id}.txt'


def check(command, session_id):
    """Returns the reason to block, or None."""
    if KINDLE_APP.search(command) and 'KINDLE_APP_OK=1' not in command:
        return ('Kindle for Mac app capture is off by default. Use the Cloud Reader extension in Brave '
                '(open -a "Brave Browser" \'https://read.amazon.com/?asin=<ASIN>#autocapture\'). '
                'Only if Laiken\'s own message asks for the Kindle app, prefix the command with KINDLE_APP_OK=1.')
    if BRAVE_RESTART.search(command):
        return 'Never quit or restart Brave; Laiken keeps using it. Close only the book tab when a capture ends.'
    if CHROME_READER.search(command):
        return 'The read.amazon.com sign-in is in Brave. Open Cloud Reader with open -a "Brave Browser".'
    asins = AUTOCAPTURE.findall(command)
    if asins:
        path = state_file(session_id)
        done = path.read_text().split() if path.exists() else []
        if done and 'NEXT_BOOK_OK=1' not in command:
            return (f'This session already captured {", ".join(done)}. Report the result and wait for Laiken\'s OK '
                    'before another book; then prefix the command with NEXT_BOOK_OK=1.')
        with path.open('a') as f:
            f.writelines(f'{a}\n' for a in asins)
    return None


def main():
    event = json.load(sys.stdin)
    reason = check(event.get('tool_input', {}).get('command', ''), event.get('session_id', 'unknown'))
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
