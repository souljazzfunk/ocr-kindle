import json
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


def test_english_pages_of_a_japanese_book_are_kept():
    # 2026-10-12: 英語教育幻想's English quotes and bibliography were blanked as foreign pages.
    japanese = 'ここからは私見です。この文書で英語の位置がどう語られているかを見ていきます。' * 4
    pages = {'page_0001': japanese, 'page_0002': 'Ministry of Education. https://www.mext.go.jp/ English Education Reform Plan.',
             'page_0003': 'グローバル化が進むにつれて、世界語としての英語の持つ役割が増していきます。' * 4}
    assert fixes(pages) == []


def test_emphasis_dots_read_as_commas_on_their_own_lines_are_removed_and_the_sentence_joined():
    # 2026-10-12: Laiken found 英語教育幻想's 傍点 read by Live Text as "、" and "、、" paragraphs that cut sentences.
    page = ('## 受信モードと発信モード\n\nコミュニケーションの四技能を異なる観点から組み直すこと\n\n、\n\n'
            'もできます。「聞く・読む」は情報を受けとる受信モードであ\n\n、、\n\nるのに対して、「話す・書く」は発信モードです。'
            '\n\n、、、\n\n次の段落です。')
    book = Book(Path('/nonexistent'), {'page_0001': page})
    found = find_all(book)
    assert [f.kind for f in found] == ['emphasis_dots'] * 3
    assert apply(book.pages, found)['page_0001'] == (
        '## 受信モードと発信モード\n\nコミュニケーションの四技能を異なる観点から組み直すこともできます。'
        '「聞く・読む」は情報を受けとる受信モードであるのに対して、「話す・書く」は発信モードです。\n\n次の段落です。')
    # A run of them with a backslash, and a full stop among them (考える練習: "じゃな\n\n、。\n\nサッカー").
    pages = {'page_0001': '中長期的な視点に立てば、\n\n、、、\n\n、\n\n\\\n\n日米同盟の深化です。',
             'page_0002': 'すっとぼけてるじゃな\n\n、。\n\nサッカーも同じで'}
    book = Book(Path('/nonexistent'), pages)
    assert apply(book.pages, find_all(book)) == {'page_0001': '中長期的な視点に立てば、日米同盟の深化です。',
                                                 'page_0002': 'すっとぼけてるじゃな。\n\nサッカーも同じで'}
    # The published 英語教育幻想 kept them after the fix: a run can open a page, and a page can break lines singly.
    pages = {'page_0001': '組み直すこと', 'page_0002': '、\n\nもできます。受信モードであ\n、、\nるのに対して',
             'page_0003': '```\nx = 1 + \\\n    2\n```'}
    book = Book(Path('/nonexistent'), pages)
    assert apply(book.pages, find_all(book)) == {'page_0002': 'もできます。受信モードであるのに対して'}


def test_a_screen_saved_twice_at_one_position_is_a_duplicate(tmp_path):
    # 2026-10-12: 英語教育幻想's page_0020 and page_0111 were both 129/2566 with the same text, a hundred pages apart.
    text = 'グローバル化が進むにつれて、世界語としての英語の持つ役割が増していきます。' * 3
    other = 'ここからは私見です。この文書で英語の位置がどう語られているかを見ていきます。' * 3
    pages = {'page_0001': text, 'page_0002': other, 'page_0003': other + '続き', 'page_0004': text.replace('増', '僧')}
    (tmp_path / 'positions.json').write_text(json.dumps({'page_0001': '129/2566', 'page_0002': '137/2566',
                                                         'page_0003': '145/2566', 'page_0004': '129/2566'}))
    found = sorted((f.kind, f.page) for f in find_all(Book(tmp_path, pages)) if f.kind == 'duplicate_page')
    assert found == [('duplicate_page', 'page_0003'), ('duplicate_page', 'page_0004')]


def test_screens_saved_again_far_apart_are_duplicates_even_at_other_positions(tmp_path):
    # 2026-10-12: Learn Like a Pro on Drive had its Pomodoro pages (paragraphs 45-177) again after paragraph 450, under
    # other reader positions, so duplicate_page, which matched positions, kept them. A short title page may repeat.
    first = ("Sounds easy, doesn't it? It is easy. Sometimes your mind may struggle while doing a Pomodoro, but the "
             'reality is almost anyone can keep their focus for twenty five minutes and then take a short break to rest.')
    second = ('You might wonder how something so simple can be so powerful. The reason is that the Pomodoro Technique '
              'captures important aspects of how your brain learns, switching between the focused and diffuse modes.')
    middle = ('Despite its black box behavior, the procedural system can be extremely sophisticated and it can help you '
              'learn a great deal without your noticing, from serving a ball to reading a page of music at sight.')
    pages = {'page_0001': '# Part One', 'page_0002': first, 'page_0003': second, 'page_0004': middle,
             'page_0005': '# Part One', 'page_0006': first.replace('twenty five', 'twenty-five'), 'page_0007': second}
    (tmp_path / 'positions.json').write_text(json.dumps({'page_0002': '12/250', 'page_0003': '13/250',
                                                         'page_0006': '118/250', 'page_0007': '119/250'}))
    found = sorted(f.page for f in find_all(Book(tmp_path, pages)) if f.kind == 'duplicate_page')
    assert found == ['page_0006', 'page_0007']


def test_a_japanese_sentence_cut_into_paragraphs_is_joined_and_a_reading_between_them_dropped():
    # 2026-10-12: 考える練習 on Drive had 104 sentences cut at a line end ("思ってしまったりし\n\nます。"), one with the
    # ruby reading of 搾取 between the halves ("若\n\nさくしゅ\n\nい人たち"); Virtual Girls Singularity 33 ("…で\n\nす").
    page = ('いるのを見ると、そうなのかな、と思ってしまったりし\n\nます。次の文です。\n\n'
            '吸い上げられるものもなくなる。そうなると今度は、若\n\nさくしゅ\n\nい人たちが正社員にならない。\n\n'
            '…・寝てしまったみたい…で\n\nす\n\nいい言い訳を考えた。\n\n## 体全体で考える\n\nなぜ勝つことが大事か？')
    # A contents list is left as it is, and so is a half that opens its own sentence after a line Live Text lost.
    contents = ('理を突きつめても意味がない「賢い中学生」式の考え方\n\nあきらめずに動きつづける\n\n無力感を打ち破る\n\nあとがき'
                '\n\n世界英語研究の中で指摘されてきまし\n\nこれは、幻想5で論じる「比較修辞学」につながります。')
    book = Book(Path('/nonexistent'), {'page_0001': page, 'page_0002': contents})
    found = find_all(book)
    assert [f.kind for f in found] == ['split_sentence'] * 3
    assert apply(book.pages, found) == {'page_0001': (
        'いるのを見ると、そうなのかな、と思ってしまったりします。次の文です。\n\n'
        '吸い上げられるものもなくなる。そうなると今度は、若い人たちが正社員にならない。\n\n'
        '…・寝てしまったみたい…です\n\nいい言い訳を考えた。\n\n## 体全体で考える\n\nなぜ勝つことが大事か？')}


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
        ('stray_symbol', 'page_0003', ' iMac. Then > ', ' iMac. Then '),
    ]


def test_blockquotes_and_suspended_hyphens_are_left_alone():
    pages = {'page_0001': BODY + 'an eight- by twenty-foot coop\n\n> I called up Bill and said, “I’m going to turn this.”'}
    assert fixes(pages) == []


def test_odd_case_inside_hyphenated_word():
    pages = {'page_0001': BODY + 'slow WiFi-only access, then WiFi again, and a lone wiFi'}
    assert fixes(pages) == [('odd_case', 'page_0001', 'wiFi', 'WiFi')]


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


def test_decorative_digits_in_chapter_references_are_put_back():
    from proofread import Book, decorative_digit
    pages = {'page_0012': 'Chapters 6, Z, and & apply the principles. In Chapter Z, we turn to books. '
                          'Generation Z and R&D stay, as do 3 & 4 apples.'}
    found = list(decorative_digit(Book(Path('.'), pages)))
    assert [(f.before, f.after) for f in found] == [('Chapters 6, Z, and &', 'Chapters 6, 7, and 8'), ('Chapter Z', 'Chapter 7')]


def test_stray_symbol_spares_code_and_fixes_the_one_it_found():
    # Python Crash Course: the fix removed the first "> " on the page (a >>> prompt) and the match stayed, three rounds.
    code = {'page_0001': BODY + '>>> age = 19\n\n>>> age > 21\nFalse\n\n<h3 > No topics</h3>'}
    assert fixes(code) == []
    prose = {'page_0001': BODY + 'He typed > here, then > there.'}
    found = list(find_all(Book(Path('/nonexistent'), prose)))
    assert [f.before for f in found] == ['y. He typed > ', ' here, then > ']
    fixed = apply(prose, found)['page_0001']
    assert fixed.endswith('He typed here, then there.') and '>' not in fixed


def test_python_prompts_that_lost_a_chevron_are_put_back():
    # Python Crash Course: 73 prompts read ">>age = 19" after the old stray_symbol fix, beside 54 whole ones.
    whole = ''.join(f'>>> x{i} = {i}\n' for i in range(10))
    pages = {'page_0001': BODY + whole + '>>age = 19\n>> > sum(digits)\n>>> fine()\n> >> odd()\n- >>car = 1 True\n'}
    assert [(f.before, f.after) for f in find_all(Book(Path('/nonexistent'), pages)) if f.kind == 'repl_prompt'] == [
        ('>>age = 19', '>>> age = 19'), ('>> > sum(digits)', '>>> sum(digits)'), ('> >> odd()', '>>> odd()'),
        ('>>car = 1 True', '>>> car = 1 True')]
    prose = {'page_0001': BODY + '>>age = 19\n'}  # no prompts to go by: left alone
    assert not [f for f in find_all(Book(Path('/nonexistent'), prose)) if f.kind == 'repl_prompt']


def test_a_section_number_over_an_epigraph_becomes_a_heading():
    # In the Plex: each chapter's sections are numbered 1, 2, 3 and open with a quote; nobody marked the numbers.
    pages = {'page_0001': '# PROLOGUE\n\n' + BODY,
             'page_0002': BODY + '\n\nIt cost $21\n\n1\n\n> "It was science fiction more than computer science."\n\n' + BODY}
    found = [f for f in find_all(Book(Path('/nonexistent'), pages)) if f.kind in ('section_number', 'heading_noise')]
    assert [(f.kind, f.after[:7]) for f in found] == [('section_number', '## 1\n\n>')]
    fixed = apply(pages, found)['page_0002']
    assert 'It cost $21\n\n## 1\n\n> "It was science' in fixed
    assert not [f for f in find_all(Book(Path('/nonexistent'), {**pages, 'page_0002': fixed}))
                if f.kind in ('section_number', 'heading_noise')]


def test_code_blocks_are_not_proofread():
    # Python Crash Course: `age < 21` and a dash in code are the program's; a stray < in the prose still goes.
    code = '```\n>>> age < 21\nFalse\n>>> name = first-the\n```'
    pages = {'page_0001': code + '\n\n' + BODY + 'Compare ages. < Then go on.'}
    assert fixes(pages) == [('stray_symbol', 'page_0001', 'mpare ages. < ', 'mpare ages. ')]
    fixed = apply(pages, find_all(Book(Path('/nonexistent'), pages)))
    assert fixed == {'page_0001': code + '\n\n' + BODY + 'Compare ages. Then go on.'}


def test_a_fix_lands_where_it_was_found_and_chevron_prompts_are_whole_again():
    # Automate the Boring Stuff, 2026-10-11: stray_symbol's text ran across a code block taken out for the detectors,
    # so its fix found nothing; repl_prompt's fix went to the first ">> " on the page, inside a whole prompt, and added a
    # > there each round. The chevrons were prompts: "›› › spam" is ">>> spam".
    prompts = ''.join(f'>>> x{i} = {i}\n' for i in range(10))
    page = (prompts + 'Enter your text.\n\n```\n>>> spam = input()\n```\n\n›› › spam\n\n'
            ">>> phoneNumRegex.findall('Cell: 415')\n› >> phoneNumRegex.findall('Cell: 415')\n" + BODY)
    pages = {'page_0001': page}
    for _ in range(3):
        found = [f for f in find_all(Book(Path('/nonexistent'), pages)) if f.kind in ('stray_symbol', 'repl_prompt')]
        pages = {**pages, **apply(pages, found)}
    fixed = pages['page_0001']
    assert '```\n>>> spam = input()\n```\n\n>>> spam\n' in fixed
    assert fixed.count(">>> phoneNumRegex.findall('Cell: 415')\n") == 2 and '>>>>' not in fixed
    assert not [f for f in find_all(Book(Path('/nonexistent'), pages)) if f.kind in ('stray_symbol', 'repl_prompt')]


def test_a_prompt_with_extra_chevrons_from_the_old_fix_is_put_back():
    prompts = ''.join(f'>>> x{i} = {i}\n' for i in range(10))
    pages = {'page_0001': prompts + ">>>>>> phoneNumRegex.findall('Cell')\n" + BODY}
    fixed = apply(pages, find_all(Book(Path('/nonexistent'), pages)))['page_0001']
    assert ">>> phoneNumRegex.findall('Cell')\n" in fixed and '>>>>' not in fixed


def test_prompts_in_code_blocks_are_mended_and_a_shift_is_not_a_prompt():
    prompts = ''.join(f'>>> x{i} = {i}\n' for i in range(10))
    pages = {'page_0001': prompts + '```\n>> > spam = int(spam)\n>>> y = x >> 2\n```\n\n' + BODY}
    fixed = apply(pages, find_all(Book(Path('/nonexistent'), pages)))['page_0001']
    assert '```\n>>> spam = int(spam)\n>>> y = x >> 2\n```' in fixed
