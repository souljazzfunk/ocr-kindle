#!/usr/bin/env python3
"""PreToolUse hook for Bash: keep Kindle captures on the order Laiken set (see CLAUDE.md).

1. Pages come from the Cloud Reader extension in Chrome (Brave until 2026-10-11, when Laiken switched). The Kindle
   for Mac app is driven only when Laiken's own message asks for it; the command then carries KINDLE_APP_OK=1.
2. Chrome and Brave are never quit, killed or restarted: Laiken keeps using them.
Whether to go on to the next book is decided by check_book.py's result inside capture_books.py, not here.

Exit 2 blocks the command and shows the reason to Claude.
"""

import json
import re
import sys

KINDLE_APP = re.compile(r'capture_app\.py|kindle2img\.applescript|\bscreencapture\b|tell application "Kindle"|open\s+-a\s+["\']?Kindle\b|kindle://')
BROWSER_RESTART = re.compile(r'(quit|kill|pkill|killall)\b[^\n;|&]*(Brave|Chrome)|(Brave|Chrome)[^\n;|&]*\bquit\b', re.I)


def check(command):
    """Returns the reason to block, or None."""
    if KINDLE_APP.search(command) and 'KINDLE_APP_OK=1' not in command:
        return ('Kindle for Mac app capture is off by default. Use the Cloud Reader extension in Chrome '
                '(venv/bin/python capture_book.py <ASIN> --title "<title>"). '
                'Only if Laiken\'s own message asks for the Kindle app, prefix the command with KINDLE_APP_OK=1.')
    if BROWSER_RESTART.search(command):
        return 'Never quit or restart Chrome or Brave; Laiken keeps using them. The extension closes its own window when a capture ends.'
    return None


def main():
    event = json.load(sys.stdin)
    reason = check(event.get('tool_input', {}).get('command', ''))
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
