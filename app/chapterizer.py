# -*- coding: utf-8 -*-
"""章节切分：按中文小说常见章节格式切分 TXT，并把中文数字统一为阿拉伯数字。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


class ChapterError(Exception):
    pass


CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}


def cn_to_int(text: str) -> Optional[int]:
    """中文数字转整数：二十一 -> 21，一千零五 -> 1005。"""
    if text.isdigit():
        return int(text)
    result, tmp = 0, 0
    for ch in text:
        if ch in CN_DIGITS:
            tmp = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            unit = CN_UNITS[ch]
            if unit >= 10000:
                result = (result + (tmp or 1)) * unit
                tmp = 0
            else:
                result += (tmp or 1) * unit
                tmp = 0
        else:
            return None
    return result + tmp


@dataclass
class Chapter:
    label: str                    # 显示用的章节徽标：第1章 / 序章
    title: str                    # 章节标题（可空）
    paragraphs: list = field(default_factory=list)


def match_chapter_start(line: str, patterns: list) -> Optional[re.Match]:
    s = line.strip()
    for pat in patterns:
        try:
            m = re.match(pat, s, re.I)
        except re.error:
            continue
        if m:
            return m
    return None


def is_chapter_heading(line: str, patterns: list) -> bool:
    """章节标题判定：命中模式，且行短、不以句末标点结尾（避免正文误切分）。"""
    m = match_chapter_start(line, patterns)
    if not m:
        return False
    s = line.strip()
    if len(s) > 60:
        return False
    if s.endswith(("。", "！", "？", "…", "”", "’", "」", "』", "）", ")", "：", "；", "——")):
        return False
    return True


def _make_label(match: re.Match, normalized: bool) -> tuple[str, str]:
    """返回 (章节徽标, 章节标题)。"""
    s = match.string
    matched = match.group(0)
    title = s[match.end():].strip()
    title = re.sub(r"^[、.．:：\s　\-_]+", "", title)

    # 第N章/回/节/卷
    m = re.search(r"第\s*([0-9零一二三四五六七八九十百千两〇]+)\s*([章回节卷部篇集])", matched)
    if m:
        num = cn_to_int(m.group(1))
        unit = m.group(2)
        if num is not None:
            if normalized:
                return f"第{num}{unit}", title
            return matched.strip(), title
    # Chapter N
    m = re.search(r"(?:Chapter|CHAPTER|chapter)\s+(\d+)", matched)
    if m:
        num = int(m.group(1))
        if normalized:
            return f"第{num}章", title
        return matched.strip(), title
    # 序章/楔子等
    return matched.strip(), title


def split_chapters(
    lines: list,
    patterns: list,
    normalized: bool = True,
    fallback_title: str = "正文",
    drop_preamble: bool = True,
) -> list:
    chapters: list = []
    current = None
    preamble: list = []

    for ln in lines:
        if not ln.strip():
            continue
        if is_chapter_heading(ln, patterns):
            m = match_chapter_start(ln, patterns)
            label, title = _make_label(m, normalized)
            current = Chapter(label=label, title=title, paragraphs=[])
            chapters.append(current)
            continue
        if current is None:
            preamble.append(ln)
        else:
            current.paragraphs.append(ln)

    if not chapters:
        chapters.append(Chapter(label="第1章", title=fallback_title, paragraphs=preamble))
        return chapters

    if preamble:
        if drop_preamble and len(preamble) <= 6:
            pass  # 书名/作者等前置行，直接丢弃
        else:
            chapters.insert(0, Chapter(label="卷首", title="", paragraphs=preamble))

    # 丢弃空章节
    chapters = [c for c in chapters if c.paragraphs or c.title]
    return chapters
