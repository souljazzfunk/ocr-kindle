# contents_chapters

## Sub-features

- Live Text contents (`layout.livetext_contents`) and Vision contents (`layout.contents_entries`), each stopping at the first prose page (`holds_prose`).
- Matching an entry to the headings in order, with one edit in five (`contents_covered`); a one-picture chapter counts by its figure title.

## How to get to it (user POV)

A book converts on the Mac; `report.json` lists `contents_chapters` ok or FAIL with the entries without a heading.

## Driving it with pytest and contents_report.py

`python3 -m pytest -q test_check_book.py -k contents` in the cloud; `contents_report.py <pages folder>` on the Mac.

## Gotchas

- A Markdown written by an older layout can lack a heading today's layout gives (近代中国史's 参考文献, page_0378): re-run the layout before calling it a check bug.
- English pages have no 「。」; prose shows as lines ending a sentence or starting lower case.
