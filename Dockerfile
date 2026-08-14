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

# 运行时数据目录挂载点
# 注意：/template 与 /fonts 不能声明为 VOLUME，否则镜像内 COPY 的
# 默认模板/字体会被空匿名卷隐藏，导致容器启动时找不到 template.yml。
VOLUME ["/input", "/meta", "/output", "/work", "/logs"]

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["web"]
