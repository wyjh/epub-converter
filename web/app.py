# -*- coding: utf-8 -*-
"""Flask Web 服务：把 TXT→EPUB 转换工具封装成带前端的后台服务。"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from app.config import Settings, sanitize_filename
from app.cover_processor import CoverError, process_cover
from app.converter import (
    convert_one,
    cleanup_converted_sources,
    clear_bookshelf,
    clear_outputs,
    remove_book_sources,
    remove_output,
)
from app.main import ensure_template, setup_logging
from app.scraper import (
    clear_scrape_cache,
    configure_scrape_cache,
    cover_host_suffixes,
    default_config_path,
    download_cover,
    fetch_cover_bytes,
    is_valid_source,
    list_sources,
    probe_sources,
    reload_sources_config,
    guess_from_txt,
    guess_from_txt_head,
    has_meta_file,
    same_stem_cover,
    search_candidates,
    prepare_book_files,
)
from app.template import effective_cover, list_font_files, load_template
from app.watcher import process_all

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 封面/文本上传上限 32MB

_LOCK = threading.Lock()


@app.after_request
def _no_store(resp):
    """禁用页面缓存，避免浏览器一直用旧界面/旧连接；封面代理除外（要缓存）。"""
    if request.path.startswith("/api/cover/proxy"):
        resp.headers["Cache-Control"] = "public, max-age=86400"
    else:
        resp.headers["Cache-Control"] = "no-store"
    return resp

settings = Settings.from_env()
# 目录参数可能是相对路径（本地开发用 ./input 这种），而 Flask 的 send_file 会按
# 应用目录解析相对路径、与进程 CWD 不一致，所以这里统一转成绝对路径。
for _attr in ("input_dir", "meta_dir", "fonts_dir", "output_dir",
              "template_dir", "work_dir", "logs_dir"):
    setattr(settings, _attr, Path(getattr(settings, _attr)).resolve())
setup_logging(settings)
configure_scrape_cache(settings.work_dir / "scrape_cache")
ensure_template(settings)
template = load_template(settings.template_dir)

PREFS_PATH = settings.output_dir / ".prefs.json"


def _load_prefs() -> dict:
    try:
        data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_prefs(data: dict) -> None:
    try:
        PREFS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _apply_prefs() -> None:
    """把 Web 端保存的设置恢复回来；环境变量优先，未设置时用 prefs。"""
    prefs = _load_prefs()
    if not settings.font_file and prefs.get("font_file"):
        settings.font_file = str(prefs["font_file"])
    if not settings.cover_width and prefs.get("cover_width"):
        try:
            settings.cover_width = int(prefs["cover_width"])
        except (TypeError, ValueError):
            pass
    if not settings.cover_height and prefs.get("cover_height"):
        try:
            settings.cover_height = int(prefs["cover_height"])
        except (TypeError, ValueError):
            pass


_apply_prefs()


def _source_options() -> list:
    """给前端下拉框用的源列表：只列已启用的。"""
    return [{"id": s["id"], "label": s["label"], "note": s.get("note", "")}
            for s in list_sources(include_disabled=False)]


def _tail_log(n: int = 200) -> str:
    p = settings.logs_dir / "conversion.log"
    if not p.is_file():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def _unique_path(directory: Path, stem: str, ext: str) -> Path:
    safe = sanitize_filename(stem, 60) or "book"
    cand = directory / f"{safe}{ext}"
    i = 1
    while cand.exists():
        cand = directory / f"{safe}-{i}{ext}"
        i += 1
    return cand


def _build_meta_yaml(base_stem: str, form) -> Path:
    data = {
        "title": (form.get("title") or base_stem).strip(),
        "author": (form.get("author") or "").strip(),
        "description": (form.get("description") or "").strip(),
    }
    tags = (form.get("tags") or "").strip()
    if tags:
        data["tags"] = [t.strip() for t in re.split(r"[,\s，、]+", tags) if t.strip()]
    for key in ("publisher", "series"):
        if form.get(key):
            data[key] = form.get(key).strip()
    if form.get("series_index"):
        try:
            data["series_index"] = float(form.get("series_index"))
        except (TypeError, ValueError):
            pass
    if form.get("lang"):
        data["lang"] = form.get("lang").strip()
    path = settings.meta_dir / f"{base_stem}.yaml"
    import yaml
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _split_tags(raw: str) -> list:
    return [t.strip() for t in re.split(r"[,\s，、]+", raw or "") if t.strip()]


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    cover_spec = effective_cover(template, settings)
    return jsonify({
        "ok": True,
        "template": template.data.get("template_name", ""),
        "cover": f"{cover_spec.width}x{cover_spec.height} q{cover_spec.quality}",
        "cover_default": f"{template.cover.width}x{template.cover.height}",
        "fonts": [f.family for f in template.fonts],
        "font_files": list_font_files(settings.fonts_dir),
        "font_file": settings.font_file,
        "sources": _source_options(),
        "dirs": {
            "input": str(settings.input_dir),
            "meta": str(settings.meta_dir),
            "output": str(settings.output_dir),
            "template": str(settings.template_dir),
        },
    })


@app.get("/api/settings")
def settings_get():
    cover_spec = effective_cover(template, settings)
    return jsonify({
        "ok": True,
        "cover_width": cover_spec.width,
        "cover_height": cover_spec.height,
        "cover_default": f"{template.cover.width}x{template.cover.height}",
        "font_file": settings.font_file,
        "fonts": list_font_files(settings.fonts_dir),
        "fonts_dir": str(settings.fonts_dir),
    })


@app.post("/api/settings")
def settings_save():
    """保存转换设置：封面分辨率 + 嵌入字体。"""
    data = request.get_json(silent=True) or request.form
    font_file = (data.get("font_file") or "").strip()
    if font_file and font_file not in list_font_files(settings.fonts_dir):
        return jsonify({"ok": False, "message": "字体文件不存在于 fonts 目录"}), 400
    try:
        cover_w = int(data.get("cover_width") or 0)
        cover_h = int(data.get("cover_height") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "分辨率必须是整数"}), 400
    if cover_w or cover_h:
        if cover_w <= 0 or cover_h <= 0 or cover_w > 4000 or cover_h > 4000:
            return jsonify({"ok": False, "message": "分辨率数值不合理（1~4000）"}), 400
    settings.font_file = font_file
    settings.cover_width = cover_w
    settings.cover_height = cover_h
    _save_prefs({
        "font_file": font_file,
        "cover_width": cover_w,
        "cover_height": cover_h,
    })
    cover_spec = effective_cover(template, settings)
    return jsonify({
        "ok": True,
        "cover": f"{cover_spec.width}x{cover_spec.height}",
        "font_file": font_file,
    })


@app.get("/api/books")
def books():
    def entry(p: Path) -> dict:
        st = p.stat()
        return {
            "name": p.name,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        }

    outputs = sorted(settings.output_dir.glob("*.epub"), key=lambda p: p.stat().st_mtime, reverse=True)
    failed_dir = settings.output_dir / "failed"
    failed = sorted(failed_dir.glob("*.epub"), key=lambda p: p.stat().st_mtime, reverse=True) if failed_dir.is_dir() else []
    pending = []
    for p in sorted(settings.input_dir.glob("*.txt")):
        title, author = guess_from_txt_head(p)
        pending.append({
            "name": p.name,
            "stem": p.stem,
            "title": title,
            "author": author,
            "has_meta": has_meta_file(settings.meta_dir, p.stem),
            "has_cover": same_stem_cover(settings.input_dir, p.stem) is not None,
        })
    return jsonify({
        "outputs": [entry(p) for p in outputs],
        "failed": [entry(p) for p in failed],
        "pending": pending,
        "counts": {"ok": len(outputs), "failed": len(failed), "pending": len(pending)},
    })


@app.get("/cover/<path:name>")
def cover_file(name: str):
    """提供 input 目录里的封面文件，供页面预览。"""
    target = (settings.input_dir / Path(name).name).resolve()
    out_root = settings.input_dir.resolve()
    if not str(target).startswith(str(out_root) + os.sep):
        abort(404)
    if not target.is_file():
        abort(404)
    return send_file(target)


@app.post("/api/import")
def import_txt():
    """导入 TXT 到 input 目录（不转换），返回文件名/猜测的书名作者。"""
    files = request.files.getlist("txt")
    if not files:
        return jsonify({"ok": False, "message": "请选择至少一个 TXT 文件"}), 400
    items = []
    with _LOCK:
        for f in files:
            if not f or not f.filename:
                continue
            base_stem = sanitize_filename(Path(f.filename).stem, 60) or "book"
            txt_path = _unique_path(settings.input_dir, base_stem, ".txt")
            f.save(txt_path)
            title, author = guess_from_txt(txt_path)
            items.append({
                "name": f.filename,
                "stem": txt_path.stem,
                "title": title,
                "author": author,
            })
    return jsonify({"ok": True, "items": items})


@app.get("/api/scrape/<stem>")
def scrape_stem(stem: str):
    """按书名（从 TXT 猜测）搜索元信息候选，供用户选择。"""
    stem = sanitize_filename(stem, 60)
    txt = settings.input_dir / f"{stem}.txt"
    if not txt.is_file():
        return jsonify({"ok": False, "message": "找不到该 TXT"}), 404
    g_title, g_author = guess_from_txt(txt)
    # 允许前端传入手动修正过的书名/作者再搜一次（猜错书名时最有用）
    title = (request.args.get("title") or "").strip() or g_title
    author = (request.args.get("author") or "").strip() or g_author
    source = request.args.get("source", "all")
    if source != "all" and not is_valid_source(source):
        source = "all"
    candidates = search_candidates(title, author, source=source)
    local_cover = same_stem_cover(settings.input_dir, stem)
    return jsonify({
        "ok": True,
        "stem": stem,
        "title": title,
        "author": author,
        "source": source,
        "sources": _source_options(),
        "candidates": candidates,
        "local_cover": local_cover.name if local_cover else "",
    })


@app.get("/api/covers/<stem>")
def covers_stem(stem: str):
    """返回候选封面列表（多个来源），供单独刮封面选择。"""
    stem = sanitize_filename(stem, 60)
    txt = settings.input_dir / f"{stem}.txt"
    if not txt.is_file():
        return jsonify({"ok": False, "message": "找不到该 TXT"}), 404
    g_title, g_author = guess_from_txt(txt)
    # 允许前端传入手动修正过的书名/作者再搜一次（猜错书名时最有用）
    title = (request.args.get("title") or "").strip() or g_title
    author = (request.args.get("author") or "").strip() or g_author
    source = request.args.get("source", "all")
    if source != "all" and not is_valid_source(source):
        source = "all"
    candidates = search_candidates(title, author, source=source)
    seen = set()
    covers = []
    for c in candidates:
        url = (c.get("cover") or "").strip()
        if url and url not in seen:
            seen.add(url)
            covers.append({
                "url": url,
                "title": c.get("title", ""),
                "author": c.get("author", ""),
                "source_label": c.get("source_label", ""),
            })
    local_cover = same_stem_cover(settings.input_dir, stem)
    return jsonify({
        "ok": True,
        "stem": stem,
        "source": source,
        "sources": _source_options(),
        "covers": covers,
        "local_cover": local_cover.name if local_cover else "",
    })


COVER_PREVIEW_MAX_EDGE = 480          # 页面预览用的最大边长（原图下载不受影响）
COVER_CACHE_DIRNAME = "cover_cache"


def _cover_cache_file(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return settings.work_dir / COVER_CACHE_DIRNAME / f"{digest}.jpg"


def _render_cover_preview(data: bytes) -> bytes:
    """把封面转成小尺寸 JPEG 供页面预览，避免几 MB 的原图拖慢候选列表。"""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as img:
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, rgba).convert("RGB")
        else:
            img = img.convert("RGB")
        if max(img.size) > COVER_PREVIEW_MAX_EDGE:
            img.thumbnail((COVER_PREVIEW_MAX_EDGE, COVER_PREVIEW_MAX_EDGE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue()


@app.get("/api/cover/proxy")
def cover_proxy():
    """封面预览代理。

    豆瓣、百度百科的图床会检查 Referer，浏览器 <img> 直连一律 403（页面上就是一片空白），
    所以候选封面统一由后端带正确 Referer 取回；顺带缩成小图并缓存。
    """
    raw = (request.args.get("url") or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host:
        return jsonify({"ok": False, "message": "封面地址不合法"}), 400
    # 白名单：只允许已知书封图床，避免这个接口被当成任意 URL 代理
    if not any(host == suffix or host.endswith("." + suffix) for suffix in cover_host_suffixes()):
        return jsonify({"ok": False, "message": "不允许代理该域名的图片"}), 403

    cache = _cover_cache_file(raw)
    if not cache.is_file():
        data = fetch_cover_bytes(raw)
        if not data:
            return jsonify({"ok": False, "message": "封面获取失败"}), 502
        try:
            data = _render_cover_preview(data)
        except Exception as exc:
            logging.getLogger("web").warning("封面预览处理失败 %s：%s", raw, exc)
            return jsonify({"ok": False, "message": "封面无法解析"}), 502
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
        except OSError as exc:
            logging.getLogger("web").debug("封面缓存写入失败：%s", exc)
    return send_file(cache, mimetype="image/jpeg", max_age=86400)


@app.post("/api/cover/save")
def cover_save():
    """下载用户选中的封面到 input/<stem>.jpg。"""
    data = request.get_json(silent=True) or request.form
    stem = sanitize_filename(str(data.get("stem", "")), 60)
    url = (data.get("url") or "").strip()
    if not stem or not url:
        return jsonify({"ok": False, "message": "缺少 stem 或封面 URL"}), 400
    dest = settings.input_dir / f"{stem}.jpg"
    ok = download_cover(url, dest)
    return jsonify({"ok": ok, "cover": dest.name if ok else None,
                    "message": "" if ok else "封面下载失败"})


COVER_UPLOAD_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")


@app.post("/api/cover/upload")
def cover_upload():
    """上传自定义封面：按项目当前分辨率居中裁切 + 缩放 + 压缩后替换该书封面。"""
    stem = sanitize_filename(str(request.form.get("stem", "")), 60)
    if not stem:
        return jsonify({"ok": False, "message": "缺少 stem"}), 400
    txt = settings.input_dir / f"{stem}.txt"
    if not txt.is_file():
        return jsonify({"ok": False, "message": "找不到该 TXT"}), 404
    upload = request.files.get("cover")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "message": "请选择要上传的封面图片"}), 400
    ext = Path(upload.filename).suffix.lower()
    if ext not in COVER_UPLOAD_EXTS:
        return jsonify({"ok": False, "message": "封面格式不支持（请用 JPG/PNG/WebP/BMP）"}), 400

    spec = effective_cover(template, settings)
    tmp_dir = settings.work_dir / "_cover_upload"
    tmp = tmp_dir / f"{stem}{ext}"
    dest = settings.input_dir / f"{stem}.jpg"
    try:
        with _LOCK:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            upload.save(tmp)
            process_cover(tmp, dest, spec)          # 尺寸/比例/质量全部按模板锁定
            # 清掉同名旧封面，避免新旧封面同时存在导致下次转换又用回旧图
            for other_ext in (".png", ".jpeg"):
                stale = settings.input_dir / f"{stem}{other_ext}"
                if stale.is_file():
                    stale.unlink()
            meta_path = settings.meta_dir / f"{stem}.yaml"
            if meta_path.is_file():
                import yaml
                data = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    data["cover"] = dest.name
                    meta_path.write_text(
                        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                        encoding="utf-8",
                    )
    except CoverError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "message": f"封面上传失败：{exc}"}), 500
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    logging.getLogger("web").info(
        "[%s] 已上传自定义封面并调整为 %dx%d", stem, spec.width, spec.height
    )
    return jsonify({
        "ok": True,
        "cover": dest.name,
        "width": spec.width,
        "height": spec.height,
        "message": f"封面已按 {spec.width}×{spec.height} 自动调整",
    })


@app.post("/api/meta/save")
def meta_save():
    """保存用户在刮削面板中确认/编辑的元信息。"""
    data = request.get_json(silent=True) or request.form
    stem = sanitize_filename(str(data.get("stem", "")), 60)
    txt = settings.input_dir / f"{stem}.txt"
    if not txt.is_file():
        return jsonify({"ok": False, "message": "找不到该 TXT"}), 404
    title = (data.get("title") or stem).strip()
    author = (data.get("author") or "").strip()
    description = (data.get("description") or "").strip()
    tags = _split_tags(data.get("tags"))
    publisher = (data.get("publisher") or "").strip()
    series = (data.get("series") or "").strip()
    cover_url = (data.get("cover_url") or "").strip()

    cover_name = ""
    if cover_url:
        dest = settings.input_dir / f"{stem}.jpg"
        if download_cover(cover_url, dest):
            cover_name = dest.name
    local_cover = same_stem_cover(settings.input_dir, stem)
    if not cover_name and local_cover is not None:
        cover_name = local_cover.name

    meta = {"title": title, "author": author, "description": description}
    if tags:
        meta["tags"] = tags
    if publisher:
        meta["publisher"] = publisher
    if series:
        meta["series"] = series
    if cover_name:
        meta["cover"] = cover_name

    import yaml
    settings.meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = settings.meta_dir / f"{stem}.yaml"
    meta_path.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return jsonify({"ok": True, "stem": stem, "meta": meta,
                    "cover": cover_name, "meta_path": str(meta_path)})


@app.post("/api/convert/one")
def convert_one_api():
    """转换书架上指定的一本 TXT。"""
    data = request.get_json(silent=True) or request.form
    stem = sanitize_filename(str(data.get("stem", "")), 60)
    txt = settings.input_dir / f"{stem}.txt"
    if not txt.is_file():
        return jsonify({"ok": False, "message": "找不到该 TXT"}), 404
    with _LOCK:
        res = convert_one(txt, settings, template, force=True)
    payload = {
        "ok": res.status == "ok",
        "status": res.status,
        "message": res.message,
        "chapters": res.chapters,
        "encoding": res.encoding,
        "output": res.output.name if res.output else None,
    }
    return jsonify(payload), 200 if res.status != "failed" else 422


@app.get("/api/sources")
def sources_list_api():
    """列出全部刮削源（含被禁用的）与各自最近一次请求的状态。"""
    return jsonify({
        "ok": True,
        "sources": list_sources(include_disabled=True),
        "config_file": str(default_config_path()),
    })


@app.get("/api/sources/health")
def sources_health_api():
    """逐个真实请求每个源做自检：哪个源还活着、返回了什么样本。"""
    keyword = (request.args.get("keyword") or "活着").strip() or "活着"
    source = request.args.get("source") or "all"
    if source != "all" and not is_valid_source(source):
        source = "all"
    results = probe_sources(keyword, source=source)
    bad_states = ("blocked", "broken", "error", "cooldown")
    return jsonify({
        "ok": True,
        "keyword": keyword,
        "time": datetime.now().isoformat(timespec="seconds"),
        "results": results,
        "ok_count": sum(1 for r in results if r.get("state") in ("ok", "cached")),
        "empty_count": sum(1 for r in results if r.get("state") == "empty"),
        "bad_count": sum(1 for r in results if r.get("state") in bad_states),
    })


@app.post("/api/sources/reload")
def sources_reload_api():
    """重新读取 sources.yml（改完配置不用重启服务）。"""
    reload_sources_config()
    return jsonify({
        "ok": True,
        "sources": _source_options(),
        "all_sources": list_sources(include_disabled=True),
        "config_file": str(default_config_path()),
    })


@app.post("/api/sources/cache/clear")
def sources_cache_clear_api():
    """清空刮削结果缓存。"""
    return jsonify({"ok": True, "removed": clear_scrape_cache()})


@app.post("/api/scan")
def scan():
    with _LOCK:
        summary = process_all(settings, template, force=bool(request.form.get("force") == "1"))
    return jsonify(summary)


@app.post("/api/scrape/batch")
def scrape_batch():
    """批量刮削书架：默认处理 input 下所有 TXT，已有元信息的直接跳过。"""
    wanted = [s.strip() for s in (request.form.get("stems") or "").split(",") if s.strip()]
    txts = sorted(settings.input_dir.glob("*.txt"))
    if wanted:
        txts = [t for t in txts if t.stem in wanted]
    results, skipped = [], 0
    with _LOCK:
        for txt in txts:
            if has_meta_file(settings.meta_dir, txt.stem):
                skipped += 1
                continue
            try:
                prep = prepare_book_files(txt, settings, template, scrape=True)
            except Exception as exc:
                results.append({"stem": txt.stem, "ok": False, "message": str(exc)})
                continue
            cover = prep.get("cover_path")
            results.append({
                "stem": txt.stem,
                "ok": True,
                "title": prep.get("title", ""),
                "author": prep.get("author", ""),
                "scraped": bool(prep.get("scraped")),
                "cover": cover.name if cover else "",
            })
    ok = sum(1 for r in results if r.get("ok"))
    return jsonify({
        "ok": True,
        "total": len(txts),
        "scraped": ok,
        "scraped_online": sum(1 for r in results if r.get("scraped")),
        "skipped": skipped,
        "failed": len(results) - ok,
        "results": results,
    })


@app.post("/api/convert/upload")
def upload_convert():
    txt_file = request.files.get("txt")
    if txt_file is None or not txt_file.filename:
        return jsonify({"ok": False, "message": "请选择要转换的 TXT 文件"}), 400

    base_stem = sanitize_filename(Path(txt_file.filename).stem, 60) or "book"
    try:
        with _LOCK:
            txt_path = _unique_path(settings.input_dir, base_stem, ".txt")
            txt_file.save(txt_path)

            cover_file = request.files.get("cover")
            if cover_file is not None and cover_file.filename:
                ext = Path(cover_file.filename).suffix.lower() or ".jpg"
                if ext not in (".jpg", ".jpeg", ".png"):
                    ext = ".jpg"
                cover_path = _unique_path(settings.input_dir, txt_path.stem, ext)
                cover_file.save(cover_path)

            meta_file = request.files.get("meta")
            meta_provided = meta_file is not None and meta_file.filename
            scrape = request.form.get("scrape", "1") == "1"
            title = (request.form.get("title") or "").strip()
            author = (request.form.get("author") or "").strip()
            description = (request.form.get("description") or "").strip()
            publisher = (request.form.get("publisher") or "").strip()
            tags = _split_tags(request.form.get("tags"))

            if meta_provided:
                meta_path = settings.meta_dir / f"{base_stem}.{Path(meta_file.filename).suffix.lstrip('.') or 'yaml'}"
                meta_path = _unique_path(settings.meta_dir, base_stem, meta_path.suffix)
                meta_file.save(meta_path)
                meta_path.rename(settings.meta_dir / f"{txt_path.stem}{meta_path.suffix}")
            elif scrape:
                prepare_book_files(
                    txt_path, settings, template,
                    title=title, author=author, description=description,
                    tags=tags, publisher=publisher, scrape=True,
                )
            else:
                _build_meta_yaml(txt_path.stem, request.form)

            result = convert_one(txt_path, settings, template, force=True)
    except Exception as exc:
        return jsonify({"ok": False, "message": f"转换异常：{exc}"}), 500

    payload = {
        "ok": result.status == "ok",
        "status": result.status,
        "message": result.message,
        "chapters": result.chapters,
        "encoding": result.encoding,
        "output": result.output.name if result.output else None,
    }
    return jsonify(payload), 200 if result.status != "failed" else 422


@app.post("/api/convert/batch")
def convert_batch():
    """批量导入多个 TXT：逐个刮削元信息并转换，可选转换后清理源文件。"""
    files = request.files.getlist("txt")
    if not files:
        return jsonify({"ok": False, "message": "请选择至少一个 TXT 文件"}), 400
    scrape = request.form.get("scrape", "1") == "1"
    auto_clean = request.form.get("cleanup", "0") == "1"
    results = []
    with _LOCK:
        for f in files:
            if not f or not f.filename:
                continue
            base_stem = sanitize_filename(Path(f.filename).stem, 60) or "book"
            txt_path = _unique_path(settings.input_dir, base_stem, ".txt")
            f.save(txt_path)
            real_stem = txt_path.stem
            prep = None
            if scrape:
                prep = prepare_book_files(txt_path, settings, template, scrape=True)
            res = convert_one(txt_path, settings, template, force=True)
            results.append({
                "name": f.filename,
                "stem": real_stem,
                "status": res.status,
                "message": res.message,
                "chapters": res.chapters,
                "encoding": res.encoding,
                "output": res.output.name if res.output else None,
                "scraped": bool(prep and prep.get("scraped")),
                "scrape_title": (prep or {}).get("title", ""),
                "scrape_author": (prep or {}).get("author", ""),
            })
        cleaned = []
        if auto_clean:
            ok_stems = [r["stem"] for r in results if r["status"] == "ok" and r.get("stem")]
            cleaned = cleanup_converted_sources(settings, ok_stems).get("removed", [])
    ok_count = sum(1 for r in results if r["status"] == "ok")
    failed = [r for r in results if r["status"] != "ok"]
    return jsonify({
        "ok": ok_count == len(results),
        "ok_count": ok_count,
        "failed_count": len(failed),
        "failed_stems": [r["name"] for r in failed],
        "results": results,
        "cleaned": cleaned,
    })


@app.post("/api/cleanup")
def cleanup():
    """删除已成功转换的源文件；stems 为空时清理全部已转换的源文件。"""
    stems_raw = request.form.get("stems", "")
    stems = [s.strip() for s in stems_raw.split(",") if s.strip()] or None
    with _LOCK:
        out = cleanup_converted_sources(settings, stems)
    return jsonify({"ok": True, "count": out["count"], "removed": out["removed"]})


@app.post("/api/remove")
def remove_book():
    """从书架移除单本书：删除 TXT/同名封面/同名元信息，输出 EPUB 不受影响。"""
    data = request.get_json(silent=True) or request.form
    stem = sanitize_filename(str(data.get("stem", "")), 60)
    if not stem:
        return jsonify({"ok": False, "message": "缺少 stem"}), 400
    with _LOCK:
        removed = remove_book_sources(settings, stem)
    return jsonify({"ok": True, "removed": removed, "count": len(removed)})


@app.post("/api/output/remove")
def output_remove():
    """删除输出结果里的单个 EPUB 文件。"""
    data = request.get_json(silent=True) or request.form
    name = str(data.get("name", ""))
    if not name:
        return jsonify({"ok": False, "message": "缺少文件名"}), 400
    with _LOCK:
        ok = remove_output(settings, name)
    if not ok:
        return jsonify({"ok": False, "message": "文件不存在"}), 404
    return jsonify({"ok": True, "removed": name})


@app.post("/api/clear/bookshelf")
def clear_bookshelf_api():
    """一键清空书架（删除 input 下所有 TXT/封面/元信息）。"""
    with _LOCK:
        removed = clear_bookshelf(settings)
    return jsonify({"ok": True, "count": len(removed), "removed": removed})


@app.post("/api/clear/output")
def clear_output_api():
    """一键清空输出结果（删除 output 与 failed 下的所有 EPUB）。"""
    with _LOCK:
        removed = clear_outputs(settings)
    return jsonify({"ok": True, "count": len(removed), "removed": removed})


@app.get("/api/download/<path:name>")
def download(name: str):
    target = (settings.output_dir / Path(name).name).resolve()
    out_root = settings.output_dir.resolve()
    if not str(target).startswith(str(out_root) + os.sep):
        abort(404)
    if not target.is_file():
        abort(404)
    return send_file(target, as_attachment=True, download_name=target.name)


@app.get("/api/logs")
def logs():
    n = request.args.get("tail", default=200, type=int)
    return jsonify({"log": _tail_log(max(10, min(n, 2000)))})


if __name__ == "__main__":
    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_PORT", "8080"))
    app.run(host=host, port=port, debug=False, threaded=True)
