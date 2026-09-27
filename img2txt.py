#!/usr/bin/env python3
"""
Turn a folder of captured Kindle pages into one Markdown book.

Each page is `<prefix>_<number>.png` with an optional sibling `.txt` holding that page's Markdown.
The Chrome extension's ZIP carries the `.txt` from Cloud Reader's text layer. Pages without one are OCRed
with Gemini, falling back to macOS Vision, so re-running only touches pages that are still missing text.
"""

import argparse
import os
import re
import shutil
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median

from google import genai
from google.genai import types

CONFIG_PATH = Path(os.environ.get('KINDLE_OCR_CONFIG', Path(__file__).with_name('config.env')))
# Most accurate first. The free tier allows gemini-3.8-flash only 20 requests a day, so the lite model takes over.
# Gemini refuses well-known books with RECITATION; local Vision OCR has no such filter but loses heading marks.
DEFAULT_MODELS = 'gemini-3.8-flash,gemini-3.5-flash-lite,vision'
VISION = 'vision'
VISION_INDENT = 0.015
LINE_END_HYPHEN = re.compile(r'([A-Za-z]+)-$')
SPELL_LOCK = threading.Lock()
WORKERS = 4
PAGE_NUMBER = re.compile(r'_(\d+)\.png$')
SENTENCE_END = tuple('。．.!?！？」』）)”')
MIN_BODY_CHARS = 100
# Which reader produced each page's .txt, one `page<TAB>source` line per write; the last line for a page wins.
SOURCES = 'ocr_sources.tsv'
TEXT_LAYER = 'textlayer'

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
            text = vision_transcribe(image) if model == VISION else transcribe(client, model, image)
        except Exception as e:
            errors.append(f'{model}: {e}')
            continue
        # A blank page still gets a non-empty file so later runs treat it as done.
        image.with_suffix('.txt').write_text(text or ' ', encoding='utf-8')
        return model, len(text)
    raise RuntimeError(' | '.join(errors))


def vision_lines(image):
    """Recognized lines as (left, top, height, text), normalized to the image with the origin at top left."""
    import Vision
    from Foundation import NSURL
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setAutomaticallyDetectsLanguage_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(image)), None)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(error)
    lines = []
    for result in request.results():
        box = result.boundingBox()  # origin at bottom left
        lines.append((box.origin.x, 1 - box.origin.y - box.size.height, box.size.height, result.topCandidates_(1)[0].string()))
    return lines


def vision_transcribe(image):
    lines = [(left, top, text) for left, top, _, text in vision_lines(image)]
    # Vision finds nothing in vertical Japanese, so an empty result must not mark the page done.
    if not lines:
        raise RuntimeError('no text found')
    return vision_markdown(lines)


def vision_markdown(lines):
    """Rebuild paragraphs from (left, top, text) line boxes: an indented line or a wide gap starts a new one."""
    lines = sorted(lines, key=lambda line: line[1])
    margin = min(left for left, _, _ in lines)
    pitch = median([b[1] - a[1] for a, b in zip(lines, lines[1:])] or [1])
    paragraphs = []
    previous_top = None
    for left, top, text in lines:
        if previous_top is None or left - margin > VISION_INDENT or top - previous_top > 1.5 * pitch:
            paragraphs.append(text)
        else:
            paragraphs[-1] = join_lines(paragraphs[-1], text)
        previous_top = top
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


def write_book(folder, title, config):
    book = join_pages(img.with_suffix('.txt').read_text(encoding='utf-8') for img in page_images(folder))
    output = folder / f'{title}.md'
    output.write_text(book, encoding='utf-8')
    print(f'Wrote {output} ({len(book)} chars)')
    drive_folder = config.get('GOOGLE_DRIVE_FOLDER')
    if drive_folder and Path(drive_folder).is_dir():
        shutil.copy2(output, drive_folder)
        print(f'Copied to {drive_folder}')


def join_pages(texts):
    """Concatenate pages, gluing a sentence that runs over a page break back together."""
    book = ''
    previous_len = 0
    for text in texts:
        text = text.strip('\n')
        if not text.strip():
            continue
        # Title pages and colophons are short and end without punctuation, but never continue.
        continues = (previous_len >= MIN_BODY_CHARS and not text.startswith(('　', '#', '[', '!['))
                     and (not book.rstrip().endswith(SENTENCE_END) or text[:1].islower()))
        if continues:
            book = join_lines(book.rstrip(), text.lstrip())
        else:
            book = (book.rstrip() + '\n\n' if book else '') + text
        previous_len = len(text)
    return book + '\n'


def main():
    parser = argparse.ArgumentParser(description='OCR captured Kindle pages and assemble one Markdown file')
    parser.add_argument('folder', type=Path, help='<book>.zip from the extension, or a folder of captured page images')
    parser.add_argument('--title', help='Output file name without extension (default: folder name)')
    parser.add_argument('--model', help=f'Comma-separated models tried in order, Gemini names or "vision" (default: GEMINI_MODEL in config.env or {DEFAULT_MODELS})')
    parser.add_argument('--force', action='store_true', help='Re-OCR pages that already have text')
    args = parser.parse_args()
    config = load_config()
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

    if todo:
        models = (args.model or config.get('GEMINI_MODEL') or DEFAULT_MODELS).split(',')
        client = None
        if models != [VISION]:
            api_key = config.get('GEMINI_API_KEY')
            if not api_key:
                sys.exit(f'GEMINI_API_KEY is not set in {CONFIG_PATH}')
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=3, initial_delay=2, max_delay=30)))

        def run(image):
            try:
                return image, ocr_page(client, models, image), None
            except Exception as e:
                return image, None, e

        failed = []
        with ThreadPoolExecutor(WORKERS) as pool:
            for done, (image, result, error) in enumerate(pool.map(run, todo), 1):
                if error:
                    print(f'[{done}/{len(todo)}] {image.name} error: {error}', flush=True)
                    failed.append(image)
                    continue
                model, chars = result
                print(f'[{done}/{len(todo)}] {image.name} {chars} chars ({model})', flush=True)
                record_sources(args.folder, [(image.stem, model)])
        if failed:
            sys.exit(f'{len(failed)} pages failed. Re-run the same command to retry only those pages.')

    write_book(args.folder, args.title or args.folder.resolve().name, config)


if __name__ == '__main__':
    main()
