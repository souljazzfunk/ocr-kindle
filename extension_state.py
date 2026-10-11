"""What the browser extension on this Mac is, compared with this repository's main branch.

The browser loads the unpacked extension straight from this checkout's extension/ folder, so whatever branch or
local edit sits there is what captures. Twice on 2026-10-10 Brave ran code that was not on main (an ad-hoc
#autocapture edit, then the unmerged extension-dom-probe branch). capture_book.py refuses to capture unless
the folder matches origin/main, and checks the version each ZIP says it was made by.
"""

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAPTURE = 'capture.json'


def repo_version(root=HERE):
    return json.loads((root / 'extension' / 'manifest.json').read_text(encoding='utf-8'))['version']


def folder_problem(root=HERE, git=None):
    """None when extension/ is exactly origin/main, else what is wrong and how to fix it."""
    git = git or (lambda *args: subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True))
    fetched = git('fetch', '-q', 'origin', 'main')
    if fetched.returncode:
        return f'could not fetch origin/main: {fetched.stderr.strip()}'
    branch = git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    dirty = git('status', '--porcelain', '--', 'extension/').stdout.strip()
    behind = git('diff', '--name-only', 'HEAD', 'origin/main', '--', 'extension/').stdout.strip()
    problems = []
    if branch != 'main':
        problems.append(f'branch is {branch}, not main')
    if dirty:
        problems.append(f'local changes in extension/: {" ".join(dirty.split())}')
    if behind:
        problems.append(f'extension/ differs from origin/main: {" ".join(behind.split())}')
    if problems:
        return '; '.join(problems) + '. Run `git checkout main && git pull` here, then ask Laiken to reload the extension once.'
    return None


def zip_version(folder):
    path = Path(folder) / CAPTURE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8')).get('extension_version')
