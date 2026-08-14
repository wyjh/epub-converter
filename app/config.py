# -*- coding: utf-8 -*-
"""运行配置：目录与参数全部来自环境变量，容器内默认值对齐挂载约定。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    input_dir: Path
    meta_dir: Path
    fonts_dir: Path
    output_dir: Path
    template_dir: Path
    work_dir: Path
    logs_dir: Path
    watch_interval: int = 15
    force_reconvert: bool = False
    ebook_convert: str = "ebook-convert"
    dry_run: bool = False
    direct_mode: bool = False
    font_file: str = ""            # 指定嵌入的字体文件名（fonts 目录内），空则用模板默认
    cover_width: int = 0           # 封面宽度覆盖（0 = 模板默认）
    cover_height: int = 0          # 封面高度覆盖（0 = 模板默认）

    @classmethod
    def from_env(cls) -> "Settings":
        def p(name: str, default: str) -> Path:
            return Path(os.environ.get(name, default))

        interval = int(os.environ.get("WATCH_INTERVAL", "15") or "15")
        force = os.environ.get("FORCE_RECONVERT", "0").lower() in ("1", "true", "yes", "on")
        dry_run = os.environ.get("DRY_RUN", "0").lower() in ("1", "true", "yes", "on")
        direct_mode = os.environ.get("DIRECT_CONVERT", "0").lower() in ("1", "true", "yes", "on")
        def _int_env(name: str) -> int:
            try:
                return int(os.environ.get(name, "0") or 0)
            except (TypeError, ValueError):
                return 0
        output_dir = p("OUTPUT_DIR", "/output")
        return cls(
            input_dir=p("INPUT_DIR", "/input"),
            meta_dir=p("META_DIR", "/meta"),
            fonts_dir=p("FONTS_DIR", "/fonts"),
            output_dir=output_dir,
            template_dir=p("TEMPLATE_DIR", "/template"),
            work_dir=p("WORK_DIR", "/tmp/epub-work"),
            logs_dir=p("LOGS_DIR", str(output_dir / "logs")),
            watch_interval=max(3, interval),
            force_reconvert=force,
            ebook_convert=os.environ.get("EBOOK_CONVERT", "ebook-convert"),
            dry_run=dry_run,
            direct_mode=direct_mode,
            font_file=os.environ.get("FONT_FILE", "") or "",
            cover_width=_int_env("COVER_WIDTH"),
            cover_height=_int_env("COVER_HEIGHT"),
        )

    def ensure_dirs(self) -> None:
        for d in (self.input_dir, self.meta_dir, self.fonts_dir,
                  self.output_dir, self.template_dir, self.work_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """去掉文件系统非法字符，得到安全的输出文件名。"""
    bad = '\\/:*?"<>|\x00'
    out = "".join("_" if c in bad else c for c in name).strip().strip(".")
    out = " ".join(out.split())
    return out[:max_len].rstrip()
