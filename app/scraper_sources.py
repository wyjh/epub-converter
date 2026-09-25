# -*- coding: utf-8 -*-
"""内置刮削源的解析实现。

设计要点：

- 每个源只负责「关键词 → 候选列表」，网络重试、结果缓存、请求节流、失败熔断
  统一由 app.scraper 处理；
- 同一个源尽量准备多条解析路径（结构化数据优先、HTML 兜底），
  主路径失效时自动走备用路径，延长源的可用寿命；
- 拿不到数据时明确区分原因：真的没这本书 / 疑似改版 / 疑似被风控，
  这样上层日志和「检测刮削源」才能给出有用的结论。

候选字段：title / author / intro / cover / publisher / rating / url / extra。
source 与 source_label 由上层统一补齐。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)
UA_BROWSER = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

QIDIAN_SEARCH_URL = "https://m.qidian.com/search"
WEREAD_SEARCH_URL = "https://weread.qq.com/web/search/global"
DOUBAN_SEARCH_URL = "https://search.douban.com/book/subject_search"
BAIKE_API_URL = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
DANGDANG_SEARCH_URL = "https://search.dangdang.com/"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
OPENLIBRARY_URL = "https://openlibrary.org/search.json"

# 出现这些特征说明是被反爬/风控拦了，而不是页面改版。
# 注意别放太宽泛的词（例如 "robot" 会命中正常的 <meta name="robots">），
# 误判会让整个源被熔断，代价比漏判大得多。
_BLOCK_HINTS = (
    "probe.js", "x-waf", "x-waf-captcha", "waf_captcha",
    "请输入验证码", "安全验证", "检测到有异常请求", "访问过于频繁",
    "sec.douban", "captcha/", "risk_verify",
)


class SourceBlockedError(Exception):
    """被反爬/风控拦截（403/418/429，或页面出现验证码特征）。"""


class SourceBrokenError(Exception):
    """接口有响应、但解析不出任何数据，通常意味着页面结构改版了。"""


def _looks_blocked(text: str) -> bool:
    low = (text or "")[:20000].lower()
    return any(hint.lower() in low for hint in _BLOCK_HINTS)


def _json_unescape(text: str) -> str:
    try:
        return json.loads('"' + text + '"')
    except Exception:
        return text.replace('\\"', '"').replace("\\/", "/")


def http_get(url: str, headers: dict, timeout: int = 15, encoding: Optional[str] = None) -> str:
    """发 GET 请求并返回文本；被风控时抛 SourceBlockedError。"""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset()
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 418, 429, 503):
            raise SourceBlockedError(f"HTTP {exc.code}（疑似被限流/风控）") from exc
        raise
    for enc in ([encoding] if encoding else []) + [charset, "utf-8", "gb18030"]:
        if not enc:
            continue
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        text = raw.decode("utf-8", "replace")
    return text


def _raise_parse_error(text: str, message: str) -> None:
    """解析不出数据时，再判断到底是"被风控"还是"页面改版"。"""
    if _looks_blocked(text):
        raise SourceBlockedError("页面出现风控/验证码特征")
    raise SourceBrokenError(message)


def http_get_json(url: str, headers: dict, timeout: int = 15, label: str = ""):
    text = http_get(url, headers, timeout)
    try:
        return json.loads(text)
    except ValueError:
        _raise_parse_error(text, f"{label or '接口'}返回的不是合法 JSON（接口可能已改版）")


# --------------------------------------------------------------------------
# 起点中文网：结构化 SSR 数据优先，字段级正则兜底
# --------------------------------------------------------------------------
_QIDIAN_SSR_RE = re.compile(
    r'<script id="vite-plugin-ssr_pageContext" type="application/json">'
)
_QIDIAN_FIELD_RES = {
    "bAuth": re.compile(r'"bAuth"\s*:\s*"((?:[^"\\]|\\.)*)"'),
    "imgUrl": re.compile(r'"imgUrl"\s*:\s*"((?:[^"\\]|\\.)*)"'),
    "desc": re.compile(r'"desc"\s*:\s*"((?:[^"\\]|\\.)*)"'),
    "cnt": re.compile(r'"cnt"\s*:\s*"((?:[^"\\]|\\.)*)"'),
    "state": re.compile(r'"state"\s*:\s*"((?:[^"\\]|\\.)*)"'),
    "cat": re.compile(r'"cat"\s*:\s*"((?:[^"\\]|\\.)*)"'),
    "subCateName": re.compile(r'"subCateName"\s*:\s*"((?:[^"\\]|\\.)*)"'),
}


def _qidian_cover_url(raw: str) -> str:
    """起点封面：补全协议并升级到 600px 大图。"""
    url = (raw or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://bookcover.yuewen.com" + url
    if "bookcover.yuewen.com" in url or "/qdbimg/" in url:
        url = re.sub(r"/\d{2,4}(?=$|[?#])", "/600", url)
    return url


def _qidian_extra(rec: dict) -> str:
    parts = []
    cat = (rec.get("cat") or "").strip()
    sub = (rec.get("subCateName") or "").strip()
    if cat:
        parts.append(cat)
    if sub and sub != cat:
        parts.append(sub)
    for key in ("cnt", "state"):
        val = str(rec.get(key) or "").strip()
        if val:
            parts.append(val)
    return " · ".join(parts)


def _qidian_records_from_ssr(html: str) -> list:
    """路径 1：页面内嵌的 SSR JSON（最准）。"""
    m = _QIDIAN_SSR_RE.search(html)
    if not m:
        return []
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[m.end():].lstrip())
    except Exception:
        return []
    page = ((obj.get("pageContext") or {}).get("pageProps") or {}).get("pageData") or {}
    info = page.get("bookInfo") or {}
    records = info.get("records")
    if not isinstance(records, list):
        records = info.get("recordsPC")
    return records if isinstance(records, list) else []


def _qidian_records_from_html(html: str) -> list:
    """路径 2：SSR 的 script 标签结构变了也不要紧，直接在页面里按字段扫。"""
    out = []
    for part in html.split('"bName"')[1:]:
        seg = part[:4000]
        name_match = re.match(r'\s*:\s*"((?:[^"\\]|\\.)*)"', seg)
        if not name_match:
            continue
        rec = {"bName": _json_unescape(name_match.group(1))}
        for key, pattern in _QIDIAN_FIELD_RES.items():
            hit = pattern.search(seg)
            if hit:
                rec[key] = _json_unescape(hit.group(1))
        bid = re.search(r'"bid"\s*:\s*(\d+)', seg)
        if bid:
            rec["bid"] = int(bid.group(1))
        out.append(rec)
    seen, uniq = set(), []
    for rec in out:
        key = rec.get("bName")
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(rec)
    return uniq


def search_qidian(keyword: str, timeout: int = 15) -> list:
    """起点中文网移动端搜索：书名/作者/简介/封面/分类一次拿全。"""
    url = QIDIAN_SEARCH_URL + "?" + urllib.parse.urlencode({"kw": keyword})
    headers = {"User-Agent": UA, "Referer": "https://m.qidian.com/"}
    html = http_get(url, headers, timeout)
    records = _qidian_records_from_ssr(html) or _qidian_records_from_html(html)
    if not records:
        _raise_parse_error(html, "起点搜索页里找不到书目数据（页面结构可能已改版）")
    out = []
    for rec in records:
        title = (rec.get("bName") or "").strip()
        if not title:
            continue
        bid = rec.get("bid") or rec.get("bookId") or ""
        out.append({
            "title": title,
            "author": (rec.get("bAuth") or "").strip(),
            "intro": re.sub(r"\s+", " ", rec.get("desc") or "").strip(),
            "cover": _qidian_cover_url(rec.get("imgUrl") or ""),
            "publisher": "",
            "rating": 0,
            "url": f"https://book.qidian.com/info/{bid}" if bid else "",
            "extra": _qidian_extra(rec),
        })
    return out


# --------------------------------------------------------------------------
# 微信读书
# --------------------------------------------------------------------------
def search_weread(keyword: str, timeout: int = 15) -> list:
    url = WEREAD_SEARCH_URL + "?" + urllib.parse.urlencode(
        {"keyword": keyword, "maxIdx": "0", "fragmentSize": "100"}
    )
    headers = {"User-Agent": UA, "Referer": "https://weread.qq.com/"}
    data = http_get_json(url, headers, timeout, label="微信读书")
    if not isinstance(data, dict):
        raise SourceBrokenError("微信读书返回的数据结构不符合预期")
    out = []
    for item in data.get("books") or []:
        info = item.get("bookInfo") or {}
        title = (info.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "author": (info.get("author") or "").strip(),
            "intro": re.sub(r"^\s*", "", info.get("intro") or "").strip(),
            # API 默认给 s_ 缩略图（70px），升到 t6_（250px）页面预览才清楚；
            # 真正下载封面时 fetch_cover_bytes 还会再升到 o_（600px）
            "cover": re.sub(r"/s_([^/]+)$", r"/t6_\1", (info.get("cover") or "").strip()),
            "publisher": (info.get("publisher") or "").strip(),
            "rating": round((info.get("newRating") or 0) / 10.0, 1),
            "url": "",
            "extra": (info.get("category") or "").strip(),
        })
    return out


# --------------------------------------------------------------------------
# 豆瓣读书：window.__DATA__ 优先，搜索页 HTML 兜底
# --------------------------------------------------------------------------
_DOUBAN_DATA_RE = re.compile(r"window\.__DATA__\s*=\s*(.+?);\s*</script>", re.S)


def _douban_detail(url: str, timeout: int = 6) -> dict:
    """抓豆瓣详情页的作者与简介（失败不影响主流程）。"""
    try:
        html = http_get(url, {"User-Agent": UA_BROWSER}, timeout)
    except Exception:
        return {}
    author = ""
    m = re.search(r'class="name"[^>]*>([^<]+)</a>', html)
    if m:
        author = m.group(1).strip()
    intro = ""
    m = re.search(r'id="link-report"[^>]*>(.*?)<div class="indent"', html, re.S)
    if m:
        block = re.sub(r"<style.*?</style>", "", m.group(1), flags=re.S)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", block)).strip(" .\u3000")
        if len(text) > 20:
            intro = text
    return {"author": author, "intro": intro}


def _douban_from_data(html: str) -> Optional[list]:
    """路径 1：搜索页里的 window.__DATA__（拿得到就返回列表，拿不到返回 None）。"""
    m = _DOUBAN_DATA_RE.search(html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(m.group(1).strip())
    except Exception:
        return None
    out = []
    for item in obj.get("items") or []:
        if not item.get("cover_url"):
            continue
        out.append({
            "title": (item.get("title") or "").strip(),
            "author": "",
            "intro": "",
            "cover": item["cover_url"].replace("/view/subject/m/public/", "/view/subject/l/public/"),
            "publisher": "",
            "rating": (item.get("rating") or {}).get("value") or 0,
            "url": item.get("url", ""),
            "extra": "",
        })
    return out


def _douban_from_html(html: str) -> list:
    """路径 2：直接解析搜索页里的结果节点（__DATA__ 变量改名时的兜底）。"""
    out = []
    for block in re.split(r'<div class="item-root"', html)[1:]:
        block = block[:4000]
        link = re.search(r'href="(https://book\.douban\.com/subject/\d+/[^"]*)"', block)
        title = re.search(r'class="title-text"[^>]*>\s*([^<]+)', block)
        if not (link and title):
            continue
        img = re.search(r'<img[^>]+src="([^"]+)"', block)
        cover = (img.group(1) if img else "").replace("/view/subject/m/public/", "/view/subject/l/public/")
        out.append({
            "title": title.group(1).strip(),
            "author": "",
            "intro": "",
            "cover": cover,
            "publisher": "",
            "rating": 0,
            "url": link.group(1),
            "extra": "",
        })
    return out


def search_douban(keyword: str, timeout: int = 15) -> list:
    """豆瓣读书搜索：封面取 l 大图。"""
    url = DOUBAN_SEARCH_URL + "?" + urllib.parse.urlencode({"search_text": keyword})
    html = http_get(url, {"User-Agent": UA_BROWSER}, timeout)
    items = _douban_from_data(html)
    if items is None:
        items = _douban_from_html(html)
        if not items:
            _raise_parse_error(html, "豆瓣搜索页结构与预期不符（页面可能已改版）")
    # 只对前 2 个候选抓详情（作者/简介），并发请求避免整体变慢
    targets = [c for c in items[:2] if c.get("url")]
    if targets:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            details = list(pool.map(lambda c: _douban_detail(c["url"]), targets))
        for cand, detail in zip(targets, details):
            cand["author"] = detail.get("author", "") or cand["author"]
            cand["intro"] = detail.get("intro", "") or cand["intro"]
    return items


# --------------------------------------------------------------------------
# 百度百科
# --------------------------------------------------------------------------
def _baike_fields(cards: list) -> tuple:
    """从百科词条卡片里取作者与作品信息。

    作者字段的 key 会随词条类型变化（m151_author / m27_author ...），
    所以按 key 后缀与中文名一起匹配，不能写死某个 key。
    """
    author, extra = "", []
    for card in cards:
        key = str(card.get("key") or "")
        name = str(card.get("name") or "")
        values = card.get("value") or []
        if not values:
            continue
        val = re.sub(r"<[^>]+>", "", str(values[0])).strip()
        if not val:
            continue
        if not author and (key.endswith("_author") or name in ("作者", "原著作者")):
            author = val
        elif name in ("作品类型", "连载平台", "连载状态", "总字数"):
            extra.append(val)
    return author, " · ".join(extra[:4])


def search_baike(keyword: str, timeout: int = 12) -> list:
    """百度百科开放接口：按精确词条返回单条候选。"""
    url = BAIKE_API_URL + "?" + urllib.parse.urlencode({
        "scope": "103", "format": "json", "appid": "379020",
        "bk_key": keyword, "bk_length": "600",
    })
    data = http_get_json(url, {"User-Agent": UA_BROWSER}, timeout, label="百度百科")
    if not isinstance(data, dict):
        raise SourceBrokenError("百度百科返回的数据结构不符合预期")
    # 词条标题字段换过名字，多留几个兜底
    title = (data.get("title") or data.get("key") or data.get("lemmaTitle") or "").strip()
    if not title:
        return []
    author, extra = _baike_fields(data.get("card") or [])
    intro = (data.get("abstract") or "").strip() or (data.get("desc") or "").strip()
    return [{
        "title": title,
        "author": author,
        "intro": intro,
        "cover": (data.get("image") or data.get("customImg") or "").strip(),
        "publisher": "",
        "rating": 0,
        "url": (data.get("url") or "").strip(),
        "extra": extra,
    }]


# --------------------------------------------------------------------------
# 当当图书：商品节点 / 标题锚点 / 旧结构 三路兜底
# --------------------------------------------------------------------------
_DANGDANG_ITEM_RE = re.compile(r'<li[^>]*id="p\d+"', re.I)
_DANGDANG_LEGACY_RE = re.compile(r'<li[^>]*class="line1"[^>]*>.*?</li>', re.S)
# 没搜到精确结果时，当当会塞一整页"为您推荐"，这些跟搜索词无关，不能当候选
_DANGDANG_NO_RESULT_RE = re.compile(r'class="search_msg"|没有找到与')
_DANGDANG_AUTHOR_SPLIT_RE = re.compile(r"编著|主编|著|译|出品")


def _dangdang_text(block: str, pattern: str) -> str:
    m = re.search(pattern, block, re.S)
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()


def _dangdang_block_to_candidate(block: str) -> Optional[dict]:
    title = _dangdang_text(block, r'<p class="name"[^>]*>\s*<a[^>]*>(.*?)</a>')
    if not title:
        tm = re.search(r'name=["\']itemlist-title["\'][^>]*title=["\']([^"\']*)["\']', block)
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip()
    if not title:
        return None
    # 电商标题常把推广语、商品描述全堆在书名后面，尽量只留书名部分
    title = re.sub(r"\s*[（(][^）)]{2,}[）)].*$", "", title).strip() or title
    title = re.split(r"[：:]", title)[0].strip() or title
    title = title[:60].strip()
    author = ""
    am = re.search(r'name=["\']itemlist-author["\'][^>]*title=["\']([^"\']*)["\']', block)
    if am:
        author = _DANGDANG_AUTHOR_SPLIT_RE.split(am.group(1))[0].strip(" ，,、") or am.group(1).strip()
    publisher = ""
    pm = re.search(r'name=["\']P_cbs["\'][^>]*title=["\']([^"\']*)["\']', block)
    if pm:
        publisher = pm.group(1).strip()
    cover = ""
    im = re.search(r"<img[^>]*src=['\"](//img[^'\"]+?\.jpg)['\"]", block)
    if im:
        cover = "https:" + im.group(1)
    return {
        "title": title,
        "author": author,
        "intro": _dangdang_text(block, r'<p class="detail"[^>]*>(.*?)</p>')[:800],
        "cover": cover,
        "publisher": publisher,
        "rating": 0,
        "url": "",
        "extra": "",
    }


def search_dangdang(keyword: str, timeout: int = 12) -> list:
    """当当图书搜索（实体书商品信息）。"""
    url = DANGDANG_SEARCH_URL + "?" + urllib.parse.urlencode({"key": keyword})
    html = http_get(url, {"User-Agent": UA_BROWSER}, timeout, encoding="gb18030")
    if _DANGDANG_NO_RESULT_RE.search(html):
        return []
    blocks = _DANGDANG_ITEM_RE.split(html)[1:]
    if not blocks:                       # 路径 2：商品 id 属性改名了，退回旧结构
        blocks = [m.group(0) for m in _DANGDANG_LEGACY_RE.finditer(html)]
    out = []
    for block in blocks:
        cand = _dangdang_block_to_candidate(block[:6000])
        if cand:
            out.append(cand)
    if not out:
        _raise_parse_error(html, "当当搜索结果里解析不出商品（页面结构可能已改版）")
    return out


# --------------------------------------------------------------------------
# Google 图书（官方 API，中文网络小说覆盖一般，作为实体书/外文书的兜底）
# --------------------------------------------------------------------------
def search_googlebooks(keyword: str, timeout: int = 8) -> list:
    url = GOOGLE_BOOKS_URL + "?" + urllib.parse.urlencode({
        "q": keyword, "maxResults": 10, "country": "US",
    })
    data = http_get_json(url, {"User-Agent": UA_BROWSER}, timeout, label="Google 图书")
    if not isinstance(data, dict):
        raise SourceBrokenError("Google 图书返回的数据结构不符合预期")
    out = []
    for item in data.get("items") or []:
        info = item.get("volumeInfo") or {}
        title = (info.get("title") or "").strip()
        if not title:
            continue
        subtitle = (info.get("subtitle") or "").strip()
        links = info.get("imageLinks") or {}
        cover = (links.get("thumbnail") or links.get("smallThumbnail") or "").strip()
        if cover:
            cover = cover.replace("http://", "https://").replace("&zoom=1", "&zoom=2")
        out.append({
            "title": title,
            "author": "、".join(info.get("authors") or []),
            "intro": (info.get("description") or "").strip(),
            "cover": cover,
            "publisher": (info.get("publisher") or "").strip(),
            "rating": info.get("averageRating") or 0,
            "url": (info.get("infoLink") or "").strip(),
            "extra": " · ".join(x for x in (
                subtitle[:20], (info.get("publishedDate") or "")[:4],
                f"{info.get('pageCount')}页" if info.get("pageCount") else "",
            ) if x),
        })
    return out


# --------------------------------------------------------------------------
# Open Library（官方 API，默认关闭）
# --------------------------------------------------------------------------
def search_openlibrary(keyword: str, timeout: int = 10) -> list:
    url = OPENLIBRARY_URL + "?" + urllib.parse.urlencode({
        "q": keyword, "limit": 10,
        "fields": "title,author_name,cover_i,first_sentence,publisher,first_publish_year,key",
    })
    data = http_get_json(url, {"User-Agent": UA_BROWSER}, timeout, label="Open Library")
    if not isinstance(data, dict):
        raise SourceBrokenError("Open Library 返回的数据结构不符合预期")
    out = []
    for doc in data.get("docs") or []:
        title = (doc.get("title") or "").strip()
        if not title:
            continue
        cover_i = doc.get("cover_i")
        first = doc.get("first_sentence")
        if isinstance(first, list):
            first = first[0] if first else ""
        out.append({
            "title": title,
            "author": "、".join(doc.get("author_name") or []),
            "intro": str(first or "").strip(),
            "cover": f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg" if cover_i else "",
            "publisher": "、".join(doc.get("publisher") or [])[:60],
            "rating": 0,
            "url": f"https://openlibrary.org{doc['key']}" if doc.get("key") else "",
            "extra": str(doc.get("first_publish_year") or ""),
        })
    return out


@dataclass(frozen=True)
class SourceDef:
    """一个刮削源的静态定义。"""

    id: str
    label: str
    search: Callable[..., list]
    timeout: int = 15
    throttle: float = 0.8       # 同一源两次请求的最小间隔（秒）
    enabled: bool = True
    homepage: str = ""
    note: str = ""


# 内置源：顺序即 UI 展示顺序，前两个是网络小说的主力
BUILTIN_SOURCES = (
    SourceDef("qidian", "起点中文网", search_qidian, throttle=1.0, enabled=True,
              homepage="https://www.qidian.com", note="网络小说首选，简介/封面最全"),
    SourceDef("weread", "微信读书", search_weread, throttle=0.8, enabled=True,
              homepage="https://weread.qq.com", note="网络小说覆盖好，600px 封面"),
    SourceDef("douban", "豆瓣读书", search_douban, throttle=1.5, enabled=True,
              homepage="https://book.douban.com", note="实体书覆盖好，请求过快会被反爬"),
    SourceDef("baike", "百度百科", search_baike, throttle=1.0, enabled=True,
              homepage="https://baike.baidu.com", note="精确词条，简介完整"),
    SourceDef("dangdang", "当当图书", search_dangdang, throttle=1.2, enabled=True,
              homepage="https://www.dangdang.com", note="实体书商品信息"),
    SourceDef("googlebooks", "Google 图书", search_googlebooks, timeout=8, throttle=0.5,
              enabled=False, homepage="https://books.google.com",
              note="官方 API，国内网络通常不可达，需要时在 sources.yml 里打开"),
    SourceDef("openlibrary", "Open Library", search_openlibrary, throttle=0.5, enabled=False,
              homepage="https://openlibrary.org", note="官方 API，中文书覆盖一般，默认关闭"),
)

BUILTIN_BY_ID = {s.id: s for s in BUILTIN_SOURCES}
