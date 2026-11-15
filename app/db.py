"""SQLite 数据层：所有 SQL 都集中在这里，换库时只改本文件。"""
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("CHUHAI_DB", Path(__file__).resolve().parent.parent / "data" / "chuhai.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
  key TEXT PRIMARY KEY, zh TEXT NOT NULL, en TEXT NOT NULL,
  tag_zh TEXT DEFAULT '', tag_en TEXT DEFAULT '', sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS authors (
  handle TEXT PRIMARY KEY, name TEXT NOT NULL,
  bio_zh TEXT DEFAULT '', bio_en TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  external_id TEXT UNIQUE NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL, title_t TEXT DEFAULT '',
  lang TEXT NOT NULL DEFAULT 'en',
  author TEXT NOT NULL REFERENCES authors(handle),
  cat TEXT NOT NULL REFERENCES categories(key),
  source_url TEXT DEFAULT '', summary TEXT DEFAULT '', cover TEXT DEFAULT '',
  paras TEXT NOT NULL DEFAULT '[]',
  mins INTEGER DEFAULT 1,
  views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, saves INTEGER DEFAULT 0,
  src_views INTEGER DEFAULT 0, src_likes INTEGER DEFAULT 0, src_saves INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'published',
  published_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status, published_at);
CREATE TABLE IF NOT EXISTS interactions (
  who TEXT NOT NULL, slug TEXT NOT NULL, kind TEXT NOT NULL, ts TEXT NOT NULL, PRIMARY KEY (who, slug, kind));
CREATE TABLE IF NOT EXISTS topics (
  key TEXT PRIMARY KEY, title TEXT NOT NULL, intro TEXT DEFAULT '',
  items TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'draft', sort INTEGER DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS webhook_nonces (sig TEXT PRIMARY KEY, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS webhook_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, ok INTEGER NOT NULL,
  status INTEGER NOT NULL, message TEXT DEFAULT '', external_ids TEXT DEFAULT '');
"""

DEFAULT_CATS = [
    ("cog", "认知", "Thinking", "想清楚：思维模型、判断与决策", "Think clearly: models, judgment, decisions"),
    ("money", "挣钱", "Money", "赚到钱：商业模式、定价与增长", "Make money: models, pricing, growth"),
    ("ai", "AI", "AI", "用上 AI：模型、Agent 与工作流", "Use AI: models, agents, workflows"),
    ("tool", "工具", "Tools", "拿来就用：软件、清单与模板", "Use it now: software, lists, templates"),
    ("global", "出海", "Global", "走出去：本地化与海外市场", "Go global: localization, new markets"),
    ("create", "创作", "Creating", "写出来：长文、Thread 与个人品牌", "Create: long-form, threads, audience"),
]

# 热度：互动加权后按天数线性衰减；展示时再按全站最高分归一到 0–100
HOT = "((a.views*0.01 + a.likes*3 + a.saves*4) / (julianday('now') - julianday(a.published_at) + 2))"
SORTS = {"hot": HOT + " DESC", "new": "a.published_at DESC", "views": "a.views DESC"}
STATUSES = ("published", "draft", "archived")
COUNTS = ("views", "likes", "saves")  # 总数 = 来源平台推来的 src_* + 本站读者产生的增量


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init():
    with connect() as con:
        con.executescript(SCHEMA)
        cols = {r[1] for r in con.execute("PRAGMA table_info(articles)")}
        for col, typ in [*((f"src_{f}", "INTEGER DEFAULT 0") for f in COUNTS), ("cover", "TEXT DEFAULT ''")]:  # 旧库补列
            if col not in cols:
                con.execute(f"ALTER TABLE articles ADD COLUMN {col} {typ}")
        con.execute("PRAGMA journal_mode=WAL")
        con.executemany(
            "INSERT OR IGNORE INTO categories(key,zh,en,tag_zh,tag_en,sort) VALUES (?,?,?,?,?,?)",
            [(*c, i) for i, c in enumerate(DEFAULT_CATS)])


def _row(r):
    if r is None:
        return None
    d = dict(r)
    d["paras"] = json.loads(d["paras"])
    d["has_trans"] = bool(d["title_t"]) and any(p[1] for p in d["paras"])
    return d


# ---------- 读取 ----------

def categories():
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM articles a WHERE a.cat=c.key AND a.status='published') AS n "
            "FROM categories c ORDER BY sort, key")]


def authors():
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT u.*, (SELECT COUNT(*) FROM articles a WHERE a.author=u.handle AND a.status='published') AS n "
            "FROM authors u ORDER BY n DESC, name")]


def author(handle):
    with connect() as con:
        r = con.execute(
            "SELECT u.*, COUNT(a.id) AS n, COALESCE(SUM(a.views),0) AS views, COALESCE(SUM(a.likes),0) AS likes "
            "FROM authors u LEFT JOIN articles a ON a.author=u.handle AND a.status='published' "
            "WHERE u.handle=? GROUP BY u.handle", (handle,)).fetchone()
        return dict(r) if r else None


def _max_hot(con):
    return con.execute(f"SELECT MAX({HOT}) FROM articles a WHERE a.status='published'").fetchone()[0] or 1


def list_articles(*, cat=None, author=None, q=None, slugs=None, sort="hot", status="published",
                  limit=50, offset=0, with_body=False):
    where, args = [], []
    if status:
        where.append("a.status=?"); args.append(status)
    if cat:
        where.append("a.cat=?"); args.append(cat)
    if author:
        where.append("a.author=?"); args.append(author)
    if slugs is not None:
        where.append(f"a.slug IN ({','.join('?' * len(slugs)) or 'NULL'})"); args += list(slugs)
    if q:
        like = f"%{q}%"
        where.append("(a.title LIKE ? OR a.title_t LIKE ? OR a.paras LIKE ? OR u.name LIKE ? OR u.handle LIKE ? "
                     "OR c.zh LIKE ? OR c.en LIKE ?)")
        args += [like] * 7
    sql = (f"SELECT a.*, {HOT} AS hot, u.name AS author_name, c.zh AS cat_zh, c.en AS cat_en "
           "FROM articles a JOIN authors u ON u.handle=a.author JOIN categories c ON c.key=a.cat "
           + ("WHERE " + " AND ".join(where) if where else "")
           + f" ORDER BY {SORTS.get(sort, SORTS['hot'])}, a.id DESC LIMIT ? OFFSET ?")
    with connect() as con:
        top = _max_hot(con)
        rows = [_row(r) for r in con.execute(sql, [*args, limit, offset])]
    for r in rows:
        r["heat"] = max(1, round(100 * r["hot"] / top))
        if not with_body:
            r["paras"] = []
    return rows


def count_articles(**kw):
    # shortcut: 全量取行再计数，文章上万时改成 SELECT COUNT(*)
    return len(list_articles(**kw, limit=100000))


def get_article(slug=None, id=None, include_unpublished=False):
    sql = ("SELECT a.*, u.name AS author_name, u.bio_zh, u.bio_en, c.zh AS cat_zh, c.en AS cat_en "
           "FROM articles a JOIN authors u ON u.handle=a.author JOIN categories c ON c.key=a.cat WHERE ")
    sql += "a.slug=?" if slug is not None else "a.id=?"
    if not include_unpublished:
        sql += " AND a.status='published'"
    with connect() as con:
        return _row(con.execute(sql, (slug if slug is not None else id,)).fetchone())


def article_status(external_id):
    with connect() as con:
        r = con.execute("SELECT status FROM articles WHERE external_id=?", (external_id,)).fetchone()
        return r[0] if r else None


def context_list(a, ctx):
    """同作者按时间倒序，同类目按热度——与设计稿一致。"""
    if ctx == "author":
        return list_articles(author=a["author"], sort="new", limit=200)
    return list_articles(cat=a["cat"], sort="hot", limit=200)


def next_for(a, ctx, exclude=()):
    ex = set(exclude) | {a["slug"]}
    for c in (ctx, "cat" if ctx == "author" else "author"):
        lst = context_list(a, c)
        i = next((k for k, x in enumerate(lst) if x["slug"] == a["slug"]), -1)
        for k in range(1, len(lst) + 1):
            n = lst[(i + k) % len(lst)]
            if n["slug"] not in ex:
                return n, c
    return None, None


def webhook_logs(limit=100):
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM webhook_log ORDER BY id DESC LIMIT ?", (limit,))]


# ---------- 写入 ----------

def interact(slug, field, on, who):
    """同一访客（IP 摘要）对同一篇：赞、收藏各算一次，可取消；阅读每天算一次。返回当前计数，文章不存在返回 None。"""
    assert field in COUNTS
    kind = f"views:{now_iso()[:10]}" if field == "views" else field
    with connect() as con:
        if not con.execute("SELECT 1 FROM articles WHERE slug=? AND status='published'", (slug,)).fetchone():
            return None
        if field == "views":  # 阅读记录只需留到第二天
            con.execute("DELETE FROM interactions WHERE kind LIKE 'views:%' AND kind < ?", (f"views:{now_iso()[:10]}",))
        if on:
            delta = con.execute("INSERT OR IGNORE INTO interactions VALUES (?,?,?,?)", (who, slug, kind, now_iso())).rowcount
        else:
            delta = -con.execute("DELETE FROM interactions WHERE who=? AND slug=? AND kind=?", (who, slug, kind)).rowcount
        if delta:
            con.execute(f"UPDATE articles SET {field}=MAX(0,{field}+?) WHERE slug=?", (delta, slug))
        return con.execute(f"SELECT {field} FROM articles WHERE slug=?", (slug,)).fetchone()[0]


def claim_nonce(sig, ttl):
    """签名只能用一次：首次见到返回 True，重放返回 False。"""
    now = int(datetime.now(timezone.utc).timestamp())
    with connect() as con:
        con.execute("DELETE FROM webhook_nonces WHERE ts < ?", (now - ttl,))
        return con.execute("INSERT OR IGNORE INTO webhook_nonces VALUES (?,?)", (sig, now)).rowcount == 1


def category_keys():
    with connect() as con:
        return [r[0] for r in con.execute("SELECT key FROM categories ORDER BY sort, key")]


def create_category(key, zh, en):
    """新建类目；key 已存在返回 False。"""
    with connect() as con:
        return con.execute("INSERT OR IGNORE INTO categories(key,zh,en,sort) "
                           "VALUES (?,?,?,(SELECT COALESCE(MAX(sort),-1)+1 FROM categories))", (key, zh, en)).rowcount == 1


def log_webhook(ok, status, message, ids=()):
    with connect() as con:
        con.execute("INSERT INTO webhook_log(ts,ok,status,message,external_ids) VALUES (?,?,?,?,?)",
                    (now_iso(), int(ok), status, message[:500], ",".join(ids)[:500]))


def slugify(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    if len(s) > 70:  # 按词截断，不把单词切成半个
        s = s[:71].rsplit("-", 1)[0] if "-" in s[:71] else s[:70]
    return s.rstrip("-")


def reading_minutes(paras):
    text = " ".join(p[0] for p in paras)
    cjk = len(re.findall(r"[一-鿿]", text))
    words = len(re.findall(r"[A-Za-z0-9']+", text))
    return max(1, round(cjk / 400 + words / 220))


def upsert_author(con, handle, name="", bio_zh="", bio_en=""):
    """空值不覆盖已有资料；新作者没给名字时用 handle。"""
    con.execute("INSERT INTO authors(handle,name,bio_zh,bio_en) VALUES (?,?,?,?) "
                "ON CONFLICT(handle) DO UPDATE SET name=COALESCE(NULLIF(?,''),name), "
                "bio_zh=COALESCE(NULLIF(excluded.bio_zh,''),bio_zh), bio_en=COALESCE(NULLIF(excluded.bio_en,''),bio_en)",
                (handle, name or handle, bio_zh, bio_en, name))


def upsert_article(d):
    """d 已校验。按 external_id 幂等：已存在则更新内容；计数只替换来源部分，本站增量保留。返回 (slug, created)。"""
    with connect() as con:
        upsert_author(con, d["author"]["handle"], d["author"]["name"],
                      d["author"].get("bio_zh", ""), d["author"].get("bio_en", ""))
        old = con.execute("SELECT id, slug FROM articles WHERE external_id=?", (d["external_id"],)).fetchone()
        base = slugify(d.get("slug")) or slugify(d["title"] if d["lang"] == "en" else d.get("title_t")) \
            or "p-" + slugify(d["external_id"])[-24:] or "p"
        slug = old["slug"] if old and not d.get("slug") else base
        n = 2
        while con.execute("SELECT 1 FROM articles WHERE slug=? AND external_id<>?", (slug, d["external_id"])).fetchone():
            slug, n = f"{base}-{n}", n + 1
        ts = now_iso()
        vals = dict(external_id=d["external_id"], slug=slug, title=d["title"], title_t=d.get("title_t", ""),
                    lang=d["lang"], author=d["author"]["handle"], cat=d["cat"], source_url=d.get("source_url", ""),
                    summary=d.get("summary", ""), cover=d.get("cover", ""), paras=json.dumps(d["paras"], ensure_ascii=False),
                    mins=d.get("mins") or reading_minutes(d["paras"]), status=d.get("status", "published"),
                    published_at=d.get("published_at") or ts, updated_at=ts)
        counts = {f: d[f] for f in COUNTS if d.get(f) is not None}
        if old:
            con.execute(f"UPDATE articles SET {', '.join(k + '=?' for k in vals)} WHERE id=?", [*vals.values(), old["id"]])
            for f, n in counts.items():
                con.execute(f"UPDATE articles SET {f}=MAX(0,{f}-src_{f}+?), src_{f}=? WHERE id=?", (n, n, old["id"]))
        else:
            vals.update(created_at=ts, **counts, **{f"src_{f}": n for f, n in counts.items()})
            con.execute(f"INSERT INTO articles({','.join(vals)}) VALUES ({','.join('?' * len(vals))})", list(vals.values()))
        return slug, old is None


def update_article(id, fields):
    allowed = {"title", "title_t", "slug", "lang", "author", "cat", "source_url", "summary", "cover", "paras", "mins",
               "status", "published_at"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if "paras" in fields:
        fields["paras"] = json.dumps(fields["paras"], ensure_ascii=False)
    fields["updated_at"] = now_iso()
    with connect() as con:
        con.execute(f"UPDATE articles SET {', '.join(k + '=?' for k in fields)} WHERE id=?", [*fields.values(), id])


def delete_article(id):
    with connect() as con:
        con.execute("DELETE FROM articles WHERE id=?", (id,))


def update_author(handle, name, bio_zh, bio_en):
    with connect() as con:
        con.execute("UPDATE authors SET name=?, bio_zh=?, bio_en=? WHERE handle=?", (name, bio_zh, bio_en, handle))


TOPIC_STATUSES = ("published", "draft")


def topics(status=None):
    """专题列表；items 为 [[slug, 入选理由], ...]，按编辑给定顺序。"""
    sql = "SELECT * FROM topics" + (" WHERE status=?" if status else "") + " ORDER BY sort, updated_at DESC"
    with connect() as con:
        rows = [dict(r) for r in con.execute(sql, (status,) if status else ())]
    for r in rows:
        r["items"] = json.loads(r["items"])
    return rows


def topic(key, include_draft=False):
    return next((t for t in topics(None if include_draft else "published") if t["key"] == key), None)


def topic_articles(t):
    """专题里仍已发布的文章，保持编辑顺序，带上入选理由 note。"""
    notes = dict((s, n) for s, n in t["items"])
    rows = {r["slug"]: r for r in list_articles(slugs=list(notes), limit=len(notes) or 1)}
    return [{**rows[s], "note": notes[s]} for s, _ in t["items"] if s in rows]


def save_topic(key, title, intro, items, status, sort):
    with connect() as con:
        con.execute("UPDATE topics SET title=?, intro=?, items=?, status=?, sort=?, updated_at=? WHERE key=?",
                    (title, intro, json.dumps(items, ensure_ascii=False), status, sort, now_iso(), key))


def create_topic(key, title):
    """新建草稿专题；key 已存在返回 False。"""
    with connect() as con:
        return con.execute("INSERT OR IGNORE INTO topics(key,title,updated_at) VALUES (?,?,?)",
                           (key, title, now_iso())).rowcount == 1


def delete_topic(key):
    with connect() as con:
        con.execute("DELETE FROM topics WHERE key=?", (key,))


def update_category(key, zh, en, tag_zh, tag_en, sort):
    with connect() as con:
        con.execute("UPDATE categories SET zh=?, en=?, tag_zh=?, tag_en=?, sort=? WHERE key=?",
                    (zh, en, tag_zh, tag_en, sort, key))
