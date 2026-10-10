# ocr-kindle

使い方は `README.md`。拡張のテストは `node --test`（CI でも同じコマンド）。

## ルール表

人に直された間違いは、まず仕組みで起きなくする。残ったものだけをここに書く。

| ルール | 何が守らせるか |
|---|---|
| 1 冊の取り込みは 1 回だけ。再読み込み・タブの復元・2 つ目のタブで同じ本をめくり直さない | `extension/autostart.js`（`#autocapture` を読んだらすぐ URL から消す、取り込み中のタブは 1 つ、保存済みの ASIN は自動では再取り込みしない）、テスト `extension/autostart.test.js` |
| 取り込みは Brave の Cloud Reader 拡張から始める。Kindle for Mac アプリは、ユーザー本人がそう書いたときだけ使う | `.claude/hooks/guard_capture.py`（`kindle2img.applescript` / `screencapture` / Kindle アプリを開くコマンドを止める。本人の指示があるときは `KINDLE_APP_OK=1` を付ける）、テスト `test_guard_capture.py` |
| 同じ本を取り直すのは、検査で ZIP が使えないと分かったときだけ | 拡張は `#autocapture` では保存済みの本を始めない。`capture_books.py` だけが `#recapture` で取り直す（それでも 1 回・1 タブ） |
| Brave を終了・再起動しない。取り込みが終わったら本のタブだけ閉じる。Chrome で read.amazon を開かない | 同じフック（Brave の quit / kill、Chrome での read.amazon を止める） |
| 次の本へ進むかは検査結果で決める。通れば次へ、ZIP が使い物にならなければ取り直し、直しきれなければ失敗として記録して次へ。人の OK は待たない | `capture_books.py`（`decide()`）、テスト `test_capture_books.py` |
| 全文版リーダーの書名・テキスト層が取れないときに、当て推量で直さない | 拡張が ZIP に `dom-probe.json`（画面構造）を入れる。`extension/pagemeta.js`、テスト `extension/pagemeta.test.js` |
| 出来上がった Markdown の不具合（見出しなし、7→Z・8→& の読み違い、ダッシュ、重複ページ、書名）は機械が見つけて直す。人が気づくのを待たない | `check_book.py`（不具合ごとに直すコマンドを返す）、`capture_book.py`（直して再検査を最大 3 周、`report.json` に残す）、テスト `test_check_book.py` |
| 検査・フック・拡張のテストは CI で回す | `.github/workflows/ci.yml`（`node --test`、`pytest` を Linux と macOS で） |

## 取り込みの手順

一覧（`ASIN<TAB>書名` の行）を渡して回す。順番も検査も次の本へ進む判断も中に入っている。

```bash
venv/bin/python capture_books.py books.tsv          # 一覧をまとめて。中断しても books.progress.json から続く
venv/bin/python capture_book.py <ASIN> --title "<書名>"   # 1 冊だけ
```

1 冊ごとに、Brave で `#autocapture` 付きの URL を開き、ZIP が出たらその本のタブだけ閉じ、`img2txt.py`（既定 `--model vision`）、`check_book.py` の検査、直せるものは直して再検査（最大 3 周）、`report.json` を書く。検査が通れば次の本へ。`pages` / `text` で落ちたら ZIP が使えないので 1 回だけ取り直す。それ以外が残れば失敗として `books.progress.json` に記録し、次の本へ進む。終わったら、できた本と失敗した本（理由つき）をまとめて報告する。
