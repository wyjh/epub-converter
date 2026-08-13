# -*- coding: utf-8 -*-
"""命令行入口：convert（批量/单本）、watch（监控）、extract-template（模板提取）。"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from app import __version__
from app.config import Settings
from app.converter import convert_one
from app.template import TemplateError, load_template
from app.watcher import process_all, watch_loop

log = logging.getLogger("converter")


def setup_logging(settings: Settings) -> None:
    settings.ensure_dirs()
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stdout)
    fh = logging.FileHandler(settings.logs_dir / "conversion.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(fh)


def ensure_template(settings: Settings) -> None:
    yml = settings.template_dir / "template.yml"
    if yml.is_file():
        return
    sample = settings.template_dir / "sample.epub"
    if sample.is_file():
        log.info("未找到 template.yml，自动从样例提取：%s", sample)
        from tools.extract_template import extract_template
        extract_template(sample, settings.template_dir, settings.fonts_dir)
        return
    raise TemplateError(
        f"未找到固化模板 {yml}。请把样例EPUB放到 {settings.template_dir}/sample.epub，"
        "或先运行 extract-template 子命令。"
    )


def cmd_convert(args, settings: Settings) -> int:
    ensure_template(settings)
    template = load_template(settings.template_dir)
    if args.file:
        p = Path(args.file)
        if not p.is_file():
            log.error("文件不存在：%s", p)
            return 1
        res = convert_one(p, settings, template, force=args.force or settings.force_reconvert)
        log.info("[%s] %s", res.stem, res.message)
        return 0 if res.status == "ok" else 1
    summary = process_all(settings, template, force=args.force or settings.force_reconvert)
    return 1 if summary["failed"] else 0


def cmd_watch(args, settings: Settings) -> int:
    ensure_template(settings)
    template = load_template(settings.template_dir)
    if args.interval:
        settings.watch_interval = max(3, args.interval)
    def _stop(signum, frame):
        log.info("收到停止信号，退出监控")
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    watch_loop(settings, template, force=args.force or settings.force_reconvert)
    return 0


def cmd_extract(args, settings: Settings) -> int:
    from tools.extract_template import main as extract_main
    argv = [str(args.sample)]
    argv += ["--out", str(args.out)]
    if settings.fonts_dir.is_dir():
        argv += ["--fonts-dir", str(settings.fonts_dir)]
    return extract_main(argv)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="epub-converter", description="TXT 批量标准化 EPUB 转换工具")
    ap.add_argument("--dry-run", action="store_true", help="只打印 calibre 命令，不实际转换")
    ap.add_argument("--local", action="store_true", help="使用当前目录下的 input/meta/fonts/output/template 等文件夹（宿主机调试用）")
    sub = ap.add_subparsers(dest="command", required=True)

    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="只打印 calibre 命令，不实际转换")
    parent.add_argument("--direct", action="store_true", default=argparse.SUPPRESS,
                        help="直接打包模式（无需 calibre，结构与样式与样例一致）")
    parent.add_argument("--local", action="store_true", default=argparse.SUPPRESS,
                        help="使用当前目录下的文件夹（宿主机调试用）")

    p_conv = sub.add_parser("convert", help="批量转换 /input 下全部 TXT（或 --file 指定单个）",
                            parents=[parent])
    p_conv.add_argument("--file", default=None, help="只转换指定 TXT")
    p_conv.add_argument("--force", action="store_true", help="忽略缓存强制重新转换")

    p_watch = sub.add_parser("watch", help="监控 /input，新 TXT 自动转换", parents=[parent])
    p_watch.add_argument("--interval", type=int, default=None, help="扫描间隔（秒）")
    p_watch.add_argument("--force", action="store_true", help="首次启动时全部重新转换")

    p_ext = sub.add_parser("extract-template", help="从参考样例EPUB提取固化模板")
    p_ext.add_argument("sample", type=Path, help="参考样例EPUB路径")
    p_ext.add_argument("--out", type=Path, default=Path("/template"))

    sub.add_parser("version", help="显示版本")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    settings.dry_run = getattr(args, "dry_run", False) or settings.dry_run
    settings.direct_mode = getattr(args, "direct", False) or settings.direct_mode
    if getattr(args, "local", False):
        base = Path.cwd()
        settings.input_dir = base / "input"
        settings.meta_dir = base / "meta"
        settings.fonts_dir = base / "fonts"
        settings.output_dir = base / "output"
        settings.template_dir = base / "template"
        settings.work_dir = base / "work"
        settings.logs_dir = settings.output_dir / "logs"
    setup_logging(settings)
    log.info("epub-converter v%s 启动", __version__)

    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "convert":
        return cmd_convert(args, settings)
    if args.command == "watch":
        return cmd_watch(args, settings)
    if args.command == "extract-template":
        return cmd_extract(args, settings)
    ap.error("未知命令")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
