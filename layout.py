"""
Give Vision's pages the structure Vision cannot see: titles, pull quotes, figures, art, drop caps and code.

Vision reads the words well, but a title, a pull quote, a sidebar label and a line in a box look alike in its line
boxes, and their place and size vary from book to book (AI for Educators sets its section titles in dark bands at the
body margin and its pull quotes centred in grey boxes). So img2txt.py keeps Vision's line boxes per page
(`<page>.lines.json`), and a model that sees the page image names, by line number, what each special line is.
It answers with line numbers and a few words, never the book's text, so RECITATION cannot refuse it, and twenty pages
go in one request. The answers are kept in layout.json, with the provider and the model that read each page, so a
re-run asks only for pages not yet read.

Providers are tried in order (Sonnet first, then Gemini, Mistral, GLM-4.6V-Flash, an OpenRouter :free vision model,
then local Ollama). One that is out of quota, rate-limited, unauthorized or unreachable is remembered until its reset
and the next one reads the pages. The book is not parked while a later provider remains. Every provider is given
LAYOUT_SCHEMA, rendered for that API. OpenAI's free tokens are not used: they share the pages, and the books are
copyrighted.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from google.genai import types

from img2txt import (BODY_LINE_CHARS, DECORATIVE_DIGITS, HEADING_NUMBER, LINES, LIVE, ROW, WORKERS, page_images,
                     vision_markdown)

LAYOUT = 'layout.json'
MODEL = 'gemini-3.5-flash-lite'
BATCH = 20  # pages per request; the free tier allows 500 requests a day, which 8 a request used up on 2026-10-10
DAILY_LIMIT = 'PerDay'  # in a 429 for the day's requests, which waiting minutes does not end
PROVIDERS = ('claude', 'gemini', 'mistral', 'glm', 'openrouter', 'ollama')
CLAUDE_MODELS = ('sonnet', 'haiku')
MISTRAL_MODEL = 'mistral-small-latest'
GLM_MODEL = 'glm-4.6v-flash'
OPENROUTER_MODEL = 'qwen/qwen3.8-27b:free'
OLLAMA_MODEL = 'qwen2.5vl:7b'  # about 6 GB; a 16 GB M1 holds it. An 8 GB Mac should set qwen2.5vl:3b.
OLLAMA_BATCH = 4
MISTRAL_URL = 'https://api.mistral.ai/v1/chat/completions'
GLM_URL = 'https://api.z.ai/api/paas/v4/chat/completions'
OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
OLLAMA_HOST = 'http://127.0.0.1:11434'
# Remembered across books. A provider that is out stays out until the time below, so the next book does not spend
# the request that finds the same limit.
PROVIDER_STATE = os.path.join(os.path.expanduser('~/Downloads/kindle'), 'layout-providers.json')
CLAUDE_RESET_S = 5 * 3600  # Claude Code's session limit, when the reply does not name an earlier time
RATE_RESET_S = 60
OTHER_RESET_S = 1800


class GeminiFailed(RuntimeError):
    """Every layout provider still in the list failed. The pages already read stay in layout.json. On 2026-10-10
    every request failed for ten minutes before anyone noticed, because the run went on and only the verdict was watched."""
CLAUDE_MODEL = 'sonnet'
CLAUDE_WORKERS = 3
CLAUDE_TIMEOUT_S = 900
CHAT_TIMEOUT_S = 180
OLLAMA_TIMEOUT_S = 600
CLAUDE = 'claude'
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

# One schema for a batch of pages. Claude takes it as --json-schema (additionalProperties included).
# Claude Code refuses a top-level array, so the pages sit under `pages`. Gemini gets for_gemini(): the
# Developer API rejects additionalProperties with 400 Unknown name (B005J3IEZQ, 2026-10-12, 2282ee7).
_INT = {'type': 'integer'}
_LINE_NUMBERS = {'type': 'array', 'items': _INT}


def _object(required, properties):
    return {'type': 'object', 'additionalProperties': False, 'required': required, 'properties': properties}


LAYOUT_SCHEMA = _object(['pages'], {'pages': {'type': 'array', 'items': _object(
    ['page', 'titles', 'quotes', 'figures', 'art', 'code'], {
        'page': {'type': 'string'},
        'titles': {'type': 'array', 'items': _object(['line', 'level'], {'line': _INT, 'level': _INT})},
        'quotes': _LINE_NUMBERS,
        'figures': {'type': 'array', 'items': _object(
            ['lines', 'description'], {'lines': _LINE_NUMBERS, 'description': {'type': 'string'}})},
        'art': _LINE_NUMBERS,
        'code': {'type': 'array', 'items': _LINE_NUMBERS},
        'drop_cap': _object(['line', 'word'], {'line': _INT, 'word': {'type': 'string'}})})}})

_GEMINI_TYPES = {'integer': types.Type.INTEGER, 'string': types.Type.STRING, 'array': types.Type.ARRAY,
                 'object': types.Type.OBJECT, 'boolean': types.Type.BOOLEAN, 'number': types.Type.NUMBER}


def for_gemini(schema):
    """LAYOUT_SCHEMA as Gemini's response_schema can carry it. additionalProperties is Claude's key."""
    if isinstance(schema, list):
        return [for_gemini(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {key: for_gemini(value) for key, value in schema.items() if key != 'additionalProperties'}


def gemini_schema(schema):
    """Gemini's Schema for a JSON Schema. additionalProperties is not copied: the API rejects it."""
    schema = for_gemini(schema)
    kwargs = {'type': _GEMINI_TYPES[schema['type']]}
    if 'properties' in schema:
        kwargs['properties'] = {key: gemini_schema(value) for key, value in schema['properties'].items()}
    if 'items' in schema:
        kwargs['items'] = gemini_schema(schema['items'])
    if 'required' in schema:
        kwargs['required'] = list(schema['required'])
    return types.Schema(**kwargs)


def gemini_rejected(schema, path='response_schema'):
    """Where a Gemini Schema payload carries additional_properties. The SDK model has the field; the Developer
    API answers 400 Unknown name. The message names gemini_schema, which omits it."""
    data = schema.model_dump(exclude_none=True) if isinstance(schema, types.Schema) else schema
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in ('additional_properties', 'additionalProperties'):
                found.append(f'{path}: additional_properties is sent to Gemini, which returns 400 Unknown name; '
                             f'omit it in gemini_schema (LAYOUT_SCHEMA keeps additionalProperties for Claude)')
            found.extend(gemini_rejected(value, f'{path}.{key}'))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            found.extend(gemini_rejected(item, f'{path}[{i}]'))
    return found


def as_json(schema):
    """The JSON Schema a Gemini Schema was built from."""
    data = schema.model_dump(exclude_none=True) if isinstance(schema, types.Schema) else schema
    kind = data['type']
    out = {'type': kind.value.lower() if hasattr(kind, 'value') else str(kind).lower()}
    if 'properties' in data:
        out['properties'] = {key: as_json(value) for key, value in data['properties'].items()}
    if 'items' in data:
        out['items'] = as_json(data['items'])
    if 'required' in data:
        out['required'] = data['required']
    if 'additional_properties' in data:
        out['additionalProperties'] = data['additional_properties']
    return out


SCHEMA = gemini_schema(LAYOUT_SCHEMA)


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
    return laid_out(json.loads(response.text), {image.stem for image in images}, model=MODEL, provider='gemini')


def laid_out(value, stems, model=None, provider=None):
    """{stem: layout} for the pages of a schema answer that belong to this batch."""
    out = {}
    for page in value['pages']:
        stem = page.get('page') if isinstance(page, dict) else None
        if stem not in stems:
            continue
        page.pop('page')
        if provider:
            page['provider'] = provider
        if model:
            page['model'] = model
        out[stem] = page
    return out


def claude_cli():
    """The Claude Code CLI, also where its installer puts it when a background job's PATH leaves that out."""
    return shutil.which('claude') or next(
        (str(p) for p in [os.path.expanduser('~/.local/bin/claude'), os.path.expanduser('~/.claude/local/claude')]
         if os.access(p, os.X_OK)), None)


def claude_model(config):
    """`CLAUDE_MODEL` in config.env. Sonnet is the default; Haiku is the other model the CLI is asked for."""
    name = (config or {}).get('CLAUDE_MODEL') or CLAUDE_MODEL
    if name not in CLAUDE_MODELS:
        raise RuntimeError(f'CLAUDE_MODEL must be sonnet or haiku, not {name}')
    return name


def model_of(name, config):
    config = config or {}
    if name == 'claude':
        return claude_model(config)
    if name == 'gemini':
        return MODEL
    if name == 'mistral':
        return config.get('MISTRAL_MODEL') or MISTRAL_MODEL
    if name == 'glm':
        return config.get('GLM_MODEL') or GLM_MODEL
    if name == 'openrouter':
        return config.get('OPENROUTER_MODEL') or OPENROUTER_MODEL
    if name == 'ollama':
        return config.get('OLLAMA_MODEL') or OLLAMA_MODEL
    raise RuntimeError(f'Unknown layout provider {name}')


def provider_order(config, cli, client, limit=None, ollama=None):
    """Providers that can be asked, in the configured order. Sonnet is first unless config.env says otherwise.
    A provider with no key is skipped. Ollama is skipped when nothing is listening, so a Mac without it does not
    pause the job. OpenAI is refused: its free tokens share the pages."""
    config = config or {}
    raw = config.get('LAYOUT_PROVIDERS') or ','.join(PROVIDERS)
    names = [part.strip() for part in raw.split(',') if part.strip()]
    if any(name == 'openai' or name.startswith('openai/') for name in names):
        raise RuntimeError('OpenAI is not a layout provider: its free tokens share the pages, and the books are copyrighted')
    unknown = [name for name in names if name not in PROVIDERS]
    if unknown:
        raise RuntimeError(f'Unknown layout provider {unknown[0]}')
    if limit == 'sonnet':
        names = ['claude']
    elif limit == 'gemini':
        names = ['gemini']
    ready = ollama_ready(config) if ollama is None else ollama
    order = []
    for name in names:
        if name == 'claude' and not cli:
            continue
        if name == 'gemini' and not client:
            continue
        if name == 'mistral' and not config.get('MISTRAL_API_KEY'):
            continue
        if name == 'glm' and not config.get('ZAI_API_KEY'):
            continue
        if name == 'openrouter' and not config.get('OPENROUTER_API_KEY'):
            continue
        if name == 'openrouter' and not free_model(model_of(name, config)):
            print(f'OPENROUTER_MODEL {model_of(name, config)} is not a :free model; skipping', flush=True)
            continue
        if name == 'ollama' and not ready:
            continue
        order.append(name)
    return order


def free_model(model):
    return model == 'openrouter/free' or model.endswith(':free')


def ollama_ready(config):
    host = ((config or {}).get('OLLAMA_HOST') or OLLAMA_HOST).rstrip('/')
    try:
        with urllib.request.urlopen(host + '/api/tags', timeout=1) as res:
            return res.status == 200
    except Exception:
        return False


def closed_objects(schema, path='$'):
    """Objects without additionalProperties: false. Claude's --json-schema and Mistral's strict json_schema reject them.
    A top-level array is the shape Claude Code refuses, which is why pages sit under `pages`."""
    found = []
    if isinstance(schema, dict):
        if path == '$' and schema.get('type') != 'object':
            found.append(f'{path}: the root must be an object; Claude rejects a top-level array')
        if schema.get('type') == 'object' and schema.get('additionalProperties') is not False:
            found.append(f'{path}: additionalProperties must be false; Claude and Mistral strict mode reject an open object')
        for key, value in schema.items():
            found += closed_objects(value, f'{path}.{key}')
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            found += closed_objects(value, f'{path}[{i}]')
    return found


def request_schema(provider, config=None):
    """The schema-bearing part of a request, with no page bytes. Offline checks read this."""
    config = config or {}
    model = model_of(provider, config) if provider != 'claude' else CLAUDE_MODEL
    if provider == 'gemini':
        return SCHEMA
    if provider == 'claude':
        return LAYOUT_SCHEMA
    if provider == 'glm':
        return chat_body('glm', [], config, model)
    if provider == 'ollama':
        return ollama_body([], config, model)
    if provider in ('mistral', 'openrouter'):
        return chat_body(provider, [], config, model)
    raise RuntimeError(f'Unknown layout provider {provider}')


def provider_rejected(provider, payload=None, config=None):
    """What this provider's API refuses in the rendered request. Empty means the request may be sent.
    Gemini's check is gemini_rejected. The others name the shape their API documents."""
    payload = request_schema(provider, config) if payload is None else payload
    if provider == 'gemini':
        return gemini_rejected(payload)
    if provider == 'claude':
        return closed_objects(payload)
    if provider == 'mistral':
        return mistral_rejected(payload)
    if provider == 'glm':
        return glm_rejected(payload)
    if provider == 'openrouter':
        return openrouter_rejected(payload)
    if provider == 'ollama':
        return ollama_rejected(payload)
    return [f'Unknown layout provider {provider}']


def mistral_rejected(body):
    fmt = (body or {}).get('response_format') or {}
    schema = (fmt.get('json_schema') or {}).get('schema')
    if fmt.get('type') != 'json_schema' or not isinstance(schema, dict):
        return ['Mistral takes response_format type json_schema with LAYOUT_SCHEMA under json_schema.schema']
    return [f'Mistral strict: {item}' for item in closed_objects(schema)]


def glm_rejected(body):
    """GLM's documented response_format is text or json_object. json_schema is not in that enum, so the schema
    is carried in the prompt and the body asks for a JSON object."""
    fmt = (body or {}).get('response_format') or {}
    if fmt.get('type') != 'json_object' or set(fmt) - {'type'}:
        return ['GLM accepts response_format type json_object only; do not send json_schema']
    texts = []
    for message in (body or {}).get('messages') or []:
        content = message.get('content')
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts += [part.get('text', '') for part in content if isinstance(part, dict)]
    blob = '\n'.join(texts)
    if '"titles"' not in blob or '"pages"' not in blob:
        return ['GLM is json_object, so the prompt must carry LAYOUT_SCHEMA']
    return []


def openrouter_rejected(body):
    model = (body or {}).get('model') or ''
    if not free_model(model):
        return [f'OpenRouter model {model} is not a :free model; paid models are not used for these pages']
    fmt = (body or {}).get('response_format') or {}
    schema = (fmt.get('json_schema') or {}).get('schema')
    if fmt.get('type') != 'json_schema' or not isinstance(schema, dict):
        return ['OpenRouter takes response_format type json_schema with LAYOUT_SCHEMA']
    return [f'OpenRouter schema: {item}' for item in closed_objects(schema)]


def ollama_rejected(body):
    """Ollama's format takes the schema itself. The string "json" is JSON mode, which is free text and the way the
    Steve Jobs reply gained a sentence after the array."""
    fmt = (body or {}).get('format')
    if not isinstance(fmt, dict) or fmt.get('type') != 'object':
        return ['Ollama format must be LAYOUT_SCHEMA, not the string "json"']
    return []


def numbered_lines(image):
    return '\n'.join(f'{i}: {line}' for i, line in enumerate(page_lines(image)))


def data_url(image):
    return 'data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode()


def chat_parts(images, schema_text=None, image_url='object'):
    text = PROMPT
    if schema_text:
        text += '\n\nReply as JSON matching this schema:\n' + schema_text
    parts = [{'type': 'text', 'text': text}]
    for image in images:
        parts.append({'type': 'text', 'text': f'Page "{image.stem}":\n{numbered_lines(image)}'})
        url = data_url(image)
        parts.append({'type': 'image_url', 'image_url': url if image_url == 'string' else {'url': url}})
    return parts


def chat_body(provider, images, config, model):
    if provider == 'glm':
        parts = chat_parts(images, json.dumps(LAYOUT_SCHEMA), image_url='object')
        fmt = {'type': 'json_object'}
    elif provider == 'mistral':
        parts = chat_parts(images, image_url='string')
        fmt = {'type': 'json_schema', 'json_schema': {'name': 'layout', 'strict': True, 'schema': LAYOUT_SCHEMA}}
    else:
        parts = chat_parts(images, image_url='object')
        fmt = {'type': 'json_schema', 'json_schema': {'name': 'layout', 'strict': True, 'schema': LAYOUT_SCHEMA}}
    return {'model': model, 'temperature': 0, 'messages': [{'role': 'user', 'content': parts}], 'response_format': fmt}


def ollama_body(images, config, model):
    return {'model': model, 'stream': False, 'format': LAYOUT_SCHEMA, 'options': {'temperature': 0},
            'messages': [{'role': 'user', 'content': PROMPT + '\n\n' + '\n\n'.join(
                f'Page "{image.stem}":\n{numbered_lines(image)}' for image in images),
                'images': [base64.b64encode(image.read_bytes()).decode() for image in images]}]}


def endpoint(provider, config):
    config = config or {}
    if provider == 'mistral':
        return MISTRAL_URL, {'Authorization': f'Bearer {config.get("MISTRAL_API_KEY", "")}', 'Content-Type': 'application/json'}
    if provider == 'glm':
        return GLM_URL, {'Authorization': f'Bearer {config.get("ZAI_API_KEY", "")}', 'Content-Type': 'application/json'}
    if provider == 'openrouter':
        return OPENROUTER_URL, {'Authorization': f'Bearer {config.get("OPENROUTER_API_KEY", "")}',
                                'Content-Type': 'application/json'}
    host = (config.get('OLLAMA_HOST') or OLLAMA_HOST).rstrip('/')
    return host + '/api/chat', {'Content-Type': 'application/json'}


def http_json(url, body, headers, timeout=CHAT_TIMEOUT_S):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors='replace')[:500]
        raise RuntimeError(f'{e.code} {detail}') from None
    except urllib.error.URLError as e:
        raise RuntimeError(f'unavailable: {e.reason}') from None


def chat_content(reply):
    if 'message' in reply and 'choices' not in reply:
        return (reply.get('message') or {}).get('content') or '', reply.get('model')
    choice = (reply.get('choices') or [{}])[0]
    content = (choice.get('message') or {}).get('content') or ''
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    return content, reply.get('model')


def parse_layout(content, stems, model, provider):
    try:
        value = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'not JSON: {e}') from None
    if not isinstance(value, dict) or 'pages' not in value:
        raise RuntimeError('the reply has no pages')
    return laid_out(value, stems, model=model, provider=provider)


def ask_chat(provider, images, config, model):
    url, headers = endpoint(provider, config)
    reply = http_json(url, chat_body(provider, images, config, model), headers)
    content, used = chat_content(reply)
    if not content:
        raise RuntimeError(f'{provider} returned nothing')
    return parse_layout(content, {image.stem for image in images}, used or model, provider)


def ask_ollama(images, config, model):
    merged = {}
    url, headers = endpoint('ollama', config)
    for start in range(0, len(images), OLLAMA_BATCH):
        batch = images[start:start + OLLAMA_BATCH]
        reply = http_json(url, ollama_body(batch, config, model), headers, timeout=OLLAMA_TIMEOUT_S)
        content, used = chat_content(reply)
        if not content:
            raise RuntimeError('ollama returned nothing')
        merged.update(parse_layout(content, {image.stem for image in batch}, used or model, 'ollama'))
    return merged


def ask_claude(cli, prompt, schema, *, cwd, add_dir, model):
    """The only `claude -p`. `--json-schema` puts the value in `structured_output`. `result` is the model's prose:
    on 2026-10-12 (Steve Jobs) a sentence after the array was read as the layout, and the book stopped."""
    if model not in CLAUDE_MODELS:
        raise RuntimeError(f'CLAUDE_MODEL must be sonnet or haiku, not {model}')
    run = subprocess.run(
        [cli, '-p', '--model', model, '--output-format', 'json', '--json-schema', json.dumps(schema),
         '--allowedTools', 'Read', '--add-dir', str(add_dir), '--strict-mcp-config',
         '--settings', '{"disableAllHooks": true}'],
        input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S,
        cwd=cwd, env=dict(os.environ, LLM_WIKI_WATCHER='1'))
    reply = json.loads(run.stdout) if run.stdout.strip().startswith('{') else {}
    output = reply.get('structured_output')
    if run.returncode or reply.get('is_error') or not isinstance(output, dict):
        detail = reply.get('result') or run.stderr or run.stdout or ''
        raise RuntimeError(f'claude -p exited {run.returncode}: {detail[:300]}')
    return output


def ask_layout_claude(cli, images, model=None):
    """Sonnet or Haiku's reading of a batch through `claude -p`: it opens each page image with its Read tool.
    Hooks are off, and LLM_WIKI_WATCHER tells the wiki's own SessionEnd hook to skip them, so these calls stay out of
    the session log the LLM wiki grows from (Laiken, 2026-10-10: seven of them had reached its queue)."""
    model = model or CLAUDE_MODEL
    parts = [PROMPT, 'Open each page image with the Read tool before answering.']
    for image in images:
        parts += [f'Page "{image.stem}": image {image.resolve()}', numbered_lines(image)]
    value = ask_claude(cli, '\n\n'.join(parts), LAYOUT_SCHEMA, cwd=images[0].parent,
                       add_dir=images[0].parent.resolve(), model=model)
    return laid_out(value, {image.stem for image in images}, model=model, provider='claude')


def display(name, model):
    if name == 'claude':
        return 'Haiku' if model == 'haiku' else 'Sonnet'
    if name == 'gemini':
        return 'Gemini'
    return name


def failure_kind(error):
    text = str(error)
    low = text.lower()
    if 'INVALID_ARGUMENT' in text or 'additional_properties' in low:
        return 'schema'
    if any(mark in text for mark in ('401', '403', 'PERMISSION_DENIED')) or 'invalid api key' in low or 'unauthorized' in low:
        return 'auth'
    if 'perday' in low or 'per day' in low or DAILY_LIMIT.lower() in low or 'usage limit' in low:
        return 'quota'
    if any(mark in low for mark in ('429', 'resource_exhausted', 'rate limit', 'rate_limit', 'too many requests')):
        return 'rate'
    if any(mark in low for mark in ('unavailable', 'connection refused', 'timed out', 'timeout', 'model not found', '502', '503', '504')):
        return 'unavailable'
    return 'other'


def block_until(name, kind, now):
    """When this provider may be asked again. A schema 400 is the request we send, so it waits for new code, not a clock."""
    if kind == 'schema':
        return None
    if kind == 'rate':
        return now + RATE_RESET_S
    if kind == 'quota' and name == 'gemini':
        pacific = datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles'))
        return (pacific.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
    if kind == 'quota' and name == 'claude':
        return now + CLAUDE_RESET_S
    if kind == 'quota':
        utc = datetime.fromtimestamp(now, ZoneInfo('UTC'))
        return (utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
    return now + OTHER_RESET_S


def converter_version():
    return hashlib.sha256(open(__file__, 'rb').read()).hexdigest()[:12]


def key_hash(config, name):
    secret = {'mistral': 'MISTRAL_API_KEY', 'glm': 'ZAI_API_KEY', 'openrouter': 'OPENROUTER_API_KEY',
              'gemini': 'GEMINI_API_KEY'}.get(name)
    return hashlib.sha256(((config or {}).get(secret, '') if secret else '').encode()).hexdigest()[:12]


def load_blocks(path, now, config, converter):
    if not path or not os.path.exists(path):
        return {}
    try:
        data = json.loads(open(path, encoding='utf-8').read())
    except (OSError, json.JSONDecodeError):
        return {}
    kept = {}
    for name, rec in data.items() if isinstance(data, dict) else []:
        if not isinstance(rec, dict):
            continue
        if rec.get('kind') == 'schema' and rec.get('converter') != converter:
            continue
        if rec.get('kind') == 'auth' and rec.get('key') != key_hash(config, name):
            continue
        until = rec.get('until')
        if until is not None and until <= now:
            continue
        kept[name] = rec
    return kept


def save_blocks(path, blocks):
    if not path:
        return
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(blocks, fh)


class Roster:
    """Which providers are out for this run and for later books, and the errors to report when none remain."""

    def __init__(self, order, blocks, now, config, path, converter):
        self.order = list(order)
        self.blocks = blocks
        self.now = now
        self.config = config
        self.path = path
        self.converter = converter
        self.lock = threading.Lock()
        self.errors = []

    def first_open(self):
        with self.lock:
            return next((name for name in self.order if name not in self.blocks), None)

    def open(self, name):
        with self.lock:
            return name not in self.blocks

    def block(self, name, model, error):
        kind = failure_kind(error)
        rec = {'kind': kind, 'until': block_until(name, kind, self.now), 'reason': str(error)[:300],
               'converter': self.converter, 'key': key_hash(self.config, name)}
        with self.lock:
            if name not in self.blocks:
                self.blocks[name] = rec
                self.errors.append((display(name, model), str(error)))
            save_blocks(self.path, self.blocks)
        print(f'{display(name, model)} is out ({kind}): {error}', flush=True)

    def message(self, left):
        body = '; '.join(f'{label}: {text[:220]}' for label, text in self.errors) or 'no layout provider is configured'
        times = [rec['until'] for rec in self.blocks.values() if rec.get('until')]
        extra = ''
        if any(DAILY_LIMIT in text for _, text in self.errors):
            extra += " Today's requests are used up; they come back at midnight Pacific (16:00 or 17:00 JST)."
        if times:
            extra += f' Resets at {int(min(times))}.'
        elif any(rec.get('kind') == 'schema' for rec in self.blocks.values()) and 'INVALID_ARGUMENT' not in body:
            extra += ' INVALID_ARGUMENT'
        return f'Layout failed with {left} pages left: {body}.{extra}'


def call_provider(name, images, model, client, cli, config):
    if name == 'claude':
        return ask_layout_claude(cli, images, model)
    if name == 'gemini':
        return ask_layout(client, images)
    if name == 'ollama':
        return ask_ollama(images, config, model)
    return ask_chat(name, images, config, model)


def mark_layout(folder, client, images, cli=None, config=None, limit=None, now=None, state_path=None):
    """Read the layout of the pages in images not read yet, and rewrite every page in images from it.

    Providers run in order. One that fails is not asked for the rest of the book; the next one is. Returns the
    pages still without a layout when every provider that was asked succeeded for the pages it was given. When
    every provider is out, raises GeminiFailed and the convert job waits for the earliest reset.
    """
    config = {} if config is None else config
    now = time.time() if now is None else now
    layout = read_layout(folder)
    again = code_rereads(folder, layout)
    todo = [image for image in images if (image.stem not in layout or image.stem in again)
            and (image.with_suffix(LINES).exists() or image.with_suffix('.txt').exists())]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    order = provider_order(config, cli, client, limit) if todo else []
    if 'claude' in order:
        claude_model(config)
    path = PROVIDER_STATE if state_path is None else state_path
    roster = Roster(order, load_blocks(path, now, config, converter_version()), now, config, path, converter_version())
    slots = {'claude': threading.Semaphore(CLAUDE_WORKERS), 'glm': threading.Semaphore(1)}

    def ask(batch):
        while True:
            name = roster.first_open()
            if name is None:
                return {}, True
            sem = slots.get(name)
            if sem:
                sem.acquire()
            try:
                if not roster.open(name):
                    continue
                model = model_of(name, config)
                return call_provider(name, batch, model, client, cli, config), False
            except Exception as e:
                roster.block(name, model_of(name, config), e)
            finally:
                if sem:
                    sem.release()

    failed = False
    if todo and not order:
        failed = True
    with ThreadPoolExecutor(WORKERS) as pool:
        for done, (pages, error) in enumerate(pool.map(ask, batches), 1):
            if error:
                print(f'layout [{done}/{len(batches)}]: {roster.message(0)}', flush=True)
                failed = True
            elif pages:
                model = next(iter(pages.values())).get('model')
                print(f'[layout {done}/{len(batches)}] {len(pages)} pages by {model}', flush=True)
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
    if failed:
        raise GeminiFailed(roster.message(len(left)))
    return left


def signature(page, key):
    if key == 'titles':
        return tuple(sorted((item.get('line'), item.get('level')) for item in page.get('titles') or [] if isinstance(item, dict)))
    if key == 'quotes':
        return tuple(sorted(i for i in page.get('quotes') or [] if isinstance(i, int)))
    if key == 'figures':
        return tuple(sorted((tuple(sorted(item.get('lines') or [])), (item.get('description') or '').strip())
                            for item in page.get('figures') or [] if isinstance(item, dict)))
    return tuple(sorted(tuple(run) for run in page.get('code') or []))


def layout_report(left_name, right_name, left, right):
    """Where two readings of the same pages differ, in the fields the layout is for."""
    lines = [f'{left_name} vs {right_name}']
    counts = {key: 0 for key in ('same', 'titles', 'quotes', 'figures', 'code', 'missing')}
    for stem in sorted(set(left) | set(right)):
        a, b = left.get(stem), right.get(stem)
        if not a or not b:
            counts['missing'] += 1
            lines.append(f'{stem}: missing from {left_name if not a else right_name}')
            continue
        diffs = [key for key in ('titles', 'quotes', 'figures', 'code') if signature(a, key) != signature(b, key)]
        if not diffs:
            counts['same'] += 1
            continue
        for key in diffs:
            counts[key] += 1
        lines.append(f'{stem}: {", ".join(diffs)}')
        for key in diffs:
            lines.append(f'  {key} {left_name}: {signature(a, key)}')
            lines.append(f'  {key} {right_name}: {signature(b, key)}')
    lines.append(f'{counts["same"]} same; titles {counts["titles"]}; quotes {counts["quotes"]}; '
                 f'figures {counts["figures"]}; code {counts["code"]}')
    return '\n'.join(lines)


def parse_spec(spec, config):
    """`sonnet` and `haiku` are the Claude CLI with that model. Anything else is a provider name, or `provider:model`."""
    spec = spec.strip()
    if spec in CLAUDE_MODELS:
        return 'claude', spec
    if spec == 'claude':
        return 'claude', claude_model(config)
    if ':' in spec:
        name, model = spec.split(':', 1)
        if name == 'claude' and model not in CLAUDE_MODELS:
            raise RuntimeError(f'CLAUDE_MODEL must be sonnet or haiku, not {model}')
        if name not in PROVIDERS:
            raise RuntimeError(f'Unknown layout provider {name}')
        return name, model
    if spec not in PROVIDERS:
        raise RuntimeError(f'Unknown layout provider {spec}')
    return spec, model_of(spec, config)


def compare(folder, spec_a, spec_b, n, *, ask, config=None):
    """Lay out the first n pages twice and report title, quote, figure and code differences. Writes nothing."""
    config = {} if config is None else config
    images = page_images(folder)[:n]
    if not images:
        raise RuntimeError(f'No page images in {folder}')
    name_a, model_a = parse_spec(spec_a, config)
    name_b, model_b = parse_spec(spec_b, config)
    return layout_report(f'{name_a}:{model_a}', f'{name_b}:{model_b}',
                         ask(name_a, model_a, images), ask(name_b, model_b, images))


def main(argv=None):
    import argparse
    from img2txt import load_config
    from google import genai

    parser = argparse.ArgumentParser(description='Lay out the same pages with two providers and report differences')
    parser.add_argument('folder', type=Path)
    parser.add_argument('--a', required=True, help='sonnet, haiku, or a provider (gemini, mistral, glm, openrouter, ollama)')
    parser.add_argument('--b', required=True)
    parser.add_argument('-n', type=int, default=8)
    args = parser.parse_args(argv)
    config = load_config()
    key = config.get('GEMINI_API_KEY')
    client = genai.Client(api_key=key) if key else None
    cli = claude_cli()

    def ask(name, model, images):
        return call_provider(name, images, model, client, cli, config)

    print(compare(args.folder, args.a, args.b, args.n, ask=ask, config=config))


if __name__ == '__main__':
    main()


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


CHAPTER_LABEL = re.compile(r'第[^章]{0,3}章')  # 第Ⅲ章, which Live Text reads as 第山章 or 第i章


def contents_covered(entry, titles):
    """Whether the book's headings account for a contents entry: one heading lists it, or the entry is several
    headings read as one line, the last perhaps cut mid-title (代替医療解剖 and 近代中国史, 2026-10-12)."""
    if any(listed(t, [entry]) for t in titles):
        return True
    keys = {k for k in (CHAPTER_LABEL.sub('', comparable(t)) for t in titles) if len(k) >= 2}
    rest = CHAPTER_LABEL.sub('', entry)
    reach = {0}
    for i in range(len(rest)):
        if i in reach:
            reach |= {i + len(k) for k in keys if rest.startswith(k, i)}
    return bool(rest) and any(r == len(rest) or len(rest) - r >= 2 and any(k.startswith(rest[r:]) for k in keys)
                              for r in reach if r > 0)


def listed(text, chapters):
    """Whether the contents lists this title: the same letters, or one begins the other (an entry cut mid-line, or two
    entries read as one line)."""
    key = comparable(re.sub(r'^(?:(?:chapter|part)\s+)?\d+\.?\s*', '', text, flags=re.I))
    return any(key and (key == entry or len(key) >= 8 and (entry.startswith(key) or key.startswith(entry)))
               for entry in chapters)
