# ocr-kindle

使い方は `README.md`。拡張のテストは `node --test`（CI でも同じコマンド）。

## ルール表

人に直された間違いは、まず仕組みで起きなくする。残ったものだけをここに書く。

| ルール | 何が守らせるか |
|---|---|
| 1 冊の取り込みは 1 回だけ。再読み込み・タブの復元・2 つ目のタブで同じ本をめくり直さない | `extension/autostart.js`（`#autocapture` を読んだらすぐ URL から消す、取り込み中のタブは 1 つ、保存済みの ASIN は自動では再取り込みしない）、テスト `extension/autostart.test.js` |
| 取り込みは Brave の Cloud Reader 拡張から始める。Kindle for Mac アプリは、ユーザー本人がそう書いたときだけ使う | `.claude/hooks/guard_capture.py`（`kindle2img.applescript` / `screencapture` / Kindle アプリを開くコマンドを止める。本人の指示があるときは `KINDLE_APP_OK=1` を付ける）、テスト `test_guard_capture.py` |
| Brave を終了・再起動しない。取り込みが終わったら本のタブだけ閉じる。Chrome で read.amazon を開かない | 同じフック（Brave の quit / kill、Chrome での read.amazon を止める） |
| 次の本に勝手に進まない。1 セッション 1 冊、結果を報告して OK をもらってから次へ | 同じフック（2 冊目の `#autocapture` は `NEXT_BOOK_OK=1` がないと止める） |
| 全文版リーダーの書名・テキスト層が取れないときに、当て推量で直さない | 拡張が ZIP に `dom-probe.json`（画面構造）を入れる。`extension/pagemeta.js`、テスト `extension/pagemeta.test.js` |
| 出来上がった Markdown の不具合（見出しなし、7→Z・8→& の読み違い、ダッシュ、重複ページ、書名）は機械が見つけて直す。人が気づくのを待たない | `check_book.py`（不具合ごとに直すコマンドを返す）、`capture_book.py`（直して再検査を最大 3 周、`report.json` に残す）、テスト `test_check_book.py` |
| 検査・フック・拡張のテストは CI で回す | `.github/workflows/ci.yml`（`node --test`、`pytest` を Linux と macOS で） |

## 取り込みの手順

1 冊は 1 コマンドで回す。順番も検査も中に入っている。

```bash
venv/bin/python capture_book.py <ASIN> --title "<一覧の書名>"
```

Brave で `#autocapture` 付きの URL を開き、ZIP が出たらその本のタブだけ閉じ、`img2txt.py`（既定 `--model vision`）、`check_book.py` の検査、直せるものは直して再検査、`report.json` を書く。終了コード 1 なら `blocking_failures` に残った項目を報告する。`warnings`（書名が一覧由来でない、テキスト層 0 ページ）はそのまま報告する。次の本は結果を報告して OK をもらってから。
