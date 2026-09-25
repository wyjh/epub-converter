# TXT → 标准 EPUB 批量转换工具

把任意 TXT 小说批量转换成**排版与参考样例完全一致**的标准 EPUB，可直接导入 Calibre 书库、推送 Kindle。

所有样式参数不是程序“临时调整”出来的，而是从参考样例 EPUB 中**提取并固化**的模板，任何新书都强制复用同一套模板，杜绝样式漂移。转换引擎默认使用内置的**直接打包模式**（与样例排版 1:1，无需额外依赖）；也可以选择内置 calibre（构建时加 `--build-arg INSTALL_CALIBRE=1`）走 `ebook-convert` 引擎。

自带 **Web 管理界面**：批量导入 TXT、多源刮削元信息（含起点中文网）、自定义上传封面、在线编辑、一键转换、下载成品、实时日志，全程网页操作。

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
      - ./config:/config            # 刮削源配置（sources.yml，首次启动自动生成）
```

### 挂载 template / fonts 目录的要求

镜像内置了默认模板和苹方字体，**不挂载 `template/`、`fonts/` 也能直接运行**。

如果要挂载自定义目录覆盖内置内容，**必须保证挂载的本地目录非空且内容正确**，否则空目录会盖住镜像内置内容，导致启动报“找不到 template.yml”或转换时报“字体缺失”：

- `./template:/template`：本地目录需要包含固化模板文件：
  - `template.yml`（模板配置，必需）
  - `stylesheet.css`、`page_styles.css`、`page_styles1.css`（样式，必需）
  - 可选：`sample.epub`（放入后容器启动时会自动提取模板）、`references/`、`sample_cover.jpg`
  - 最简单的方式：直接把项目仓库自带的 `template/` 目录挂上去（里面已是完整固化模板）。
- `./fonts:/fonts`：本地目录需要包含字体文件（`.ttf` / `.otf` / `.ttc`），否则转换时提示字体缺失；直接挂项目自带的 `fonts/` 目录即可。

> 本地目录是空的就不要挂载，使用镜像内置模板/字体即可。

---

## 功能特性

- **Web 管理界面**（默认端口 8080）：导入、刮削、选封面、转换、下载、日志全部可视化操作。
- **批量导入**：一次上传多本 TXT 到书架，自动猜测书名/作者。
- **多源刮削**：内置 **起点中文网 / 微信读书 / 豆瓣读书 / 百度百科 / 当当图书**，另有 **Google 图书 / Open Library** 两个官方 API 源（默认关闭，国内网络一般连不上）；可手动切换或合并展示，多个源、多轮关键词**并发**搜索，不会因为某个源慢就整体超时。
- **刮削源可配置**：源配置在 `sources.yml`，可以开关某个源、调请求间隔、加自定义源（普通 JSON/HTML 接口不用改代码）。
- **刮不到也能救**：书名从「正文开头 + 文件名」双重猜测，兼容 `《书名》作者：xxx`、`书名-作者`、`书名（作者）`、`[站名] 书名 作者` 等下载站常见命名，并自动清理“全集/txt下载/精校版”等噪声；在刮削面板里手动改书名后点“重新刮削”就能按新书名再搜一遍。
- **抗失效**（详见下文「刮削源会失效怎么办」）：每个源都有 2~3 条解析路径（主路径断了自动走备用）；刮削结果本地缓存 7 天，重复刮同一本书不再发请求；请求节流 + 连续失败熔断，避免被限流、也避免坏源拖慢整体；网页端「检测刮削源」一键看出哪个源还活着、是改版还是被风控。
- **批量刮削**：书架上点“批量刮削书架”即可一次刮完所有还没元信息的 TXT（已有元信息的自动跳过），刮完再统一转换。
- **封面单独刮削**：多张候选中挑一张下载（起点自动取 600px 大图）；不选封面也能转换（自动占位封面）。
- **自定义上传封面**：可直接上传本地图片，上传后**自动按项目当前分辨率**（默认 440×578）居中裁切、缩放、压缩，立刻替换该书封面。
- **自动清洗**：识别 UTF-8 / GBK / GB18030 / Big5 编码，清除网页广告、连载提示、冗余空行，合并硬换行错段。
- **智能切章**：按中文小说常见格式切章（第N章/回/节、序章、楔子、尾声、番外、Chapter N 等），中文数字统一为阿拉伯数字；每章正文开头都带章节名。
- **元信息**：书名、作者、简介、标签、出版社、系列写入 EPUB 元数据，并按样例格式生成“内容简介”页。
- **封面标准化**：按模板锁定的尺寸/比例/质量统一裁切、缩放、压缩（当前 440×578、质量 98）。
- **封面分辨率可自定义**：Web 端可改封面宽高，默认保持模板的 440×578，也可通过 `COVER_WIDTH` / `COVER_HEIGHT` 环境变量指定；刮削下载和自定义上传的封面都按该分辨率统一处理。
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

镜像默认内置模板与苹方字体，直接跑即可；自定义字体/模板时挂载 `fonts/`、`template/` 目录覆盖（挂载的目录必须非空，具体见上文“挂载 template / fonts 目录的要求”）。

---

## 使用指南（Web 界面）

打开 `http://localhost:8080` 后，推荐流程：

> **界面说明**：点书架的“刮削 / 刮封面”会从右侧滑出**抽屉面板**，它不占用页面流，内部独立滚动，
> 保存按钮固定在底部——书架上书再多也不用上下翻找。候选列表默认只显示匹配度高的，
> 低匹配的折叠在“显示其余 N 条低匹配候选”里。底部还有**“保存并转换”**可以一步完成。
> **整体布局**：从上到下依次是「导入 TXT → 书架 → 输出结果 / 转换设置 → 运行日志 → 刮削源」，
> 就是常规的整页滚动（只有一个滚动条）。书架放在最显眼的位置并占满整行：书名/作者是主行、
> 原始文件名降到次要位置，长书名自动换行不会被按钮挤扁；顶部筛选框可按书名/作者/文件名过滤。
> 点书架里的「刮削 / 刮封面」会从右侧滑出抽屉面板，保存按钮固定在底部，不用翻页找。
> 按 `Esc` 或点抽屉外的遮罩即可关闭。

1. **导入**：在“导入 TXT”一次选择多本 TXT，导入后进入下方书架（自动猜测书名/作者）。
2. **转换设置（可选）**：在“转换设置”里自定义封面分辨率（默认 440×578）和嵌入字体（来自 `/fonts` 目录），保存后对所有转换生效。
3. **刮削元信息**：点书架的“刮削”，选择刮削源（全部 / 起点中文网 / 微信读书 / 豆瓣读书 / 百度百科 / 当当图书），返回候选列表（书名/作者/简介/评分/封面/分类字数，带来源标签）；勾选一个候选，或直接手动编辑书名/作者/简介/标签/出版社，点“保存元信息”。
   - 一个候选都不合适时：把“书名”改成你确定的名字，再点“重新刮削”，程序会按新书名重新搜索；文件名猜错书名的情况基本靠这一步解决。
4. **封面（可选）**：点“刮封面”从多张候选里挑一张下载；或点“上传本地封面”选自己的图片，上传后会自动按当前分辨率（默认 440×578）裁切压缩并立即生效；都不选则转换时自动生成占位封面。
   - 如果候选列表是空的，先去「刮削源」卡片点“检测刮削源”，一眼就能看出是哪个源出了问题。
5. **转换**：书架每本可单独点“转换”，或点“扫描并转换全部”批量转换。
6. **下载**：右侧“输出结果”列出生成的 EPUB，点击下载。
7. **批量刮削**：书架上点“批量刮削书架”，一次把所有还没元信息的 TXT 刮完（已有元信息的跳过）；随后可直接“扫描并转换全部”。
8. **清理**：书架支持单本移除（🗑）、“清理已转换源文件”、“清空书架”；输出支持单文件删除（🗑）和“清空输出”；勾选“免确认删除”后所有删除都不再弹确认框。

---

## 刮削源说明

| 数据源 | 可刮到内容 | 说明 |
| --- | --- | --- |
| 起点中文网 | 书名、作者、完整简介、封面（600px 大图）、分类/字数/连载状态 | **网络小说首选**，覆盖最全；走移动端页面内嵌数据，无需登录 |
| 微信读书 | 书名、作者、简介、封面（600px 原图） | 网络小说覆盖好，搜索无需登录 |
| 豆瓣读书 | 书名、作者、评分、简介、高清封面 | 实体书覆盖好；网络小说容易匹配到同名实体书 |
| 百度百科 | 书名、作者、完整简介、封面大图、作品类型/连载平台/连载状态/总字数 | 按精确词条返回单条结果 |
| 当当图书 | 书名、作者、出版社、简介、封面 | 实体书商品信息；**搜不到精确结果时返回空**，不会拿“为您推荐”的商品凑数 |
| Google 图书 | 书名、作者、简介、封面、页数 | 官方 API，很稳，但**国内网络通常连不上**，默认关闭 |
| Open Library | 书名、作者、出版信息、封面 | 官方 API，中文书覆盖一般，默认关闭 |

> **候选封面为什么要走后端代理**：豆瓣、百度百科的图床会校验 `Referer`，浏览器直接 `<img>` 加载一律 403（页面上就是一片空白）。所以候选封面统一由 `/api/cover/proxy` 带正确 `Referer` 取回、缩成 480px 小图并缓存到 `work/cover_cache/`；**你选中下载时仍取原图**，画质不受预览缩略图影响。该接口带图床白名单，不会被当成任意 URL 代理。

> 刮削依赖网络，第三方接口可能变动导致偶尔刮不到；此时可切换其他源、手动改书名后“重新刮削”，或直接手动填写。所有刮削数据仅用于生成个人阅读用的元信息，版权归原作者/平台所有。

---

## 刮削源会失效怎么办

这些源里只有 Google 图书和 Open Library 是官方 API，其余都是“逆向”第三方页面或内部接口拿到的数据（起点是页面内嵌 JSON、豆瓣是页面里的 `__DATA__` 变量、当当是 HTML 选择器、百度百科是它的开放接口）。第三方没有任何兼容承诺，**改版是必然的**。所以这里不追求“永不失效”，而是让它少发生、发生了也能自己扛住、并且你一眼能看出坏在哪。

### 四层防护

| 机制 | 作用 |
| --- | --- |
| **多路兜底解析** | 每个源准备 2~3 条解析路径，主路径失效自动走备用。比如起点先读 SSR JSON，读不到就按字段级正则扫页面；豆瓣先读 `__DATA__`，读不到就解析结果节点；当当先按商品 id 切分，切不动就退回旧结构。 |
| **结果缓存** | 刮削结果按「源 + 关键词」缓存 7 天（`work/scrape_cache/`），重复刮同一本书不再发请求——既快，也大幅降低被限流的概率。 |
| **请求节流** | 同一源的两次请求之间有最小间隔（豆瓣 1.5s、当当 1.2s…），避免连续高频请求触发反爬。 |
| **失败熔断** | 某个源连续失败 3 次就暂停 5 分钟，不再拖慢整体搜索，期间其他源照常工作。 |
| **失败分级** | 状态区分「正常 / 无结果 / 疑似改版 / 疑似风控 / 请求失败 / 已熔断」——“确实没这本书”和“接口坏了”不再都显示成“刮不到”。 |

### 网页端：检测刮削源

「刮削源」卡片里点**“检测刮削源”**，会真实请求每个源并列出状态、耗时、样本书名和作者；改完 `sources.yml` 后点“重新加载配置”即可生效（不用重启服务）；想强制重新联网时点“清空刮削缓存”。

### sources.yml：关源、调参、加自定义源

本地运行读项目根 `sources.yml`；Docker 里读宿主的 `./config/sources.yml`（首次启动自动生成，带完整注释）。

```yaml
settings:
  cache_enabled: true
  cache_ttl_hours: 168      # 缓存 7 天
  failure_threshold: 3      # 连续失败 3 次熔断
  cooldown_seconds: 300     # 熔断 5 分钟

sources:
  qidian:
    enabled: true
    throttle: 1.0           # 两次请求最小间隔（秒），调大更不容易被反爬
  douban:
    enabled: true
    throttle: 1.5
  googlebooks:
    enabled: false          # 国内网络连不上就别开

# 自定义源：普通 JSON / HTML 接口不用改代码就能接
custom:
  - id: myapi
    label: 我的书库
    enabled: true
    url: "https://example.com/search?q={keyword}"
    headers: {User-Agent: "Mozilla/5.0"}
    timeout: 10
    throttle: 1
    format: json                  # json 或 html
    items: "data.list"            # json：结果数组所在路径
    fields:
      title: "name"               # 字段映射，json 支持 a.b.c 路径
      author: "author"
      intro: "desc"
      cover: "cover_url"
      publisher: "publisher"
      rating: "score"
      url: "link"

  - id: mysite
    label: 某小说站
    format: html
    url: "https://example.org/s?q={keyword}"
    item_regex: '<li class="book">.*?</li>'    # 每个结果块
    fields:
      title: "<h3>(.*?)</h3>"                   # html 用正则取第 1 组
      author: "作者：(.*?)<"
```

### 官方 API 兜底源

Google 图书和 Open Library 接口稳定、有官方承诺，但**中文网络小说覆盖一般**，且国内网络通常直连不上（Google 尤其）。它们在 `sources.yml` 里默认关闭；如果你的网络能访问，打开即可作为实体书/外文书的兜底。所有源请求都带硬超时保护，某个源卡住不会拖垮整体搜索。

---

## 自己添加刮削源

内置源搜不到你要的书（冷门书、内部资料、自出版），或者你有自己的书库服务时，可以在 `sources.yml` 里挂一个自定义源——**普通 JSON / HTML 接口不用改代码**。

### 先说两个限制

1. **接口必须是公开可访问的**：不支持需要登录态、验证码或 JS 渲染后才能出结果的页面。需要 token 或多次请求的，走文末「进阶」。
2. **页面上的候选封面走后端代理**，只允许已知图床。用自定义源时，把它的封面域名写进 `settings.extra_cover_hosts`，否则封面不显示（书名/作者照常能存）。

### 第一步：找到接口，看懂返回结构

在浏览器里打开目标站点：`F12` → **Network** → 筛 XHR/Fetch → 在站内搜索框输入关键词 → 找到返回结果的那个请求。

- 右键 **Copy → Copy link address** 拿到接口地址，把关键词换成 `{keyword}`
- 看 **Response**，确认是 JSON 还是 HTML

**JSON** 要确定两件事：
- 结果数组在哪一层。例如 `{"data": {"list": [...]}}` → `items: "data.list"`
- 每个字段叫什么。例如 `{"name": "...", "author": "..."}` → `title: "name"`

**HTML** 要确定两件事：
- 每个结果块的 HTML。例如 `<li class="book">...</li>` → `item_regex`
- 每个字段在块里的正则，**必须有捕获组 `(...)`**

### 第二步：写进 sources.yml

```yaml
settings:
  extra_cover_hosts: ["cdn.example.com"]   # 自定义源的封面域名（用不到封面可以不写）

custom:
  - id: my_source            # 唯一标识（不能和内置源重名）
    label: 我的书库           # 界面上显示的名字
    enabled: true
    url: "https://example.com/api/search?q={keyword}"   # {keyword} 自动替换并做 URL 编码
    headers:                 # 可选：站点校验 UA / Referer 时用
      User-Agent: "Mozilla/5.0 ..."
    timeout: 10              # 可选，默认 15 秒
    throttle: 1              # 可选，同一源两次请求的最小间隔（秒）
    format: json             # json（默认）或 html
    encoding: gb18030        # 可选，接口不是 UTF-8 时指定
    items: "data.list"       # json：结果数组路径，留空表示顶层就是数组
    item_regex: '<li class="book">.*?</li>'   # html：每个结果块的正则
    fields:
      title: "name"          # 必填；title 取不到的条目会被跳过
      author: "author"
      intro: "description"
      cover: "cover_url"
      publisher: "publisher"
      rating: "score"
      url: "detail_url"
      extra: "category"      # 候选卡片上额外显示的一行小字
```

| 配置项 | 作用 |
| --- | --- |
| `id` / `label` | 唯一标识 / 界面显示名 |
| `url` | 接口地址，`{keyword}` 是关键词占位符 |
| `headers` | 额外请求头（UA、Referer 等） |
| `timeout` / `throttle` | 超时秒数 / 同源最小请求间隔 |
| `format` | `json` 或 `html` |
| `items` | 仅 json：结果数组路径，支持 `a.b.c` 和数组下标（`a.list.0`） |
| `item_regex` | 仅 html：每个结果块的正则 |
| `fields.*` | 字段映射（见下） |

**字段映射规则**：JSON 用**路径**取值（`volumeInfo.title`；取到数组会自动用 `、` 连接）；HTML 用**正则**取第一个捕获组，匹配到的内容会自动去标签、压缩空白。

### 例子 1：JSON 接口

```yaml
custom:
  - id: mybookapi
    label: 我的书库 API
    url: "https://books.example.com/api/search?kw={keyword}&limit=10"
    format: json
    items: "result.books"
    fields:
      title: "bookName"
      author: "authorName"
      intro: "summary"
      cover: "coverUrl"
      publisher: "press"
      rating: "score"
```

对应的返回长这样：

```json
{"result": {"books": [
  {"bookName": "书名", "authorName": "作者", "summary": "简介",
   "coverUrl": "https://cdn.example.com/1.jpg", "press": "出版社", "score": 9.1}
]}}
```

### 例子 2：HTML 页面

```yaml
custom:
  - id: mysite
    label: 某小说站
    url: "https://www.example.org/search?q={keyword}"
    format: html
    item_regex: '<li class="book-item">.*?</li>'
    fields:
      title: '<h3 class="name">(.*?)</h3>'
      author: '作者：(.*?)<'
      intro: '<p class="intro">(.*?)</p>'
      cover: '<img[^>]+src="([^"]+)"'
```

> `item_regex` 和字段正则都按**跨行**匹配（等价于 Python 的 `re.S`）。
> 封面若是 `//cdn.example.com/x.jpg` 这种协议相对地址，记得把 `cdn.example.com` 加进 `extra_cover_hosts`。

### 第三步：生效并验证

1. 保存 `sources.yml`
2. 网页「刮削源」卡片点 **重新加载配置**（不用重启服务）
3. 新源会出现在列表里，点 **检测刮削源** 看它能不能取到结果
4. 状态「正常」且样本书名正确 → 去书架点「刮削」，源下拉里选它（或直接选「全部」）

### 排查表

| 现象 | 多半是 |
| --- | --- |
| 状态「疑似改版」 | `items` 路径写错，或接口返回的是 HTML 而不是 JSON |
| 状态「疑似风控」 | 请求太快，把 `throttle` 调大（比如 2~3 秒） |
| 状态「请求失败」 | 域名写错、网络不通，或 `timeout` 太短 |
| 取到数据但候选是空的 | `fields.title` 映射错了；title 为空的条目会被跳过 |
| 候选有书名但封面空白 | 封面域名不在白名单，加进 `settings.extra_cover_hosts` |
| 中文乱码 | 给该源加 `encoding: gb18030` |
| `id` 被跳过 | 和内置源（qidian/weread/douban/baike/dangdang…）重名了，换一个 |

### 进阶：接口需要复杂逻辑时

要先取 token、多次请求、或必须先跑 JS 的，配置就不够用了。这时照抄一个内置源：

1. 打开 `app/scraper_sources.py`
2. 参考 `search_weread` 写一个函数：

```python
def search_yours(keyword: str, timeout: int = 15) -> list:
    text = http_get("https://example.com/api?q=" + urllib.parse.quote(keyword),
                    {"User-Agent": UA_BROWSER}, timeout)
    data = json.loads(text)          # 需要区分改版时用 http_get_json(url, headers, timeout, label="你的源")
    out = []
    for item in data.get("list") or []:
        out.append({"title": item["name"], "author": item.get("author", ""),
                    "intro": item.get("desc", ""), "cover": item.get("cover", ""),
                    "publisher": "", "rating": 0, "url": "", "extra": ""})
    if not out:
        _raise_parse_error(text, "解析不出数据（看是不是改版了）")
    return out
```

3. 在文件末尾的 `BUILTIN_SOURCES` 里加一条：

```python
SourceDef("yours", "你的源名", search_yours, throttle=1.0, enabled=True,
          homepage="https://example.com", note="一句话说明"),
```

这样它就是一等公民源，缓存、请求节流、失败熔断、「检测刮削源」和失败分级全都自动生效。
拿不到数据时抛 `SourceBrokenError("...")`（疑似改版），被拦时抛 `SourceBlockedError("...")`（疑似风控），自检里会如实显示。

---

## 目录结构（宿主机挂载到容器）

| 宿主机目录 | 容器目录 | 用途 |
| --- | --- | --- |
| `./input` | `/input` | 待转换的 TXT 与同名封面图片 |
| `./meta` | `/meta` | 与 TXT 同名的元信息配置文件 |
| `./fonts` | `/fonts` | 苹方字体文件（TTF/OTF） |
| `./output` | `/output` | 转换结果 + 运行日志 |
| `./template` | `/template` | 固化模板；可放入 `sample.epub` 自动提取 |
| `./config` | `/config` | 刮削源配置 `sources.yml`（首次启动自动生成，可关源/加自定义源） |

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

也可以在 Web 端「刮削与编辑 → 上传本地封面」（或“刮封面”面板里的上传按钮）直接上传自己的图片：程序用同一套逻辑**立即**把它裁成目标分辨率、压缩成 JPEG，保存为 `input/书名.jpg`，页面上马上能看到处理后的结果。上传成功后会清掉同名旧封面，并同步改写元信息里的 `cover:` 指向。

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
| GET | `/api/scrape/<stem>` | 刮削元信息候选（`?source=all/qidian/weread/douban/baike/dangdang`；可加 `&title=&author=` 手动指定搜索词） |
| GET | `/api/covers/<stem>` | 获取封面候选列表（参数同上） |
| POST | `/api/cover/save` | 下载选中的封面 |
| POST | `/api/cover/upload` | 上传自定义封面（表单 `stem` + `cover` 文件），按当前分辨率自动裁切压缩 |
| GET | `/api/cover/proxy` | 候选封面预览代理（`?url=` 第三方封面地址，带 Referer + 缩略图缓存） |
| POST | `/api/scrape/batch` | 批量刮削书架（已有元信息的跳过，可用 `stems=a,b` 指定子集） |
| GET | `/api/sources` | 列出全部刮削源及其最近一次请求状态（不联网） |
| GET | `/api/sources/health` | 逐个真实请求各源做自检（`?keyword=活着` 可换测试词） |
| POST | `/api/sources/reload` | 重新读取 `sources.yml`（改完配置不用重启） |
| POST | `/api/sources/cache/clear` | 清空刮削结果缓存 |
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

# 运行内置测试（Python，82 项）
python tests/run_tests.py

# 前端交互冒烟测试（需要 node；用 DOM 桩验证抽屉/候选折叠/书架筛选逻辑）
node tests/ui_smoke.js

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
先在「刮削源」卡片点**“检测刮削源”**：能直接看到每个源是否正常、返回了什么样本、是“疑似改版”还是“疑似风控”。如果某个源显示异常，把它在 `sources.yml` 里关掉即可，其余源不受影响。
书名被文件名噪声带偏也很常见（比如 `斗破苍穹全集txt下载`、`[玄幻] 诡秘之主 爱潜水的乌贼`），程序会自动清洗并用清洗后的关键词再搜一轮；如果还不准，就在刮削面板里把“书名”改成准确名字再点“重新刮削”。仍然搜不到时直接手动填写书名/作者/简介；封面刮不到会自动生成占位封面，也可以自己上传一张。

**Q：提示字体缺失**  
确认 `fonts/` 里包含 `template.yml` 中 `provided_file` 对应的文件名（默认 `PingFangSC-Light.ttf`）。

**Q：模板校验不通过**  
查看 `output/failed/` 与日志中列出的具体不达标项；不要手工改 CSS，应检查输入文件（如封面尺寸异常、TXT 编码异常）后重转。

**Q：候选封面显示空白**  
豆瓣、百度百科的图床校验 `Referer`，浏览器直连会被 403，所以候选封面都走后端 `/api/cover/proxy` 代理取回。如果仍然空白，说明该图床规则又变了：可查看日志里的 `封面预览处理失败` 记录，或直接在刮削面板里上传一张自己的封面继续转换。

**Q：某本书章节没切对**  
正文段落若以“第七章……”开头可能被误判（工具已用“短行且不以句末标点结尾”过滤）；可在 `template/template.yml` 的 `text_cleaning.chapter_patterns` 中调整规则后重转。

---

## 免责声明

本项目仅用于个人学习与阅读排版研究。刮削数据来自第三方平台，版权归原作者及平台所有；请支持正版，勿将本工具用于传播未经授权的电子书。
