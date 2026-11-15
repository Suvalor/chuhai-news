"""chuhainews：前台（SSR）+ SEO/GEO 端点 + WebHook + MCP。"""
import hashlib
import hmac
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, indexnow, limits
from .i18n import HOT_QUERIES, T, count, fmt
from .ingest import SCHEMA as WEBHOOK_SCHEMA, BatchError, publish
from .limits import client_ip
from .mcp_tools import mcp

HERE = Path(__file__).resolve().parent
PAGE = 30
EXCEL_RATIO_PATH = "/tools/excel-ratio-converter"
CJK = re.compile(r"[一-鿿]")
AI_BOTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-User", "Claude-SearchBot", "anthropic-ai",
           "PerplexityBot", "Perplexity-User", "Google-Extended", "Applebot-Extended", "Bytespider", "CCBot",
           "meta-externalagent", "Amazonbot", "DuckAssistBot", "cohere-ai", "MistralAI-User"]

templates = Jinja2Templates(directory=HERE / "templates")
# 静态文件网址带修改时间作版本号：文件一改网址就变，浏览器不会继续用缓存里的旧 CSS/JS
templates.env.globals["asset"] = lambda name: f"/static/{name}?v={int((HERE / 'static' / name).stat().st_mtime)}"


@asynccontextmanager
async def lifespan(app):
    db.init()
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="chuhainews", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
# MCP Streamable HTTP 端点直接挂到 /mcp（无状态 + JSON 响应，便于部署在任意反代后面）
_mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
                                   host=os.getenv("CHUHAI_BIND", "0.0.0.0"))
app.router.routes.extend(_mcp_app.routes)



# ---------- 公共工具 ----------

def site_url(request):
    return os.getenv("SITE_URL", str(request.base_url)).rstrip("/")


def ui_of(request):
    return "en" if request.cookies.get("ui") == "en" else "zh"


def font(s):
    return "zh" if CJK.search(s or "") else "en"


def parse_ts(iso):
    d = datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def date_label(iso, ui):
    days = (datetime.now(timezone.utc) - parse_ts(iso)).days
    if ui == "en":
        return "Today" if days < 1 else "Yesterday" if days == 1 else f"{days}d ago" if days < 7 else f"{days // 7}w ago" if days < 60 else iso[:10]
    return "今天" if days < 1 else "昨天" if days == 1 else f"{days} 天前" if days < 7 else f"{days // 7} 周前" if days < 60 else iso[:10]


def deco(rows, ui):
    t = T[ui]
    for r in rows:
        r["cat_name"] = r["cat_zh"] if ui == "zh" else r["cat_en"]
        r["date_label"] = date_label(r["published_at"], ui)
        r["date"] = r["published_at"][:10]
        r["f"], r["ft"] = font(r["title"]), font(r["title_t"])
        r["lang_label"] = t["origEn"] if r["lang"] == "en" else t["origZh"]
        r["lang_short"] = t["short_" + r["lang"]]
        r["views_f"], r["likes_f"], r["saves_f"] = fmt(r["views"]), fmt(r["likes"]), fmt(r["saves"])
    return rows


def render(request, name, status=200, **kw):
    ui = ui_of(request)
    base = site_url(request)
    now = datetime.now()
    kw.setdefault("canonical", base + request.url.path)
    kw.setdefault("robots", "index,follow,max-image-preview:large,max-snippet:-1")
    return templates.TemplateResponse(request, name, {
        "ui": ui, "t": T[ui], "base": base, "path": request.url.path, "font": font, "fmt": fmt,
        "count": lambda n: count(n, ui), "today": now.strftime("%Y.%m.%d"), "year": now.year, **kw}, status_code=status)


def website_ld(base, ui):
    return {"@context": "https://schema.org", "@type": "WebSite", "name": "chuhainews", "url": base + "/",
            "description": T[ui]["siteDesc"], "inLanguage": ["zh-CN", "en"],
            "potentialAction": {"@type": "SearchAction", "target": base + "/search?q={q}", "query-input": "required name=q"}}


def list_ld(base, name, url, rows):
    return {"@context": "https://schema.org", "@type": "CollectionPage", "name": name, "url": url,
            "mainEntity": {"@type": "ItemList", "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "url": f"{base}/a/{r['slug']}", "name": r["title"]}
                for i, r in enumerate(rows)]}}


def crumbs_ld(items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": n, "item": u} for i, (n, u) in enumerate(items)]}


def page_of(request):
    try:
        return max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        return 1


def sort_of(request):
    s = request.query_params.get("sort", "hot")
    return s if s in db.SORTS else "hot"


def listing(request, ui, **filters):
    page, sort = page_of(request), sort_of(request)
    rows = deco(db.list_articles(sort=sort, limit=PAGE + 1, offset=(page - 1) * PAGE, **filters), ui)
    return dict(items=rows[:PAGE], sort=sort, page=page, has_more=len(rows) > PAGE)


# ---------- 前台页面 ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    ui, base = ui_of(request), site_url(request)
    cats = db.categories()
    for c in cats:
        top = db.list_articles(cat=c["key"], limit=1)
        c["top"] = top[0]["title"] if top else ""
    featured = deco(db.list_articles(limit=1), ui)
    authors = db.authors()
    topics = topic_cards(ui)
    lst = listing(request, ui)
    total = sum(c["n"] for c in cats)
    t = T[ui]
    return render(request, "index.html", view="home", cats=cats, featured=featured[0] if featured else None,
                  authors=authors, topics=topics, total=total, list_count=count(total, ui),
                  list_label=("05" if topics else "04") + " — " + t["rankLabel"],
                  title="chuhainews — " + ("值得读完的长文" if ui == "zh" else "Long reads worth finishing"),
                  description=t["siteDesc"], canonical=base + "/",
                  ld=[website_ld(base, ui), list_ld(base, t["rankLabel"], base + "/", lst["items"])], **lst)


@app.get("/c/{key}", response_class=HTMLResponse)
def category(request: Request, key: str):
    ui, base = ui_of(request), site_url(request)
    cats = db.categories()
    cat = next((c for c in cats if c["key"] == key), None)
    if not cat:
        return not_found(request)
    t = T[ui]
    name, alt = (cat["zh"], cat["en"]) if ui == "zh" else (cat["en"], cat["zh"])
    lst = listing(request, ui, cat=key)
    url = f"{base}/c/{key}"
    return render(request, "index.html", view="cat", cats=cats, cat=cat, cat_name=name, cat_alt=alt,
                  cat_no=f"{cats.index(cat) + 1:02d}", cat_tag=cat["tag_zh"] if ui == "zh" else cat["tag_en"],
                  list_label=f"{name} — {t['rankLabel']}", list_count=count(cat["n"], ui),
                  title=(f"{name} · {alt}" if alt != name else name) + " — chuhainews", description=(cat["tag_zh"] if ui == "zh" else cat["tag_en"]),
                  canonical=url, ld=[list_ld(base, name, url, lst["items"]),
                                     crumbs_ld([("chuhainews", base + "/"), (name, url)])], **lst)


@app.get("/u/{handle}", response_class=HTMLResponse)
def author_page(request: Request, handle: str):
    ui, base = ui_of(request), site_url(request)
    au = db.author(handle)
    if not au:
        return not_found(request)
    t = T[ui]
    bio = (au["bio_zh"] or au["bio_en"]) if ui == "zh" else (au["bio_en"] or au["bio_zh"])
    lst = listing(request, ui, author=handle)
    url = f"{base}/u/{handle}"
    person = {"@context": "https://schema.org", "@type": "ProfilePage", "url": url,
              "mainEntity": {"@type": "Person", "name": au["name"], "alternateName": "@" + handle,
                             "description": bio, "sameAs": [f"https://x.com/{handle}"]}}
    return render(request, "index.html", view="author", au=au, bio=bio, list_label=t["articlesOf"],
                  list_count=count(au["n"], ui), title=f"{au['name']} (@{handle}) — chuhainews",
                  description=bio or f"{au['name']} — {t['articlesOf']}", canonical=url,
                  ld=[person, crumbs_ld([("chuhainews", base + "/"), (au["name"], url)])], **lst)


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    ui = ui_of(request)
    q = q.strip()[:100]
    items = deco(db.list_articles(q=q, sort=sort_of(request), limit=100), ui) if q else []
    found_authors = [a for a in db.authors() if q and q.lower() in " ".join(
        [a["name"], a["handle"], a["bio_zh"], a["bio_en"]]).lower()]
    return render(request, "index.html", view="search", q=q, items=items, sort=sort_of(request), page=1,
                  has_more=False, hot=HOT_QUERIES[ui], found_authors=found_authors, list_label=T[ui]["results"],
                  list_count=count(len(items), ui), title=(f"{q} — " if q else "") + T[ui]["search"] + " — chuhainews",
                  description=T[ui]["siteDesc"], robots="noindex,follow")


@app.get("/saved", response_class=HTMLResponse)
def saved(request: Request):
    ui = ui_of(request)
    return render(request, "index.html", view="saved", items=[], title=T[ui]["saved"] + " — chuhainews",
                  description=T[ui]["siteDesc"], robots="noindex,follow")


def topic_cards(ui):
    """已发布且至少有一篇在架文章的专题。"""
    out = []
    for tp in db.topics("published"):
        n = len(db.topic_articles(tp))
        if n:
            out.append({**tp, "n": n, "lead": first_sentence(tp["intro"])})
    return out


@app.get("/t/{key}", response_class=HTMLResponse)
def topic_page(request: Request, key: str):
    ui, base = ui_of(request), site_url(request)
    tp = db.topic(key)
    if not tp:
        return not_found(request)
    items = deco(db.topic_articles(tp), ui)
    url = f"{base}/t/{key}"
    return render(request, "index.html", view="topic", tp=tp, items=items, sort="topic", page=None, has_more=False,
                  list_label=T[ui]["topics"], list_count=count(len(items), ui), title=f"{tp['title']} — chuhainews",
                  description=tp["intro"][:160] or tp["title"], canonical=url,
                  robots="index,follow" if items else "noindex,follow",  # 文章全下架的专题不进索引
                  ld=[{**list_ld(base, tp["title"], url, items), **({"description": tp["intro"]} if tp["intro"] else {})},
                      crumbs_ld([("chuhainews", base + "/"), (tp["title"], url)])])


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    ui, base = ui_of(request), site_url(request)
    email = os.getenv("CONTACT_EMAIL", "").strip()
    org = {"@type": "Organization", "name": "chuhainews", "url": base + "/", **({"email": email} if email else {})}
    ld = {"@context": "https://schema.org", "@type": "AboutPage", "url": base + "/about", "inLanguage": ["zh-CN", "en"],
          "mainEntity": org}
    return render(request, "index.html", view="about", email=email, items=[],
                  title=T[ui]["about"] + " — chuhainews", canonical=base + "/about",
                  description="chuhainews 由 chuhainews 编辑部运营：选文标准、AI 翻译说明、版权与删除联系方式。" if ui == "zh" else
                  "Who runs chuhainews, how articles are picked and translated, and how authors can request removal.",
                  ld=[ld, crumbs_ld([("chuhainews", base + "/"), (T[ui]["about"], base + "/about")])])


def article_ctx(a, ui, base):
    bio = (a["bio_zh"] or a["bio_en"]) if ui == "zh" else (a["bio_en"] or a["bio_zh"])
    rails = {c: deco(db.context_list(a, c), ui) for c in ("author", "cat")}
    nxt = {c: db.next_for(a, c) for c in ("author", "cat")}
    return dict(a=a, bio=bio, rails=rails, nxt=nxt, url=f"{base}/a/{a['slug']}")


@app.get(EXCEL_RATIO_PATH, response_class=HTMLResponse)
def excel_ratio_converter(request: Request):
    ui = ui_of(request)
    t = T[ui]
    base = site_url(request)
    url = base + EXCEL_RATIO_PATH
    ld = {"@context": "https://schema.org", "@type": "WebApplication", "name": t["excelRatio"],
          "url": url, "description": t["excelDesc"], "applicationCategory": "BusinessApplication",
          "operatingSystem": "Any", "inLanguage": ["zh-CN", "en"], "isAccessibleForFree": True,
          "offers": {"@type": "Offer", "price": "0"}}
    return render(request, "index.html", view="excel-ratio", title=f"{t['excelRatio']} | chuhainews",
                  description=t["excelDesc"], ld=[ld, crumbs_ld([("chuhainews", base + "/"), (t["excelRatio"], url)])])


@app.get("/a/{slug}.md", response_class=PlainTextResponse)
def article_md(request: Request, slug: str):
    a = db.get_article(slug)
    if not a:
        return PlainTextResponse("not found", 404)
    return PlainTextResponse(to_markdown(a, site_url(request)), media_type="text/markdown; charset=utf-8")


@app.get("/a/{slug}", response_class=HTMLResponse)
def read(request: Request, slug: str):
    ui, base = ui_of(request), site_url(request)
    a = db.get_article(slug)
    if not a:
        return not_found(request)
    deco([a], ui)
    c = article_ctx(a, ui, base)
    desc = a["summary"] or excerpt(a)
    image = a["cover"] or next((p[3] for p in a["paras"] if len(p) > 3), None)  # 没封面就用正文第一张图
    ld = {"@context": "https://schema.org", "@type": "Article", "headline": a["title"][:110],
          "alternativeHeadline": a["title_t"] or None, "description": desc, "url": c["url"],
          "mainEntityOfPage": c["url"], "inLanguage": "zh-CN" if a["lang"] == "zh" else "en",
          "datePublished": a["published_at"], "dateModified": a["updated_at"], "wordCount": len(" ".join(p[0] for p in a["paras"])),
          "timeRequired": f"PT{a['mins']}M", "articleSection": a["cat_name"],
          "author": {"@type": "Person", "name": a["author_name"], "url": f"https://x.com/{a['author']}"},
          "publisher": {"@type": "Organization", "name": "chuhainews", "url": base + "/"},
          "isBasedOn": a["source_url"] or None, "image": image,
          "interactionStatistic": [
              {"@type": "InteractionCounter", "interactionType": "https://schema.org/ReadAction", "userInteractionCount": a["views"]},
              {"@type": "InteractionCounter", "interactionType": "https://schema.org/LikeAction", "userInteractionCount": a["likes"]}]}
    ld = {k: v for k, v in ld.items() if v is not None}
    crumbs = crumbs_ld([("chuhainews", base + "/"), (a["cat_name"], f"{base}/c/{a['cat']}"), (a["title"], c["url"])])
    return render(request, "read.html", view="read", title=f"{a['title']} — {a['author_name']} · chuhainews",
                  description=desc, canonical=c["url"], og_type="article", ld=[ld, crumbs], image=image,
                  md_url=c["url"] + ".md", **c)


@app.get("/fragment/next", response_class=HTMLResponse)
def fragment_next(request: Request, slug: str, ctx: str = "author", exclude: str = ""):
    """滚动续读：返回下一篇的文章片段（含侧栏模板），没有下一篇时 204。"""
    ui = ui_of(request)
    cur = db.get_article(slug)
    if not cur:
        return Response(status_code=204)
    nxt, how = db.next_for(cur, "cat" if ctx == "cat" else "author", exclude.split(",")[:20])
    if not nxt:
        return Response(status_code=204)
    a = deco([db.get_article(nxt["slug"])], ui)[0]
    t = T[ui]
    label = t["upNext"] + " — " + (f"{t['sameAuthor']} · {a['author_name']}" if a["author"] == cur["author"]
                                   else f"{t['sameCat']} · {a['cat_name']}" if a["cat"] == cur["cat"] else a["cat_name"])
    return render(request, "_article.html", divider=label, **article_ctx(a, ui, site_url(request)))


@app.get("/fragment/rows", response_class=HTMLResponse)
def fragment_rows(request: Request, slugs: str = ""):
    ui = ui_of(request)
    wanted = [s for s in slugs.split(",") if s][:200]
    rows = deco(db.list_articles(slugs=wanted, sort="new", limit=200), ui) if wanted else []
    return render(request, "_rows.html", items=rows, sort="new")


@app.post("/api/a/{slug}/{action}")
def interact(request: Request, slug: str, action: str):
    field, on = {"view": ("views", True), "like": ("likes", True), "unlike": ("likes", False),
                 "save": ("saves", True), "unsave": ("saves", False)}.get(action, (None, True))
    if not field:
        return JSONResponse({"error": "unknown action"}, 404)
    # 只存 IP 的摘要，不存原始 IP
    who = hashlib.sha256((os.getenv("SECRET_KEY", "") + "|" + client_ip(request)).encode()).hexdigest()[:24]
    n = db.interact(slug, field, on, who)
    return JSONResponse({field: n}) if n is not None else JSONResponse({"error": "not found"}, 404)


@app.get("/ui/{lang}")
def set_ui(lang: str, next: str = "/"):
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    r = RedirectResponse(target, 303)
    r.set_cookie("ui", "en" if lang == "en" else "zh", max_age=31536000, samesite="lax")
    return r


def not_found(request):
    ui = ui_of(request)
    return render(request, "index.html", status=404, view="404", items=[], title="404 — chuhainews",
                  description=T[ui]["siteDesc"], robots="noindex")


@app.exception_handler(404)
async def handle_404(request, exc):
    if request.url.path.startswith(("/api/", "/mcp", "/static/")):
        return JSONResponse({"error": "not found"}, 404)
    return not_found(request)


# ---------- SEO / GEO ----------

@app.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request):
    base = site_url(request)
    bots = "".join(f"User-agent: {b}\nAllow: /\n\n" for b in AI_BOTS)
    return (f"# 允许所有爬虫，包括 AI 搜索与训练爬虫\nUser-agent: *\nAllow: /\n\n{bots}"
            f"Sitemap: {base}/sitemap.xml\n# LLM 导航: {base}/llms.txt\n")


def xml_escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@app.get("/sitemap.xml")
def sitemap(request: Request):
    base = site_url(request)
    arts = db.list_articles(sort="new", limit=50000)
    urls = [(base + "/", arts[0]["updated_at"] if arts else None)]
    urls += [(base + "/about", None), (base + EXCEL_RATIO_PATH, None)]
    urls += [(f"{base}/t/{tp['key']}", tp["updated_at"]) for tp in topic_cards("zh")]
    urls += [(f"{base}/c/{c['key']}", None) for c in db.categories() if c["n"]]
    urls += [(f"{base}/u/{a['handle']}", None) for a in db.authors() if a["n"]]
    urls += [(f"{base}/a/{a['slug']}", a["updated_at"]) for a in arts]
    body = "".join(f"<url><loc>{xml_escape(u)}</loc>" + (f"<lastmod>{m}</lastmod>" if m else "") + "</url>"
                   for u, m in urls)
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    + body + "</urlset>", media_type="application/xml")


@app.get("/feed.xml")
def feed(request: Request):
    base = site_url(request)
    items = ""
    for a in db.list_articles(sort="new", limit=50, with_body=True):
        desc = a["summary"] or excerpt(a, 300)
        items += (f"<item><title>{xml_escape(a['title'])}</title><link>{base}/a/{a['slug']}</link>"
                  f"<guid isPermaLink=\"true\">{base}/a/{a['slug']}</guid>"
                  f"<pubDate>{format_datetime(parse_ts(a['published_at']))}</pubDate>"
                  f"<author>noreply@chuhai.news ({xml_escape(a['author_name'])})</author>"
                  f"<category>{xml_escape(a['cat_zh'])}</category><description>{xml_escape(desc)}</description></item>")
    return Response('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>chuhainews</title>'
                    f"<link>{base}/</link><description>{xml_escape(T['zh']['siteDesc'])}</description>"
                    f"<language>zh-cn</language>{items}</channel></rss>", media_type="application/rss+xml")



# ---------- 只读 JSON（给外部信源聚合拉取，范围与 RSS 相同） ----------

def article_brief(a, base):
    return {"slug": a["slug"], "url": f"{base}/a/{a['slug']}", "source_url": a["source_url"], "lang": a["lang"],
            "category": a["cat"], "category_zh": a["cat_zh"], "title": a["title"], "title_translated": a["title_t"],
            "summary": a["summary"] or excerpt(a, 300), "author": {"handle": a["author"], "name": a["author_name"]},
            "published_at": a["published_at"], "updated_at": a["updated_at"],
            "views": a["views"], "likes": a["likes"], "saves": a["saves"]}


@app.get("/api/articles")
def api_articles(request: Request, limit: int = 50, category: str | None = None):
    """最新发布的文章（按发布时间倒序，最多 100 篇）；正文与逐段译文用 /api/articles/{slug}。"""
    base = site_url(request)
    arts = db.list_articles(cat=category, sort="new", limit=max(1, min(limit, 100)), with_body=True)
    return {"site": base, "articles": [article_brief(a, base) for a in arts]}


@app.get("/api/articles/{slug}")
def api_article(request: Request, slug: str):
    a = db.get_article(slug)
    if not a:
        return JSONResponse({"error": "not found"}, 404)
    kinds = {"h2": "heading", "img": "image"}
    paras = [{"type": kinds.get(p[2] if len(p) > 2 else "p", "paragraph"), "text": p[0], "translation": p[1],
              **({"src": p[3]} if len(p) > 3 else {})} for p in a["paras"]]
    return {**article_brief({**a, "author_name": a["author_name"]}, site_url(request)), "paragraphs": paras}

def excerpt(a, n=160):
    """没填摘要时的描述：取开头几段正文（跳过小标题和配图）凑满 n 字。"""
    text, sep = "", "" if a["lang"] == "zh" else " "
    for p in a["paras"]:
        if len(p) == 2:
            text += (sep if text else "") + p[0]
            if len(text) >= n:
                return text[:n - 1].rstrip() + "…"
    return text


def first_sentence(s):
    return re.split(r"(?<=[。！？!?])|(?<=\.)\s", (s or "").strip(), maxsplit=1)[0].strip()


def md_block(p, i):
    kind = p[2] if len(p) > 2 else "p"
    return f"### {p[i]}" if kind == "h2" else f"![{p[i]}]({p[3]})" if kind == "img" else p[i]


def to_markdown(a, base):
    lines = [f"# {a['title']}", ""]
    if a["title_t"]:
        lines += [f"> {a['title_t']}", ""]
    lines += [f"- 作者 / Author: {a['author_name']} (@{a['author']})", f"- 类目 / Topic: {a['cat_zh']} / {a['cat_en']}",
              f"- 发布 / Published: {a['published_at'][:10]}", f"- 原文语言 / Language: {a['lang']}",
              f"- 原帖 / Source: {a['source_url'] or '—'}", f"- 本页 / Canonical: {base}/a/{a['slug']}", ""]
    if a["summary"]:
        lines += ["## 编者导读 / Editor's note", "", a["summary"], ""]
    lines += ["## 原文 / Original", ""]
    lines += [md_block(p, 0) + "\n" for p in a["paras"]]
    if a["has_trans"]:
        lines += ["## 译文 / Translation", ""] + [md_block(p, 1) + "\n" for p in a["paras"] if p[1] or len(p) > 3]
    lines.append(f"---\n原文版权归作者所有。Copyright belongs to @{a['author']}.")
    return "\n".join(lines)


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms(request: Request):
    base = site_url(request)
    out = [f"# chuhainews", "", f"> {T['zh']['siteDesc']}", "> " + T["en"]["siteDesc"], "",
           "每篇文章都有 Markdown 版本：在文章网址后加 `.md`。全文合集见 llms-full.txt。",
           f"运营方、选文标准、翻译方式（AI 翻译，未逐篇校对）与版权联系：{base}/about",
           f"Agent 可通过 MCP（Streamable HTTP）查询：{base}/mcp", ""]
    out += ["## 实用工具 / Tools", "",
            f"- [{T['zh']['excelRatio']} / {T['en']['excelRatio']}]({base + EXCEL_RATIO_PATH}): {T['zh']['excelDesc']}", ""]
    tps = topic_cards("zh")
    if tps:
        out += ["## 专题 / Topics", ""]
        out += [f"- [{tp['title']}]({base}/t/{tp['key']}): {tp['n']} 篇" + (f" — {tp['lead']}" if tp["lead"] else "")
                for tp in tps]
        out.append("")
    for c in db.categories():
        arts = db.list_articles(cat=c["key"], sort="hot", limit=30)
        if not arts:
            continue
        out += [f"## {c['zh']} / {c['en']}", ""]
        for a in arts:
            note = first_sentence(a["summary"]) or a["title_t"]
            out.append(f"- [{a['title']}]({base}/a/{a['slug']}.md): {a['author_name']}" + (f" — {note}" if note else ""))
        out.append("")
    out += ["## Optional", "", f"- [全文合集]({base}/llms-full.txt)", f"- [RSS]({base}/feed.xml)", f"- [Sitemap]({base}/sitemap.xml)"]
    return "\n".join(out)


@app.get("/llms-full.txt", response_class=PlainTextResponse)
def llms_full(request: Request):
    base = site_url(request)
    # shortcut: 一次拼出最近 200 篇全文；文章更多时改为分页或按类目拆文件
    arts = [db.get_article(a["slug"]) for a in db.list_articles(sort="new", limit=200)]
    return "\n\n".join(to_markdown(a, base) for a in arts if a)


@app.get("/{name}.txt", response_class=PlainTextResponse)
def indexnow_key(request: Request, name: str):
    """IndexNow 密钥文件：搜索引擎用它确认推送来自站长。"""
    k = indexnow.key()
    return PlainTextResponse(k) if k and name == k else not_found(request)


# ---------- WebHook ----------

MAX_BODY = 5 * 1024 * 1024
SIG_TTL = 300  # 签名时间戳允许的偏差（秒）；窗口内同一签名只收一次


@app.get("/api/webhook")
def webhook_schema():
    """参数说明（JSON Schema）：LLM / 自动化平台先 GET 这里，再按它组装 POST。"""
    return WEBHOOK_SCHEMA


@app.post("/api/webhook")
async def webhook(request: Request):
    secret = os.getenv("WEBHOOK_SECRET", "")
    if not secret:
        db.log_webhook(False, 503, "WEBHOOK_SECRET 未配置，拒收")
        return JSONResponse({"error": "webhook disabled: WEBHOOK_SECRET not set"}, 503)
    key = "push:" + client_ip(request)
    if limits.blocked(key):
        db.log_webhook(False, 429, f"{client_ip(request)} 认证失败过多，暂时拒收")
        return JSONResponse({"error": "too many failed attempts, retry later"}, 429)
    body = await request.body()
    if len(body) > MAX_BODY:
        db.log_webhook(False, 413, f"请求体 {len(body)} 字节超限")
        return JSONResponse({"error": "payload too large"}, 413)
    ts = request.headers.get("x-chuhai-timestamp", "")
    sig = request.headers.get("x-chuhai-signature", "")
    bearer = request.headers.get("authorization", "")
    expected = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    signed = bool(sig) and ts.isdigit() and abs(time.time() - int(ts)) <= SIG_TTL and hmac.compare_digest(sig, expected)
    if not (signed or (bearer and hmac.compare_digest(bearer, "Bearer " + secret))):
        limits.fail(key)
        db.log_webhook(False, 401, "签名、时间戳或令牌不正确")
        return JSONResponse({"error": "invalid signature or token (signed requests need a fresh X-Chuhai-Timestamp)",
                             "docs": "GET /api/webhook"}, 401)
    if signed and not db.claim_nonce(sig, SIG_TTL * 2):
        db.log_webhook(False, 409, "重放的签名请求，已拒收")
        return JSONResponse({"error": "replayed request"}, 409)
    try:
        data = json.loads(body)
    except ValueError:
        db.log_webhook(False, 400, "不是合法 JSON")
        return JSONResponse({"error": "invalid JSON", "docs": "GET /api/webhook"}, 400)
    items = data["articles"] if isinstance(data, dict) and "articles" in data else data if isinstance(data, list) else [data]
    try:
        results = publish(items, site_url(request))
    except BatchError as e:
        if e.index is None:
            return JSONResponse({"error": str(e), "docs": "GET /api/webhook"}, 400)
        return JSONResponse({"error": str(e), "index": e.index, "docs": "GET /api/webhook"}, 422)
    return {"ok": True, "results": results}


from . import admin  # noqa: E402  admin 依赖本模块的 templates/site_url，放到最后避免循环导入
app.include_router(admin.router)
