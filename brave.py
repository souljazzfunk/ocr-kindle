"""Read-only facts about Laiken's Brave that decide whether a capture can save without a person.

On 2026-10-10 extension 1.3 saved through chrome.downloads with saveAs:false and the save dialog still opened:
Brave asks where to save by default (download.prompt_for_download unset), and that setting wins over saveAs.
capture_books.py refuses to start while Brave would ask, and each capture's download is checked in Brave's own
history, so a save that took another route cannot pass. Nothing here writes to Brave.
"""

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

BRAVE_DIR = Path.home() / 'Library/Application Support/BraveSoftware/Brave-Browser'
COMPLETE = 1  # history downloads.state


def extension_profile(root=BRAVE_DIR, folder='ocr-kindle/extension'):
    """(profile dir, extension id) of the profile that loads this repository's extension, or (None, None)."""
    for prefs in sorted(Path(root).glob('*/Secure Preferences')):
        settings = json.loads(prefs.read_text(encoding='utf-8')).get('extensions', {}).get('settings', {})
        for ext_id, ext in settings.items():
            if str(ext.get('path', '')).rstrip('/').endswith(folder):
                return prefs.parent, ext_id
    return None, None


def prompt_problem(root=BRAVE_DIR):
    """None when Brave saves without asking, else what to change. Unset means Brave's default, which asks."""
    profile, _ = extension_profile(root)
    if profile is None:
        return 'no Brave profile loads ocr-kindle/extension'
    prefs = json.loads((profile / 'Preferences').read_text(encoding='utf-8'))
    if prefs.get('download', {}).get('prompt_for_download') is False:
        return None
    return (f'Brave ({profile.name}) asks where to save each download, which opens a dialog on every ZIP. '
            'Ask Laiken to turn off "Ask where to save each file before downloading" in Brave Settings → Downloads.')


def download_record(zip_path, root=BRAVE_DIR):
    """(saved by this extension, completed) for the ZIP from Brave's download history, or None when not found."""
    profile, ext_id = extension_profile(root)
    if profile is None or not (profile / 'History').exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / 'History'
        shutil.copy2(profile / 'History', copy)  # Brave holds a lock on the live file
        with sqlite3.connect(copy) as db:
            row = db.execute('SELECT by_ext_id, state FROM downloads WHERE target_path = ? ORDER BY id DESC LIMIT 1',
                             (str(zip_path),)).fetchone()
    if row is None:
        return None
    return row[0] == ext_id, row[1] == COMPLETE
