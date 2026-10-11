#!/usr/bin/env python3
"""CI: a change under extension/ must raise extension/manifest.json's version.

The extension reloads itself when the channel (channel.py) reports another version on disk, and capture_book.py checks
the version each capture.json names, so two different builds must never share a version.

    python scripts/check_extension_version.py origin/main
"""

import json
import subprocess
import sys


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True, check=True).stdout


def version_tuple(version):
    return tuple(int(part) for part in version.split('.'))


def check(base):
    changed = [f for f in git('diff', '--name-only', f'{base}...HEAD', '--', 'extension/').split() if not f.endswith('.test.js')]
    if not changed:
        return None
    before = json.loads(git('show', f'{base}:extension/manifest.json'))['version']
    after = json.loads(open('extension/manifest.json', encoding='utf-8').read())['version']
    if version_tuple(after) <= version_tuple(before):
        return (f'{", ".join(changed)} changed but extension/manifest.json is still {after} (base {before}). '
                'Raise "version" so the extension reloads itself into this build.')
    return None


if __name__ == '__main__':
    error = check(sys.argv[1] if len(sys.argv) > 1 else 'origin/main')
    if error:
        sys.exit(error)
    print('extension version ok')
