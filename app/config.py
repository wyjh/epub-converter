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

    @classmethod
    def from_env(cls) -> "Settings":
        def p(name: str, default: str) -> Path:
            return Path(os.environ.get(name, default))

        interval = int(os.environ.get("WATCH_INTERVAL", "15") or "15")
        force = os.environ.get("FORCE_RECONVERT", "0").lower() in ("1", "true", "yes", "on")
        dry_run = os.environ.get("DRY_RUN", "0").lower() in ("1", "true", "yes", "on")
        direct_mode = os.environ.get("DIRECT_CONVERT", "0").lower() in ("1", "true", "yes", "on")
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
