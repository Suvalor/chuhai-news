---
title: chuhainews 链接批量导入方案：X API 取文 + DeepSeek 翻译
created: 2026-11-16
updated: 2026-11-16
tags: [chuhainews, x-api, deepseek, import]
source: "用户需求：只给一批 X 链接，站点自动取文、翻译、入库，并节省 X API 次数与 token"
status: draft
related: [README.md, docs/20261115-editorial-intro-and-topics.md]
---

# 结论

在站内新增「链接导入」：后台粘贴链接，或调用 `POST /api/import {"urls": [...]}`。站点先用库里已有数据去重，再批量调 X API 取原文和指标，然后每篇调用一次 DeepSeek，完成翻译、导读初稿和类目判断，最后作为**草稿**入库，由你在后台审核后发布。

导入放在站内还是站外，花的钱一样。选择站内有两个原因：
1. 去重要查站内的数据库。放在站外，要么多一次查询，要么重复花钱。
2. 你只需要给链接。

这会推翻上一份方案里「本站不调用 LLM」的决定。导读仍以编辑部名义发布，所以 DeepSeek 只出初稿，入库状态一律是 draft。

真正省钱的是下面这几条，与接口放在哪里无关。

# 计费事实（2026-11-16 核对）

| 项目 | 价格 | 依据 |
|---|---|---|
| X 读帖 | $0.005 / 条，**按返回条数计费，与请求次数无关** | docs.x.com/x-api/getting-started/pricing |
| X 读用户 | $0.010 / 个 | 同上 |
| X 去重 | 同一条资源在同一个 UTC 日内重复读取，只收一次（官方称「软保证」） | 同上 |
| DeepSeek（deepseek-flash） | 输入未命中缓存 $0.15–0.30 / 百万 token，命中缓存约为其 1/50；输出 $0.60–1.20 / 百万 token；低峰时段半价 | 第三方汇总，**上线前以 DeepSeek 官网为准** |

估算 100 篇新文章的成本：
- X 读帖 $0.50。
- X 读作者 0～$1.00，已知作者不读。
- DeepSeek 按每篇约 4k 输入、5k 输出 token 计，大约 $0.3～0.6。

一次性导入并不贵。**长期最大的开销是指标刷新**：100 篇文章每天刷一次，每月约 $15。

# 七条省钱规则

1. **先查库再调 X。**已存在的 `external_id`（`x:<帖子ID>`）跳过取文，只有显式要求刷新时才读。
2. **不展开作者。**取帖时不带 `expansions=author_id`，只对库里没有的作者调用 `GET /2/users?ids=`，一次最多 100 个。已知作者一分不花。
3. **一次请求打包 100 个 ID**：`GET /2/tweets?ids=…&tweet.fields=created_at,lang,author_id,public_metrics,note_tweet,article`。这样做省的是请求次数和限流，不省钱，但实现最简单。
4. **原始返回存进库里。**X 的原文先落表，DeepSeek 失败后重试时直接读库，不再调 X。
5. **每篇只翻译一次。**以原文哈希判断，原文没变就不再调用 DeepSeek。刷新指标完全不经过 DeepSeek。
6. **DeepSeek 提示词。**
   - 固定的系统提示放在最前面，包括翻译规则、类目表、导读写法和输出 JSON 格式，以命中前缀缓存。
   - 一次调用同时产出 `title_translated`、`paragraphs_translated[]`、`summary` 和 `category`。
   - **只输出译文，不回显原文。**输出 token 最贵，回显原文会让输出成本翻倍。
   - 批量任务排在 DeepSeek 的低峰时段运行。
7. **指标刷新分级。**每篇只在发布后第 1、3、7 天各刷一次，之后不再刷，每篇终身约 $0.015。字段对应关系：`impression_count` 对应 views，`like_count` 对应 likes，`bookmark_count` 对应 saves。

整条链路是普通程序在跑，**不让 Claude 或其他 Agent 逐篇读文章**，所以不消耗对话 token。

# 流程

```
链接（后台文本框 / POST /api/import，Bearer 认证同 WebHook）
 → 解析出帖子 ID，查库去重
 → X 批量取帖（≤100/次）+ 只取新作者
 → 原文落 imports 表（状态：fetched）
 → 后台线程逐篇调用 DeepSeek（状态：translated / failed，可重试）
 → 复用 ingest.publish(..., via="Import") 以 draft 入库（沿用现有校验；草稿不推送 IndexNow）
 → 后台审核导读和译文 → 发布（此时才推送 IndexNow）
```

正文取法：
- X Article 读 `article` 字段。
- 长帖读 `note_tweet.text`。
- 普通短帖不收录，在导入结果里标「非长文」。

# 改动范围（约 6 个文件，需你确认后再动）

| 文件 | 改动 |
|---|---|
| `app/xapi.py`（新） | 解析链接、批量取帖、取新作者；只用标准库 urllib |
| `app/translate.py`（新） | DeepSeek 调用：固定提示词，按 JSON 输出，失败重试 |
| `app/db.py` | 新增 `imports` 表（url、external_id、raw、state、error、updated_at），以及刷新指标时只更新 `src_*` 字段的函数 |
| `app/main.py` / `app/admin.py` | `POST /api/import`；后台「导入」页（粘贴链接、看状态、重试）；「刷新指标」按钮或接口 |
| `.env.example`、README | `X_BEARER_TOKEN`、`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL=deepseek-flash` |
| `tests/test_app.py` | 用桩替换 X 与 DeepSeek：去重不调 X、已知作者不读、译文条数对不上判失败、入库为 draft |

# 动工前必须先验证的三件事

1. **`article` 对象的结构。**官方数据字典只写了「用于获取 Article 的文本和实体」，没有列出子字段，标题、正文、配图分别放在哪个键里都不清楚。第一步用你的 X key 读 1 条真实 Article，花费 $0.005，按真实返回写解析代码。
2. **Article 链接的格式。**`x.com/i/article/<ID>` 里的 ID 很可能不是帖子 ID，不能直接查 `/2/tweets`。初版只收 `x.com/<用户>/status/<ID>` 形式的链接；其他格式要等第 1 步验证后再定。
3. **配图。**Article 里的图片能否通过 API 拿到 https 地址，同样等第 1 步确认。拿不到时，初版只导入文字。

# 补充：用 Grok 订阅找链接（2026-11-16）

用户用的是 Grok 订阅。Grok 只负责**找链接**：在 X 上按选文标准搜，每篇只输出一行 `链接 | 类目 | 一句入选理由`，每篇大约 20～30 个 token。链接**不必先存成本地文件**，直接贴进后台「导入」框，或者推到 `POST /api/import`。上传的只是链接，原文、指标和译文都由服务器自己去取。

**不让 Grok 输出原文和译文**，原因有三：
- 这是最费 token 的部分：一篇 3000 词的长文，原文加译文要输出约 9k token。
- 每条消息只能处理 1～3 篇，而且每次都要你手动复制。
- 模型转写原文不保证逐字一致，本站标的是「原文」，这一点不能出错。

原文从哪里取，需要用户决定：
1. **X API 按量付费**（推荐）。$0.005 一篇，指标是准确数字。另需购买 X 开发者额度，这和 Grok 订阅是两回事。
2. **Grok 输出全文 JSON**。不额外花钱，但最费 token，而且要手动操作。
3. **第三方免费接口**（如 fxtwitter）。非官方，随时可能失效，条款上属于灰色地带。

翻译仍然用 DeepSeek，每篇约 $0.005。如果用户手上的是 xAI 的 API key 而不是 grok.com 订阅，也可以让站点调 Grok 来翻译，但同样按 token 计费。

# 补充二：实测 fxtwitter，改用本地脚本（2026-11-16，取代上面的站内导入）

**实测**：用户给的 10 个链接，`GET https://api.fxtwitter.com/<用户>/status/<ID>` 全部返回 200。
- 4 篇是 X Article：`tweet.article` 带有 `title` 和 `content.blocks`（Draft.js 格式，块类型为 unstyled / header-two / 有序列表 / 无序列表 / atomic）。配图的取法：`entityMap` 给出 mediaId，再到 `media_entities[].media_id` 找到对应项，取 `media_info.original_img_url`（pbs.twimg.com 的 https 地址）。
- 6 篇是长帖（`is_note_tweet`），全文在 `text` 里。
- 指标齐全：`views`、`likes`、`bookmarks`。
- 10 篇原文合计约 9 万字符，其中最长一篇 53,874 字符。

基于以上结果，方案改为 **Grok 找链接 → 本地脚本 `scripts/import_x.py links.txt` → 现有 WebHook**。站点代码不用改，也不放 X 或 DeepSeek 的 key，「本站不调用 LLM」的决定得以保留。

- **取文**：fxtwitter 每篇一次 GET，免费、不登录。失败的篇目才改用 X API 按量付费（$0.005 一篇）。不用无头浏览器：它要登录 X，有封号风险，页面结构一变就失效。
- **翻译**：DeepSeek 只翻译、不回显原文。长文按约 60 个块切成一批，每批返回的数组长度必须和输入一致。估算 10 篇约输出 3 万 token，不到 $0.1。
- **转换**：
  - header-two 转成小标题。
  - 列表项转成正文段，前面加「• 」或序号。
  - atomic 块转成配图。
  - 第一张图或 `cover_media` 作封面。
- **更新已有页面**：本站已经有这 10 篇，但内容是编辑部写的中文要点，`lang=zh`，没有译文。按 `upsert_article` 的写法，处理时有三点要注意：
  1. 必须沿用原来的 `external_id`，才能就地更新。线上 slug 是 `p-ry-x-<ID>`，可以推出原 ID 以 `…ry:x:<ID>` 结尾，完整前缀要到后台查。用错 ID 会新建一篇重复文章。
  2. 不传 `slug` 就保留原网址，已被收录的链接不会失效。
  3. 不传 `summary` 会被清空。所以脚本要先读取 `/api/articles/<slug>`，把现有导读和类目带上，导读不重写，也就不花 token。
- **流程**：先 `--dry-run`，在本地生成 JSON 供核对，确认后再推送。
- **门槛**：原文少于 1500 字符的跳过并注明原因。例如本批 Gojiberry 那条只有 357 字符，是一条课程宣传帖，不算长文。

# 风险

- **X 条款**：用 API 读取后全文翻译、转载他人内容，属于版权和平台条款问题，与上一份方案中的版权风险相同。关于页的删除联系方式（`CONTACT_EMAIL`）上线前要填好。
- **密钥**：站点会持有 X 和 DeepSeek 两个付费 key，只放在 `.env`，不进库、不进日志。
- **翻译质量**：关于页已写明「AI 翻译，未逐篇校对」，与本方案一致；导读仍需人工过一眼。

# 补充三：WebHook 只推链接，站点自动导入（2026-11-21，用户决定）

用户要全自动：Grok Bot 往 `POST /api/webhook` 推 `{"links": [...]}`，站点后台用 fxtwitter 取文和指标，DeepSeek 翻译、写导读、判类目，**直接发布**。这推翻了「本站不调用 LLM」和「新文章以草稿入库」两条决定（只对链接模式生效；整篇推送照旧）。

- 类目：Grok 推链接时带上；没带或写错由 DeepSeek 判。
- 指标：只在入库时取一次；重推同一链接时，已有译文的只更新浏览／点赞／收藏，不再翻译。
- 代码：取文和翻译函数从 `scripts/import_x.py` 移到 `app/importer.py`，脚本改为引用，本地「生成 → 核对 → 推送」流程保留。
- 服务器 `.env` 需要 `DEEPSEEK_API_KEY`，否则链接导入返回 503。
