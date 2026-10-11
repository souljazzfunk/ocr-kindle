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
