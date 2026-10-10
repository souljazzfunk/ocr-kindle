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
        answer = [{'page': s, 'titles': [{'line': 0, 'level': 2}], 'quotes': [], 'figures': [], 'art': []} for s in stems]
        return SimpleNamespace(text=json.dumps(answer))

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    monkeypatch.setattr(layout, 'BATCH', 8)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    images = sorted(tmp_path.glob('page_*.png'))
    assert mark_layout(tmp_path, client, images) == []
    assert sorted(len(batch) for batch in asked) == [2, 8] and len(read_layout(tmp_path)) == 10
    assert (tmp_path / 'page_0001.txt').read_text().startswith('## A TITLE\n\nBody text')
    assert mark_layout(tmp_path, client, images) == [] and len(asked) == 2  # kept in layout.json



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
        return SimpleNamespace(text=json.dumps([{'page': s, 'titles': [{'line': 0, 'level': 2}]} for s in stems]))

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
        stems = re.findall(r'Page "([^"]+)": image (\S+)', input)
        assert all(Path(path).exists() for _, path in stems)  # Sonnet opens the image itself
        answer = [{'page': s, 'titles': [{'line': 0, 'level': 2}], 'quotes': [], 'figures': [], 'art': []} for s, _ in stems]
        reply = {'type': 'result', 'is_error': False, 'result': 'Here it is:\n```json\n' + json.dumps(answer) + '\n```'}
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr='')

    monkeypatch.setattr(layout.types.Part, 'from_bytes', lambda data, mime_type: b'')
    monkeypatch.setattr(layout.time, 'sleep', lambda s: pytest.fail('waited on a daily limit'))
    monkeypatch.setattr(layout.subprocess, 'run', run)
    monkeypatch.setattr(layout, 'WORKERS', 1)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    assert mark_layout(tmp_path, client, sorted(tmp_path.glob('page_*.png')), cli='claude') == []
    assert len(calls) == 2 and calls[0][:4] == ['claude', '-p', '--model', 'sonnet']
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
