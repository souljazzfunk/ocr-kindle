#!/usr/bin/env python3
"""
Find the errors that capture and OCR leave in a book folder, show a sample of each kind, and fix them all.

Each detector finds one kind of error on every page and proposes the same fix for each hit (see README).
Hits on text-layer pages are false positives, since that text is exact, and are counted to show how far a kind
can be trusted.
"""

import argparse
import difflib
import html
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from img2txt import (TEXT_LAYER, VISION, load_config, page_images, read_sources, vision_lines, vision_transcribe,
                     write_book)

SAMPLES = 6
DUPLICATE_RATIO = 0.8
MIN_OVERLAP_WORDS = 8
OVERLAP_RATIO = 0.85
MIN_CROSS_READ_WORDS = 4
KINDLE_UI = ('Review this book on Amazon', 'Recommend this book', 'Follow the author', 'Before you go')
# Words that follow an em dash but almost never end a hyphenated compound.
FUNCTION_WORDS = frozenset('a an the and but or nor yet who whom whose which what that this these those he she it '
                           'they we you i me him her them us his its their our my your to as if when while where '
                           'not is was were are be been had has would could should will can did does'.split())
HOMOGLYPHS = str.maketrans('АВЕКМНОРСТХаеорсухіјѕԁ', 'ABEKMHOPCTXaeopcyxijsd')
CYRILLIC = re.compile('[\u0400-\u04ff]')
CJK = re.compile('[\u3040-\u30ff\u4e00-\u9fff]')
LATIN = re.compile('[A-Za-z]')
HYPHEN_BREAK = re.compile(r'(?<![\w-])([A-Za-z]+)-( ?)([a-z]+)(?![\w-])')
DASH = re.compile(r'(?<![\w-])([A-Za-z]+)-([A-Za-z]+)(?![\w-])')
ODD_CASE = re.compile(r'\b[A-Za-z]*[a-z][A-Z][A-Za-z]*\b')
STRAY_SYMBOL = re.compile(r'(?m)^[>›<‹]$\n?|(?<= )[>›<‹] ')
WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")


@dataclass(frozen=True)
class Finding:
    kind: str
    page: str
    before: str
    after: str | None  # None: report only

    def apply(self, text):
        return text.replace(self.before, self.after, 1)


@dataclass
class Book:
    folder: Path
    pages: dict  # page stem -> text
    _lines: dict = field(default_factory=dict)

    @cached_property
    def corpus(self):
        return '\n'.join(self.pages.values())

    @cached_property
    def counts(self):
        return Counter(w.lower() for w in WORD.findall(self.corpus))

    def lines(self, stem):
        """The page image's lines as Vision reads them, or [] when there is no readable image."""
        if stem not in self._lines:
            try:
                self._lines[stem] = vision_lines(self.folder / f'{stem}.png')
            except Exception:
                self._lines[stem] = []
        return self._lines[stem]


def words(text):
    """Words compared across readers: quotes unified, Markdown marks and Gemini's figure notes dropped."""
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text.replace('’', "'").replace('“', '"').replace('”', '"'))
    return re.sub(r'[#*_]', '', text).split()


def duplicate_page(book):
    pages = book.pages
    stems = [s for s in pages if pages[s].strip()]
    for previous, stem in zip(stems, stems[1:]):
        a, b = words(pages[previous]), words(pages[stem])
        if a and b and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= DUPLICATE_RATIO:
            yield Finding('duplicate_page', stem, pages[stem], ' ')


def seam_overlap(book):
    """A page that starts by repeating the end of the text before it; a new capture tool can restart a page or two
    back. Readers differ in quotes and dashes, so the repeat is matched approximately."""
    pages = book.pages
    stems = [s for s in pages if pages[s].strip()]
    for i, stem in enumerate(stems[1:], 1):
        before = [w for s in stems[max(0, i - 2):i] for w in words(pages[s])]
        tokens = [t for t in re.finditer(r'\S+', pages[stem]) if words(t.group())]
        head = [words(t.group())[0] for t in tokens]
        starts = [j for j in range(len(before) - MIN_OVERLAP_WORDS + 1) if before[j:j + 5] == head[:5]]
        for j in starts:
            n = len(before) - j
            if n < len(head) and difflib.SequenceMatcher(None, before[j:], head[:n], autojunk=False).ratio() >= OVERLAP_RATIO:
                end = tokens[n].start() if n < len(tokens) else len(pages[stem])
                yield Finding('seam_overlap', stem, pages[stem][:end], '')
                break


def foreign_page(book):
    book_is_cjk = len(CJK.findall(book.corpus)) > len(LATIN.findall(book.corpus))
    for stem, text in book.pages.items():
        cjk, latin = len(CJK.findall(text)), len(LATIN.findall(text))
        if cjk + latin and (cjk > latin) != book_is_cjk or sum(ui in text for ui in KINDLE_UI) >= 2:
            yield Finding('foreign_page', stem, text, ' ')


def hyphen_break(book):
    for stem, text in book.pages.items():
        for m in HYPHEN_BREAK.finditer(text):
            head, _, tail = m.groups()
            if should_join(book, head, tail) and not hyphenated_mid_line(book, stem, head, tail):
                yield Finding('hyphen_break', stem, m.group(), head + tail)


def should_join(book, head, tail):
    """Join when the book spells the word joined at least as often; the spell checker also accepts compounds that
    only look joinable (handholding, redhanded), so it is no help here."""
    joined = book.counts[(head + tail).lower()]
    return joined and joined >= book.counts[f'{head}-{tail}'.lower()]


def hyphenated_mid_line(book, stem, head, tail):
    """A hyphen inside a line of the page image is the book's own; at a line end it could be either."""
    return any(f'{head}-{tail}' in text for _, _, _, text in book.lines(stem))


def dash_as_hyphen(book):
    counts = book.counts
    for stem, text in book.pages.items():
        for m in DASH.finditer(text):
            head, tail = m.groups()
            if len(head) > 1 and tail.lower() in FUNCTION_WORDS and counts[m.group().lower()] == 1:
                yield Finding('dash_as_hyphen', stem, m.group(), f'{head}—{tail}')


def homoglyph(book):
    if len(CYRILLIC.findall(book.corpus)) * 100 > len(LATIN.findall(book.corpus)):
        return  # a book with real Cyrillic text
    for stem, text in book.pages.items():
        for m in re.finditer(r'\S*[\u0400-\u04ff]\S*', text):
            fixed = m.group().translate(HOMOGLYPHS)
            if not CYRILLIC.search(fixed):
                yield Finding('homoglyph', stem, m.group(), fixed)


def odd_case(book):
    spellings = defaultdict(Counter)
    for w in WORD.findall(book.corpus):
        spellings[w.lower()][w] += 1
    for stem, text in book.pages.items():
        for token in set(ODD_CASE.findall(text)):
            usual, count = spellings[token.lower()].most_common(1)[0]
            # NeXT and AirPort recur; a misread casing appears once.
            if usual != token and spellings[token.lower()][token] == 1 and count > 1:
                yield Finding('odd_case', stem, token, usual)


def stray_symbol(book):
    for stem, text in book.pages.items():
        for m in STRAY_SYMBOL.finditer(text):
            yield Finding('stray_symbol', stem, m.group(), '')


DETECTORS = [duplicate_page, seam_overlap, foreign_page, hyphen_break, dash_as_hyphen, homoglyph, odd_case, stray_symbol]


def cross_read(folder, pages, sources):
    """Compare each non-Vision page with a Vision reading of its image, cached next to it as .vision.txt."""
    for stem, text in pages.items():
        if sources.get(stem) == VISION or not text.strip():
            continue
        cache = folder / f'{stem}.vision.txt'
        if not cache.exists():
            try:
                cache.write_text(vision_transcribe(folder / f'{stem}.png'), encoding='utf-8')
            except RuntimeError:
                cache.write_text('', encoding='utf-8')
        a, b = words(text), words(cache.read_text(encoding='utf-8'))
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag != 'equal' and max(i2 - i1, j2 - j1) >= MIN_CROSS_READ_WORDS:
                yield Finding('cross_read', stem, ' '.join(a[i1:i2]) or '(missing)', ' '.join(b[j1:j2]) or '(missing)')


def load_pages(folder):
    return {img.stem: img.with_suffix('.txt').read_text(encoding='utf-8')
            for img in page_images(folder) if img.with_suffix('.txt').exists()}


def find_all(book):
    return [f for detect in DETECTORS for f in detect(book)]


def apply(pages, findings):
    changed = {}
    for f in findings:
        if f.after is not None:
            changed[f.page] = f.apply(changed.get(f.page, pages[f.page]))
    return changed


def evenly_spaced(items, n):
    if len(items) <= n:
        return items
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def context(text, snippet, width=160):
    at = max(text.find(snippet), 0)
    return text[max(0, at - width):at], text[at:at + len(snippet)], text[at + len(snippet):at + len(snippet) + width]


def letters(text):
    return re.sub(r'[^A-Za-z]', '', text)


def line_crop(book, finding, index):
    """Cut the line holding the finding out of the page image, so a sample is checked at a glance."""
    folder = book.folder
    image = folder / f'{finding.page}.png'
    text = book.pages[finding.page]
    # cross_read spans are normalized words, so they are matched on their own rather than in context.
    window = letters(''.join(context(text, finding.before, 40)) if finding.before in text else finding.before + finding.after)
    hit = finding.before
    if finding.kind in ('duplicate_page', 'foreign_page', 'seam_overlap') or not letters(hit):
        return image.name
    # The line sharing the longest run of letters with the text around the hit; a hyphenated word spans two lines.
    best = max(book.lines(finding.page), default=None, key=lambda line: difflib.SequenceMatcher(
        None, letters(line[3]), window, autojunk=False).find_longest_match().size)
    if best is None:
        return image.name
    _, top, height, _ = best
    size = subprocess.run(['sips', '-g', 'pixelWidth', '-g', 'pixelHeight', str(image)], capture_output=True, text=True).stdout.split()
    width, full = int(size[-3]), int(size[-1])
    crop = folder / 'proofread' / f'{finding.kind}_{index}.png'
    crop.parent.mkdir(exist_ok=True)
    subprocess.run(['sips', '-c', str(int(height * 3 * full)), str(width), '--cropOffset', str(max(0, int((top - height) * full))),
                    '0', str(image), '--out', str(crop)], capture_output=True)
    return f'proofread/{crop.name}'


def report(book, findings, sources):
    folder, pages = book.folder, book.pages
    by_kind = defaultdict(list)
    for f in findings:
        by_kind[f.kind].append(f)
    parts = ['<!doctype html><meta charset="utf-8"><title>Proofread</title><style>'
             'body{font:15px system-ui;margin:16px;max-width:1100px}.s{display:flex;gap:16px;margin:12px 0;'
             'border-top:1px solid #ccc;padding-top:12px}.s img{width:520px;flex:none;align-self:flex-start}mark{background:#fd6}'
             'ins{background:#9e9;text-decoration:none}</style><h1>Proofread</h1>']
    for kind, items in by_kind.items():
        trusted = sum(sources.get(f.page) == TEXT_LAYER for f in items)
        action = 'report only' if items[0].after is None else 'fixed by --apply'
        parts.append(f'<h2>{kind}: {len(items)} ({trusted} on text-layer pages), {action}</h2>')
        for index, f in enumerate(evenly_spaced(sorted(items, key=lambda f: f.page), SAMPLES)):
            if f.kind == 'cross_read':
                body = f'page text: <mark>{html.escape(f.before)}</mark><br>Vision: <ins>{html.escape(f.after)}</ins>'
            else:
                pre, hit, post = (html.escape(s[:600]) for s in context(pages[f.page], f.before))
                body = f'…{pre}<mark>{hit}</mark>{post}…<br>→ <ins>{html.escape(f.after or "")}</ins>'
            parts.append(f'<div class="s"><img src="{line_crop(book, f, index)}"><div><b>{f.page}</b> '
                         f'({sources.get(f.page, "unknown")})<br>{body}</div></div>')
    (folder / 'proofread.html').write_text('\n'.join(parts), encoding='utf-8')
    return by_kind


def main():
    parser = argparse.ArgumentParser(description='Find and fix capture and OCR errors in a book folder')
    parser.add_argument('folder', type=Path)
    parser.add_argument('--apply', help='Comma-separated kinds to fix, or "all"')
    parser.add_argument('--cross-read', action='store_true', help='Also compare non-Vision pages with a Vision reading')
    parser.add_argument('--title', help='Output file name without extension (default: folder name)')
    args = parser.parse_args()

    book, sources = Book(args.folder, load_pages(args.folder)), read_sources(args.folder)
    pages = book.pages
    findings = find_all(book)
    if args.cross_read:
        findings += cross_read(args.folder, pages, sources)
    by_kind = report(book, findings, sources)
    for kind, items in by_kind.items():
        trusted = sum(sources.get(f.page) == TEXT_LAYER for f in items)
        print(f'{kind:15} {len(items):5} on {len({f.page for f in items})} pages, {trusted} on text-layer pages')
    print(f'Samples: {args.folder / "proofread.html"}')

    if args.apply:
        kinds = set(by_kind) if args.apply == 'all' else set(args.apply.split(','))
        changed = apply(pages, [f for f in findings if f.kind in kinds])
        for stem, text in changed.items():
            (args.folder / f'{stem}.txt').write_text(text, encoding='utf-8')
        print(f'Fixed {len(changed)} pages')
        write_book(args.folder, args.title or args.folder.resolve().name, load_config())


if __name__ == '__main__':
    main()
