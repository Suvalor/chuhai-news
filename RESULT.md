---
title: chuhainews 实验结果
created: 2026-11-12
updated: 2026-11-12
status: done
related: [PLAN.md, README.md]
---

# 结果摘要

可运行的 Python 站点（FastAPI + Jinja2 SSR + SQLite），按 Claude Design「ChuhaiEditorial.dc.html」实现前台六种视图、中英界面、键盘操作、滚动续读与旋转字环动效；WebHook 入库（HMAC 签名或 Bearer，整批校验、按 external_id 幂等更新）；MCP（官方 SDK 2.3，Streamable HTTP，5 个只读工具）；管理后台（文章增改与上下架、作者、类目、WebHook 投递日志）；SEO/GEO（JSON-LD、sitemap、RSS、全开放 robots、llms.txt、逐篇 Markdown）。

验证：
- pytest 9 项全过（签名拒收、坏批次整批不入库、幂等更新、各页面、SEO/GEO 端点、计数不为负、续读片段、后台登录与编辑、MCP 走 HTTP 的 initialize / tools/list / tools/call）。
- 真实服务上用 curl 推送签名 WebHook：成功入库；错签名 401；草稿不对外（404）；MCP 搜索返回正确链接。
- 浏览器 1440 宽：首页、阅读页、翻译开关、赞、滚动续读（同作者读完自动换同类目，网址与侧栏同步）。390 宽：阅读页底栏与布局；首页、类目、作者、收藏、阅读五页无横向溢出；收藏页「赞过的」列出已赞文章；英文界面切换正常。

# 与假设的偏差

- 设计稿用前端状态切换视图；这里改成每个视图独立网址的服务端渲染，交互由 JS 增强，便于收录。
- 设计稿的旋转字环（rotation）放在标题右侧会把标题挤成孤行，改放到标题下方右侧留白。
- 进场动画在后台标签页会停在透明帧，加了「页面不可见时跳过动画」。
- 预览面板无法直接启动外置盘里的进程（系统权限），改为终端启动、面板连接。
- 管理后台在浏览器里只验证了登录页，登录后的操作由 pytest 覆盖，没在浏览器里点过。

# 我学到了什么

- mcp Python SDK 2.x 把 FastMCP 改名为 `MCPServer`（`mcp.server.mcpserver`）；挂进 FastAPI 时要把 `streamable_http_app()` 的路由并入，并在 FastAPI lifespan 里运行 `session_manager.run()`。
- `uvicorn --env-file` 依赖 python-dotenv，没装时直接崩溃。

# 下一步

- 部署：域名、HTTPS、`SITE_URL`、`SECRET_KEY`，接上真实的采集/翻译流水线推送 WebHook。
- 计数接口没有限流，上线前在反代加简单限速。
- 每篇文章的 og:image 尚未生成；需要时可加服务端生成的分享图。
- 演示库 `data/demo.db` 里有设计稿虚构文章和一条 WebHook 冒烟测试草稿，正式库用 `data/chuhai.db`，不要混用。
