# Position checks

Whether a capture covered the book from its start to its end, decided from `positions.json` (reader footer per screen), `trace.jsonl` (what the extension saw and did) and `capture.json` (why it stopped).

## Sub-features

- `start` `start_position`: the first numbered screen is no later than the start allows.
- `end` `end_position`: the last numbered page is within the end tolerance of the total.
- `stop` `capture_stop`: no turn cap ended the capture.
- `records` `positions`: positions.json is present (warning only).

## How to get to it (user POV)

- The convert runner runs the checks after each conversion and writes `report.json`.
- By hand: `check_book.py <folder> --title <title>`.

## Driving it with verify.sh

Preconditions:

- A pages folder with `positions.json` (extension 1.3 or later).

- **See the evidence.** Run `verify.sh positions <ASIN> "<pages folder>"`. It prints each numbered series (a change of total starts a new one: Kindle locations of 11824, then printed pages of 568) and the trace's first observations, with the count at position 1.
- **Check.** Run `verify.sh run <ASIN> "<pages folder>" --fast`. `summary.txt` has the four verdicts; `meta.txt` has `source_untouched=yes`.
- **Known case.** Steve Jobs: The Exclusive Biography, `B005J3IEZQ`, `pages (before 20261012T215800)`: at be98524 `start_position` fails with `first numbered screen 2 shows page 3 of 11824` although the trace shows position 1 of 11824 and the body starts at 1 of 568 on screen 58. `end_position` passes with `last page 568 of 568`.

## Gotchas

- The reader numbers some books by location first, then by page; a series is not always page numbers.
- An unnumbered screen (`''`) is not evidence of anything; the cover is unnumbered in positions.json even when the trace shows position 1 on it.
- The current `pages` folder of a book may be a short capture that an older code passed (Steve Jobs: The Exclusive Biography `pages`, 52 png, `passed` in report.json). Check the folder the failure was reported for.
