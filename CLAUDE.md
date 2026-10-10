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

## 取り込みの手順

1. `open -a "Brave Browser" 'https://read.amazon.com/?asin=<ASIN>#autocapture'`（英語の本は .com のアカウント）
2. `~/Downloads/<書名>.zip` が出たら、その本のタブだけ閉じる
3. `venv/bin/python img2txt.py ~/Downloads/<書名>.zip`（英語は `--model vision` で枠を使わない）
4. `venv/bin/python proofread.py ~/Downloads/<書名>` で件数を見て、サンプルが正しい種類だけ `--apply`
5. 結果（ページ数、文字数、`dom-probe.json` の中身で分かったこと）を報告し、次の本は OK を待つ
