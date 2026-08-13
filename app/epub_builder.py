# -*- coding: utf-8 -*-
"""调用 calibre ebook-convert 执行转换；所有样式参数来自固化模板，不做启发式调整。"""

from __future__ import annotations

import html
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from app.config import Settings
from app.meta_loader import Meta
from app.template import Template

log = logging.getLogger("converter")


class EpubConvertError(Exception):
    pass


def description_html(meta: Meta) -> str:
    paras = [html.escape(p) for p in meta.description_paragraphs()]
    if not paras:
        return ""
    return "<div>" + "".join(f"<p>{p}</p>" for p in paras) + "<p> </p></div>"


def build_command(
    settings: Settings,
    template: Template,
    meta: Meta,
    html_path: Path,
    out_path: Path,
    cover_path: Path,
) -> list:
    toc = template.toc
    cmd = [
        settings.ebook_convert,
        str(html_path),
        str(out_path),
        "--input-encoding", "utf-8",
        "--language", meta.lang or "zh",
        "--title", meta.title or "未命名",
        "--authors", meta.author or "佚名",
        "--cover", str(cover_path),
        "--embed-all-fonts",
        "--disable-font-rescaling",
        "--chapter", toc.get("chapter_xpath", "//h:h2"),
        "--level1-toc", toc.get("heading_xpath", "//h:h1 | //h:h2"),
        "--max-toc-links", "0",
        "--epub-version", "2",
    ]
    desc = description_html(meta)
    if desc:
        cmd += ["--comments", desc]
    if meta.tags:
        cmd += ["--tags", ",".join(meta.tags)]
    if meta.publisher:
        cmd += ["--publisher", meta.publisher]
    if meta.series:
        cmd += ["--series", meta.series, "--series-index", str(meta.series_index)]
    return cmd


def run_ebook_convert(cmd: list, dry_run: bool = False) -> subprocess.CompletedProcess:
    if dry_run:
        log.info("DRY-RUN 转换命令：%s", " ".join(f'"{c}"' if " " in c else c for c in cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    if shutil.which(cmd[0]) is None:
        raise EpubConvertError(
            f"找不到 {cmd[0]}。容器内请确认已安装 calibre；本机调试请安装 calibre 或使用 --dry-run。"
        )
    log.info("执行：%s %s ...", cmd[0], " ".join(str(c) for c in cmd[1:6]))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise EpubConvertError(f"无法启动 {cmd[0]}，请确认 calibre 已安装")
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-25:])
        raise EpubConvertError(f"ebook-convert 转换失败（退出码 {proc.returncode}）：\n{tail}")
    return proc
