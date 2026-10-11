"""One writer at a time, across processes: per book folder, and per git call on this checkout.

On 2026-10-11 a conversion of Automate the Boring Stuff started by hand ran while the list was about to convert the
same book again from its ZIP, and both would have written the same folder and Drive copy. A lock is
a sibling file held with flock, so it is let go when its process ends, however it ends. A process passes the locks it
holds to the commands it starts (KINDLE_LOCKS_HELD), so capture_book.py's img2txt.py does not wait on its own parent.
"""

import contextlib
import fcntl
import os
from pathlib import Path

HELD = 'KINDLE_LOCKS_HELD'
_kept = []  # locks a command-line run holds until it exits


def book_lock(path):
    """The lock file of a book: its folder, or the folder a ZIP extracts to."""
    path = Path(path).resolve()
    folder = path.with_suffix('') if path.suffix == '.zip' else path
    return folder.with_name(f'.{folder.name}.lock')


def _held():
    return [p for p in os.environ.get(HELD, '').split(os.pathsep) if p]


def _acquire(lock, say):
    lock = Path(lock).resolve()
    if str(lock) in _held():
        return None
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock, 'a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        say(f'Waiting for another run to finish with {lock.name[1:-5]}', flush=True)
        fcntl.flock(handle, fcntl.LOCK_EX)
    os.environ[HELD] = os.pathsep.join(_held() + [str(lock)])
    return handle


def _release(handle):
    os.environ[HELD] = os.pathsep.join(p for p in _held() if p != handle.name)
    fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()


@contextlib.contextmanager
def holding(lock, say=print):
    handle = _acquire(lock, say)
    try:
        yield
    finally:
        if handle:
            _release(handle)


def keep(lock, say=print):
    """Hold the lock until this process exits (a command that works on one book)."""
    handle = _acquire(lock, say)
    if handle:
        _kept.append(handle)


@contextlib.contextmanager
def git_checkout(root):
    """Hold this checkout's git while one caller reads or moves its refs.

    On 2026-10-11 the list pulled main while the next book's capture fetched origin/main to check the extension
    folder; git refused the fetch (cannot lock ref 'refs/remotes/origin/main') and The Art of Possibility was recorded
    as a crash before it was captured. The capture and convert jobs (kc_run.py) both pull, and a capture fetches.
    Each open() is its own flock, so this keeps threads of one run apart as well as runs.
    """
    git_dir = Path(root) / '.git'
    path = git_dir / 'kindle-git.lock' if git_dir.is_dir() else Path(root) / '.kindle-git.lock'
    with open(path, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
