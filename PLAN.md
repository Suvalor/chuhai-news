---
title: chuhainews 长文精选站
created: 2026-11-12
updated: 2026-11-12
tags: [python, fastapi, seo, geo, mcp, webhook]
source: "Claude Design: Chuhai News工具站设计 / ChuhaiEditorial.dc.html；参考 github.com/joeseesun/xnews"
status: active
---

# 目的

把 Claude Design 的「编辑风」稿（纸色底、墨黑、朱红）做成能上线的 X 长文精选站：内容由 WebHook 推入，有管理后台管文章，对搜索引擎和 AI 引擎都友好，并开放 MCP 给 Agent 查询。

# 假设

- 采集和翻译在站外完成（爬虫 / Agent / 自动化平台），本站只负责接收、存储、展示、分发。原文与译文按段成对推送。
- 单机 SQLite 足够支撑早期流量；服务端渲染（SSR）比前端 SPA 更利于 SEO/GEO。
- 设计稿里的文章、作者都是示例，只用于本地演示，不作真实内容。

# 架构

| 层 | 选型 | 说明 |
|---|---|---|
| Web | FastAPI + Jinja2 SSR | 每篇文章独立 URL，正文直接在 HTML 里；JS 只做增强 |
| 数据 | SQLite（标准库 sqlite3） | `data/chuhai.db`，零运维 |
| 入库 | `POST /api/webhook` | HMAC-SHA256 签名或 Bearer 令牌；按 `external_id` 幂等 upsert；每次投递记日志 |
| MCP | 官方 `mcp` 2.x，Streamable HTTP 挂在 `/mcp` | 只读工具：搜索、取文章、分类、作者、最新 |
| 前端 | 原生 CSS/JS | 进场揭示动画、旋转字环（rotation）、翻译开关、键盘操作、滚动续读、收藏/赞 |
| 后台 | `/admin` | 口令登录（签名 Cookie），文章增改、上下架、作者、分类、WebHook 日志 |

## SEO

title / description / canonical / Open Graph / Twitter Card；JSON-LD（WebSite+SearchAction、Article、BreadcrumbList、Person、CollectionPage）；`sitemap.xml`、`feed.xml`（RSS）、语义化 HTML（article/h1/time）；草稿 `noindex`。

## GEO（生成式引擎可读性）

`robots.txt` 允许所有爬虫（含 GPTBot、ClaudeBot、PerplexityBot 等，显式列出）；`llms.txt` 导航 + `llms-full.txt` 全文；每篇文章 `/a/<slug>.md` 纯 Markdown 版本，并在 HTML 里用 `<link rel="alternate" type="text/markdown">` 声明；正文首段即结论、作者与原帖链接清楚标注来源。

# 步骤

1. 数据层与 WebHook → verify：签名错拒收、同一 external_id 二次推送只更新不重复
2. 前台六视图（首页 / 分类 / 作者 / 收藏 / 搜索 / 阅读）→ verify：浏览器桌面与 390 宽手机检查
3. SEO/GEO 端点 → verify：测试检查 sitemap、robots、llms、JSON-LD
4. MCP → verify：用 MCP 客户端走一次 initialize + tools/call
5. 管理后台 → verify：未登录拒绝；登录后改标题前台可见
6. RESULT.md + 索引登记

# 预期产出

可运行的项目、pytest 测试、README（部署、WebHook 协议、MCP 接入）、RESULT.md。

# 风险与回退

- 自动翻译不在范围内：没有译文时只显示原文，翻译开关隐藏。
- 管理后台单口令，适合个人运营；多人协作再换账号体系。
- SQLite 写并发有限；量大后换 PostgreSQL（查询集中在 `app/db.py`）。
