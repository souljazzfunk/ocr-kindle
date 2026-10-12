# ocr-kindle

使い方は `README.md`。拡張のテストは `node --test`（CI でも同じコマンド）。

## ルール表

決まりと、それを守らせるテストの表は `RULES.md` にある。CLAUDE.md に置くと、ocr-kindle に触れないスレッドも毎回読むので分けた。

- ocr-kindle のファイルを変える前、PR を出す前に `RULES.md` を読む。読むまでは life-automation の `.claude/hooks/rules_first.py` が Edit・Write・PR を止める。
- 取り込みや変換について判断して答えるときも、先に `RULES.md` を読む。

## 取り込みの手順

Mac では launchd の 2 つの仕事（`launchd/` の plist）が `kc_run.py` を 1 歩ずつ回し続ける。本が入る道は 2 つで、どちらも本のフォルダ `~/Downloads/kindle/<ASIN>/` を作るだけ：`~/Downloads/kindle/books.tsv`（`ASIN<TAB>書名[<TAB>ドメイン]` の行）と、`channel.py library` が書く `~/Downloads/library-<ドメイン>.json`。

```bash
venv/bin/python kc_run.py capture                      # 1 歩: main を pull、次の本を 1 冊取り込む
venv/bin/python kc_run.py convert                      # 1 歩: main を pull、次の本を 1 冊変換・検査するか、1 冊 Drive へ写す
venv/bin/python capture_book.py <ASIN> --title "<書名>"   # 1 冊だけ手で
```

取り込みの 1 歩は `channel.py`（127.0.0.1:8765）に仕事を出す。Chrome の拡張がそれを取って自分の窓で本を開き、ページを 1 枚ずつ `<本のフォルダ>/pages/` に送り、終われば窓を閉じる。変換の 1 歩は `img2txt.py`（既定 `--model vision`）、`check_book.py` の検査、直せるものは直して再検査（最大 3 周）、`report.json` を書く。検査が通った本は次の 1 歩で Drive に写し、wiki 取り込みを裏で始める。どの本が次に何をするかは `book.py` の `Book.state()` が本のフォルダから出す（`Ready(段)`・`Parked(失敗)`・`Done`）。
