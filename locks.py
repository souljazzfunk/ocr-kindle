"""One writer at a time, across processes: per book folder and per book list.

On 2026-10-11 a conversion of Automate the Boring Stuff started by hand ran while the list was about to convert the
same book again from its ZIP, and both would have written the same folder, Drive copy and progress file. A lock is
a sibling file held with flock, so it is let go when its process ends, however it ends. A process passes the locks it
holds to the commands it starts (KINDLE_LOCKS_HELD), so capture_book.py's img2txt.py does not wait on its own parent.
"""

import contextlib
import fcntl
import os
from pathlib import Path

HELD = 'KINDLE_LOCKS_HELD'
_kept = []  # locks a command-line run holds until it exits


class Busy(RuntimeError):
    pass


def book_lock(path):
    """The lock file of a book: its folder, or the folder a ZIP extracts to."""
    path = Path(path).resolve()
    folder = path.with_suffix('') if path.suffix == '.zip' else path
    return folder.with_name(f'.{folder.name}.lock')


def _held():
    return [p for p in os.environ.get(HELD, '').split(os.pathsep) if p]


def _acquire(lock, wait, say):
    lock = Path(lock).resolve()
    if str(lock) in _held():
        return None
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock, 'a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if not wait:
            handle.close()
            raise Busy(f'another run holds {lock}')
        say(f'Waiting for another run to finish with {lock.name[1:-5]}', flush=True)
        fcntl.flock(handle, fcntl.LOCK_EX)
    os.environ[HELD] = os.pathsep.join(_held() + [str(lock)])
    return handle


def _release(handle):
    os.environ[HELD] = os.pathsep.join(p for p in _held() if p != handle.name)
    fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()


@contextlib.contextmanager
def holding(lock, wait=True, say=print):
    handle = _acquire(lock, wait, say)
    try:
        yield
    finally:
        if handle:
            _release(handle)


def keep(lock, wait=True, say=print):
    """Hold the lock until this process exits (a command that works on one book)."""
    handle = _acquire(lock, wait, say)
    if handle:
        _kept.append(handle)
