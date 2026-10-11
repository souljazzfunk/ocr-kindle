import os
import subprocess
import sys
import textwrap

import pytest

import locks

TRY = textwrap.dedent('''
    import sys, locks
    try:
        locks.keep(sys.argv[1], wait=False)
        print('got it')
    except locks.Busy:
        print('busy')
''')


def other_process(lock, env=None):
    return subprocess.run([sys.executable, '-c', TRY, str(lock)], capture_output=True, text=True,
                          cwd=os.path.dirname(locks.__file__), env=env).stdout.strip()


@pytest.fixture(autouse=True)
def no_inherited_locks(monkeypatch):
    monkeypatch.delenv(locks.HELD, raising=False)


def test_a_book_has_one_lock_whether_named_by_its_zip_or_its_folder(tmp_path):
    assert locks.book_lock(tmp_path / 'Dilla Time.zip') == locks.book_lock(tmp_path / 'Dilla Time')
    assert locks.book_lock(tmp_path / 'Dilla Time').parent == tmp_path.resolve()


def test_a_second_run_on_the_same_book_waits_for_the_first(tmp_path):
    # 2026-10-11: Automate the Boring Stuff was converted by hand while the list was about to convert it again.
    lock = locks.book_lock(tmp_path / 'Automate the Boring Stuff')
    with locks.holding(lock):
        clean = {k: v for k, v in os.environ.items() if k != locks.HELD}
        assert other_process(lock, clean) == 'busy'
        # A command this run starts inherits the lock instead of waiting on its own parent.
        assert other_process(lock) == 'got it'
    assert other_process(lock) == 'got it'
    assert locks.HELD not in os.environ or str(lock) not in os.environ[locks.HELD]


def test_a_lock_is_let_go_when_its_holder_ends_however_it_ends(tmp_path):
    lock = tmp_path / 'books.lock'
    holder = subprocess.Popen([sys.executable, '-c', 'import sys, time, locks; locks.keep(sys.argv[1]); print("held", flush=True); time.sleep(30)',
                               str(lock)], stdout=subprocess.PIPE, text=True, cwd=os.path.dirname(locks.__file__))
    assert holder.stdout.readline().strip() == 'held'
    with pytest.raises(locks.Busy):
        with locks.holding(lock, wait=False):
            pass
    holder.kill()
    holder.wait()
    with locks.holding(lock, wait=False):
        pass


def test_git_calls_on_the_checkout_wait_for_each_other_as_on_2026_10_11(tmp_path):
    # The list's pull and a capture's fetch of origin/main ran together, and git refused the fetch.
    (tmp_path / '.git').mkdir()
    import threading
    order = []
    inside = threading.Event()

    def first():
        with locks.git_checkout(tmp_path):
            inside.set()
            order.append('pull starts')
            threading.Event().wait(0.2)
            order.append('pull ends')

    thread = threading.Thread(target=first)
    thread.start()
    inside.wait()
    with locks.git_checkout(tmp_path):
        order.append('fetch')
    thread.join()
    assert order == ['pull starts', 'pull ends', 'fetch']


def test_the_list_and_the_capture_hold_the_checkout_while_they_run_git(monkeypatch):
    import capture_books
    import extension_state
    held = []
    monkeypatch.setattr(locks, 'git_checkout', lambda root: _Recorder(held))
    extension_state.folder_problem(git=lambda *a: type('R', (), {'returncode': 0, 'stdout': 'main', 'stderr': ''})())
    assert held == ['in', 'out']
    held.clear()
    capture_books.update_checkout(git=lambda *a: type('R', (), {'returncode': 0, 'stdout': 'main' if a[1] == '--abbrev-ref' else 'a', 'stderr': ''})())
    assert held == ['in', 'out']


class _Recorder:
    def __init__(self, held):
        self.held = held

    def __enter__(self):
        self.held.append('in')

    def __exit__(self, *_):
        self.held.append('out')
