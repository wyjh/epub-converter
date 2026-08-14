# TXT → 标准 EPUB 批量转换工具

把任意 TXT 小说批量转换成**排版与参考样例完全一致**的标准 EPUB，可直接导入 Calibre 书库、推送 Kindle。

所有样式参数不是程序“临时调整”出来的，而是从参考样例 EPUB 中**提取并固化**的模板，任何新书都强制复用同一套模板，杜绝样式漂移。转换引擎默认使用内置的**直接打包模式**（与样例排版 1:1，无需额外依赖）；也可以选择内置 calibre（构建时加 `--build-arg INSTALL_CALIBRE=1`）走 `ebook-convert` 引擎。

自带 **Web 管理界面**：批量导入 TXT、多源刮削元信息（书名/作者/简介/封面）、在线编辑、一键转换、下载成品、实时日志，全程网页操作。

---

## 快速开始

```bash
docker compose up -d --build
```

复制上面这一条命令即可构建并启动，然后打开 **http://localhost:8080** 使用 Web 界面。

> 首次运行需要创建数据目录（`input/`、`meta/`、`output/`、`template/`、`fonts/`）；Docker Compose 会自动创建挂载目录，也可以手动 `mkdir -p input meta fonts output template`。

### 完整 docker-compose.yml（可复制）

项目根目录自带 `docker-compose.yml`，内容如下，所有目录都是宿主机挂载：

```yaml
services:
  epub-converter:
    build: .
    image: liangjh6960/epub-converter:latest
    container_name: epub-converter
    restart: unless-stopped
    environment:
      WATCH_INTERVAL: "15"          # 扫描间隔（秒）
      FORCE_RECONVERT: "0"          # 设为 1 强制全部重新转换
      # FONT_FILE: "PingFangSC-Medium.ttf"   # 指定嵌入字体（fonts/ 目录内文件名）
      # COVER_WIDTH: "600"                   # 自定义封面宽度（0/留空 = 模板默认 440）
      # COVER_HEIGHT: "800"                  # 自定义封面高度（0/留空 = 模板默认 578）
    ports:
      - "8080:8080"                 # Web 管理界面
    volumes:
      - ./input:/input              # 待转换 TXT + 封面
      - ./meta:/meta                # 同名元信息文件
      - ./fonts:/fonts              # 字体目录：放入任意 .ttf/.otf/.ttc 自动识别，可覆盖镜像内置字体
      - ./output:/output            # 转换结果 + 日志
      - ./template:/template        # 固化模板（可放 sample.epub 自动提取）
```

---

## 功能特性

- **Web 管理界面**（默认端口 8080）：导入、刮削、选封面、转换、下载、日志全部可视化操作。
- **批量导入**：一次上传多本 TXT 到书架，自动猜测书名/作者。
- **多源刮削**：支持 **微信读书 / 豆瓣读书 / 百度百科 / 当当图书** 四个数据源，可手动切换或合并展示；候选结果可选可编辑；刮不到时自动生成占位封面，保证“导入即转换”。
- **封面单独刮削**：多张候选中挑一张下载；不选封面也能转换（自动占位封面）。
- **自动清洗**：识别 UTF-8 / GBK / GB18030 / Big5 编码，清除网页广告、连载提示、冗余空行，合并硬换行错段。
- **智能切章**：按中文小说常见格式切章（第N章/回/节、序章、楔子、尾声、番外、Chapter N 等），中文数字统一为阿拉伯数字；每章正文开头都带章节名。
- **元信息**：书名、作者、简介、标签、出版社、系列写入 EPUB 元数据，并按样例格式生成“内容简介”页。
- **封面标准化**：按模板锁定的尺寸/比例/质量统一裁切、缩放、压缩（当前 440×578、质量 98）。
- **封面分辨率可自定义**：Web 端可改封面宽高，默认保持模板的 440×578，也可通过 `COVER_WIDTH` / `COVER_HEIGHT` 环境变量指定。
- **字体可插拔**：往 `fonts/` 放任意 `.ttf/.otf/.ttc` 即可自动识别；Web 端可切换嵌入字体，默认用模板字体；`@font-face` 之外的样式逐字复制样例原版 CSS，不做任何改动。
- **自动校验**：字体嵌入、字号/行高/缩进/间距/对齐、页面边距、封面尺寸、元信息、目录章节数逐项比对，不达标即失败并给出明确报错。
- **一键清理**：转换完成后可删除源文件；书架/输出支持单个移除、免确认删除、一键清空。
- **输出统一命名**：`《书名》-作者.epub`。

---

## Docker Compose 详解

### 环境要求

- Docker Engine 20.10+（含 `docker compose` 插件）
- 能访问外网（构建镜像需安装 Python 依赖；默认不内置 calibre，构建很快）

### 步骤

```bash
# 1. 进入项目目录
cd epub

# 2. 创建数据目录
mkdir -p input meta fonts output template

# 3. 准备数据
#    input/    放入 TXT 小说（可选同名的 jpg/png 封面）
#    meta/     放入与 TXT 同名的 yaml 元信息（可选，不填也能转）
#    fonts/    放入任意字体 *.ttf/*.otf/*.ttc（可选，默认用镜像内置字体；见“字体配置”）
#    template/ 已有固化模板则无需操作；放 sample.epub 可让容器自动提取模板

# 4. 构建并启动（后台运行）
docker compose up -d --build

# 5. 打开 Web 管理界面
#    http://localhost:8080

# 6. 查看运行日志
docker compose logs -f
```

### 常用命令

```bash
# 停止服务
docker compose down

# 停止后保留数据卷/目录，重新启动
docker compose up -d

# 代码更新后重新构建
docker compose build && docker compose up -d

# 纯命令行监控模式（每 15 秒扫描 input/，发现新 TXT 自动转换）
docker compose exec epub-converter watch
```

> 容器默认启动 Web 界面；`input/`、`meta/`、`output/`、`template/`、`fonts/` 都是宿主机目录直接挂载，重启容器数据不丢失。

### 从 Docker Hub 拉取（无需本地构建）

```bash
docker pull liangjh6960/epub-converter:latest

docker run -d --name epub-converter --restart unless-stopped \
  -v "$PWD/input:/input" \
  -v "$PWD/meta:/meta" \
  -v "$PWD/fonts:/fonts" \
  -v "$PWD/output:/output" \
  -v "$PWD/template:/template" \
  -p 8080:8080 \
  liangjh6960/epub-converter:latest
```

镜像默认内置模板与苹方字体，直接跑即可；自定义字体/模板时挂载 `fonts/`、`template/` 目录覆盖。

---

## 使用指南（Web 界面）

打开 `http://localhost:8080` 后，推荐流程：

1. **导入**：在“导入 TXT”一次选择多本 TXT，导入后进入下方书架（自动猜测书名/作者）。
2. **转换设置（可选）**：在“转换设置”里自定义封面分辨率（默认 440×578）和嵌入字体（来自 `/fonts` 目录），保存后对所有转换生效。
3. **刮削元信息**：点书架的“刮削”，选择刮削源（全部 / 微信读书 / 豆瓣读书 / 百度百科 / 当当图书），返回候选列表（书名/作者/简介/评分/封面，带来源标签）；勾选一个候选，或直接手动编辑书名/作者/简介/标签/出版社，点“保存元信息”。
4. **刮封面（可选）**：点“刮封面”从多张候选中挑一张；也可以不选，转换时自动生成占位封面。
5. **转换**：书架每本可单独点“转换”，或点“扫描并转换全部”批量转换。
6. **下载**：右侧“输出结果”列出生成的 EPUB，点击下载。
7. **清理**：书架支持单本移除（🗑）、“清理已转换源文件”、“清空书架”；输出支持单文件删除（🗑）和“清空输出”；勾选“免确认删除”后所有删除都不再弹确认框。

---

## 刮削源说明

| 数据源 | 可刮到内容 | 说明 |
| --- | --- | --- |
| 微信读书 | 书名、作者、简介、封面（600px 原图） | 网络小说覆盖好，搜索无需登录 |
| 豆瓣读书 | 书名、作者、评分、简介、高清封面 | 实体书覆盖好 |
| 百度百科 | 书名、作者、完整简介、封面大图 | 按精确词条返回单条结果 |
| 当当图书 | 书名、作者、出版社、封面 | 实体书商品信息 |

> 刮削依赖网络，第三方接口可能变动导致偶尔刮不到；此时可切换其他源，或手动填写。所有刮削数据仅用于生成个人阅读用的元信息，版权归原作者/平台所有。

---

## 目录结构（宿主机挂载到容器）

| 宿主机目录 | 容器目录 | 用途 |
| --- | --- | --- |
| `./input` | `/input` | 待转换的 TXT 与同名封面图片 |
| `./meta` | `/meta` | 与 TXT 同名的元信息配置文件 |
| `./fonts` | `/fonts` | 苹方字体文件（TTF/OTF） |
| `./output` | `/output` | 转换结果 + 运行日志 |
| `./template` | `/template` | 固化模板；可放入 `sample.epub` 自动提取 |

> 镜像已内置默认模板与苹方字体，**不挂载也能直接运行**；挂载 `template/`、`fonts/` 用于自定义覆盖。

## 手动运行（Docker run）

```bash
docker build -t liangjh6960/epub-converter:latest .

docker run -d --name epub-converter --restart unless-stopped \
  -v "$PWD/input:/input" \
  -v "$PWD/meta:/meta" \
  -v "$PWD/fonts:/fonts" \
  -v "$PWD/output:/output" \
  -v "$PWD/template:/template" \
  -p 8080:8080 \
  -e WATCH_INTERVAL=15 \
  liangjh6960/epub-converter:latest
```

## 手动触发转换（不进入监控）

```bash
# 批量转换 /input 下全部 TXT
docker exec epub-converter convert

# 只转换指定文件
docker exec epub-converter convert --file /input/某本书.txt

# 强制全部重新转换（忽略缓存）
docker exec epub-converter convert --force

# 重新提取模板
docker exec epub-converter extract-template /template/sample.epub --out /template
```

---

## 元信息文件格式

文件名必须与 TXT 同名（如 `书A.txt` ↔ `书A.yaml`），推荐 YAML：

```yaml
# meta/书A.yaml
title: 书名
author: 作者名
description: |
  这是书籍简介第一段，会按样例格式展示在“内容简介”页。

  这是简介第二段（可选）。
tags:
  - 科幻
  - 悬疑
publisher: 出版社
series: 系列名
series_index: 1
lang: zh
cover: 书A.jpg        # 可选；不写则自动找 /input 下同名图片
```

同时兼容 JSON 与 `key=value` 文本：

```json
{
  "title": "书名",
  "author": "作者名",
  "description": "简介",
  "tags": ["科幻", "悬疑"]
}
```

```ini
title=书名
author=作者名
description=简介
tags=科幻,悬疑
```

没有元信息文件也能转换：书名取 TXT 文件名，作者显示“佚名”，简介为空。

## 封面规则

封面图片放到 `input/`，与 TXT 同名（`书A.txt` → `书A.jpg` / `书A.png`），或在元信息中显式指定 `cover:`。

程序统一处理：**居中裁剪到目标比例 → 缩放到目标像素尺寸 → 白底合成（透明 PNG 转 RGB）→ JPEG 质量按模板压缩**。

默认分辨率为 440×578（从参考样例提取），质量 98。可在 Web 端“转换设置”里自定义宽高，或通过环境变量 `COVER_WIDTH` / `COVER_HEIGHT` 指定；改回 440×578 即恢复模板默认。

没有封面时自动生成带书名的占位封面，转换流程不会被卡住。

## 字体配置

把任意字体文件（`.ttf` / `.otf` / `.ttc`）放入 `fonts/` 即可，转换时会自动识别并嵌入。模板中记录了“样例嵌入的字体族与字重 → 提供字体文件名”的映射：

```yaml
fonts:
  embedded:
    - family: PingFang SC
      weight: '300'
      sample_file: PingFang-SC-Light.otf
      provided_file: PingFangSC-Light.ttf
```

默认嵌入 **PingFangSC-Light**（苹方细体）。转换时程序只替换 `@font-face` 的 `src` 指向实际嵌入的字体文件，其余 CSS 逐字保留。字体选择优先级：**Web“转换设置”指定的字体 / `FONT_FILE` 环境变量 > 模板默认（PingFangSC-Light）> `fonts/` 目录里自动探测的字体**。

> 苹方字体体积大且受版权保护，本仓库不包含字体文件。镜像内置一份默认字体；想用其他字体，把文件放进 `fonts/`（挂载目录）即可，无需改代码。

---

## 模板机制（如何做到 1:1）

“模板” = `template/template.yml` + 三份从样例逐字复制的 CSS：

- `stylesheet.css`：正文、标题、封面页的 class 样式
- `page_styles.css` / `page_styles1.css`：页面边距与 `@font-face` 字体声明

这些文件由 `extract-template` 从参考样例 EPUB 自动生成：

```bash
# 本地
python tools/extract_template.py 参考样例.epub --out template --fonts-dir fonts

# 容器内
docker exec epub-converter extract-template /template/sample.epub --out /template
```

提取内容包括：封面尺寸/比例/JPEG 质量、嵌入字体族与字重、正文/标题/简介页的字号、行高、缩进、边距、对齐、颜色，页面上下边距，目录规则，以及清洗/切章规则。**模板一经固化即为唯一样式基准**；想换版式，只能换样例重新提取，程序不会自行推算任何间距或字号。

---

## 转换后校验项（不合格即失败）

每个 EPUB 产出后自动解包检查：

- mimetype 合法、ZIP 结构完整
- 元信息：书名、作者、简介、标签写入 OPF
- 样式关键参数：首行缩进 2em、行高 130%、段前/段后 1em、两端对齐、正文苹方字体、`@page` 上下边距 5pt
- 字体：CSS 正确引用提供的字体文件，且 EPUB 内嵌入了完整字体（非子集）
- 封面：像素尺寸必须等于模板规格（440×578）、格式 JPEG
- 目录：章节数不少于正文章节数，且包含“内容简介”入口

失败时输出文件移入 `output/failed/`，并在日志中列出全部未通过项。

---

## 日志与错误处理

- 实时日志输出到 stdout（`docker compose logs -f` 可见）。
- 运行日志写入 `output/logs/conversion.log`（每条记录含时间、书名、编码、章节数、成败）。
- 单个文件失败不影响批量任务：程序继续处理其余 TXT，最后汇总成功/跳过/失败清单。
- 常见失败给出明确原因：编码无法识别、清洗后为空、字体缺失、calibre 转换失败、模板校验不通过。

---

## 后端 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/import` | 批量导入 TXT 到 input（不转换） |
| GET | `/api/scrape/<stem>` | 刮削元信息候选（`?source=all/weread/douban/baike/dangdang`） |
| GET | `/api/covers/<stem>` | 获取封面候选列表（`?source=all/weread/douban/baike/dangdang`） |
| POST | `/api/cover/save` | 下载选中的封面 |
| POST | `/api/meta/save` | 保存编辑后的元信息 |
| POST | `/api/convert/one` | 转换书架上单本 TXT |
| POST | `/api/convert/upload` | 上传 TXT（+封面+元信息）并转换 |
| POST | `/api/convert/batch` | 批量上传多个 TXT 并逐个转换 |
| POST | `/api/scan` | 扫描并批量转换 `/input` |
| POST | `/api/remove` | 从书架移除单本书（TXT/封面/元信息） |
| POST | `/api/output/remove` | 删除单个输出 EPUB |
| POST | `/api/cleanup` | 删除已成功转换的源文件 |
| POST | `/api/clear/bookshelf` | 一键清空书架 |
| POST | `/api/clear/output` | 一键清空输出结果 |
| GET | `/api/books` | 书架/输出/失败清单 |
| GET | `/api/download/<文件名>` | 下载 EPUB |
| GET | `/api/logs` | 最近日志 |
| GET | `/api/health` | 模板与目录信息 |

> 安全提示：这是内网/本机工具，Web 服务默认监听所有网卡（`0.0.0.0:8080`）。请勿直接暴露到公网；如需公网访问请自行加反向代理与鉴权。

---

## Kindle / Calibre 兼容性

- 输出为 EPUB 2（与参考样例一致），含 `toc.ncx` 目录、封面元信息、嵌入字体。
- 直接拖入 Calibre 书库即可；推送到 Kindle 时建议在 Calibre 中转换成 AZW3/KFX 后发送。
- 不依赖任何在线服务，转换流程离线可用（刮削除外）。

---

## 本地开发（无 Docker / 无 calibre）

```bash
pip install -r requirements.txt

# 只打印 calibre 命令，不实际执行（本机无 calibre 时必须加 --dry-run）
python -m app.main --local convert --dry-run

# 无 calibre 时直接打包出 EPUB（结构与样式与样例 1:1）
python -m app.main --local convert --direct

# 运行内置测试
python tests/run_tests.py

# 本地启动 Web 界面
INPUT_DIR=./input META_DIR=./meta FONTS_DIR=./fonts OUTPUT_DIR=./output \
TEMPLATE_DIR=./template WORK_DIR=./work LOGS_DIR=./output/logs \
python -m web.app
```

> 直接打包模式（`--direct` 或环境变量 `DIRECT_CONVERT=1`）：不依赖 calibre，按参考样例的 EPUB 内部结构原样生成（封面 SVG 页、封面/简介/章节页、原版 CSS、嵌入苹方字体、NCX 目录）。Docker 环境默认使用 calibre `ebook-convert`，只有找不到 calibre 时才自动降级。

---

## 常见问题

**Q：构建镜像时提示无法下载 calibre / 依赖**  
构建需要外网（apt + pip）。国内网络可给 Docker 配置镜像加速后重试。

**Q：转换失败提示“找不到 ebook-convert”**  
容器外调试时未安装 calibre。加 `--dry-run` 只预览命令，或在容器内运行。

**Q：刮削不到结果**  
第三方接口偶尔变动或书名差异导致。尝试切换其他刮削源，或直接手动填写书名/作者/简介；封面刮不到会自动生成占位封面。

**Q：提示字体缺失**  
确认 `fonts/` 里包含 `template.yml` 中 `provided_file` 对应的文件名（默认 `PingFangSC-Light.ttf`）。

**Q：模板校验不通过**  
查看 `output/failed/` 与日志中列出的具体不达标项；不要手工改 CSS，应检查输入文件（如封面尺寸异常、TXT 编码异常）后重转。

**Q：某本书章节没切对**  
正文段落若以“第七章……”开头可能被误判（工具已用“短行且不以句末标点结尾”过滤）；可在 `template/template.yml` 的 `text_cleaning.chapter_patterns` 中调整规则后重转。

---

## 免责声明

本项目仅用于个人学习与阅读排版研究。刮削数据来自第三方平台，版权归原作者及平台所有；请支持正版，勿将本工具用于传播未经授权的电子书。
