# ocr-kindle のルール表

人に直された間違いは、まず仕組みで起きなくする。残ったものだけをここに書く。

| ルール | 何が守らせるか |
|---|---|
| 1 冊の取り込みは 1 回だけ。2 つ目のタブやブラウザで同じ本をめくり直さない | `channel.py`、`test_channel.py::test_a_job_goes_to_one_worker_only`、`test_channel.py::test_bad_pages_and_other_jobs_are_turned_away`（F001 F018 F072） |
| 取り込みは `KINDLE_BROWSER`（既定 Chrome）の拡張だけが受け持つ | `channel.py` の `get_job`、`test_channel.py::test_only_the_chosen_browser_gets_jobs`、`extension/job.test.js::the worker names its browser so that only the chosen one gets jobs`（F072） |
| Chrome も Brave も終了・再起動しない | `.claude/hooks/guard_capture.py`、`test_guard_capture.py::test_the_browsers_are_never_restarted`（F002） |
| 取り込みが終わったら、拡張が自分で開いた窓だけを閉じる | `extension/background.js` の `endJob`、`extension/job.test.js::a job the channel dropped or replaced ends; an unreachable channel changes nothing`（F008 F034） |
| Kindle for Mac アプリは、ユーザー本人が頼んだときだけ使う | `.claude/hooks/guard_capture.py`、`test_guard_capture.py::test_kindle_app_needs_laikens_ask`、`test_capture_app.py::test_it_will_not_start_without_laikens_word`（F002 F015） |
| アプリで取り込むのは `kindle_app_only` の本だけ。取り込んだら変換の仕事に渡す | `capture_app.py`、`test_capture_app.py::test_only_the_app_only_books_are_captured_and_handed_to_the_convert_job`（F063）。経緯は `docs/kindle-app.md` |
| アプリで開かない本は、タブバーから開くか、もう 1 回押して待つ | `capture_app.py`、`test_capture_app.py::test_a_cover_not_downloaded_is_pressed_again_and_waited_for_as_laiken_said`、`test_capture_app.py::test_the_recent_book_whose_cover_has_no_frame_is_opened_from_the_tab_bar`（F060） |
| アプリで先頭に戻るのは、目次を一番上まで戻してその項目を押す | `capture_app.py` の `go_to_start`、`test_capture_app.py::test_the_start_is_reached_through_the_table_of_contents_and_read_from_the_footer`（F061） |
| 目次で先頭に戻れなければ、1 ページずつ先頭まで戻る | `capture_app.py` の `go_to_start`、`test_capture_app.py::test_when_the_table_of_contents_does_not_move_the_book_pages_are_turned_back_one_at_a_time`（F061） |
| アプリのツールバーはポインタを窓の上端に置いて出す。ページはクリックしない | `capture_app.py` の `hover_window`、`test_capture_app.py::test_the_toolbar_is_brought_by_resting_the_pointer_on_the_top_edge_never_by_a_click`（F064） |
| アプリの画面は Accessibility API で読み、決め打ちの秒数でなく部品が出るまで待つ | `ax.py`、`capture_app.py` の `wait_for`、`test_ax.py::test_the_tree_is_read_through_the_api_only_for_the_real_osascript`、`test_capture_app.py::test_the_run_path_waits_for_elements_not_for_fixed_seconds`（F062） |
| アプリの画面の読み方は、Mac で記録した木（`fixtures/kindle/`）で検査する | `capture_app.py`、`test_fixtures.py::test_each_recorded_tree_reads_as_expected`、`test_fixtures.py::test_each_flag_is_seen_true_and_false_in_the_recordings`（F059） |
| アプリの読み終わり画面は、最後の画像ごと捨てて Escape で閉じる | `capture_app.py` の `end_sheet_shown`、`test_capture_app.py::test_the_end_of_book_sheet_is_closed_and_its_image_dropped_after_a_capture`（F059） |
| アプリのライブラリに戻るのは Close Book ボタンだけ。File > Close や Cmd+W は使わない | `capture_app.py`、`test_capture_app.py::test_a_reader_left_open_is_closed_with_its_close_book_button_never_file_close`（F059） |
| 本は 1 ページ目から最後まで取り込む。開いた位置からそのまま取り込まない | `extension/policy.js`、`extension/policy.test.js::opened mid-book: pages back to page 1 saving each screen, then forward past them to the end`、`test_check_book.py::test_a_capture_that_began_mid_book_is_caught`（F007） |
| 始まりと終わりの位置が記録されていない取り込みは落とす。記録なしを通さない | `check_book.py` の `check_folder`、`test_check_book.py::test_a_capture_with_no_recorded_start_or_end_fails_both`（F128） |
| 先頭から遠くで開いた本は、最初にスクラバーで先頭へ跳ぶ。本を丸ごと逆にめくらない | `policy.js` の `JUMP_FROM`、`extension/main.js`、`extension/policy.test.js::a book opened far from its start jumps to the start before it saves anything, and pages back as before when the jump does not move it`（F107） |
| 前のページのボタンで戻れなくなったら矢印キーで戻り、止まった画面を記録する | `extension/policy.js`、`extension/policy.test.js::a reader stuck on Previous page pages back by the left arrow key`、`test_channel.py::test_the_screen_where_a_capture_stopped_is_kept_beside_the_trace`（F076） |
| 戻るはずのめくりで先のページに出たら、次の手（左キー、右キー）に替え、その画面は保存しない | `extension/policy.js` の `WAYS`、`extension/policy.test.js::a book read right to left, whose left arrow key turns forward, pages back by the right arrow key`（F093） |
| 次のページのボタンで進めなくなったら、終わり近くでない限り矢印キーで進む | `extension/policy.js` の `WAYS`・`nearEnd`、`extension/policy.test.js::a reader stuck on Next page mid-book pages on by the right arrow key, then by the left in a book read right to left`（F105） |
| 番号のない画面で進めなくなったら、最後に読めた番号で終わり近くかを決める | `extension/policy.js` の `edge`・`known`、`extension/policy.test.js::a reader stuck on an unnumbered screen far from the end pages on by the arrow key`（F132） |
| 位置は隠れたフッターの文字から先に読み、ページの本文はその次に読む | `extension/pagemeta.js` の `readPosition`、`extension/pagemeta.test.js::the position is read from the footer the reader hides, then from the page text`（F138） |
| ローマ数字の前付けのページは先頭近くと数え、その後で止まっても終わりにしない | `extension/policy.js` の `frontMatter`、`extension/policy.test.js::a reader stuck after Roman-numbered front matter pages on by the arrow key`（F138） |
| 取り込み中の待ちはワーカーのタイマーで数え、隠れた窓で抑えられるページのタイマーに頼らない | `extension/content.js` の `sleep`、`extension/background.js` の `sleep` 受け口、`extension/manifest.test.js::content.js waits on the worker timer, which Chrome does not hold back in a hidden window`（F140） |
| 隠れた取り込みの窓は、Mac が使われていない間は前に出す。使われている間は動かさない | `extension/job.js` の `showIfIdle`、`extension/background.js`、`extension/job.test.js::a hidden reader window comes to the front only while nobody is using the Mac`（F143） |
| 隠れた窓で固まった取り込みは、休みの回数を数えずに窓が前に出るまで休む | `extension/policy.js` の `canRest`、`extension/policy.test.js::a reader that froze in a hidden window rests until the window comes to the front`（F144） |
| 窓が隠れている間は、めくれなくなっても本の終わりと決めない | `extension/policy.js` の `edge`、`extension/policy.test.js::a hidden reader that stops turning near the end rests instead of ending the capture`（F146） |
| 取り込みの間は画面を消さない。画面が消えると窓が隠れてめくれなくなる | `channel.py` の `awake`、`test_channel.py::test_the_display_is_kept_awake_for_the_whole_capture_and_let_go_after`（F145） |
| 最初の画面に位置がまだ出ていなければ数秒待ってから、先頭へ跳ぶかを決める | `extension/policy.js` の `FIRST_POSITION_WAITS`、`extension/policy.test.js::a first screen without its footer yet waits for it, then jumps to the start from far in`（F143） |
| 戻る途中、先頭から遠くで止まった番号のない画面を本の先頭と見なさない | `extension/policy.js` の `edge`、`extension/policy.test.js::paging back, an unnumbered screen that stops turning far from page 1 is not taken for the start`（F143） |
| 先頭へ跳んだ直後は、ページの画像が出るまで長めに待つ | `extension/policy.js`、`extension/policy.test.js::after the jump to the start, a screen without its image yet is waited for`（F110） |
| どの手でもめくれなくなったら、2 分休んで最初の手からやり直す（1 冊 3 回まで） | `extension/policy.js` の `rest`、`extension/policy.test.js::a reader that stops turning mid-book both ways rests and starts over with the button before the capture gives up`（F108） |
| フッターの単位が途中で変わったら、前の単位の番号と比べずに画面を保存する | `extension/policy.js` の `savedPages`、`extension/policy.test.js::screens past a change of the footer unit are saved, not taken for screens before the last saved page`（F147） |
| 先頭から進み直すとき、開いた位置より前の画面を保存し直さない | `extension/policy.js`、`extension/policy.test.js::forward from page 1, a screen before the opening one is not saved again under a new hash`（F077） |
| ページめくりは際限なく続けない。上限はページ数に合わせ、向きごとに数える | `extension/policy.js`、`extension/policy.test.js::turns past three per page stop the capture, each direction counted on its own`（F017 F019 F027） |
| 止まった取り込みを 1 時間待たない。数分で理由を残して終える | `channel.py`、`test_channel.py::test_a_capture_with_no_page_and_no_event_for_the_stall_time_is_stalled`（F032 F039） |
| 工程ごとの秒数を report.json に残し、基準より遅い工程は speed の検査で落とす | `capture_book.py` の `speed`・`BUDGETS`、`channel.py`、`test_check_book.py::test_each_step_is_timed_in_the_report_and_a_slow_one_fails_the_speed_check`（F083） |
| 1 冊の時間は最初の取り込みから Drive まで数え、基準を超えた本を名指す | `kc_run.py` の `publish`、`book.py` の `Book.start`、`test_kc_run.py::test_a_book_s_time_from_its_first_capture_to_drive_is_recorded_once_and_a_slow_one_is_named`（F089） |
| 変換待ちの新しい本が 2 冊あれば、次の本を取り込まずに待つ | `kc_run.py` の `AHEAD`、`test_kc_run.py::test_the_capture_job_stays_at_most_two_new_books_ahead_of_the_convert_job`（F111） |
| 変換の仕事が止まっている間は、取り込みの仕事は変換を待たない | `kc_run.py` の `_step`、`test_kc_run.py::test_a_paused_convert_job_does_not_hold_the_capture_job`（F150） |
| 変換は、まだ公開していない本を先にし、公開済みの本の変換し直しは後にする | `kc_run.py` の `step`、`test_kc_run.py::test_a_new_book_is_converted_before_published_books_are_converted_again`（F112） |
| 公開した本の report.json と時間を Drive に写し、結果の確認を Mac のセッションに頼まない | `test_kc_run.py::test_a_published_book_s_report_and_time_are_copied_to_drive_for_the_cloud_to_read`、`test_check_book.py::test_a_book_converted_by_hand_also_leaves_its_report_on_drive`（F090 F092） |
| 変換の 1 歩ごとに全部の本の状態を Drive の `_books.json` に書き、一覧の様子を Mac に聞かない | `kc_run.py` の `step`・`describe`、`capture_book.py` の `publish_states`、`test_kc_run.py::test_each_convert_step_leaves_every_book_s_state_on_drive_for_the_cloud_to_read`（F117） |
| 本を別の題名で公開し直したら、Drive の前の写しを消して 1 冊 1 ファイルにする | `capture_book.py` の `publish`、`test_kc_run.py::test_a_book_published_again_under_a_new_title_leaves_one_copy_on_drive`（F120） |
| ページは画像でも canvas でも取り込み、画像がまだない画面は待ってから判断する | `extension/content.js` の `pageImage`、`extension/policy.js`、`extension/policy.test.js::a screen whose image is not there yet is observed again before the capture fails`（F067） |
| サンプルに転送された本や読み込み直されたタブで止まらない | `extension/job.js`、`extension/job.test.js::a job tab that reloads before sending a page gets its job again; after a page it gets none`、`extension/job.test.js::the sample reader is told apart from the book`（F066） |
| 全文版リーダーの画面が読めないときは、画面の記録から読む。当て推量で直さない | `extension/pagemeta.js`、`extension/pagemeta.test.js::the library list is read from the page JSON, authors without their trailing colon`、`test_channel.py::test_a_library_scan_writes_what_the_page_showed`（F067 F069） |
| 拡張は main と同じもの。ブランチや書きかけを読み込ませず、変えたら版を上げる | `extension_state.py`、`test_extension_state.py::test_capture_refuses_before_offering_the_job`、`test_extension_state.py::test_ci_requires_a_version_bump_when_the_extension_changes`（F009） |
| 拡張の変更が効いたと言うのは、実機の取り込みが通ったときだけ | 宿題（F012） |
| 取り込みのせいで止めた本は、取り込みのコードが変わったら取り直す | `book.py` の `RULES`・`Book.state`、`test_kc_run.py::test_a_book_parked_for_its_capture_by_the_checks_is_captured_again_once_the_extension_changes`（F095） |
| 同じ本を取り直すのは、取り込みが使えないと分かったときの 1 回だけ | `book.py` の `RULES`、`test_kc_run.py::test_an_unusable_capture_is_captured_once_more_then_the_book_is_parked`、`test_kc_run.py::test_a_stalled_capture_is_tried_once_more_then_parked`（F031） |
| 取り込めない本（アプリ専用・サンプル）は、その本だけを失敗にして次へ進む | `book.py` の `RULES`、`test_kc_run.py::test_kindle_app_only_parks_only_that_book_and_new_extension_code_does_not_retry_it`、`test_check_book.py::test_a_book_the_reader_opens_as_its_sample_fails_as_sample_only`（F033 F066） |
| 取り込まない本 (楽譜集など) は skip.tsv に書き、一覧からもフォルダからも外す | `skip.tsv`、`book.py` の `Library.add`、`test_book.py::test_a_book_in_skip_tsv_is_never_captured_or_converted_even_with_its_folder_left`（F142） |
| 落ちた本だけを止め、一覧は人に聞かずに次の本へ進む | `book.py` の `RULES`、`kc_run.py`、`test_kc_run.py::test_a_book_that_fails_its_checks_is_parked_and_the_list_goes_on`（F005 F020） |
| どの本でも落ちる失敗は、その仕事だけを止めて自分で再開する | `book.py` の `record_failure`、`test_kc_run.py::test_an_unreachable_extension_pauses_captures_for_every_book_and_resumes_by_itself`（F014） |
| 同じチェックが 2 冊続けて落ちたら、その仕事を新しいコードまで止める | `book.py` の `record_failure`、`test_kc_run.py::test_the_same_check_on_two_books_in_a_row_pauses_the_job_until_its_code_changes`（F023） |
| 別の工程のせいの検査で止めた仕事は、次の 1 歩で再開する | `book.py` の `Library.active_pause`、`test_kc_run.py::test_a_convert_pause_left_by_capture_failures_under_older_rules_is_lifted_on_the_next_step`（F096） |
| 本や仕事を止めた失敗は、Mac なしで再生できる記録を Drive に残す | `cases.py`、`test_cases.py::test_every_capture_case_replays_through_policy_js_to_the_actions_it_recorded`、`test_kc_run.py::test_a_parked_book_leaves_a_case_on_drive_with_its_report_and_pages`（F059） |
| 同じ本・同じチェック・同じ版の失敗の記録は 1 つだけ | `cases.py`、`test_cases.py::test_the_same_failure_with_the_same_versions_leaves_one_case_and_new_code_leaves_another`（F059） |
| main に入った修正は、人に頼まず次の 1 歩から使う | `kc_run.py`、`test_kc_run.py::test_main_is_pulled_each_step_and_local_changes_pause_the_job_with_gits_message`（F022） |
| 拡張の再読み込みを人に頼まない。版が変われば拡張が自分で読み込み直す | `extension/job.js` の `pollStep`、`extension/job.test.js::an idle worker on another version than the checkout reloads itself, at most once a minute`（F022） |
| 取り込めた本は取り直さない。通った本は変換のコードが変わってもやり直さない | `book.py` の `Book.state`、`test_kc_run.py::test_a_passed_book_stays_done_when_the_converter_changes_and_a_capture_is_not_redone`（F036） |
| 止まった本は、その検査のコードが変わったときだけやり直す | `book.py` の `touch_of`、`test_kc_run.py::test_a_parked_check_and_its_pause_rerun_only_when_that_check_s_code_changes`、`test_book.py::test_a_checks_code_is_the_functions_that_decide_it`（F127） |
| 一覧の進み具合は本のフォルダから出し、別のファイルに写さない | `book.py`、`test_kc_run.py::test_books_progress_json_is_carried_over_once_and_stale_results_are_redone`（F020 F031） |
| 1 冊のフォルダに同時に書くのは 1 つの変換だけ。checkout の git も 1 つずつ | `locks.py`、`test_locks.py::test_a_second_run_on_the_same_book_waits_for_the_first`、`test_locks.py::test_git_calls_on_the_checkout_wait_for_each_other_as_on_2026_10_11`（F038） |
| 1 冊を変換している間に次の本を取り込む。2 つの仕事は同じファイルに書かない | `kc_run.py`、`test_kc_run.py::test_the_convert_job_makes_no_book_folders`、`test_kc_run.py::test_the_launchd_jobs_run_one_step_of_each_job_and_are_started_again`（F063） |
| Drive の本を書き換えるのは検査に通ったときだけ。試しの変換で上書きしない | `capture_book.py` の `publish`、`test_check_book.py::test_a_book_that_failed_its_checks_is_not_copied_to_drive`、`test_check_book.py::test_only_the_runner_copies_a_book_to_drive_and_a_trial_conversion_does_not`（F036 F065） |
| Markdown の不具合は機械が見つけて直す。人が気づくのを待たない | `check_book.py`、`capture_book.py`、`test_check_book.py::test_the_defects_of_how_to_write_a_lot_are_each_named_with_a_fix`、`test_check_book.py::test_correct_runs_each_fix_once_and_stops_when_clean`（F004 F016 F025） |
| 校正が直すのは見つけた箇所だけ。コードの記号やプロンプトは消さない | `proofread.py`、`test_proofread.py::test_a_fix_lands_where_it_was_found_and_chevron_prompts_are_whole_again`、`test_proofread.py::test_stray_symbol_spares_code_and_fixes_the_one_it_found`（F041） |
| 索引の文字や題辞の上の番号を、見出しのない章の始まりと数えない | `check_book.py`、`test_check_book.py::test_index_letter_is_not_a_chapter_opening`、`test_check_book.py::test_a_numbered_section_with_an_epigraph_is_not_a_missed_chapter`（F042 F043） |
| コードの本でコードを見出し・箇条書き・地の文にしない | `layout.py`、`test_layout.py::test_code_is_fenced_line_by_line_and_never_a_title`、`test_check_book.py::test_a_code_book_without_code_blocks_is_caught_and_its_pages_read_again`（F044） |
| 配置がコードを段落や箇条書きに残したら、校正がコードブロックに入れ、つながったプロンプトを 1 行ずつに分ける | `proofread.py` の `loose_code`、`book.py` の `TOUCH`、`test_proofread.py::test_code_left_as_paragraphs_and_bullets_is_fenced_and_its_joined_prompts_split`（F141） |
| Vision のページの構造を、位置と大きさだけで当て推量しない | `layout.py`、`test_layout.py::test_titles_quotes_lists_and_art_take_their_place`（F016 F024） |
| 同じ読み直しを修正として繰り返さない | `layout.py`、`test_layout.py::test_layout_is_asked_once_per_page_in_batches`（F021 F024） |
| 縦書きの日本語は Live Text で読む。Vision が 1 行も返さないページを空で通さない | `img2txt.py`、`test_check_book.py::test_a_page_vision_finds_no_lines_on_is_read_by_live_text`（F068） |
| Vision が空白だけで読んだ行は捨て、ページの変換を止めない | `img2txt.py` の `vision_transcribe`、`test_img2txt.py::test_a_blank_line_vision_reads_does_not_crash_the_page`（F136） |
| Vision が短く読んだページは Live Text と比べ、多く読めたほうを使う | `img2txt.py` の `vision_transcribe`、`test_check_book.py::test_a_vertical_page_vision_reads_only_a_few_pieces_of_is_read_by_live_text`、`test_check_book.py::test_a_full_english_page_is_not_sent_to_live_text`（F073） |
| 日本語の本の英語のページ（引用・参考文献）を、外国語のページとして消さない | `proofread.py` の `foreign_page`、`test_proofread.py::test_english_pages_of_a_japanese_book_are_kept`（F074） |
| 記号だけの段落（傍点を読んだ読点やバックスラッシュ）は消し、切れた文をつなぐ | `proofread.py` の `emphasis_dots`、`test_proofread.py::test_emphasis_dots_read_as_commas_on_their_own_lines_are_removed_and_the_sentence_joined`（F106 F109） |
| 同じ位置で同じ文のページは、離れていても重複として消す | `proofread.py` の `duplicate_page`、`test_proofread.py::test_a_screen_saved_twice_at_one_position_is_a_duplicate`（F077） |
| 長いページが前のページと同じ文なら、位置が違っても重複として消す | `proofread.py` の `duplicate_page`・`shingles`、`test_proofread.py::test_screens_saved_again_far_apart_are_duplicates_even_at_other_positions`（F116） |
| 行末で段落に切れた日本語の文はつなぎ、間に入ったルビは消す | `proofread.py` の `split_sentence`、`test_proofread.py::test_a_japanese_sentence_cut_into_paragraphs_is_joined_and_a_reading_between_them_dropped`（F115） |
| Live Text の見出しから節の印を外し、目次の項目は見出しにせず、目次で階層を揃える | `layout.py` の `section_mark`・`livetext_contents`、`test_layout.py::test_live_text_titles_lose_the_section_mark_and_take_their_level_from_the_contents`（F078） |
| 節の印は、読み違えた字（十・＋・+）の仲間ごと見出しから外す | `layout.py` の `SECTION_MARKS`、`test_layout.py::test_titles_read_with_a_plus_lose_it_and_a_line_the_contents_lists_becomes_a_chapter`（F080） |
| 目次に載った短い行は、Gemini が段落にしても章の見出しにする | `layout.py` の `listed`、`test_layout.py::test_titles_read_with_a_plus_lose_it_and_a_line_the_contents_lists_becomes_a_chapter`（F081） |
| 目次に載った章が 1 つでも見出しになっていなければ、本を落とす | `check_book.py` の `contents_chapters`、`test_check_book.py::test_a_chapter_the_contents_lists_without_a_heading_is_caught`（F082） |
| 1 行に読まれた目次の数項目や、読み違えた章番号（第山章）も、見出しと照らせば通す | `layout.py` の `contents_covered`、`test_check_book.py::test_contents_lines_read_as_one_and_chapter_numerals_misread_still_find_their_headings`、`test_check_book.py::test_a_cut_contents_line_matches_its_headings`（F118） |
| 目次の続きは英語の文でも止める。目次の項目は見出しの並びと近似で照らす | `layout.py` の `holds_prose`・`contents_covered`、`check_book.py` の `contents_unheaded`、`test_check_book.py::test_the_recorded_contents_of_three_parked_books_find_their_headings`（F130） |
| 目次の表紙・扉は見出しを求めず、英語の Introduction などの前置きは外して見出しと照らす | `layout.py` の `LANDMARKS`・`SECTION_LABEL`、`test_check_book.py::test_the_landmarks_and_the_introduction_label_of_race_s_contents_need_no_heading_of_their_own`（F148） |
| 目次の見出しの下が文を行に切ったものなら、そのページを本の目次としない | `layout.py` の `reads_as_sentence`、`test_check_book.py::test_the_publisher_s_newsletter_page_under_a_table_of_contents_link_is_not_the_book_s_contents`（F149） |
| 日本語の本の節番号とその下の題は見出しにする。文の上の番号は章の始まりと数えない | `proofread.py` の `numbered_title`、`test_proofread.py::test_a_numbered_section_title_of_a_japanese_book_becomes_a_heading`、`test_check_book.py::test_a_number_over_a_japanese_sentence_is_not_a_chapter_opening`（F119） |
| 章の見出しの番号は目次の番号に揃え、段落とつながった見出しは段落に戻す | `layout.py` の `contents_spelling`、`test_layout.py::test_a_chapter_number_read_as_a_symbol_takes_the_contents_number_and_a_run_on_title_stays_a_paragraph`（F084） |
| 変換し直すとき、配置済みのページ（Live Text と Vision）も新しいコードで書き直す | `img2txt.py`、`test_img2txt.py::test_live_text_pages_laid_out_before_are_written_again_without_a_request`、`test_img2txt.py::test_vision_pages_laid_out_before_are_written_again_without_a_request`（F079 F131） |
| 配置済みのページの書き直しが変わったら、目次の検査で止まった本をやり直す | `book.py` の `TOUCH`、`test_book.py::test_a_contents_park_is_retried_when_the_rewriting_of_laid_out_pages_changes`（F131） |
| 校正は日本語の見出しを、文の途中で切れたものだけ外す | `proofread.py` の `not_a_title`、`test_img2txt.py::test_a_japanese_title_written_again_is_not_taken_back_as_heading_noise`、`test_book.py::test_a_contents_park_is_retried_when_proofreading_stops_taking_titles_back`（F134） |
| Live Text のページは毎回 Live Text 自身の読みから組み直す。前の出力から組み直さない | `img2txt.py` の `LIVE`・`restore_live`、`test_layout.py::test_a_second_layout_starts_from_live_text_s_reading_not_from_the_first_layout_s_output`（F088） |
| Live Text のページも見出しを Gemini に読ませる。段落のままにしない | `layout.py` の `livetext_markdown`、`test_layout.py::test_a_live_text_page_takes_its_titles_from_gemini`（F075） |
| 配置は Sonnet から順に試し、後ろが残るうちは本を止めない | `layout.py` の `provider_order`、`test_layout.py::test_sonnet_is_asked_first_and_a_spent_one_gives_way_to_the_next`、`test_layout.py::test_any_failed_gemini_request_stops_the_book_without_sending_the_rest`（F028） |
| OpenRouter は配置の順に入れない | `layout.py` の `PROVIDERS`、`test_layout.py::test_openrouter_is_not_a_layout_provider`（F028） |
| 短時間の制限は待ち直してから短い冷却にし、日次の上限とは分ける | `layout.py` の `failure_kind`、`test_layout.py::test_a_short_limit_is_retried_and_a_daily_quota_waits`（F028） |
| Ollama には文脈の長さを送り、入り切らない束は分ける | `layout.py` の `ollama_body`、`test_layout.py::test_ollama_sends_num_ctx_and_splits_a_batch_that_does_not_fit`（F028） |
| 書かれたリセット時刻まで休み、無ければ決め打ちの秒数にする | `layout.py` の `block_until`、`test_layout.py::test_a_stated_reset_is_kept_and_a_session_limit_is_quota`（F028） |
| 尽きた提供者だけをリセットまで休ませ、全部尽きたときだけ仕事を止める | `book.py` の `gemini_reset`、`test_layout.py::test_an_exhausted_provider_stays_out_until_its_reset`、`test_kc_run.py::test_a_gemini_quota_failure_waits_for_the_reset_and_converts_the_same_pages_with_no_new_capture`（F028） |
| Sonnet には Gemini と同じスキーマを --json-schema で渡す。自由文の JSON は読まない | `scripts/check_claude.py`、`layout.py` の `ask_claude`・`LAYOUT_SCHEMA`、`test_layout.py::test_a_claude_call_without_json_schema_is_the_steve_jobs_reply`（F121） |
| 各提供者のスキーマは LAYOUT_SCHEMA から描き、拒む形は通信せずに落とす | `layout.py` の `provider_rejected`、`test_layout.py::test_each_provider_schema_is_rejected_offline_when_the_shape_is_wrong`、`test_layout.py::test_the_schema_merged_in_2282ee7_is_rejected_by_gemini`（F122） |
| スキーマの 400 は変換コードが変わるまで待つ | `book.py` の `gemini_reset`、`test_kc_run.py::test_a_gemini_400_waits_for_new_code`（F122） |
| claude のモデルは config.env（既定 sonnet、haiku も）。ページにモデル名を残す | `layout.py`、`test_layout.py::test_the_claude_model_comes_from_config_and_is_named_on_the_page`（F028） |
| 同じページを二つのモデルで配置し、見出し・引用・図・コードの違いを出す | `layout.py` の `compare`、`test_layout.py::test_compare_reports_titles_quotes_figures_and_code`（F028） |
| ルール表の各行は決まりを確かめるテストを名前で挙げ、テストと共に CI で回す | `scripts/rules.py`、`.github/workflows/ci.yml`、`test_rules.py::test_a_named_test_that_does_not_exist_fails`（F071 F072） |
