import os
import subprocess
import sys
from pathlib import Path

from proofread import Book, apply, find_all

SCRIPT = Path(__file__).with_name('proofread.py')
BODY = ('The group became known as the Homebrew Computer Club, and it encapsulated the Whole Earth fusion between '
        'the counterculture and technology. ')


def fixes(pages):
    book = Book(Path('/nonexistent'), pages)
    return sorted((f.kind, f.page, f.before, f.after) for f in find_all(book))


def test_page_level_errors():
    pages = {
        'page_0001': BODY + 'Brand saw Jobs as one of the purest embodiments of the cultural mix that the catalog sought.',
        'page_0002': 'Brand saw Jobs as one of the purest embodiments of the cultural mix that the catalog sought.\n\n'
                     'Wozniak spotted the flyer.',
        'page_0003': 'ここまでに済ませたこと: ブランチに修正をコミットしました。',
        'page_0004': 'Wozniak went with Allen Baum to the first meeting, held in a garage in Menlo Park.',
        'page_0005': 'Wozniak went with Allen Baum to the first meeting, held in a garage in Menlo Park!',
        'page_0006': 'Overall rating Review this book on Amazon. Recommend this book Follow the author',
    }
    assert fixes(pages) == [
        ('duplicate_page', 'page_0005', pages['page_0005'], ' '),
        ('foreign_page', 'page_0003', pages['page_0003'], ' '),
        ('foreign_page', 'page_0006', pages['page_0006'], ' '),
        ('seam_overlap', 'page_0002', pages['page_0002'][:pages['page_0002'].index('Wozniak')], ''),
    ]
    fixed = apply(pages, [f for f in find_all(Book(Path('/nonexistent'), pages))])
    assert fixed['page_0002'] == 'Wozniak spotted the flyer.'


def test_word_level_errors():
    pages = {
        'page_0001': BODY + 'Sculley met Jobs. Sculley agreed. It was an appliance-a self-contained unit.',
        'page_0002': BODY + 'Scul-ley, a built-in drive, and the whole thing came together-and "Неwas" fine.',
        'page_0003': BODY + 'NeXT and NeXT again, the iMAC, the iMac, the iMac. Then > the rest.',
    }
    assert fixes(pages) == [
        ('dash_as_hyphen', 'page_0001', 'appliance-a', 'appliance—a'),
        ('dash_as_hyphen', 'page_0002', 'together-and', 'together—and'),
        ('homoglyph', 'page_0002', '"Неwas"', '"Hewas"'),
        ('hyphen_break', 'page_0002', 'Scul-ley', 'Sculley'),
        ('odd_case', 'page_0003', 'iMAC', 'iMac'),
        ('stray_symbol', 'page_0003', '> ', ''),
    ]


def test_blockquotes_and_suspended_hyphens_are_left_alone():
    pages = {'page_0001': BODY + 'an eight- by twenty-foot coop\n\n> I called up Bill and said, “I’m going to turn this.”'}
    assert fixes(pages) == []


def test_apply_rewrites_pages_and_rebuilds_book(tmp_path):
    book = tmp_path / 'My Book'
    book.mkdir()
    (book / 'page_0001.txt').write_text(BODY + 'He was an appliance-a unit.')
    (book / 'page_0002.txt').write_text('Some text to keep.')
    for n in (1, 2):
        (book / f'page_{n:04d}.png').write_bytes(b'png')
    env = {**os.environ, 'KINDLE_OCR_CONFIG': '/nonexistent/config.env'}
    result = subprocess.run([sys.executable, str(SCRIPT), str(book), '--apply', 'dash_as_hyphen'],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'dash_as_hyphen      1 on 1 pages, 0 on text-layer pages' in result.stdout
    assert (book / 'page_0001.txt').read_text() == BODY + 'He was an appliance—a unit.'
    assert (book / 'My Book.md').read_text() == BODY + 'He was an appliance—a unit.\n\nSome text to keep.\n'
