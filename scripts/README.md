---
title: scripts 目录说明（含 import_x.py 调用规范）
created: 2026-11-16
updated: 2026-11-16
tags: [chuhainews, scripts, import]
status: active
---

# scripts/

| 文件 | 用途 |
|---|---|
| `dev.sh` | 本地预览，用演示库启动，见根目录 README「运行」 |
| `seed_demo.py` | 生成演示数据（虚构文章，不得发布到线上） |
| `import_x.py` | 从 X 链接导入文章：取原文和指标，翻译，生成 WebHook 载荷，确认后推送到线上 |

---

# import_x.py 调用规范（写给 LLM / Agent）

## 一句话

给一份 X 链接清单，脚本用 fxtwitter 取原文、配图和浏览／点赞／收藏数，再用 DeepSeek 翻译，生成一份 JSON 供人核对。**推送是单独的第二步，必须先得到用户明确同意。**

## 什么时候用

- 用户给出一批 X 链接，或 Grok 那种「链接 | 类目 | 理由」表格，要求收录到 chuhainews。
- 用户要求把站上已有文章换成原文和译文（站上已有的按原帖 ID 匹配）。

不适用于：手写文章（直接用 WebHook 或后台）、非 X 来源。

## 前置条件

- 工作目录是项目根目录 `03 实验场/024-chuhai-news/`，用 `.venv/bin/python` 运行。
- 脚本自动读取根目录 `.env`，**不要把 `.env` 的内容打印出来或写进任何文件**：
  - `SITE_URL`：线上站点，用于查已有文章和推送。
  - `DEEPSEEK_API_KEY`：没有就不翻译，生成的文章会因为没有译文被推送步骤拒绝。
  - `DEEPSEEK_MODEL`：可不设，默认 `deepseek-flash`，2026-11-16 已核实可用。
  - `WEBHOOK_SECRET`：只有推送步骤用。
- 需要能访问外网：`api.fxtwitter.com`、`api.deepseek.com` 和 `SITE_URL`。

## 第一步：生成（不改线上，可以直接做）

1. 把链接写进 `data/import/links-<日期>.txt`（`data/` 不进版本库），一行一篇：

   ```
   https://x.com/Jayyanginspires/status/2116534636188729593 | money | 一手营销增长经验
   https://x.com/harris/status/2120167761934491654	money
   https://www.chuhainews.com/a/p-ry-x-2116534636188729593
   ```

   - 链接写 `x.com/<用户>/status/<ID>` 或本站 `/a/<slug>`。`x.com/i/article/...` 这种链接识别不了，要换成该 Article 所在帖子的 status 链接。
   - 类目写在同一行，用 `|` 或 Tab 分隔，只能是 `cog money ai tool global create`。站上已有的文章不写类目就沿用原来的。第三列之后的文字（入选理由等）会被忽略。
   - `#` 开头的行和空行会被忽略；同一个原帖 ID 出现多次只算一次。

2. 运行：

   ```bash
   .venv/bin/python scripts/import_x.py data/import/links-<日期>.txt [--existing-prefix "<前缀>"] [--min-chars 1500]
   ```

   - 运行时间主要花在翻译上，一篇 5 万字符的长文要好几分钟。**把超时设到 10 分钟以上，或者放到后台跑。**
   - 每篇打印一行 `[结果] 链接 类型 字符数 段数 图数`，最后一行给出生成的 JSON 路径。

## 第二步：读报告，按状态码处理

JSON 结构：`{"site", "articles": [就绪的 WebHook 载荷], "report": [每个链接一条]}`。`report[].status` 的含义和处理方式如下：

| status | 含义 | 处理 |
|---|---|---|
| `ready_new` | 新文章，已翻译，以草稿入库，导读是 DeepSeek 初稿 | 可以推送。告诉用户：推送后要在后台改导读并发布 |
| `ready_update` | 站上已有，会就地更新为原文加译文，保留原网址、导读和发布状态 | 可以推送。告诉用户：这一步会替换线上正文 |
| `need_external_id` | 站上已有，但没给 `--existing-prefix`，或前缀和线上网址对不上（看 `warn`） | **问用户要前缀**（后台 WebHook 日志或文章编辑页里的 external_id 中，原帖 ID 之前的部分），拿到后重跑。不要猜，猜错会在线上多出一篇重复文章 |
| `need_category` | 新文章没写类目 | 问用户，或按内容建议一个类目、经用户确认后写进链接行，再重跑 |
| `skipped_short` | 原文字符数低于 `--min-chars` | 列给用户看。只有用户同意时，才用 `--min-chars 0` 重跑 |
| `invalid` | 本地校验没过（如段落超过 400） | 把 `result` 原样报告给用户 |
| `failed` | 取文失败或翻译失败（如译文条数对不上） | 单独重跑这一篇。fxtwitter 长期失败就停下来报告，不要换成其他抓取方式 |

**为了省 token，脚本先校验、后翻译**：凡是 `need_*`、`skipped_short`、`invalid` 状态的文章，都没有调用 DeepSeek，重跑不会重复花钱。已经 `ready_*` 的文章重跑时会再翻译一次，所以只重跑有问题的链接：新建一个只含这些链接的链接文件。

只有 `ready_*` 的文章会写进 `articles`。

## 第三步：推送（会改线上，必须先得到用户同意）

推送前向用户报告三件事：
- 将新建几篇、更新几篇，各列标题和链接；
- 跳过了哪些，原因是什么；
- 抽查的结果：抽一篇看原文和译文是否逐段对应、配图是否在，以及第一段的原文和译文。

用户明确同意后再运行：

```bash
.venv/bin/python scripts/import_x.py --push data/import/<时间>.json
```

- 推送时只推 external_id、类目、译文三者都有的文章，其余跳过并打印数量。
- 成功时每篇打印「新建／更新 + 网址」。
- 失败时退出码非 0，打印 HTTP 状态和服务器返回内容。如果是 422 `{error, index}`，说明整批都没有写入：报告给用户，按提示改好后整批重推。
- 推送后，`ready_update` 和已发布的文章会自动通知 IndexNow。新文章是草稿，要等用户在后台发布后才对外。

## 硬性规则

1. 没有用户同意，不运行 `--push`。
2. 不猜 `--existing-prefix`。
3. 不改 JSON 里的原文和译文，不替用户重写导读。用户要求改时，只改他指定的那几处。
4. 不擅自降低 `--min-chars`。
5. 不打印、不转写 `.env` 里的任何值。

## 成本（2026-11-16）

- **fxtwitter**：免费，但不是官方接口，可能随时失效。
- **DeepSeek `deepseek-flash`**：按 token 计费。只送原文、只回译文，固定的系统提示词可以命中缓存。按 10 篇约 9 万字符原文估算，输出约 3 万 token，花费不到 $0.1；以 DeepSeek 官网价格为准。

## 已知限制

- **已有文章匹配**：只匹配站上最新的 100 篇已发布文章，更早的文章和草稿会被当成新文章，生成 `x:<ID>` 并建成重复草稿。
- **长帖标题**：长帖（note tweet）没有标题，用第一句代替，最长 110 个字符。
- **超链接和嵌入**：Article 正文里的超链接只保留文字；嵌入的推文、视频等非图片块会被丢掉。
