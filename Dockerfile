FROM python:3.11-alpine

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apk add --no-cache bash

# 转换默认使用内置“直接打包模式”（排版与样例 1:1，无需额外依赖）

COPY requirements.txt /tmp/requirements.txt
# 依赖分层安装（每层小，方便在弱网环境下推送镜像）
RUN pip install --no-cache-dir PyYAML Pillow
RUN pip install --no-cache-dir chardet Flask

WORKDIR /app
COPY app /app/app
COPY tools /app/tools
COPY web /app/web
COPY entrypoint.sh /app/entrypoint.sh
# 内置默认模板/字体备份：启动时若挂载目录为空会自动恢复到 /template、/fonts
COPY template /opt/epub-defaults/template

# 字体逐个 COPY：每个字体重开一层，镜像仍内置全部字体
COPY "fonts/PingFangSC-Light.ttf" /opt/epub-defaults/fonts/
COPY "fonts/PingFangSC-Thin.ttf" /opt/epub-defaults/fonts/
COPY "fonts/PingFangSC-Regular.ttf" /opt/epub-defaults/fonts/
COPY "fonts/PingFangSC-Medium.ttf" /opt/epub-defaults/fonts/
COPY "fonts/PingFangSC-Semibold.ttf" /opt/epub-defaults/fonts/
COPY ["fonts/PingFang Heavy.ttf", "/opt/epub-defaults/fonts/"]
COPY "fonts/PingFang-SC-Light.otf" /opt/epub-defaults/fonts/

EXPOSE 8080

RUN mkdir -p /input /meta /output /work /logs /template /fonts \
    && chmod +x /app/entrypoint.sh

# 运行时数据目录挂载点
# 注意：/template 与 /fonts 不能声明为 VOLUME，否则镜像内 COPY 的
# 默认模板/字体会被空匿名卷隐藏，导致容器启动时找不到 template.yml。
VOLUME ["/input", "/meta", "/output", "/work", "/logs"]

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["web"]
