# -*- coding: utf-8 -*-
"""元信息读取：读取 /meta 下与 TXT 同名的配置文件（YAML 首选，兼容 JSON/INI）。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class MetaError(Exception):
    pass


@dataclass
class Meta:
    title: str
    author: str = ""
    description: str = ""
    tags: list = field(default_factory=list)
    publisher: str = ""
    series: str = ""
    series_index: float = 1.0
    lang: str = "zh"
    cover: str = ""
    extra: dict = field(default_factory=dict)

    def description_paragraphs(self) -> list[str]:
        text = (self.description or "").strip()
        if not text:
            return []
        return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    def filename(self) -> str:
        author = self.author or "佚名"
        return f"《{self.title}》-{author}"


def _candidates(txt_path: Path, meta_dir: Path) -> list[Path]:
    stem = txt_path.stem
    exts = ["yaml", "yml", "json", "txt"]
    names = []
    for ext in exts:
        names += [f"{stem}.{ext}", f"{stem}.meta.{ext}"]
    return [meta_dir / n for n in names if (meta_dir / n).is_file()]


def _parse_yaml(text: str) -> dict:
    import yaml
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _parse_ini(text: str) -> dict:
    data = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        data[key.strip().lower()] = val.strip()
    return data


def _as_list(val) -> list:
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    return [x.strip() for x in str(val).replace("，", ",").split(",") if x.strip()]


def load_meta(txt_path: Path, meta_dir: Path) -> tuple[Meta, Optional[Path]]:
    stem = txt_path.stem
    cand = _candidates(txt_path, meta_dir)
    data: dict = {}
    source = None
    if cand:
        source = cand[0]
        text = source.read_text(encoding="utf-8", errors="replace")
        try:
            if source.suffix.lower() in (".yaml", ".yml"):
                data = _parse_yaml(text)
            elif source.suffix.lower() == ".json":
                data = json.loads(text)
                if not isinstance(data, dict):
                    data = {}
            else:
                data = _parse_ini(text)
        except Exception as exc:
            raise MetaError(f"元信息文件解析失败 {source}：{exc}")

    title = str(data.get("title") or data.get("书名") or stem).strip()
    author = str(data.get("author") or data.get("作者") or "").strip()
    desc = str(data.get("description") or data.get("简介") or data.get("desc") or "").strip()
    tags = _as_list(data.get("tags") or data.get("标签") or data.get("category"))
    publisher = str(data.get("publisher") or data.get("出版社") or "").strip()
    series = str(data.get("series") or data.get("系列") or "").strip()
    try:
        series_index = float(data.get("series_index", 1.0) or 1.0)
    except (TypeError, ValueError):
        series_index = 1.0
    lang = str(data.get("lang") or data.get("language") or "zh").strip() or "zh"
    cover = str(data.get("cover") or data.get("封面") or "").strip()
    return Meta(
        title=title, author=author, description=desc, tags=tags,
        publisher=publisher, series=series, series_index=series_index,
        lang=lang, cover=cover, extra=data,
    ), source
