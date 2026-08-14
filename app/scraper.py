# -*- coding: utf-8 -*-
"""元信息刮削：导入 TXT 后自动补全书名、作者、简介、封面等元信息。

支持多个刮削源：微信读书（weread）、豆瓣读书（douban），
可指定单个源或全部源；刮削不到时回退为占位封面。
"""

from __future__ import annotations

import io
import json
import logging
import re
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from app.template import effective_cover

log = logging.getLogger("scraper")

WEREAD_SEARCH_URL = "https://weread.qq.com/web/search/global"
DOUBAN_SEARCH_URL = "https://search.douban.com/book/subject_search"
BAIKE_API_URL = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
DANGDANG_SEARCH_URL = "https://search.dangdang.com/"
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)
UA_BROWSER = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
SOURCE_WEREAD = "weread"
SOURCE_DOUBAN = "douban"
SOURCE_BAIKE = "baike"
SOURCE_DANGDANG = "dangdang"
SOURCES = (SOURCE_WEREAD, SOURCE_DOUBAN, SOURCE_BAIKE, SOURCE_DANGDANG)
SOURCE_LABELS = {
    SOURCE_WEREAD: "微信读书",
    SOURCE_DOUBAN: "豆瓣读书",
    SOURCE_BAIKE: "百度百科",
    SOURCE_DANGDANG: "当当图书",
}
SCORE_THRESHOLD = 78.0
_TITLE_SUFFIXES = ("全集", "全文", "完整版", "完结版", "完结", "无删减", "下载", "电子书", "txt版", "txt")
_META_EXTS = (".yaml", ".yml", ".json", ".txt")
_COVER_EXTS = (".jpg", ".jpeg", ".png")


def _norm(text: str) -> str:
    return re.sub(r"[\s《》【】\[\]()（）「」『』:：,，.。!！?？\-—_·・]+", "", text or "").lower()


def _sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    if na in nb or nb in na:
        return min(100.0, 85.0 + min(len(na), len(nb)) * 2)
    return SequenceMatcher(None, na, nb).ratio() * 100.0


def _match_score(cand: dict, title: str, author: str = "") -> float:
    """候选匹配分：书名相似度 + 作者命中加分/冲突减分。"""
    score = _sim(title, cand.get("title", ""))
    if author:
        a_n = _norm(author)
        ca_n = _norm(cand.get("author", ""))
        if a_n and ca_n:
            if a_n in ca_n or ca_n in a_n:
                score += 15.0
            else:
                score -= 20.0
    return score


def pick_best(candidates: list, title: str, author: str = "") -> Optional[dict]:
    """按匹配分挑选最佳候选，低于阈值视为未找到。"""
    best, best_score = None, 0.0
    for cand in candidates:
        score = _match_score(cand, title, author)
        if score > best_score:
            best, best_score = cand, score
    if best is not None and best_score >= SCORE_THRESHOLD:
        return best
    return None


def search_candidates(
    title: str, author: str = "", limit: int = 8, source: Optional[str] = None
) -> list:
    """按指定源（或全部源）搜索，返回带匹配分与来源标签的候选列表。"""
    if not title:
        return []
    selected = [source] if source in SOURCES else list(SOURCES)
    out = []
    for src in selected:
        try:
            if src == SOURCE_WEREAD:
                candidates = _weread_search(title)
            elif src == SOURCE_DOUBAN:
                candidates = _douban_search(title)
            elif src == SOURCE_BAIKE:
                candidates = _baike_search(title)
            elif src == SOURCE_DANGDANG:
                candidates = _dangdang_search(title)
            else:
                candidates = []
        except Exception as exc:
            log.warning("刮削源 %s 搜索失败（%s）：%s", src, title, exc)
            candidates = []
        for cand in candidates[:limit]:
            cand = dict(cand)
            cand["score"] = round(_match_score(cand, title, author), 1)
            out.append(cand)
    return out


def _weread_search(keyword: str) -> list:
    url = WEREAD_SEARCH_URL + "?" + urllib.parse.urlencode(
        {"keyword": keyword, "maxIdx": "0", "fragmentSize": "100"}
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": "https://weread.qq.com/"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    out = []
    for item in data.get("books") or []:
        info = item.get("bookInfo") or {}
        title = (info.get("title") or "").strip()
        if not title:
            continue
        intro = re.sub(r"^\s*", "", info.get("intro") or "").strip()
        out.append({
            "title": title,
            "author": (info.get("author") or "").strip(),
            "intro": intro,
            "cover": (info.get("cover") or "").strip(),
            "publisher": (info.get("publisher") or "").strip(),
            "rating": round((info.get("newRating") or 0) / 10.0, 1),
            "source": SOURCE_WEREAD,
            "source_label": SOURCE_LABELS[SOURCE_WEREAD],
        })
    return out


def _douban_detail(url: str) -> dict:
    """抓取豆瓣详情页的作者与简介。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA_BROWSER})
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        log.debug("豆瓣详情抓取失败 %s：%s", url, exc)
        return {}
    author = ""
    m = re.search(r'class="name"[^>]*>([^<]+)</a>', html)
    if m:
        author = m.group(1).strip()
    intro = ""
    m = re.search(r'id="link-report"[^>]*>(.*?)<div class="indent"', html, re.S)
    if m:
        block = m.group(1)
        block = re.sub(r"<style.*?</style>", "", block, flags=re.S)
        text = re.sub(r"<[^>]+>", "", block)
        text = re.sub(r"\s+", " ", text).strip(" .\u3000")
        if len(text) > 20:
            intro = text
    return {"author": author, "intro": intro}


def _douban_search(keyword: str) -> list:
    """豆瓣读书搜索：解析搜索页 window.__DATA__，封面取 l 大图。"""
    url = DOUBAN_SEARCH_URL + "?" + urllib.parse.urlencode({"search_text": keyword})
    req = urllib.request.Request(url, headers={"User-Agent": UA_BROWSER})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", "replace")
    m = re.search(r"window\.__DATA__\s*=\s*(.+?);\s*</script>", html, re.S)
    if not m:
        return []
    try:
        obj, _ = json.JSONDecoder().raw_decode(m.group(1).strip())
    except Exception:
        return []
    out = []
    for item in obj.get("items") or []:
        if not item.get("cover_url"):
            continue
        cover = item["cover_url"].replace("/view/subject/m/public/", "/view/subject/l/public/")
        rating = (item.get("rating") or {}).get("value") or 0
        out.append({
            "title": (item.get("title") or "").strip(),
            "author": "",
            "intro": "",
            "cover": cover,
            "publisher": "",
            "rating": rating,
            "url": item.get("url", ""),
            "source": SOURCE_DOUBAN,
            "source_label": SOURCE_LABELS[SOURCE_DOUBAN],
        })
    # 只对前 2 个候选抓详情（作者/简介），避免太慢
    for cand in out[:2]:
        if cand.get("url"):
            detail = _douban_detail(cand["url"])
            cand["author"] = detail.get("author", "") or cand["author"]
            cand["intro"] = detail.get("intro", "") or cand["intro"]
    return out


def _baike_search(keyword: str) -> list:
    """百度百科开放接口：按精确词条返回单条候选（作者/简介/封面图）。"""
    url = BAIKE_API_URL + "?" + urllib.parse.urlencode({
        "scope": "103", "format": "json", "appid": "379020",
        "bk_key": keyword, "bk_length": "600",
    })
    req = urllib.request.Request(url, headers={"User-Agent": UA_BROWSER})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if not isinstance(data, dict) or not data.get("title"):
        return []
    author = ""
    for card in data.get("card") or []:
        if card.get("key") == "m27_author":
            values = card.get("value") or []
            if values:
                author = re.sub(r"<[^>]+>", "", str(values[0])).strip()
            break
    intro = (data.get("abstract") or "").strip()
    if not intro:
        intro = (data.get("desc") or "").strip()
    return [{
        "title": (data.get("title") or keyword).strip(),
        "author": author,
        "intro": intro,
        "cover": (data.get("image") or "").strip(),
        "publisher": "",
        "rating": 0,
        "source": SOURCE_BAIKE,
        "source_label": SOURCE_LABELS[SOURCE_BAIKE],
    }]


def _dangdang_search(keyword: str) -> list:
    """当当图书搜索：解析搜索结果页（书名/作者/出版社/封面）。"""
    url = DANGDANG_SEARCH_URL + "?" + urllib.parse.urlencode({"key": keyword})
    req = urllib.request.Request(url, headers={"User-Agent": UA_BROWSER})
    with urllib.request.urlopen(req, timeout=12) as resp:
        raw = resp.read()
    try:
        html = raw.decode("gb18030")
    except Exception:
        html = raw.decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'<li[^>]*class="line1"[^>]*>.*?</li>', html, re.S):
        block = m.group(0)
        title = ""
        tm = re.search(r'<p class="name"[^>]*>\s*<a[^>]*>(.*?)</a>', block, re.S)
        if tm:
            t = re.sub(r"<[^>]+>", "", tm.group(1))
            t = re.sub(r"\s+", " ", t).strip()
            title = re.split(r"[：:]", t)[0].strip()
        if not title:
            am = re.search(r'name="itemlist-title"[^>]*title="([^"]*)"', block)
            if am:
                title = re.split(r"[：:]", am.group(1))[0].strip()
        if not title:
            continue
        author = ""
        am = re.search(r"itemlist-author[^>]*title='([^']*)'", block)
        if am:
            author = am.group(1).strip()
        publisher = ""
        pm = re.search(r"P_cbs[^>]*title='([^']*)'", block)
        if pm:
            publisher = pm.group(1).strip()
        cover = ""
        im = re.search(r"<img[^>]*src=['\"](//img[^'\"]+?-\d+_b_[^'\"]+\.jpg)['\"]", block)
        if im:
            cover = "https:" + im.group(1)
        out.append({
            "title": title,
            "author": author,
            "intro": "",
            "cover": cover,
            "publisher": publisher,
            "rating": 0,
            "source": SOURCE_DANGDANG,
            "source_label": SOURCE_LABELS[SOURCE_DANGDANG],
        })
    return out


def scrape_book(
    title: str, author: str = "", source: Optional[str] = None
) -> Optional[dict]:
    """按书名（可带作者）刮削一本书的元信息，失败返回 None。"""
    if not title:
        return None
    return pick_best(search_candidates(title, author, source=source), title, author)


def guess_title_author(lines: list) -> tuple:
    """从 TXT 前几行猜测书名与作者。"""
    title, author = "", ""
    for raw in lines[:40]:
        s = raw.strip()
        if not s:
            continue
        if re.match(r"^(声明|本书由|本站|如有|版权|免责|TXT|txt)", s):
            continue
        m = re.match(r"^(?:作者|著者|原著|原著作者|原作者)\s*[:：]\s*(.+)$", s)
        if m and not author:
            author = m.group(1).strip().strip("。. ")
            continue
        if not title and len(s) <= 30:
            m2 = re.match(r"^《(.+?)》(.*)$", s)
            t = (m2.group(1) + (m2.group(2) or "")) if m2 else s.strip("《》[]【】")
            t = re.sub(r"(全集|全文|完整版|完结版|完结|无删减|下载|电子书|txt版|txt)$", "", t).strip()
            if t and not re.search(r"[：:]", t):
                title = t
    return title, author


def guess_from_txt(txt_path: Path) -> tuple:
    try:
        from app.txt_reader import read_text_with_encoding
        text, _ = read_text_with_encoding(txt_path)
    except Exception:
        text = txt_path.read_text(encoding="utf-8", errors="replace")
    return guess_title_author(text.splitlines())


def guess_from_txt_head(txt_path: Path) -> tuple:
    """只读文件头 64KB 猜测书名/作者，用于列表快速展示。"""
    try:
        with txt_path.open("rb") as fh:
            head = fh.read(65536)
    except OSError:
        return "", ""
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = head.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = head.decode("gb18030", errors="replace")
    return guess_title_author(text.splitlines())


def download_cover(url: str, dest: Path) -> bool:
    """下载封面并统一转成 JPEG。优先尝试 o_（600px 原图）版本，保证清晰。"""
    if not url:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    m = re.search(r"(?:/[a-z0-9]+_)([^/]+)$", url)
    if m:
        base, name = url[: m.start(0)], m.group(1)
        # 微信读书封面尺寸：o_ 600px 原图 > x_ 500px > t6_ > b_ > m_ > s_
        variants = [f"{base}/{pref}_{name}" for pref in ("o", "x", "t6", "b", "m", "s")]
    else:
        variants = [url]
    from PIL import Image
    for cand in variants:
        try:
            headers = {"User-Agent": UA_BROWSER}
            if "doubanio.com" in cand:
                headers["Referer"] = "https://book.douban.com/"
            elif "weread.qq.com" in cand or "wfqqreader" in cand:
                headers["Referer"] = "https://weread.qq.com/"
            elif "bcebos.com" in cand:
                headers["Referer"] = "https://baike.baidu.com/"
            elif "ddimg.cn" in cand:
                headers["Referer"] = "https://search.dangdang.com/"
            req = urllib.request.Request(cand, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            if len(data) < 500:
                continue
            img = Image.open(io.BytesIO(data))
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                img = Image.alpha_composite(bg, rgba).convert("RGB")
            else:
                img = img.convert("RGB")
            img.save(dest, format="JPEG", quality=95, optimize=True)
            log.info("封面已下载：%s", dest.name)
            return True
        except Exception as exc:
            log.debug("封面候选下载失败 %s：%s", cand, exc)
    return False


def _load_font(fonts_dir: Optional[Path], size: int):
    from PIL import ImageFont
    candidates = []
    if fonts_dir and fonts_dir.is_dir():
        for ext in ("*.ttf", "*.otf", "*.ttc"):
            candidates += sorted(fonts_dir.glob(ext))
    candidates += [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for cand in candidates:
        try:
            return ImageFont.truetype(str(cand), size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_placeholder_cover(
    title: str, dest: Path, spec, fonts_dir: Optional[Path] = None
) -> bool:
    """生成一张带书名的占位封面（模板比例），保证没有真实封面也能转换。"""
    from PIL import Image, ImageDraw
    try:
        w, h = int(spec.width), int(spec.height)
        img = Image.new("RGB", (w, h), (38, 47, 63))
        draw = ImageDraw.Draw(img)
        text = (title or "未命名").strip()
        font = _load_font(fonts_dir, 34)
        max_chars = max(1, (w - 40) // 34)
        lines = [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
        line_h = 46
        y = (h - len(lines) * line_h) // 2
        for ln in lines:
            tw = draw.textlength(ln, font=font)
            draw.text(((w - tw) / 2, y), ln, fill=(245, 242, 234), font=font)
            y += line_h
        img.save(dest, format="JPEG", quality=int(spec.quality), optimize=True)
        return True
    except Exception as exc:
        log.warning("占位封面生成失败：%s", exc)
        return False


def has_meta_file(meta_dir: Path, stem: str) -> bool:
    return any((meta_dir / f"{stem}{ext}").is_file() for ext in _META_EXTS)


def same_stem_cover(input_dir: Path, stem: str) -> Optional[Path]:
    for ext in _COVER_EXTS:
        p = input_dir / f"{stem}{ext}"
        if p.is_file():
            return p
    return None


def prepare_book_files(
    txt_path: Path,
    settings,
    template,
    title: str = "",
    author: str = "",
    description: str = "",
    tags=None,
    publisher: str = "",
    series: str = "",
    scrape: bool = True,
) -> dict:
    """刮削并落盘元信息/封面，返回 {title, author, description, meta_path, cover_path, scraped}。

    已有元信息文件或同名封面时尊重用户输入，不覆盖。
    """
    stem = txt_path.stem
    meta_dir, input_dir, fonts_dir = settings.meta_dir, settings.input_dir, settings.fonts_dir
    meta_path = meta_dir / f"{stem}.yaml"

    if has_meta_file(meta_dir, stem):
        log.info("[%s] 已有元信息文件，跳过刮削", stem)
        return {
            "title": title or stem,
            "author": author,
            "description": description,
            "meta_path": None,
            "cover_path": same_stem_cover(input_dir, stem),
            "scraped": False,
        }

    g_title, g_author = guess_from_txt(txt_path)
    title = title.strip() or g_title or stem
    author = author.strip() or g_author or ""
    description = description.strip()
    scraped = None
    if scrape:
        scraped = scrape_book(title, author)
        if scraped:
            title = title or scraped["title"]
            author = author or scraped["author"]
            description = description or scraped["intro"]

    cover_path = same_stem_cover(input_dir, stem)
    if cover_path is None and scraped and download_cover(scraped.get("cover", ""), input_dir / f"{stem}.jpg"):
        cover_path = input_dir / f"{stem}.jpg"
    if cover_path is None and make_placeholder_cover(
        title, input_dir / f"{stem}.jpg", effective_cover(template, settings), fonts_dir
    ):
        cover_path = input_dir / f"{stem}.jpg"

    data = {"title": title, "author": author, "description": description}
    if tags:
        data["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if publisher:
        data["publisher"] = publisher
    if series:
        data["series"] = series
    if cover_path is not None and cover_path.name != f"{stem}.jpg":
        data["cover"] = cover_path.name

    import yaml
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    log.info(
        "[%s] 元信息已生成（%s），封面：%s",
        stem,
        "刮削来源" if scraped else "书名/占位封面",
        cover_path.name if cover_path else "无",
    )
    return {
        "title": title,
        "author": author,
        "description": description,
        "meta_path": meta_path,
        "cover_path": cover_path,
        "scraped": bool(scraped),
    }
