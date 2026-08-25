#!/usr/bin/env bash
set -euo pipefail

# 容器默认工作目录为 /app
cd /app

# 挂载目录为空时，恢复镜像内置的默认模板/字体
if [ ! -f /template/template.yml ] && [ ! -f /template/sample.epub ] && [ -d /opt/epub-defaults/template ]; then
  echo "[entrypoint] /template 为空，恢复镜像内置模板"
  cp -a /opt/epub-defaults/template/. /template/
fi
if [ -z "$(ls -A /fonts 2>/dev/null)" ] && [ -d /opt/epub-defaults/fonts ]; then
  echo "[entrypoint] /fonts 为空，恢复镜像内置字体"
  cp -a /opt/epub-defaults/fonts/. /fonts/
fi

# 若模板尚未固化、但 /template 里有样例EPUB，则自动提取模板
if [ ! -f /template/template.yml ] && [ -f /template/sample.epub ]; then
  echo "[entrypoint] 检测到样例EPUB，正在提取固化模板..."
  python /app/tools/extract_template.py /template/sample.epub --out /template --fonts-dir /fonts
fi

case "${1:-watch}" in
  web)
    shift || true
    exec python -m web.app "$@"
    ;;
  watch)
    shift || true
    exec python -m app.main watch "$@"
    ;;
  convert)
    shift || true
    exec python -m app.main convert "$@"
    ;;
  extract-template)
    shift || true
    exec python /app/tools/extract_template.py "$@"
    ;;
  version)
    exec python -m app.main version
    ;;
  *)
    exec "$@"
    ;;
esac
