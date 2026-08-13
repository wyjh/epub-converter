# -*- coding: utf-8 -*-
"""Flask Web 服务：把 TXT→EPUB 转换工具封装成带前端的后台服务。"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from app.config import Settings, sanitize_filename
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
    SOURCES,
    SOURCE_LABELS,
    download_cover,
    guess_from_txt,
    guess_from_txt_head,
    has_meta_file,
    same_stem_cover,
    search_candidates,
    prepare_book_files,
)
from app.template import load_template
from app.watcher import process_all

app = Flask(__name__, static_folder="static", static_url_path="/static")

_LOCK = threading.Lock()


@app.after_request
def _no_store(resp):
    """禁用页面缓存，避免浏览器一直用旧界面/旧连接。"""
    resp.headers["Cache-Control"] = "no-store"
    return resp

settings = Settings.from_env()
setup_logging(settings)
ensure_template(settings)
template = load_template(settings.template_dir)


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
    return jsonify({
        "ok": True,
        "template": template.data.get("template_name", ""),
        "cover": f"{template.cover.width}x{template.cover.height} q{template.cover.quality}",
        "fonts": [f.family for f in template.fonts],
        "dirs": {
            "input": str(settings.input_dir),
            "meta": str(settings.meta_dir),
            "output": str(settings.output_dir),
            "template": str(settings.template_dir),
        },
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
    title, author = guess_from_txt(txt)
    source = request.args.get("source", "all")
    if source not in SOURCES:
        source = "all"
    candidates = search_candidates(title, author, source=source)
    local_cover = same_stem_cover(settings.input_dir, stem)
    return jsonify({
        "ok": True,
        "stem": stem,
        "title": title,
        "author": author,
        "source": source,
        "sources": [{"id": s, "label": SOURCE_LABELS[s]} for s in SOURCES],
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
    title, author = guess_from_txt(txt)
    source = request.args.get("source", "all")
    if source not in SOURCES:
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
        "sources": [{"id": s, "label": SOURCE_LABELS[s]} for s in SOURCES],
        "covers": covers,
        "local_cover": local_cover.name if local_cover else "",
    })


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


@app.post("/api/scan")
def scan():
    with _LOCK:
        summary = process_all(settings, template, force=bool(request.form.get("force") == "1"))
    return jsonify(summary)


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
