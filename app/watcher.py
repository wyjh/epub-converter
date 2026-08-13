# -*- coding: utf-8 -*-
"""批量/监控处理：一次性处理 /input 全部 TXT，或按间隔轮询自动转换。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from app.config import Settings
from app.converter import convert_one
from app.template import Template

log = logging.getLogger("converter")


def process_all(settings: Settings, template: Template, force: bool = False) -> dict:
    txts = sorted(settings.input_dir.glob("*.txt"))
    if not txts:
        log.info("输入目录中没有 TXT 文件：%s", settings.input_dir)
        return {"total": 0, "ok": 0, "skipped": 0, "failed": 0, "failed_stems": []}

    ok = skipped = failed = 0
    failed_stems = []
    log.info("开始批量转换，共 %d 个 TXT", len(txts))
    for t in txts:
        res = convert_one(t, settings, template, force=force)
        if res.status == "ok":
            ok += 1
        elif res.status == "skipped":
            skipped += 1
        else:
            failed += 1
            failed_stems.append(res.stem)
    summary = f"本次处理：成功 {ok}，跳过 {skipped}，失败 {failed}"
    if failed_stems:
        summary += f"；失败清单：{', '.join(failed_stems)}"
    log.info(summary)
    return {"total": len(txts), "ok": ok, "skipped": skipped, "failed": failed,
            "failed_stems": failed_stems}


def watch_loop(settings: Settings, template: Template, force: bool = False) -> None:
    log.info("进入监控模式，每 %d 秒扫描一次 %s", settings.watch_interval, settings.input_dir)
    while True:
        try:
            process_all(settings, template, force=force)
        except Exception:
            log.exception("监控轮询出错")
        time.sleep(settings.watch_interval)
