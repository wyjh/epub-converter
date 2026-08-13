# -*- coding: utf-8 -*-
"""转换后校验：逐项比对固化模板参数，任何偏差都视为失败，杜绝样式漂移。"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from app.meta_loader import Meta
from app.template import Template


def _fix_zip_name(name: str) -> str:
    try:
        return name.encode("cp437").decode("gbk")
    except Exception:
        return name


def _minify(css: str) -> str:
    return re.sub(r"\s+", "", css).lower()


def verify_epub(
    epub_path: Path,
    template: Template,
    meta: Meta,
    chapter_count: int,
) -> list:
    issues: list = []
    try:
        zp = zipfile.ZipFile(epub_path)
    except zipfile.BadZipFile:
        return ["输出不是有效的 ZIP/EPUB 文件"]

    names = [_fix_zip_name(n) for n in zp.namelist()]

    # mimetype
    if names and not names[0].endswith("mimetype"):
        issues.append("mimetype 不是压缩包首个条目")
    try:
        if zp.read("mimetype").decode("ascii", errors="replace").strip() != "application/epub+zip":
            issues.append("mimetype 内容不正确")
    except KeyError:
        issues.append("缺少 mimetype 文件")

    def find(suffix: str) -> str:
        for n in names:
            if n.endswith(suffix):
                return n
        return ""

    # 元信息
    opf_name = find("content.opf")
    opf = zp.read(opf_name).decode("utf-8", errors="replace") if opf_name else ""
    if not opf:
        issues.append("缺少 content.opf")
    else:
        if meta.title and meta.title not in opf:
            issues.append(f"OPF 中缺少书名：{meta.title}")
        if meta.author and meta.author not in opf:
            issues.append(f"OPF 中缺少作者：{meta.author}")
        if meta.description and meta.description[:20] not in opf:
            issues.append("OPF 中缺少书籍简介")
        if meta.tags and meta.tags[0] not in opf:
            issues.append(f"OPF 中缺少标签：{meta.tags[0]}")

    # 样式：所有 CSS 合并后做关键参数比对
    css_text = ""
    for n in names:
        if n.endswith(".css"):
            try:
                css_text += zp.read(n).decode("utf-8", errors="replace") + "\n"
            except KeyError:
                pass
    mc = _minify(css_text)
    required_css = [
        ("text-indent:2em", "正文首行缩进"),
        ("line-height:130%", "正文行高"),
        ("margin-top:1em", "段前间距"),
        ("margin-bottom:1em", "段后间距"),
        ("text-align:justify", "文字两端对齐"),
        ('font-family:"pingfangsc"', "正文字体（苹方）"),
        ("@page", "页面规则"),
        ("margin-top:5pt", "页面上边距"),
        ("margin-bottom:5pt", "页面下边距"),
    ]
    for token, label in required_css:
        if token not in mc:
            issues.append(f"样式未对齐模板：缺少 {label}（{token}）")

    # 字体嵌入
    font_files = [n for n in names if n.lower().endswith((".ttf", ".otf"))]
    big_fonts = [n for n in font_files if (zp.getinfo(n).file_size or 0) > 1_000_000]
    if not big_fonts:
        issues.append("未嵌入字体文件（需要完整的苹方字体）")
    for font in template.fonts:
        css_name = font.sample_file or font.provided_file
        token = f"url(fonts/{css_name})".lower()
        if css_name and token not in mc:
            issues.append(f"CSS 未引用字体：{css_name}")

    # 封面尺寸
    m_cover = re.search(r'<meta name="cover" content="([^"]+)"', opf)
    cover_name = ""
    if m_cover:
        m_item = re.search(
            r'<item[^>]*id="' + re.escape(m_cover.group(1)) + r'"[^>]*href="([^"]+)"', opf
        )
        if m_item:
            cover_name = m_item.group(1)
    if not cover_name:
        for n in names:
            if n.lower().endswith(("cover.jpg", "cover.jpeg", "cover.png")):
                cover_name = n
                break
    if cover_name:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(zp.read(cover_name)))
            spec = template.cover
            if im.size != (spec.width, spec.height):
                issues.append(
                    f"封面尺寸不符合模板：{im.size[0]}x{im.size[1]} != {spec.width}x{spec.height}"
                )
            if im.format and im.format.lower() != spec.fmt.lower():
                issues.append(f"封面格式不符合模板：{im.format} != {spec.fmt}")
        except Exception as exc:
            issues.append(f"封面校验异常：{exc}")
    else:
        issues.append("EPUB 中未找到封面图片")

    # 目录
    ncx_name = find("toc.ncx")
    if ncx_name:
        ncx = zp.read(ncx_name).decode("utf-8", errors="replace")
        nav_section = re.search(r"<navMap>(.*?)</navMap>", ncx, re.S)
        nav_labels = re.findall(r"<text>(.*?)</text>", nav_section.group(1) if nav_section else "", re.S)
        labels = nav_labels
        labels = [re.sub(r"<[^>]+>", "", x).strip() for x in labels]
        fixed = {template.toc.get("cover_label", "封面"), template.toc.get("intro_label", "内容简介"), "简介"}
        chapter_labels = [x for x in labels if x and x not in fixed]
        if len(chapter_labels) < chapter_count:
            issues.append(f"目录章节数不足：{len(chapter_labels)} < {chapter_count}")
        intro_label = template.toc.get("intro_label", "简介")
        intro_heading = template.toc.get("intro_page_heading", "内容简介")
        if intro_label not in labels and intro_heading not in labels and not any(meta.title in x for x in labels):
            issues.append(f"目录缺少简介入口（{intro_label}）")
    else:
        issues.append("缺少 toc.ncx 目录文件")

    return sorted(set(issues))
