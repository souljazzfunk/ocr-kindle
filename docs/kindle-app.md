# Kindle for Mac アプリでの取り込み

CLAUDE.md のルール表の Kindle アプリの行に書き足されていた観察・時刻・発言を、消さずにここへ移した (2026-10-12、台帳 F071)。ルール表に残したのは決まりとそれを守らせるコードとテストだけ。挙動の記録は `fixtures/kindle/` (読み方は `expect.json`、検査は `test_fixtures.py`) と `capture_app.py --probe <ASIN>` の記録 (`~/Downloads/kindle-probe/<ASIN>/` の `ui-library.txt`・`ui-book.txt`・`screen-<画面>.png`・`probe.json`) が持つ。アプリの手順は当て推量で書かず、この記録から書く。

時刻はどれも 2026-10-11 (JST)。

## いつ使うか

- 取り込みは Chrome の Cloud Reader 拡張から始める。Kindle for Mac アプリは、ユーザー本人がそう書いたときだけ使う。コマンドには `KINDLE_APP_OK=1` を付ける (`.claude/hooks/guard_capture.py`)。
- 2026-10-11 に Laiken が `kindle_app_only` の本だけアプリで取り込むと決めた。`capture_app.py` は一覧のうちその本だけを開き、先頭に戻し、取り込み、取り込めたと記録して変換の仕事に渡す。`KINDLE_APP_OK=1` がないと始めない。最初に落ちた本で止まる。
- Mac の Kindle は新しい版 (7.40、窓名は常に Kindle、Go メニューなし)。2026-10-11 に Laiken がこの版に合わせると決めた。

## ライブラリで本を開く

- ライブラリの表紙ボタンを押して開く。見えなければ検索欄で探す。kindle:// のリンクでは開かない。
- 押す前に毎回 Kindle を前面にする。18:12 の実行では別のアプリが前面で、押しても開かなかった。
- Close Book の直後は表紙の AXPress が通らないことがある (18:12・18:24 の実行)。5 秒待ってから押し、開かなければ表紙の中心を実際にクリックする。
- 直前に読んだ本の表紙ボタンは枠が 0×0 で返り、押してもクリックしても開かない (18:42 の記録)。その本は下のタブバーの本名のボタンから開く。
- ライブラリのまま開かない本は、もう 1 回押してダウンロードを待つ。Laiken 17:21「もう 1 回クリックすればダウンロードが始まる」(台帳 F060)。

## 先頭に戻る

- 目次 (Table of Contents) を開き、一覧をスクロールホイールで一番上まで戻してから、一番上の項目を Accessibility API の AXPress で押す。
- Laiken 19:26「Cover でなくても一番上なら先頭へ行く」。19:26 に Mac で押して Location 185 → 1 を確認した。
- 目次は今の章の位置で開く。Sri Lanka 20:44、30% の本で「After One Month in Sri Lanka」が一番上。40 行の上スクロールで「Cover」が一番上に来た (`fixtures/kindle/ax-toc-sri-lanka-scrolled.txt`)。
- パネルは滑り込んでくるので、行が止まってからスクロールし、一番上の行が 2 回続けて変わらなくなるまで続ける。1 回で止めたら Sri Lanka は「Beginning」、Japan Travel Planner は 1.5 節から始まり表紙〜1.4 が抜けた (20:58 の記録)。
- 項目は静的テキスト (Bolivia、`fixtures/kindle/applescript-toc-bolivia.txt`) か、幅 473 px の AXButton (Sri Lanka。AppleScript の entire contents では見つからず、API の AXPress で押せた)。押せなければ中心をクリックする。
- 19:20 の記録では、項目はボタンではなく静的テキストで、題名・著者・Close menu の X の下に並んでいた。「その他」メニューには「Recommend this Book」が並ぶので押さない。
- 表紙にも脚注は出る (Sri Lanka 20:51 の記録)。20:45 の「出ない」はツールバーが隠れていただけだった。
- 脚注が読めないのに「項目を押せたから先頭」とはみなさない。#81 のその規則で Japan Travel Planner が 1.5 節から通ってしまった (20:58)。
- ページ番号の本 (Japan Travel Planner) は「Location N of M」でなく「Page N , P%」で位置を言う (21:06 の記録、`fixtures/kindle/ax-reader-pages-japan.txt`)。`location_of` はどちらも読む。
- 目次で動かなければ ← で 1 ページずつ戻り、20 回ごとに脚注の「Location N of M」を読んで Location 1 になるか下がらなくなるまで続ける。Laiken 19:27「何も使えないなら 1 ページずつ戻る。止まらない」(台帳 F061)。
- 脚注の総数は 0 で返ることがあるので、先頭かは N だけで判断する。
- 進み具合のスライダーは使わない。値の書き込み・左端のクリック・つまみのドラッグのどれでも動かなかった (19:02・19:11 の実行)。
- どの手で先頭へ戻ったかと秒数は `start:` の行に出る。

## 画面を読む

- 画面の部品は `ax.py` が Accessibility API (pyobjc) で読む。AppleScript の `entire contents` と部品ごとの属性読みは 234 行に 114 秒かかり (20:02 の計測)、本を開くのに 3〜4 分かかっていた。Laiken 19:59「遅すぎる」(台帳 F062)。
- 待ちは決め打ちの sleep でなく、目当ての部品が出るまで 0.3 秒ごとに読み直す (`wait_for`)。
- リーダーの部品の名前は title ではなく description に入っている (18:04 の記録)。

## ツールバー

- ツールバーが隠れていると要素の木にはグループしかない。ポインタを窓の上端に置いて出す (`hover_window`)。Sri Lanka 20:34、ページを 4 回クリックしても出ず、上端へのマウス移動で即出た (20:36 の記録)。1 回の移動で出なければ、ページへ動かしてから上端へ動かし直す。
- ページはクリックしない。クリックはページをめくる (20:40 の記録、試しのクリックで読書位置が 3% → 30% に進んだ、台帳 F064)。2 段組みでは窓の中央は段のあいだの余白で何も起きない (Laiken 20:39)。
- ツールバーが出たのを確かめてから押す。出なければ Escape を 2 回と Close で閉じてからクリックし直す。

## 読み終わりの画面と閉じ方

- 読み終えた本には「読み終わり」の画面 (評価・おすすめ・著者フォロー) が出て、その上に共有メニューが開くことがある (18:59 の実行)。
- 取り込みの → はその画面で評価の星にフォーカスを移す (Japan Travel Planner 20:59 の記録、`fixtures/kindle/ax-end-sheet-japan.txt`)。Enter やスペースは送らない。
- 取り込みのあとその画面が出ていれば、最後の画像 (その画面) を捨てて Escape で閉じる (`end_sheet_shown`)。
- ライブラリに戻るのはリーダーの Close Book ボタンだけ。File > Close や Cmd+W はアプリを終了させる (17:56 の記録)。

## 表から移した、アプリ以外の経緯

- 取り込みのブラウザは 2026-10-11 に Laiken が Brave から Chrome に替えた。`KINDLE_BROWSER=brave` で Brave に戻せる。2.0 の作り直しで、この決まりを守っていたのは `capture_book.py` が URL を `KINDLE_BROWSER` で開く処理だったが #86 で消え、2026-10-12 に Brave の拡張も同じ本を取った。今は `channel.py` の `get_job` が選んだブラウザの worker にだけ仕事を渡す (台帳 F072)。
- 画面の読み方の検査: 2026-10-11 の #56〜#76 の多くは、記録と違う木を思い込みで書いて実機で落ちた (title でなく description、0×0 の表紙、スライダーのない木)。新しい読み方を足すときは、その画面の `ax.dump()` を `fixtures/kindle/` に入れ、`expect.json` に何と読めるべきかを書く。`test_fixtures.py` は記録ごとに `in_library`・`chrome_shown`・`menu_open`・`location_of`・`cover_of`・`tab_of`・`toc_entries` を照合し、どの判定にも真の記録と偽の記録が 1 つずつ要る (台帳 F059)。
