# ocr-kindle

使い方は `README.md`。拡張のテストは `node --test`（CI でも同じコマンド）。

## ルール表

人に直された間違いは、まず仕組みで起きなくする。残ったものだけをここに書く。

| ルール | 何が守らせるか |
|---|---|
| 1 冊の取り込みは 1 回だけ。再読み込み・タブの復元・2 つ目のタブで同じ本をめくり直さない | `extension/autostart.js`（`#autocapture` を読んだらすぐ URL から消す、取り込み中のタブは 1 つ、保存済みの ASIN は自動では再取り込みしない）、テスト `extension/autostart.test.js` |
| 取り込みは Brave の Cloud Reader 拡張から始める。Kindle for Mac アプリは、ユーザー本人がそう書いたときだけ使う | `.claude/hooks/guard_capture.py`（`capture_app.py` / `kindle2img.applescript` / `screencapture` / Kindle アプリを開くコマンドを止める。本人の指示があるときは `KINDLE_APP_OK=1` を付ける）、テスト `test_guard_capture.py`。2026-10-11 に Laiken が `kindle_app_only` の本だけアプリで取り込むと決めた：`capture_app.py` は一覧のうちその本だけを開いて先頭に戻し、取り込み、同じ検査に通す（`KINDLE_APP_OK=1` がないと始めない、最初に落ちた本で止まる）。テスト `test_capture_app.py` |
| 同じ本を取り直すのは、検査で ZIP が使えないと分かったときだけ | 拡張は `#autocapture` では保存済みの本を始めない。`capture_books.py` だけが `#recapture` で取り直す（それでも 1 回・1 タブ） |
| Brave を終了・再起動しない。取り込みが終わったら本のタブだけ閉じる。Chrome で read.amazon を開かない | 同じフック（Brave の quit / kill、Chrome での read.amazon を止める） |
| 次の本へ進むかは検査結果で決める。通れば次へ、ZIP が使い物にならなければ取り直し。Markdown の検査が直しきれなければ一覧を止め、変換を直してその本が通るまで次の本へ進まない（Laiken：試しに 1 冊、正しくできるようになったら全部）。人の OK は待たない | `capture_books.py`（`decide()`、失敗で終了コード 2。`--keep-going` で記録して次へ）、テスト `test_capture_books.py` |
| ブラウザか拡張のせいの失敗（保存、版、タブ、先頭・末尾）が残ったら、一覧を進めずに止める。拡張の変更が効いたと言うのは、実機の取り込みでこれが通ったときだけ | `capture_books.py` の `STOP_CHECKS`（取り直しのあとも残れば終了コード 2 で止まり、同じコマンドで続きから）、テスト `test_capture_books.py` |
| 本は 1 ページ目から最後まで取り込む。リーダーは前回読んだ位置で開くので、そのまま取り込まない | `extension/content.js`（自動取り込みは 1 ページ目まで戻ってから始め、戻れなければ断る。戻るときはリーダーの道具を使う：進み具合のスライダーを先頭へ、だめなら目次の先頭の項目、残りだけ 1 ページずつ。どれを使ったかは `capture.json` の `start`。ZIP に `positions.json`）、`check_book.py` の `start_position` / `end_position`（落ちたら取り直し）、テスト `extension/pagemeta.test.js`・`test_check_book.py` |
| 取り込みが終わった本のタブは確実に閉じる | 拡張の worker が ZIP の保存完了後に自動取り込みのタブだけ閉じる（`tabToClose`、テスト `extension/autostart.test.js`）。`capture_book.py` が残っていないか確かめ、残れば閉じ、閉じられなければ `tab_closed` で落とす |
| Brave で動く拡張は main と同じもの。ブランチやローカルの書きかけを読み込ませない。変えたら版を上げる | `extension_state.py`（拡張フォルダが main でない・書きかけがあると `capture_book.py` が取り込みを断る）、拡張が ZIP に `capture.json`（版）を入れ、`extension_version` で再読み込み忘れを落とす、CI `scripts/check_extension_version.py`（extension/ を変えて版が上がっていない PR を落とす）、テスト `test_extension_state.py` |
| ZIP はダイアログなしで保存する。保存できたと言うのは Brave の履歴で拡張が保存を終えたと確かめたときだけ | `brave.py`（読むだけ）。`capture_books.py` は Brave が保存場所を尋ねる設定のままなら始めない。`capture_book.py` の `download` が Brave の履歴で拡張による保存と完了を確かめる。テスト `test_brave.py` |
| 取り込みのページめくりは際限なく続けない。取り込めないときは 1 時間待たずに理由を残して止まる。一覧を続きから再開するとき、同じ本のタブを 2 つ開かない | `extension/pagemeta.js` の `turnGuard`（戻る・進むとも、同じページで 30 回めくったら止める。回数の上限はページ数 × 3 + 50。1 ページに 2 回以上めくる本があるので、ページ数ぴったりにはしない）、ZIP の `capture.json` の `stopped` を `check_book.py` の `capture_stop` で落とす。拡張は自動取り込みを断ると `<ASIN>.skip.json` に理由を書いてタブを閉じ、`capture_book.py` はそれを見てすぐ `capture_refused` にする。本のタブが ZIP も理由も残さずに消えたら、2 分で `tab_gone` にする。拡張は取り込み中、タブ名の先頭に `[kc 段階 ページ 表示/非表示]` を出し、`capture_book.py` がそれを読む。5 分動かないか、タブが 5 分画面に出ていなければ `capture_stall` にする（動いている長い本は 4 時間まで待つ）。`tab_gone`・`no_zip`・`capture_stall` は新しいタブで 1 回だけ自動で取り直し、それでも出なければ止まる。Cloud Reader で開けない本（「Kindle App Is Required」）は拡張が理由つきで断り、`kindle_app_only` としてその本だけ失敗にして次へ進む（Kindle アプリは使わない）。`capture_book.py` は開く前に同じ本のタブを閉じ、閉じられなければ `tab_before`。どれも `STOP_CHECKS` で一覧を止め、その本は次に再開したとき `#recapture` でやり直す。テスト `extension/pagemeta.test.js`・`test_check_book.py`・`test_capture_books.py` |
| main に入った修正は、人に頼まず次の本から使う。拡張が変わったときだけ、再読み込みを 1 回頼んで止まる | `capture_books.py` の `update_checkout`（本ごとに `git pull --ff-only`。Python が変われば自分を起動し直して続きから、`extension/` が変われば終了コード 2 で止まる）、テスト `test_capture_books.py` |
| 変換を直したら、前に検査で落ちた本も人に頼まず、保存済みの ZIP から変換し直す | `capture_books.py` の `converter_version` と `stale_failure`（記録に変換コードの版を残し、版が変わっていれば保存済みの ZIP からその本を 1 回だけ変換し直す。ZIP が使えないと分かったときだけ取り直す。Kindle アプリ専用の本は対象外）、テスト `test_capture_books.py` |
| 1 冊のフォルダ（と Drive のその本）に同時に書くのは 1 つの変換だけ。1 つの一覧を回すのも 1 本だけ | `locks.py`（本のフォルダの隣の `.<書名>.lock` と一覧の `books.lock` を flock で持つ。`capture_book.convert`・`img2txt.py`・`proofread.py` は本の鍵が空くまで待ち、`capture_books.py`・`capture_app.py` は一覧の鍵が取れなければ始めない。子のコマンドは親の鍵を引き継ぐ）、テスト `test_locks.py`・`test_capture_books.py` |
| Brave を遊ばせない。1 冊を変換している間に、次の本を Brave で取り込む。ただし Brave で取り込むのは 1 冊、変換も 1 冊ずつ。結果は一覧の順に記録し、止まる理由が出たら、取り込み済みの本を変換して記録してから止まる | `capture_books.py` の `Job`・`Converting`・`make_room`・`settle`（`capture_book.convert` の `brave` と `converting` で取り込みと変換を 1 つずつにする）。main が動いたときは、変換中の本を終えてから pull する（`update_checkout` の `settle`）。テスト `test_capture_books.py` |
| Drive の本を書き換えるのは `capture_book.py` の検査と修正のあとだけ。試しの変換で Drive を上書きしない | `capture_book.py` の `publish`。`img2txt.py` と `proofread.py` は本のフォルダにだけ書く。テスト `test_check_book.py` |
| 全文版リーダーの書名・テキスト層が取れないときに、当て推量で直さない | 拡張が ZIP に `dom-probe.json`（画面構造）を入れる。`extension/pagemeta.js`、テスト `extension/pagemeta.test.js` |
| 出来上がった Markdown の不具合（見出しなし、7→Z・8→& の読み違い、ダッシュ、重複ページ、書名、Al→AI、'11→'ll、欠けた `>>>`）は機械が見つけて直す。人が気づくのを待たない。コードの `<` `>` は迷い込んだ記号として消さない。直すのは見つけた箇所だけ | `check_book.py`（不具合ごとに直すコマンドを返す）、`capture_book.py`（直して再検査を最大 3 周、校正は各周の最後、`report.json` に残す）、テスト `test_check_book.py`・`test_layout.py` |
| Vision のページの構造（見出し・引用・図・飾り・頭文字）を位置と大きさだけで当て推量しない。同じ読み直しを修正として繰り返さない | `layout.py`（Gemini がページ画像と行番号から構造だけを答え、`layout.json` に残す。章の階層は本の目次で揃える）、`check_book.py` の `layout`（未読のページがあれば落として読み直す）、テスト `test_layout.py` |
| Gemini の呼び出しが 1 回でも失敗したら（1 日の上限 500 回を含む）、残りのページは Gemini に送らず Mac の Sonnet（`claude -p`）に回す。Sonnet も失敗したら、本を失敗にせず、その場で一覧を止めて理由を残す。再開したら取り直さず、保存済みのフォルダから変換する | `layout.py` の `ask_layout_claude` と `GeminiFailed`（PerDay の 429 は待たない。1 回 20 ページ。ログに `GEMINI ERROR`。Sonnet が読んだページは `layout.json` に `"model": "claude"`）、`img2txt.py` が `gemini_error.txt` に理由を書いて終了コード 3、`capture_book.py` の `gemini`、`capture_books.py` が `waiting` と `source` を記録して終了コード 2。テスト `test_layout.py`・`test_check_book.py`・`test_capture_books.py` |
| 検査を通った本は、その場で LLM wiki への取り込みを始める。次の本の取り込みと並行で進め、wiki の毎時の実行や人を待たない | `capture_book.py` の `start_wiki_ingest`（`report.json` が通ったら llm-wiki の `scripts/watch.sh` を切り離して起動。場所は `WIKI_WATCH` で変えられる）。watch.sh は実行中なら次の 1 回を予約し、検証済みの本は 30 分の安定待ちを飛ばす。テスト `test_check_book.py` |
| 検査・フック・拡張のテストは CI で回す | `.github/workflows/ci.yml`（`node --test`、`pytest` を Linux と macOS で） |

## 取り込みの手順

一覧（`ASIN<TAB>書名` の行）を渡して回す。順番も検査も次の本へ進む判断も中に入っている。

```bash
venv/bin/python capture_books.py books.tsv          # 一覧をまとめて。中断しても books.progress.json から続く
venv/bin/python capture_book.py <ASIN> --title "<書名>"   # 1 冊だけ
```

1 冊ごとに（前の本の変換と並行で）、Brave で `#autocapture` 付きの URL を開き、ZIP が出たらその本のタブだけ閉じ、`img2txt.py`（既定 `--model vision`）、`check_book.py` の検査、直せるものは直して再検査（最大 3 周）、`report.json` を書く。検査が通ればその本の wiki 取り込みを裏で始めて、次の本へ。`pages` / `text` で落ちたら ZIP が使えないので 1 回だけ取り直す。それ以外が残れば失敗として `books.progress.json` に記録し、一覧を止める（変換を直してその本が通ってから再開）。終わったら、できた本と失敗した本（理由つき）をまとめて報告する。
