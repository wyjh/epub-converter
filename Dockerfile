FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 内置 headless calibre（含 ebook-convert）
RUN apt-get update \
    && apt-get install -y calibre \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY app /app/app
COPY tools /app/tools
COPY web /app/web
COPY entrypoint.sh /app/entrypoint.sh
COPY template /template
COPY fonts /fonts

EXPOSE 8080

RUN mkdir -p /input /meta /output /work /logs \
    && chmod +x /app/entrypoint.sh

# 宿主机目录挂载点
VOLUME ["/input", "/meta", "/fonts", "/output", "/template"]

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["web"]
