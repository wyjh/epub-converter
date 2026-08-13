# -*- coding: utf-8 -*-
"""固化模板加载：读取 template/template.yml 与原版 CSS，校验字段完整性。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


class TemplateError(Exception):
    pass


@dataclass
class CoverSpec:
    width: int
    height: int
    quality: int = 90
    fmt: str = "jpeg"
    color_mode: str = "RGB"


@dataclass
class FontSpec:
    family: str
    weight: str
    sample_file: str
    provided_file: str


@dataclass
class Template:
    path: Path
    data: dict
    css_files: list = field(default_factory=list)

    @property
    def cover(self) -> CoverSpec:
        c = self.data.get("cover", {})
        return CoverSpec(
            width=int(c.get("width", 440)),
            height=int(c.get("height", 578)),
            quality=int(c.get("quality", 90)),
            fmt=c.get("format", "jpeg"),
            color_mode=c.get("color_mode", "RGB"),
        )

    @property
    def fonts(self) -> list:
        out = []
        for f in self.data.get("fonts", {}).get("embedded", []):
            out.append(FontSpec(
                family=f.get("family", ""),
                weight=f.get("weight", "normal"),
                sample_file=f.get("sample_file", ""),
                provided_file=f.get("provided_file", ""),
            ))
        return out

    @property
    def cleaning(self) -> dict:
        return self.data.get("text_cleaning", {}) or {}

    @property
    def chapter_patterns(self) -> list:
        return self.cleaning.get("chapter_patterns", [])

    @property
    def junk_patterns(self) -> list:
        return self.cleaning.get("junk_patterns", [])

    @property
    def toc(self) -> dict:
        return self.data.get("toc", {}) or {}

    def css_path(self, name: str) -> Path:
        p = self.path / name
        if not p.is_file():
            raise TemplateError(f"模板缺少样式文件：{p}")
        return p

    def provided_font_file(self, font: FontSpec, fonts_dir: Path) -> Optional[Path]:
        if not font.provided_file:
            return None
        cand = fonts_dir / font.provided_file
        if cand.is_file():
            return cand
        for f in fonts_dir.iterdir():
            if f.name.lower() == font.provided_file.lower():
                return f
        return None


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        raise TemplateError("缺少 PyYAML 依赖，无法读取 template.yml")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise TemplateError(f"模板格式错误：{path} 顶层必须是键值结构")
    return data


def load_template(template_dir: Path) -> Template:
    yml = template_dir / "template.yml"
    if not yml.is_file():
        raise TemplateError(f"未找到固化模板 {yml}，请先运行模板提取工具，或挂载 /template")
    data = _load_yaml(yml)
    css = data.get("fonts", {}).get("css_files") or ["stylesheet.css", "page_styles1.css"]
    return Template(path=template_dir, data=data, css_files=list(css))


def build_working_css(template: Template, fonts_dir: Path, dest: Path) -> None:
    """把模板的三份 CSS 逐字复制到工作目录，不做任何改写，保证与样例字节一致。"""
    dest.mkdir(parents=True, exist_ok=True)
    for name in template.css_files:
        content = template.css_path(name).read_text(encoding="utf-8", errors="replace")
        (dest / name).write_text(content, encoding="utf-8")
