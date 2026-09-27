# Kindle OCR

Turn a Kindle book you own into one Markdown file for personal use (notes, study, feeding to an LLM).

1. A Chrome extension pages through the book in Kindle Cloud Reader and downloads `<book title>.zip` with every page image. The ZIP also holds each page's exact text from Cloud Reader's screen-reader text layer when one is present.
2. Cloud Reader often shows only the free sample, even for a purchased book. `kindle2img.applescript` then captures the rest from the Kindle for Mac app into the same folder, continuing the page numbers.
3. `img2txt.py` OCRs, with Gemini, only the pages that have no text yet, then joins all pages into `<book title>.md`.

Pages whose text comes from the text layer need no OCR, so they cost nothing and contain no recognition errors. Vertical Japanese (縦書き) works in all paths.

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
3. The browser saves `<book title>.zip`, holding `page_0001.png` plus `page_0001.txt` where the text layer had text. Pages stay in memory until then, so leave the tab open.
4. Run:

```bash
venv/bin/python img2txt.py ~/Downloads/<book title>.zip
```

The script extracts the ZIP into `~/Downloads/<book title>/`, prints how many pages already have text and how many it will OCR, runs 4 OCR requests in parallel, and writes `<book title>.md` into that folder. OCR tries the models in `GEMINI_MODEL` in order (default `gemini-3.8-flash`, then `gemini-3.5-flash-lite` once the free tier's 20 daily requests run out, then `vision`). `vision` is the macOS Vision framework, running locally with no quota. It catches the pages Gemini refuses with `RECITATION`, which happens on many pages of well-known books, but it outputs plain paragraphs without `#` heading marks. It reads horizontal text only, so vertical Japanese needs Gemini. If `GOOGLE_DRIVE_FOLDER` is set, the file is also copied there.

If any page fails (quota, network), the script exits with an error and keeps the pages that succeeded. Run the same command again on the folder to retry only the missing pages. `--force` re-OCRs everything, `--model` picks another Gemini model, and `--title` sets the output name.

## Audiobook

`md2audio.py` reads the Markdown aloud with a macOS `say` voice and writes one `.m4a` with a chapter marker per chapter. The voice and speed follow the book's language: Kyoko (Enhanced) at 220 words per minute when kana and kanji outnumber English words, Zoe (Premium) at 170 otherwise. `--voice` and `--rate` override them. It needs `ffmpeg` (`brew install ffmpeg`) and nothing from `venv`. Download the voices once in System Settings → Accessibility → Spoken Content → System Voice → Manage Voices, or pass another with `--voice`, e.g. `--voice Kyoko` for the built-in one.

```bash
python3 md2audio.py ~/Downloads/<book title>/<book title>.md
```

The file name is the book title. Chapters split at the shallowest heading level that appears more than once, and a chapter under 80 characters (`--min-chars`) is merged into the next one. Output goes to `Kindle_audio/<book title>/` next to `GOOGLE_DRIVE_FOLDER`, so it syncs to Drive, or to `--out`. Each chapter is kept as `chapters/NNN.m4a`, so an interrupted run resumes where it stopped. `--max-chapters 2` makes a quick sample, `--rate` changes the speed, and `--force` re-synthesizes.

## Kindle for Mac

If Cloud Reader stopped at the end of the sample, extract the ZIP, open the same book in the Kindle for Mac app at the page where the capture stopped, and run the AppleScript on the extracted folder:

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
| `cross_read` | With `--cross-read`, four or more words where a page from Gemini or the text layer and a second Vision reading of its image disagree | Report only |

`proofread.html` shows up to six samples of each kind, spread evenly through the book, each with the line cut out of its page image. It also counts the hits on text-layer pages. That text is exact, so those hits are false positives and show how far a kind can be trusted. Apply only the kinds whose samples check out, e.g. `--apply seam_overlap,dash_as_hyphen`. Fixes rewrite the page `.txt` files, so a second run finds nothing more of the applied kinds. `img2txt.py` records in `ocr_sources.tsv` which reader produced each page.

`--cross-read` runs Vision once more on every page that did not come from Vision and caches the result as `page_NNNN.vision.txt`, which takes about a second a page. On the Steve Jobs biography it found no invented or dropped text in the 263 Gemini pages. Every disagreement it found was a photo caption that one reader skipped.

## Tests

```bash
venv/bin/pip install pytest
venv/bin/python -m pytest -q
```
