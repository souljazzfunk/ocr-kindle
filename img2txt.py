#!/usr/bin/env python3
"""
Turn a folder of captured Kindle pages into one Markdown book.

Each page is `<prefix>_<number>.png` with an optional sibling `.txt` holding that page's Markdown.
The Chrome extension's ZIP carries the `.txt` from Cloud Reader's text layer. Pages without one are OCRed
with Gemini, falling back to macOS Vision, so re-running only touches pages that are still missing text.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from pathlib import Path
from statistics import median

from google import genai
from google.genai import types

import locks

CONFIG_PATH = Path(os.environ.get('KINDLE_OCR_CONFIG', Path(__file__).with_name('config.env')))
# Most accurate first. The free tier allows gemini-3.8-flash only 20 requests a day, so the lite model takes over.
# Gemini refuses well-known books with RECITATION; local Vision OCR has no such filter but loses heading marks.
DEFAULT_MODELS = 'gemini-3.8-flash,gemini-3.5-flash-lite,vision'
VISION = 'vision'
VISION_INDENT = 0.015
PARAGRAPH_GAP = 1.4  # a gap this many line pitches wide ends a paragraph (reference entries: 1.5)
ROW = 1e-4  # pieces of one printed line are this close in top (same_line)
LEVEL = 0.5  # pieces whose middles are this many line heights apart, or less, are one line
# Vision gives no heading marks, and its line heights vary by half again within one paragraph, so a heading is
# found by its place on the page: set apart from the body (centred, or in a larger type), with space above or below.
HEADING_RATIO = 1.5
# Taller than this, a line is cover type, handwriting in a photo or a rotated chart label, not a heading.
MAX_HEADING_RATIO = 2.5
SET_APART = 0.1  # left edge this far right of the body margin: centred or displayed
BODY_LINE_CHARS = 30
MIN_BODY_LINES = 3  # a page without body text (cover, title page, figure) gets no headings
MAX_HEADINGS = 3  # more set-apart lines than this on one page are a figure, a table or a photo
SPACED = 1.8  # a heading has this many line pitches of space above or below it
# Decorative heading digits Vision reads as letters (How to Write a Lot: 7 as Z, 8 as &).
DECORATIVE_DIGITS = str.maketrans('Z&', '78')
HEADING_NUMBER = re.compile(r'^((?:Chapter|Part|Step|No\.?)\s+)?([0-9Z&]{1,3})$', re.I)
LINE_END_HYPHEN = re.compile(r'([A-Za-z]+)-$')
SPELL_LOCK = threading.Lock()
WORKERS = 4  # Gemini requests at once; the free tier's rate limit, not the Mac, is the bound
# Vision runs on the Mac, one page per core. Separate processes, so the Python half of each page (line boxes to
# Markdown) does not queue behind one interpreter lock. At 4 threads the 999 screens of Automate the Boring Stuff
# went at about 100 a minute on an 8-core Mac.
VISION_WORKERS = os.cpu_count() or WORKERS
PAGE_NUMBER = re.compile(r'_(\d+)\.png$')
SENTENCE_END = tuple('。．.!?！？」』）)”')
MIN_BODY_CHARS = 100
FULL_LINE = 0.85  # a line this share of the body's median length runs to the margin
FULL_LINE_TAIL = 400
# Which reader produced each page's .txt, one `page<TAB>source` line per write; the last line for a page wins.
SOURCES = 'ocr_sources.tsv'
GEMINI_FAILED_EXIT = 3  # a Gemini request failed: capture_book.py stops the list instead of failing the book
GEMINI_ERROR = 'gemini_error.txt'  # why, for capture_book.py
TEXT_LAYER = 'textlayer'
PICTURE = 'picture'  # a page Vision found no words on, in a book it reads
NO_TEXT = 'no text found'
LIVETEXT = 'livetext'  # a page Live Text (tools/livetext.swift) read more of than Vision
LIVETEXT_CHECK = 400  # Vision text shorter than this, or with any Japanese, is checked against Live Text
LIVETEXT_MARGIN = 1.2
CJK = re.compile('[\u3040-\u30ff\u4e00-\u9fff]')
LIVETEXT_SOURCE = Path(__file__).resolve().parent / 'tools' / 'livetext.swift'
PICTURE_SHARE = 0.5  # below half the book: photos in a book Vision reads (For 91 Days in Oviedo: 49 of 265)
LINES = '.lines.json'  # Vision's line boxes for a page, for layout.py
# Live Text's own reading of a page, for layout.py: the .txt is layout's output, so a second layout read from it lost
# what the first took off (英語教育幻想, 2026-10-12: with 73 「十」 gone, its 28 「＋」 fell short of a section mark).
LIVE = '.live.txt'

OCR_PROMPT = """Transcribe the book text on this Kindle page image exactly as printed.

- Copy the text verbatim. Never summarize, paraphrase, translate, correct, or add text.
- Japanese vertical text (縦書き) reads top to bottom, columns from right to left.
- Skip running headers, page numbers, reading progress ("位置", "%", "minutes left") and app UI.
- Output Markdown: `#`/`##` for chapter and section titles, a blank line between paragraphs.
- Keep a leading full-width space (　) where a paragraph starts with an indent.
- Drop furigana (ruby). For a figure or photo without text write `![short description in the book's language]()`.
- If the page has no text, output nothing."""


def load_config():
    if not CONFIG_PATH.exists():
        return {}
    config = {}
    for line in CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            config[key.strip()] = value.strip().strip('"').strip("'")
    return config


def page_images(folder):
    pages = [p for p in folder.glob('*.png') if PAGE_NUMBER.search(p.name)]
    return sorted(pages, key=lambda p: int(PAGE_NUMBER.search(p.name).group(1)))


def has_text(image):
    text_path = image.with_suffix('.txt')
    return text_path.exists() and text_path.stat().st_size > 0


def ocr_page(client, models, image):
    errors = []
    for model in models:
        try:
            text, model = vision_transcribe(image) if model == VISION else (transcribe(client, model, image), model)
        except Exception as e:
            errors.append(f'{model}: {e}')
            continue
        # A blank page still gets a non-empty file so later runs treat it as done.
        image.with_suffix('.txt').write_text(text or ' ', encoding='utf-8')
        return model, len(text)
    raise RuntimeError(' | '.join(errors))


def ocr_job(client, models, image):
    """One page for the worker pool: (image, (model, chars), None) or (image, None, error)."""
    try:
        return image, ocr_page(client, models, image), None
    except Exception as e:
        return image, None, e


def ocr_pool(models):
    """Vision alone runs in one process per core; anything with Gemini stays at WORKERS threads."""
    return (ProcessPoolExecutor, VISION_WORKERS) if models == [VISION] else (ThreadPoolExecutor, WORKERS)


def vision_lines(image, region=None):
    """Recognized lines as (left, top, height, text), normalized to the image with the origin at top left.

    region (left, top, right, bottom, in the same terms) reads only that part of the page.
    """
    import Vision
    from Foundation import NSURL
    from Quartz import CGRectMake
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setAutomaticallyDetectsLanguage_(True)
    x, y, w, h = 0, 0, 1, 1
    if region:
        left, top, right, bottom = region
        x, y, w, h = left, 1 - bottom, right - left, bottom - top  # Vision's origin is at the bottom left
        request.setRegionOfInterest_(CGRectMake(x, y, w, h))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(image)), None)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(error)
    lines = []
    for result in request.results():
        box = result.boundingBox()  # origin at bottom left, relative to the region
        lines.append((x + box.origin.x * w, 1 - y - (box.origin.y + box.size.height) * h, box.size.height * h,
                      result.topCandidates_(1)[0].string()))
    return same_line(lines)


def same_line(lines):
    """Put the pieces of one printed line in reading order. Vision can split a widely spaced line into pieces whose
    tops differ a little, so sorting by top shuffled them (AI for Educators: "I've had when discussing This is the
    visceral AI and its reaction"). Pieces whose middles are level share the top of the row, a hair apart from left
    to right, so every later sort by top reads them in order. They stay apart: a chart's labels are pieces too."""
    rows = []
    for line in sorted(lines, key=lambda line: line[0]):
        middle = line[1] + line[2] / 2
        row = next((row for row in rows if abs(row[0][1] + row[0][2] / 2 - middle) < LEVEL * min(row[0][2], line[2])),
                   None)
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    ordered = []
    for row in rows:
        top = min(piece[1] for piece in row)
        ordered += [(piece[0], top + k * 1e-6, piece[2], piece[3]) for k, piece in enumerate(row)]
    return ordered


def livetext_binary(source=LIVETEXT_SOURCE):
    """tools/livetext built from its Swift source on this Mac, again whenever the source is newer."""
    binary = source.with_suffix('')
    if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
        tmp = binary.with_name(f'.{binary.name}.{os.getpid()}')
        subprocess.run(['swiftc', '-O', str(source), '-o', str(tmp)], check=True, capture_output=True)
        os.replace(tmp, binary)  # parallel workers may build it at once; each rename is whole
    return binary


def livetext(image, run=subprocess.run, binary=None):
    """Live Text's transcript of a page, for pages Vision finds no lines on."""
    done = run([str(binary or livetext_binary()), str(image)], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f'livetext: {done.stderr.strip() or done.returncode}')
    return done.stdout.strip()


def vision_transcribe(image, read_lines=None, read_livetext=None):
    """(text, reader): Vision's lines with headings, or Live Text's transcript when it reads more of the page.

    Vision returns no lines on vertical Japanese, or only the few horizontal pieces on such a page: on 英語教育幻想
    (2026-10-12) it read one 「ジ」 of a full vertical page, and an English quote of 6 lines above vertical body text.
    So Live Text also reads every page whose Vision text is short or Japanese, and wins when it reads clearly more.
    Live Text gives no line boxes, so its pages get no layout from layout.py."""
    boxes = (read_lines or vision_lines)(image)
    text = vision_markdown(vision_headings(boxes)) if boxes else ''
    live = ''
    if (sys.platform == 'darwin' or read_livetext) and (len(text) < LIVETEXT_CHECK or CJK.search(text)):
        live = (read_livetext or livetext)(image)
    if live and (not boxes or chars(live) > LIVETEXT_MARGIN * chars(text) + 10):
        image.with_suffix(LIVE).write_text(live, encoding='utf-8')
        return live, LIVETEXT
    if not boxes:
        raise RuntimeError(NO_TEXT)  # an empty result must not mark the page done
    image.with_suffix(LINES).write_text(json.dumps(boxes, ensure_ascii=False), encoding='utf-8')
    return text, VISION


def restore_live(images, sources, read=None):
    """Read again with Live Text the pages it read before LIVE was kept, so their layout starts from its reading. A page
    it cannot read now keeps its layout from the .txt."""
    restored = []
    for image in images:
        if sources.get(image.stem) != LIVETEXT or image.with_suffix(LIVE).exists():
            continue
        try:
            image.with_suffix(LIVE).write_text((read or livetext)(image), encoding='utf-8')
        except RuntimeError as e:
            print(f'{image.name}: {e}; laid out from its .txt', flush=True)
            continue
        restored.append(image)
    return restored


def chars(text):
    return len(re.sub(r'\s', '', text))


def vision_headings(lines):
    """Turn (left, top, height, text) boxes into (left, top, text), marking chapter and section titles as headings.

    A chapter opening (a number over a title, How to Write a Lot) becomes one `# 7 Writing Books`;
    other titles become `##`. Cover, title and figure pages, and lines in a photo, get none.
    """
    lines = sorted(lines, key=lambda line: line[1])
    marks, pitch = heading_lines(lines)
    marked = []
    i = 0
    while i < len(lines):
        left, top, _, text = lines[i]
        text = text.strip()
        i += 1
        if i - 1 not in marks:
            marked.append((left, top, text))
            continue
        number = HEADING_NUMBER.match(text)
        if number:
            text = (number.group(1) or '') + number.group(2).translate(DECORATIVE_DIGITS)
        # The title under a chapter number, and the second line of a title that wraps (no space between), join it.
        under_number = bool(number)
        while i in marks and not HEADING_NUMBER.match(lines[i][3].strip()) and (
                under_number or lines[i][1] - lines[i - 1][1] <= SPACED * pitch):
            text = f'{text} {lines[i][3].strip()}'
            i += 1
            under_number = False
        marked.append((left, top, ('# ' if number else '## ') + text))
    return marked


def heading_lines(lines):
    """Indexes of the lines (sorted by top) that are titles, and the body's line pitch."""
    margin = min((left for left, _, _, _ in lines), default=0)
    body = [line for line in lines if line[0] - margin < SET_APART / 2 and len(line[3]) >= BODY_LINE_CHARS]
    if len(body) < MIN_BODY_LINES:
        return set(), 0
    # Body text starts at the margin; a page with many lines away from it holds a photo, a chart or a table.
    if sum(left - margin > SET_APART for left, _, _, _ in lines) > MAX_HEADINGS:
        return set(), 0
    size = median(height for _, _, height, _ in body)
    pitch = median(b[1] - a[1] for a, b in zip(body, body[1:]))
    marks = set()
    for i, (left, top, height, text) in enumerate(lines):
        text = text.strip()
        set_apart = left - margin > SET_APART or height >= HEADING_RATIO * size
        above = top - lines[i - 1][1] if i else 1
        below = lines[i + 1][1] - top if i + 1 < len(lines) else 1
        if (set_apart and height <= MAX_HEADING_RATIO * size and max(above, below) > SPACED * pitch
                and looks_like_title(text)):
            marks.add(i)
    return (marks if len(marks) <= MAX_HEADINGS else set()), pitch


def looks_like_title(text):
    """A title starts with a capital or a number and does not stop mid-sentence."""
    if HEADING_NUMBER.match(text):
        return True
    first = text.lstrip('"\'“‘(')[:1]
    return (bool(first) and len(text) <= 80 and (first.isupper() or first.isdigit())
            and not text.endswith((',', ';', '-')) and not re.match(r'(?:FIGURE|TABLE|Figure|Table)\b', text))


def vision_markdown(lines, pitch=None):
    """Rebuild paragraphs from (left, top, text) line boxes: a wide gap, a heading, or a line indented after one that
    ends a sentence starts a new one. A line indented after one that runs on is a hanging indent (AI for Educators
    sets its dialogue that way), so it continues the paragraph; a line back at the first line's place after a
    hanging indent starts the next (the entries of a reference list)."""
    lines = sorted(lines, key=lambda line: line[1])
    pitch = pitch or median([b[1] - a[1] for a, b in zip(lines, lines[1:]) if b[1] - a[1] > ROW] or [1])
    paragraphs = []
    previous = None
    previous_heading = False
    starts = []  # left of each line of the current paragraph
    for left, top, text in lines:
        piece = previous is not None and top - previous[1] <= ROW  # the rest of a printed line
        # Titles, block quotes and image notes stand alone; a list item starts a paragraph of its own.
        heading = re.match(r'#+ |> |!\[|```', text) is not None
        indented = (not piece and previous is not None and left - previous[0] > VISION_INDENT
                    and previous[2].rstrip().endswith(SENTENCE_END))
        hanging = (not piece and len(starts) >= 2 and starts[1] - starts[0] > VISION_INDENT
                   and abs(left - starts[0]) < VISION_INDENT / 2)
        if (previous is None or heading or previous_heading or indented or hanging or text.startswith('- ')
                or top - previous[1] > PARAGRAPH_GAP * pitch):
            paragraphs.append(text)
            starts = [left]
        else:
            paragraphs[-1] = join_lines(paragraphs[-1], text)
            if not piece:
                starts.append(left)
        previous = (left, top, text)
        previous_heading = heading
    return '\n\n'.join(paragraphs)


def join_lines(left, right):
    """Join text broken at a line or page end, undoing a hyphenation unless it splits a compound (twenty-eight)."""
    head = LINE_END_HYPHEN.search(left)
    tail = re.match(r'[a-z]+', right)
    if head and tail and is_word(head.group(1) + tail.group()):
        return left[:-1] + right
    if head:
        return left + right
    return left + ('' if not left[-1].isascii() and not right[0].isascii() else ' ') + right


def is_word(word):
    from AppKit import NSSpellChecker
    from Foundation import NSNotFound
    with SPELL_LOCK:  # NSSpellChecker is not thread-safe
        found, _ = NSSpellChecker.sharedSpellChecker().checkSpellingOfString_startingAt_language_wrap_inSpellDocumentWithTag_wordCount_(
            word, 0, 'en', False, 0, None)
    return found.location == NSNotFound


def transcribe(client, model, image):
    response = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=image.read_bytes(), mime_type='image/png'), OCR_PROMPT],
        config=types.GenerateContentConfig(
            temperature=0, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
    )
    reason = response.candidates[0].finish_reason if response.candidates else 'no candidates'
    if reason != types.FinishReason.STOP:
        raise RuntimeError(f'finish_reason={reason}')
    return (response.text or '').strip('\n ')


def record_sources(folder, sources):
    with open(folder / SOURCES, 'a', encoding='utf-8') as f:
        f.writelines(f'{page}\t{source}\n' for page, source in sources)


def read_sources(folder):
    path = folder / SOURCES
    if not path.exists():
        return {}
    return dict(line.split('\t') for line in path.read_text(encoding='utf-8').splitlines() if '\t' in line)


def ends_open(image, text):
    """Whether the page's last line runs to the right margin, so its paragraph may go on overleaf; None when Vision's
    line boxes are not kept. A short last line ends the paragraph ("First printing: March 2023" on a copyright page
    ran into the dedication overleaf)."""
    path = image.with_suffix(LINES)
    if not path.exists():
        return None
    lines = [line[3].strip() for line in sorted(json.loads(path.read_text(encoding='utf-8')), key=lambda l: l[1])]
    body = [len(line) for line in lines if len(line) >= BODY_LINE_CHARS]
    if len(body) < MIN_BODY_LINES:
        return False
    tail = text[-FULL_LINE_TAIL:]
    last = next((line for line in reversed(lines) if line and line in tail), None)
    return None if last is None else len(last) >= FULL_LINE * median(body)


def write_book(folder, title):
    images = page_images(folder)
    texts = [img.with_suffix('.txt').read_text(encoding='utf-8') for img in images]
    book = join_pages(texts, [ends_open(img, text) for img, text in zip(images, texts)])
    output = folder / f'{title}.md'
    output.write_text(book, encoding='utf-8')
    print(f'Wrote {output} ({len(book)} chars)')


def join_pages(texts, open_ends=None):
    """Concatenate pages, gluing a sentence that runs over a page break back together.

    open_ends says for each page whether its last line runs to the margin (ends_open); False keeps the next page apart.
    """
    book = ''
    previous_len = 0
    previous_open = None
    for text, open_end in zip(texts, open_ends or [None] * len(texts)):
        text = text.strip('\n')
        if not text.strip():
            continue
        # Title pages and colophons are short and end without punctuation, but never continue; nor does a title
        # at the foot of a page (AI for Educators: "## CHESS" ran into the next page's first line), nor code.
        ends_in_title = book.rstrip().rsplit('\n\n', 1)[-1].startswith('#') or book.rstrip().endswith('```')
        continues = (previous_len >= MIN_BODY_CHARS and not ends_in_title and previous_open is not False and not text.startswith(('　', '#', '[', '![', '> ', '- ', '```'))
                     and (not book.rstrip().endswith(SENTENCE_END) or text[:1].islower()))
        if continues:
            book = join_lines(book.rstrip(), text.lstrip())
        else:
            book = (book.rstrip() + '\n\n' if book else '') + text
        previous_len = len(text)
        previous_open = open_end
    return book + '\n'


def main():
    parser = argparse.ArgumentParser(description='OCR captured Kindle pages and assemble one Markdown file')
    parser.add_argument('folder', type=Path, help='<book>.zip from the extension, or a folder of captured page images')
    parser.add_argument('--title', help='Output file name without extension (default: folder name)')
    parser.add_argument('--model', help=f'Comma-separated models tried in order, Gemini names or "vision" (default: GEMINI_MODEL in config.env or {DEFAULT_MODELS})')
    parser.add_argument('--force', action='store_true', help='Re-OCR pages that already have text')
    parser.add_argument('--layout', choices=['gemini', 'sonnet'], default=None,
                        help='Use only Gemini or only Sonnet. Default: LAYOUT_PROVIDERS in config.env, Sonnet first')
    args = parser.parse_args()
    config = load_config()
    locks.keep(locks.book_lock(args.folder))
    if args.folder.suffix == '.zip':
        folder = args.folder.with_suffix('')
        archive = zipfile.ZipFile(args.folder)
        archive.extractall(folder)
        record_sources(folder, [(Path(name).stem, TEXT_LAYER) for name in archive.namelist() if name.endswith('.txt')])
        args.folder = folder

    images = page_images(args.folder)
    if not images:
        sys.exit(f'No page images found in {args.folder}')
    todo = [img for img in images if args.force or not has_text(img)]
    print(f'{len(images)} pages, {len(images) - len(todo)} already have text, {len(todo)} to OCR')
    api_key = config.get('GEMINI_API_KEY')
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=3, initial_delay=2, max_delay=30))) if api_key else None
    read_now = set()

    if todo:
        models = (args.model or config.get('GEMINI_MODEL') or DEFAULT_MODELS).split(',')
        if models != [VISION] and not client:
            sys.exit(f'GEMINI_API_KEY is not set in {CONFIG_PATH}')

        failed = []
        executor, workers = ocr_pool(models)
        print(f'OCR with {workers} {"processes" if executor is ProcessPoolExecutor else "threads"}', flush=True)
        with executor(workers) as pool:
            for done, (image, result, error) in enumerate(pool.map(partial(ocr_job, None if models == [VISION] else client, models), todo), 1):
                if error:
                    print(f'[{done}/{len(todo)}] {image.name} error: {error}', flush=True)
                    failed.append((image, error))
                    continue
                model, chars = result
                print(f'[{done}/{len(todo)}] {image.name} {chars} chars ({model})', flush=True)
                record_sources(args.folder, [(image.stem, model)])
                read_now.add(image.stem)
        # A photo or a blank page has no words for Vision to find (Dilla Time: a Rhodes piano on page 207). In a book
        # Vision reads on most pages, such pages are pictures, even many of them in a travel book; a book where most
        # pages fail is vertical Japanese, which Vision cannot read, so those stay pending.
        blank = [image for image, error in failed if NO_TEXT in str(error)]
        if blank and models == [VISION] and len(blank) < PICTURE_SHARE * len(images):
            for image in blank:
                image.with_suffix('.txt').write_text('![Picture]()', encoding='utf-8')
            record_sources(args.folder, [(image.stem, PICTURE) for image in blank])
            print(f'{len(blank)} pages without words kept as pictures: {", ".join(i.stem for i in blank)}', flush=True)
            failed = [(image, error) for image, error in failed if image not in blank]
        if failed:
            sys.exit(f'{len(failed)} pages failed. Re-run the same command to retry only those pages.')

    # Pages read just now, and pages never laid out, take Gemini's layout; the rest keep their text and its proofreading.
    # So do the pages of a code book laid out before code was asked for (layout.code_rereads).
    from layout import GeminiFailed, claude_cli, code_rereads, mark_layout, provider_order, read_layout
    sources = read_sources(args.folder)
    done = read_layout(args.folder)
    again = code_rereads(args.folder, done)
    vision = [img for img in images if sources.get(img.stem) in (VISION, LIVETEXT)
              and (img.stem in read_now or img.stem not in done or img.stem in again)]
    cli = claude_cli()
    if args.layout == 'sonnet' and not cli:
        sys.exit('--layout sonnet needs the Claude Code CLI (claude) on PATH or in ~/.local/bin')
    order = provider_order(config, cli, None if args.layout == 'sonnet' else client, args.layout)
    if vision and order:
        try:
            left = mark_layout(args.folder, None if args.layout == 'sonnet' else client, vision, cli,
                               config=config, limit=args.layout)
        except GeminiFailed as e:
            print(e, flush=True)
            (args.folder / GEMINI_ERROR).write_text(str(e), encoding='utf-8')
            sys.exit(GEMINI_FAILED_EXIT)
        except RuntimeError as e:
            if not str(e).startswith(('CLAUDE_MODEL', 'Unknown layout', 'OpenAI')):
                raise
            print(e, flush=True)
            (args.folder / GEMINI_ERROR).write_text(str(e), encoding='utf-8')
            sys.exit(GEMINI_FAILED_EXIT)
        (args.folder / GEMINI_ERROR).unlink(missing_ok=True)
        print(f'Layout read on {len(vision) - len(left)} of {len(vision)} Vision and Live Text pages'
              + (f'; {len(left)} left, run again to retry' if left else ''), flush=True)
    elif vision:
        print(f'No layout provider is available: {len(vision)} Vision pages keep titles guessed from layout')
    # Live Text pages already laid out are written again from layout.json, with no request: a new livetext_markdown
    # (section marks, contents pages) then reaches books converted before it.
    kept = [img for img in images if sources.get(img.stem) == LIVETEXT and img.stem in done and img not in vision]
    if kept and sys.platform == 'darwin' and restore_live(kept, sources):
        print('Live Text read again on the pages laid out before its reading was kept', flush=True)
    if kept:
        mark_layout(args.folder, None, kept)

    write_book(args.folder, args.title or args.folder.resolve().name)


if __name__ == '__main__':
    main()
