import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import layout
from img2txt import join_pages, same_line, vision_markdown
from layout import mark_layout, page_markdown, read_layout, styled_titles
from proofread import Book, apply, find_all

# Line boxes shaped like AI for Educators' pages, as Vision returns them: (left, top, height, text).
BODY = [(0.023, 0.30 + i * 0.038, 0.027, f'Body text of the section, a full line of running text number {i} and on')
        for i in range(6)]


def page(*lines):
    return sorted(lines, key=lambda line: line[1])


def test_titles_quotes_lists_and_art_take_their_place():
    boxes = page((0.066, 0.05, 0.016, '1 0 1 1 0'), (0.093, 0.10, 0.031, 'WHAT ARE CHEATING AND PLAGIARISM ANYMORE?'),
                 (0.038, 0.17, 0.022, 'ENCOURAGING ACADEMIC HONESTY'), (0.066, 0.21, 0.016, '0'),
                 (0.20, 0.25, 0.027, 'We must use our "tomorrow glasses."'),
                 *BODY, (0.10, 0.55, 0.027, '► Our future decisions are informed by past decisions.'))
    layout_ = {'titles': [{'line': 1, 'level': 1}, {'line': 2, 'level': 2}], 'quotes': [4], 'figures': [], 'art': [0]}
    text = page_markdown(boxes, layout_)
    assert text.startswith('# WHAT ARE CHEATING AND PLAGIARISM ANYMORE?\n\n## ENCOURAGING ACADEMIC HONESTY\n\n'
                           '> We must use our "tomorrow glasses."\n\nBody text')
    assert text.endswith('\n\n- Our future decisions are informed by past decisions.')
    assert '1 0 1' not in text and '\n0\n' not in text


def test_a_wrapped_title_joins_but_a_quote_marked_as_a_title_stays_apart():
    boxes = page((0.148, 0.10, 0.027, '22. CREATE REVIEW CONTENT, QUESTIONS, AND ACTIVITIES'), (0.159, 0.135, 0.025, 'WITH AI.'),
                 (0.03, 0.20, 0.027, 'Ideas are nothing without vision and execution.'),
                 (0.038, 0.25, 0.027, 'RECOGNIZE THE SPECIAL VALUE OF OUR HUMANITY.'), *BODY)
    # Gemini named only the first line of the wrapped title, and took the pull quote for a title.
    text = page_markdown(boxes, {'titles': [{'line': 0, 'level': 2}, {'line': 2, 'level': 2}, {'line': 3, 'level': 2}]})
    assert text.startswith('## 22. CREATE REVIEW CONTENT, QUESTIONS, AND ACTIVITIES WITH AI.\n\n'
                           '## Ideas are nothing without vision and execution.\n\n'
                           '## RECOGNIZE THE SPECIAL VALUE OF OUR HUMANITY.\n\n')


def test_the_contents_page_settles_chapter_levels_and_its_entries_are_a_list():
    contents = page((0.1, 0.1, 0.03, 'Contents'), (0.1, 0.2, 0.025, '1. Welcome to the AI Revolution'),
                    (0.1, 0.25, 0.025, '4. 30 Ways Al Can Support Teaching and Learning'))
    text = page_markdown(contents, {'titles': [{'line': 0, 'level': 1}, {'line': 1, 'level': 1}]})
    assert text == '# Contents\n\n- 1. Welcome to the AI Revolution\n\n- 4. 30 Ways Al Can Support Teaching and Learning'
    chapters = ['welcometotheairevoiution', 'waysaicansupportteachingandiearning']  # as contents_entries keeps them
    opening = page((0.085, 0.10, 0.032, '30 WAYS AI CAN SUPPORT TEACHING AND LEARNING'),
                   (0.156, 0.20, 0.027, '17. USE AI TO IMPROVE VOCABULARY.'), *BODY)
    # Gemini's levels drift between batches; the contents page decides.
    marks = {'titles': [{'line': 0, 'level': 2}, {'line': 1, 'level': 1}]}
    assert page_markdown(opening, marks, chapters).startswith(
        '# 30 WAYS AI CAN SUPPORT TEACHING AND LEARNING\n\n## 17. USE AI TO IMPROVE VOCABULARY.\n\n')


def test_a_chart_becomes_a_note_but_a_text_box_keeps_its_words():
    chart = [(0.3 + 0.1 * (i % 3), 0.62 + 0.03 * i, 0.02, label) for i, label in enumerate(
        ['VISIBILITY', 'Peak of Inflated Expectations', 'Plateau of Productivity', 'Trough of Disillusionment', 'TIME'])]
    boxes = page(*BODY, (0.3, 0.58, 0.027, 'The Gartner Hype Cycle'), *chart)
    figure = {'lines': [7, 8, 9, 10, 11], 'description': 'A hype cycle chart'}
    text = page_markdown(boxes, {'titles': [{'line': 6, 'level': 2}], 'figures': [figure]})
    assert text.endswith('\n\n![The Gartner Hype Cycle: A hype cycle chart]()') and 'Trough' not in text
    definition = page(*BODY)
    kept = page_markdown(definition, {'figures': [{'lines': [0, 1, 2, 3, 4, 5], 'description': 'Definition box'}]})
    assert 'Definition box' not in kept and kept.count('Body text') == 6


def test_drop_caps_give_the_first_word_back():
    cap = page((0.026, 0.205, 0.096, 'S'), (0.108, 0.200, 0.028, 'ome say: Block it. Ban it. Stop the cheating.'), *BODY)
    # Gemini read the word without its initial; the tall S is put back and leaves no stray letter.
    text = page_markdown(cap, {'drop_cap': {'line': 0, 'word': 'ome'}})
    assert text.startswith('Some say: Block it.') and '\n\nS\n' not in text
    lost = page((0.121, 0.10, 0.026, 'uturists and education thought leaders have talked'), *BODY)
    assert page_markdown(lost, {'drop_cap': {'line': 1, 'word': 'Futurists'}}).startswith('Futurists and education')
    beside = page((0.026, 0.836, 0.094, 'A'), (0.163, 0.831, 0.029, 'artificial intelligence tools have developed'))
    assert page_markdown(beside, {'drop_cap': {'line': 1, 'word': 'As'}}).startswith('As artificial intelligence')
    assert page_markdown(cap, {}).startswith('Some say')  # no word from Gemini: the tall letter alone


def test_titles_styled_like_most_others_are_titles(tmp_path):
    marked = {}
    for n in range(6):
        stem = f'page_{n:04d}'
        (tmp_path / f'{stem}.lines.json').write_text(json.dumps([[0.038, 0.1, 0.027, f'SECTION TITLE NUMBER {n}']] + BODY))
        marked[stem] = {'titles': [{'line': 0, 'level': 2}] if n else [], 'quotes': [0] if not n else []}
    assert styled_titles(tmp_path, marked) == {'page_0000': [{'line': 0, 'level': 2}]}
    # Sidebar labels centred in a box share no style with enough titles, so they stay as Gemini left them.
    (tmp_path / 'page_0009.lines.json').write_text(json.dumps([[0.376, 0.1, 0.027, 'KEY DEFINITION']] + BODY))
    assert 'page_0009' not in styled_titles(tmp_path, {**marked, 'page_0009': {}})


def test_layout_is_asked_once_per_page_in_batches(tmp_path, monkeypatch):
    for n in range(1, 11):
        (tmp_path / f'page_{n:04d}.png').write_bytes(b'png')
        (tmp_path / f'page_{n:04d}.lines.json').write_text(json.dumps([[0.1, 0.1, 0.03, 'A TITLE']] + BODY))
    asked = []

    def generate_content(model, contents, config):
        stems = [part.split('"')[1] for part in contents if isinstance(part, str) and part.startswith('Page "')]
        asked.append(stems)
        assert config.response_schema is layout.SCHEMA
        answer = [{'page': s, 'titles': [{'line': 0, 'level': 2}], 'quotes': [], 'figures': [], 'art': []} for s in stems]
        return SimpleNamespace(text=json.dumps({'pages': answer}))

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    monkeypatch.setattr(layout, 'BATCH', 8)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    images = sorted(tmp_path.glob('page_*.png'))
    assert mark_layout(tmp_path, client, images) == []
    assert sorted(len(batch) for batch in asked) == [2, 8] and len(read_layout(tmp_path)) == 10
    assert (tmp_path / 'page_0001.txt').read_text().startswith('## A TITLE\n\nBody text')
    assert mark_layout(tmp_path, client, images) == [] and len(asked) == 2  # kept in layout.json



def test_a_live_text_page_takes_its_titles_from_gemini(tmp_path, monkeypatch):
    # 2026-10-12: 英語教育幻想 came out with 0 headings; its Live Text pages had no layout, so 「十日米同盟」 stayed a paragraph.
    (tmp_path / 'page_0011.png').write_bytes(b'png')
    (tmp_path / 'page_0011.txt').write_text('十日米同盟\nここからは私見です。この文書で英語の位置が語られます。\n', encoding='utf-8')
    asked = []

    def generate_content(model, contents, config):
        asked.append([part for part in contents if isinstance(part, str) and part.startswith('0: ')])
        return SimpleNamespace(text=json.dumps({'pages': [{'page': 'page_0011', 'titles': [{'line': 0, 'level': 2}],
                                                           'quotes': [], 'figures': [], 'art': [], 'code': []}]}))

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    images = [tmp_path / 'page_0011.png']
    assert mark_layout(tmp_path, client, images) == []
    assert asked == [['0: 十日米同盟\n1: ここからは私見です。この文書で英語の位置が語られます。']]
    assert (tmp_path / 'page_0011.txt').read_text(encoding='utf-8') == '## 十日米同盟\n\nここからは私見です。この文書で英語の位置が語られます。'
    (tmp_path / 'layout.json').unlink()
    assert mark_layout(tmp_path, client, images) == []  # laid out again, the marks are not doubled
    assert (tmp_path / 'page_0011.txt').read_text(encoding='utf-8').startswith('## 十日米同盟\n\n')


def test_live_text_titles_lose_the_section_mark_and_take_their_level_from_the_contents(tmp_path, monkeypatch):
    # 2026-10-12: 英語教育幻想's 73 titles kept 「十」 (its 「†」), its contents entries came out as titles, and some
    # section titles came out at level 1.
    pages = {'page_0001': ['目次', '幻想1 英語は世界の共通語である 13', '幻想2 ネイティブの英語が正しい 41'],
             'page_0002': ['幻想3 英語は早く始めるほどよい 70', '主要参考・引用文献 240']}
    body = 'ここからは私見です。この文書で英語の位置が語られます。'
    for n in range(3, 23):
        pages[f'page_{n:04d}'] = (['幻想1 英語は世界の共通語である'] if n == 3 else []) + [f'十見出し{n}', body]
    for stem, lines in pages.items():
        (tmp_path / f'{stem}.png').write_bytes(b'png')
        (tmp_path / f'{stem}.txt').write_text('\n'.join(lines), encoding='utf-8')

    def generate_content(model, contents, config):
        stems = [part.split('"')[1] for part in contents if isinstance(part, str) and part.startswith('Page "')]
        return SimpleNamespace(text=json.dumps({'pages': [{'page': s, 'quotes': [], 'figures': [], 'art': [], 'code': [],
                                                           'titles': [{'line': i, 'level': 1} for i, line in enumerate(pages[s])
                                                                      if line != body]} for s in stems]}))

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    assert mark_layout(tmp_path, client, sorted(tmp_path.glob('page_*.png'))) == []
    read = lambda stem: (tmp_path / f'{stem}.txt').read_text(encoding='utf-8')
    assert read('page_0001') == '# 目次\n\n幻想1 英語は世界の共通語である 13\n\n幻想2 ネイティブの英語が正しい 41'
    assert read('page_0002') == '幻想3 英語は早く始めるほどよい 70\n\n主要参考・引用文献 240'
    assert read('page_0003') == f'# 幻想1 英語は世界の共通語である\n\n## 見出し3\n\n{body}'
    assert read('page_0004') == f'## 見出し4\n\n{body}'


def test_gemini_s_daily_limit_is_not_waited_out_and_stops_after_writing_what_was_laid_out(tmp_path, monkeypatch):
    # 2026-10-10: the free tier's 500 requests a day ran out mid-list, and every later page failed its layout.
    for n in range(1, 31):
        (tmp_path / f'page_{n:04d}.png').write_bytes(b'png')
        (tmp_path / f'page_{n:04d}.lines.json').write_text(json.dumps([[0.1, 0.1, 0.03, 'A TITLE']] + BODY))
    asked = []

    def generate_content(model, contents, config):
        stems = [part.split('"')[1] for part in contents if isinstance(part, str) and part.startswith('Page "')]
        asked.append(stems)
        if len(asked) > 1:
            raise RuntimeError('429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier')
        return SimpleNamespace(text=json.dumps({'pages': [{'page': s, 'titles': [{'line': 0, 'level': 2}]} for s in stems]}))

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    monkeypatch.setattr(layout.time, 'sleep', lambda s: pytest.fail('waited on a daily limit'))
    monkeypatch.setattr(layout, 'WORKERS', 1)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    with pytest.raises(layout.GeminiFailed, match='10 pages left.*midnight Pacific'):
        mark_layout(tmp_path, client, sorted(tmp_path.glob('page_*.png')))
    assert len(asked) == 2 and len(read_layout(tmp_path)) == 20
    assert (tmp_path / 'page_0001.txt').read_text().startswith('## A TITLE')


def test_any_failed_gemini_request_stops_the_book_without_sending_the_rest(tmp_path, monkeypatch):
    for n in range(1, 61):
        (tmp_path / f'page_{n:04d}.png').write_bytes(b'png')
        (tmp_path / f'page_{n:04d}.lines.json').write_text(json.dumps(BODY))
    asked = []

    def generate_content(model, contents, config):
        asked.append(1)
        raise RuntimeError('403 PERMISSION_DENIED API key not valid')

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    monkeypatch.setattr(layout, 'WORKERS', 1)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    with pytest.raises(layout.GeminiFailed, match='60 pages left: 403 PERMISSION_DENIED'):
        mark_layout(tmp_path, client, sorted(tmp_path.glob('page_*.png')))
    assert len(asked) == 1  # three batches, one request

def test_pieces_of_one_printed_line_read_left_to_right():
    pieces = [(0.44, 0.009, 0.026, "I've had when discussing"), (0.024, 0.010, 0.021, 'This is'),
              (0.145, 0.010, 0.021, 'the visceral'), (0.82, 0.010, 0.021, 'AI and its'), (0.318, 0.012, 0.019, 'reaction'),
              (0.023, 0.048, 0.028, 'implementation with friends, family, and colleagues.')]
    text = vision_markdown([(left, top, text) for left, top, _, text in same_line(pieces)])
    assert text == ("This is the visceral reaction I've had when discussing AI and its implementation with friends, "
                    'family, and colleagues.')


def test_hanging_indents_continue_a_paragraph_and_a_reference_list_splits():
    dialogue = [(0.121, 0.009, 'I have such mixed emotions about all of this AI stuff. I want to be'),
                (0.137, 0.049, "at the forefront, and I know that this is a big part of my students'"),
                (0.136, 0.090, "future. But there's a lot to be concerned about.")]
    assert vision_markdown(dialogue).count('\n') == 0
    references = [(0.023, 0.10, 'Gartner. (2022, May 17). Gartner Hype Cycle research methodology. Gartner.'),
                  (0.060, 0.14, 'Retrieved February 27, 2023, from https://www.gartner.com/en/research'),
                  (0.023, 0.18, 'Harwell, D. (2023, February 25). Tech\'s hottest new job: AI whisperer. No'),
                  (0.060, 0.22, 'coding required. The Washington Post.')]
    assert vision_markdown(references).count('\n\n') == 1


def test_a_page_ending_on_a_short_line_or_a_title_is_not_glued_to_the_next():
    body = 'Library of Congress Control Number: 2023933910 Paperback ISBN: 978-1-956306-47-7 and more text here'
    assert join_pages([body + ' First printing: March 2023', 'To the forward-thinking teachers.'], [False, None]) == (
        body + ' First printing: March 2023\n\nTo the forward-thinking teachers.\n')
    assert join_pages([body + ' and the chess\n\n## CHESS', '- The fears: chess was ruined.']) == (
        body + ' and the chess\n\n## CHESS\n\n- The fears: chess was ruined.\n')


def test_proofreading_fixes_al_for_ai_and_digits_for_ll():
    pages = {'page_0001': 'AI tools. AI assistants. AI is here. Also, an Al chatbot. Al-operated robots. Al Gore spoke.',
             'page_0002': "The more that Al becomes assimilated, the more that it'11 impact the classroom."}
    findings = find_all(Book(Path('/nonexistent'), pages))
    fixed = apply(pages, [f for f in findings if f.kind in ('l_for_i', 'digits_for_ll')])
    assert fixed['page_0001'] == 'AI tools. AI assistants. AI is here. Also, an AI chatbot. AI-operated robots. Al Gore spoke.'
    assert fixed['page_0002'] == "The more that AI becomes assimilated, the more that it'll impact the classroom."


def test_proofreading_fixes_al_in_a_title_cover_digits_and_a_stray_colon():
    pages = {'page_0001': 'AI tools. AI assistants. AI is here.\n\n- 4. 30 Ways Al Can Support Teaching and Learning',
             'page_0002': 'MATT MILLER AUTHOR OF DITCH THAT TEXTBOOK 1 10111010\n\nIn 2023 there were 100000 users.',
             'page_0003': 'Help ourselves thrive with this technology.:\n\nFor example: e.g.: stays.'}
    findings = find_all(Book(Path('/nonexistent'), pages))
    fixed = apply(pages, [f for f in findings if f.kind in ('l_for_i', 'binary_art', 'stray_colon')])
    assert fixed['page_0001'].endswith('- 4. 30 Ways AI Can Support Teaching and Learning')
    assert fixed['page_0002'] == 'MATT MILLER AUTHOR OF DITCH THAT TEXTBOOK\n\nIn 2023 there were 100000 users.'
    assert fixed['page_0003'] == 'Help ourselves thrive with this technology.\n\nFor example: e.g.: stays.'


def test_heading_noise_is_made_plain_text(tmp_path):
    (tmp_path / 'ocr_sources.tsv').write_text('page_0001\tvision\n')
    body = BODY[0][3] + ' ' + BODY[1][3]
    pages = {'page_0001': '## about the rates and slopes, people!\n\n## WHY WRITE\n\n' + body}
    findings = [f for f in find_all(Book(tmp_path, pages)) if f.kind == 'heading_noise']
    assert apply(pages, findings)['page_0001'] == 'about the rates and slopes, people!\n\n## WHY WRITE\n\n' + body


def test_sonnet_lays_out_the_pages_gemini_could_not_and_the_book_goes_on(tmp_path, monkeypatch):
    # 2026-10-10: Gemini's 500 requests a day ran out mid-list and the list waited for the next day.
    for n in range(1, 31):
        (tmp_path / f'page_{n:04d}.png').write_bytes(b'png')
        (tmp_path / f'page_{n:04d}.lines.json').write_text(json.dumps([[0.1, 0.1, 0.03, 'A TITLE']] + BODY))

    def generate_content(model, contents, config):
        raise RuntimeError('429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier')

    calls = []

    def run(command, input, **kwargs):
        calls.append(command)
        assert kwargs['env']['LLM_WIKI_WATCHER'] == '1'
        assert 'JSON array' not in input
        assert json.loads(command[command.index('--json-schema') + 1]) == layout.LAYOUT_SCHEMA
        stems = re.findall(r'Page "([^"]+)": image (\S+)', input)
        assert all(Path(path).exists() for _, path in stems)  # Sonnet opens the image itself
        pages = [{'page': s, 'titles': [{'line': 0, 'level': 2}], 'quotes': [], 'figures': [], 'art': []} for s, _ in stems]
        # The sentence after the array is the Steve Jobs reply. The layout is structured_output, level 2, not this.
        prose = json.dumps([{'page': s, 'titles': [{'line': 0, 'level': 1}]} for s, _ in stems])
        reply = {'type': 'result', 'is_error': False, 'structured_output': {'pages': pages},
                 'result': prose + '\n\nNote: on pages [1] and [2] the title is set [large].'}
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    monkeypatch.setattr(layout.time, 'sleep', lambda s: pytest.fail('waited on a daily limit'))
    monkeypatch.setattr(layout.subprocess, 'run', run)
    monkeypatch.setattr(layout, 'WORKERS', 1)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    assert mark_layout(tmp_path, client, sorted(tmp_path.glob('page_*.png')), cli='claude') == []
    assert len(calls) == 2 and calls[0][:4] == ['claude', '-p', '--model', 'sonnet']
    assert '{"disableAllHooks": true}' in calls[0]  # the LLM wiki logs no session for these calls
    saved = read_layout(tmp_path)
    assert len(saved) == 30 and all(page['model'] == 'claude' for page in saved.values())
    assert (tmp_path / 'page_0030.txt').read_text().startswith('## A TITLE')


def test_the_book_stops_when_sonnet_fails_too(tmp_path, monkeypatch):
    for n in range(1, 61):
        (tmp_path / f'page_{n:04d}.png').write_bytes(b'png')
        (tmp_path / f'page_{n:04d}.lines.json').write_text(json.dumps(BODY))
    calls = []

    def run(command, input, **kwargs):
        calls.append(1)
        return SimpleNamespace(returncode=1, stdout='', stderr='Claude usage limit reached')

    def generate_content(model, contents, config):
        raise RuntimeError('429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier')

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    monkeypatch.setattr(layout.subprocess, 'run', run)
    monkeypatch.setattr(layout, 'WORKERS', 1)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    with pytest.raises(layout.GeminiFailed, match='60 pages left: Gemini: 429.*Sonnet: .*usage limit.*midnight Pacific'):
        mark_layout(tmp_path, client, sorted(tmp_path.glob('page_*.png')), cli='claude')
    assert len(calls) == 1  # three batches, one call to each



def test_the_steve_jobs_reply_is_not_read_from_the_prose_around_the_array(tmp_path, monkeypatch):
    # 2026-10-12, Steve Jobs: result was the array plus a sentence ("Extra data"). Reading that sentence
    # as the layout gave up Sonnet for the book. Without structured_output the call has failed.
    for n in range(1, 61):
        (tmp_path / f'page_{n:04d}.png').write_bytes(b'png')
        (tmp_path / f'page_{n:04d}.lines.json').write_text(json.dumps([[0.1, 0.1, 0.03, 'A TITLE']] + BODY))
    calls = []

    def run(command, input, **kwargs):
        calls.append(command)
        stems = [s for s, _ in re.findall(r'Page "([^"]+)": image (\S+)', input)]
        answer = json.dumps([{'page': s, 'titles': [{'line': 0, 'level': 2}]} for s in stems])
        reply = {'type': 'result', 'is_error': False,
                 'result': answer + '\n\nNote: on pages [1] and [2] the title is set [large].'}
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')

    def generate_content(model, contents, config):
        raise RuntimeError('429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier')

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    monkeypatch.setattr(layout.subprocess, 'run', run)
    monkeypatch.setattr(layout, 'WORKERS', 1)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    with pytest.raises(layout.GeminiFailed, match='60 pages left'):
        mark_layout(tmp_path, client, sorted(tmp_path.glob('page_*.png')), cli='claude')
    assert len(calls) == 1 and '--json-schema' in calls[0]
    assert read_layout(tmp_path) == {}


# ask_layout_claude as of 4b6ab59 (PR #113). It asks for the array in prose and reads result.
STEVE_JOBS = r'''
def ask_layout_claude(cli, images):
    """Sonnet's reading of a batch of Vision pages through `claude -p`."""
    parts = [PROMPT, 'Open each page image with the Read tool before answering. Reply with only a JSON array, one '
             'object per page: {"page": "<name>", "titles": [{"line": n, "level": n}], "quotes": [n], '
             '"figures": [{"lines": [n], "description": "..."}], "art": [n], "code": [[n]], "drop_cap": {"line": n, "word": "..."} '
             'or null}.']
    for image in images:
        parts += [f'Page "{image.stem}": image {image.resolve()}',
                  '\n'.join(f'{i}: {line}' for i, line in enumerate(page_lines(image)))]
    run = subprocess.run(
        [cli, '-p', '--model', CLAUDE_MODEL, '--output-format', 'json', '--allowedTools', 'Read',
         '--add-dir', str(images[0].parent.resolve()), '--strict-mcp-config',
         '--settings', '{"disableAllHooks": true}'],
        input='\n\n'.join(parts), capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S,
        cwd=images[0].parent, env=dict(os.environ, LLM_WIKI_WATCHER='1'))
    reply = json.loads(run.stdout) if run.stdout.strip().startswith('{') else {}
    if run.returncode or reply.get('is_error') or 'result' not in reply:
        raise RuntimeError(f'claude -p exited {run.returncode}: {(reply.get("result") or run.stderr or run.stdout)[:300]}')
    stems = {image.stem for image in images}
    return {page.pop('page'): dict(page, model=CLAUDE) for page in json_array(reply['result'])
            if isinstance(page, dict) and page.get('page') in stems}
'''


def test_a_claude_call_without_json_schema_is_the_steve_jobs_reply():
    import importlib.util
    spec = importlib.util.spec_from_file_location('check_claude', Path(__file__).parent / 'scripts' / 'check_claude.py')
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    found = check.problems(STEVE_JOBS, 'layout.py')
    assert found == ['layout.py:11: ask_layout_claude calls claude -p without --json-schema; '
                     'call ask_claude, which passes the schema']
    assert layout.as_json(layout.SCHEMA) == layout.LAYOUT_SCHEMA
    assert check.main() == 0

# Line boxes shaped like Python Crash Course's pages (2026-10-11): body at 0.08, listings at 0.10, an indent 0.05.
PCC = [(0.08, 0.10, 0.022, 'In Python, the hash mark (#) indicates a comment. Anything following a hash mark in your'),
       (0.08, 0.135, 0.022, 'code is ignored by the Python interpreter. For example:'),
       (0.08, 0.20, 0.022, 'comment.py'), (0.10, 0.24, 0.022, '# Say hello to everyone.'),
       (0.10, 0.275, 0.022, 'print("Hello Python people!")'),
       (0.08, 0.33, 0.022, 'Python ignores the first line and executes the second line.'),
       (0.10, 0.38, 0.022, 'Hello Python people!')]


def test_code_is_fenced_line_by_line_and_never_a_title():
    # Gemini named the comment a title too; code wins. The program's output is a block of its own.
    text = page_markdown(page(*PCC), {'titles': [{'line': 3, 'level': 1}], 'code': [[3, 4], [6]]})
    assert text.endswith('For example:\n\ncomment.py\n\n```\n# Say hello to everyone.\nprint("Hello Python people!")'
                         '\n```\n\nPython ignores the first line and executes the second line.\n\n'
                         '```\nHello Python people!\n```')
    # An interpreter session keeps one line per line (it came out as ">>age = 18 >> age == 18 True").
    session = page((0.10, 0.10, 0.022, '>>> age = 18'), (0.10, 0.135, 0.022, '>>> age == 18'), (0.10, 0.17, 0.022, 'True'))
    assert page_markdown(session, {'code': [[0, 1, 2]]}) == '```\n>>> age = 18\n>>> age == 18\nTrue\n```'
    assert page_markdown(session, {}) == '>>> age = 18 >>> age == 18 True'  # what a layout without code gave


def test_code_keeps_its_indentation_from_the_left_edges():
    listing = page((0.101, 0.10, 0.022, "magicians = ['alice', 'david', 'carolina']"),
                   (0.100, 0.135, 0.022, 'for magician in magicians:'),
                   (0.151, 0.17, 0.022, 'print(f"{magician.title()}, that was a great trick!")'),
                   (0.150, 0.205, 0.022, 'if magician == "david":'), (0.199, 0.24, 0.022, 'print("Encore!")'),
                   (0.100, 0.275, 0.022, 'print("Thank you, everyone.")'),
                   # Vision split a line at the gap before its comment: one line again, the comment in its column.
                   (0.100, 0.31, 0.022, 'message = "Hi"'), (0.35, 0.31 + 1e-6, 0.022, '# greet'))
    assert page_markdown(listing, {'code': [list(range(8))]}) == (
        "```\nmagicians = ['alice', 'david', 'carolina']\nfor magician in magicians:\n"
        '    print(f"{magician.title()}, that was a great trick!")\n    if magician == "david":\n'
        '        print("Encore!")\nprint("Thank you, everyone.")\nmessage = "Hi"' + ' ' * 6 + '# greet\n```')


def test_only_the_code_pages_of_a_code_book_laid_out_before_code_are_read_again(tmp_path, monkeypatch):
    # Gemini allows 500 requests a day: of Python Crash Course's pages only those with code are asked again, once.
    session = [[0.10, 0.10 + i * 0.035, 0.022, line] for i, line in enumerate(
        ['>>> age = 18', '>>> age == 18', 'True', 'import this', 'def greet_user():'])]
    old = {}
    for n in range(1, 11):
        stem = f'page_{n:04d}'
        (tmp_path / f'{stem}.png').write_bytes(b'png')
        (tmp_path / f'{stem}.lines.json').write_text(json.dumps(session if n <= 5 else BODY))
        old[stem] = {'titles': [], 'quotes': [], 'figures': [], 'art': []}
    (tmp_path / 'layout.json').write_text(json.dumps(old))
    asked = []

    def generate_content(model, contents, config):
        stems = [part.split('"')[1] for part in contents if isinstance(part, str) and part.startswith('Page "')]
        asked.extend(stems)
        return SimpleNamespace(text=json.dumps({'pages': [{'page': s, 'titles': [], 'code': [0, 1, 2]} for s in stems]}))

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    images = sorted(tmp_path.glob('page_*.png'))
    assert layout.code_rereads(tmp_path, old) == {f'page_{n:04d}' for n in range(1, 6)}
    assert mark_layout(tmp_path, client, images) == [] and sorted(asked) == [f'page_{n:04d}' for n in range(1, 6)]
    assert read_layout(tmp_path)['page_0001']['code'] == [[0, 1, 2]]  # a flat answer is one group
    assert (tmp_path / 'page_0001.txt').read_text().startswith('```\n>>> age = 18\n>>> age == 18\nTrue\n```')
    assert mark_layout(tmp_path, client, images) == [] and len(asked) == 5
    # A book without code (fewer than CODE_BOOK_LINES lines like it) keeps every page's layout.
    for n in range(4, 11):
        (tmp_path / f'page_{n:04d}.lines.json').write_text(json.dumps(BODY))
    assert layout.code_rereads(tmp_path, old) == set()


def test_prose_does_not_look_like_code():
    prose = ['If you want to do this:', 'import goods from China', 'for the following reasons:', '> A pull quote',
             'Python ignores the first line.', '$5 for a coffee', 'print it out and sign it']
    assert not [line for line in prose if layout.CODE_LINE.match(line)]
    code = ['>>> age = 18', '›> print("Hello")', '>> > favorite_language', '$ python3 hello_world.py', 'import pygame',
            'from die import Die', 'def greet_user(username):', 'print ("Hi")', '# Say hello to everyone.',
            'for magician in magicians:']
    assert all(layout.CODE_LINE.match(line) for line in code)


def test_titles_read_with_a_plus_lose_it_and_a_line_the_contents_lists_becomes_a_chapter(tmp_path, monkeypatch):
    # 2026-10-12: 英語教育幻想 came out with 29 titles starting 「＋」 or 「+」 (its 「†」 again), and Gemini left
    # 幻想5 and 幻想6 as paragraphs, one of them cut short; the contents had read 幻想6 and 幻想7 as one line.
    pages = {'page_0001': ['目次', '幻想5 それぞれの国の文化や言語には独特さがある',
                           '幻想6 英語ができれば世界中だれとでも意思疎通できる幻想7 英語力は社会的・経済的成功をもたらす']}
    body = 'ここからは私見です。この文書で英語の位置が語られます。'
    titles = {}
    for n in range(2, 22):
        mark = '十＋+'[n % 3]
        lines = [f'{mark}見出し{n}', body]
        if n == 2:
            lines = ['幻想5それぞれの国の文化や言語には独特さがあ'] + lines
        if n == 3:
            lines = ['幻想6 英語ができれば世界中だれとでも意思疎通できる', '幻想6の冒頭で紹介した資料によると、英語は不可欠です。'] + lines
        pages[f'page_{n:04d}'] = lines
        titles[f'page_{n:04d}'] = [lines.index(f'{mark}見出し{n}')]
    for stem, lines in pages.items():
        (tmp_path / f'{stem}.png').write_bytes(b'png')
        (tmp_path / f'{stem}.txt').write_text('\n'.join(lines), encoding='utf-8')

    def generate_content(model, contents, config):
        stems = [part.split('"')[1] for part in contents if isinstance(part, str) and part.startswith('Page "')]
        return SimpleNamespace(text=json.dumps({'pages': [{'page': s, 'titles': [{'line': i, 'level': 2}
                                                                                 for i in titles.get(s, [])]} for s in stems]}))

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    assert mark_layout(tmp_path, client, sorted(tmp_path.glob('page_*.png'))) == []
    read = lambda stem: (tmp_path / f'{stem}.txt').read_text(encoding='utf-8')
    assert read('page_0002') == f'# 幻想5それぞれの国の文化や言語には独特さがあ\n\n## 見出し2\n\n{body}'
    assert read('page_0003') == (f'# 幻想6 英語ができれば世界中だれとでも意思疎通できる\n\n'
                                 f'幻想6の冒頭で紹介した資料によると、英語は不可欠です。\n\n## 見出し3\n\n{body}')
    assert read('page_0004') == f'## 見出し4\n\n{body}'


def test_a_chapter_number_read_as_a_symbol_takes_the_contents_number_and_a_run_on_title_stays_a_paragraph(tmp_path, monkeypatch):
    # 2026-10-12: 英語教育幻想's 「幻想8」 came out as 「# 幻想∞…」, and 「†海外の日本語教育事情も関係している」 ran into
    # the next paragraph and became one heading of 120 characters.
    pages = {'page_0001': ['目次', '幻想8 英語学習は幼少期からできるだけ早く始めた方がよい']}
    body = 'ここからは私見です。この文書で英語の位置が語られます。'
    run_on = '＋海外の日本語教育事情も関係しているこれまで見てきたように、海外で仕事をする際、使用する言語は必ずしも英語でないことがわかりました。'
    titles = {}
    for n in range(2, 10):
        lines = [f'十見出し{n}', body]
        if n == 2:
            lines = ['幻想∞英語学習は幼少期からできるだけ早く始めた方がよい'] + lines
        if n == 3:
            lines = lines + [run_on]
        pages[f'page_{n:04d}'] = lines
        titles[f'page_{n:04d}'] = [i for i, line in enumerate(lines) if line != body]
    for stem, lines in pages.items():
        (tmp_path / f'{stem}.png').write_bytes(b'png')
        (tmp_path / f'{stem}.txt').write_text('\n'.join(lines), encoding='utf-8')

    def generate_content(model, contents, config):
        stems = [part.split('"')[1] for part in contents if isinstance(part, str) and part.startswith('Page "')]
        return SimpleNamespace(text=json.dumps({'pages': [{'page': s, 'titles': [{'line': i, 'level': 2}
                                                                                 for i in titles.get(s, [])]} for s in stems]}))

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    assert mark_layout(tmp_path, client, sorted(tmp_path.glob('page_*.png'))) == []
    read = lambda stem: (tmp_path / f'{stem}.txt').read_text(encoding='utf-8')
    assert read('page_0002') == f'# 幻想8英語学習は幼少期からできるだけ早く始めた方がよい\n\n## 見出し2\n\n{body}'
    assert read('page_0003') == f'## 見出し3\n\n{body}\n\n{run_on[1:]}'


def test_a_second_layout_starts_from_live_text_s_reading_not_from_the_first_layout_s_output(tmp_path):
    # 2026-10-12: 英語教育幻想's first layout took 「十」 off 73 titles; laid out again from those .txt files, its 28
    # 「＋」 titles were too few to count as a section mark and kept it.
    import img2txt
    body = 'ここからは私見です。この文書で英語の位置が語られます。'
    layout_json = {}
    for n in range(1, 20):
        mark = '＋' if n <= 5 else '十'
        stem = f'page_{n:04d}'
        (tmp_path / f'{stem}.png').write_bytes(b'png')
        (tmp_path / f'{stem}{img2txt.LIVE}').write_text(f'{mark}見出し{n}\n{body}', encoding='utf-8')
        (tmp_path / f'{stem}.txt').write_text(f'## {"＋" if mark == "＋" else ""}見出し{n}\n\n{body}', encoding='utf-8')
        layout_json[stem] = {'titles': [{'line': 0, 'level': 2}]}
    (tmp_path / layout.LAYOUT).write_text(json.dumps(layout_json))
    assert mark_layout(tmp_path, None, sorted(tmp_path.glob('page_*.png'))) == []
    assert (tmp_path / 'page_0001.txt').read_text(encoding='utf-8') == f'## 見出し1\n\n{body}'
    assert (tmp_path / 'page_0006.txt').read_text(encoding='utf-8') == f'## 見出し6\n\n{body}'


def test_pages_live_text_read_before_its_reading_was_kept_are_read_again(tmp_path):
    import img2txt
    pages = [tmp_path / f'page_{n:04d}.png' for n in (1, 2, 3)]
    for page in pages:
        page.write_bytes(b'png')
    (tmp_path / f'page_0002{img2txt.LIVE}').write_text('kept', encoding='utf-8')
    sources = {'page_0001': img2txt.LIVETEXT, 'page_0002': img2txt.LIVETEXT, 'page_0003': img2txt.VISION}
    assert img2txt.restore_live(pages, sources, read=lambda image: f'read {image.stem}') == [pages[0]]

    def unreadable(image):
        raise RuntimeError(f'livetext: cannot read {image}')
    (tmp_path / f'page_0001{img2txt.LIVE}').rename(tmp_path / 'kept.txt')
    assert img2txt.restore_live(pages, sources, read=unreadable) == []
    (tmp_path / 'kept.txt').rename(tmp_path / f'page_0001{img2txt.LIVE}')
    assert (tmp_path / f'page_0001{img2txt.LIVE}').read_text(encoding='utf-8') == 'read page_0001'
    assert (tmp_path / f'page_0002{img2txt.LIVE}').read_text(encoding='utf-8') == 'kept'
