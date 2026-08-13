# -*- coding: utf-8 -*-
"""封面处理：按模板锁定的尺寸/比例/压缩参数，把任意封面裁切压缩成标准封面。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.template import CoverSpec


class CoverError(Exception):
    pass


def find_cover_image(txt_path: Path, meta_cover: str, input_dir: Path) -> Optional[Path]:
    if meta_cover:
        p = Path(meta_cover)
        if not p.is_absolute():
            p = input_dir / p
        if p.is_file():
            return p
    stem = txt_path.stem
    for ext in (".jpg", ".jpeg", ".png"):
        cand = txt_path.with_suffix(ext)
        if cand.is_file():
            return cand
    for name in (f"{stem}.jpg", f"{stem}.jpeg", f"{stem}.png", "cover.jpg", "cover.png"):
        cand = input_dir / name
        if cand.is_file():
            return cand
    return None


def process_cover(src: Path, dst: Path, spec: CoverSpec) -> Path:
    try:
        from PIL import Image
    except ImportError:
        raise CoverError("缺少 Pillow 依赖，无法处理封面图片")

    try:
        im = Image.open(src)
        im.load()
    except Exception as exc:
        raise CoverError(f"封面图片无法打开 {src}：{exc}")

    # 统一到 RGB，透明底合成到白色背景
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, rgba).convert("RGB")
    else:
        im = im.convert("RGB")

    target_w, target_h = int(spec.width), int(spec.height)
    scale = max(target_w / im.width, target_h / im.height)
    new_w, new_h = max(target_w, round(im.width * scale)), max(target_h, round(im.height * scale))
    im = im.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    im = im.crop((left, top, left + target_w, top + target_h))

    try:
        im.save(dst, format="JPEG", quality=int(spec.quality), optimize=True)
    except Exception as exc:
        raise CoverError(f"封面压缩失败：{exc}")

    with Image.open(dst) as check:
        if check.size != (target_w, target_h):
            raise CoverError(f"封面尺寸校验失败：{check.size} != ({target_w}, {target_h})")
    return dst
