"""链接导入：fxtwitter 取原文与指标 → DeepSeek 只出译文、导读和类目 → 走 ingest.publish 入库。

WebHook 收到 {"links": [...]} 时在后台调用 run()，直接发布；本地脚本 scripts/import_x.py 共用这里的取文与翻译函数。
DeepSeek 打不开链接，取文只靠 fxtwitter（免费、非官方）。
"""
import json
import os
import re
import threading
import urllib.request
from datetime import datetime, timezone

from . import db
from .ingest import BatchError, normalize, publish

MIN_CHARS = 1500  # 原文少于这个字符数不算长文，跳过

STATUS_RE = re.compile(r"(?:x|twitter)\.com/(\w{1,15})/status/(\d+)")
UA = {"User-Agent": "chuhainews-import/1.0"}
SYSTEM_TRANSLATE = (
    "你是 chuhainews 的译者。把 items 数组里的每一项译成目标语言（to: zh = 简体中文，en = English）。"
    "规则：逐项翻译，输出项数和顺序与输入完全一致；只输出译文，不输出原文；保留数字、金额、专有名词、产品名、@用户名和链接；"
    "以「• 」或「1. 」这类序号开头的项保留该前缀；语气口语化，不加译者注。"
    '只输出 JSON：{"items": ["译文", ...]}')
SYSTEM_SUMMARY = (
    "你是 chuhainews 编辑部。读下面的长文，用简体中文写编者导读初稿：2～3 句，80～200 字，"
    "写作者的核心判断，以及它对做出海或 AI 实操的人意味着什么。不要写「本文介绍了」，不用冒号引出结论。"
    "再从 categories（key → 名称）里选一个最贴切的类目 key。"
    '只输出 JSON：{"summary": "...", "category": "key"}')


def http_json(url, data=None, headers=None, timeout=60):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={**UA, **({"Content-Type": "application/json"} if body else {}),
                                                         **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def article_paras(art):
    """X Article（Draft.js 块）→ [(kind, text, src)]。小标题→h2，列表加「• 」/序号，atomic 里的图片→img。"""
    content = art["content"]
    em = content.get("entityMap") or {}
    em = {str(e["key"]): e["value"] for e in em} if isinstance(em, list) else em
    imgs = {m.get("media_id"): (m.get("media_info") or {}).get("original_img_url") for m in art.get("media_entities") or []}
    out, n = [], 0
    for b in content["blocks"]:
        kind, text = b.get("type"), (b.get("text") or "").strip()
        n = n + 1 if kind == "ordered-list-item" else 0
        if kind == "atomic":
            for r in b.get("entityRanges") or []:
                v = em.get(str(r.get("key"))) or {}
                for it in (v.get("data") or {}).get("mediaItems") or [] if v.get("type") == "MEDIA" else []:
                    if (src := imgs.get(it.get("mediaId"))) and src.startswith("https://"):
                        out.append(("img", "", src))
        elif text:
            if kind.startswith("header"):
                out.append(("h2", text, ""))
            else:
                out.append(("p", f"{n}. {text}" if n else "• " + text if kind == "unordered-list-item" else text, ""))
    return out


def note_paras(t):
    text = re.sub(r"\s*https://t\.co/\w+\s*$", "", t.get("text") or "")
    out = [("p", p.strip(), "") for p in re.split(r"\n\s*\n", text) if p.strip()]
    photos = [m["url"] for m in (t.get("media") or {}).get("all") or [] if m.get("type") == "photo"]
    return out + [("img", "", u) for u in photos if u.startswith("https://")]


def note_title(text, limit=110):
    first = (text or "").strip().split("\n")[0]
    s = re.split(r"(?<=[.!?。！？])\s", first, maxsplit=1)[0]
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"
def deepseek(system, payload):
    r = http_json("https://api.deepseek.com/chat/completions", {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"), "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]},
        {"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]}, timeout=600)
    return json.loads(r["choices"][0]["message"]["content"])


def chunks(items, max_items=60, max_chars=8000):
    cur, size = [], 0
    for i, s in enumerate(items):
        if cur and (len(cur) >= max_items or size + len(s) > max_chars):
            yield cur
            cur, size = [], 0
        cur.append(i)
        size += len(s)
    if cur:
        yield cur


def translate(texts, to, call=deepseek):
    """逐项翻译；空串不送。每批返回条数必须与输入一致，不一致重试两次后报错。"""
    out = [""] * len(texts)
    todo = [i for i, s in enumerate(texts) if s.strip()]
    for part in chunks([texts[i] for i in todo]):
        src = [texts[todo[j]] for j in part]
        for attempt in range(3):
            got = call(SYSTEM_TRANSLATE, {"to": to, "items": src}).get("items")
            if isinstance(got, list) and len(got) == len(src) and all(isinstance(x, str) for x in got):
                break
            if attempt == 2:
                raise ValueError(f"译文条数对不上（送 {len(src)} 条，回 {len(got) if isinstance(got, list) else '非数组'}）")
        for j, s in zip(part, got):
            out[todo[j]] = s.strip()
    return out

# ---------- 组装 ----------

def fetch(handle, xid):
    t = http_json(f"https://api.fxtwitter.com/{handle}/status/{xid}").get("tweet")
    if not t:
        raise ValueError("fxtwitter 没有返回帖子")
    return t


def assemble(t, url):
    """fxtwitter 帖子 → (WebHook 载荷初稿, 段落, 报告信息)。external_id、类目、导读、状态由调用方填。"""
    art = t.get("article")
    paras = article_paras(art) if art else note_paras(t)
    info = {"kind": "Article" if art else "长帖" if t.get("is_note_tweet") else "短帖",
            "chars": sum(len(p[1]) for p in paras), "paras": len(paras), "images": sum(p[0] == "img" for p in paras)}
    payload = {
        "source_url": url, "lang": "zh" if (t.get("lang") or "").startswith("zh") else "en",
        "title": (art or {}).get("title") or note_title(t.get("text")), "title_translated": "",
        "author": {"handle": t["author"]["screen_name"], "name": t["author"].get("name", "")},
        "paragraphs": [{"type": {"h2": "heading", "img": "image"}.get(k, "paragraph"), "text": s, "translation": "",
                        **({"src": src} if k == "img" else {})} for k, s, src in paras],
        "published_at": datetime.fromtimestamp(t["created_timestamp"], timezone.utc).isoformat(),
        "cover": ((art or {}).get("cover_media") or {}).get("media_info", {}).get("original_img_url")
        or next((p[2] for p in paras if p[0] == "img"), ""),
        "views": t.get("views"), "likes": t.get("likes"), "saves": t.get("bookmarks")}
    return {k: v for k, v in payload.items() if v is not None}, paras, info


def fill_translation(payload, paras):
    """补全文译文；缺导读或类目时，再调一次 DeepSeek 出导读初稿和类目（只填空着的那项）。"""
    got = translate([payload["title"]] + [p[1] for p in paras], "en" if payload["lang"] == "zh" else "zh")
    payload["title_translated"] = got[0]
    for para, tr in zip(payload["paragraphs"], got[1:]):
        para["translation"] = tr
    if not payload.get("summary") or not payload.get("category"):
        body = "\n\n".join(p[1] for p in paras if p[0] != "img")[:12000]
        r = deepseek(SYSTEM_SUMMARY, {"title": payload["title"], "text": body,
                                      "categories": {c["key"]: c["zh"] for c in db.categories()}})
        payload["summary"] = payload.get("summary") or str(r.get("summary") or "").strip()[:600]
        payload["category"] = payload.get("category") or str(r.get("category") or "").strip()


# ---------- WebHook 链接导入（站内后台跑） ----------

def parse_links(items):
    """links 每项是 "链接 | 类目 | 备注" 字符串或 {url, category}。返回 [(handle, id, 类目)]，按 ID 去重。
    认不出的链接整批拒收；类目不认识就留空，交给 DeepSeek 判。"""
    if not isinstance(items, list) or not 0 < len(items) <= 100:
        raise ValueError("links 必须是 1–100 条")
    keys, out, seen = set(db.category_keys()), [], set()
    for i, it in enumerate(items):
        parts = [it.get("url"), it.get("category")] if isinstance(it, dict) else re.split(r"[|\t]", it) \
            if isinstance(it, str) else []
        m = STATUS_RE.search(parts[0]) if parts and isinstance(parts[0], str) else None
        if not m:
            raise ValueError(f"links[{i}] 不是 x.com/<用户>/status/<ID> 链接")
        cat = next((p.strip() for p in parts[1:] if isinstance(p, str) and p.strip() in keys), "")
        if m.group(2) not in seen:
            seen.add(m.group(2))
            out.append((m.group(1), m.group(2), cat))
    return out


def existing(xid):
    """站上已有的同一原帖（按 source_url 里的帖子 ID 找，含草稿）。"""
    with db.connect() as con:
        r = con.execute("SELECT slug FROM articles WHERE source_url LIKE ? OR source_url LIKE ?",
                        (f"%/status/{xid}", f"%/status/{xid}?%")).fetchone()
    return db.get_article(slug=r[0], include_unpublished=True) if r else None


def import_one(handle, xid, cat, base):
    url = f"https://x.com/{handle}/status/{xid}"
    t = fetch(handle, xid)
    old = existing(xid)
    if old and old["has_trans"]:  # 已有译文：只更新浏览/点赞/收藏，不再花 DeepSeek token
        d = {k: v for k, v in old.items() if k not in ("views", "likes", "saves")}
        d.update(slug="", author={"handle": old["author"], "name": ""},
                 views=t.get("views"), likes=t.get("likes"), saves=t.get("bookmarks"))
        db.upsert_article(d)
        db.log_webhook(True, 200, f"Import：{url} 已有译文，只更新数据", [old["external_id"]])
        return
    payload, paras, info = assemble(t, url)
    if info["chars"] < MIN_CHARS:
        db.log_webhook(False, 422, f"Import：{url} 跳过：原文 {info['chars']} 字符，低于 {MIN_CHARS}")
        return
    payload.update(external_id=old["external_id"] if old else f"x:{xid}", category=cat or (old or {}).get("cat", ""),
                   summary=(old or {}).get("summary", ""), status=(old or {}).get("status", "published"))
    normalize({**payload, "category": payload["category"] or db.category_keys()[0]})  # 先校验、后翻译，坏帖不花 token
    fill_translation(payload, paras)
    publish([payload], base, via="Import")


_lock = threading.Lock()


def run(links, base):
    """后台逐篇导入并直接发布。一篇失败只记进 WebHook 日志，不影响其他篇；同一时间只跑一批。
    shortcut: 队列在进程内存里，重启会丢掉没跑完的链接；要可靠时改成数据库队列，重推同一批即可补上。"""
    with _lock:
        for handle, xid, cat in links:
            try:
                import_one(handle, xid, cat, base)
            except BatchError:
                pass  # publish 已写日志
            except Exception as e:
                db.log_webhook(False, 500, f"Import：https://x.com/{handle}/status/{xid} 失败：{e}")
