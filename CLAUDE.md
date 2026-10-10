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
| ブラウザか拡張のせいの失敗（保存、版、タブ、先頭・末尾）が残ったら、一覧を進めずに止める。拡張の変更が効いたと言うのは、実機の取り込みでこれが通ったときだけ | `capture_books.py` の `STOP_CHECKS`（取り直しのあとも残れば終了コード 2 で止まり、同じコマンドで続きから）、テスト `test_capture_books.py` |
| 本は 1 ページ目から最後まで取り込む。リーダーは前回読んだ位置で開くので、そのまま取り込まない | `extension/content.js`（自動取り込みは 1 ページ目まで戻ってから始め、戻れなければ断る。ZIP に `positions.json`）、`check_book.py` の `start_position` / `end_position`（落ちたら取り直し）、テスト `extension/pagemeta.test.js`・`test_check_book.py` |
| 取り込みが終わった本のタブは確実に閉じる | 拡張の worker が ZIP の保存完了後に自動取り込みのタブだけ閉じる（`tabToClose`、テスト `extension/autostart.test.js`）。`capture_book.py` が残っていないか確かめ、残れば閉じ、閉じられなければ `tab_closed` で落とす |
| Brave で動く拡張は main と同じもの。ブランチやローカルの書きかけを読み込ませない。変えたら版を上げる | `extension_state.py`（拡張フォルダが main でない・書きかけがあると `capture_book.py` が取り込みを断る）、拡張が ZIP に `capture.json`（版）を入れ、`extension_version` で再読み込み忘れを落とす、CI `scripts/check_extension_version.py`（extension/ を変えて版が上がっていない PR を落とす）、テスト `test_extension_state.py` |
| ZIP はダイアログなしで保存する。保存できたと言うのは Brave の履歴で拡張が保存を終えたと確かめたときだけ | `brave.py`（読むだけ）。`capture_books.py` は Brave が保存場所を尋ねる設定のままなら始めない。`capture_book.py` の `download` が Brave の履歴で拡張による保存と完了を確かめる。テスト `test_brave.py` |
| 取り込みのページめくりは際限なく続けない。取り込めないときは 1 時間待たずに理由を残して止まる。一覧を続きから再開するとき、同じ本のタブを 2 つ開かない | `extension/pagemeta.js` の `turnGuard`（戻る・進むとも、本のページ数 + 50 回か、同じページで 30 回めくったら止める）、ZIP の `capture.json` の `stopped` を `check_book.py` の `capture_stop` で落とす。拡張は自動取り込みを断ると `<ASIN>.skip.json` に理由を書いてタブを閉じ、`capture_book.py` はそれを見てすぐ `capture_refused` にする。`capture_book.py` は開く前に同じ本のタブを閉じ、閉じられなければ `tab_before`。どれも `STOP_CHECKS` で一覧を止める。テスト `extension/pagemeta.test.js`・`test_check_book.py`・`test_capture_books.py` |
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
