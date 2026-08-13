#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模板提取工具：从参考样例 EPUB 中提取并固化统一样式模板。

用法：
    python tools/extract_template.py <参考样例.epub> --out <模板目录> [--fonts-dir <字体目录>]

产物（写入 --out）：
    template.yml        固化参数：封面规格、字体映射、排版参数、目录规则、清洗规则
    stylesheet.css      样例原版样式（逐字复制，禁止改动）
    page_styles.css     样例原版页面样式
    page_styles1.css    样例原版字体回退样式（含 @font-face）
    sample_cover.jpg    样例封面原图（用于人工比对）
    references/         封面页/简介页/章节页/目录 样例文件（人工参考）
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import Image
except Exception:  # pragma: no cover - 容器内必装，本机缺失时降级
    Image = None

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


def fix_zip_name(name: str) -> str:
    """zipfile 对非 UTF-8 文件名默认按 cp437 解码，真实名称通常是 GBK。"""
    try:
        return name.encode("cp437").decode("gbk")
    except Exception:
        return name


def find_entry(zp: zipfile.ZipFile, suffix: str) -> str:
    for n in zp.namelist():
        if n.endswith(suffix):
            return n
    raise KeyError(f"EPUB 内找不到 {suffix}")


def read_bytes(zp: zipfile.ZipFile, suffix: str) -> bytes:
    return zp.read(find_entry(zp, suffix))


def read_text(zp: zipfile.ZipFile, suffix: str) -> str:
    raw = read_bytes(zp, suffix)
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_block(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    return m.group(1) if m else ""


def block_values(block: str) -> dict:
    out = {}
    for key, val in re.findall(r"([a-zA-Z-]+)\s*:\s*([^;}]+)", block):
        out[key.strip().lower()] = val.strip()
    return out


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def estimate_jpeg_quality(img_path: Path) -> int:
    """用 Pillow 把样例封面按不同质量重编码，挑出体积最接近原图的档位。"""
    if Image is None:
        return 90
    im = Image.open(img_path)
    target = img_path.stat().st_size
    best_q, best_diff = 90, float("inf")
    for q in range(70, 99):
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=q, optimize=True)
        diff = abs(buf.tell() - target)
        if diff < best_diff:
            best_q, best_diff = q, diff
    return best_q


def dump_yaml(data, path: Path) -> None:
    if yaml is not None:
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_template(sample_epub: Path, out_dir: Path, fonts_dir: Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    ref_dir = out_dir / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)

    zp = zipfile.ZipFile(sample_epub)
    names = [fix_zip_name(n) for n in zp.namelist()]

    # 1) 原版样式表，逐字复制
    css_files = ["stylesheet.css", "page_styles.css", "page_styles1.css"]
    for f in css_files:
        (out_dir / f).write_bytes(read_bytes(zp, f))

    # 2) 封面原图
    try:
        cover_src = find_entry(zp, "Images/cover.jpg")
    except KeyError:
        cover_src = find_entry(zp, "Images/cover.jpeg")
    cover_bytes = zp.read(cover_src)
    cover_path = out_dir / "sample_cover.jpg"
    cover_path.write_bytes(cover_bytes)

    # 3) 人工参考文件
    for ref in ("titlepage.xhtml", "cover.html", "intro.html", "chapter_0.html", "toc.ncx", "content.opf"):
        try:
            data = read_bytes(zp, ref)
            (ref_dir / ref).write_bytes(data)
        except KeyError:
            pass

    # 4) OPF 元信息
    opf = read_text(zp, "content.opf")
    def tag(name: str):
        m = re.search(r"<dc:" + name + r"[^>]*>(.*?)</dc:" + name + ">", opf, re.S)
        return m.group(1).strip() if m else ""
    meta = {
        "title": tag("title"),
        "author": tag("creator"),
        "publisher": tag("publisher"),
        "language": tag("language"),
        "date": tag("date"),
        "tags": [t.strip() for t in re.findall(r"<dc:subject[^>]*>(.*?)</dc:subject>", opf, re.S)],
        "description": strip_tags(tag("description")),
    }
    m_cal = re.search(r"calibre \(([\d.]+)\)", opf)
    calibre_version = m_cal.group(1) if m_cal else "unknown"

    # 5) 封面尺寸：titlepage 的 svg viewBox / image 属性
    titlepage = read_text(zp, "titlepage.xhtml")
    m_vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', titlepage)
    m_img = re.search(r'<image[^>]*width="(\d+)"[^>]*height="(\d+)"', titlepage)
    if m_vb:
        cw, ch = int(m_vb.group(1)), int(m_vb.group(2))
    elif m_img:
        cw, ch = int(m_img.group(1)), int(m_img.group(2))
    else:
        im = Image.open(io.BytesIO(cover_bytes))
        cw, ch = im.size

    # 6) 排版参数（从原版 CSS 中解析，仅用于固化清单与后续校验）
    css = (out_dir / "stylesheet.css").read_text(encoding="utf-8", errors="replace")
    css1 = (out_dir / "page_styles1.css").read_text(encoding="utf-8", errors="replace")

    def vals(sel, source=None):
        return block_values(parse_block(source if source is not None else css, sel))

    page_block = parse_block(css1, "@page") or parse_block(css, "@page")
    page_vals = block_values(page_block)

    # @font-face 块：family/weight/src
    font_faces = []
    for block in re.findall(r"@font-face\s*\{(.*?)\}", css1 + "\n" + css, re.S):
        bv = block_values(block)
        srcs = re.findall(r"url\(([^)]+)\)", block)
        if "font-family" in bv:
            font_faces.append({
                "family": bv["font-family"].strip('"'),
                "weight": bv.get("font-weight", "normal"),
                "src": srcs,
            })
    # 判断哪些字体真正嵌入（url 指向 EPUB 内存在的文件）
    embedded = []
    for ff in font_faces:
        for src in ff["src"]:
            src_path = src.split("?")[0]
            try:
                if src_path in names or any(n.endswith("/" + src_path) for n in names):
                    embedded.append(ff)
                    break
            except Exception:
                continue

    # 7) 提供字体与样例字体文件名对应（在 /fonts 中按名称近似匹配）
    font_mapping = []
    if fonts_dir is not None and fonts_dir.is_dir():
        provided = sorted(p.name for p in fonts_dir.glob("*.ttf")) + sorted(
            p.name for p in fonts_dir.glob("*.otf")
        )
        for ff in embedded:
            sample_font = Path(ff["src"][0]).name if ff["src"] else ""
            match = None
            if sample_font:
                if sample_font in provided:
                    match = sample_font
                else:
                    base = re.sub(r"[-\s]", "", sample_font.lower()).replace(".otf", "")
                    for cand in provided:
                        cand_base = re.sub(r"[-\s]", "", cand.lower()).rsplit(".", 1)[0]
                        if base == cand_base or sample_font.lower() == cand.lower():
                            match = cand
                            break
            font_mapping.append({
                "family": ff["family"],
                "weight": ff["weight"],
                "sample_file": sample_font,
                "provided_file": match or "",
            })

    template = {
        "template_name": f"参考样例模板（{sample_epub.stem}）",
        "source_epub": sample_epub.name,
        "extracted_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "calibre_version": calibre_version,
        "cover": {
            "width": cw,
            "height": ch,
            "format": "jpeg",
            "quality": estimate_jpeg_quality(cover_path),
            "color_mode": "RGB",
        },
        "fonts": {
            "embedded": font_mapping,
            "css_files": css_files,
        },
        "typography": {
            "page_margins": {
                "top": page_vals.get("margin-top", "5pt"),
                "bottom": page_vals.get("margin-bottom", "5pt"),
            },
            "cover_page": {
                "class": "calibre",
                "title_class": "calibre2",
                "author_class": "author",
                "values": vals(".calibre"),
            },
            "body": {
                "class": "calibre5",
                "values": vals(".calibre5"),
            },
            "paragraph": {
                "class": "calibre6",
                "values": vals(".calibre6"),
            },
            "chapter_heading": {
                "class": "head1",
                "values": vals(".head1"),
            },
            "intro_heading": {
                "class": "head",
                "values": vals(".head"),
            },
            "chapter_badge": {
                "class": "chapter-sequence-number",
                "values": vals(".chapter-sequence-number"),
            },
        },
        "metadata_page": {
            "heading": "内容简介",
            "heading_class": "head",
            "paragraph_class": "calibre6",
        },
        "toc": {
            "cover_label": "封面",
            "intro_label": "简介",
            "intro_page_heading": "内容简介",
            "heading_xpath": "//h:h1 | //h:h2",
            "chapter_xpath": "//h:h2",
        },
        "sample_metadata": meta,
        "text_cleaning": {
            "max_blank_lines": 1,
            "merge_wrapped_lines": True,
            "normalize_chapter_numbers": True,
            "junk_patterns": [
                r"^\s*[（(【\[]?(本章未完|下一章|最新章节|请记住(本站|本书)|手机用户|最快更新|无广告|全文阅读|笔趣阁|顶点小说|酷匠网|新笔趣阁)[^。！？]{0,40}[）)】\]]?\s*$",
                r"^https?://\S+$",
                r"^www\.\S+$",
                r"^\s*[\[\(【]?(本章完|全文完)[\]\)】]?\s*$",
            ],
            "chapter_patterns": [
                r"^\s*第\s*[0-9零一二三四五六七八九十百千两]+\s*[章回节卷部篇集]",
                r"^\s*(序章|楔子|引子|前言|尾声|后记|番外|外传|终章|终曲)",
                r"^\s*(Chapter|CHAPTER|chapter)\s+\d+",
            ],
            "fallback_chapter_title": "正文",
        },
    }

    dump_yaml(template, out_dir / "template.yml")
    return template


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从参考样例EPUB提取固化样式模板")
    ap.add_argument("sample_epub", type=Path, help="参考样例EPUB路径")
    ap.add_argument("--out", type=Path, default=Path("template"), help="模板输出目录")
    ap.add_argument("--fonts-dir", type=Path, default=None, help="/fonts 字体目录（用于建立字体映射）")
    args = ap.parse_args(argv)

    if not args.sample_epub.is_file():
        print(f"[ERROR] 找不到样例EPUB：{args.sample_epub}", file=sys.stderr)
        return 1
    try:
        tpl = extract_template(args.sample_epub, args.out, args.fonts_dir)
    except Exception as exc:
        print(f"[ERROR] 模板提取失败：{exc}", file=sys.stderr)
        return 1
    print(f"[OK] 模板已生成到 {args.out}/")
    print(f"     封面规格：{tpl['cover']['width']}x{tpl['cover']['height']}，质量 {tpl['cover']['quality']}")
    print(f"     嵌入字体：{', '.join(f['family'] for f in tpl['fonts']['embedded']) or '无'}")
    print(f"     样例元信息：{tpl['sample_metadata']['title']} - {tpl['sample_metadata']['author']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
