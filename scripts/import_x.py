"""从 X 链接导入文章：fxtwitter 取原文与指标 → DeepSeek 只出译文 → 生成 WebHook 载荷。默认只生成文件，不推送。

用法（在项目根目录）：
  .venv/bin/python scripts/import_x.py links.txt [--existing-prefix "xxx:x:"] [--min-chars 1500]
      读链接（每行一个 x.com/<用户>/status/<ID> 或本站 /a/<slug>，同一行可带类目 key，用 | 或 Tab 分隔），
      生成 data/import/<时间>.json（只含就绪的文章）并打印报告，不推送。
  .venv/bin/python scripts/import_x.py --push data/import/<时间>.json
      核对后推送到 SITE_URL/api/webhook。
环境变量（自动读项目根目录 .env）：SITE_URL、WEBHOOK_SECRET（推送用）、DEEPSEEK_API_KEY（不设则译文留空）、
DEEPSEEK_MODEL（默认 deepseek-flash）。
完整说明（输入格式、报告状态码、何时推送）见 scripts/README.md。
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import db  # noqa: E402
from app.importer import (STATUS_RE, UA, assemble, fetch, fill_translation, http_json,  # noqa: E402,F401
                          article_paras, note_paras, note_title, translate)  # 后四个给 tests/test_import_x.py 用
from app.ingest import normalize  # noqa: E402

SLUG_RE = re.compile(r"/a/([^\s/?#|)]+)")


def load_env():
    f = ROOT / ".env"
    for line in f.read_text().splitlines() if f.exists() else []:
        k, sep, v = line.partition("=")
        if sep and not k.strip().startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def http_text(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read().decode()


# ---------- 读链接 ----------

def read_links(path, site, cats):
    """返回 [(handle, id, category or "")]，按 ID 去重。本站 /a/<slug> 链接先查出原帖地址。"""
    out, seen = [], set()
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = STATUS_RE.search(line)
        if not m and (s := SLUG_RE.search(line)):
            m = STATUS_RE.search(http_json(f"{site}/api/articles/{s.group(1)}").get("source_url") or "")
        if not m:
            print(f"跳过无法识别的行：{line.strip()[:80]}")
            continue
        cat = next((p.strip() for p in re.split(r"[|\t]", line) if p.strip() in cats), "")
        if m.group(2) not in seen:
            seen.add(m.group(2))
            out.append((m.group(1), m.group(2), cat))
    return out


def existing_articles(site):
    """站上已发布文章：原帖 ID → 简要信息。shortcut: 只看最新 100 篇，文章多了改成按 ID 查询的接口。"""
    found = {}
    for a in http_json(f"{site}/api/articles?limit=100")["articles"]:
        if m := STATUS_RE.search(a.get("source_url") or ""):
            found[m.group(2)] = a
    return found


def existing_summary(site, slug):
    """API 在导读为空时会返回正文摘录，所以从 .md 里取真正的导读段。"""
    m = re.search(r"## 编者导读 / Editor's note\n\n(.*?)\n\n## 原文", http_text(f"{site}/a/{slug}.md"), re.S)
    return m.group(1).strip() if m else ""


# ---------- 组装 ----------

def build(handle, xid, cat, old, prefix, site, min_chars, use_llm):
    """返回 (payload or None, 报告行)。"""
    rep = {"id": xid, "url": f"https://x.com/{handle}/status/{xid}"}
    try:
        t = fetch(handle, xid)
    except ValueError as e:
        return None, {**rep, "status": "failed", "result": f"失败：{e}"}
    payload, paras, info = assemble(t, rep["url"])
    rep.update(info)
    if info["chars"] < min_chars:
        return None, {**rep, "status": "skipped_short", "result": f"跳过：原文 {info['chars']} 字符，低于 {min_chars}"}
    cat = cat or (old or {}).get("category") or ""

    if old:  # 沿用旧 external_id，否则会新建一篇重复文章
        eid = prefix + xid if prefix else ""
        if eid and old["slug"].startswith("p-") and "p-" + db.slugify(eid)[-24:] != old["slug"]:
            rep["warn"] = f"前缀 {prefix!r} 与线上网址 {old['slug']} 对不上"
            eid = ""
        rep["existing"] = old["slug"]
        summary = existing_summary(site, old["slug"])
    else:
        eid, summary = f"x:{xid}", ""

    payload.update(external_id=eid, category=cat, summary=summary, status="published" if old else "draft")
    # 先校验、后翻译：有问题的文章不花 DeepSeek token
    try:
        normalize({**payload, "external_id": eid or "check"})
    except ValueError as e:
        return None, {**rep, "status": "invalid", "result": f"校验不过：{e}"}
    if not eid:
        return None, {**rep, "status": "need_external_id", "result": "external_id 待确认：用 --existing-prefix 重跑"}
    if not cat:
        return None, {**rep, "status": "need_category", "result": "缺类目：在链接行里写类目 key"}

    if use_llm:
        fill_translation(payload, paras)
        if not summary:
            rep["summary"] = "DeepSeek 初稿，发布前请改"
    rep["translated"] = use_llm
    if old:
        return payload, {**rep, "status": "ready_update", "result": "就绪（更新已有页面）"}
    return payload, {**rep, "status": "ready_new", "result": "就绪（新建草稿）"}


def run(args):
    site = os.getenv("SITE_URL", "").rstrip("/")
    if not site:
        sys.exit("缺少 SITE_URL")
    db.DB_PATH = Path(tempfile.mkdtemp()) / "check.db"  # 本地校验用临时库，不碰真实数据
    db.init()
    use_llm = bool(os.getenv("DEEPSEEK_API_KEY"))
    if not use_llm:
        print("未设 DEEPSEEK_API_KEY：只取原文和指标，译文留空\n")
    links = read_links(args.links, site, db.category_keys())
    old = existing_articles(site)
    arts, report = [], []
    for handle, xid, cat in links:
        try:
            p, r = build(handle, xid, cat, old.get(xid), args.existing_prefix, site, args.min_chars, use_llm)
        except Exception as e:  # 一篇失败不影响其他篇
            p, r = None, {"id": xid, "url": f"https://x.com/{handle}/status/{xid}", "status": "failed",
                         "result": f"失败：{e}"}
        report.append(r)
        if p:
            arts.append(p)
        print(f"[{r['result']}] {r['url']}  {r.get('kind', '')} {r.get('chars', '')}字符 {r.get('paras', '')}段 "
              f"{r.get('images', '')}图" + (f"  线上 /a/{r['existing']}" if r.get("existing") else "")
              + (f"  ⚠ {r['warn']}" if r.get("warn") else ""))
        time.sleep(0.5)
    out = ROOT / "data" / "import" / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"site": site, "articles": arts, "report": report}, ensure_ascii=False, indent=1))
    print(f"\n已生成 {out.relative_to(ROOT)}（{len(arts)} 篇）。核对后推送：.venv/bin/python scripts/import_x.py --push {out.relative_to(ROOT)}")


def push(path):
    """推送 JSON 里的 articles（run 只把就绪的文章写进去）。没有译文的不推，免得把线上页面换成无译文版本。"""
    site, secret = os.getenv("SITE_URL", "").rstrip("/"), os.getenv("WEBHOOK_SECRET", "")
    data = json.loads(Path(path).read_text())
    arts = [a for a in data["articles"] if a.get("external_id") and a.get("category") and a.get("title_translated")]
    print(f"推送 {len(arts)} 篇，跳过 {len(data['articles']) - len(arts)} 篇（缺 external_id、类目或译文）")
    if not (site and secret and arts):
        sys.exit("没有可推送的文章，或缺 SITE_URL / WEBHOOK_SECRET")
    batch, size = [], 0
    for a in arts + [None]:  # None 收尾，推最后一批
        n = len(json.dumps(a, ensure_ascii=False).encode()) if a else 0
        if batch and (a is None or len(batch) == 100 or size + n > 4_000_000):
            try:
                r = http_json(f"{site}/api/webhook", {"articles": batch}, {"Authorization": "Bearer " + secret}, timeout=120)
            except urllib.error.HTTPError as e:  # 422 带 {error, index}：整批没写入，按提示改 JSON 后重推
                sys.exit(f"推送失败 HTTP {e.code}：{e.read().decode()[:500]}")
            for x in r["results"]:
                print(("新建" if x["created"] else "更新"), x["url"])
            batch, size = [], 0
        if a:
            batch.append(a)
            size += n


if __name__ == "__main__":
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("links", nargs="?", help="链接文件")
    ap.add_argument("--push", metavar="JSON", help="推送已核对的 JSON")
    ap.add_argument("--existing-prefix", default="", help="站上已有文章的 external_id 前缀（ID 之前的部分）")
    ap.add_argument("--min-chars", type=int, default=1500, help="原文少于这个字符数的跳过")
    a = ap.parse_args()
    push(a.push) if a.push else run(a) if a.links else ap.print_help()
