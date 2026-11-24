# GEO Monitor 操作指南

## 阶段及开关

入口：管理后台 /admin/geo（继承后台认证，noindex、no-store）。现在可采集 AI 导流、人工录入 Cloudflare 周度爬虫量及 GSC/Bing 收录样本、管理题库和事件。表默认空白，不把未知值填为 0。

- docs/geo-monitor.csv 预留 10 题 × 4 平台的 40 个手工记录槽位，不是已完成测试。每个新会话回答后填写 date、answer_text、规范化 URL JSON 数组、hit_mode（recommended/citation_only/miss）、rank_pos、竞品域名；网页不做自动化。人工记录与 API 结果分开。
- 导流：按北京时间日期、AI 平台、首次落地页去重；无标识的访问不计。原始 IP、UA 和哈希不落盘；重启后以最大值覆盖，可能少计。各平台 UV 不应相加当全站独立访客。仅可信反代可开启 GEO_TRUST_CF_IP。
- 先核实 GSC >=30 页，或连续两个月 AI 导流/品牌提及，或手工记录每月超过 2 小时，才启用付费自动执行。
- 先查官方价格、运行每平台一题的授权计费试验并核对账单；设置保守单次预算预留（含工具调用、搜索、输出、失败重试），再设 GEO_MAX_USD 和 GEO_ENABLED=1。预留硬熔断不代表服务商实际账单保证；供应商价格与搜索次数会变化，另设服务商账单限额。未配置预算或 Key 的平台跳过。
- 不输出或保存 API Key。原始答复与引用只保存在后台数据库。CSV 含真实结果后应作为内部文件，不提交公开仓库。

## 命令

在本地已安装依赖环境执行：

    .venv/bin/python -m app.geo_run --plan
    .venv/bin/python -m app.geo_run --import-csv docs/geo-monitor.csv

容器内执行（先把填好的 CSV 安全复制到容器 /tmp，镜像只包含 app）：

    docker compose exec -T web python -m app.geo_run --plan
    docker compose exec -T web python -m app.geo_run

后台立即运行只登记队列。宿主机 cron 每分钟消费队列，每月 1 日登记一轮（用真实部署目录替换 /path/to/repo；启用前不安装）：

    * * * * * cd /path/to/repo && docker compose exec -T web python -m app.geo_run
    0 3 1 * * cd /path/to/repo && docker compose exec -T web python -m app.geo_run --enqueue

SQLite 原子领取防止并行执行；异常中止记 partial，进程被强制杀死后 running 记录需管理员确认没有存活 worker 再恢复，不自动重试产生额外费用。

## API 口径

OpenAI Responses web_search + US 地区；Gemini generateContent + google_search；Perplexity Agent API web_search（Sonar 已进入迁移期）；xAI Responses web_search。模型和配置以返回记录为准。仅 OpenAI 显式地区参数，其余标为服务器默认。Gemini 仅向已知 Google 跳转域名发请求取 Location，不抓取目标正文。Grok inline citations 与其他搜索来源并非完全等价。推荐顺序是正文品牌/网址的启发式初判，应人工复核。DeepSeek 使用真值表；没配置预算时不调用判读。每月人工抽查不少于 10% 的已判结果，抽 5 题在四家网页对照。实际 API 连通性和计费未验证前不要上线自动化。

官方文档：
- https://developers.openai.com/api/docs/guides/tools-web-search
- https://ai.google.dev/gemini-api/docs/google-search
- https://docs.perplexity.ai/docs/agent-api/quickstart
- https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview
- https://docs.x.ai/developers/tools/citations

## 尚需真实外部数据

GSC/Bing API 及 Cloudflare GraphQL 拉取需要已验证站点与只读凭据，当前用手工录入。不能从 site: 搜索推断真实收录率。40 条手工基线、每月校准与真实账单均待运营填报；未进行 600 次真实付费调用。
