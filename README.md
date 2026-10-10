# Kindle OCR

Turn a Kindle book you own into one Markdown file for personal use (notes, study, feeding to an LLM).

1. A browser extension (loaded in Brave, which holds the read.amazon.com sign-in) pages through the book in Kindle Cloud Reader from page 1 and saves `<book title>.zip` with every page image. The sample reader also has a screen-reader text layer, saved as each page's exact text; the full-book reader has none.
2. `img2txt.py` OCRs only the pages that have no text yet, then joins all pages into `<book title>.md`.
3. `capture_books.py` runs both for a list of books and checks every result (see **One command per book**).

The Kindle for Mac app is not part of this flow. `kindle2img.applescript` (last section) is used only when Laiken asks for it, and the capture hook blocks it otherwise.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp config.env.example config.env   # then set GEMINI_API_KEY
```

Load the extension once. Open `chrome://extensions` (or `brave://extensions`), turn on Developer mode, choose **Load unpacked**, and select the `extension/` folder.

## Usage

1. Open the book in Cloud Reader (`read.amazon.co.jp` or `read.amazon.com`) and go to the first page you want. A larger window gives larger page images.
2. Click the extension's toolbar icon. The badge shows the current location. Capture stops by itself at the end of the book (or of the sample), or when you click the icon again.
   To start without clicking, open the book's URL with `#autocapture` at the end, e.g. `open -a "Brave Browser" 'https://read.amazon.com/?asin=B07HNMJV57#autocapture'`. The extension removes the marker from the address bar as soon as it reads it, so a reload or a restored tab does not start again. Only one tab captures at a time, and a book whose ZIP was already saved does not start automatically again; click the icon to capture it once more. The reader reopens a book where it was last read, so an automatic capture first pages back to page 1 (badge `back`); when it cannot get there it refuses. The badge shows `skip` when a start was refused, and the tab's console says why. When the ZIP is saved, an automatic capture closes its own tab; a capture started by clicking leaves the tab open.
3. The extension saves `<book title>.zip` straight into the default Downloads folder, with no save dialog (it adds ` (1)` rather than replace an earlier ZIP), holding `page_0001.png` plus `page_0001.txt` where the text layer had text. Pages stay in memory until then, so leave the tab open. The title comes from the reader's title element, else from the tab title, else the ASIN. The ZIP also holds `dom-probe.json`, a record of the reader page's element structure (tags, ids, classes, counts; no book text), so that when the full-book reader stops matching the title or text-layer selectors, the fix can be read from a capture instead of guessed. `positions.json` records the reader's "Page N of M" for every saved page.
4. Run:

```bash
venv/bin/python img2txt.py ~/Downloads/<book title>.zip
```

The script extracts the ZIP into `~/Downloads/<book title>/`, prints how many pages already have text and how many it will OCR, runs 4 OCR requests in parallel, and writes `<book title>.md` into that folder. OCR tries the models in `GEMINI_MODEL` in order (default `gemini-3.8-flash`, then `gemini-3.5-flash-lite` once the free tier's 20 daily requests run out, then `vision`). `vision` is the macOS Vision framework, running locally with no quota. It catches the pages Gemini refuses with `RECITATION`, which happens on many pages of well-known books. Vision reads the words but gives no structure. So `img2txt.py` keeps each page's Vision line boxes (`page_NNNN.lines.json`), and `layout.py` sends the page image with its numbered lines to `gemini-3.5-flash-lite`, eight pages a request. Gemini answers with line numbers only: which lines are titles (and their level), pull quotes, text inside a picture, decoration, and the first word of a paragraph that starts with a drop cap. It never repeats the book's text, so `RECITATION` does not refuse it, and the answers are kept in `layout.json`, so a re-run asks only for pages not yet read. The page is then rebuilt: titles become `#`/`##`, pull quotes `>`, list bullets `-`, a chart or infographic one `![description]()` note, and decoration goes. The book's own contents page decides which titles are chapters (`#`), because Gemini's levels drift between requests. A line in capitals that starts where most of the book's titles in capitals start is a title too, when Gemini missed it. The lines beside a drop cap are read again with the cap left out, because Vision can misread them. A page whose last line is short is not joined to the next page. Without `GEMINI_API_KEY`, Vision pages keep titles guessed from the page layout alone (a line set apart from the body, in a larger type or centred), and `check_book.py` fails the `layout` check until Gemini has read every page. It reads horizontal text only, so vertical Japanese needs Gemini. If `GOOGLE_DRIVE_FOLDER` is set, the file is also copied there.

If any page fails (quota, network), the script exits with an error and keeps the pages that succeeded. Run the same command again on the folder to retry only the missing pages. `--force` re-OCRs everything, `--model` picks another Gemini model, and `--title` sets the output name.

## One command per book

`capture_book.py` runs the steps above for one book, then checks the result and fixes what it can:

```bash
venv/bin/python capture_book.py B07HNMJV57 --title "How to Write a Lot"
venv/bin/python capture_book.py B07HNMJV57 --zip ~/Downloads/kindle-book.zip   # from an existing capture
```

It opens the book in Brave with `#autocapture`, waits for the ZIP, checks that the book's tab is gone (closing it if the extension did not, and failing the blocking `tab_closed` check if it stays open), runs `img2txt.py` (`--model vision` by default), then `check_book.py`. Each check names a fix: Vision pages without a layout from Gemini, and a page without text, re-run `img2txt.py`; headings that cannot be titles become plain text again, and duplicate pages, seams, foreign pages, dashes, stray symbols, homoglyphs, `Al` for `AI` and `'11` for `'ll` run `proofread.py --apply`. Proofreading runs last in each round, because re-reading pages would undo its fixes. No headings, a heading read as `Z`/`&`, and a chapter opening without a heading have no automatic fix, because reading the pages again gives the same result; they stay as failures. The fixes run and the checks repeat, up to three rounds. `report.json` in the book folder records every check, and the exit code is 1 while a blocking check still fails. `start_position` and `end_position` fail when `positions.json` (or, for older ZIPs, `dom-probe.json`) shows the capture began after page 1 or stopped short of the last page; a ZIP without `positions.json` gets the `positions` warning. A default output name (`kindle-book` or the ASIN) and zero text-layer pages are warnings: they do not block, and the ZIP's `dom-probe.json` says where to look. `check_book.py <folder>` runs the checks alone.

`capture_books.py books.tsv` runs a list (`ASIN<TAB>title` per line) the same way and decides from the checks what to do next: a clean book is done and the next one starts; a book whose ZIP is unusable (`pages`, `text`, `start_position` or `end_position` failed) is captured again once with `#recapture`, which is the only way a saved book auto-starts again; a book whose earlier run failed one of those checks starts with `#recapture` too; a book the fixes could not clean is recorded as failed, with the check names, and the list goes on. `books.progress.json` next to the list holds every verdict, so an interrupted run resumes at the next book, and the summary at the end lists what failed and why.

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
