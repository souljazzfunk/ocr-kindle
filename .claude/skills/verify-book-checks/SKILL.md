---
name: verify-book-checks
description: Verify ocr-kindle's book checks (check_book.py, starting with contents_chapters) against real converted book folders on the Mac or against the recorded fixtures in the cloud. Use before and after changing a check, and to tell a false park from a real defect.
---

# Verify the book checks

The surface is the convert job on the Mac: `kc_run.py convert` runs `check_book.py` on `~/Downloads/kindle/<ASIN>/pages/` and parks the book, or pauses the job, on a FAIL. A user sees that as `report.json` and the launchd log `~/Library/Logs/ocr-kindle/convert.log`.

## Doctor

- Mac folder tools: the checkout `/Users/otchy/Documents/GitHub.nosync/ocr-kindle`, `/Users/otchy/Downloads` and `/Users/otchy/Library/Logs/ocr-kindle` connected. `tail -n 3 convert.log` says whether the job runs or is `paused:` (and why); `~/Downloads/kindle/pause.convert.json` holds the pause.
- `git log -1` in the Mac checkout matches the main commit you expect (`kc_run.py` pulls main each step).

## Drive

- Cloud, recorded pages: `python3 -m pytest -q test_check_book.py -k contents`. `fixtures/contents/<ASIN>.json` holds the contents pages and the headings the Mac recorded; add a book there when it is parked, never hand-written headings.
- Mac, real folders (the device shell has no venv; the helper stubs the Mac-only modules):
  `python3 -I .claude/skills/verify-book-checks/contents_report.py ~/mnt/Downloads/kindle/<ASIN>/pages ...` from the checkout. It prints `CONTENTS OK` or `CONTENTS FAIL` and the entries without a heading.
- Recording a book for a fixture: the extraction is the same as `_recorded_contents_book` reads back (contents page, the pages after it up to the first prose page, the Markdown's headings and figure titles).

## Evidence

- Before a fix: the failing test or `CONTENTS FAIL` line with the entries. After: the same command passing. Paste both into the PR.
- Done on the machine only when the next convert step writes a `report.json` with every check `ok` (or `warn`) and the log shows no `paused:` line. A green CI is not that.

## Cleanup

Nothing to tear down: the helper only reads. Never edit or delete files under `~/Downloads/kindle/` from the check run.
