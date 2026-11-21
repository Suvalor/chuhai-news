---
title: chuhainews 使用说明
created: 2026-11-12
updated: 2026-11-16
tags: [python, fastapi, webhook, mcp, seo, geo]
status: active
---

# chuhainews

X 长文精选站：WebHook 推入文章 → SQLite → 服务端渲染的编辑风前台 + 管理后台 + MCP。设计来自 Claude Design「Chuhai News工具站设计 / ChuhaiEditorial.dc.html」。

## 运行

```bash
uv sync                                  # 装依赖到项目内 .venv
.venv/bin/python -m pytest -q            # 全部测试
bash scripts/dev.sh                      # 本地预览 http://127.0.0.1:8090
```

`scripts/dev.sh` 第一次运行会生成 `dev.env`（随机的后台口令与 WebHook 密钥），并用演示库 `data/demo.db` 启动。演示库里的 14 篇文章与 7 位作者来自设计稿，**都是虚构示例**，不要发布到线上。

外置盘权限：预览面板不能直接启动外置盘里的进程，请在终端运行 `dev.sh`，预览只连接 `http://127.0.0.1:8090`。

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `WEBHOOK_SECRET` | 是 | 没设置时 WebHook 一律 503 拒收 |
| `CONTACT_EMAIL` | 线上建议 | 关于页公开的删除/更正联系邮箱；不设则关于页不显示联系方式 |
| `INDEXNOW_KEY` | 线上建议 | IndexNow 密钥（8–128 位字母、数字或 `-`，`openssl rand -hex 16`）。设置且 `SITE_URL` 为 https 时才推送 |
| `MCP_WRITE_TOKEN` | 否 | MCP 写入工具的独立令牌；不设则沿用 `WEBHOOK_SECRET`。分开设置后，泄露其一只需换其一 |
| `ADMIN_PASSWORD` | 是 | 没设置时后台不可登录 |
| `SECRET_KEY` | 建议 | 参与后台 Cookie 签名；更换后所有会话失效 |
| `SITE_URL` | 线上必填 | 例如 `https://chuhai.news`，用于 canonical、sitemap、MCP 返回的链接 |
| `CHUHAI_DB` | 否 | 默认 `data/chuhai.db` |

## Docker 部署

```bash
cp .env.example .env        # 填 ADMIN_PASSWORD / WEBHOOK_SECRET / SECRET_KEY / SITE_URL
docker compose up -d --build
```

- 服务只监听宿主 `127.0.0.1:8090`，由本机 Caddy/Nginx 反代并配 HTTPS。Caddy 一行即可：`你的域名 { reverse_proxy 127.0.0.1:8090 }`。
- 数据库在命名卷 `chuhai-data`（容器内 `/app/data/chuhai.db`），重建、升级镜像不丢数据。首次启动是空库。
- 搬入已有的库（例如本地 `real.db`）：先在本地 `sqlite3 data/real.db ".backup /tmp/chuhai.db"` 得到单文件，传到服务器后：

  ```bash
  docker compose create                                   # 已在运行则改为 docker compose stop web
  docker compose cp /tmp/chuhai.db web:/app/data/chuhai.db
  docker compose run --rm -u root web sh -c 'rm -f /app/data/chuhai.db-wal /app/data/chuhai.db-shm; chown app /app/data/chuhai.db'
  docker compose up -d
  ```

  拷进去的文件属于 root，必须 `chown`，否则服务只能读不能写；旧的 `-wal` 日志不删会套到新库上把数据弄坏。
- 备份：`docker compose exec web python -c "import sqlite3; sqlite3.connect('/app/data/chuhai.db').backup(sqlite3.connect('/app/data/backup.db'))"`，再 `docker compose cp web:/app/data/backup.db ./`。
- 更新代码：`docker compose up -d --build`。看日志：`docker compose logs -f web`。
- 管理后台：`https://你的域名/admin`，口令为 `.env` 里的 `ADMIN_PASSWORD`。

## WebHook 协议

选文核验、推送和更新已有文章的操作注意事项见 [文章推送规则](docs/20261116-webhook-publishing-guidelines.md)。

`POST /api/webhook`。完整参数定义（JSON Schema，含认证方式、字段、限制和示例）：`GET /api/webhook`，源文件 `app/webhook_schema.json`。让 LLM 推送时，把这个地址或文件交给它即可；也可以直接把 `$defs.article` 当作函数调用的参数定义。出错时 400/422 响应会带 `docs` 字段指回这里。

二选一认证：

- 签名（推荐）：请求头 `X-Chuhai-Timestamp: <当前 Unix 秒>` + `X-Chuhai-Signature: sha256=<hex(HMAC_SHA256(WEBHOOK_SECRET, 时间戳 + "." + 原始请求体))>`。时间戳与服务器相差超过 5 分钟拒收；同一签名只收一次，截获的请求重放会得到 409。
- `Authorization: Bearer <WEBHOOK_SECRET>`（给不能算签名的自动化平台和 LLM）

请求体可以是一篇对象、对象数组，或 `{"articles": [...]}`（每批 ≤100 篇，≤5MB）。**整批先校验再入库**：有一篇不合法则整批拒收，返回 422 和出错序号。

```json
{
  "external_id": "x:1789000000000",
  "source_url": "https://x.com/maralin/status/1789000000000",
  "lang": "en",
  "category": "cog",
  "title": "Notes on Taste",
  "title_translated": "关于品味的几条笔记",
  "author": {"handle": "maralin", "name": "Mara Lin", "bio_zh": "…", "bio_en": "…"},
  "paragraphs": [{"text": "Taste is not a gift.", "translation": "品味不是天赋。"}],
  "published_at": "2026-11-12T08:00:00+08:00",
  "status": "published",
  "summary": "可选，作为 meta description",
  "slug": "可选，自定义网址"
}
```

- `external_id` 与 `source_url` 至少给一个；同一 `external_id` 再次推送 = 更新正文。
- `cover`（可选，https）作封面：分享卡片 `og:image` / `twitter:image`（大图卡片）和 JSON-LD `image`；不给则取正文第一张图，都没有就不出图。页面正文里不显示封面。后台编辑页也能填。
- `author.name` / `bio_zh` / `bio_en` 为空时不覆盖已有作者资料；新作者没给名字时用 handle 作显示名。
- `views` / `likes` / `saves`（可选，非负整数）填来源平台的浏览、赞、收藏（X 书签）数。前台显示的是「来源数 + 本站读者产生的数」；再推只替换来源数，本站增量保留，没带的字段不改。
- 正文二选一：`paragraphs`（字符串或 `{text, translation}`），或 `body` + `body_translated`（按空行分段，第 N 段对应第 N 段）。
- 段落可带 `type`：`paragraph`（默认）、`heading`（小标题）、`image`（配图，必须给 `src`，只收 https；`text` / `translation` 作图注，可省略）。例：`{"type": "heading", "text": "第一步：采集", "translation": "Step 1: Capture"}`、`{"type": "image", "src": "https://pbs.twimg.com/media/xxx?format=jpg&name=large"}`。用 `body` 时写 `## 小标题`、`![图注](https://图片)`，后台编辑框也用这套写法。
- `summary` 是**编者导读**：编辑部对这篇的判断，2～3 句（约 80～200 字），不写「本文介绍了」。它显示在阅读页正文前，写进 `.md`（`## 编者导读`）和 `llms.txt`（取第一句），同时作 SEO 描述。整篇推送时本站不自动生成导读。
- 没给 `summary` 时不显示导读，描述自动取开头几段正文（跳过小标题和图）凑满 160 字。网址按译名生成时按词截断，不切半个单词。
- `category` 必须是已有类目的 key（默认 `cog / money / ai / tool / global / create`）；写错或不存在会 422 拒收，并列出可用 key。新类目先在后台「类目」页新建。
- `status` 为 `draft` 时只进后台，不对外。
- 没有译文时前台不显示翻译开关。整篇推送时本站不翻译，翻译在推送前完成；只推链接时由站点调用 DeepSeek 翻译（见下一节）。

每次投递（成功或失败）都记在后台「WebHook」页。

### 只推链接，站点自动导入

同一个 `POST /api/webhook`、同样的认证，请求体写成 `{"links": [...]}`（1–100 条）：

```json
{"links": ["https://x.com/builder/status/2120385360677204173 | tool | 入选理由", {"url": "https://x.com/a/status/123", "category": "ai"}]}
```

- 立刻返回 `202 {"ok": true, "queued": N}`，站点在后台逐篇处理，**直接发布**，每篇的结果写进后台「WebHook」页。
- 取原文和浏览／点赞／收藏数用 fxtwitter；DeepSeek 只负责翻译、写导读初稿、判类目（链接里没写类目或写错时）。DeepSeek 自己打不开链接。
- 原文少于 1500 字符的跳过；同一原帖站上已有译文的，只更新三个数字，不再翻译、不花 token。
- 服务器 `.env` 要有 `DEEPSEEK_API_KEY`（可选 `DEEPSEEK_MODEL`），否则返回 503。
- 队列在进程内存里，容器重启会丢掉没跑完的链接；看日志缺哪篇，重推那几条即可。

### 从 X 链接导入（本地脚本）

`scripts/import_x.py` 在本机跑，用来把一批 X 链接变成 WebHook 载荷。它先生成 JSON、人工核对后再推，适合要审稿的批次；全自动用上面的 `links`。给 LLM / Agent 的完整调用规范（报告状态码、什么时候可以推送）见 [scripts/README.md](scripts/README.md)。

```bash
.venv/bin/python scripts/import_x.py links.txt                   # 只生成 data/import/<时间>.json 并打印报告，不推送
.venv/bin/python scripts/import_x.py --push data/import/<时间>.json  # 核对后推送
```

- **链接文件**：一行一篇，写 `x.com/<用户>/status/<ID>` 或本站 `/a/<slug>`。同一行可以用 `|` 或 Tab 带上类目 key，Grok 给出的表格可以直接贴进来。
- **取原文**：用 fxtwitter 公开接口（`api.fxtwitter.com`），免费、不用登录。X Article 的小标题、列表、配图和浏览／点赞／收藏数都能拿到。这个接口不是官方的，失效时要改用 X API。
- **翻译**：设了 `DEEPSEEK_API_KEY`（可选 `DEEPSEEK_MODEL`，默认 `deepseek-flash`）才会翻译，DeepSeek 只返回译文。新文章还会多一次调用，生成导读初稿。不设 key 时译文留空。
- **长度门槛**：原文少于 `--min-chars`（默认 1500）字符的跳过。
- **站上已有的文章**（按原帖 ID 匹配最新 100 篇）：保留原网址、导读和发布状态。external_id 必须和旧的一致，否则会新建一篇重复文章：用 `--existing-prefix` 给出前缀，脚本会拿线上网址核对；前缀没确认的文章不翻译，也不写进 JSON。新文章用 `x:<ID>`，以草稿入库。
- **推送**：只推送 external_id、类目、译文都齐全的文章，读 `.env` 里的 `SITE_URL` 和 `WEBHOOK_SECRET`。

## 专题

后台「专题」页新建（只填网址 key 和标题，新建即草稿），进编辑页填导语、文章，再改成「已发布」。文章一行一篇：`slug | 入选理由`，也可直接贴文章网址，入选理由可省略；顺序就是前台顺序，重复的行只算一次，写错的 slug 会拒绝保存。前台只展示已发布、且至少有一篇在架文章的专题；文章下架后自动从专题里消失。

建议真实文章满 30 篇、一个专题能凑够 5～8 篇好文章时再发布专题，否则就是空壳页。

## 只读 JSON

给外部看板或信源聚合拉取（如「个人工作台 · 新闻与信源」），公开只读，范围与 RSS 相同：

- `GET /api/articles?limit=50&category=money`：最新发布的文章，按发布时间倒序，最多 100 篇。每篇含 `url`、`source_url`（X 原帖）、`title`、`title_translated`、`summary`、`author`、`category`、`published_at`、`views/likes/saves`。
- `GET /api/articles/{slug}`：同上字段加 `paragraphs`（`type` 为 `paragraph`/`heading`/`image`，带 `text`、`translation`，图片带 `src`）。

## MCP

Streamable HTTP 端点：`{SITE_URL}/mcp`（无状态，JSON 响应）。工具：

| 工具 | 作用 |
|---|---|
| `search_articles(query, category?, limit?)` | 按标题、译名、正文、作者、类目搜索 |
| `latest_articles(category?, sort?, limit?)` | 最新 / 热度 / 阅读量排序 |
| `get_article(slug)` | 全文，逐段原文 + 译文 |
| `list_categories()` / `list_authors()` | 类目与作者 |
| `list_topics()` | 已发布专题：导语、按编辑顺序排列的文章与每篇的入选理由 `note` |
| `publish_articles(articles)` | 推送文章（新建或按 `external_id` 更新）。字段、限制、整批校验都与 WebHook 相同，参数定义就是 `$defs.article`。需要连接请求头带 `Authorization: Bearer <令牌>`（`MCP_WRITE_TOKEN`，未设则为 `WEBHOOK_SECRET`），不带只能读；每次推送记入后台「WebHook」页 |

Claude Code 接入：

```bash
claude mcp add --transport http chuhainews https://你的域名/mcp                     # 只读
claude mcp add --transport http chuhainews https://你的域名/mcp --header "Authorization: Bearer $MCP_WRITE_TOKEN"  # 可推送（未设 MCP_WRITE_TOKEN 时用 WEBHOOK_SECRET）
```

## SEO / GEO

| 端点 / 手段 | 作用 |
|---|---|
| 服务端渲染 + 语义标签 | 每篇文章独立网址 `/a/<slug>`，正文直接在 HTML 里 |
| title / description / canonical / Open Graph / Twitter Card | 每页单独生成；搜索、收藏页 `noindex,follow` |
| JSON-LD | WebSite+SearchAction、Article（含作者、原帖 isBasedOn、阅读/赞计数）、BreadcrumbList、ProfilePage、CollectionPage（类目、专题）、AboutPage+Organization |
| `/sitemap.xml`、`/feed.xml` | 站点地图与 RSS |
| `/robots.txt` | 允许所有爬虫；显式列出 GPTBot、ClaudeBot、PerplexityBot 等 AI 爬虫 |
| `/llms.txt`、`/llms-full.txt` | 给大模型的站点导航与全文合集 |
| `/a/<slug>.md` | 每篇文章的 Markdown 版本，HTML 用 `rel=alternate type=text/markdown` 声明 |
| 编者导读 | `summary` 显示在正文前并进 `.md`、`llms.txt`：本站相对原帖的原创增量 |
| IndexNow | 文章或专题的公开状态变化时（发布、修改、下架、删除；一直是草稿的不推），后台线程通知 `api.indexnow.org`（Bing、Yandex、Naver 等，Google 不参与）；密钥文件在 `/<INDEXNOW_KEY>.txt`；失败只写日志 `chuhai.indexnow`，不影响发布 |
| `/about` | 运营方、选文标准、翻译方式（AI 翻译，未逐篇校对）、版权与删除联系；`llms.txt` 开头指向它 |
| `/t/<key>` 专题 | 编辑部策划的主题合集，带导语与每篇入选理由；进 sitemap、`llms.txt`、MCP |

## 让搜索引擎收录（上线后做一次）

1. 设置 `SITE_URL`（https）和 `INDEXNOW_KEY`，重启后确认 `https://你的域名/<INDEXNOW_KEY>.txt` 能打开、内容就是密钥。
2. Google Search Console 验证域名，提交 `https://你的域名/sitemap.xml`。Google 不支持 IndexNow，也停用了 sitemap ping，之后靠它定期重读 sitemap。
3. Bing Webmaster Tools 从 Search Console 导入站点。新文章由 IndexNow 推送；Bing 的索引也供 ChatGPT 搜索、Copilot、DuckDuckGo 等使用。
4. AI 爬虫（GPTBot、ClaudeBot、PerplexityBot 等）没有提交接口，靠 `robots.txt` 放行、sitemap、`llms.txt` 和站内链接自己发现。

## 前台交互

首页（按问题找 / 今日必读 / 作者 / 专题 / 榜单；没有专题时不显示专题区）、类目、作者、专题、搜索、收藏、阅读、关于，中英界面切换（Cookie）。阅读页：← → 切换、T 翻译、L 赞、S 收藏、ESC 返回、/ 搜索；滚到底自动接下一篇（同作者优先，读完换同类目），网址与侧栏随当前文章更新。动效：进场揭示、首页旋转字环（rotation）、翻译模糊淡入、点赞弹跳；系统开启「减少动态效果」时全部停用。赞和收藏存在浏览器本地，同时累加到服务器计数；服务器按访客 IP 的摘要去重（不存原始 IP）：同一 IP 对同一篇赞、收藏各算一次，阅读每天算一次。

## 安全

- 写入只有三个入口，都要授权：WebHook（签名或 Bearer）、MCP `publish_articles`（Bearer）、管理后台（口令）。MCP 读取工具与前台公开。
- 同一 IP 15 分钟内认证失败 10 次（后台登录、WebHook、MCP 写入分别计），之后暂时拒绝（429）。计数在进程内存里，重启清零。
- 线上必须走 HTTPS（由反代负责），否则 Bearer 令牌和后台口令会明文传输。密钥只放 `.env`，不进镜像和数据库。

## 目录

```
app/main.py        前台路由、SEO/GEO 端点、WebHook
app/db.py          SQLite 读写（换库只改这里）
app/ingest.py      WebHook 载荷校验
app/mcp_tools.py   MCP 工具
app/admin.py       管理后台
app/templates/     Jinja2 模板（admin/ 为后台）
app/static/        site.css / site.js / admin.css
scripts/           dev.sh 本地预览，seed_demo.py 演示数据，import_x.py 从 X 链接导入
tests/             pytest
```
