# check_book.py verification map

The maintained source for verifying the checks of `check_book.py` against real book folders. Read this index, then the feature file.

## Baseline preconditions

- `verify.sh doctor` passes and shows the branch you mean to test.
- The book folder exists: `verify.sh folders <ASIN>` lists it.
- The base verdict is known: a run on the base commit (or `report.json` via `compare`) for the same folder.

## Driving conventions

- Always go through `verify.sh run`; never point `check_book.py` at `~/Downloads/kindle` itself.
- Name the pages folder exactly as `folders` prints it, quoted.
- Record the run directory with every claim.

## Proof and skip reporting

- Proof is `summary.txt` + `meta.txt` (`source_untouched=yes`) of a base run and a changed run, and `compare.txt`.
- A `--fast` run proves only the position checks.
- Say which folder and commit a verdict came from. Do not report a check as fixed from a different book than the one that failed.

## Features

- [Position checks](./position-checks.md): `start_position`, `end_position`, `capture_stop`, `positions` from the capture records.
- [Full book check](./full-check.md): every check on a cloned folder, compared with the runner's `report.json`.
- [Capture history](./capture-history.md): the pages folders of a book and what each one failed.
