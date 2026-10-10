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

from img2txt import SOURCES, TEXT_LAYER, VISION, page_images, read_sources
from proofread import Book, find_all, load_pages

PROBE = 'dom-probe.json'
POSITIONS = 'positions.json'
POSITION = re.compile(r'(?:位置|ページ|Location|Page)\D{0,3}(\d+)\s*(?:/|of)\s*(\d+)')
DEFAULT_TITLES = re.compile(r'^(kindle-book|[A-Z0-9]{10})$')
DECORATIVE_HEADING = re.compile(r'(?m)^#+\s*(?:Chapter\s+|Part\s+)?[Z&]+\s*$', re.I)
# Proofread kinds whose fix is safe without a look at samples; the others are reported for a human.
AUTO_KINDS = ('duplicate_page', 'foreign_page', 'seam_overlap', 'dash_as_hyphen', 'stray_symbol', 'homoglyph')
REVIEW_KINDS = ('hyphen_break', 'odd_case')


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
    from_vision = sources and all(s == VISION for s in sources.values())
    reocr = ['img2txt.py', str(folder), '--force', '--model', VISION] + (['--title', title] if title else [])
    headings = len(re.findall(r'(?m)^#+ ', text))
    checks.append(Check('headings', md.exists() and headings > 0, f'{headings} headings in {md.name}',
                        fix=reocr if from_vision and not headings else None))
    decorative = DECORATIVE_HEADING.findall(text)
    checks.append(Check('decorative_digits', not decorative, f'{len(decorative)} headings read as Z or &',
                        fix=reocr if from_vision and decorative else None))

    pages = load_pages(folder)
    findings = find_all(Book(folder, pages)) if pages else []
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
