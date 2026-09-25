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
    check("刮削源注册", all(s in SOURCES for s in ("qidian", "weread", "douban", "baike", "dangdang"))
          and SOURCE_LABELS.get("qidian") == "起点中文网"
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

    print("== 10. 起点中文网解析（离线） ==")
    import json as _json
    from app.scraper import _qidian_cover_url, _qidian_records
    raw_cover = "//bookcover.yuewen.com/qdbimg/349573/1209977/180"
    check("封面补协议并升级 600px",
          _qidian_cover_url(raw_cover) == "https://bookcover.yuewen.com/qdbimg/349573/1209977/600",
          _qidian_cover_url(raw_cover))
    check("已是 600px 保持不变",
          _qidian_cover_url("https://bookcover.yuewen.com/qdbimg/349573/1209977/600").endswith("/600"))
    payload = {"pageContext": {"pageProps": {"pageData": {"bookInfo": {"records": [
        {"bName": "斗破苍穹", "bAuth": "天蚕土豆", "desc": "这里是属于斗气的世界",
         "bid": 1209977, "imgUrl": raw_cover,
         "cat": "玄幻", "subCateName": "异世大陆", "cnt": "533.23万字", "state": "完结"},
    ]}}}}}
    html = ('<html><script id="vite-plugin-ssr_pageContext" type="application/json">'
            + _json.dumps(payload, ensure_ascii=False) + "</script></html>")
    recs = _qidian_records(html)
    check("SSR 内嵌记录解析", len(recs) == 1 and recs[0]["bName"] == "斗破苍穹", str(recs))
    check("无 SSR 数据时安全返回", _qidian_records("<html></html>") == [])

    print("== 11. 书名清洗与文件名解析 ==")
    from app.scraper import clean_search_title, guess_from_name
    check("去 txt 下载噪声", clean_search_title("斗破苍穹txt下载") == "斗破苍穹",
          clean_search_title("斗破苍穹txt下载"))
    check("去完结后缀与书名号", clean_search_title("《诡秘之主》全集") == "诡秘之主",
          clean_search_title("《诡秘之主》全集"))
    check("去尾部作者（空格）", clean_search_title("全职高手 作者：蝴蝶蓝") == "全职高手",
          clean_search_title("全职高手 作者：蝴蝶蓝"))
    check("作者在前时取书名", clean_search_title("作者：天蚕土豆-斗破苍穹") == "斗破苍穹",
          clean_search_title("作者：天蚕土豆-斗破苍穹"))
    check("正常书名不被误伤", clean_search_title("诡秘之主 第二部") == "诡秘之主 第二部",
          clean_search_title("诡秘之主 第二部"))
    check("叠加噪声后缀反复剥离", clean_search_title("斗破苍穹全集txt下载") == "斗破苍穹",
          clean_search_title("斗破苍穹全集txt下载"))
    check("文件名 书名-作者", guess_from_name("杀神-逆苍天") == ("杀神", "逆苍天"),
          str(guess_from_name("杀神-逆苍天")))
    check("文件名 书名（作者）", guess_from_name("大奉打更人（卖报小郎君）") == ("大奉打更人", "卖报小郎君"),
          str(guess_from_name("大奉打更人（卖报小郎君）")))
    check("文件名 站名+书名+作者", guess_from_name("[玄幻] 诡秘之主 爱潜水的乌贼") == ("诡秘之主", "爱潜水的乌贼"),
          str(guess_from_name("[玄幻] 诡秘之主 爱潜水的乌贼")))
    check("文件名 作者在前", guess_from_name("作者：天蚕土豆-斗破苍穹") == ("斗破苍穹", "天蚕土豆"),
          str(guess_from_name("作者：天蚕土豆-斗破苍穹")))
    check("无意义文件名视为空", guess_from_name("新建文本文档") == ("", ""),
          str(guess_from_name("新建文本文档")))

    from app.scraper import guess_from_txt
    probe = settings.work_dir / "[玄幻] 诡秘之主 爱潜水的乌贼.txt"
    probe.write_text("【玄幻】诡秘之主 爱潜水的乌贼 全集下载\n\n第1章 序\n　　正文内容。\n",
                     encoding="utf-8")
    check("正文+文件名联合猜测", guess_from_txt(probe) == ("诡秘之主", "爱潜水的乌贼"),
          str(guess_from_txt(probe)))

    print("== 12. 自定义封面分辨率 ==")
    from app.template import CoverSpec, effective_cover
    from app.cover_processor import process_cover
    conf = Settings.from_env()
    conf.cover_width, conf.cover_height = 500, 700
    spec = effective_cover(template, conf)
    check("自定义规格生效", (spec.width, spec.height) == (500, 700), f"{spec.width}x{spec.height}")
    check("质量沿用模板", spec.quality == template.cover.quality, str(spec.quality))
    square = settings.work_dir / "square_upload.png"
    Image.new("RGB", (1000, 1000), (200, 30, 30)).save(square)
    custom = process_cover(square, settings.work_dir / "custom_cover.jpg", spec)
    with Image.open(custom) as im:
        check("方图按项目分辨率裁切", im.size == (500, 700), str(im.size))
        check("输出仍为 JPEG", im.format == "JPEG", str(im.format))
    check("未设置时回到模板默认",
          (effective_cover(template, Settings.from_env()).width, ) == (template.cover.width, ))
    conf.cover_width = conf.cover_height = 0

    print("== 13. 刮削源解析健壮性（离线） ==")
    from app.scraper import (
        _DANGDANG_NO_RESULT_RE, _baike_fields, _cover_headers, _cover_variants,
    )
    # 百度百科作者字段的 key 会随词条类型变化，不能写死 m27_author（原来就是这么错的）
    author, extra = _baike_fields([
        {"key": "m151_name", "name": "中文名", "value": ["十方天士"]},
        {"key": "m151_author", "name": "作者", "value": ["逆苍天"]},
        {"key": "m151_type", "name": "作品类型", "value": ["玄幻·东方玄幻"]},
        {"key": "m151_state", "name": "连载状态", "value": ["已完结"]},
    ])
    check("百科作者按 key 后缀识别", author == "逆苍天", repr(author))
    check("百科作品信息", "玄幻" in extra and "已完结" in extra, repr(extra))
    check("百科旧 key 仍兼容",
          _baike_fields([{"key": "m27_author", "name": "作者", "value": ["某作者"]}])[0] == "某作者")
    check("百科没有作者时为空",
          _baike_fields([{"key": "m151_x", "name": "其他", "value": ["v"]}])[0] == "")

    # 当当：老解析依赖 <li class="line1">，整页只剩 1 个节点，会漏掉其余几十条
    check("当当无结果可识别",
          bool(_DANGDANG_NO_RESULT_RE.search('<h1 class="search_msg">抱歉，没有找到与"x"相关的商品！</h1>')))
    check("当当有结果不误判",
          not _DANGDANG_NO_RESULT_RE.search("<html><li id='p123'>活着</li></html>"))

    # 封面：防盗链 Referer + 尺寸升级（浏览器直连豆瓣/百科一律 403，必须走代理）
    check("豆瓣封面带对的 Referer",
          "book.douban.com" in _cover_headers("https://img3.doubanio.com/x.jpg").get("Referer", ""))
    check("百科封面带对的 Referer",
          "baike.baidu.com" in _cover_headers("https://bkimg.cdn.bcebos.com/pic/x").get("Referer", ""))
    check("微信读书封面优先高清变体",
          _cover_variants("https://x.com/cover/a/s_123.jpg")[0].endswith("/o_123.jpg"),
          str(_cover_variants("https://x.com/cover/a/s_123.jpg")[:2]))
    check("起点封面优先 600px",
          _cover_variants("https://bookcover.yuewen.com/qdbimg/349573/1/180")[0].endswith("/600"))
    check("官方 API 源已注册",
          "googlebooks" in SOURCES and "openlibrary" in SOURCES, str(SOURCES))

    print("== 14. 自定义源与刮削缓存（离线） ==")
    import json as _json2
    import tempfile as _tempfile
    import threading as _threading
    from http.server import BaseHTTPRequestHandler as _BaseHandler
    from http.server import HTTPServer as _HTTPServer
    from app.scraper import (
        _cache_get, _cache_put, _json_path, _make_custom_search,
        clear_scrape_cache, configure_scrape_cache, list_sources,
    )

    check("JSON 路径取值", _json_path({"a": {"b": [0, {"c": 7}]}}, "a.b.1.c") == 7)
    check("JSON 路径容错", _json_path({"a": 1}, "a.b.c") is None and _json_path({"a": 1}, "") == {"a": 1})

    class _MockHandler(_BaseHandler):
        def do_GET(self):
            if "html" in self.path:      # 用于验证「JSON 源拿到 HTML」的错误分级
                body = b"<html><body>not json</body></html>"
                ctype = "text/html; charset=utf-8"
            else:
                body = _json2.dumps({"data": {"list": [
                    {"name": "自定义源测试书", "author": "张三", "desc": "简介", "score": 8.8},
                ]}}, ensure_ascii=False).encode()
                ctype = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = _HTTPServer(("127.0.0.1", 0), _MockHandler)
    _threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        custom = _make_custom_search({
            "id": "mockapi", "label": "测试源",
            "url": "http://127.0.0.1:%d/search?q={keyword}" % port,
            "format": "json", "items": "data.list",
            "fields": {"title": "name", "author": "author", "intro": "desc", "rating": "score"},
        })
        items = custom("任意关键词")
        check("自定义源字段映射",
              len(items) == 1 and items[0]["title"] == "自定义源测试书" and items[0]["rating"] == 8.8,
              str(items))

        # JSON 源拿到 HTML 时应判为「疑似改版」而不是笼统报错（必须在关服务前测）
        from app.scraper_sources import SourceBrokenError
        bad = _make_custom_search({"id": "badjson", "label": "坏源",
                                   "url": "http://127.0.0.1:%d/html?q={keyword}" % port,
                                   "format": "json", "fields": {"title": "name"}})
        try:
            bad("x")
            check("JSON 源拿到 HTML 判为疑似改版", False, "没有抛异常")
        except SourceBrokenError as exc:
            check("JSON 源拿到 HTML 判为疑似改版", "JSON" in str(exc), str(exc))
    finally:
        server.shutdown()

    check("自定义源不进内置源列表",
          "mockapi" not in [x["id"] for x in list_sources()])

    configure_scrape_cache(settings.work_dir / "scrape_cache_test")
    clear_scrape_cache()
    _cache_put("mock", "某个关键词", [{"title": "缓存书"}])
    check("刮削缓存读写", _cache_get("mock", "某个关键词") == [{"title": "缓存书"}],
          str(_cache_get("mock", "某个关键词")))
    check("缓存未命中返回 None", _cache_get("mock", "没写过的关键词") is None)
    check("缓存可清空", clear_scrape_cache() >= 1 and _cache_get("mock", "某个关键词") is None)

    # 自定义源的封面域名要能加进封面代理白名单
    from app.scraper import COVER_HOST_SUFFIXES, _build_registry, cover_host_suffixes
    check("封面白名单可扩展", len(cover_host_suffixes()) >= len(COVER_HOST_SUFFIXES),
          str(len(cover_host_suffixes())))
    registry = _build_registry({"settings": {}, "sources": {}, "custom": [
        {"id": "qidian", "label": "冒牌起点", "url": "https://x/y", "fields": {"title": "t"}},
        {"id": "brand_new", "label": "新源", "url": "https://x/y", "fields": {"title": "t"}},
        {"id": "", "label": "无 id", "url": "https://x/y", "fields": {"title": "t"}},
    ]})
    ids = [rt.spec.id for rt in registry]
    check("与内置源重名的自定义源被跳过", ids.count("qidian") == 1 and "brand_new" in ids, str(ids))


    print("== 15. 多路兜底解析（离线，模拟接口改版） ==")
    from app.scraper_sources import (
        _dangdang_block_to_candidate, _douban_from_html, _douban_from_data,
        _qidian_records_from_html, _qidian_records_from_ssr,
    )
    # 起点：SSR 的 script 标签没了，但字段还在页面里 → 兜底路径要能捞出来
    broken_ssr = ('<html><body><script>var ctx={"bookInfo":{"records":['
                  '{"bName":"斗破苍穹","bAuth":"天蚕土豆","desc":"斗气世界","bid":1209977,'
                  '"imgUrl":"//bookcover.yuewen.com/qdbimg/349573/1209977/180",'
                  '"cat":"玄幻","cnt":"533万字","state":"完结"}]}}</script></body></html>')
    check("路径1：无 SSR 标签时返回空", _qidian_records_from_ssr(broken_ssr) == [])
    fallback = _qidian_records_from_html(broken_ssr)
    check("路径2：字段级正则兜底捞到记录",
          len(fallback) == 1 and fallback[0]["bName"] == "斗破苍穹"
          and fallback[0].get("bAuth") == "天蚕土豆" and fallback[0].get("bid") == 1209977,
          str(fallback))

    # 豆瓣：__DATA__ 变量改名/消失 → 退回解析搜索结果节点
    broken_data = ('<div class="item-root"><a href="https://book.douban.com/subject/1082154/" '
                   'class="cover-link"><img src="https://img1.doubanio.com/view/subject/m/public/s1078958.jpg">'
                   '</a><div class="title-text">活着</div></div>')
    check("路径1：无 __DATA__ 返回 None", _douban_from_data(broken_data) is None)
    db = _douban_from_html(broken_data)
    check("路径2：HTML 节点兜底",
          len(db) == 1 and db[0]["title"] == "活着" and "/l/public/" in db[0]["cover"],
          str(db))

    # 当当：标题里堆了推广语、作者带"著/出品" → 清理后应只剩书名和姓名
    dd = _dangdang_block_to_candidate(
        '<p class="name" name="title"><a name="itemlist-title">活着（余华代表作，精装） 余华代表作，正版</a></p>'
        '<p class="detail">★ 描述文字</p>'
        "<span><a name='itemlist-author' title='余华 著 ，新经典 出品'>余华</a></span>"
        "<a name='P_cbs' title='北京十月文艺出版社'>出版社</a>"
        "<img src='//img3m3.ddimg.cn/23/25/29311943-1_b_1.jpg'>")
    check("当当书名清理", dd and dd["title"] == "活着", str(dd and dd["title"]))
    check("当当作者清理", dd and dd["author"] == "余华", str(dd and dd["author"]))
    check("当当出版社/封面/简介",
          dd and dd["publisher"] == "北京十月文艺出版社" and dd["cover"].startswith("https://img")
          and dd["intro"] == "★ 描述文字", str(dd))

    print()
    if FAILED:
        print(f"共 {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("全部测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
