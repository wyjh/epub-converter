# -*- coding: utf-8 -*-
"""元信息刮削：导入 TXT 后自动补全书名、作者、简介、封面等元信息。

这一层负责「源的管理」而不是「源的解析」：

- 源定义来自 app.scraper_sources（内置源）与 sources.yml（开关 / 超时 / 自定义源）；
- 统一做结果缓存、请求节流、失败熔断，减少被限流、加快重复刮削；
- 把「没这本书」和「源坏了」区分开（ok / empty / blocked / broken / error），
  供日志和「检测刮削源」使用。

对外接口保持稳定：search_candidates / scrape_book / download_cover /
prepare_book_files / guess_* 等。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

from app.scraper_sources import (
    BUILTIN_BY_ID,
    BUILTIN_SOURCES,
    _raise_parse_error,
    SourceBlockedError,
    SourceBrokenError,
    SourceDef,
    UA,
    UA_BROWSER,
    _DANGDANG_NO_RESULT_RE,
    _baike_fields,
    _qidian_cover_url,
    _qidian_records_from_ssr as _qidian_records,
    http_get,
    http_get_json,
)

log = logging.getLogger("scraper")

SOURCE_QIDIAN = "qidian"
SOURCE_WEREAD = "weread"
SOURCE_DOUBAN = "douban"
SOURCE_BAIKE = "baike"
SOURCE_DANGDANG = "dangdang"
SOURCE_GOOGLEBOOKS = "googlebooks"
SOURCE_OPENLIBRARY = "openlibrary"

# 全部内置源（顺序即默认展示顺序）
SOURCES = tuple(s.id for s in BUILTIN_SOURCES)
SOURCE_LABELS = {s.id: s.label for s in BUILTIN_SOURCES}

SCORE_THRESHOLD = 78.0
_META_EXTS = (".yaml", ".yml", ".json", ".txt")
_COVER_EXTS = (".jpg", ".jpeg", ".png")
_NAME_STOPWORDS = (
    "新建文本文档", "新建文档", "未命名", "无标题", "文档", "小说", "下载",
    "全本", "全集", "book", "novel", "txt", "text", "untitled",
)
_MAX_WORKERS = 10

# 允许代理的封面图床（Web 端封面代理用它做白名单，避免变成任意 URL 代理）
# 自定义源需要额外域名时写在 sources.yml 的 settings.extra_cover_hosts 里。
COVER_HOST_SUFFIXES = (
    "yuewen.com", "qidian.com",
    "myqcloud.com", "weread.qq.com",
    "doubanio.com", "douban.com",
    "bcebos.com", "baidu.com",
    "ddimg.cn", "dangdang.com",
    "googleusercontent.com", "books.google.com",
    "openlibrary.org", "archive.org",
)

DEFAULT_CONFIG = {
    "settings": {
        "extra_cover_hosts": [],      # 自定义源的封面域名（追加到封面代理白名单）
        "cache_enabled": True,
        "cache_ttl_hours": 168,       # 刮削结果缓存 7 天
        "failure_threshold": 3,       # 连续失败多少次后熔断
        "cooldown_seconds": 300,      # 熔断时长
    },
    "sources": {s.id: {"enabled": s.enabled} for s in BUILTIN_SOURCES},
    "custom": [],
}

_CONFIG_TEMPLATE = """# 刮削源配置
# 改完保存后，在网页上点「重新加载配置」或重启服务即可生效。
#
# settings:
#   extra_cover_hosts  自定义源的封面图床域名，例如 ["cdn.example.com"]。
#                      页面上的候选封面走后端代理，只允许已知图床；用自定义源时把它的
#                      封面域名加到这里，否则封面会因为不在白名单而不显示。
#   cache_enabled      是否缓存刮削结果（强烈建议开启，能显著减少请求、降低被限流概率）
#   cache_ttl_hours    缓存有效期（小时），默认 7 天
#   failure_threshold  某个源连续失败多少次后临时熔断
#   cooldown_seconds   熔断时长（秒），期间跳过该源，避免拖慢整体搜索
#
# sources: 内置源的开关与参数（enabled / timeout / throttle）
#   throttle 是同一源两次请求的最小间隔（秒），调大可以降低被反爬的概率
#
# custom: 自定义源，接口是普通 JSON/HTML 时不用改代码就能接
#   - id: myapi                    唯一标识
#     label: 我的书库              界面显示名
#     enabled: true
#     url: "https://example.com/search?q={keyword}"     {keyword} 会被替换成关键词
#     headers: {User-Agent: "..."}   可选
#     timeout: 10                    可选
#     throttle: 1                    可选
#     format: json                   json（默认）或 html
#     items: "data.list"             json：结果数组所在路径，留空表示顶层就是数组
#     item_regex: '<li class="book">.*?</li>'   html：每个结果块的正则
#     encoding: gb18030              可选；接口不是 UTF-8 时指定（默认自动猜）
#     fields:                        字段映射
#       title: "name"                json 取字段（支持 a.b.c 路径）/ html 用正则取第 1 组
#       author: "author"
#       intro: "desc"
#       cover: "cover_url"
#       publisher: "publisher"
#       rating: "score"
#       url: "link"
#       extra: "category"

settings:
  extra_cover_hosts: []      # 自定义源的封面域名，例如 ["cdn.example.com"]
  cache_enabled: true
  cache_ttl_hours: 168
  failure_threshold: 3
  cooldown_seconds: 300

sources:
  qidian:
    enabled: true
    throttle: 1.0
  weread:
    enabled: true
    throttle: 0.8
  douban:
    enabled: true
    throttle: 1.5        # 豆瓣对高频请求敏感，慢一点更稳
  baike:
    enabled: true
  dangdang:
    enabled: true
    throttle: 1.2
  googlebooks:
    enabled: false       # 官方 API；国内网络通常连不上，需要时再打开
    timeout: 8
  openlibrary:
    enabled: false       # 官方 API；中文书覆盖一般，需要时再打开

custom: []
"""


# ==========================================================================
# 配置
# ==========================================================================
def default_config_path() -> Path:
    return Path(os.environ.get("SOURCES_FILE", "sources.yml"))


def load_sources_config(path: Optional[Path] = None) -> dict:
    """读取 sources.yml；文件不存在时返回默认配置并写出一份带注释的模板。"""
    target = Path(path) if path else default_config_path()
    if not target.is_file():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
            log.info("已生成刮削源配置模板：%s", target)
        except OSError as exc:
            log.debug("刮削源配置模板写出失败：%s", exc)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        import yaml
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("刮削源配置读取失败（改用默认配置）：%s", exc)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    if not isinstance(data, dict):
        return json.loads(json.dumps(DEFAULT_CONFIG))
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if isinstance(data.get("settings"), dict):
        cfg["settings"].update(data["settings"])
    if isinstance(data.get("sources"), dict):
        for sid, entry in data["sources"].items():
            if isinstance(entry, dict):
                cfg["sources"].setdefault(sid, {}).update(entry)
    if isinstance(data.get("custom"), list):
        cfg["custom"] = [c for c in data["custom"] if isinstance(c, dict) and c.get("id")]
    return cfg


# ==========================================================================
# 运行时状态：节流 / 熔断 / 最近一次结果
# ==========================================================================
class SourceRuntime:
    """单个源的运行时状态。"""

    def __init__(self, spec: SourceDef):
        self.spec = spec
        self.lock = threading.Lock()
        self._last_call = 0.0
        self._fail_streak = 0
        self._cooldown_until = 0.0
        self.last: dict = {"state": "unknown", "message": "", "count": 0,
                           "elapsed": 0.0, "time": "", "keyword": ""}
        self.cache_hits = 0

    @property
    def cooling(self) -> bool:
        return time.time() < self._cooldown_until

    def throttle_wait(self) -> None:
        interval = max(0.0, float(self.spec.throttle or 0))
        if interval <= 0:
            return
        with self.lock:
            wait = self._last_call + interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def record(self, state: str, message: str = "", count: int = 0,
               elapsed: float = 0.0, keyword: str = "", broken: bool = False) -> None:
        with self.lock:
            if state in ("ok", "empty", "cached"):
                self._fail_streak = 0
            elif broken:
                self._fail_streak += 1
                if self._fail_streak >= _failure_threshold():
                    self._cooldown_until = time.time() + _cooldown_seconds()
                    log.warning("刮削源 %s 连续失败 %d 次，暂停 %.0f 秒",
                                self.spec.label, self._fail_streak, _cooldown_seconds())
            self.last = {
                "state": state,
                "message": message[:300],
                "count": count,
                "elapsed": round(elapsed, 2),
                "time": datetime.now().isoformat(timespec="seconds"),
                "keyword": keyword,
            }

    def snapshot(self) -> dict:
        return {
            "id": self.spec.id,
            "label": self.spec.label,
            "enabled": self.spec.enabled,
            "builtin": self.spec.id in BUILTIN_BY_ID,
            "note": self.spec.note,
            "homepage": self.spec.homepage,
            "throttle": self.spec.throttle,
            "timeout": self.spec.timeout,
            "cooling": self.cooling,
            "fail_streak": self._fail_streak,
            "cache_hits": self.cache_hits,
            **self.last,
        }


_CONFIG: dict = {}
_REGISTRY: list = []
_REGISTRY_LOCK = threading.Lock()
_CACHE_DIR: Optional[Path] = None


def _settings_cfg() -> dict:
    cfg = _CONFIG.get("settings")
    return cfg if isinstance(cfg, dict) else {}


def _failure_threshold() -> int:
    try:
        return max(1, int(_settings_cfg().get("failure_threshold", 3)))
    except (TypeError, ValueError):
        return 3


def _cooldown_seconds() -> float:
    try:
        return max(0.0, float(_settings_cfg().get("cooldown_seconds", 300)))
    except (TypeError, ValueError):
        return 300.0


def _cache_ttl_seconds() -> float:
    try:
        hours = float(_settings_cfg().get("cache_ttl_hours", 168))
    except (TypeError, ValueError):
        hours = 168.0
    return max(0.0, hours) * 3600.0


def _cache_enabled() -> bool:
    return bool(_settings_cfg().get("cache_enabled", True))


# ==========================================================================
# 自定义源
# ==========================================================================
def _json_path(obj, path: str):
    """按 a.b.c 路径取值（允许以 $. 开头，支持列表下标）。"""
    if not path:
        return obj
    cur = obj
    for part in str(path).strip().lstrip("$").strip(".").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def _custom_field(item, rule: str, fmt: str) -> str:
    if fmt == "html":
        m = re.search(rule, item, re.S)
        if not m:
            return ""
        val = m.group(1) if m.groups() else m.group(0)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", val)).strip()
    val = _json_path(item, rule)
    if isinstance(val, list):
        val = "、".join(str(v) for v in val if v)
    return str(val).strip() if val is not None else ""


def _make_custom_search(entry: dict) -> Callable[..., list]:
    """按配置生成一个自定义源的搜索函数（json / html 两种格式）。"""
    fields = entry.get("fields") or {}
    fmt = str(entry.get("format") or "json").lower()

    def _search(keyword: str, timeout: int = 15) -> list:
        url = str(entry.get("url") or "").replace("{keyword}", urllib.parse.quote(keyword))
        if not url:
            raise SourceBrokenError("自定义源没有配置 url")
        headers = {k: str(v) for k, v in (entry.get("headers") or {}).items()}
        headers.setdefault("User-Agent", UA_BROWSER)
        encoding = entry.get("encoding")
        if fmt == "html":
            html = http_get(url, headers, timeout, encoding=encoding)
            item_re = entry.get("item_regex")
            source_items = re.findall(item_re, html, re.S) if item_re else [html]
        else:
            text = http_get(url, headers, timeout, encoding=encoding)
            try:
                data = json.loads(text)
            except ValueError:
                _raise_parse_error(text, "自定义源返回的不是合法 JSON")
            source_items = _json_path(data, entry.get("items") or "") or []
            if isinstance(source_items, dict):
                source_items = [source_items]
            if not isinstance(source_items, list):
                raise SourceBrokenError("自定义源 items 路径取不到数组")
        out = []
        for item in source_items:
            cand = {key: _custom_field(item, rule, fmt) for key, rule in fields.items()}
            if not cand.get("title"):
                continue
            cand.setdefault("author", "")
            cand.setdefault("intro", "")
            cand.setdefault("cover", "")
            cand.setdefault("publisher", "")
            cand.setdefault("url", "")
            cand.setdefault("extra", "")
            try:
                cand["rating"] = float(cand.get("rating") or 0)
            except (TypeError, ValueError):
                cand["rating"] = 0
            out.append(cand)
        if not out and source_items:
            raise SourceBrokenError("自定义源取到了数据但字段映射不匹配（检查 fields 配置）")
        return out

    return _search


def _build_registry(cfg: dict) -> list:
    src_cfg = cfg.get("sources") or {}
    runtimes = []
    for spec in BUILTIN_SOURCES:
        over = src_cfg.get(spec.id) or {}
        runtime_spec = SourceDef(
            id=spec.id,
            label=spec.label,
            search=spec.search,
            timeout=int(over.get("timeout", spec.timeout) or spec.timeout),
            throttle=float(over.get("throttle", spec.throttle) or 0),
            enabled=bool(over.get("enabled", spec.enabled)),
            homepage=spec.homepage,
            note=spec.note,
        )
        runtimes.append(SourceRuntime(runtime_spec))
    for entry in cfg.get("custom") or []:
        sid = str(entry.get("id"))
        if not sid:
            continue
        if sid in BUILTIN_BY_ID or any(rt.spec.id == sid for rt in runtimes):
            log.warning("自定义源 id 与已有源重名，已跳过：%s", sid)
            continue
        runtime_spec = SourceDef(
            id=sid,
            label=str(entry.get("label") or sid),
            search=_make_custom_search(entry),
            timeout=int(entry.get("timeout", 15) or 15),
            throttle=float(entry.get("throttle", 1.0) or 0),
            enabled=bool(entry.get("enabled", True)),
            homepage=str(entry.get("homepage") or ""),
            note=str(entry.get("note") or "自定义源"),
        )
        runtimes.append(SourceRuntime(runtime_spec))
    return runtimes


def _registry() -> list:
    global _REGISTRY, _CONFIG
    if not _REGISTRY:
        with _REGISTRY_LOCK:
            if not _REGISTRY:
                _CONFIG = load_sources_config()
                _REGISTRY = _build_registry(_CONFIG)
    return _REGISTRY


def reload_sources_config(path: Optional[Path] = None) -> list:
    """重新加载配置（改完 sources.yml 后调用）。"""
    global _REGISTRY, _CONFIG
    with _REGISTRY_LOCK:
        _CONFIG = load_sources_config(path)
        _REGISTRY = _build_registry(_CONFIG)
    log.info("刮削源配置已重新加载：%d 个源", len(_REGISTRY))
    return _REGISTRY


def configure_scrape_cache(directory: Path) -> None:
    """指定刮削结果缓存的落地目录。"""
    global _CACHE_DIR
    _CACHE_DIR = Path(directory)


def _cache_dir() -> Path:
    if _CACHE_DIR is not None:
        return _CACHE_DIR
    return Path(os.environ.get("WORK_DIR", "work")) / "scrape_cache"


def _cache_file(source_id: str, keyword: str) -> Path:
    raw = f"{source_id}\x00{keyword}".encode("utf-8")
    return _cache_dir() / f"{hashlib.sha1(raw).hexdigest()}.json"


def _cache_get(source_id: str, keyword: str) -> Optional[list]:
    if not _cache_enabled():
        return None
    path = _cache_file(source_id, keyword)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return None
    if _cache_ttl_seconds() > 0 and time.time() - float(payload.get("time") or 0) > _cache_ttl_seconds():
        return None
    return payload["items"]


def _cache_put(source_id: str, keyword: str, items: list) -> None:
    if not _cache_enabled():
        return
    try:
        path = _cache_file(source_id, keyword)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"time": time.time(), "source": source_id,
                        "keyword": keyword, "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        log.debug("刮削缓存写入失败：%s", exc)


def clear_scrape_cache() -> int:
    """清空刮削结果缓存，返回删除的文件数。"""
    directory = _cache_dir()
    if not directory.is_dir():
        return 0
    count = 0
    for path in directory.glob("*.json"):
        try:
            path.unlink()
            count += 1
        except OSError:
            pass
    return count


# ==========================================================================
# 源查询执行
# ==========================================================================
def _select_runtimes(source: Optional[str]) -> list:
    runtimes = _registry()
    if source and source not in ("all", ""):
        return [rt for rt in runtimes if rt.spec.id == source]
    return [rt for rt in runtimes if rt.spec.enabled]


def _call_search(fn, keyword: str, timeout: int):
    """带硬超时的源调用。

    socket 超时管不住 DNS 解析卡死（实测 Google 图书能卡 128 秒），
    所以在外面再兜一层墙钟超时，保证单个源最多拖慢这么点时间。
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn, keyword, timeout).result(timeout=timeout + 5)
    finally:
        pool.shutdown(wait=False)


def _run_source(rt: SourceRuntime, keyword: str, use_cache: bool = True,
                force: bool = False, count_failure: bool = True) -> list:
    """执行单个源的搜索：缓存 → 节流 → 熔断 → 解析，并记录状态。

    count_failure=False 用于「检测刮削源」：照实记录状态，但不把源熔断掉。
    """
    if not rt.spec.enabled:
        return []
    if not force and rt.cooling:
        rt.record("cooldown", "熔断中，暂时跳过", 0, 0.0, keyword)
        return []
    if use_cache:
        cached = _cache_get(rt.spec.id, keyword)
        if cached is not None:
            rt.cache_hits += 1
            rt.record("cached", "命中缓存", len(cached), 0.0, keyword)
            return cached
    rt.throttle_wait()
    started = time.time()
    try:
        items = _call_search(rt.spec.search, keyword, rt.spec.timeout) or []
    except FuturesTimeout:
        message = f"请求超过 {rt.spec.timeout + 5} 秒未返回，已放弃"
        rt.record("error", message, 0, time.time() - started, keyword, broken=count_failure)
        log.warning("刮削源 %s %s（%s）", rt.spec.label, message, keyword)
        return []
    except SourceBlockedError as exc:
        rt.record("blocked", str(exc), 0, time.time() - started, keyword, broken=count_failure)
        log.warning("刮削源 %s 疑似被风控（%s）：%s", rt.spec.label, keyword, exc)
        return []
    except SourceBrokenError as exc:
        rt.record("broken", str(exc), 0, time.time() - started, keyword, broken=count_failure)
        log.warning("刮削源 %s 解析失败（可能已改版）：%s", rt.spec.label, exc)
        return []
    except Exception as exc:
        rt.record("error", f"{type(exc).__name__}: {exc}", 0, time.time() - started,
                  keyword, broken=count_failure)
        log.warning("刮削源 %s 请求失败（%s）：%s", rt.spec.label, keyword, exc)
        return []
    rt.record("ok" if items else "empty", "", len(items), time.time() - started, keyword)
    _cache_put(rt.spec.id, keyword, items)
    return items


def search_candidates(
    title: str, author: str = "", limit: int = 8, source: Optional[str] = None
) -> list:
    """按指定源（或全部已启用源）并发搜索，返回带匹配分与来源标签的候选列表。

    先用原始书名搜索，若清洗后能得到不同的关键词，则再搜一轮，提升命中率。
    """
    if not title:
        return []
    runtimes = _select_runtimes(source)
    if not runtimes:
        return []

    tasks = [(rt, title) for rt in runtimes]
    alt = clean_search_title(title)
    if alt and _norm(alt) != _norm(title):
        tasks += [(rt, alt) for rt in runtimes]

    merged: dict = {rt.spec.id: [] for rt in runtimes}
    with ThreadPoolExecutor(max_workers=max(1, min(_MAX_WORKERS, len(tasks)))) as pool:
        futures = {pool.submit(_run_source, rt, kw): rt.spec.id for rt, kw in tasks}
        for fut, sid in futures.items():
            try:
                merged[sid].extend(fut.result() or [])
            except Exception as exc:      # 兜底：单个源失败不影响其他源
                log.warning("刮削源 %s 结果汇总失败：%s", sid, exc)

    out = []
    for rt in runtimes:
        seen, picked = set(), 0
        for cand in merged.get(rt.spec.id, []):
            key = (_norm(cand.get("title", "")), _norm(cand.get("author", "")))
            if key in seen:
                continue
            seen.add(key)
            cand = dict(cand)
            cand["source"] = rt.spec.id
            cand["source_label"] = rt.spec.label
            cand["score"] = round(_match_score(cand, title, author), 1)
            out.append(cand)
            picked += 1
            if picked >= limit:
                break
    return out


def scrape_book(
    title: str, author: str = "", source: Optional[str] = None
) -> Optional[dict]:
    """按书名（可带作者）刮削一本书的元信息，失败返回 None。"""
    if not title:
        return None
    return pick_best(search_candidates(title, author, source=source), title, author)


def probe_sources(keyword: str = "活着", source: Optional[str] = None) -> list:
    """逐个真实请求每个源，返回自检结果（绕过缓存与熔断）。"""
    runtimes = _select_runtimes(source) or _registry()
    results: dict = {}
    with ThreadPoolExecutor(max_workers=max(1, min(_MAX_WORKERS, len(runtimes)))) as pool:
        futures = {pool.submit(_run_source, rt, keyword, False, True, False): rt.spec.id
                   for rt in runtimes}
        for fut, sid in futures.items():
            try:
                results[sid] = fut.result() or []
            except Exception:
                results[sid] = []
    out = []
    for rt in runtimes:
        snap = rt.snapshot()
        items = results.get(rt.spec.id) or []
        sample = items[0] if items else {}
        snap.update({
            "probe_keyword": keyword,
            "probe_count": len(items),
            "sample_title": (sample.get("title") or "")[:40],
            "sample_author": (sample.get("author") or "")[:20],
            "sample_cover": "有" if sample.get("cover") else "无",
        })
        out.append(snap)
    return out


def cover_host_suffixes() -> tuple:
    """封面代理白名单：内置图床 + sources.yml 里 extra_cover_hosts 追加的域名。"""
    extra = _settings_cfg().get("extra_cover_hosts")
    if not isinstance(extra, (list, tuple)):
        return COVER_HOST_SUFFIXES
    cleaned = []
    for item in extra:
        host = str(item or "").strip().lower().lstrip(".")
        if host and host not in COVER_HOST_SUFFIXES:
            cleaned.append(host)
    return COVER_HOST_SUFFIXES + tuple(cleaned)


def list_sources(include_disabled: bool = True) -> list:
    return [rt.snapshot() for rt in _registry()
            if include_disabled or rt.spec.enabled]


def enabled_source_ids() -> list:
    return [rt.spec.id for rt in _registry() if rt.spec.enabled]


def is_valid_source(source_id: str) -> bool:
    return any(rt.spec.id == source_id for rt in _registry())


def source_label(source_id: str) -> str:
    for rt in _registry():
        if rt.spec.id == source_id:
            return rt.spec.label
    return SOURCE_LABELS.get(source_id, source_id)


def _norm(text: str) -> str:
    return re.sub(r"[\s《》【】\[\]()（）「」『』:：,，.。!！?？\-—_·・]+", "", text or "").lower()


_NAME_STOPWORD_NORMS = {_norm(w) for w in _NAME_STOPWORDS}


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


def _looks_like_author(text: str) -> bool:
    """判断一段文本是否像作者名（用于拆分"书名 作者"这类文件名）。"""
    s = (text or "").strip()
    if not (1 < len(s) <= 16):
        return False
    if re.search(r"[0-9０-９]", s):
        return False
    if re.search(
        r"(全集|全本|全文|完结|完本|番外|大结局|正文|上部|下部|上册|下册|中册|简介|内容|"
        r"第[一二三四五六七八九十百千0-9]+[章节回卷部集])", s
    ):
        return False
    if re.search(r"[，。！？；：、“”‘’《》【】()（）\[\]]", s):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z·.\- ]*", s))


def clean_search_title(title: str) -> str:
    """清理书名里的下载站噪声（全本/完结/txt/作者:xxx 等），用于二次搜索。"""
    t = (title or "").strip()
    if not t:
        return ""
    t = re.sub(r"^\s*[【\[(（][^】\])）]{1,24}[】\])）]\s*", "", t)          # 前缀站名/标签
    t = re.sub(
        r"\s*[【\[(（][^】\])）]{0,24}?(?:全本|全集|全文|完结|完本|校对|精校|未删减|无删减|txt|TXT|下载|阅读)"
        r"[^】\])）]{0,24}?[】\])）]", "", t)                                # 括号里的"（全集）"之类
    suffix_re = re.compile(
        r"\s*[-_—·]?\s*(?:全本|全集|全文|完整版|完结版|完本|完结|无删减|未删减|精校版|校对版|精校|"
        r"txt下载|txt版|txt|电子书|免费阅读|在线阅读|最新章节|笔趣阁|新笔趣阁)\s*$",
        re.I,
    )
    while True:                      # 叠加后缀（如"斗破苍穹全集txt下载"）需要反复剥离
        stripped = suffix_re.sub("", t)
        if stripped == t:
            break
        t = stripped
    t = re.sub(r"(?:\s*[-_—·]\s*|\s+)(?:作者|著者|原著|原作者)\s*[:：]\s*.+$", "", t)  # 尾部"作者：xxx"
    t = re.sub(r"\s*[（(]\s*(?:作者|著者|原著|原作者)\s*[:：]?\s*[^）)]{1,24}[）)]\s*$", "", t)
    # "作者：xxx-书名"：作者在前，取后半段作为书名
    m = re.match(r"^(?:作者|著者|原著|原作者)\s*[:：]\s*[^-_—·]{1,16}\s*[-_—·]\s*(.+)$", t)
    if m:
        t = m.group(1)
    else:
        m = re.match(r"^(.+?)\s*[-_—·]\s*([^-_—·]{2,16})$", t)             # 尾部"-作者名"
        if m and _looks_like_author(m.group(2)):
            t = m.group(1)
        else:
            m = re.match(r"^(.+?)\s+(\S{2,16})$", t)                      # 尾部" 作者名"
            if m and _looks_like_author(m.group(2)):
                t = m.group(1)
    t = re.sub(r"^《(.+)》$", r"\1", t.strip())                            # 最后再剥书名号
    return t.strip().strip("-_—· ").strip()


def guess_from_name(stem: str) -> tuple:
    """从文件名猜测书名/作者，兼容 txt 下载站常见命名。

    支持：《书名》作者：xxx / 书名-作者 / 书名_作者 / 书名（作者）/
    作者：xxx-书名 / 书名 作者 等；猜不出来时返回 ("", "")。
    """
    s = (stem or "").strip()
    if not s:
        return "", ""
    s = re.sub(r"^\s*[【\[(（][^】\])）]{1,24}[】\])）]\s*", "", s)      # 前缀站名/标签
    s = re.sub(r"(?i)^\s*(?:书名|小说名|title)\s*[:：]\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip()

    title, author = "", ""

    # 1) 《书名》作者：xxx / 《书名》 xxx
    m = re.match(r"^《(.+?)》\s*(.*)$", s)
    if m:
        title = m.group(1).strip()
        rest = re.sub(r"^(?:作者|著者|原著|原作者|作者是)\s*[:：]?\s*", "", m.group(2).strip())
        if rest and _looks_like_author(rest):
            author = rest

    # 2) 书名（作者）
    if not title:
        m = re.match(r"^(.+?)\s*[（(]\s*(?:作者\s*[:：]?\s*)?([^）)]{1,20})\s*[）)]\s*$", s)
        if m and _looks_like_author(m.group(2)):
            title, author = m.group(1).strip(), m.group(2).strip()

    # 3) 作者：xxx-书名
    if not title:
        m = re.match(r"^(?:作者|著者|原著|原作者)\s*[:：]\s*(.+?)\s*[-_—·]\s*(.+)$", s)
        if m and _looks_like_author(m.group(1)):
            author, title = m.group(1).strip(), m.group(2).strip()

    # 4) 书名 - 作者 / 书名_作者 / 书名 作者 / 书名 作者：xxx
    if not title:
        m = re.match(r"^(.+?)\s*(?:[-_—·]|\s)\s*(?:作者\s*[:：]\s*)?([^-_—·]{2,16})$", s)
        if m and _looks_like_author(m.group(2)):
            title, author = m.group(1).strip(), m.group(2).strip()

    if not title:
        title = s

    title = clean_search_title(title)
    if not title or _norm(title) in _NAME_STOPWORD_NORMS:
        return "", author
    if len(title) < 2 and not re.search(r"[A-Za-z\u4e00-\u9fff]{2}", title):
        return "", author
    return title, author


def _merge_guess(content_title: str, content_author: str, stem: str) -> tuple:
    """合并"正文开头猜测"与"文件名猜测"：正文常是广告语，此时信文件名。"""
    name_title, name_author = guess_from_name(stem)
    title, author = content_title.strip(), content_author.strip()
    if not title:
        title = name_title
    elif name_title and _sim(name_title, title) < 50.0:
        log.debug("正文猜测 %r 与文件名 %r 差异大，采用文件名", title, name_title)
        title = name_title
    if not author:
        author = name_author
    return title, author


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
        # 同一行含"书名 作者：xxx"
        if not title:
            m3 = re.match(r"^(.{1,30}?)\s*(?:作者|著者|原著|原作者)\s*[:：]\s*(.+)$", s)
            if m3:
                cand = clean_search_title(m3.group(1))
                if cand and not re.search(r"[：:]", cand):
                    title = cand
                    if not author:
                        author = m3.group(2).strip().strip("。. ")
                    continue
        if not title and len(s) <= 30:
            m2 = re.match(r"^《(.+?)》(.*)$", s)
            # 不再手工 strip("《》【】")：那会把「【玄幻】书名」拆成「玄幻】书名」
            t = (m2.group(1) + (m2.group(2) or "")) if m2 else s
            t = re.sub(r"(全集|全文|完整版|完结版|完结|无删减|下载|电子书|txt版|txt)$", "", t).strip()
            if t and not re.search(r"[：:]", t):
                title = t
    return clean_search_title(title), author


def guess_from_txt(txt_path: Path) -> tuple:
    try:
        from app.txt_reader import read_text_with_encoding
        text, _ = read_text_with_encoding(txt_path)
    except Exception:
        text = txt_path.read_text(encoding="utf-8", errors="replace")
    c_title, c_author = guess_title_author(text.splitlines())
    return _merge_guess(c_title, c_author, txt_path.stem)


def guess_from_txt_head(txt_path: Path) -> tuple:
    """只读文件头 64KB 猜测书名/作者，用于列表快速展示。"""
    try:
        with txt_path.open("rb") as fh:
            head = fh.read(65536)
    except OSError:
        return guess_from_name(txt_path.stem)
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = head.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = head.decode("gb18030", errors="replace")
    c_title, c_author = guess_title_author(text.splitlines())
    return _merge_guess(c_title, c_author, txt_path.stem)


# ==========================================================================
# 封面
# ==========================================================================
def _cover_variants(url: str) -> list:
    """同一张封面的不同尺寸地址，从大到小排列。"""
    m = re.search(r"(?:/[a-z0-9]+_)([^/]+)$", url)
    if m:
        base, name = url[: m.start(0)], m.group(1)
        # 微信读书封面尺寸：o_ 600px 原图 > x_ 500px > t6_ > b_ > m_ > s_
        return [f"{base}/{pref}_{name}" for pref in ("o", "x", "t6", "b", "m", "s")]
    if "/qdbimg/" in url:
        # 起点封面：360/600 大图优先
        return [re.sub(r"/\d{2,4}(?=$|[?#])", f"/{pref}", url) for pref in ("600", "360", "180")]
    return [url]


def _cover_headers(url: str) -> dict:
    """各图床要求的 Referer，缺了会被防盗链拦掉（豆瓣/百度百科尤其严格）。"""
    headers = {"User-Agent": UA_BROWSER}
    if "doubanio.com" in url:
        headers["Referer"] = "https://book.douban.com/"
    elif "yuewen.com" in url or "qidian.com" in url:
        headers["Referer"] = "https://m.qidian.com/"
    elif "weread.qq.com" in url or "wfqqreader" in url:
        headers["Referer"] = "https://weread.qq.com/"
    elif "bcebos.com" in url or "baidu.com" in url:
        headers["Referer"] = "https://baike.baidu.com/"
    elif "ddimg.cn" in url:
        headers["Referer"] = "https://search.dangdang.com/"
    elif "googleusercontent.com" in url or "books.google.com" in url:
        headers["Referer"] = "https://books.google.com/"
    return headers


def fetch_cover_bytes(url: str) -> Optional[bytes]:
    """取封面原始字节：自动尝试更高清尺寸并带上正确的 Referer。"""
    if not url:
        return None
    for cand in _cover_variants(url):
        try:
            req = urllib.request.Request(cand, headers=_cover_headers(cand))
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            if len(data) >= 500:
                return data
        except Exception as exc:
            log.debug("封面候选下载失败 %s：%s", cand, exc)
    return None


def download_cover(url: str, dest: Path) -> bool:
    """下载封面并统一转成 JPEG。优先尝试高清版本，保证清晰。"""
    data = fetch_cover_bytes(url)
    if not data:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    try:
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
        log.debug("封面保存失败 %s：%s", dest, exc)
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
    from app.template import effective_cover

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

    user_title, user_author = title.strip(), author.strip()
    g_title, g_author = guess_from_txt(txt_path)
    # 搜索关键词：调用方传入 > 从正文/文件名猜测
    title = user_title or g_title or stem
    author = user_author or g_author or ""
    description = description.strip()
    scraped = None
    if scrape:
        scraped = scrape_book(title, author)
        if scraped:
            # 刮削到的书名/作者是权威数据，覆盖"猜出来"的噪声书名；
            # 但用户显式填写的元信息永远优先。
            title = user_title or scraped.get("title") or g_title or stem
            author = user_author or scraped.get("author") or g_author or ""
            description = description or scraped.get("intro") or ""

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
