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

The script extracts the ZIP into `~/Downloads/<book title>/`, prints how many pages already have text and how many it will OCR, runs 4 OCR requests in parallel, and writes `<book title>.md` into that folder. OCR tries the models in `GEMINI_MODEL` in order (default `gemini-3.8-flash`, then `gemini-3.5-flash-lite` once the free tier's 20 daily requests run out). If `GOOGLE_DRIVE_FOLDER` is set, the file is also copied there.

If any page fails (quota, network), the script exits with an error and keeps the pages that succeeded. Run the same command again on the folder to retry only the missing pages. `--force` re-OCRs everything, `--model` picks another Gemini model, and `--title` sets the output name.

## Audiobook

`md2audio.py` reads the Markdown aloud with the macOS `say` voice Kyoko (Enhanced) at 220 words per minute and writes one `.m4a` with a chapter marker per chapter. It needs `ffmpeg` (`brew install ffmpeg`) and nothing from `venv`. Download the voice once in System Settings → Accessibility → Spoken Content → System Voice → Manage Voices, or pass `--voice Kyoko` for the built-in one.

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

`ltr` turns pages with the right arrow (English etc.), `rtl` with the left arrow (vertical Japanese, the default). The script numbers its captures after the folder's highest page, so `page_0137.png` follows `page_0136.png`, and stops when a page turn leaves the window unchanged, which is the end of the book. Without a folder it writes to a new `~/Downloads/Kindle_Screenshots_<timestamp>/`, which is also how to capture a book that does not open in Cloud Reader at all. The margins that crop the header and footer are at the top of the script.

The two apps break pages in different places, so the first Mac capture usually repeats the last paragraphs of the Cloud Reader part. After OCR, delete the repeated text from its `.txt` and run `img2txt.py` again to rebuild the Markdown without new OCR requests. Every Mac page goes through OCR.

The app running `osascript` needs Screen Recording and Accessibility permission (System Settings → Privacy & Security), and must be restarted after granting them. Without Screen Recording the captures show only the desktop; without Accessibility the page turns fail. Leave the Kindle window in front while it runs.

## Tests

```bash
venv/bin/pip install pytest
venv/bin/python -m pytest -q
```
