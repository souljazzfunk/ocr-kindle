# Full book check

Every check of `check_book.py` on a complete pages folder with its Markdown, as the convert runner does it, compared check by check with the runner's `report.json`.

## Sub-features

- `all` all checks, text checks included (headings, chapter_titles, contents_chapters, proofread).
- `compare` which verdicts differ from `report.json`.

## How to get to it (user POV)

- The convert step: `kc_run.py convert` runs `check_book.py` and writes `report.json` into the pages folder.

## Driving it with verify.sh

Preconditions:

- The pages folder has the book's Markdown named after the title in `~/Downloads/kindle/<ASIN>/book.json`.

- **Run.** `verify.sh run <ASIN> "<pages folder>"`. About 15 s for 1862 pages. Read `summary.txt`.
- **Compare.** `verify.sh compare <run dir>`. Lines starting `changed` are checks whose verdict differs from `report.json`. `speed` shows `now=-` always: it is added by capture_book.py, not check_book.py.
- **Clean up.** `verify.sh cleanup <run dir>`; `checks.json`, `summary.txt`, `compare.txt`, `meta.txt` remain.

## Gotchas

- `report.json` comes from the code of its convert step (see `stage.convert.json` `version` in the book folder), so `changed` lines also show changes of other checks since then. Steve Jobs: The Exclusive Biography at be98524: `contents_chapters` is `ok` in report.json and FAIL now with 3848 entries.
- Without the Markdown under the right name, `headings` fails with 0 headings: check the `title=` in `meta.txt`.
