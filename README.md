# Kindle OCR

Turn a Kindle book you own into one Markdown file for personal use (notes, study, feeding to an LLM).

1. A Chrome extension pages through the book in Kindle Cloud Reader and downloads `<book title>.zip` with every page image. The ZIP also holds each page's exact text from Cloud Reader's screen-reader text layer when one is present.
2. `img2txt.py` OCRs, with Gemini, only the pages that have no text yet, then joins all pages into `<book title>.md`.

Pages whose text comes from the text layer need no OCR, so they cost nothing and contain no recognition errors. Vertical Japanese (縦書き) works in both paths.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp config.env.example config.env   # then set GEMINI_API_KEY
```

Load the extension once. Open `chrome://extensions` (or `brave://extensions`), turn on Developer mode, choose **Load unpacked**, and select the `extension/` folder.

## Usage

1. Open the book in Cloud Reader (`read.amazon.co.jp` or `read.amazon.com`) and go to the first page you want. A larger window gives larger page images.
2. Click the extension's toolbar icon. The badge shows the current location. Capture stops by itself at the end of the book, or when you click the icon again.
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

## Kindle for Mac fallback

Some books do not open in Cloud Reader. For those, `kindle2img.applescript` drives the Kindle for Mac app with arrow keys and `screencapture`. Edit `MAX_PAGES`, `PAGE_DIRECTION` and the margins at the top of the script, then run `osascript kindle2img.applescript`. The app running `osascript` needs Screen Recording and Accessibility permission (System Settings → Privacy & Security), and must be restarted after granting them. Without Screen Recording the captures show only the desktop; without Accessibility the page turns fail. It writes `~/Downloads/Kindle_Screenshots_<timestamp>/screenshot_001.png`, which `img2txt.py` accepts as is. Every page goes through OCR on this path, and the script does not detect the end of the book.

## Tests

```bash
venv/bin/pip install pytest
venv/bin/python -m pytest -q
```
