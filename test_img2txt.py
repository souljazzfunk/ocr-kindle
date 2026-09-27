import os
import subprocess
import sys
import zipfile
from pathlib import Path

from img2txt import join_pages

SCRIPT = Path(__file__).with_name('img2txt.py')


def run(*args):
    env = {**os.environ, 'KINDLE_OCR_CONFIG': '/nonexistent/config.env'}
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, env=env)


def test_join_pages_glues_sentence_split_across_pages():
    pages = ['　本書は、' + 'あ' * 100 + '応用言語学の知見から', '検証します。私は、\n\n　次の段落。', '## 幻想1', '　私たちの日常生活は']
    assert join_pages(pages) == '　本書は、' + 'あ' * 100 + '応用言語学の知見から検証します。私は、\n\n　次の段落。\n\n## 幻想1\n\n　私たちの日常生活は\n'


def test_join_pages_english_and_blank_pages():
    body = 'It was the best of times, it was the worst of times, it was the age of wisdom, it was the age of wisdom, it was the age of'
    assert join_pages([body, ' ', 'foolishness.', 'New page.']) == body + ' foolishness.\n\nNew page.\n'


def test_join_pages_keeps_short_title_pages_separate():
    assert join_pages(['筑摩ｅブックス', '〈お断り〉', '幻想1　アメリカ英語', '私たちの日常生活は']) == '筑摩ｅブックス\n\n〈お断り〉\n\n幻想1　アメリカ英語\n\n私たちの日常生活は\n'


def test_assembles_without_api_when_every_page_has_text(tmp_path):
    book = tmp_path / 'My Book'
    book.mkdir()
    for n, text in [(2, 'second.'), (10, 'tenth.'), (1, 'first.')]:
        (book / f'page_{n:04d}.png').write_bytes(b'png')
        (book / f'page_{n:04d}.txt').write_text(text)
    result = run(book)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (book / 'My Book.md').read_text() == 'first.\n\nsecond.\n\ntenth.\n'


def test_missing_text_without_api_key_leaves_page_pending(tmp_path):
    (tmp_path / 'screenshot_001.png').write_bytes(b'not really a png')
    (tmp_path / 'screenshot_002.png').write_bytes(b'png')
    (tmp_path / 'screenshot_002.txt').write_text('done.')
    result = run(tmp_path)
    assert result.returncode == 1
    assert 'GEMINI_API_KEY is not set' in result.stderr
    assert '2 pages, 1 already have text, 1 to OCR' in result.stdout
    assert not (tmp_path / 'screenshot_001.txt').exists()


def test_accepts_extension_zip_and_names_output_after_it(tmp_path):
    archive = tmp_path / '英語教育幻想.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('page_0001.png', b'png')
        z.writestr('page_0001.txt', '## はじめに')
        z.writestr('page_0002.png', b'png')
        z.writestr('page_0002.txt', '　本文。')
    result = run(archive)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / '英語教育幻想' / '英語教育幻想.md').read_text() == '## はじめに\n\n　本文。\n'
