#!/bin/bash
# Run this checkout's check_book.py on a clone of a real book folder and keep the evidence.
#
#   verify.sh doctor
#   verify.sh folders <ASIN>                         list the pages folders of a book, newest first
#   verify.sh positions <ASIN> [<pages folder>]      the numbered series and the trace's first lines (read-only)
#   verify.sh run <ASIN> [<pages folder>] [--fast]   clone, run check_book.py --json, save the verdicts
#   verify.sh compare <run dir>                      this run's verdicts next to the folder's report.json
#   verify.sh cleanup <run dir>                      move the clone to the Trash, keep the evidence
#
# <pages folder> is a name under ~/Downloads/kindle/<ASIN>/ ("pages", "pages (before 20261012T215800)"); default "pages".
# The real book folder is never written: check_book.py runs on an APFS clone (cp -c) in the run directory.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
KINDLE="${KINDLE_DIR:-$HOME/Downloads/kindle}"
OUT="${VERIFY_OUT:-$HOME/Downloads/kindle-verify}"
MAIN="$(cd "$(git -C "$REPO" rev-parse --git-common-dir)/.." && pwd)"
PY="$REPO/venv/bin/python"
[ -x "$PY" ] || PY="$MAIN/venv/bin/python"
META=(positions.json trace.jsonl capture.json dom-probe.json dom-probe-stopped.json report.json)

die() { echo "verify: $*" >&2; exit 2; }
book_dir() { local d="$KINDLE/$1/${2:-pages}"; [ -d "$d" ] || die "no folder $d"; echo "$d"; }
title_of() { "$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("title",""))' "$KINDLE/$1/book.json" 2>/dev/null || true; }
fingerprint() { (cd "$1" && find . -maxdepth 1 -type f -exec stat -f '%N %z %m' {} + | sort | shasum | cut -c1-16); }

cmd="${1:-}"; shift || true
case "$cmd" in
doctor)
  echo "repo      $REPO ($(git -C "$REPO" rev-parse --abbrev-ref HEAD) $(git -C "$REPO" rev-parse --short HEAD), $(git -C "$REPO" status --short | wc -l | tr -d ' ') changed files)"
  [ -x "$PY" ] && echo "python    $PY" || die "no venv python at $REPO/venv or $MAIN/venv"
  (cd "$REPO" && "$PY" -c 'import check_book' ) && echo "import    check_book.py from $REPO ok" || die "check_book.py does not import"
  [ -d "$KINDLE" ] && echo "books     $KINDLE ($(ls "$KINDLE" | grep -c '^B') folders)" || die "no $KINDLE"
  mkdir -p "$OUT" && echo "evidence  $OUT"
  launchctl list 2>/dev/null | grep -i kindle | sed 's/^/launchd   /' || echo "launchd   no kindle jobs loaded"
  ;;
folders)
  [ -n "${1:-}" ] || die "usage: folders <ASIN>"
  ls -1t "$KINDLE/$1" | grep '^pages' | while read -r f; do
    r="$KINDLE/$1/$f/report.json"
    fails=$([ -f "$r" ] && "$PY" -c 'import json,sys; print(",".join(c["name"] for c in json.load(open(sys.argv[1]))["checks"] if not c["ok"] and c["blocking"]) or "passed")' "$r" || echo "no report")
    echo "$f	$(ls "$KINDLE/$1/$f" | grep -c '\.png$') png	$fails"
  done
  ;;
positions)
  d="$(book_dir "$1" "${2:-}")"
  "$PY" - "$d" <<'EOF'
import json, re, sys
from pathlib import Path
d = Path(sys.argv[1])
p = d / 'positions.json'
if p.exists():
    series, last = [], None
    for name, text in json.loads(p.read_text()).items():
        m = re.search(r'(\d+)/(\d+)', text or '')
        key = m.group(2) if m else ''
        if key != last:
            series.append([name, name, (m.group(1) if m else ''), (m.group(1) if m else ''), key, 0]); last = key
        series[-1][1] = name; series[-1][3] = m.group(1) if m else ''; series[-1][5] += 1
    print('positions.json series (first screen .. last screen: first value .. last value of total, screens):')
    for a, b, v1, v2, total, n in series:
        print(f'  {a} .. {b}: ' + (f'{v1} .. {v2} of {total}' if total else 'unnumbered') + f', {n}')
else:
    print('no positions.json')
t = d / 'trace.jsonl'
if t.exists():
    lines = t.read_text().splitlines()
    print(f'trace.jsonl first lines (of {len(lines)}):')
    for line in lines[:6]:
        e = json.loads(line); print(f"  t={e.get('t')} position={e['observation'].get('position')} action={e.get('action')}")
    ones = sum(1 for line in lines if (json.loads(line)['observation'].get('position') or {}).get('n') == 1)
    print(f'  observations at position 1: {ones}')
else:
    print('no trace.jsonl')
EOF
  ;;
run)
  asin="${1:-}"; [ -n "$asin" ] || die "usage: run <ASIN> [<pages folder>] [--fast]"; shift
  pages="pages"; fast=""
  for a in "$@"; do [ "$a" = "--fast" ] && fast=1 || pages="$a"; done
  src="$(book_dir "$asin" "$pages")"
  run="$OUT/$(date +%Y%m%dT%H%M%S)-$asin"; mkdir -p "$run"
  before="$(fingerprint "$src")"
  if [ -n "$fast" ]; then  # only the capture's records: the position checks are right, the text checks are not meaningful
    mkdir "$run/folder"; for f in "${META[@]}"; do [ -f "$src/$f" ] && cp -c "$src/$f" "$run/folder/"; done
  else
    cp -cR "$src" "$run/folder"
  fi
  title="$(title_of "$asin")"
  # check_book.py names the markdown after --title; clone it under the same name so the text checks read it.
  [ -z "$fast" ] && [ -n "$title" ] && [ ! -f "$run/folder/$title.md" ] && [ -f "$KINDLE/$asin/$title.md" ] && cp -c "$KINDLE/$asin/$title.md" "$run/folder/"
  printf 'asin=%s\nsource=%s\ntitle=%s\nfast=%s\ncommit=%s\nchanged=%s\n' "$asin" "$src" "$title" "${fast:-0}" \
    "$(git -C "$REPO" rev-parse --short HEAD)" "$(git -C "$REPO" status --short | tr '\n' ' ')" > "$run/meta.txt"
  set +e
  (cd "$REPO" && "$PY" check_book.py "$run/folder" ${title:+--title "$title"} --json) > "$run/checks.json" 2> "$run/stderr.txt"
  echo "exit=$?" >> "$run/meta.txt"
  set -e
  after="$(fingerprint "$src")"
  echo "source_untouched=$([ "$before" = "$after" ] && echo yes || echo NO)" >> "$run/meta.txt"
  "$PY" -c 'import json,sys
for c in json.load(open(sys.argv[1])):
    print(("ok  " if c["ok"] else ("FAIL" if c["blocking"] else "warn")), c["name"].ljust(18), c["detail"])' "$run/checks.json" | tee "$run/summary.txt" || cat "$run/stderr.txt"
  cat "$run/meta.txt"; echo "run dir: $run"
  ;;
compare)
  run="${1:-}"; [ -f "$run/checks.json" ] || die "usage: compare <run dir>"
  src="$(sed -n 's/^source=//p' "$run/meta.txt")"
  [ -f "$src/report.json" ] || die "no report.json in $src"
  "$PY" - "$src/report.json" "$run/checks.json" <<'EOF' | tee "$run/compare.txt"
import json, sys
old = {c['name']: c for c in json.load(open(sys.argv[1]))['checks']}
new = {c['name']: c for c in json.load(open(sys.argv[2]))}
for name in dict.fromkeys([*old, *new]):
    a, b = old.get(name), new.get(name)
    va = '-' if not a else ('ok' if a['ok'] else 'FAIL')
    vb = '-' if not b else ('ok' if b['ok'] else 'FAIL')
    print(f"{'changed' if va != vb else 'same   '} {name:18} report={va:4} now={vb:4} {b['detail'] if b else ''}")
EOF
  ;;
cleanup)
  run="${1:-}"; [ -d "$run" ] && [[ "$run" == "$OUT"/* ]] || die "usage: cleanup <run dir under $OUT>"
  [ -d "$run/folder" ] && mv "$run/folder" "$HOME/.Trash/$(basename "$run")-folder" && echo "clone moved to the Trash"
  ls "$run"
  ;;
*) sed -n '2,12p' "$0"; exit 2 ;;
esac
