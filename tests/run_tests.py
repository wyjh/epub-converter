#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地测试：编码检测、清洗、元信息、章节切分、封面处理、HTML 生成、转换命令（DRY-RUN）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.make_sample_inputs import make_inputs

FAILED = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILED.append(name)


def main() -> int:
    print("== 准备测试输入 ==")
    make_inputs(ROOT / "tests")

    os.environ["INPUT_DIR"] = str(ROOT / "tests/input")
    os.environ["META_DIR"] = str(ROOT / "tests/input")
    os.environ["FONTS_DIR"] = str(ROOT / "fonts")
    os.environ["OUTPUT_DIR"] = str(ROOT / "tests/output")
    os.environ["TEMPLATE_DIR"] = str(ROOT / "template")
    os.environ["WORK_DIR"] = str(ROOT / "tests/work")
    os.environ["LOGS_DIR"] = str(ROOT / "tests/output/logs")
    os.environ["DRY_RUN"] = "1"

    from app.config import Settings
    from app.template import load_template
    settings = Settings.from_env()
    settings.ensure_dirs()
    template = load_template(settings.template_dir)
    print(f"  模板：{template.data['template_name']}")
    print(f"  封面规格：{template.cover.width}x{template.cover.height} q{template.cover.quality}")

    print("== 1. 编码检测 ==")
    from app.txt_reader import read_text_with_encoding, clean_lines
    t1, e1 = read_text_with_encoding(settings.input_dir / "样例书.txt")
    t2, e2 = read_text_with_encoding(settings.input_dir / "GBK样例.txt")
    check("UTF-8 识别", e1 in ("utf-8", "utf-8-sig"), e1)
    check("GBK 识别", e2 in ("gb18030", "gbk"), e2)

    print("== 2. 文本清洗 ==")
    lines = clean_lines(t1, template.cleaning)
    joined = "\n".join(lines)
    check("广告行已删除", "https://www.example.com/ads" not in joined)
    check("连载提示已删除", "本章未完" not in joined)
    check("冗余空行已压缩", "\n\n\n" not in "\n" + joined + "\n")
    check("硬换行已合并", "结尾一句没有句号就换行了这是被硬换行拆开的后半句" in joined)
    check("全角缩进保留", any(l.startswith("　　") for l in lines))

    print("== 3. 元信息读取 ==")
    from app.meta_loader import load_meta
    meta, src = load_meta(settings.input_dir / "样例书.txt", settings.meta_dir)
    check("书名", meta.title == "测试之书", meta.title)
    check("作者", meta.author == "测试作者", meta.author)
    check("标签", meta.tags == ["测试", "科幻", "小说"], str(meta.tags))
    check("简介分段", len(meta.description_paragraphs()) == 2)

    print("== 4. 章节切分 ==")
    from app.chapterizer import split_chapters
    chapters = split_chapters(lines, template.chapter_patterns)
    labels = [c.label for c in chapters]
    check("章节数量", len(chapters) == 5, str(labels))
    check("中文数字规范化", "第1章" in labels and "第2章" in labels, str(labels))
    check("英文章节规范化", "第4章" in labels, str(labels))
    check("无编号章节保留", "尾声" in labels, str(labels))
    check("章节标题提取", chapters[0].title == "开篇", chapters[0].title)

    gbk_lines = clean_lines(t2, template.cleaning)
    gbk_chapters = split_chapters(gbk_lines, template.chapter_patterns)
    check("GBK 章节切分", [c.label for c in gbk_chapters] == ["序章", "第7章"],
          str([c.label for c in gbk_chapters]))

    print("== 5. 封面处理 ==")
    from app.cover_processor import find_cover_image, process_cover
    src_cover = find_cover_image(settings.input_dir / "样例书.txt", meta.cover, settings.input_dir)
    check("封面自动发现", src_cover is not None, str(src_cover))
    dst_cover = process_cover(src_cover, settings.work_dir / "cover_test.jpg", template.cover)
    from PIL import Image
    with Image.open(dst_cover) as im:
        check("封面尺寸=模板", im.size == (template.cover.width, template.cover.height), str(im.size))
        check("封面格式 JPEG", im.format == "JPEG", str(im.format))

    print("== 6. HTML 生成 ==")
    from app.html_builder import build_book_html
    html = build_book_html(meta, chapters, template.css_files,
                           intro_heading=template.toc.get("intro_page_heading", "内容简介"))
    check("封面区块", 'class="calibre2"' in html)
    check("简介区块", "内容简介" in html)
    check("章节徽标", 'class="chapter-sequence-number"' in html)
    check("章节名段落", '<p class="calibre6">第1章 开篇</p>' in html
          and '<p class="calibre6">第3章</p>' in html)
    check("正文 class", 'class="calibre6"' in html)
    check("CSS 引用", 'href="stylesheet.css"' in html and 'href="page_styles1.css"' in html)

    print("== 7. 转换流程（DRY-RUN） ==")
    from app.converter import convert_one
    res = convert_one(settings.input_dir / "样例书.txt", settings, template)
    check("转换状态 ok", res.status == "ok", res.message)
    check("输出命名《书名》-作者", res.output.name == "《测试之书》-测试作者.epub", res.output.name)
    check("章节数", res.chapters == 5, str(res.chapters))

    print("== 8. 字体映射 ==")
    from app.template import build_working_css
    work = settings.work_dir / "css_check"
    work.mkdir(exist_ok=True)
    build_working_css(template, settings.fonts_dir, work)
    css = (work / "page_styles1.css").read_text(encoding="utf-8")
    check("字体 src 与模板一致", "url(fonts/PingFang-SC-Light.otf)" in css)

    print("== 9. 刮削辅助（书名猜测/匹配） ==")
    from app.scraper import guess_title_author, pick_best, SOURCES, SOURCE_LABELS
    check("刮削源注册", all(s in SOURCES for s in ("weread", "douban", "baike", "dangdang"))
          and SOURCE_LABELS.get("baike") == "百度百科" and SOURCE_LABELS.get("dangdang") == "当当图书")
    g_title, g_author = guess_title_author(["《杀神》全集", "作者：逆苍天", "正文内容……"])
    check("刮削书名猜测", g_title == "杀神", repr(g_title))
    check("刮削作者猜测", g_author == "逆苍天", repr(g_author))

    cands = [
        {"title": "杀神", "author": "逆苍天"},
        {"title": "杀神1", "author": "逆苍天"},
    ]
    best = pick_best(cands, "杀神", "逆苍天")
    check("刮削精确匹配", best and best["title"] == "杀神", str(best))

    best2 = pick_best([{"title": "杀神传", "author": "其他作者"}], "杀神", "")
    check("刮削包含匹配", best2 is not None and best2["title"] == "杀神传", str(best2))

    best3 = pick_best([{"title": "星际争霸：拾荒者", "author": "乔迪·豪泽"}], "星际拾荒者", "演示作者")
    check("刮削低分拒绝", best3 is None, str(best3))

    print()
    if FAILED:
        print(f"共 {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("全部测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
