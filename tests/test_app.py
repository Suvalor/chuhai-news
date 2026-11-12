import hashlib
import hmac
import itertools
import json
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET

os.environ["CHUHAI_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["WEBHOOK_SECRET"] = "s3cret"
os.environ["ADMIN_PASSWORD"] = "pw-test"
os.environ["SITE_URL"] = "https://chuhai.test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ART = {"external_id": "x:1", "source_url": "https://x.com/maralin/status/1", "lang": "en", "category": "cog",
       "title": "Notes on Taste", "title_translated": "关于品味的几条笔记",
       "author": {"handle": "maralin", "name": "Mara Lin", "bio_zh": "独立顾问"},
       "paragraphs": [{"text": "Taste is not a gift.", "translation": "品味不是天赋。"},
                      {"text": "Copy by hand.", "translation": "亲手临摹。"}]}
ART2 = {**ART, "external_id": "x:2", "title": "Pricing Is Positioning", "title_translated": "定价是定位",
        "category": "money", "paragraphs": ["Price is the first sentence."]}


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


_seq = itertools.count()


def sign(body, secret="s3cret", ts=None):
    # 同一秒内推同样内容会被当成重放（409），测试里把时间戳错开
    ts = str(int(time.time()) - next(_seq) % 200 if ts is None else ts)
    sig = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Chuhai-Timestamp": ts, "X-Chuhai-Signature": sig, "Content-Type": "application/json"}


def signed(c, payload, secret="s3cret", ts=None):
    body = json.dumps(payload).encode()
    return c.post("/api/webhook", content=body, headers=sign(body, secret, ts))


def test_webhook_rejects_bad_signature(c):
    assert signed(c, ART, secret="wrong").status_code == 401
    assert c.post("/api/webhook", json=ART, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_webhook_rejects_invalid_batch_atomically(c):
    bad = {**ART2, "external_id": "x:bad", "author": {"handle": "bad handle!"}}
    r = signed(c, {"articles": [{**ART, "external_id": "x:never"}, bad]})
    assert r.status_code == 422 and r.json()["index"] == 1
    assert c.get("/search", params={"q": "Notes"}).text.count("/a/notes-on-taste") == 0  # 第一篇也没写入


def test_webhook_upsert_is_idempotent(c):
    r = signed(c, ART)
    assert r.status_code == 200 and r.json()["results"][0] == {
        "external_id": "x:1", "slug": "notes-on-taste", "created": True, "url": "https://chuhai.test/a/notes-on-taste"}
    r = signed(c, {**ART, "title_translated": "关于品味"})
    assert r.json()["results"][0]["created"] is False
    assert c.post("/api/webhook", json=[ART2], headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert "关于品味" in c.get("/a/notes-on-taste").text


def test_pages(c):
    home = c.get("/")
    assert home.status_code == 200 and "Notes on Taste" in home.text and '"@type": "WebSite"' in home.text
    a = c.get("/a/notes-on-taste").text
    for s in ['<link rel="canonical" href="https://chuhai.test/a/notes-on-taste">', "Taste is not a gift.",
              "品味不是天赋。", '"@type": "Article"', 'type="text/markdown"', "https://x.com/maralin/status/1"]:
        assert s in a, s
    assert c.get("/c/cog").status_code == 200 and "Notes on Taste" in c.get("/c/cog").text
    assert "Mara Lin" in c.get("/u/maralin").text
    assert "notes-on-taste" in c.get("/search", params={"q": "品味"}).text
    assert c.get("/a/nope").status_code == 404 and c.get("/c/nope").status_code == 404
    assert re.search(r'href="/static/site\.css\?v=\d+"', a) and re.search(r'src="/static/site\.js\?v=\d+"', a)
    assert re.search(r'href="/static/admin\.css\?v=\d+"', c.get("/admin/login").text)
    md = c.get("/a/notes-on-taste.md")
    assert md.headers["content-type"].startswith("text/markdown") and md.text.startswith("# Notes on Taste")


def test_seo_geo_endpoints(c):
    robots = c.get("/robots.txt").text
    assert "User-agent: *\nAllow: /" in robots and "GPTBot" in robots and "Disallow" not in robots
    assert "https://chuhai.test/sitemap.xml" in robots
    sm = ET.fromstring(c.get("/sitemap.xml").content)
    assert "https://chuhai.test/a/notes-on-taste" in [e.text for e in sm.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    ET.fromstring(c.get("/feed.xml").content)
    assert "https://chuhai.test/a/notes-on-taste.md" in c.get("/llms.txt").text
    assert "品味不是天赋。" in c.get("/llms-full.txt").text


def test_like_counts_never_negative(c):
    assert c.post("/api/a/notes-on-taste/like").json() == {"likes": 1}
    assert c.post("/api/a/notes-on-taste/unlike").json() == {"likes": 0}
    assert c.post("/api/a/notes-on-taste/unlike").json() == {"likes": 0}
    assert c.post("/api/a/notes-on-taste/hack").status_code == 404


def test_fragment_next_and_rows(c):
    r = c.get("/fragment/next", params={"slug": "notes-on-taste", "ctx": "author", "exclude": "notes-on-taste"})
    assert r.status_code == 200 and 'data-art="pricing-is-positioning"' in r.text
    r = c.get("/fragment/next", params={"slug": "notes-on-taste", "exclude": "notes-on-taste,pricing-is-positioning"})
    assert r.status_code == 204
    assert 'data-slug="pricing-is-positioning"' in c.get("/fragment/rows", params={"slugs": "pricing-is-positioning"}).text


def test_admin_requires_login_and_edits(c):
    assert c.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    assert "口令不正确" in c.post("/admin/login", data={"password": "x"}).text
    c.post("/admin/login", data={"password": "pw-test"})
    page = c.get("/admin")
    assert page.status_code == 200 and "Notes on Taste" in page.text
    aid = re.search(r'href="/admin/a/(\d+)"><b>Notes on Taste</b>', page.text).group(1)
    form = {"id": aid, "title": "Notes on Taste, Revised", "author": "maralin", "cat": "cog", "lang": "en",
            "status": "published", "orig": "Para one.\n\nPara two.", "trans": "第一段。\n\n第二段。", "slug": "notes-on-taste"}
    assert c.post("/admin/a/save", data=form).status_code == 200
    assert "Notes on Taste, Revised" in c.get("/a/notes-on-taste").text
    c.post(f"/admin/a/{aid}/status", data={"status": "archived"})
    assert c.get("/a/notes-on-taste").status_code == 404
    c.post(f"/admin/a/{aid}/status", data={"status": "published"})
    c.post("/admin/logout")
    assert c.get("/admin", follow_redirects=False).status_code == 303


def test_mcp_over_http(c):
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "chuhainews"
    tools = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
    assert {"search_articles", "get_article", "latest_articles", "list_categories", "list_authors"} <= {
        t["name"] for t in tools["result"]["tools"]}
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                        "params": {"name": "get_article", "arguments": {"slug": "pricing-is-positioning"}}}).json()
    text = r["result"]["content"][0]["text"]
    assert "Price is the first sentence." in text and "https://chuhai.test/a/pricing-is-positioning" in text


RICH = {"external_id": "x:rich", "lang": "zh", "category": "ai", "author": {"handle": "lieflat_3"},
        "title": "去AI味实验", "title_translated": "After five million characters and half a million judgments I built a skill to remove AI tone",
        "paragraphs": [{"text": "短开头。", "translation": "Short opening."},
                       {"type": "heading", "text": "第一部分", "translation": "Part one"},
                       {"type": "image", "src": "https://pbs.twimg.com/media/x.jpg", "text": "指标表", "translation": "Indicator table"},
                       {"text": "第二段正文" * 40, "translation": "Body."}]}


def test_headings_images_slug_and_excerpt(c):
    assert signed(c, {**RICH, "external_id": "x:bad-img", "paragraphs": [{"type": "image", "src": "http://a/b.jpg"}]}).status_code == 422
    r = signed(c, RICH).json()["results"][0]
    slug = r["slug"]
    assert slug == "after-five-million-characters-and-half-a-million-judgments-i-built-a"  # 按词截断
    page = c.get(f"/a/{slug}").text
    assert '<h2 class="sub has-t"><span class="o" lang="zh">第一部分</span>' in page
    assert '<img src="https://pbs.twimg.com/media/x.jpg" alt="指标表"' in page and "<figcaption" in page
    desc = re.search(r'<meta name="description" content="([^"]*)"', page).group(1)
    assert desc.startswith("短开头。第二段正文") and desc.endswith("…") and len(desc) == 160  # 跳过小标题和图
    md = c.get(f"/a/{slug}.md").text
    assert "### 第一部分" in md and "![指标表](https://pbs.twimg.com/media/x.jpg)" in md and "![Indicator table](" in md
    mcp = c.post("/mcp", headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
                 json={"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "get_article", "arguments": {"slug": slug}}})
    paras = json.loads(mcp.json()["result"]["content"][0]["text"])["paragraphs"]
    assert [p["type"] for p in paras] == ["paragraph", "heading", "image", "paragraph"] and paras[2]["src"].endswith("x.jpg")
    # 后台打开再原样保存，小标题和图片不能丢
    c.post("/admin/login", data={"password": "pw-test"})
    aid = re.search(r'href="/admin/a/(\d+)"><b>去AI味实验</b>', c.get("/admin").text).group(1)
    form = c.get(f"/admin/a/{aid}").text
    orig = re.search(r'<textarea name="orig"[^>]*>(.*?)</textarea>', form, re.S).group(1)
    trans = re.search(r'<textarea name="trans"[^>]*>(.*?)</textarea>', form, re.S).group(1)
    assert "## 第一部分" in orig and "![Indicator table](https://pbs.twimg.com/media/x.jpg)" in trans
    import html
    c.post("/admin/a/save", data={"id": aid, "title": "去AI味实验", "author": "lieflat_3", "cat": "ai", "lang": "zh",
                                  "status": "published", "slug": slug, "orig": html.unescape(orig), "trans": html.unescape(trans)})
    c.post("/admin/logout")
    assert c.get(f"/a/{slug}").text.count('class="sub has-t"') == 1 and 'alt="指标表"' in c.get(f"/a/{slug}").text


def test_webhook_schema_matches_ingest(c):
    from app.ingest import normalize
    schema = c.get("/api/webhook").json()
    art = schema["$defs"]["article"]["properties"]
    src = open("app/ingest.py").read()
    read = set(re.findall(r'raw\.get\("(\w+)"', src)) | set(re.findall(r'raw\["(\w+)"\]', src))
    assert read == set(art), read ^ set(art)  # 校验代码读的字段与文档一一对应，防止文档过期
    for ex in schema["examples"]:
        normalize(ex)
    assert signed(c, {"title": "x"}).json()["docs"] == "GET /api/webhook"


def mcp_call(c, name, args, token=None):
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    return c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": name, "arguments": args}}).json()["result"]


def test_source_counts_keep_local_increments(c):
    art = {**ART, "external_id": "x:counts", "title": "Counting Things", "views": 1000, "likes": 50, "saves": 7}
    assert signed(c, {**art, "likes": -1}).status_code == 422
    slug = signed(c, art).json()["results"][0]["slug"]
    c.post(f"/api/a/{slug}/like"); c.post(f"/api/a/{slug}/save")  # 本站读者 +1 赞 +1 收藏
    signed(c, {**art, "likes": 80})  # X 上涨到 80 赞；没带 saves/views 就不改
    a = json.loads(mcp_call(c, "get_article", {"slug": slug})["content"][0]["text"])
    assert (a["likes"], a["saves"], a["views"]) == (81, 8, 1000)


def test_mcp_publish_requires_token(c):
    art = {**ART, "external_id": "x:mcp", "title": "Pushed Over MCP", "likes": 3}
    r = mcp_call(c, "publish_articles", {"articles": [art]})
    assert r["isError"] and "Authorization" in r["content"][0]["text"]
    r = mcp_call(c, "publish_articles", {"articles": [art]}, token="wrong")
    assert r["isError"]
    r = mcp_call(c, "publish_articles", {"articles": [art, {**art, "external_id": "x:mcp2", "category": "Bad Cat"}]}, token="s3cret")
    assert r["isError"] and "第 1 篇" in r["content"][0]["text"]
    assert "Pushed Over MCP" not in c.get("/search", params={"q": "Pushed"}).text  # 整批未写入
    r = mcp_call(c, "publish_articles", {"articles": [art]}, token="s3cret")
    res = json.loads(r["content"][0]["text"])["results"][0]
    assert res["created"] and res["url"] == "https://chuhai.test/a/pushed-over-mcp"
    assert "Pushed Over MCP" in c.get("/a/pushed-over-mcp").text
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    tools = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
    schema = next(t for t in tools if t["name"] == "publish_articles")["inputSchema"]
    assert "likes" in schema["properties"]["articles"]["items"]["properties"]  # 模型能看到完整字段说明


def test_admin_filters_by_category_and_author(c):
    for art in (ART, ART2, RICH):
        assert signed(c, art).status_code == 200
    c.post("/admin/login", data={"password": "pw-test"})
    titles = lambda qs: set(re.findall(r'href="/admin/a/\d+"><b>(.*?)</b>', c.get("/admin" + qs).text))
    assert "Notes on Taste" in titles("?cat=cog") and "Pricing Is Positioning" not in titles("?cat=cog")
    assert titles("?author=lieflat_3") == {"去AI味实验"}
    assert titles("?cat=money&author=lieflat_3") == set()
    page = c.get("/admin?cat=cog").text
    assert '<option value="cog" selected>' in page and 'href="/admin">清除</a>' in page
    c.post("/admin/logout")


def test_author_name_not_overwritten_by_empty_push(c):
    art = {**ART, "external_id": "x:noname", "title": "Second Note", "author": {"handle": "maralin"}}
    assert signed(c, ART).status_code == 200 and signed(c, art).status_code == 200
    page = c.get("/u/maralin").text
    assert "Mara Lin" in page and "独立顾问" in page  # 没带名字和简介，保持原样
    signed(c, {**art, "author": {"handle": "newbie"}, "external_id": "x:newbie"})
    assert ">newbie<" in c.get("/u/newbie").text  # 新作者没给名字时用 handle


def test_cover_image_for_share_cards(c):
    assert signed(c, RICH).status_code == 200
    assert signed(c, {**ART, "external_id": "x:badcover", "cover": "http://a/b.jpg"}).status_code == 422
    slug = signed(c, {**ART, "external_id": "x:cover", "title": "With Cover", "cover": "https://img.test/c.jpg"}).json()["results"][0]["slug"]
    page = c.get(f"/a/{slug}").text
    assert '<meta property="og:image" content="https://img.test/c.jpg">' in page and "summary_large_image" in page
    assert re.search(r'"image":\s*"https://img.test/c.jpg"', page)
    rich = c.get("/a/after-five-million-characters-and-half-a-million-judgments-i-built-a").text
    assert '<meta property="og:image" content="https://pbs.twimg.com/media/x.jpg">' in rich  # 没封面取正文第一张图
    plain = c.get("/a/notes-on-taste").text
    assert "og:image" not in plain and '<meta name="twitter:card" content="summary">' in plain


def test_signature_replay_and_stale_timestamp_rejected(c):
    body = json.dumps({**ART, "external_id": "x:replay", "title": "Replay Me"}).encode()
    h = sign(body)
    assert c.post("/api/webhook", content=body, headers=h).status_code == 200
    assert c.post("/api/webhook", content=body, headers=h).status_code == 409  # 原样重放
    assert signed(c, ART, ts=int(time.time()) - 3600).status_code == 401  # 过期时间戳
    old = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()  # 旧的无时间戳签名
    assert c.post("/api/webhook", content=body, headers={"X-Chuhai-Signature": "sha256=" + old}).status_code == 401


def test_unknown_category_rejected_and_created_in_admin(c):
    r = signed(c, {**ART, "external_id": "x:tols", "category": "tols"})
    assert r.status_code == 422 and "未知类目 tols" in r.json()["error"] and "tool" in r.json()["error"]
    c.post("/admin/login", data={"password": "pw-test"})
    assert c.post("/admin/categories", data={"key": "design", "zh": "设计", "en": "Design"}).status_code == 200
    assert c.post("/admin/categories", data={"key": "design", "zh": "x", "en": "x"}).status_code == 400  # 重复
    assert c.post("/admin/categories", data={"key": "Bad Key", "zh": "x", "en": "x"}).status_code == 400
    c.post("/admin/logout")
    assert signed(c, {**ART, "external_id": "x:design", "category": "design", "title": "On Design"}).status_code == 200
    assert "On Design" in c.get("/c/design").text


def test_interactions_deduped_per_visitor(c):
    signed(c, {**ART, "external_id": "x:dedupe", "title": "Like Once"})
    assert [c.post("/api/a/like-once/like").json()["likes"] for _ in range(3)] == [1, 1, 1]
    assert c.post("/api/a/like-once/unlike").json()["likes"] == 0
    assert [c.post("/api/a/like-once/view").json()["views"] for _ in range(3)] == [1, 1, 1]  # 每天一次
    assert c.post("/api/a/like-once/save").json()["saves"] == 1 and c.post("/api/a/like-once/save").json()["saves"] == 1


def test_failed_auth_rate_limited(c, monkeypatch):
    from app import admin, limits
    monkeypatch.setattr(admin.time, "sleep", lambda s: None)
    limits._fails.clear()
    for _ in range(limits.MAX_FAILS):
        assert c.post("/admin/login", data={"password": "nope"}).status_code == 200
    assert c.post("/admin/login", data={"password": "pw-test"}, follow_redirects=False).status_code == 429  # 口令对也先挡住
    for _ in range(limits.MAX_FAILS):
        signed(c, ART, secret="wrong")
    assert signed(c, ART).status_code == 429
    r = mcp_call(c, "publish_articles", {"articles": [ART]}, token="s3cret")
    assert r["isError"] and "过多" in r["content"][0]["text"]  # MCP 写入与 WebHook 同一来源共用计数
    limits._fails.clear()


def test_mcp_write_token_overrides_webhook_secret(c, monkeypatch):
    monkeypatch.setenv("MCP_WRITE_TOKEN", "mcp-only")
    art = {**ART, "external_id": "x:mcptoken", "title": "Separate Token"}
    assert mcp_call(c, "publish_articles", {"articles": [art]}, token="s3cret")["isError"]
    assert not mcp_call(c, "publish_articles", {"articles": [art]}, token="mcp-only").get("isError")
    from app import limits
    limits._fails.clear()
