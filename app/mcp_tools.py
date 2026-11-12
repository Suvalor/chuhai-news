"""MCP 工具：检索与引用站内文章（公开）；publish_articles 推送文章（需 Bearer 令牌，规则与 WebHook 相同）。"""
import hmac
import os

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import db, limits
from .ingest import SCHEMA, BatchError, publish

mcp = MCPServer(
    name="chuhainews",
    instructions="chuhainews 收录 X 上值得读完的长文（原文 + 中英译文）。先用 search_articles 或 latest_articles 找文章，"
                 "再用 get_article 取全文。引用时请附上返回的 url 和原帖 source_url。"
                 "推送文章用 publish_articles，连接时请求头需带 Authorization: Bearer <写入令牌>。",
)


def _base():
    return os.getenv("SITE_URL", "http://127.0.0.1:8090").rstrip("/")


def _card(a):
    return {"slug": a["slug"], "title": a["title"], "title_translated": a["title_t"], "lang": a["lang"],
            "author": a["author_name"], "handle": a["author"], "category": a["cat"], "published_at": a["published_at"],
            "minutes": a["mins"], "views": a["views"], "likes": a["likes"], "saves": a["saves"], "heat": a.get("heat"),
            "url": f"{_base()}/a/{a['slug']}", "source_url": a["source_url"]}


@mcp.tool()
def search_articles(query: str, category: str | None = None, limit: int = 10) -> list[dict]:
    """按关键词搜索文章（标题、译名、正文、作者、类目）。category 可选：cog/money/ai/tool/global/create。"""
    return [_card(a) for a in db.list_articles(q=query.strip()[:100], cat=category, limit=max(1, min(limit, 50)))]


@mcp.tool()
def latest_articles(category: str | None = None, sort: str = "new", limit: int = 10) -> list[dict]:
    """列出文章。sort: new（最新）/ hot（热度）/ views（阅读量）。"""
    return [_card(a) for a in db.list_articles(cat=category, sort=sort if sort in db.SORTS else "new",
                                               limit=max(1, min(limit, 50)))]


@mcp.tool()
def get_article(slug: str) -> dict:
    """按 slug 取全文。paragraphs 每项为 {type: paragraph|heading|image, text 原文/图注, translation 译文, src 图片地址}。"""
    a = db.get_article(slug)
    if not a:
        raise ValueError(f"找不到文章 {slug}")
    return {**_card(a), "summary": a["summary"], "cover": a["cover"],
            "author_bio": {"zh": a["bio_zh"], "en": a["bio_en"]},
            "paragraphs": [{"type": {"h2": "heading", "img": "image"}.get(p[2], "paragraph") if len(p) > 2 else "paragraph",
                            "text": p[0], "translation": p[1], **({"src": p[3]} if len(p) > 3 else {})} for p in a["paras"]]}


@mcp.tool()
def list_categories() -> list[dict]:
    """列出全部类目及已发布文章数。"""
    return [{"key": c["key"], "zh": c["zh"], "en": c["en"], "description": c["tag_zh"], "articles": c["n"]}
            for c in db.categories()]


@mcp.tool()
def list_authors() -> list[dict]:
    """列出作者及已发布文章数。"""
    return [{"handle": a["handle"], "name": a["name"], "bio": a["bio_zh"] or a["bio_en"], "articles": a["n"],
             "x_url": f"https://x.com/{a['handle']}"} for a in db.authors()]


@mcp.tool()
def publish_articles(articles: list[dict], ctx: Context) -> dict:
    """推送文章：新建，或按 external_id 更新（再推同一篇 = 更新）。每批 1–100 篇，整批校验，一篇不合法则整批不写入。
    需要 MCP 连接的请求头带 `Authorization: Bearer <令牌>`（MCP_WRITE_TOKEN，未设置则为 WEBHOOK_SECRET）。字段与 WebHook（GET /api/webhook）完全相同；
    views/likes/saves 填来源平台（如 X）的浏览、赞、收藏数，再推时只替换来源部分，本站读者的增量保留。本站不做翻译。"""
    secret = os.getenv("MCP_WRITE_TOKEN") or os.getenv("WEBHOOK_SECRET", "")  # 设了独立令牌就只认它
    req = ctx.request_context.request
    auth = req.headers.get("authorization", "") if req is not None else ""
    key = "push:" + limits.client_ip(req)
    if limits.blocked(key):
        raise ToolError("认证失败过多，请 15 分钟后再试")
    if not secret or not hmac.compare_digest(auth, "Bearer " + secret):
        limits.fail(key)
        db.log_webhook(False, 401, "MCP：令牌不正确或未配置")
        raise ToolError("未授权：连接 MCP 时请求头需带 Authorization: Bearer <MCP_WRITE_TOKEN，未设置则为 WEBHOOK_SECRET>")
    try:
        return {"ok": True, "results": publish(articles, _base(), via="MCP")}
    except BatchError as e:
        raise ToolError(f"第 {e.index} 篇：{e}" if e.index is not None else str(e))


# 参数定义直接用 WebHook 的文章 Schema，让模型看到每个字段的说明和限制
_art = SCHEMA["$defs"]["article"]
mcp._tool_manager.get_tool("publish_articles").parameters = {  # 无公开 API 可改参数 Schema
    "type": "object", "required": ["articles"],
    "properties": {"articles": {"type": "array", "minItems": 1, "maxItems": 100, "items": _art}}}
