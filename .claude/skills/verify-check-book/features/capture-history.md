# Capture history

A book is captured again when a capture is unusable; the old pages folder is renamed `pages (before <timestamp>)`. This feature lists them so a check is run on the folder that failed.

## Sub-features

- `list` pages folders newest first, png count, blocking failures in `report.json`.

## How to get to it (user POV)

- Finder: `~/Downloads/kindle/<ASIN>/`.

## Driving it with verify.sh

Preconditions:

- The ASIN folder exists.

- **List.** `verify.sh folders <ASIN>`. Each line: folder name, png count, `passed`, `no report`, or the comma-separated blocking failures.

## Gotchas

- `no report` means the conversion did not finish (often 0 png).
- Folder names contain spaces and parentheses; quote them.
