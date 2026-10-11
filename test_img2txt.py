import os
import subprocess
import sys
import zipfile

import pytest
from pathlib import Path

from img2txt import join_pages, vision_headings, vision_markdown

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


def test_join_pages_breaks_after_closing_quote_unless_sentence_continues():
    body = 'Brand sees Jobs as one of the purest embodiments of the cultural mix that the catalog sought to celebrate. '
    pages = [body + '“He got the notion of tools for human use.”', 'Brand’s catalog was published.', body + '“Stay hungry,”', 'he said.']
    assert join_pages(pages) == (body + '“He got the notion of tools for human use.”\n\nBrand’s catalog was published.\n\n'
                                 + body + '“Stay hungry,” he said.\n')


def test_join_pages_keeps_short_title_pages_separate():
    assert join_pages(['筑摩ｅブックス', '〈お断り〉', '幻想1　アメリカ英語', '私たちの日常生活は']) == '筑摩ｅブックス\n\n〈お断り〉\n\n幻想1　アメリカ英語\n\n私たちの日常生活は\n'


def test_vision_markdown_rebuilds_paragraphs_from_line_boxes():
    pytest.importorskip('AppKit')  # join_lines spell-checks with NSSpellChecker
    lines = [
        (0.121, 0.30, 'Apple II.'),
        (0.121, 0.10, 'Mike Markkula'),
        (0.152, 0.25, 'Bushnell suggested a former mar-'),
        (0.121, 0.20, 'All of this required'),
        (0.119, 0.275, 'keting manager at IBM-'),
        (0.121, 0.225, 'money.'),
        (0.121, 0.325, 'He was thirty-'),
        (0.121, 0.35, 'three.'),
    ]
    assert vision_markdown(lines) == (
        'Mike Markkula\n\nAll of this required money.\n\n'
        'Bushnell suggested a former marketing manager at IBM-Apple II. He was thirty-three.')


# Line boxes shaped like How to Write a Lot's, as Vision returns them: (left, top, height, text).
BODY_LINES = [(0.023, 0.573 + i * 0.038, 0.0276 + (i % 3) * 0.002, f'Body text of the chapter, line number {i} of this page')
              for i in range(8)]


def test_vision_headings_join_a_chapter_number_and_its_title_and_fix_decorative_digits():
    lines = [(0.484, 0.170, 0.0392, 'Z'), (0.380, 0.257, 0.0334, 'Writing Books')] + BODY_LINES
    marked = vision_headings(lines)
    assert marked[0] == (0.484, 0.170, '# 7 Writing Books')
    assert [t for _, _, t in marked[1:]] == [t for *_, t in BODY_LINES]


def test_vision_headings_mark_section_titles_and_join_a_wrapped_one():
    lines = [(0.214, 0.104, 0.0325, 'WHAT SHOULD I DO? SETTING GOALS'), (0.301, 0.161, 0.0262, 'AND TRACKING PROGRESS')]
    lines += [(left, top - 0.3, h, t) for left, top, h, t in BODY_LINES]
    assert vision_headings(lines)[0] == (0.214, 0.104, '## WHAT SHOULD I DO? SETTING GOALS AND TRACKING PROGRESS')


def test_vision_headings_skip_cover_photo_and_mid_sentence_lines():
    cover = [(0.379, 0.270, 0.1119, 'How'), (0.316, 0.419, 0.1163, 'Write'), (0.522, 0.509, 0.1541, 'Iot'),
             (0.382, 0.920, 0.0557, 'Paul J. Silvia, PhD')]
    assert not any(t.startswith('#') for _, _, t in vision_headings(cover))  # no body text on the page
    photo = [(0.260, 0.0, 0.0992, 'JAN 30 GONS'), (0.188, 0.119, 0.0412, '• Tom'), (0.542, 0.300, 0.1262, 'Persevere'),
             (0.432, 0.528, 0.1359, '• read paper on'), (0.224, 0.487, 0.0538, 'Draft')]
    photo += [(left, top + 0.2, h, t) for left, top, h, t in BODY_LINES[:6]]
    assert not any(t.startswith('#') for _, _, t in vision_headings(photo))
    # A body line Vision boxes taller than its neighbours, in the middle of a paragraph.
    tall = BODY_LINES[:4] + [(0.024, 0.725, 0.0403, 'a wayward writing group, like hanging out with the')] + [
        (left, top + 0.2, h, t) for left, top, h, t in BODY_LINES[4:]]
    assert not any(t.startswith('#') for _, _, t in vision_headings(tall))


def test_vision_headings_leave_a_page_of_one_size_alone():
    lines = [(0.1, 0.1, 0.02, 'Z'), (0.1, 0.2, 0.02, 'and more text')]
    assert vision_headings(lines) == [(0.1, 0.1, 'Z'), (0.1, 0.2, 'and more text')]


def test_vision_markdown_keeps_headings_on_their_own_lines():
    lines = [(0.40, 0.10, '# 7 Finding Time'), (0.30, 0.12, '## WHY WRITE'), (0.12, 0.14, 'Writing is a skill.')]
    assert vision_markdown(lines) == '# 7 Finding Time\n\n## WHY WRITE\n\nWriting is a skill.'


def test_join_pages_undoes_hyphenation_across_pages():
    pytest.importorskip('AppKit')
    body = 'Valentine, however, did not become a preeminent Silicon Valley investor by relying on surface appearances. '
    pages = [body + 'He manipu-', 'lating everyone. ' + body + 'twenty-', 'eight times.']
    assert join_pages(pages) == body + 'He manipulating everyone. ' + body + 'twenty-eight times.\n'


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


def test_join_pages_keeps_figure_notes_apart():
    body = 'It was the best of times, it was the worst of times, it was the age of wisdom, it was the age of wisdom, it was the age of'
    assert join_pages([body, '![Jobs in 1982]()', 'foolishness.']) == body + '\n\n![Jobs in 1982]()\n\nfoolishness.\n'


def test_vision_pages_run_one_process_per_core_and_gemini_stays_at_its_rate_limit(monkeypatch):
    import img2txt
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
    assert img2txt.ocr_pool(['vision']) == (ProcessPoolExecutor, img2txt.VISION_WORKERS)
    assert img2txt.VISION_WORKERS == (os.cpu_count() or img2txt.WORKERS)
    assert img2txt.ocr_pool(['gemini-3.8-flash', 'vision']) == (ThreadPoolExecutor, img2txt.WORKERS)


def test_a_page_job_runs_in_a_separate_process(tmp_path):
    # The job must pickle for a process pool: a module-level function, with the page result or the error.
    import img2txt
    from concurrent.futures import ProcessPoolExecutor
    from functools import partial
    image = tmp_path / 'page_0001.png'
    image.write_bytes(b'not an image')
    with ProcessPoolExecutor(2) as pool:
        [(page, result, error)] = list(pool.map(partial(img2txt.ocr_job, None, ['no-such-model']), [image]))
    assert page == image and result is None and 'no-such-model' in str(error)


def test_a_few_pages_without_words_are_pictures_but_a_book_of_them_stays_pending(tmp_path, monkeypatch):
    import img2txt

    def ocr_job(client, models, image):
        if image.stem in ('page_0001', 'page_0207') or 'vertical' in str(image):
            return image, None, RuntimeError(img2txt.NO_TEXT)
        image.with_suffix('.txt').write_text('Words on the page.')
        return image, ('vision', 18), None

    monkeypatch.setattr(img2txt, 'ocr_job', ocr_job)
    monkeypatch.setattr(img2txt, 'ocr_pool', lambda models: (img2txt.ThreadPoolExecutor, 2))
    monkeypatch.setattr(img2txt, 'load_config', lambda: {})
    book = tmp_path / 'Dilla Time'
    book.mkdir()
    for n in range(1, 241):
        (book / f'page_{n:04d}.png').write_bytes(b'png')
    monkeypatch.setattr(sys, 'argv', ['img2txt.py', str(book), '--model', 'vision'])
    img2txt.main()  # Dilla Time: a photo of a Rhodes piano did not fail the book
    assert (book / 'page_0207.txt').read_text() == '![Picture]()'
    assert img2txt.read_sources(book)['page_0207'] == img2txt.PICTURE
    vertical = tmp_path / 'vertical'
    vertical.mkdir()
    for n in range(1, 4):
        (vertical / f'page_{n:04d}.png').write_bytes(b'png')
    monkeypatch.setattr(sys, 'argv', ['img2txt.py', str(vertical), '--model', 'vision'])
    with pytest.raises(SystemExit):
        img2txt.main()
    assert not list(vertical.glob('*.txt'))


def test_a_photo_heavy_travel_book_keeps_its_photos_as_pictures(tmp_path, monkeypatch):
    # 2026-10-11: For 91 Days in Oviedo had 49 photo pages of 265 and failed the 5% limit.
    import img2txt

    def ocr_job(client, models, image):
        if int(image.stem[-4:]) % 5 == 0:
            return image, None, RuntimeError(img2txt.NO_TEXT)
        image.with_suffix('.txt').write_text('Words on the page.')
        return image, ('vision', 18), None

    monkeypatch.setattr(img2txt, 'ocr_job', ocr_job)
    monkeypatch.setattr(img2txt, 'ocr_pool', lambda models: (img2txt.ThreadPoolExecutor, 2))
    monkeypatch.setattr(img2txt, 'load_config', lambda: {})
    book = tmp_path / 'For 91 Days in Oviedo'
    book.mkdir()
    for n in range(1, 266):
        (book / f'page_{n:04d}.png').write_bytes(b'png')
    monkeypatch.setattr(sys, 'argv', ['img2txt.py', str(book), '--model', 'vision'])
    img2txt.main()
    assert (book / 'page_0010.txt').read_text() == '![Picture]()'


def test_join_pages_glues_nothing_to_code():
    # Python Crash Course: a listing at the foot of a page and the text overleaf stay apart.
    body = 'Python ignores the first line and executes the second line, which you can see in the output below'
    assert join_pages([body + '\n\n```\nprint("Hello")\n```', 'and so on.', body, '```\nHello\n```']) == (
        body + '\n\n```\nprint("Hello")\n```\n\nand so on.\n\n' + body + '\n\n```\nHello\n```\n')
    assert vision_markdown([(0.08, 0.1, 'For example:'), (0.1, 0.13, '```\n>>> age = 18\nTrue\n```'),
                            (0.08, 0.2, 'that runs on')]) == 'For example:\n\n```\n>>> age = 18\nTrue\n```\n\nthat runs on'
