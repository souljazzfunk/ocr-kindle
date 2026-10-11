import json
import sqlite3
from pathlib import Path

import pytest

import browser
import capture_book
import capture_books

EXT = 'jjdobbobffjockmhfkicadhmomagalnc'


def profile(root, prompt=None, downloads=()):
    p = root / 'Profile 1'
    p.mkdir(parents=True)
    (p / 'Secure Preferences').write_text(json.dumps({'extensions': {'settings': {
        'other': {'path': '/x/other'}, EXT: {'path': '/Users/otchy/Documents/GitHub.nosync/ocr-kindle/extension'}}}}))
    prefs = {'download': {'default_directory': '/Users/otchy/Downloads'}}
    if prompt is not None:
        prefs['download']['prompt_for_download'] = prompt
    (p / 'Preferences').write_text(json.dumps(prefs))
    db = sqlite3.connect(p / 'History')
    db.execute('CREATE TABLE downloads (id INTEGER, target_path TEXT, by_ext_id TEXT, state INTEGER)')
    db.executemany('INSERT INTO downloads VALUES (?, ?, ?, ?)', downloads)
    db.commit()
    db.close()
    return root


def test_finds_the_profile_that_loads_this_extension(tmp_path):
    assert browser.extension_profile(profile(tmp_path)) == (tmp_path / 'Profile 1', EXT)


def test_unset_prompt_setting_is_refused_as_on_2026_10_10(tmp_path):
    # The real state that opened the save dialog: Brave's default, prompt_for_download not written.
    assert 'Ask where to save' in browser.prompt_problem(profile(tmp_path))
    assert browser.prompt_problem(profile(tmp_path / 'b', prompt=True)) is not None
    assert browser.prompt_problem(profile(tmp_path / 'c', prompt=False)) is None


def test_download_record_tells_the_extension_from_a_page_link(tmp_path):
    zip_path = '/Users/otchy/Downloads/AI for Educators (1).zip'
    root = profile(tmp_path, downloads=[(889, '/Users/otchy/Downloads/kindle-book.zip', '', 1),
                                        (890, zip_path, EXT, 1)])
    assert browser.download_record(zip_path, root) == (True, True)
    assert browser.download_record('/Users/otchy/Downloads/kindle-book.zip', root) == (False, True)  # downloads 885-889
    assert browser.download_record('/nowhere.zip', root) is None


def test_download_check_passes_only_an_extension_save_that_completed():
    assert capture_book.download_check('a.zip', record=lambda p: (True, True)).ok
    assert 'page link' in capture_book.download_check('a.zip', record=lambda p: (False, True)).detail
    assert not capture_book.download_check('a.zip', record=lambda p: (True, False)).ok
    assert not capture_book.download_check('a.zip', record=lambda p: None).ok


def test_the_list_does_not_start_while_brave_would_ask(tmp_path, monkeypatch):
    books = tmp_path / 'books.tsv'
    books.write_text('B1\tOne\n')
    monkeypatch.setattr(browser, 'prompt_problem', lambda: 'Brave asks where to save')
    monkeypatch.setattr(capture_books.capture_book, 'convert', lambda *a, **k: pytest.fail('started a capture'))
    monkeypatch.setattr('sys.argv', ['capture_books.py', str(books)])
    with pytest.raises(SystemExit, match='Not starting: Brave asks'):
        capture_books.main()
