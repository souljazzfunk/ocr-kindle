#!/usr/bin/env python3
"""Convert an OCR'd Kindle Markdown book into a chaptered m4a audiobook.

Pipeline: Markdown --(split on chapter headings)--> chapter text --(macOS `say`)--> per-chapter m4a
          --(ffmpeg concat + chapter metadata)--> one m4a with chapter markers.

Standard library only. Requires macOS `say` and `ffmpeg`/`ffprobe` on PATH.

Usage:
    python3 md2audio.py "/path/to/book.md"                 # full book
    python3 md2audio.py book.md --chapters 1-2             # quick test
    python3 md2audio.py book.md --chapters 7-9,12          # only these chapters
    python3 md2audio.py book.md --out ~/Music/Kindle_audio # custom output root
    python3 md2audio.py book.md --voice Kyoko --rate 0      # another voice at its own speed
    python3 md2audio.py book.md --split                    # one file per chapter

The voice and speed follow the book's language: Kyoko (Enhanced) at 220 wpm for Japanese, Zoe (Premium) at
170 wpm for English.

Output: <out>/<book title>/<book title>.m4a  (+ chapters/NNN.m4a kept for resume)
        with --split, <out>/<book title>/NN_<chapter title>.m4a instead of the single file
Default <out> is the sibling folder "Kindle_audio" next to GOOGLE_DRIVE_FOLDER
from config.env, so the audiobook syncs to Google Drive and plays on iPhone.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# voice: default `say` voice; rate: default words per minute; stop: spoken after a chapter title so it ends
# with a pause; opening: title of the text before the first chapter.
LANGUAGES = {
    "ja": {"voice": "Kyoko (Enhanced)", "rate": 220, "stop": "。", "opening": "冒頭"},
    "en": {"voice": "Zoe (Premium)", "rate": 170, "stop": ".", "opening": "Opening"},
}


def language_of(md):
    """'ja' when kana and kanji outnumber English words, else 'en'. Words, not letters, so that a Japanese book
    full of names like iPhone still reads as Japanese."""
    cjk = len(re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]", md))
    return "ja" if cjk > len(re.findall(r"[A-Za-z]+", md)) else "en"


def load_config(path=HERE / "config.env"):
    cfg = {}
    if not path.exists():
        return cfg
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


# ---------- markdown → chapters ----------

def clean_text(md):
    """Strip Markdown syntax so `say` reads only prose."""
    t = md
    t = re.sub(r"```.*?```", "", t, flags=re.S)          # code fences
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)            # images
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)       # links → text
    t = re.sub(r"<[^>]+>", "", t)                          # html tags
    t = re.sub(r"^[ \t]{0,3}>[ \t]?", "", t, flags=re.M)  # blockquote marks
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)   # heading marks
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)        # bullet marks
    t = re.sub(r"^\s*\d+\.\s+", "", t, flags=re.M)        # numbered marks
    t = re.sub(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", "", t, flags=re.M)  # rules
    t = re.sub(r"^\s*\|.*\|\s*$", "", t, flags=re.M)      # table rows
    t = re.sub(r"[*_`~]+", "", t)                          # emphasis / code
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def split_chapters(md, min_chars=80, lang="ja"):
    """Split on the shallowest heading level used more than once; return [(chapter_title, text), ...].

    A single top-level heading is the book title, so it never counts as a chapter level.
    Headings with nothing between them (CHAPTER ONE / CHILDHOOD) form one chapter title.
    Sections shorter than min_chars are carried into the next one rather than dropped.
    """
    levels = [len(m.group(1)) for m in re.finditer(r"^(#{1,6})\s", md, flags=re.M)]
    split_level = min((lv for lv in set(levels) if levels.count(lv) > 1), default=0)
    heading = re.compile(rf"^#{{{split_level}}}\s+(.*)") if split_level else None

    sections = [(None, [])]
    for line in md.splitlines():
        m = heading.match(line) if heading else None
        if m and sections[-1][0] and not "".join(sections[-1][1]).strip():
            sections[-1][0].append(m.group(1).strip())
        elif m:
            sections.append(([m.group(1).strip()], []))
        else:
            sections[-1][1].append(line)

    out, carry = [], ""
    for heads, body in sections:
        body = clean_text("\n".join(body))
        if heads is None:
            if not body:
                continue
            ct, spoken = LANGUAGES[lang]["opening"], body
        else:
            ct = " ".join(heads)
            spoken = "".join(f"{h}{LANGUAGES[lang]['stop']}\n\n" for h in heads) + body
            spoken = spoken.rstrip()
        text = carry + spoken
        if len(text) < min_chars:
            carry = text + "\n\n"
            continue
        out.append((ct, text))
        carry = ""
    if carry:
        out.append((ct, carry.strip()))
    return out


def parse_chapters(spec, count):
    """'7-9,12' -> [7, 8, 9, 12]; '7-' runs to the last chapter. 1-based, like the file numbers."""
    picked = set()
    for part in spec.split(","):
        lo, dash, hi = part.strip().partition("-")
        first = int(lo)
        last = (int(hi) if hi else count) if dash else first
        if not 1 <= first <= last <= count:
            raise ValueError(f"chapter range {part.strip()!r} is outside 1-{count}")
        picked.update(range(first, last + 1))
    return sorted(picked)


# ---------- audio ----------

def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r


def duration_seconds(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)])
    return float(json.loads(r.stdout)["format"]["duration"])


def synthesize_chapter(text, out_m4a, voice, rate):
    txt = out_m4a.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    tmp = out_m4a.with_suffix(".part.m4a")
    cmd = ["say", "-v", voice, "-f", str(txt), "-o", str(tmp),
           "--file-format=m4af", "--data-format=aac"]
    if rate:
        cmd += ["-r", str(rate)]
    run(cmd)
    tmp.rename(out_m4a)


def safe_name(s, limit):
    return re.sub(r'[/\\:*?"<>|]', "_", s)[:limit].strip()


def chapter_filename(i, count, title):
    """NN_<title>.m4a, zero-padded to the chapter count so files sort in reading order."""
    return f"{i:0{max(2, len(str(count)))}d}_{safe_name(title, 80)}.m4a"


def tag_copy(src, dst, title, album, track):
    tmp = dst.with_suffix(".part.m4a")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-map_metadata", "-1", "-c", "copy",
         "-metadata", f"title={title}", "-metadata", f"album={album}", "-metadata", f"track={track}",
         "-metadata", "artist=Kindle (OCR)", "-metadata", "genre=Audiobook", str(tmp)])
    tmp.rename(dst)


def ffmeta_escape(s):
    return re.sub(r"([=;#\\\n])", r"\\\1", s)


def concat_with_chapters(chapter_files, titles, book_title, out_path):
    workdir = out_path.parent
    listfile = workdir / "concat.txt"
    listfile.write_text("".join(f"file '{p.as_posix()}'\n" for p in chapter_files), encoding="utf-8")

    meta = [";FFMETADATA1",
            f"title={ffmeta_escape(book_title)}",
            f"album={ffmeta_escape(book_title)}",
            "artist=Kindle (OCR)",
            "genre=Audiobook", ""]
    t = 0.0
    for p, ct in zip(chapter_files, titles):
        d = duration_seconds(p)
        meta += ["[CHAPTER]", "TIMEBASE=1/1000",
                 f"START={int(t * 1000)}", f"END={int((t + d) * 1000)}",
                 f"title={ffmeta_escape(ct)}", ""]
        t += d
    metafile = workdir / "chapters.ffmeta"
    metafile.write_text("\n".join(meta), encoding="utf-8")

    tmp = out_path.with_suffix(".part.m4a")
    run(["ffmpeg", "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-i", str(metafile), "-map_metadata", "1", "-c", "copy", str(tmp)])
    tmp.rename(out_path)
    return t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("md_file", help="Markdown produced by img2txt.py (its file name is the book title)")
    ap.add_argument("--out", help="Output root folder (default: <GOOGLE_DRIVE_FOLDER>/../Kindle_audio)")
    ap.add_argument("--voice", help="macOS voice (default: Kyoko (Enhanced) for Japanese, Zoe (Premium) for English; "
                                    "download it in System Settings > Accessibility > Spoken Content)")
    ap.add_argument("--rate", type=int, help="Speech rate in wpm (default: 220 for Japanese, 170 for English; 0 = voice default)")
    ap.add_argument("--chapters", help="Only these chapters, 1-based, e.g. 7-9,12 or 7- (default: all)")
    ap.add_argument("--min-chars", type=int, default=80, help="Merge chapters shorter than this into the next one")
    ap.add_argument("--split", action="store_true", help="Write one file per chapter instead of one file for the book")
    ap.add_argument("--force", action="store_true", help="Re-synthesize chapters that already exist")
    args = ap.parse_args()

    for tool in ("say", "ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            sys.exit(f"error: `{tool}` not found on PATH")

    md_path = Path(args.md_file).expanduser()
    md = md_path.read_text(encoding="utf-8")
    book_title = md_path.stem
    lang = language_of(md)
    voice = args.voice or LANGUAGES[lang]["voice"]
    rate = LANGUAGES[lang]["rate"] if args.rate is None else args.rate
    chapters = split_chapters(md, min_chars=args.min_chars, lang=lang)
    if not chapters:
        sys.exit("error: no text found")
    count = len(chapters)
    try:
        picked = parse_chapters(args.chapters, count) if args.chapters else range(1, count + 1)
    except ValueError as e:
        sys.exit(f"error: {e}")
    chapters = [(i, *chapters[i - 1]) for i in picked]

    if args.out:
        root = Path(args.out).expanduser()
    else:
        drive = load_config().get("GOOGLE_DRIVE_FOLDER")
        root = (Path(drive).expanduser().parent / "Kindle_audio") if drive else (HERE / "audio")
    safe_title = safe_name(book_title, 120)
    book_dir = root / safe_title
    ch_dir = book_dir / "chapters"
    ch_dir.mkdir(parents=True, exist_ok=True)

    total_chars = sum(len(t) for _, _, t in chapters)
    print(f"book: {book_title}")
    print(f"chapters: {len(chapters)} of {count}  chars: {total_chars:,}  language: {lang}  voice: {voice}  rate: {rate}")
    print(f"out: {book_dir}")

    files, titles = [], []
    for i, ct, text in chapters:
        out_m4a = ch_dir / f"{i:03d}.m4a"
        if out_m4a.exists() and not args.force:
            print(f"  [{i:03d}/{count}] skip (exists) {ct}")
        else:
            print(f"  [{i:03d}/{count}] {ct} ({len(text):,} chars)", flush=True)
            synthesize_chapter(text, out_m4a, voice, rate)
        files.append(out_m4a)
        titles.append(ct)

    if args.split:
        for (i, ct, _), p in zip(chapters, files):
            tag_copy(p, book_dir / chapter_filename(i, count, ct), ct, book_title, f"{i}/{count}")
        print(f"done: {book_dir}  ({len(files)} chapter files)")
        return

    final = book_dir / f"{safe_title}.m4a"
    total = concat_with_chapters(files, titles, book_title, final)
    h, m = divmod(int(total // 60), 60)
    print(f"done: {final}  ({h}h{m:02d}m, {len(files)} chapters)")


if __name__ == "__main__":
    main()
