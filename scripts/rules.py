"""ルール表 (`| ルール | 何が守らせるか |`) を読む。

ルール表は「ルール」と「何が守らせるか」の対。守らせるものが空、「文章で守る」、「宿題」の行は、
決定論的な仕組みがまだない行として数える。

life-automation の quality/rules.py の写し。ocr-kindle の CI は life-automation を読めないので、ここでは台帳の id の
形だけを見る。足したのは、仕組みのある行がその決まりを確かめるテストを名前で挙げ、そのテストが実在すること。
2.0 の作り直しで「取り込みは選んだブラウザだけ」を守っていたコードが消えても、行が挙げていたのは別の役目の
フックで、決まりを確かめるテストもなく、何も落ちなかった (台帳 F072)。
"""
import re

HEADER = re.compile(r"^\|\s*ルール\s*\|\s*何が守らせるか\s*\|\s*$")
SEPARATOR = re.compile(r"^\|[\s:|-]+\|\s*$")
NOT_ENFORCED = ("文章で守る", "宿題", "仕組みなし")


def rows(text):
    """[(ルール, 何が守らせるか)] を返す。表がいくつあってもすべて。"""
    out, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if HEADER.match(s):
            inside = True
            continue
        if not inside:
            continue
        if SEPARATOR.match(s):
            continue
        if not s.startswith("|"):
            inside = False
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        rule, how = cells[0], " | ".join(cells[1:]).strip()
        out.append((rule, how))
    return out


def enforced(how):
    return bool(how) and not any(w in how for w in NOT_ENFORCED)


# ルール表の 1 行は「ルール 1 文」と「仕組みのパスと台帳の id」だけ。観察・時刻・人の発言・試した手は
# fixture・テスト・台帳の summary・コミットに書く。ocr-kindle の表は、修正のたびに近い行へ経緯を書き足し、
# 1 行 3000 字の記録になった (台帳 F071)。
RULE_MAX, HOW_MAX = 80, 220
STAMP = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{4}-\d{2}-\d{2}\b")
PERSON = re.compile(r"Laiken|らいけん")
LEDGER_ID = re.compile(r"\bF\d{3,}\b")
TEST_REF = re.compile(r"`([\w./-]+\.(?:py|js))::([^`]+)`")
PATH = re.compile(r"`([\w./-]+\.(?:py|js|sh|yml|yaml|json|md|txt|applescript|plist))(?:::[^`]*)?`")


def defines_test(source, name):
    """source (テストのファイルの中身) に name という名前のテストがあるか。pytest の関数か node:test の test()。"""
    if re.search(rf"^\s*def {re.escape(name)}\(", source, re.M):
        return True
    return any(f"test({q}{name}{q}" in source for q in "'\"`")


def lint(text, files, read, ledger_ids=None):
    """ルール表の形の問題を (ルールの頭, 理由) で返す。files はリポジトリの追跡ファイルのパス。

    read はリポジトリのパスを受けてファイルの中身を返す (なければ None)。行が挙げるテストの名前を探すのに使う。
    ledger_ids を渡すと、行に書いた台帳の id が台帳にあるかも確かめる。
    """
    names = {f.rsplit("/", 1)[-1] for f in files} | set(files)
    for rule, how in rows(text):
        head = rule[:30]
        if len(rule) > RULE_MAX:
            yield head, f"ルールが {len(rule)} 字 (上限 {RULE_MAX})。1 文にし、細部は仕組みのコードとテストへ"
        if len(how) > HOW_MAX:
            yield head, f"仕組みの欄が {len(how)} 字 (上限 {HOW_MAX})。パスと台帳の id だけにし、経緯は台帳とコミットへ"
        for cell in (rule, how):
            if STAMP.search(cell):
                yield head, f"時刻や日付 ({STAMP.search(cell).group()}) がある。経緯は台帳の summary とコミットへ"
                break
        for cell in (rule, how):
            if PERSON.search(cell):
                yield head, "人の発言がある。発言は台帳の summary へ"
                break
        if not enforced(how):
            continue
        paths = PATH.findall(how)
        missing = [p for p in paths if p not in names and p.rsplit("/", 1)[-1] not in names]
        if not paths:
            yield head, "仕組みの欄に実在するファイルのパスがない (まだなければ「宿題」と書く)"
        elif missing:
            yield head, f"仕組みのファイルがリポジトリにない: {', '.join(missing)}"
        tests = TEST_REF.findall(how)
        if not tests:
            yield head, ("仕組みの欄にテストの名前がない (`test_x.py::test_name` か `extension/x.test.js::名前`)。"
                         "決まりを確かめるテストがなければ「宿題」と書く")
        for path, name in tests:
            if path in missing:
                continue
            source = read(path)
            if source is None or not defines_test(source, name):
                yield head, f"テストがない: {path}::{name}"
        ids = LEDGER_ID.findall(how)
        if not ids:
            yield head, "仕組みの欄に台帳の id (F0xx) がない。なぜこのルールがあるかは台帳が持つ"
        elif ledger_ids is not None:
            unknown = [i for i in ids if i not in ledger_ids]
            if unknown:
                yield head, f"台帳にない id: {', '.join(unknown)}"


def main(argv=None):
    import argparse
    import json
    import subprocess
    import sys
    from pathlib import Path

    ap = argparse.ArgumentParser(description="ルール表の形を検査する")
    ap.add_argument("tables", nargs="+", type=Path)
    ap.add_argument("--ledger", type=Path, help="台帳 (failures.jsonl)。渡すと id の存在も確かめる")
    args = ap.parse_args(argv)
    ledger_ids = None
    if args.ledger:
        ledger_ids = {json.loads(l)["id"] for l in args.ledger.read_text(encoding="utf-8").splitlines() if l.strip()}
    found = 0
    for table in args.tables:
        root = table.resolve().parent
        files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True,
                               check=True).stdout.splitlines()
        text = table.read_text(encoding="utf-8")
        def read(path, root=root):
            f = root / path
            return f.read_text(encoding="utf-8") if f.is_file() else None

        for head, msg in lint(text, files, read, ledger_ids):
            print(f"{table}: {head}: {msg}")
            found += 1
    if found:
        return 1
    print(f"RULES OK: {sum(len(rows(t.read_text(encoding='utf-8'))) for t in args.tables)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
