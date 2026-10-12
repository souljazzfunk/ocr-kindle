#!/usr/bin/env python3
"""
Check a converted book folder for the defects seen so far and name the fix for each.

Every check that failed once on a real book lives here, so the next book is checked by the machine, not by eye.
`capture_book.py` runs the automatic fixes and checks again; the rest are reported with what to do.
Exit code 1 when a blocking check fails.
"""

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from img2txt import LIVETEXT, SOURCES, TEXT_LAYER, VISION, page_images, read_sources
from layout import CODE_LINE, code_pages, code_rereads, listed, livetext_contents, read_layout
from proofread import CODE_BLOCK, DECORATIVE_REF, Book, find_all, load_pages

PROBE = 'dom-probe.json'
CAPTURE = 'capture.json'
POSITIONS = 'positions.json'
POSITION = re.compile(r'(?:位置|ページ|Location|Page)\D{0,3}(\d+)\s*(?:/|of)\s*(\d+)')
DEFAULT_TITLES = re.compile(r'^(kindle-book|[A-Z0-9]{10})$')
DECORATIVE_HEADING = re.compile(r'(?m)^#+\s*(?:Chapter\s+|Part\s+)?[Z&]+(?:\s.*)?$', re.I)
HEADING = re.compile(r'(?m)^(#+) (.*)$')
# A bare chapter number on its own line, then a short title, then body text: a chapter opening with no heading.
# An index entry ("Zen of Python, 30-31") is not a title: Python Crash Course's index letter Z, read as 7, came before one.
MISSED_CHAPTER = re.compile(r'(?m)^(?:[1-9]|1\d|20|[Z&])\n\n(?![#\d])(?![^\n]*,\s*\d+(?:[-–]\d+)?\s*\n)[^\n]{1,80}\n\n'
                            r'[^#\n]{%d,}' % 100)
# Proofread kinds whose fix is safe without a look at samples; the others are reported for a human.
AUTO_KINDS = ('duplicate_page', 'foreign_page', 'seam_overlap', 'dash_as_hyphen', 'stray_symbol', 'emphasis_dots', 'split_sentence',
              'homoglyph', 'decorative_digit', 'repl_prompt', 'section_number', 'l_for_i', 'digits_for_ll', 'binary_art',
              'stray_colon')
REVIEW_KINDS = ('hyphen_break', 'odd_case')
LOOSE_CODE = 0.25  # share of a programming book's code lines that may stand outside code blocks


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True
    fix: list | None = None  # a command to run from the repository root, or None when a person must act


def markdown_file(folder, title=None):
    return folder / f'{title or folder.resolve().name}.md'


def page_positions(folder):
    """(screen index from 0, N, M) for each captured page whose reader footer showed "Page N of M".

    positions.json (extension 1.3) has every page; older ZIPs have at most the dom-probe pages, which give the start.
    Returns (positions, whether they cover every page).
    """
    path = folder / POSITIONS
    if path.exists():
        found = []
        for name, text in json.loads(path.read_text(encoding='utf-8')).items():
            m = re.search(r'(\d+)/(\d+)', text or '')
            if m:
                found.append((int(re.search(r'(\d+)$', name).group(1)) - 1, int(m.group(1)), int(m.group(2))))
        return sorted(found), True
    path = folder / PROBE
    if not path.exists():
        return [], False
    probe = json.loads(path.read_text(encoding='utf-8'))
    found = []
    for entry in probe if isinstance(probe, list) else [probe]:
        m = POSITION.search(json.dumps(entry, ensure_ascii=False))
        if m and isinstance(entry, dict):
            found.append((entry.get('page', 1) - 1, int(m.group(1)), int(m.group(2))))
    return sorted(found), False


def end_tolerance(total):
    """Pages at the end the reader may not number (end matter), or the last turn may skip."""
    return max(2, round(total * 0.02))


def check_folder(folder, title=None):
    folder = Path(folder)
    images = page_images(folder)
    checks = []

    numbers = [int(re.search(r'_(\d+)\.png$', p.name).group(1)) for p in images]
    checks.append(Check('pages', bool(numbers) and numbers == list(range(1, len(numbers) + 1)),
                        f'{len(numbers)} pages' + ('' if numbers == list(range(1, len(numbers) + 1)) else ', numbering has gaps or repeats'),
                        fix=None))

    missing = [p.name for p in images if not p.with_suffix('.txt').exists()]
    checks.append(Check('text', not missing, f'{len(missing)} pages without text',
                        fix=['img2txt.py', str(folder)] if missing else None))

    positions, complete = page_positions(folder)
    first = positions[0] if positions else None
    # A screen shows one page number or less, so page N cannot be reached in fewer than N - 1 screens.
    late = first and first[1] - 1 > first[0]
    checks.append(Check('start_position', not late,
                        f'first numbered screen {first[0] + 1} shows page {first[1]} of {first[2]}' if first else 'start not recorded',
                        fix=None))
    last = positions[-1] if complete and positions else None
    short = last and last[1] < last[2] - end_tolerance(last[2])
    checks.append(Check('end_position', not short,
                        f'last page {last[1]} of {last[2]}' if last else 'end not recorded', fix=None))
    capture = folder / CAPTURE
    stopped = json.loads(capture.read_text(encoding='utf-8')).get('stopped') if capture.exists() else ''
    checks.append(Check('capture_stop', not stopped, f'a turn cap ended the capture: {stopped}' if stopped
                        else 'the capture ran to the end of the book', fix=None))
    checks.append(Check('positions', complete, f'{POSITIONS} ' + ('present' if complete else
                        'missing: a ZIP from extension 1.2 or older, so start and end were not fully checked'),
                        blocking=False, fix=None))

    md = markdown_file(folder, title)
    checks.append(Check('title', bool(title) or not DEFAULT_TITLES.match(md.stem), f'output name "{md.stem}"', blocking=False,
                        fix=None))

    sources = read_sources(folder)
    layer = sum(s == TEXT_LAYER for s in sources.values())
    probe = folder / PROBE
    checks.append(Check('text_layer', layer > 0, f'{layer} of {len(images)} pages from the text layer'
                        + (f'; read {probe.name} and fix the selector in extension/content.js' if probe.exists() and not layer else ''),
                        blocking=False, fix=None))

    text = md.read_text(encoding='utf-8') if md.exists() else ''
    fenced = CODE_BLOCK.findall(text)
    text = CODE_BLOCK.sub('', text)  # a comment in code is no title, and its digits no chapter reference
    # Vision's pages take their titles, quotes and figures from Gemini (layout.py); a page it has not read yet keeps
    # titles guessed from the layout alone, which miss most of them. Running img2txt.py again asks for those pages,
    # and the pages of a programming book read before Gemini was asked for code.
    laid = read_layout(folder)
    again = code_rereads(folder, laid)
    unread = [img.stem for img in images if sources.get(img.stem) in (VISION, LIVETEXT) and (img.stem not in laid or img.stem in again)]
    checks.append(Check('layout', not unread, f'{len(unread)} Vision and Live Text pages without a layout from Gemini'
                        + (f' ({len(again)} to read again for code)' if again else '')
                        + (f', e.g. {unread[0]}' if unread else ''),
                        fix=['img2txt.py', str(folder), '--model', VISION] + (['--title', title] if title else [])
                        if unread else None))
    # Python Crash Course (2026-10-11): no code block at all; comments were titles, listings bullets or prose
    # (">>age = 18 >> age == 18 True"). Reading the pages again gives the same blocks, so this is reported.
    if code_pages(folder):
        loose = [line for line in text.splitlines() if not line.startswith('#') and CODE_LINE.match(line.removeprefix('- '))]
        inside = sum(bool(CODE_LINE.match(line.strip())) for block in fenced for line in block.splitlines())
        checks.append(Check('code_blocks', len(loose) <= LOOSE_CODE * (len(loose) + inside),
                            f'{len(loose)} of {len(loose) + inside} code lines outside code blocks'
                            + (f', e.g. "{loose[0][:60]}"' if loose else ''), fix=None))
    # Reading the pages again gives the same titles, so these are reported, not retried.
    headings = len(re.findall(r'(?m)^#+ ', text))
    checks.append(Check('headings', md.exists() and headings > 0, f'{headings} headings in {md.name}', fix=None))
    decorative = DECORATIVE_HEADING.findall(text)
    in_text = [m.group() for m in DECORATIVE_REF.finditer(text) if re.search(r'[Z&]', m.group())]
    checks.append(Check('decorative_digits', not decorative and not in_text,
                        f'{len(decorative)} headings and {len(in_text)} chapter references read as Z or &',
                        # References in the text are fixed by the proofread check's decorative_digit.
                        fix=None))
    pages = load_pages(folder)
    findings = find_all(Book(folder, pages)) if pages else []
    noise = [f.after for f in findings if f.kind == 'heading_noise']
    checks.append(Check('heading_noise', not noise, f'{len(noise)} headings that are not titles'
                        + (f', e.g. "{noise[0][:40]}"' if noise else ''),
                        fix=['proofread.py', str(folder), '--apply', 'heading_noise'] + (['--title', title] if title else [])
                        if noise else None))
    # A number over an epigraph is a section the proofread check's section_number makes a heading, not a chapter.
    missed = [m for m in MISSED_CHAPTER.findall(text) if not re.match(r'\S+\n\n> ', m)]
    # Reading the pages again gives the same headings, so a missed chapter is reported, not retried.
    # The openings themselves go in the report, so the converter is fixed from them rather than from a guess.
    shown = '; '.join('"' + re.sub(r'\n+', ' / ', m)[:70] + '"' for m in missed[:5])
    checks.append(Check('chapter_titles', not missed, f'{len(missed)} chapter openings without a heading'
                        + (f': {shown}' if missed else ''), fix=None))
    # 英語教育幻想 (2026-10-12) passed with 幻想5 and 幻想6 left as paragraphs: every chapter its contents lists has a heading.
    titles = [title for _, title in HEADING.findall(text)]
    unheaded = [entry for entry in livetext_contents(page_images(folder))[1] if not any(listed(t, [entry]) for t in titles)]
    checks.append(Check('contents_chapters', not unheaded, f'{len(unheaded)} contents entries without a heading'
                        + (': ' + '; '.join(f'"{e[:30]}"' for e in unheaded[:5]) if unheaded else ''), fix=None))

    counts = {}
    for f in findings:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    auto = [k for k in AUTO_KINDS if counts.get(k)]
    checks.append(Check('proofread', not auto, ', '.join(f'{k} {counts[k]}' for k in auto) or 'nothing to fix',
                        fix=['proofread.py', str(folder), '--apply', ','.join(auto)] + (['--title', title] if title else []) if auto else None))
    review = [k for k in REVIEW_KINDS if counts.get(k)]
    checks.append(Check('proofread_review', not review, ', '.join(f'{k} {counts[k]}' for k in review) or 'nothing to review',
                        blocking=False, fix=None))
    return checks


def main():
    parser = argparse.ArgumentParser(description='Check a converted book folder and name the fix for each defect')
    parser.add_argument('folder', type=Path)
    parser.add_argument('--title')
    parser.add_argument('--json', action='store_true', help='Print the checks as JSON')
    args = parser.parse_args()
    checks = check_folder(args.folder, args.title)
    if args.json:
        print(json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=1))
    else:
        for c in checks:
            mark = 'ok  ' if c.ok else ('FAIL' if c.blocking else 'warn')
            print(f'{mark} {c.name:18} {c.detail}' + (f'  → {" ".join(c.fix)}' if c.fix and not c.ok else ''))
    sys.exit(any(not c.ok and c.blocking for c in checks))


if __name__ == '__main__':
    main()
