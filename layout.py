"""
Give Vision's pages the structure Vision cannot see: titles, pull quotes, figures, art, drop caps and code.

Vision reads the words well, but a title, a pull quote, a sidebar label and a line in a box look alike in its line
boxes, and their place and size vary from book to book (AI for Educators sets its section titles in dark bands at the
body margin and its pull quotes centred in grey boxes). So img2txt.py keeps Vision's line boxes per page
(`<page>.lines.json`), and a Gemini model that sees the page image names, by line number, what each special line is.
It answers with line numbers and a few words, never the book's text, so RECITATION cannot refuse it, and twenty pages
go in one request. The answers are kept in layout.json, so a re-run asks only for pages not yet read.

When Gemini fails (its free tier ran out mid-list on 2026-10-10), Sonnet answers the same question through the
Claude Code CLI on the Mac (`claude -p`), on Laiken's Claude plan, so the list does not wait a day for the quota.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import median

from google.genai import types

from img2txt import (BODY_LINE_CHARS, DECORATIVE_DIGITS, HEADING_NUMBER, LINES, LIVE, ROW, WORKERS, page_images,
                     vision_markdown)

LAYOUT = 'layout.json'
MODEL = 'gemini-3.5-flash-lite'
BATCH = 20  # pages per request; the free tier allows 500 requests a day, which 8 a request used up on 2026-10-10
TRIES = 5
RATE_WAIT_S = 30  # the free tier allows 15 requests a minute
DAILY_LIMIT = 'PerDay'  # in a 429 for the day's requests, which waiting minutes does not end


class GeminiFailed(RuntimeError):
    """A layout request failed after its retries. The rest of the book's requests are not sent: on 2026-10-10 every
    request failed for ten minutes before anyone noticed, because the run went on and only the verdict was watched."""
CLAUDE_MODEL = 'sonnet'
CLAUDE_WORKERS = 3
CLAUDE_TIMEOUT_S = 900
CLAUDE = 'claude'  # the model name kept in layout.json for the pages Claude laid out
LINE_PITCH = 1.6  # line pitch at most this many line heights
MIN_FIGURE_LINES = 3
CAPS_MIN_LETTERS = 3
TITLE_GAP = 2.5  # line heights between two lines of one title, at most
PICTURE_PIECES = 15
FIGURE_PAGE = 0.6  # a page this much figure is a figure: its titles and credits are the picture's
STYLE_LEFT = 0.006  # lines starting this close are one style
STYLE_MIN = 5
STYLE_SHARE = 0.8
CAP_REACH = 3  # lines either side of the one Gemini named
CAP_GAP = 0.01
CAP_HEIGHT = 1.8  # a drop cap is this many lines tall, or more
CAP_LINES = 4
NOISE_CHARS = 2  # a line this short that is no title is art read as text (binary digits behind a chapter number)
BINARY = re.compile(r'[01\s]{3,}')  # zeros and ones are a picture of data, not words
BULLET = re.compile(r'^[•►▪‣◦●■]\s*')
CONTENTS = re.compile(r'^(?:(?:table of )?contents|目\s*次)$', re.I)
CONTENTS_PAGES = 0.15  # the contents page is in the front matter
# A line that only code starts so: a prompt (Vision reads >>> as >>, ›> or >> >), a shell's $, a definition, an
# import, a call to print, a comment, a block statement. A book with this many is a programming book.
CODE_LINE = re.compile(r'^(?:(?:[>›»] ?){2,3}\S|\$ \w|(?:def|class) \w.*:$|import [\w.]+(?:, [\w.]+)*(?: as \w+)?$'
                       r'|from [\w.]+ import \S|print ?\(|# \w|for \w+(?:, \w+)* in [\w.()\[\], ]+:$|(?:else|try|finally):$)')
CODE_BOOK_LINES = 20
CODE_JITTER = 0.004  # code lines whose left edges are this close start in one column
INDENT = 4  # spaces in one step of indentation in the books' code (Python Crash Course, Automate the Boring Stuff)

PROMPT = """Each image is one page of a book. Under it is the page's text as OCR read it, one numbered line per
printed line, top to bottom. Describe the page's structure by line number.

- titles: chapter titles (level 1, with the chapter number's line when the number is printed apart) and headings of
  sections and of numbered items (level 2, or 3 below a level 2). A heading may sit in a band or a box; mark every
  line styled as a heading. A title that wraps is several lines; name each. Not titles: table-of-contents entries
  (the "Contents" heading itself is one), pull quotes, sidebar labels (e.g. KEY DEFINITION) and the terms they
  define, captions, running heads, URLs, emphasised sentences, list items.
- quotes: lines of a pull quote or callout, set apart from the running text to highlight it (not a quotation inside a
  paragraph).
- figures: each group of lines that is text inside a picture, chart, diagram or infographic, with a short
  description of the picture. Not figures: boxes of running text (definitions, sidebars, tips, callouts); their
  words are the book's text.
- art: lines that are decoration read as text, not words of the book: digits or letters forming a picture, ornaments,
  logo letters, stray marks.
- drop_cap: when a paragraph on the page starts with a large initial letter, the line that letter begins and the
  paragraph's first word as printed, initial letter included.
- code: each group of consecutive lines that is program code, a shell or interpreter session (lines after >>> or $
  and what they print) or a program's output shown as a listing. A comment starting with # in code is code, not a
  title; code lines are never titles, quotes or list items.

Give every page; leave a list empty when nothing on the page belongs in it."""

_INT = types.Schema(type=types.Type.INTEGER)
_LINES = types.Schema(type=types.Type.ARRAY, items=_INT)
SCHEMA = types.Schema(type=types.Type.ARRAY, items=types.Schema(
    type=types.Type.OBJECT, required=['page', 'titles', 'quotes', 'figures', 'art', 'code'], properties={
        'page': types.Schema(type=types.Type.STRING),
        'titles': types.Schema(type=types.Type.ARRAY, items=types.Schema(
            type=types.Type.OBJECT, required=['line', 'level'], properties={'line': _INT, 'level': _INT})),
        'quotes': _LINES,
        'figures': types.Schema(type=types.Type.ARRAY, items=types.Schema(
            type=types.Type.OBJECT, required=['lines', 'description'], properties={
                'lines': _LINES, 'description': types.Schema(type=types.Type.STRING)})),
        'art': _LINES,
        'code': types.Schema(type=types.Type.ARRAY, items=_LINES),
        'drop_cap': types.Schema(type=types.Type.OBJECT, nullable=True, required=['line', 'word'], properties={
            'line': _INT, 'word': types.Schema(type=types.Type.STRING)})}))


def read_layout(folder):
    path = folder / LAYOUT
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def code_pages(folder):
    """{page: its Vision lines that look like code} in a programming book, one with at least CODE_BOOK_LINES of
    them; {} in any other book."""
    counts = {path.name[:-len(LINES)]: sum(bool(CODE_LINE.match(line[3].strip()))
                                           for line in json.loads(path.read_text(encoding='utf-8')))
              for path in folder.glob(f'*{LINES}')}
    return counts if sum(counts.values()) >= CODE_BOOK_LINES else {}


def code_rereads(folder, layout):
    """Pages of a programming book laid out before `code` was asked for (no `code` key) whose lines look like code;
    they are read once more. Python Crash Course (2026-10-11) came out with its comments as titles and its listings as
    bullets or run into prose. Other pages, and every page of a book without code, keep their layout."""
    if all('code' in page for page in layout.values()):
        return set()
    return {stem for stem, n in code_pages(folder).items() if n and stem in layout and 'code' not in layout[stem]}


def sorted_boxes(image):
    return sorted(json.loads(image.with_suffix(LINES).read_text(encoding='utf-8')), key=lambda line: line[1])


def page_lines(image):
    """The numbered lines Gemini sees: Vision's lines, or for a Live Text page (no line boxes) the paragraphs of its
    own reading; from the .txt, with any heading marks from an earlier layout taken off, when that was not kept."""
    if image.with_suffix(LINES).exists():
        return [line[3] for line in sorted_boxes(image)]
    live = image.with_suffix(LIVE)
    text = (live if live.exists() else image.with_suffix('.txt')).read_text(encoding='utf-8')
    return [re.sub(r'^#+ ', '', line) for line in text.splitlines() if line.strip()]


def livetext_markdown(lines, page, chapters=(), contents=False, mark='', spelled=()):
    """A Live Text page with Gemini's titles marked. 英語教育幻想 (2026-10-12) came out with no headings: its section
    marks 「†」 read as 「十」 and the titles stayed paragraphs. Without boxes, only the titles are taken: the book's
    section mark is taken off them, the contents page gives their level and its numbers (spelled), and a contents
    page's entries stay text. A title Live Text ran into the next paragraph stays a paragraph, its words kept."""
    levels = {t['line']: t['level'] for t in page.get('titles', []) if 0 <= t.get('line', -1) < len(lines)}
    # 幻想5 and 幻想6 (2026-10-12) were left as paragraphs by Gemini; a short line the contents lists is a chapter title.
    levels.update({i: 1 for i, line in enumerate(lines) if i not in levels and not contents and '。' not in line
                   and len(line) <= LISTED_MAX and listed(line[1:] if mark and line[:1] in mark else line, chapters)})
    out = []
    for i, line in enumerate(lines):
        title = line[1:].lstrip() if mark and line[:1] in mark and i in levels else line
        if i not in levels or contents and not CONTENTS.match(line.strip()) or '。' in line and len(line) > LISTED_MAX:
            out.append(title)
            continue
        level = chapter_level(title, levels[i], chapters)
        out.append(f'{"#" * level} {contents_spelling(title, spelled) if level == 1 else title}')
    return '\n\n'.join(out)


# Each group is one mark with the characters Live Text reads it as: 英語教育幻想's 「†」 came out as 「十」, 「＋」 and 「+」.
SECTION_MARKS = ('十＋+†‡', '◆◇', '■□', '●○', '▼▽', '★☆', '※')
LISTED_MAX = 50
MARK_MIN = 5
MARK_SHARE = 0.3


def section_mark(titles):
    """The group of SECTION_MARKS a book sets before its section titles, when its characters start at least MARK_MIN of
    them and MARK_SHARE of all: 英語教育幻想's 「†」, read as 「十」 on 73 titles and as 「＋」 or 「+」 on 29 more."""
    firsts = [t.strip()[:1] for t in titles if t.strip()]
    counts = {group: sum(first in group for first in firsts) for group in SECTION_MARKS}
    mark = max(counts, key=counts.get)
    return mark if counts[mark] >= MARK_MIN and counts[mark] >= MARK_SHARE * len(firsts) else ''


def contents_spelling(title, spelled):
    """The title with the numbers its contents line has where Live Text read a symbol: 英語教育幻想's 「幻想8」 came out as
    「幻想∞」 in the text and right in the contents. Everything else must match, at least 8 characters of it."""
    for entry in spelled:
        out, i, j, swapped = [], 0, 0, False
        while i < len(title) and j < len(entry):
            a, b = title[i], entry[j]
            if a == b:
                out.append(a)
                i, j = i + 1, j + 1
            elif a.isspace():
                out.append(a)
                i += 1
            elif b.isspace():
                j += 1
            elif b.isdigit() and not comparable(a) and not a.isdigit():
                out.append(b)
                i, j, swapped = i + 1, j + 1, True
            else:
                break
        else:
            if swapped and i >= 8:
                return ''.join(out) + title[i:]
    return title


def livetext_contents(images):
    """(stems, entries, spelled) of a Live Text book's contents: the front-matter page with a 目次 line and the pages right after
    it that hold no sentence (no 「。」). 英語教育幻想's contents ran over several pages, and each entry came out as a
    title, some cut mid-line."""
    pages = [image for image in images[:max(1, round(len(images) * CONTENTS_PAGES))]
             if not image.with_suffix(LINES).exists() and image.with_suffix('.txt').exists()]
    for k, image in enumerate(pages):
        lines = page_lines(image)
        start = next((i for i, text in enumerate(lines) if CONTENTS.match(text.strip())), None)
        if start is None:
            continue
        stems, entries = [image.stem], lines[start + 1:]
        for after in pages[k + 1:]:
            more = page_lines(after)
            if not more or any('。' in line for line in more):
                break
            stems.append(after.stem)
            entries += more
        spelled = [re.sub(r'\s*\d+$', '', x).strip() for x in entries]
        return set(stems), [e for e in map(comparable, spelled) if len(e) >= 3], spelled
    return set(), [], []


def ask_layout(client, images):
    """Gemini's reading of a batch of Vision pages: {stem: layout} for each page it answered."""
    parts = [PROMPT]
    for image in images:
        parts += [f'Page "{image.stem}":', types.Part.from_bytes(data=image.read_bytes(), mime_type='image/png'),
                  '\n'.join(f'{i}: {line}' for i, line in enumerate(page_lines(image)))]
    response = client.models.generate_content(model=MODEL, contents=parts, config=types.GenerateContentConfig(
        temperature=0, response_mime_type='application/json', response_schema=SCHEMA,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
    stems = {image.stem for image in images}
    return {page.pop('page'): page for page in json.loads(response.text) if page.get('page') in stems}


def claude_cli():
    """The Claude Code CLI, also where its installer puts it when a background job's PATH leaves that out."""
    return shutil.which('claude') or next(
        (str(p) for p in [os.path.expanduser('~/.local/bin/claude'), os.path.expanduser('~/.claude/local/claude')]
         if os.access(p, os.X_OK)), None)


def json_array(text):
    """The JSON array in a reply that may wrap it in a code fence or a sentence."""
    start, end = text.find('['), text.rfind(']')
    if start < 0 or end < start:
        raise ValueError(f'no JSON array in the reply: {text[:200]}')
    return json.loads(text[start:end + 1])


def ask_layout_claude(cli, images):
    """Sonnet's reading of a batch of Vision pages through `claude -p`: it opens each page image with its Read tool.
    Hooks are off, and LLM_WIKI_WATCHER tells the wiki's own SessionEnd hook to skip them, so these calls stay out of
    the session log the LLM wiki grows from (Laiken, 2026-10-10: seven of them had reached its queue)."""
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


def mark_layout(folder, client, images, cli=None):
    """Ask Gemini for the layout of the pages in images not read yet, and rewrite every page in images from it.
    Once a Gemini request fails, the rest go to Sonnet through cli (`claude -p`); with no client, all of them do.

    Returns the pages still without a layout (a failed request; running img2txt.py again asks again).
    """
    layout = read_layout(folder)
    again = code_rereads(folder, layout)
    todo = [image for image in images if (image.stem not in layout or image.stem in again)
            and (image.with_suffix(LINES).exists() or image.with_suffix('.txt').exists())]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]

    failed = threading.Event()  # Gemini failed: send it nothing more
    claude_failed = threading.Event()
    claude_slots = threading.Semaphore(CLAUDE_WORKERS)
    gemini_error = []
    if not client:
        failed.set()

    def ask(batch):
        for attempt in range(TRIES):
            if failed.is_set():
                break
            try:
                return ask_layout(client, batch), None
            except Exception as e:
                if '429' not in str(e) or DAILY_LIMIT in str(e) or attempt + 1 == TRIES:
                    if not failed.is_set():
                        print(f'GEMINI ERROR [layout]: {e}' + ('; Sonnet lays out the rest' if cli else ''), flush=True)
                        gemini_error.append(e)
                    failed.set()
                    break
                time.sleep(RATE_WAIT_S)
        if not cli or claude_failed.is_set():
            return {}, (gemini_error or [None])[0]
        with claude_slots:
            if claude_failed.is_set():
                return {}, None
            try:
                return ask_layout_claude(cli, batch), None
            except Exception as e:
                claude_failed.set()
                return {}, RuntimeError(f'Gemini: {str((gemini_error or ["no API key"])[0])[:300]}; Sonnet: {e}')

    first_error = None
    with ThreadPoolExecutor(WORKERS) as pool:
        for done, (pages, error) in enumerate(pool.map(ask, batches), 1):
            if error:
                print(f'GEMINI ERROR [layout {done}/{len(batches)}]: {error}', flush=True)
                first_error = first_error or error
            elif any(page.get('model') == CLAUDE for page in pages.values()):
                print(f'[layout {done}/{len(batches)}] {len(pages)} pages by Sonnet', flush=True)
            layout.update((stem, dict(page, code=code_groups(page.get('code')))) for stem, page in pages.items())
            (folder / LAYOUT).write_text(json.dumps(layout, ensure_ascii=False, indent=0), encoding='utf-8')
    every = page_images(folder)
    contents, toc, spelled = livetext_contents(every)
    chapters = contents_entries(every) or toc
    live = [image for image in every if image.stem in layout and not image.with_suffix(LINES).exists()
            and image.with_suffix('.txt').exists()]
    mark = section_mark([lines[t['line']] for image in live for lines in [page_lines(image)]
                         for t in layout[image.stem].get('titles', []) if 0 <= t.get('line', -1) < len(lines)])
    styled = styled_titles(folder, layout)
    for image in images:
        if image.stem in layout and image.with_suffix(LINES).exists():
            page = dict(layout[image.stem], titles=layout[image.stem].get('titles', []) + styled.get(image.stem, []))
            boxes, page = reread_beside_cap(image, sorted_boxes(image), page)
            text = page_markdown(boxes, page, chapters)
            image.with_suffix('.txt').write_text(text or ' ', encoding='utf-8')
        elif image.stem in layout and image.with_suffix('.txt').exists():
            text = livetext_markdown(page_lines(image), layout[image.stem], chapters, image.stem in contents, mark, spelled)
            image.with_suffix('.txt').write_text(text or ' ', encoding='utf-8')
    left = [image for image in images if image.stem not in layout]
    if first_error:
        # The pages laid out so far are written above; the rest wait for the cause to be gone.
        when = (' Today\'s requests are used up; they come back at midnight Pacific (16:00 or 17:00 JST).'
                if DAILY_LIMIT in str(first_error) else '')
        raise GeminiFailed(f'Layout failed with {len(left)} pages left: {str(first_error)[:300]}.{when}')
    return left


def page_markdown(boxes, page, chapters=()):
    """A page's Markdown from Vision's line boxes (sorted by top, as numbered for Gemini) and Gemini's layout.

    Titles take their level (neighbouring title lines at one level are a title that wraps, and a bare chapter number
    joins the title under it); pull quotes become block quotes, figures one image note each, code a fenced block, and
    art goes. A code line is nothing else.
    """
    n = len(boxes)
    valid = lambda i: isinstance(i, int) and 0 <= i < n
    runs = code_runs(page.get('code', []), valid)
    code = {i for run in runs for i in run}
    char = code_char(boxes, runs)
    fences = {run[0]: code_block(boxes, run, char) for run in runs}
    levels = {t['line']: t['level'] for t in page.get('titles', [])
              if valid(t['line']) and 1 <= t['level'] <= 3 and t['line'] not in code}
    quotes = {i for i in page.get('quotes', []) if valid(i)} - set(levels) - code
    art = {i for i in page.get('art', []) if valid(i)} - set(levels)
    texts = [box[3].strip() for box in boxes]
    figure_at, member = {}, {}
    for figure in page.get('figures', []):
        lines = sorted(i for i in figure['lines'] if valid(i) and i not in levels and i not in code)
        # One or two lines beside an icon are the book's words (a label, a sentence), and so are long lines (a
        # definition box Gemini took for a picture); only short labels of a chart or diagram give way to a note.
        if len(lines) >= MIN_FIGURE_LINES and median(len(boxes[i][3]) for i in lines) < BODY_LINE_CHARS:
            figure_at[lines[0]] = figure['description'].strip() or 'Figure'
            art |= set(lines[1:])
            member.update((i, lines[0]) for i in lines)
    # A title right above a figure's text is the figure's own (AI for Educators: the infographic's "30 WAYS AI CAN
    # SUPPORT TEACHING AND LEARNING" came back as the chapter title again), so it joins the figure's note.
    short = [i for i in range(n) if i not in art | code and i not in levels and len(texts[i]) < BODY_LINE_CHARS]
    if not member and len(short) >= PICTURE_PIECES and sum(len(texts[i]) for i in short) >= FIGURE_PAGE * sum(
            len(texts[i]) for i in range(n) if i not in art):
        # Gemini can miss a page that is a picture full of words (AI for Educators' infographic came back as six
        # chapter titles once); scattered short pieces with no running text give it away.
        figure_at[short[0]] = 'Figure'
        art |= set(short[1:])
        member.update((i, short[0]) for i in short)
    captions = {}
    words = [i for i in range(n) if i not in art or i in member]
    size = lambda lines: sum(len(texts[i]) for i in lines)
    if member and size(member) >= FIGURE_PAGE * size(words):
        # A page that is a picture (an infographic): its titles, credits and stray words are the picture's.
        first = min(figure_at)
        captions[first] = [texts[i] for i in sorted(levels)]
        art |= set(words) - {first}
        levels.clear()
    for start in sorted(i for i in levels if i - 1 not in levels):
        end = start
        while end + 1 in levels:
            end += 1
        if end + 1 in member:
            captions.setdefault(member[end + 1], []).extend(texts[start:end + 1])
            for i in range(start, end + 1):
                levels.pop(i)
                art.add(i)
    for first, title in captions.items():
        if title:
            figure_at[first] = f'{" ".join(title).rstrip(":")}: {figure_at[first]}'
    drop_cap(texts, boxes, page.get('drop_cap'), art, code)
    art -= code
    contents = next((i for i, text in enumerate(texts) if CONTENTS.match(text)), None)
    entries = set(range(contents + 1, n)) - art - code if contents is not None else set()
    for i in entries:
        levels.pop(i, None)

    marked = []
    i = 0
    while i < n:
        left, top, _, _ = boxes[i]
        text, level = texts[i], levels.get(i)
        i += 1
        if i - 1 in code:
            if i - 1 in fences:
                marked.append((left, top, fences[i - 1]))
        elif i - 1 in figure_at:
            marked.append((left, top, f'![{figure_at[i - 1]}]()'))
        elif i - 1 in art or not level and (len(text.replace(' ', '')) <= NOISE_CHARS or BINARY.fullmatch(text)):
            continue
        elif level:
            parts = [text]
            if HEADING_NUMBER.match(text) and levels.get(i):
                level = levels[i]
            while (levels.get(i) == level and same_title(boxes, texts, i)
                   or i < n and continues_title(boxes, texts, i, levels, quotes | art | code)):
                parts.append(texts[i])
                i += 1
            parts = [(m.group(1) or '') + m.group(2).translate(DECORATIVE_DIGITS) if (m := HEADING_NUMBER.match(p))
                     else p for p in parts]
            title = ' '.join(parts)
            marked.append((left, top, '#' * chapter_level(title, level, chapters) + ' ' + title))
        elif i - 1 in quotes:
            parts = [text]
            while i in quotes and i not in figure_at:
                parts.append(texts[i])
                i += 1
            marked.append((left, top, '> ' + ' '.join(parts)))
        elif i - 1 in entries:
            marked.append((left, top, '- ' + text))
        else:
            marked.append((left, top, BULLET.sub('- ', text)))
    if not marked:
        return ''
    # On a page of few lines the median gap is itself a paragraph gap (a chapter's subtitle and its first line), so
    # the pitch is held to what the body's type size allows.
    tops = [top for _, top, _ in marked]
    gaps = [b - a for a, b in zip(tops, tops[1:]) if b - a > ROW] or [1]
    body = [box[2] for i, box in enumerate(boxes) if i not in levels and len(texts[i]) >= BODY_LINE_CHARS]
    return vision_markdown(marked, min(median(gaps), LINE_PITCH * median(body)) if body else None)


def code_groups(code):
    """Groups of line numbers as kept in layout.json; a flat list (a reply that dropped the nesting) is one group."""
    if not isinstance(code, list):
        return []
    if all(isinstance(i, int) for i in code):
        return [code] if code else []
    return [group for group in code if isinstance(group, list)]


def code_runs(groups, valid):
    """Gemini's code groups as runs of consecutive lines, so a line between two parts of a group keeps its place."""
    runs = []
    for group in groups:
        lines = sorted({i for i in group if valid(i)}) if isinstance(group, list) else []
        for k, i in enumerate(lines):
            if k and i == lines[k - 1] + 1:
                runs[-1].append(i)
            else:
                runs.append([i])
    return runs


def code_char(boxes, runs):
    """The width of one character of the page's code. Vision's boxes have no width, so the smallest step in from a
    run's leftmost line is taken as one indent of INDENT spaces; with no indented line, any width will do."""
    steps = [boxes[i][0] - min(boxes[j][0] for j in run) for run in runs
             for k, i in enumerate(run) if not k or boxes[i][1] - boxes[run[k - 1]][1] > ROW]
    steps = [step for step in steps if step > CODE_JITTER]
    return min(steps) / INDENT if steps else float('inf')


def code_block(boxes, run, char):
    """A run of code lines as a fenced block, one printed line per line, indented by its left edge's distance from
    the run's leftmost line, since Python's indentation is its syntax. Pieces of one printed line (Vision splits a
    line at a wide gap) go back together, as far apart as their places on the page."""
    base = min(boxes[i][0] for i in run)
    rows = []  # [top, text]
    for i in run:
        left, top, _, text = boxes[i]
        column = round((left - base) / char) if char != float('inf') else 0
        if rows and top - rows[-1][0] <= ROW:
            rows[-1][1] += ' ' * max(1, column - len(rows[-1][1])) + text.strip()
        else:
            rows.append([top, ' ' * column + text.strip()])
    return '```\n' + '\n'.join(text for _, text in rows) + '\n```'


def capitals(text):
    return text.upper() == text and len(re.findall('[A-Z]', text)) > CAPS_MIN_LETTERS


def styled_titles(folder, layout):
    """Titles Gemini missed, found by their style across the book: {stem: [{line, level}]}.

    Gemini judges eight pages at a time, so a heading styled like forty others can come back as a pull quote or as
    nothing (AI for Educators: "WE'VE BEEN HERE BEFORE." in the same dark band as every section title). A line in
    capitals that starts where the book's titles in capitals start, when nearly all such lines are titles, is one.
    """
    lines = []  # (left, stem, line, level or None)
    for stem, page in layout.items():
        path = folder / f'{stem}{LINES}'
        if not path.exists():
            continue
        boxes = sorted(json.loads(path.read_text(encoding='utf-8')), key=lambda line: line[1])
        levels = {t['line']: t['level'] for t in page.get('titles', [])}
        skip = set(page.get('art', [])) | {i for figure in page.get('figures', []) for i in figure.get('lines', [])} | {
            i for group in page.get('code', []) for i in group}
        lines += [(box[0], stem, i, levels.get(i)) for i, box in enumerate(boxes)
                  if i not in skip and capitals(box[3].strip())]
    found = {}
    lines.sort()
    group = []
    for line in lines + [(float('inf'), None, None, None)]:
        if group and line[0] - group[0][0] > STYLE_LEFT:
            marked = [level for _, _, _, level in group if level]
            if len(marked) >= STYLE_MIN and len(marked) >= STYLE_SHARE * len(group):
                level = max(set(marked), key=marked.count)
                for _, stem, i, current in group:
                    if not current:
                        found.setdefault(stem, []).append({'line': i, 'level': level})
            group = []
        group.append(line)
    return found


def same_title(boxes, texts, i):
    """Whether title line i carries on the title line above it: close below, in the same case, or under a bare
    chapter number. Two titles Gemini put next to each other stay apart (AI for Educators: a pull quote marked as a
    title ran into the section title under it)."""
    above, line = boxes[i - 1], boxes[i]
    return bool(HEADING_NUMBER.match(texts[i - 1])) or (
        line[1] - above[1] <= TITLE_GAP * above[2] and capitals(texts[i - 1]) == capitals(texts[i]))


def continues_title(boxes, texts, i, levels, other):
    """An unmarked line that carries on the title above it: same type size, close below, in capitals when the
    title is (AI for Educators: "22. CREATE REVIEW CONTENT, QUESTIONS, AND ACTIVITIES" over "WITH AI.")."""
    if i in levels or i in other or not texts[i]:
        return False
    above, line = boxes[i - 1], boxes[i]
    capitals = texts[i - 1].upper() == texts[i - 1]
    return (abs(line[2] - above[2]) <= 0.15 * above[2] and line[1] - above[1] <= LINE_PITCH * above[2]
            and capitals and texts[i].upper() == texts[i] and re.search('[A-Z]', texts[i]) is not None)


def tall_letters(boxes):
    """Indexes of single capital letters standing taller than the body: drop caps Vision read as boxes of their own."""
    size = median([box[2] for box in boxes if len(box[3].strip()) > NOISE_CHARS] or [0])
    return [i for i, box in enumerate(boxes) if re.fullmatch('[A-Z]', box[3].strip()) and box[2] > CAP_HEIGHT * size]


def cap_word(boxes, cap):
    """The paragraph's first word as Gemini read it, with the letter Vision saw as a tall box when Gemini left it
    out (AI for Educators: "ome" for "Some")."""
    word = (cap or {}).get('word', '').strip()
    tall = tall_letters(boxes)
    if word[:1].islower() and tall:
        word = boxes[tall[0]][3].strip() + word
    return word


def cap_line(boxes, cap):
    """The line a drop cap begins: near the line Gemini named, the one whose first word ends the printed word
    ("uturists" for "Futurists"), else the line beside a tall letter ("artificial" beside the A of "As"), else the
    line Gemini named."""
    texts = [box[3].strip() for box in boxes]
    word, named = cap_word(boxes, cap), (cap or {}).get('line')
    if not re.fullmatch(r"[A-Z][\w'’-]*", word) or not isinstance(named, int) or not 0 <= named < len(texts):
        return None
    tall = tall_letters(boxes)
    near = [i for i in range(max(0, named - CAP_REACH), min(len(texts), named + CAP_REACH + 1)) if i not in tall]
    cut = [i for i in near if (first := texts[i].split(' ', 1)[0]) and word.endswith(first) and len(first) < len(word)]
    if cut:
        return min(cut, key=lambda i: abs(i - named))
    for j in tall:
        left, top, height, _ = boxes[j]
        beside = [i for i, box in enumerate(boxes) if i not in tall and box[0] > left and top - box[2] <= box[1] <= top + height / 2]
        if beside:
            return min(beside, key=lambda i: boxes[i][1])
    return None if named in tall else named


def reread_beside_cap(image, boxes, page):
    """Read again the lines beside a drop cap. With the cap in the way Vision can merge it with the next line and
    misread both (AI for Educators: "spin for years and years." came out as "Cine at and yours.") or lose the
    letters next to it; read with the cap left out, the lines come back right. Returns the boxes and the layout
    renumbered."""
    from img2txt import vision_lines
    cap = page.get('drop_cap')
    i = cap_line(boxes, cap)
    if i is None or any(i in group for group in page.get('code', [])):
        return boxes, page
    left, top, height, _ = boxes[i]
    big = [box for box in boxes if box[0] < left - CAP_GAP and box[2] > CAP_HEIGHT * height
           and top - height <= box[1] <= top + CAP_LINES * height]
    if not big:
        return boxes, page
    cap_box = big[0]
    span = (min(top, cap_box[1]) - CAP_GAP / 2, cap_box[1] + cap_box[2] + CAP_GAP / 2)
    in_span = lambda box: span[0] <= box[1] + box[2] / 2 <= span[1]
    beside = [box for box in boxes if in_span(box) and box[0] > cap_box[0] + CAP_GAP]
    region = (min(box[0] for box in beside + [boxes[i]]) - CAP_GAP / 2, span[0], 1, span[1])
    try:
        new = vision_lines(image, region)
    except Exception:
        return boxes, page
    if not new:
        return boxes, page
    kept = [(j, box) for j, box in enumerate(boxes) if not (in_span(box) and (box is cap_box or box[0] > cap_box[0] + CAP_GAP))]
    merged = sorted([(box[1], 0, j, box) for j, box in kept] + [(box[1], 1, -1, box) for box in new])
    number = {j: n for n, (_, kind, j, _) in enumerate(merged) if kind == 0}
    first_new = next(n for n, (_, kind, _, _) in enumerate(merged) if kind == 1)
    renumber = lambda lines: [number[j] for j in lines if j in number]
    page = dict(page, titles=[dict(t, line=number[t['line']]) for t in page.get('titles', []) if t['line'] in number],
                quotes=renumber(page.get('quotes', [])), art=renumber(page.get('art', [])),
                figures=[dict(f, lines=renumber(f.get('lines', []))) for f in page.get('figures', [])],
                code=[renumber(group) for group in page.get('code', [])], drop_cap=dict(cap, line=first_new))
    return [box for _, _, _, box in merged], page


def drop_cap(texts, boxes, cap, art, code=()):
    """Give a paragraph its first word back; Gemini gives the word as printed, cap_line finds its line. Without a
    word from Gemini, the line beside a tall letter, to its right, takes the letter. Code lines are left as read."""
    tall = tall_letters(boxes)
    i = cap_line(boxes, cap)
    if i is None or i in code:
        for j in tall:
            left, top, height, letter = boxes[j]
            beside = [k for k, box in enumerate(boxes) if k not in tall and k not in code and box[0] > left
                      and top - box[2] <= box[1] <= top + height / 2]
            if beside:
                k = min(beside, key=lambda k: boxes[k][1])
                texts[k] = letter.strip() + texts[k]
                art.add(j)
        return
    word = cap_word(boxes, cap)
    first = texts[i].split(' ', 1)[0]
    if first and word.endswith(first) and len(first) < len(word):
        texts[i] = word + texts[i][len(first):]
    elif first != word:
        texts[i] = word + ' ' + texts[i]
    art.update(j for j in tall if texts[j] == word[0])


def comparable(text):
    """Letters and Japanese only, lower case, with Vision's I/l confusion folded (AI read as Al)."""
    return re.sub(r'[^a-z\u3040-\u30ff\u4e00-\u9fff]', '', text.lower().replace('l', 'i'))


def contents_entries(images):
    """The entries of the book's contents page, as comparable strings: every line below a "Contents" line in the
    front matter. Empty when the book has no such page."""
    for image in images[:max(1, round(len(images) * CONTENTS_PAGES))]:
        if not image.with_suffix(LINES).exists():
            continue
        lines = [line[3].strip() for line in sorted_boxes(image)]
        start = next((i for i, text in enumerate(lines) if CONTENTS.match(text)), None)
        if start is not None:
            entries = [comparable(re.sub(r'^\d+\.?\s*', '', text)) for text in lines[start + 1:]]
            return [entry for entry in entries if len(entry) >= 3]
    return []


def chapter_level(text, level, chapters):
    """Level 1 for a title the contents page lists (or the contents heading itself), level 2 or deeper otherwise.

    Gemini sees eight pages at a time, so its levels drift between batches (AI for Educators: tips 17 to 26 came
    back as chapters); the book's own contents page settles them. Without one, Gemini's level stands.
    """
    if not chapters:
        return level
    return 1 if CONTENTS.match(text.strip()) or listed(text, chapters) else max(level, 2)


def listed(text, chapters):
    """Whether the contents lists this title: the same letters, or one begins the other (an entry cut mid-line, or two
    entries read as one line)."""
    key = comparable(re.sub(r'^(?:(?:chapter|part)\s+)?\d+\.?\s*', '', text, flags=re.I))
    return any(key and (key == entry or len(key) >= 8 and (entry.startswith(key) or key.startswith(entry)))
               for entry in chapters)
