# -*- coding: utf-8 -*-
"""HTML 源文件生成：完全复用样例的 class 结构与原版 CSS，实现 1:1 排版。"""

from __future__ import annotations

import html
from typing import Optional

from app.chapterizer import Chapter
from app.meta_loader import Meta


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _esc_attr(text: str) -> str:
    return html.escape(text or "", quote=True)


def build_book_html(
    meta: Meta,
    chapters: list,
    css_files: list,
    cover_file: str = "cover.jpg",
    intro_heading: str = "内容简介",
) -> str:
    lang = meta.lang or "zh"
    title = meta.title or "未命名"
    doc_title = f"{title} - {meta.author}" if meta.author else title

    css_links = "\n".join(
        f'<link rel="stylesheet" type="text/css" href="{_esc_attr(f)}"/>' for f in css_files
    )

    parts = [
        "<!DOCTYPE html>",
        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{_esc_attr(lang)}" xml:lang="{_esc_attr(lang)}">',
        "<head>",
        '<meta charset="utf-8"/>',
        f"<title>{_esc(doc_title)}</title>",
        css_links,
        "</head>",
        '<body class="calibre5">',
        '<div class="calibre">',
        f'<div class="pic"><img src="{_esc_attr(cover_file)}" class="calibre1" alt="封面"/></div>',
        f'<h1 class="calibre2">{_esc(title)}</h1>',
    ]
    if meta.author:
        parts.append(
            f'<div class="author"><b class="calibre3">{_esc(meta.author)}</b> '
            f'<span class="calibre4">/ 著</span></div>'
        )
    parts.append("</div>")

    paras = meta.description_paragraphs()
    if paras:
        parts.append(f'<h1 class="head">{_esc(intro_heading)}</h1>')
        for p in paras:
            if not p.startswith(("　", "\t", " ")):
                p = "　　" + p
            parts.append(f'<p class="calibre6">{_esc(p)}</p>')

    for ch in chapters:
        badge = _esc(ch.label)
        if ch.title:
            parts.append(
                f'<h2 class="head1"><span class="chapter-sequence-number">{badge}</span>'
                f'<br class="calibre7"/>{_esc(ch.title)}</h2>'
            )
        else:
            parts.append(f'<h2 class="head1"><span class="chapter-sequence-number">{badge}</span></h2>')
        # 与参考样例一致：标题下方再带一行“第N章 章节名”正文，
        # 保证任何阅读器里每章开头都能看到章节名。
        heading_text = f"{ch.label} {ch.title}".strip()
        if heading_text:
            parts.append(f'<p class="calibre6">{_esc(heading_text)}</p>')
        for p in ch.paragraphs:
            parts.append(f'<p class="calibre6">{_esc(p)}</p>')

    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)
