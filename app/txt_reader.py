# -*- coding: utf-8 -*-
"""TXT 读取：自动检测编码（UTF-8 / GBK 等）+ 清洗冗余空行、广告垃圾、分段错乱。"""

from __future__ import annotations

import re
from pathlib import Path

from app.chapterizer import is_chapter_heading


class TxtReadError(Exception):
    pass


ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5")


def read_text_with_encoding(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, ValueError):
            continue
    try:
        import chardet
        guess = chardet.detect(data)
        enc = guess.get("encoding") or "utf-8"
        return data.decode(enc, errors="replace"), enc
    except ImportError:
        return data.decode("utf-8", errors="replace"), "utf-8"


def _is_junk(line: str, patterns: list) -> bool:
    s = line.strip()
    if not s:
        return False
    for pat in patterns:
        try:
            if re.match(pat, s, re.I):
                return True
        except re.error:
            continue
    return False


def _para_end(line: str) -> bool:
    return line.endswith(("。", "！", "？", "…", "”", "’", "：", "；", "）", ")", "】", "]", "》", "——", "…", "﹏"))


def clean_lines(text: str, rules: dict | None = None) -> list[str]:
    rules = rules or {}
    junk = rules.get("junk_patterns", [])
    chapter_patterns = rules.get("chapter_patterns", [])
    max_blank = max(1, int(rules.get("max_blank_lines", 1)))
    merge = bool(rules.get("merge_wrapped_lines", True))
    ensure_indent = bool(rules.get("ensure_paragraph_indent", True))
    indent_char = rules.get("paragraph_indent", "　　")

    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u200b-\u200d\u2060]", "", text)

    # 只去掉行尾空白；行首的全角缩进（　　）必须原样保留
    raw_lines = [ln.rstrip() for ln in text.split("\n")]

    cleaned: list[str] = []
    blank_run = 0
    for ln in raw_lines:
        if not ln.strip():
            blank_run += 1
            if blank_run <= max_blank:
                cleaned.append("")
            continue
        if _is_junk(ln, junk):
            continue
        blank_run = 0
        if (
            ensure_indent
            and not ln.startswith(("　", "\t", " ", "-", "—", "•"))
            and not is_chapter_heading(ln, chapter_patterns)
        ):
            cleaned.append(indent_char + ln.strip())
        else:
            cleaned.append(ln)

    # 合并被硬换行拆开的段落
    if merge and cleaned:
        merged: list[str] = []
        for ln in cleaned:
            if (
                merged
                and ln.strip()
                and merged[-1].strip()
                and not is_chapter_heading(merged[-1], chapter_patterns)
                and not _para_end(merged[-1].strip())
                and len(merged[-1]) < 80
            ):
                merged[-1] = merged[-1] + ln.lstrip("　 \t")
            else:
                merged.append(ln)
        cleaned = merged

    # 末尾空行
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return cleaned
