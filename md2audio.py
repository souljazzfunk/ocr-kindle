#!/usr/bin/env python3
"""Convert an OCR'd Kindle Markdown book into a chaptered m4a audiobook.

Pipeline: Markdown --(split on chapter headings)--> chapter text --(macOS `say`)--> per-chapter m4a
          --(ffmpeg concat + chapter metadata)--> one m4a with chapter markers.

Standard library only. Requires macOS `say` and `ffmpeg`/`ffprobe` on PATH.

Usage:
    python3 md2audio.py "/path/to/book.md"                 # full book
    python3 md2audio.py book.md --max-chapters 2           # quick test
    python3 md2audio.py book.md --out ~/Music/Kindle_audio # custom output root
    python3 md2audio.py book.md --voice Kyoko --rate 0      # another voice at its own speed

The voice follows the book's language: Kyoko (Enhanced) for Japanese, Ava (Premium) for English.

Output: <out>/<book title>/<book title>.m4a  (+ chapters/NN.m4a kept for resume)
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

# voice: default `say` voice; stop: spoken after a chapter title so it ends with a pause; opening: title of the
# text before the first chapter.
LANGUAGES = {
    "ja": {"voice": "Kyoko (Enhanced)", "stop": "。", "opening": "冒頭"},
    "en": {"voice": "Ava (Premium)", "stop": ".", "opening": "Opening"},
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
    Sections shorter than min_chars are carried into the next one rather than dropped.
    """
    levels = [len(m.group(1)) for m in re.finditer(r"^(#{1,6})\s", md, flags=re.M)]
    split_level = min((lv for lv in set(levels) if levels.count(lv) > 1), default=0)
    heading = re.compile(rf"^#{{{split_level}}}\s+(.*)") if split_level else None

    sections = [(None, [])]
    for line in md.splitlines():
        m = heading.match(line) if heading else None
        if m:
            sections.append((m.group(1).strip(), []))
        else:
            sections[-1][1].append(line)

    out, carry = [], ""
    for ct, body in sections:
        body = clean_text("\n".join(body))
        if ct is None:
            if not body:
                continue
            ct, spoken = LANGUAGES[lang]["opening"], body
        else:
            spoken = f"{ct}{LANGUAGES[lang]['stop']}\n\n{body}".rstrip()
        text = carry + spoken
        if len(text) < min_chars:
            carry = text + "\n\n"
            continue
        out.append((ct, text))
        carry = ""
    if carry:
        out.append((ct, carry.strip()))
    return out


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
    ap.add_argument("--voice", help="macOS voice (default: Kyoko (Enhanced) for Japanese, Ava (Premium) for English; "
                                    "download it in System Settings > Accessibility > Spoken Content)")
    ap.add_argument("--rate", type=int, default=220, help="Speech rate in wpm (default: 220; 0 = voice default)")
    ap.add_argument("--max-chapters", type=int, default=0, help="Only synthesize the first N chapters (test runs)")
    ap.add_argument("--min-chars", type=int, default=80, help="Merge chapters shorter than this into the next one")
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
    chapters = split_chapters(md, min_chars=args.min_chars, lang=lang)
    if not chapters:
        sys.exit("error: no text found")
    if args.max_chapters:
        chapters = chapters[: args.max_chapters]

    if args.out:
        root = Path(args.out).expanduser()
    else:
        drive = load_config().get("GOOGLE_DRIVE_FOLDER")
        root = (Path(drive).expanduser().parent / "Kindle_audio") if drive else (HERE / "audio")
    safe_title = re.sub(r'[/\\:*?"<>|]', "_", book_title)[:120]
    book_dir = root / safe_title
    ch_dir = book_dir / "chapters"
    ch_dir.mkdir(parents=True, exist_ok=True)

    total_chars = sum(len(t) for _, t in chapters)
    print(f"book: {book_title}")
    print(f"chapters: {len(chapters)}  chars: {total_chars:,}  language: {lang}  voice: {voice}")
    print(f"out: {book_dir}")

    files, titles = [], []
    for i, (ct, text) in enumerate(chapters, 1):
        out_m4a = ch_dir / f"{i:03d}.m4a"
        if out_m4a.exists() and not args.force:
            print(f"  [{i:03d}/{len(chapters)}] skip (exists) {ct}")
        else:
            print(f"  [{i:03d}/{len(chapters)}] {ct} ({len(text):,} chars)", flush=True)
            synthesize_chapter(text, out_m4a, voice, args.rate)
        files.append(out_m4a)
        titles.append(ct)

    final = book_dir / f"{safe_title}.m4a"
    total = concat_with_chapters(files, titles, book_title, final)
    h, m = divmod(int(total // 60), 60)
    print(f"done: {final}  ({h}h{m:02d}m, {len(files)} chapters)")


if __name__ == "__main__":
    main()
