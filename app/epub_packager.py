# -*- coding: utf-8 -*-
"""直接打包模式：不依赖 calibre，按参考样例的 EPUB 结构原样生成（CSS/字体/封面/目录逐项对齐）。

仅在本地无 calibre 时使用；Docker 环境默认仍走 ebook-convert。
"""

from __future__ import annotations

import html
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, sanitize_filename
from app.meta_loader import Meta
from app.template import Template


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _esc_attr(text: str) -> str:
    return html.escape(text or "", quote=True)


def _page_head(title: str, lang: str, css_files: list) -> list:
    links = "\n".join(
        f'<link rel="stylesheet" type="text/css" href="{_esc_attr(f)}"/>' for f in css_files
    )
    return [
        "<?xml version='1.0' encoding='utf-8'?>",
        f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{_esc_attr(lang)}">',
        "<head>",
        f"<title>{_esc(title)}</title>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>',
        links,
        "</head>",
    ]


def _cover_page(meta: Meta, lang: str) -> str:
    parts = [
        "<?xml version='1.0' encoding='utf-8'?>",
        '<html xmlns="http://www.w3.org/1999/xhtml">',
        "  <head>",
        "    <title>Cover</title>",
        '    <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>',
        '  <link rel="stylesheet" type="text/css" href="../../../stylesheet.css"/>',
        '<link rel="stylesheet" type="text/css" href="../../../page_styles.css"/>',
        "</head>",
        '  <body class="calibre">',
        '<div class="pic"><img src="../Images/cover.jpg" class="calibre1"/></div>',
        f'<h1 class="calibre2">{_esc(meta.title)}</h1>',
    ]
    if meta.author:
        parts.append(
            f'<div class="author"><b class="calibre3">{_esc(meta.author)}</b> '
            f'<span class="calibre4">/ 著</span></div>'
        )
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


def _intro_page(meta: Meta, lang: str, heading: str) -> str:
    paras = []
    for p in meta.description_paragraphs():
        if not p.startswith(("　", "\t", " ")):
            p = "　　" + p
        paras.append(f'<p class="calibre6">{_esc(p)}</p>')
    body = f'<h1 class="head">{_esc(heading)}</h1>' + "".join(paras)
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN">\n'
        "  <head>\n"
        "    <title>Intro</title>\n"
        '    <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>\n'
        '  <link rel="stylesheet" type="text/css" href="../../../stylesheet.css"/>\n'
        '<link rel="stylesheet" type="text/css" href="../../../page_styles1.css"/>\n'
        "</head>\n"
        '  <body class="calibre5">\n'
        + body
        + "</body>\n"
        + "</html>\n"
    )


def _chapter_page(ch, idx: int, lang: str) -> str:
    parts = _page_head("Chapter", lang, ["../../../stylesheet.css", "../../../page_styles1.css"])
    parts += ['<body class="calibre5">']
    if ch.title:
        parts.append(
            f'<h2 class="head1"><span class="chapter-sequence-number">{_esc(ch.label)}</span>'
            f'<br class="calibre7"/>{_esc(ch.title)}</h2>'
        )
    else:
        parts.append(f'<h2 class="head1"><span class="chapter-sequence-number">{_esc(ch.label)}</span></h2>')
    # 与参考样例一致：标题下方再带一行“第N章 章节名”正文，
    # 保证任何阅读器里每章开头都能看到章节名。
    heading_text = f"{ch.label} {ch.title}".strip()
    if heading_text:
        parts.append(f'<p class="calibre6">{_esc(heading_text)}</p>')
    for p in ch.paragraphs:
        parts.append(f'<p class="calibre6">{_esc(p)}</p>')
    parts.append("</body></html>")
    return "\n".join(parts)


def _titlepage_xhtml(cover_w: int, cover_h: int, cover_ref: str) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">\n'
        "    <head>\n"
        '        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8"/>\n'
        '        <meta name="calibre:cover" content="true"/>\n'
        "        <title>Cover</title>\n"
        '        <style type="text/css" title="override_css">\n'
        "            @page {padding: 0pt; margin:0pt}\n"
        "            body { text-align: center; padding:0pt; margin: 0pt; }\n"
        "        </style>\n"
        "    </head>\n"
        "    <body>\n"
        "        <div>\n"
        f'            <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'version="1.1" width="100%" height="100%" viewBox="0 0 {cover_w} {cover_h}" '
        'preserveAspectRatio="none">\n'
        f'                <image width="{cover_w}" height="{cover_h}" xlink:href="{_esc_attr(cover_ref)}"/>\n'
        "            </svg>\n"
        "        </div>\n"
        "    </body>\n"
        "</html>\n"
    )


def _container_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n"
        "</container>\n"
    )


def _opf(meta: Meta, chapters: list, uuid_id: str, timestamp: str, font_files: list, top_dir: str) -> str:
    manifest = []
    spine = []
    manifest.append('<item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>')
    manifest.append(f'<item id="cover" href="{top_dir}/OEBPS/Images/cover.jpg" media-type="image/jpeg"/>')
    manifest.append('<item id="page_css" href="page_styles.css" media-type="text/css"/>')
    manifest.append('<item id="page_css1" href="page_styles1.css" media-type="text/css"/>')
    manifest.append('<item id="css" href="stylesheet.css" media-type="text/css"/>')
    manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

    order = ["titlepage", "coverpage", "intro"]
    manifest.append(f'<item id="coverpage" href="{top_dir}/OEBPS/Text/cover.html" media-type="application/xhtml+xml"/>')
    manifest.append(f'<item id="intro" href="{top_dir}/OEBPS/Text/intro.html" media-type="application/xhtml+xml"/>')
    for i, ch in enumerate(chapters):
        rid = f"chapter_{i}"
        manifest.append(
            f'<item id="{rid}" href="{top_dir}/OEBPS/Text/chapter_{i:03d}.html" media-type="application/xhtml+xml"/>'
        )
        order.append(rid)

    for i, fname in enumerate(font_files):
        manifest.append(
            f'<item id="font_{i}" href="fonts/{_esc_attr(fname)}" media-type="application/vnd.ms-opentype"/>'
        )

    spine = "".join(f'<itemref idref="{rid}"/>' for rid in order)

    desc_html = ""
    paras = [html.escape(p) for p in meta.description_paragraphs()]
    if paras:
        desc_html = "<div>" + "".join(f"<p>{p}</p>" for p in paras) + "<p> </p></div>"

    subjects = "".join(f"<dc:subject>{_esc(s)}</dc:subject>" for s in meta.tags)
    series_meta = ""
    if meta.series:
        series_meta += (
            f'<meta name="calibre:series" content="{_esc_attr(meta.series)}"/>\n'
            f'<meta name="calibre:series_index" content="{meta.series_index}"/>\n'
        )

    return f"""<?xml version="1.0"  encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uuid_id">
  <metadata xmlns:opf="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:calibre="http://calibre.kovidgoyal.net/2009/metadata">
    <dc:title>{_esc(meta.title)}</dc:title>
    <dc:creator opf:role="aut" opf:file-as="{_esc_attr(meta.author or '佚名')}">{_esc(meta.author or '佚名')}</dc:creator>
    <dc:contributor opf:role="bkp">epub-converter (direct pack)</dc:contributor>
    <dc:date>{timestamp}</dc:date>
    <dc:identifier id="uuid_id" opf:scheme="uuid">{uuid_id}</dc:identifier>
    {("<dc:publisher>" + _esc(meta.publisher) + "</dc:publisher>") if meta.publisher else ""}
    {("<dc:description>" + desc_html + "</dc:description>") if desc_html else ""}
    {subjects}
    <dc:language>{_esc(meta.lang or "zh")}</dc:language>
    <meta name="cover" content="cover"/>
    {series_meta}
  </metadata>
  <manifest>
{chr(10).join("    " + m for m in manifest)}
  </manifest>
  <spine toc="ncx">
    {spine}
  </spine>
  <guide>
    <reference type="cover" href="titlepage.xhtml" title="Cover"/>
  </guide>
</package>
"""
def _ncx(meta: Meta, chapters: list, uuid_id: str, intro_label: str, top_dir: str) -> str:
    labels = [("封面", f"{top_dir}/OEBPS/Text/cover.html"), (intro_label, f"{top_dir}/OEBPS/Text/intro.html")]
    labels += [(f"{ch.label} {ch.title}".strip(), f"{top_dir}/OEBPS/Text/chapter_{i:03d}.html")
               for i, ch in enumerate(chapters)]
    nav = "".join(
        f'<navPoint id="navPoint-{i + 1}" playOrder="{i + 1}" class="chapter">'
        f"<navLabel><text>{_esc(label)}</text></navLabel>"
        f'<content src="{_esc_attr(href)}"/></navPoint>'
        for i, (label, href) in enumerate(labels)
    )
    return f"""<?xml version='1.0' encoding='utf-8'?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zho">
  <head>
    <meta name="dtb:uid" content="{uuid_id}"/>
    <meta name="dtb:depth" content="2"/>
    <meta name="dtb:generator" content="epub-converter"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{_esc(meta.title)}</text></docTitle>
  <navMap>
    {nav}
  </navMap>
</ncx>
"""


def pack_epub(
    meta: Meta,
    chapters: list,
    template: Template,
    settings: Settings,
    work: Path,
    out_path: Path,
) -> Path:
    """按样例结构直接打包 EPUB（mimetype 必须首个且不压缩）。"""
    uuid_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    cover_w, cover_h = template.cover.width, template.cover.height
    lang = meta.lang or "zh"
    font_files = sorted({(f.sample_file or f.provided_file) for f in template.fonts})
    top_dir = f"{sanitize_filename(meta.title or 'book', 60)}.epub"
    cover_ref = f"{top_dir}/OEBPS/Images/cover.jpg"
    text_root = f"{top_dir}/OEBPS/Text"
    img_root = f"{top_dir}/OEBPS/Images"

    text_dir = work / "text_parts"
    if text_dir.exists():
        shutil.rmtree(text_dir)
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "cover.html").write_text(_cover_page(meta, lang), encoding="utf-8")
    (text_dir / "intro.html").write_text(
        _intro_page(meta, lang, template.toc.get("intro_page_heading", "内容简介")), encoding="utf-8"
    )
    for i, ch in enumerate(chapters):
        (text_dir / f"chapter_{i:03d}.html").write_text(_chapter_page(ch, i, lang), encoding="utf-8")

    with zipfile.ZipFile(out_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", _container_xml())
        zf.writestr("titlepage.xhtml", _titlepage_xhtml(cover_w, cover_h, cover_ref))
        zf.writestr("toc.ncx", _ncx(meta, chapters, uuid_id, template.toc.get("intro_label", "简介"), top_dir))
        zf.writestr("content.opf", _opf(meta, chapters, uuid_id, timestamp, font_files, top_dir))
        for css_name in template.css_files:
            zf.write(work / css_name, css_name)
        for font in template.fonts:
            css_name = font.sample_file or font.provided_file
            if css_name and (work / "fonts" / css_name).is_file():
                zf.write(work / "fonts" / css_name, f"fonts/{css_name}")
        zf.write(work / "cover.jpg", f"{img_root}/cover.jpg")
        for p in sorted((text_dir).glob("*.html")):
            zf.write(p, f"{text_root}/{p.name}")
    return out_path
