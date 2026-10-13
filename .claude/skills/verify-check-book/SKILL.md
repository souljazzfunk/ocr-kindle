---
name: verify-check-book
description: Run this checkout's check_book.py on a clone of a real ocr-kindle book folder (~/Downloads/kindle/<ASIN>/pages…) and keep the verdicts as evidence. Use before and after changing a check in check_book.py (start_position, end_position, contents_chapters, …), to reproduce a parked book's failure, or to see which checks a code change flips on real books.
---

# Verify check_book.py on real books

The surface is the CLI `check_book.py <folder> --title <title> --json`, the same call the convert runner makes. The real input is a book's pages folder under `~/Downloads/kindle/<ASIN>/`: the current capture is `pages`, older ones are `pages (before <timestamp>)`. Each has `report.json` with the verdicts the runner saw.

Everything goes through `verify.sh` in this directory. Run it from any directory; it uses the checks of the checkout it sits in (a worktree tests its own branch).

## Launch

Nothing to start. `check_book.py` is a short-lived CLI. The helper picks `venv/bin/python` of this checkout, or of the main checkout when a worktree has none.

The launchd jobs `com.otchy.kindle-capture` and `com.otchy.kindle-convert` write into `~/Downloads/kindle` all the time. Never run `check_book.py` directly on a folder there: the checks write caches (`*.vision.txt`, `proofread.html`) and race the runner. `verify.sh run` clones the folder with `cp -c` (APFS clone: instant, no extra space) into the run directory and checks the clone.

## Doctor

```bash
.claude/skills/verify-check-book/verify.sh doctor
```

Expect: repo path with branch, commit and the number of changed files (uncommitted changes ARE what gets tested), the python, `import check_book.py … ok`, the book count, the evidence directory, and the two launchd jobs. Exit 2 names what is missing.

## Drive

```bash
V=.claude/skills/verify-check-book/verify.sh
$V folders B005J3IEZQ                                              # pages folders, png count, blocking failures in report.json
$V positions B005J3IEZQ "pages (before 20261012T215800)"           # numbered series + trace start, read-only
$V run B005J3IEZQ "pages (before 20261012T215800)" --fast          # only the capture records: position checks, seconds
$V run B005J3IEZQ "pages (before 20261012T215800)"                 # whole folder: every check, ~15 s for 1862 pages
$V compare ~/Downloads/kindle-verify/<run dir>                     # report.json verdict vs now, per check
$V cleanup ~/Downloads/kindle-verify/<run dir>                     # clone to the Trash, evidence stays
```

`--fast` copies only `positions.json trace.jsonl capture.json dom-probe*.json report.json`. Trust only `start_position`, `end_position`, `capture_stop` and `positions` from it; `pages` and `headings` fail by construction.

`run` exits 0 after a successful run; the verdicts are in the summary and in `meta.txt` (`exit=1` means a blocking check failed, as the runner would see it).

## Evidence

Each run is `~/Downloads/kindle-verify/<YYYYmmddTHHMMSS>-<ASIN>/`:

- `meta.txt`: source folder, title, fast or not, commit, uncommitted files, `exit=`, `source_untouched=yes|NO` (fingerprint of the real folder's files before and after).
- `checks.json`: the full JSON from `check_book.py`. `summary.txt`: one line per check. `stderr.txt`.
- `compare.txt` after `compare`.

Proof standards:

- A fix is proven by two runs on the same folder: one on the base commit (the check fails as reported) and one with the change (it passes), and `compare` showing which other checks changed. A change that flips a check you did not mean to touch is a finding.
- `source_untouched` must be `yes`. `NO` means the runner wrote the folder during the run (rerun) or the helper is broken (stop).
- Name the book by its full title (3 words or more) in reports.
- Unit tests (`node --test`, pytest) are a separate proof; this skill is the real-data one.

## Cleanup

`verify.sh cleanup <run dir>` moves only the clone (`<run dir>/folder`) to `~/.Trash`; the evidence files stay. It refuses a path outside `~/Downloads/kindle-verify`. Do not delete files with `rm` (the permission gate blocks it); give the user paths instead.

## Isolation

Runs are separate directories, so several can run at once, also from several worktrees. Nothing here writes to `~/Downloads/kindle`, Drive, or git.

## Features

See [features/README.md](features/README.md).
