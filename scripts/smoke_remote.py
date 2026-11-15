"""Smoke-test a deployed chuhainews instance from outside.

Reads SITE_URL, WEBHOOK_SECRET and optional MCP_WRITE_TOKEN from ../.env. Never prints secrets.
Writes exactly one row to production: a DRAFT article (external_id/slug selftest-<date>), re-pushed idempotently.
At most 3 deliberate auth failures, well under the 10-per-15-min lockout.

Usage: python3 scripts/smoke_remote.py
"""
import datetime, hashlib, hmac, json, pathlib, re, sqlite3, sys, time
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))
SITE = env.get("SITE_URL", "").strip().rstrip("/")
SECRET = env.get("WEBHOOK_SECRET", "").strip()
MCP_TOKEN = env.get("MCP_WRITE_TOKEN", "").strip() or SECRET
if not SITE.startswith("https://") or not SECRET:
    sys.exit("need SITE_URL (https://…) and WEBHOOK_SECRET in .env")

TAG = "selftest-" + datetime.date.today().strftime("%Y%m%d")
s = requests.Session()
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  — {detail}" if detail else ""))


def signed(body: bytes, ts=None, secret=SECRET):
    ts = str(ts or int(time.time()))
    sig = hmac.new(secret.encode(), (ts + ".").encode() + body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Chuhai-Timestamp": ts, "X-Chuhai-Signature": f"sha256={sig}"}


def mcp(method, params=None, token=None, rid=1):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = s.post(f"{SITE}/mcp", headers=h, timeout=30,
               json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    text = r.text
    if text.startswith("event:") or "\ndata:" in text:  # tolerate SSE framing
        text = next(l[5:] for l in text.splitlines() if l.startswith("data:"))
    return r.status_code, json.loads(text) if text.strip() else {}


def tool(name, args, token=None):
    code, d = mcp("tools/call", {"name": name, "arguments": args}, token)
    res = d.get("result", {})
    payload = res.get("structuredContent") or next(
        (json.loads(c["text"]) for c in res.get("content", []) if c.get("type") == "text" and c["text"][:1] in "[{"), None)
    return code, res, payload


# ---- 1. public pages & SEO/GEO endpoints
r = s.get(SITE + "/", timeout=30)
check("home 200 over https", r.status_code == 200 and r.url.startswith("https://"), f"{r.status_code} {r.url}")
canon = re.search(r'<link rel="canonical" href="([^"]+)"', r.text)
check("canonical uses SITE_URL", bool(canon) and canon.group(1).startswith(SITE), canon.group(1) if canon else "missing")
http = requests.get(SITE.replace("https://", "http://", 1) + "/", timeout=30, allow_redirects=False)
check("http redirects to https", http.status_code in (301, 302, 307, 308), str(http.status_code))
for path, ctype in [("/robots.txt", "text/plain"), ("/sitemap.xml", "xml"), ("/feed.xml", "xml"),
                    ("/llms.txt", "text"), ("/llms-full.txt", "text"), ("/api/webhook", "json")]:
    r = s.get(SITE + path, timeout=30)
    check(f"GET {path}", r.status_code == 200 and ctype in r.headers.get("content-type", ""),
          f"{r.status_code} {r.headers.get('content-type')}")
sitemap = s.get(SITE + "/sitemap.xml", timeout=30).text
urls = re.findall(r"<loc>([^<]+)</loc>", sitemap)
check("sitemap links use SITE_URL", bool(urls) and all(u.startswith(SITE) for u in urls), f"{len(urls)} urls")
art = next((u for u in urls if "/a/" in u), None)
if art:
    check("article page 200", s.get(art, timeout=30).status_code == 200, art)
    check("article .md 200", s.get(art + ".md", timeout=30).status_code == 200)
demo = {row[0] for row in sqlite3.connect(ROOT / "data" / "demo.db").execute("select slug from articles")}
leaked = sorted(d for d in demo if any(u.endswith("/a/" + d) for u in urls))
check("no fictional demo articles online", not leaked, ", ".join(leaked[:5]))

# ---- 2. MCP (read)
code, d = mcp("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "smoke", "version": "1"}})
check("MCP initialize", code == 200 and "result" in d, str(code))
code, d = mcp("tools/list", rid=2)
names = sorted(t["name"] for t in d.get("result", {}).get("tools", []))
check("MCP tools/list", {"search_articles", "latest_articles", "get_article", "list_authors",
                         "list_categories", "publish_articles"} <= set(names), ", ".join(names))
_, _, cats = tool("list_categories", {})
_, _, authors = tool("list_authors", {})
_, _, latest = tool("latest_articles", {"limit": 3})
check("MCP latest_articles returns SITE_URL links",
      bool(latest) and all(str(a.get("url", "")).startswith(SITE) for a in (latest if isinstance(latest, list) else latest.get("result", []))),
      json.dumps(latest, ensure_ascii=False)[:160])

# ---- 3. WebHook auth + ingest (one DRAFT row)
cat_key = (cats if isinstance(cats, list) else cats.get("result", [{}]))[0].get("key", "ai") if cats else "ai"
author_list = authors if isinstance(authors, list) else (authors or {}).get("result", [])
handle = author_list[0].get("handle", "selftest") if author_list else "selftest"
article = {"external_id": TAG, "slug": TAG, "status": "draft", "category": cat_key,
           "author": {"handle": handle}, "title": f"[{TAG}] smoke test, do not publish",
           "paragraphs": ["Automated smoke test. Draft only."]}
body = json.dumps(article).encode()
check("webhook no auth -> 401", s.post(SITE + "/api/webhook", data=body, headers={"Content-Type": "application/json"}).status_code == 401)
check("webhook bad signature -> 401", s.post(SITE + "/api/webhook", data=body, headers=signed(body, secret="wrong")).status_code == 401)
check("webhook stale timestamp -> 401",
      s.post(SITE + "/api/webhook", data=body, headers=signed(body, ts=int(time.time()) - 3600)).status_code == 401)
h = signed(body)
r = s.post(SITE + "/api/webhook", data=body, headers=h)
check("webhook signed draft accepted", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
check("webhook replay -> 409", s.post(SITE + "/api/webhook", data=body, headers=h).status_code == 409)
bad = json.dumps([article, {"title": "missing fields"}]).encode()
r = s.post(SITE + "/api/webhook", data=bad, headers={"Content-Type": "application/json", "Authorization": f"Bearer {SECRET}"})
check("webhook invalid batch -> 422 with index", r.status_code == 422 and r.json().get("index") == 1, f"{r.status_code} {r.text[:160]}")
check("draft not public", s.get(f"{SITE}/a/{TAG}", timeout=30).status_code == 404)

# ---- 4. MCP write
code, res, _ = tool("publish_articles", {"articles": [article]})
check("MCP publish without token refused", code in (401, 403) or res.get("isError") is True, f"{code} {str(res)[:120]}")
code, res, payload = tool("publish_articles", {"articles": [article]}, token=MCP_TOKEN)
check("MCP publish with token (same draft, idempotent)", code == 200 and not res.get("isError"), f"{code} {str(payload)[:120]}")
check("draft still not public", s.get(f"{SITE}/a/{TAG}", timeout=30).status_code == 404)

print(f"\n{sum(results)}/{len(results)} passed · test draft left in DB: slug {TAG} (status draft)")
sys.exit(0 if all(results) else 1)
