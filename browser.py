"""The browser captures run in, and read-only facts about it that decide whether a capture can save without a person.

Chrome since 2026-10-11 (Laiken installed the extension there and chose it over Brave); KINDLE_BROWSER=brave goes
back to Brave. Both are Chromium: the same AppleScript, Preferences and History.

On 2026-10-10 extension 1.3 saved through chrome.downloads with saveAs:false and the save dialog still opened:
Brave asks where to save by default (download.prompt_for_download unset), and that setting wins over saveAs.
Chrome's default is not to ask: on 2026-10-11 its switch was off, the setting unset, and the list refused to start
because it read unset as Brave's default.
capture_books.py refuses to start while the browser would ask, and each capture's download is checked in the
browser's own history, so a save that took another route cannot pass. Nothing here writes to the browser.
"""

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

SUPPORT = Path.home() / 'Library/Application Support'
# (app, profile folder, whether it asks where to save while download.prompt_for_download is unset)
BROWSERS = {'chrome': ('Google Chrome', SUPPORT / 'Google/Chrome', False),
            'brave': ('Brave Browser', SUPPORT / 'BraveSoftware/Brave-Browser', True)}
APP, ROOT, ASKS_BY_DEFAULT = BROWSERS[os.environ.get('KINDLE_BROWSER', 'chrome')]
NAME = APP.split()[-1] if APP == 'Google Chrome' else APP.split()[0]  # Chrome, Brave
COMPLETE = 1  # history downloads.state


def extension_profile(root=ROOT, folder='ocr-kindle/extension'):
    """(profile dir, extension id) of the profile that loads this repository's extension, or (None, None)."""
    for prefs in sorted(Path(root).glob('*/Secure Preferences')) + sorted(Path(root).glob('*/Preferences')):
        settings = json.loads(prefs.read_text(encoding='utf-8')).get('extensions', {}).get('settings', {}) or {}
        for ext_id, ext in settings.items():
            if str(ext.get('path', '')).rstrip('/').endswith(folder):
                return prefs.parent, ext_id
    return None, None


def prompt_problem(root=ROOT, asks_by_default=ASKS_BY_DEFAULT):
    """None when the browser saves without asking, else what to change. Unset means the browser's own default."""
    profile, _ = extension_profile(root)
    if profile is None:
        return f'no {NAME} profile loads ocr-kindle/extension'
    prefs = json.loads((profile / 'Preferences').read_text(encoding='utf-8'))
    if not prefs.get('download', {}).get('prompt_for_download', asks_by_default):
        return None
    return (f'{NAME} ({profile.name}) asks where to save each download, which opens a dialog on every ZIP. '
            f'Ask Laiken to turn off "Ask where to save each file before downloading" in {NAME} Settings → Downloads.')


def download_record(zip_path, root=ROOT):
    """(saved by this extension, completed) for the ZIP from the browser's download history, or None when not found."""
    profile, ext_id = extension_profile(root)
    if profile is None or not (profile / 'History').exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / 'History'
        shutil.copy2(profile / 'History', copy)  # the browser holds a lock on the live file
        with sqlite3.connect(copy) as db:
            row = db.execute('SELECT by_ext_id, state FROM downloads WHERE target_path = ? ORDER BY id DESC LIMIT 1',
                             (str(zip_path),)).fetchone()
    if row is None:
        return None
    return row[0] == ext_id, row[1] == COMPLETE
