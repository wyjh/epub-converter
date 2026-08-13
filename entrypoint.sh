#!/usr/bin/env bash
set -euo pipefail

# 容器默认工作目录为 /app
cd /app

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
