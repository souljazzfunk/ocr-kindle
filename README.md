# Kindle OCR

Turn a Kindle book you own into one Markdown file for personal use (notes, study, feeding to an LLM).

1. A Chrome extension pages through the book in Kindle Cloud Reader and sends every page image to `channel.py`, a small server on `127.0.0.1` that `capture_book.py` runs, which writes them into `~/Downloads/<book title>/`. The sample reader also has a screen-reader text layer, saved as each page's exact text; the full-book reader has none.
2. `img2txt.py` OCRs only the pages that have no text yet, then joins all pages into `<book title>.md`.
3. `kc_run.py` runs both, one step at a time, for every book of a list, and checks every result (see **The list**).

The Kindle for Mac app is not part of this flow. `kindle2img.applescript` (last section) is used only when Laiken asks for it, and the capture hook blocks it otherwise.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp config.env.example config.env   # then set GEMINI_API_KEY, and any layout keys below
```

Load the extension once. Open `chrome://extensions`, turn on Developer mode, choose **Load unpacked**, and select the `extension/` folder. From version 2.0 on, the extension reloads itself whenever the channel reports a newer version in this checkout, so it needs loading by hand only once.

## Usage

`capture_book.py <ASIN>` (below) offers the book to the extension through the channel. The extension's worker polls `GET /job` every 30 seconds (every 2 seconds while a job runs and for a minute after), opens the book in a window of its own, and pages through it: back from where the reader opens to the first screen, saving every new screen, then forward to the end, skipping the screens it already saved. Each page goes to the channel as it is taken, so nothing waits in the tab. When the book ends, the channel renames the pages into `page_0001.png` (plus `page_0001.txt` where the text layer had text) in reading order and writes `positions.json` (the reader's "Page N of M" for every page), `capture.json` (extension version, how the start was reached, why the capture stopped early if it did), `dom-probe.json` (the reader page's element structure, no book text, so a selector can be fixed from a record instead of guessed) and `trace.jsonl` (every observation and paging decision); the extension then closes its window. `venv/bin/python channel.py library <domain>` records the library page the same way into `~/Downloads/library-<domain>.json`.

To convert a captured folder on its own:

```bash
venv/bin/python img2txt.py ~/Downloads/<book title>
```

Given a ZIP from extension 1.x, the script first extracts it into `~/Downloads/<book title>/`. It prints how many pages already have text and how many it will OCR, runs 4 OCR requests in parallel, and writes `<book title>.md` into that folder. OCR tries the models in `GEMINI_MODEL` in order (default `gemini-3.8-flash`, then `gemini-3.5-flash-lite` once the free tier's 20 daily requests run out, then `vision`). `vision` is the macOS Vision framework, running locally with no quota. It catches the pages Gemini refuses with `RECITATION`, which happens on many pages of well-known books. Vision reads the words but gives no structure. So `img2txt.py` keeps each page's Vision line boxes (`page_NNNN.lines.json`), and `layout.py` sends the page image with its numbered lines to `gemini-3.5-flash-lite`, eight pages a request. Gemini answers with line numbers only: which lines are titles (and their level), pull quotes, text inside a picture, decoration, program code and its output, and the first word of a paragraph that starts with a drop cap. It never repeats the book's text, so `RECITATION` does not refuse it, and the answers are kept in `layout.json`, so a re-run asks only for pages not yet read. The page is then rebuilt: titles become `#`/`##`, pull quotes `>`, list bullets `-`, a chart or infographic one `![description]()` note, code a fenced block with one printed line per line and its indentation taken from the left edges, and decoration goes. In a programming book (20 or more lines such as `>>>`, `$ `, `def`, `import`), pages laid out before Gemini was asked for code and holding such lines are read once more, and the `code_blocks` check fails while more than a quarter of its code lines stand outside a block. Proofreading leaves code blocks alone. The book's own contents page decides which titles are chapters (`#`), because Gemini's levels drift between requests. A line in capitals that starts where most of the book's titles in capitals start is a title too, when Gemini missed it. The lines beside a drop cap are read again with the cap left out, because Vision can misread them. A page whose last line is short is not joined to the next page. Without `GEMINI_API_KEY`, Vision pages keep titles guessed from the page layout alone (a line set apart from the body, in a larger type or centred), and `check_book.py` fails the `layout` check until Gemini has read every page. It reads horizontal text only, so vertical Japanese needs Gemini. If `GOOGLE_DRIVE_FOLDER` is set, `capture_book.py` copies the file there after its checks and fixes; `img2txt.py` and `proofread.py` on their own write only into the book's folder, so a trial run never replaces the book on Drive.

If any page fails (quota, network), the script exits with an error and keeps the pages that succeeded. Run the same command again on the folder to retry only the missing pages. `--force` re-OCRs everything, `--model` picks another Gemini model, and `--title` sets the output name.

## Layout providers

Page structure is read by the first provider in `LAYOUT_PROVIDERS` that is still available. The default order is Sonnet (`claude -p`), Gemini, Mistral, Z.ai GLM-4.6V-Flash, an OpenRouter `:free` vision model, then local Ollama. A provider with no key in `config.env` is skipped. One that hits a quota, a rate limit, a rejected key, or is down, is remembered in `~/Downloads/kindle/layout-providers.json` until its reset, and the next one reads the remaining pages. While a later provider remains, that provider reads the pages. The convert job pauses only when every configured provider is out, until the earliest reset. `layout.json` records `provider` and `model` on each page. The same checks run whichever model read the page.

`CLAUDE_MODEL` is `sonnet` or `haiku` (default `sonnet`). Compare the two on the first pages of a captured book. This writes nothing:

```bash
venv/bin/python layout.py ~/Downloads/kindle/<ASIN>/pages --a sonnet --b haiku -n 8
```

The report lists pages whose titles (line and level), quotes, figures or code differ.

Keys and the local model:

- **Mistral** — a key from [console.mistral.ai](https://console.mistral.ai/). In the workspace settings, turn off training on your data before the first book. The pages are copyrighted.
- **Z.ai** — a key from [z.ai](https://z.ai/). The model is GLM-4.6V-Flash, free, one request at a time.
- **OpenRouter** — a key from [openrouter.ai/keys](https://openrouter.ai/keys). Only `:free` models are sent. The default is `google/gemma-4-31b-it:free`, then `google/gemma-4-26b-a4b-it:free` when that model is missing or cannot take an image. Free limits are 20 requests a minute and 50 a day (1000 a day after buying 10 credits). In OpenRouter's privacy settings, turn off providers that train on prompts. The code refuses OpenAI, whose free tokens share the pages.
- **Ollama** — already on the Mac. A 16 GB M1 holds the 7B model; an 8 GB Mac should set `OLLAMA_MODEL=qwen2.5vl:3b`.

```bash
ollama pull qwen2.5vl:7b
```

OCR is unchanged: Gemini models, then local Vision. Layout is the step that rotates, because that is the step with one shared schema.

## One command per book

`capture_book.py` runs the steps above for one book, then checks the result and fixes what it can:

```bash
venv/bin/python capture_book.py B07HNMJV57 --title "How to Write a Lot"
venv/bin/python capture_book.py B07HNMJV57 --zip ~/Downloads/kindle-book.zip   # from an existing capture
```

It captures the book through the channel (above; `capture_stall` when no page or event arrives for 5 minutes, `extension_unreachable` when no worker takes the job in 90 seconds, `capture_refused` or `kindle_app_only` when the extension refuses), runs `img2txt.py` (`--model vision` by default), then `check_book.py`. Each check names a fix: Vision pages without a layout from Gemini, and a page without text, re-run `img2txt.py`; headings that cannot be titles become plain text again, and duplicate pages, seams, foreign pages, dashes, stray symbols, homoglyphs, `Al` for `AI` and `'11` for `'ll` run `proofread.py --apply`. Proofreading runs last in each round, because re-reading pages would undo its fixes. No headings, a heading read as `Z`/`&`, and a chapter opening without a heading have no automatic fix, because reading the pages again gives the same result; they stay as failures. The fixes run and the checks repeat, up to three rounds. `report.json` in the book folder records every check, and the exit code is 1 while a blocking check still fails. `start_position` and `end_position` fail when `positions.json` (or, for older ZIPs, `dom-probe.json`) shows the capture began after page 1 or stopped short of the last page; a ZIP without `positions.json` gets the `positions` warning. A default output name (`kindle-book` or the ASIN) and zero text-layer pages are warnings: they do not block, and the ZIP's `dom-probe.json` says where to look. `check_book.py <folder>` runs the checks alone.

`capture_book.py` itself does not keep a list; the list runner below calls the same steps one at a time.

## The list

Every book has a folder, `~/Downloads/kindle/<ASIN>/`, and the files in it are its whole state; no other file keeps progress. Two lists make the folders: `~/Downloads/kindle/books.tsv` (`ASIN<TAB>title`, optionally `<TAB>domain`) and each `~/Downloads/library-<domain>.json` that `channel.py library` writes. The first time a folder is made, an entry for the book in a `books.progress.json` beside the list is carried over: a capture an earlier run converted becomes this book's capture, and a book Cloud Reader cannot open stays parked for the Kindle app.

```bash
venv/bin/python kc_run.py capture    # one step: pull main, capture the next book that needs it
venv/bin/python kc_run.py convert    # one step: pull main, convert and check the next book, or copy one to Drive
```

Each run takes one step and exits. launchd runs both jobs and starts each again ten seconds after it exits (`launchd/com.otchy.kindle-capture.plist` and `launchd/com.otchy.kindle-convert.plist`, logging to `~/Library/Logs/ocr-kindle/`), so code merged to main is used from the next step, and one book is captured while the one before it converts. When there is nothing to do, a step waits a minute before it exits. If the pull fails because of local changes, the job pauses with git's message and resumes once a pull works.

A book goes through three stages, defined in one table (`STAGES` in `book.py`): `capture` writes the pages into `<ASIN>/pages/`, `convert` runs `img2txt.py` and the checks and fixes and writes `report.json`, and `publish` copies a book that passed to `GOOGLE_DRIVE_FOLDER` and starts the wiki's watcher. Each stage records its output in `stage.<stage>.json`, or its failure in `failure.<stage>.json`, with the stage's version: the sha256 of that stage's own source files (`channel.py` and `extension/` for capture; `img2txt.py`, `layout.py`, `check_book.py` and `proofread.py` for convert and publish). `book.py`'s `Book.state()` reads these files and says what the book needs next: `Ready(stage)`, `Parked(failure)` or `Done`. When the converter's code changes, every converted book is converted again from its pages, whether it passed or failed. A book whose capture worked is not captured again when the extension changes.

A failure is one of two kinds, set by check name in `RULES` in `book.py`. A book failure parks that book, and the job goes on to the next one. A parked book is tried again once the code of the stage that failed it changes (after a crash, once any module changes), except a book Cloud Reader cannot open (`kindle_app_only`), which waits for the Kindle app. A capture the checks find unusable (`pages`, `text`, `start_position`, `end_position`, `capture_stall`) is captured once more before the book is parked. A systemic failure would fail every book, so it pauses that job for all of them in `pause.<job>.json`: signed out of Cloud Reader (resumes after 30 minutes), the extension unreachable, of another version or loaded from a checkout that is not main (10 minutes), Gemini failing after Sonnet did too (at Gemini's daily reset, midnight Pacific time, when the quota ran out; else 30 minutes). The same check failing on two books in a row also pauses the job, until the code of the stage it blames changes. A pause also ends when that code changes.

`KINDLE_APP_OK=1 venv/bin/python capture_app.py ~/Downloads/kindle/books.tsv` captures the listed books parked as `kindle_app_only` from the Kindle for Mac app, only when Laiken asks for it (the capture hook blocks it otherwise), and records them as captured; the convert job converts them like any other book.

## Cases

Every failure that parks a book or pauses a job leaves a case, so the failure can be studied and replayed without the Mac. `cases.py` writes it to `~/Downloads/kindle/cases/<YYYYmmddTHHMMSS>-<ASIN>-<stage>/`: `case.json` (the book, the stage, the failed checks and error, the attempt, every stage version, the checkout's commit, and whether the failure was the book's or systemic), the capture's `trace.jsonl`, `dom-probe.json`, `capture.json`, `positions.json` and `report.json` when they exist, and at most three page images: the page a failed check points at (with its text for a convert check), the first and the last. A case stays under 5 MB; an image that would pass that is left out and named in `skipped`. The same book failing the same checks with the same versions again leaves no new case.

Each case is then copied to `KINDLE_CASES_FOLDER` in `config.env`, by default `Kindle_cases` beside `GOOGLE_DRIVE_FOLDER`, so Drive syncs it to where the cloud can read it. With no Drive configured, or when the copy fails, the case stays only on the Mac.

A case copied into `fixtures/cases/` becomes a regression test: `test_cases.py` feeds the observations of its `trace.jsonl` through `extension/policy.js` (`node scripts/replay_trace.mjs <trace.jsonl>`) and checks that the actions match the ones the Mac recorded.

## Audiobook

`md2audio.py` reads the Markdown aloud with a macOS `say` voice and writes one `.m4a` with a chapter marker per chapter. The voice and speed follow the book's language: Kyoko (Enhanced) at 220 words per minute when kana and kanji outnumber English words, Zoe (Premium) at 170 otherwise. `--voice` and `--rate` override them. It needs `ffmpeg` (`brew install ffmpeg`) and nothing from `venv`. Download the voices once in System Settings → Accessibility → Spoken Content → System Voice → Manage Voices, or pass another with `--voice`, e.g. `--voice Kyoko` for the built-in one.

```bash
python3 md2audio.py ~/Downloads/<book title>/<book title>.md
```

The file name is the book title. Chapters split at the shallowest heading level that appears more than once. Headings with no text between them, such as `CHAPTER ONE` / `CHILDHOOD` / `Abandoned and Chosen`, form one chapter title, and a chapter under 80 characters (`--min-chars`) is merged into the next one. Output goes to `Kindle_audio/<book title>/` next to `GOOGLE_DRIVE_FOLDER`, so it syncs to Drive, or to `--out`. Each chapter is cached in `cache/<book title>/NNN.m4a` in this repository, outside Drive, so an interrupted run resumes where it stopped and Drive holds only the files to play. `--chapters 7-9,12` synthesizes only those chapters (`7-` runs to the end, `1-2` makes a quick sample), numbered and cached by their place in the whole book, so a later run reuses them. Without `--split` the single file holds only the chosen chapters. `--rate` changes the speed, and `--force` re-synthesizes.

`--split` writes one file per chapter instead of one file for the book, as `NN_<chapter title>.m4a` in the same `<book title>/` folder. The numbers are zero-padded to the chapter count, so the files sort in reading order, and each file is tagged with its chapter title, the book title as album, and its track number.

## Kindle for Mac (only when Laiken asks)

This path drives Laiken's screen and needs macOS permissions, so it is never a first step or a fallback an agent picks on its own. If Cloud Reader stopped at the end of the sample, extract the ZIP, open the same book in the Kindle for Mac app at the page where the capture stopped, and run the AppleScript on the extracted folder:

```bash
unzip -d ~/Downloads/"<book title>" ~/Downloads/"<book title>.zip"
osascript kindle2img.applescript ltr ~/Downloads/"<book title>"
venv/bin/python img2txt.py ~/Downloads/"<book title>"
```

`ltr` turns pages with the right arrow (English etc.), `rtl` with the left arrow (vertical Japanese, the default). The script numbers its captures after the folder's highest page, so `page_0137.png` follows `page_0136.png`, and stops when a page turn leaves the window unchanged, which is the end of the book. The last one or two captures show Kindle's end-of-book panel instead of text, which `proofread.py` blanks. Without a folder it writes to a new `~/Downloads/Kindle_Screenshots_<timestamp>/`, which is also how to capture a book that does not open in Cloud Reader at all. The margins that crop the header and footer are at the top of the script.

The two apps break pages in different places, so the first Mac capture usually repeats the last paragraphs of the Cloud Reader part; `proofread.py` removes the repeat. Every Mac page goes through OCR.

The app running `osascript` needs Screen Recording and Accessibility permission (System Settings → Privacy & Security), and must be restarted after granting them. Without Screen Recording the captures show only the desktop; without Accessibility the page turns fail. The script brings Kindle back to the front before every capture and page turn, so switching apps while it runs does not put another window into the captures.

## Proofreading

A book assembled from several readers carries errors typical of each, and of the switch between them. `proofread.py` finds every instance of each kind and fixes them all at once, after a look at a few samples:

```bash
venv/bin/python proofread.py ~/Downloads/"<book title>"                  # counts and proofread.html
venv/bin/python proofread.py ~/Downloads/"<book title>" --apply all      # fix, then rebuild <book title>.md
```

| Kind | Error | Fix |
| --- | --- | --- |
| `duplicate_page` | The same page captured twice, when a page turn did not land | Blank the repeat |
| `seam_overlap` | A page that starts by repeating the end of the one or two before it, where the Mac capture took over | Cut the repeat |
| `foreign_page` | Another app captured over Kindle, or Kindle's end-of-book panel | Blank the page |
| `hyphen_break` | A word split at a line end and never rejoined, like `Scul-ley` | Join it when the book spells it joined at least as often and the page image does not show the hyphen mid-line |
| `dash_as_hyphen` | Vision reading an em dash as a hyphen, like `together-and` | Restore the em dash when a function word follows and the pair occurs once |
| `homoglyph` | Cyrillic look-alike letters from Vision inside English, like `Не` | Map them to Latin |
| `odd_case` | A casing that appears once while the book spells the word another way, like `iMAC` | Use the book's spelling |
| `stray_symbol` | Kindle's page-turn chevron read as a `>` or `›` on its own | Remove it |
| `l_for_i` | Vision reading a capital I after a capital as a small l, like `Al` for `AI` or `AlforEDUbook` | Use the spelling with I when the book has it at least three times and at least a fifth as often; `Al` before a capitalised word (a name) stays |
| `digits_for_ll` | The contraction `'ll` read as `'11`, like `it'11` | Restore `'ll` |
| `heading_noise` | A heading on a Vision page that cannot be a title: it starts in lower case, ends with a comma, or has no vowel; on a page Gemini has not laid out, any heading on a page without body text | Make it plain text |
| `cross_read` | With `--cross-read`, four or more words where a page from Gemini or the text layer and a second Vision reading of its image disagree | Report only |

`proofread.html` shows up to six samples of each kind, spread evenly through the book, each with the line cut out of its page image. It also counts the hits on text-layer pages. That text is exact, so those hits are false positives and show how far a kind can be trusted. Apply only the kinds whose samples check out, e.g. `--apply seam_overlap,dash_as_hyphen`. Fixes rewrite the page `.txt` files, so a second run finds nothing more of the applied kinds. `img2txt.py` records in `ocr_sources.tsv` which reader produced each page.

`--cross-read` runs Vision once more on every page that did not come from Vision and caches the result as `page_NNNN.vision.txt`, which takes about a second a page. On the Steve Jobs biography it found no invented or dropped text in the 263 Gemini pages. Every disagreement it found was a photo caption that one reader skipped.

## Tests

```bash
venv/bin/pip install pytest
venv/bin/python -m pytest -q
```
