# -*- coding: utf-8 -*-
"""单本书转换编排：清洗 → 元信息 → 封面 → HTML → calibre → 校验 → 落盘。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.chapterizer import split_chapters
from app.config import Settings, sanitize_filename
from app.cover_processor import CoverError, find_cover_image, process_cover
from app.epub_builder import EpubConvertError, build_command, run_ebook_convert
from app.html_builder import build_book_html
from app.meta_loader import Meta, load_meta
from app.template import Template, TemplateError, build_working_css
from app.txt_reader import TxtReadError, clean_lines, read_text_with_encoding
from app.verifier import verify_epub

log = logging.getLogger("converter")


class ConvertError(Exception):
    pass


@dataclass
class ConvertResult:
    stem: str
    status: str            # ok / skipped / failed
    output: Optional[Path] = None
    message: str = ""
    encoding: str = ""
    chapters: int = 0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_state(output_dir: Path) -> dict:
    p = output_dir / ".processed.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(output_dir: Path, state: dict) -> None:
    (output_dir / ".processed.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _source_files_for(settings: Settings, stem: str) -> list:
    """列出该书的源文件（TXT、同名封面、同名元信息），用于转换后清理。"""
    stem = sanitize_filename(stem, 60)
    out = []
    txt = settings.input_dir / f"{stem}.txt"
    if txt.is_file():
        out.append(txt)
    for ext in (".jpg", ".jpeg", ".png"):
        p = settings.input_dir / f"{stem}{ext}"
        if p.is_file():
            out.append(p)
    for ext in (".yaml", ".yml", ".json", ".txt"):
        p = settings.meta_dir / f"{stem}{ext}"
        if p.is_file():
            out.append(p)
    return out


def cleanup_converted_sources(settings: Settings, stems: Optional[list] = None) -> dict:
    """删除已成功转换的源文件（TXT/封面/元信息），输出 EPUB 不受影响。"""
    state = _load_state(settings.output_dir)
    ok_stems = [s for s, v in state.items() if v.get("status") == "ok"]
    targets = [s for s in ok_stems if stems is None or s in stems]
    removed = []
    for stem in sorted(targets):
        for p in _source_files_for(settings, stem):
            try:
                p.unlink()
                removed.append(str(p))
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.warning("[%s] 清理失败 %s：%s", stem, p, exc)
    if removed:
        log.info("已清理 %d 个源文件", len(removed))
    return {"count": len(removed), "removed": removed}


def remove_book_sources(settings: Settings, stem: str) -> list:
    """删除书架上单本书的源文件（TXT/同名封面/同名元信息）。"""
    removed = []
    for p in _source_files_for(settings, stem):
        try:
            p.unlink()
            removed.append(str(p))
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("[%s] 移除失败 %s：%s", stem, p, exc)
    return removed


def remove_output(settings: Settings, name: str) -> bool:
    """删除输出目录里的 EPUB，并从已处理状态中移除对应记录。"""
    safe = Path(name).name
    target = settings.output_dir / safe
    if not target.is_file():
        return False
    target.unlink()
    state = _load_state(settings.output_dir)
    changed = [s for s, v in state.items() if v.get("output") == safe]
    for s in changed:
        del state[s]
    if changed:
        _save_state(settings.output_dir, state)
    return True


def clear_bookshelf(settings: Settings) -> list:
    """清空书架：删除 input 下所有 TXT 及其同名封面/元信息。"""
    removed = []
    for txt in sorted(settings.input_dir.glob("*.txt")):
        removed += remove_book_sources(settings, txt.stem)
    return removed


def clear_outputs(settings: Settings) -> list:
    """清空输出结果：删除 output 及 failed 目录下的所有 EPUB，并重置状态。"""
    removed = []
    for p in sorted(settings.output_dir.glob("*.epub")):
        p.unlink()
        removed.append(str(p))
    failed_dir = settings.output_dir / "failed"
    if failed_dir.is_dir():
        for p in sorted(failed_dir.glob("*.epub")):
            p.unlink()
            removed.append(str(p))
    state_file = settings.output_dir / ".processed.json"
    if state_file.is_file():
        state_file.unlink()
    return removed


def _workdir_for(settings: Settings, stem: str) -> Path:
    safe = sanitize_filename(stem, 60) or "book"
    d = settings.work_dir / safe
    if d.exists():
        shutil.rmtree(d)  # 仅删除本次任务的工作目录，路径经过 sanitize 校验
    d.mkdir(parents=True, exist_ok=True)
    return d


def _copy_fonts(settings: Settings, template: Template, work: Path) -> None:
    fonts_dir = work / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    missing = []
    for font in template.fonts:
        src = template.provided_font_file(font, settings.fonts_dir)
        if src is None:
            missing.append(f"{font.family} ({font.provided_file})")
            continue
        # 以模板 CSS 引用的文件名落盘，保证 CSS 与样例逐字节一致
        target_name = font.sample_file or src.name
        shutil.copy2(src, fonts_dir / target_name)
    if missing:
        raise ConvertError(f"字体缺失（请挂载 /fonts 并提供这些字体）：{', '.join(missing)}")


def convert_one(txt_path: Path, settings: Settings, template: Template, force: bool = False) -> ConvertResult:
    stem = txt_path.stem
    result = ConvertResult(stem=stem, status="failed", message="未开始")
    state = _load_state(settings.output_dir)
    digest = _sha256(txt_path)
    prev = state.get(stem, {})

    try:
        meta, meta_source = load_meta(txt_path, settings.meta_dir)
    except Exception as exc:
        result.message = f"元信息读取失败：{exc}"
        log.error("[%s] %s", stem, result.message)
        return result

    if not meta.title:
        meta.title = stem

    out_name = f"{sanitize_filename(meta.filename())}.epub"
    out_path = settings.output_dir / out_name

    if (
        not force
        and prev.get("status") == "ok"
        and prev.get("sha256") == digest
        and out_path.is_file()
    ):
        result.status = "skipped"
        result.output = out_path
        result.message = "已转换且内容无变化，跳过"
        log.info("[%s] %s", stem, result.message)
        return result

    work = _workdir_for(settings, stem)
    try:
        # 1) 编码检测 + 清洗
        text, encoding = read_text_with_encoding(txt_path)
        lines = clean_lines(text, template.cleaning)
        result.encoding = encoding
        if not lines:
            raise ConvertError("清洗后文本为空，请检查源 TXT 内容")
        log.info("[%s] 编码=%s，清洗后 %d 行", stem, encoding, len(lines))

        # 2) 章节切分
        chapters = split_chapters(
            lines,
            template.chapter_patterns,
            normalized=bool(template.cleaning.get("normalize_chapter_numbers", True)),
            fallback_title=template.cleaning.get("fallback_chapter_title", "正文"),
            drop_preamble=bool(template.cleaning.get("drop_preamble", True)),
        )
        result.chapters = len(chapters)
        log.info("[%s] 识别到 %d 章", stem, len(chapters))

        # 3) 封面
        cover_src = find_cover_image(txt_path, meta.cover, settings.input_dir)
        if cover_src is None:
            from app.scraper import make_placeholder_cover
            placeholder = settings.input_dir / f"{stem}.jpg"
            if make_placeholder_cover(meta.title or stem, placeholder, template.cover, settings.fonts_dir):
                cover_src = placeholder
                log.info("[%s] 未找到封面，已自动生成占位封面", stem)
        if cover_src is None:
            raise ConvertError("未找到封面图片且占位封面生成失败")
        cover_path = process_cover(cover_src, work / "cover.jpg", template.cover)
        log.info("[%s] 封面已标准化为 %dx%d（%s）",
                 stem, template.cover.width, template.cover.height, cover_src.name)

        # 4) 字体 + CSS（原版复制，仅替换字体 src）
        _copy_fonts(settings, template, work)
        build_working_css(template, settings.fonts_dir, work)

        # 5) HTML 源
        html_path = work / "book.html"
        html_path.write_text(
            build_book_html(meta, chapters, template.css_files,
                            intro_heading=template.toc.get("intro_page_heading", "内容简介")),
            encoding="utf-8",
        )

        # 6) 转换：默认走 calibre；本地无 calibre 时自动切换直接打包模式
        use_direct = settings.direct_mode
        if not use_direct and not settings.dry_run and shutil.which(settings.ebook_convert) is None:
            use_direct = True
            log.warning("[%s] 未找到 %s，自动切换为直接打包模式", stem, settings.ebook_convert)

        if use_direct:
            if settings.dry_run:
                log.info("[%s] DRY-RUN 直接打包：%s", stem, out_path.name)
            else:
                from app.epub_packager import pack_epub
                pack_epub(meta, chapters, template, settings, work, out_path)
                log.info("[%s] 直接打包完成（CSS/字体/封面/目录与样例一致）", stem)
        else:
            cmd = build_command(settings, template, meta, html_path, out_path, cover_path)
            run_ebook_convert(cmd, dry_run=settings.dry_run)
            if settings.dry_run:
                result.status = "ok"
                result.output = out_path
                result.message = "DRY-RUN 完成（未实际转换）"
                return result
        if not out_path.is_file():
            raise ConvertError("转换流程未生成输出文件")

        # 7) 模板一致性校验
        issues = verify_epub(out_path, template, meta, len(chapters))
        if issues:
            failed_dir = settings.output_dir / "failed"
            failed_dir.mkdir(exist_ok=True)
            target = failed_dir / out_name
            shutil.move(str(out_path), str(target))
            raise ConvertError("模板校验未通过：\n  - " + "\n  - ".join(issues))
        log.info("[%s] 模板校验通过，输出 %s", stem, out_path.name)

        state[stem] = {
            "sha256": digest,
            "output": out_name,
            "status": "ok",
            "time": datetime.now().isoformat(timespec="seconds"),
            "encoding": encoding,
            "chapters": len(chapters),
            "meta_source": str(meta_source) if meta_source else "",
        }
        _save_state(settings.output_dir, state)
        result.status = "ok"
        result.output = out_path
        result.message = f"转换成功（{len(chapters)} 章，编码 {encoding}）"
        return result
    except (ConvertError, CoverError, EpubConvertError, TxtReadError, TemplateError) as exc:
        result.message = str(exc)
        log.error("[%s] 转换失败：%s", stem, result.message)
        return result
    except Exception as exc:  # 兜底：任何异常都要有明确报错
        result.message = f"未预期错误：{exc!r}"
        log.exception("[%s] 转换失败", stem)
        return result
