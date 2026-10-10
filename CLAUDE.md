# ocr-kindle

使い方は `README.md`。拡張のテストは `node --test`（CI でも同じコマンド）。

## ルール表

人に直された間違いは、まず仕組みで起きなくする。残ったものだけをここに書く。

| ルール | 何が守らせるか |
|---|---|
| 1 冊の取り込みは 1 回だけ。再読み込み・タブの復元・2 つ目のタブで同じ本をめくり直さない | `extension/autostart.js`（`#autocapture` を読んだらすぐ URL から消す、取り込み中のタブは 1 つ、保存済みの ASIN は自動では再取り込みしない）、テスト `extension/autostart.test.js` |
