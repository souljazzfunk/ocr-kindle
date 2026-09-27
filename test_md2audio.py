from md2audio import clean_text, language_of, split_chapters


def test_splits_on_repeated_level_under_a_single_title():
    md = "# 本の題\n\n前書きの本文。\n\n## 第1章\n\n### 節\n\n本文その一。\n\n## 第2章\n\n本文その二。\n"
    assert split_chapters(md, min_chars=0) == [
        ("冒頭", "本の題\n\n前書きの本文。"),
        ("第1章", "第1章。\n\n節\n\n本文その一。"),
        ("第2章", "第2章。\n\n本文その二。"),
    ]


def test_short_sections_are_merged_forward_not_dropped():
    md = "## 扉\n\n短い。\n\n## 本編\n\n" + "長い本文。" * 20 + "\n\n## 奥付\n\n終。\n"
    chapters = split_chapters(md, min_chars=50)
    assert [ct for ct, _ in chapters] == ["本編", "奥付"]
    assert chapters[0][1].startswith("扉。\n\n短い。\n\n本編。")
    assert chapters[1][1] == "奥付。\n\n終。"


def test_book_without_headings_is_one_chapter():
    assert split_chapters("ただの本文。\n", min_chars=0) == [("冒頭", "ただの本文。")]


def test_clean_text_drops_figure_notes_and_quote_marks():
    md = "He smiled.\n\n![Jobs in 1982]()\n\n> I called up Bill.\n\nThe end."
    assert clean_text(md) == "He smiled.\n\nI called up Bill.\n\nThe end."


def test_english_book_gets_english_pauses_and_opening():
    md = "Praise for the book.\n\n# CHAPTER ONE\n\nWhen Paul Jobs was mustered out.\n\n# CHAPTER TWO\n\nWoz.\n"
    assert language_of(md) == "en"
    assert split_chapters(md, min_chars=0, lang="en") == [
        ("Opening", "Praise for the book."),
        ("CHAPTER ONE", "CHAPTER ONE.\n\nWhen Paul Jobs was mustered out."),
        ("CHAPTER TWO", "CHAPTER TWO.\n\nWoz."),
    ]


def test_japanese_book_is_detected_despite_latin_words():
    assert language_of("# 第1章\n\nAppleのiPhoneは2007年に発表された。") == "ja"
