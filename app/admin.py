"""管理后台：单口令登录（ADMIN_PASSWORD），签名 Cookie 会话。"""
import hashlib
import hmac
import os
import re
import time
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import db, geo, indexnow, limits
from fastapi import HTTPException
from datetime import date
from .ingest import block_text, join_blocks
from .main import site_url, templates

router = APIRouter(prefix="/admin")
COOKIE = "chuhai_admin"
TTL = 7 * 86400


def _key():
    pw = os.getenv("ADMIN_PASSWORD", "")
    return hashlib.sha256((os.getenv("SECRET_KEY", "") + "|" + pw).encode()).digest() if pw else None


def _token(exp):
    return f"{exp}.{hmac.new(_key(), str(exp).encode(), hashlib.sha256).hexdigest()}"


def authed(request):
    key, tok = _key(), request.cookies.get(COOKIE, "")
    if not key or "." not in tok:
        return False
    exp = tok.split(".", 1)[0]
    return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(tok, _token(int(exp)))


def page(request, name, **kw):
    r = templates.TemplateResponse(request, "admin/" + name, {"base": site_url(request), "topic_path": db.topic_path, **kw})
    r.headers["X-Robots-Tag"] = "noindex, nofollow"
    return r


def ping(request, *paths):
    """页面公开状态有变化时通知 IndexNow。"""
    indexnow.notify([site_url(request) + p for p in paths])


def guard(request):
    return None if authed(request) else RedirectResponse("/admin/login", 303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return page(request, "login.html", disabled=_key() is None, error="")


@router.post("/login")
def login(request: Request, password: str = Form("")):
    pw, key = os.getenv("ADMIN_PASSWORD", ""), "login:" + limits.client_ip(request)
    if limits.blocked(key):
        r = page(request, "login.html", disabled=not pw, error="失败次数过多，请 15 分钟后再试")
        r.status_code = 429
        return r
    if not pw or not hmac.compare_digest(password.encode(), pw.encode()):
        limits.fail(key)
        time.sleep(0.5)  # 减缓暴力猜测
        return page(request, "login.html", disabled=not pw, error="口令不正确")
    r = RedirectResponse("/admin", 303)
    r.set_cookie(COOKIE, _token(int(time.time()) + TTL), max_age=TTL, httponly=True, samesite="strict",
                 secure=request.url.scheme == "https", path="/admin")
    return r


@router.post("/logout")
def logout():
    r = RedirectResponse("/admin/login", 303)
    r.delete_cookie(COOKIE, path="/admin")
    return r


@router.get("", response_class=HTMLResponse)
def articles(request: Request, q: str = "", status: str = "", cat: str = "", author: str = "", page_no: int = 1):
    if (r := guard(request)):
        return r
    st = status if status in db.STATUSES else None
    rows = db.list_articles(q=q.strip() or None, status=st, cat=cat or None, author=author or None, sort="new",
                            limit=51, offset=(max(1, page_no) - 1) * 50)
    counts = {s: db.count_articles(status=s) for s in db.STATUSES}
    return page(request, "list.html", rows=rows[:50], has_more=len(rows) > 50, q=q, status=status, cat=cat,
                author=author, page_no=max(1, page_no), counts=counts, authors=db.authors(),
                cats={c["key"]: c["zh"] for c in db.categories()})


@router.post("/a/{id}/status")
def set_status(request: Request, id: int, status: str = Form(...)):
    if (r := guard(request)):
        return r
    a = db.get_article(id=id, include_unpublished=True)
    if a and status in db.STATUSES:
        db.update_article(id, {"status": status})
        if "published" in (a["status"], status):
            ping(request, f"/a/{a['slug']}")
    return RedirectResponse(request.headers.get("referer") or "/admin", 303)


@router.post("/a/{id}/delete")
def delete(request: Request, id: int):
    if (r := guard(request)):
        return r
    a = db.get_article(id=id, include_unpublished=True)
    db.delete_article(id)
    if a and a["status"] == "published":
        ping(request, f"/a/{a['slug']}")
    return RedirectResponse("/admin", 303)


def _form_ctx(a, error=""):
    return dict(a=a, error=error, cats=db.categories(), authors=db.authors(), statuses=db.STATUSES,
                orig="\n\n".join(block_text(p, 0) for p in a["paras"]) if a else "",
                trans="\n\n".join(block_text(p, 1) for p in a["paras"]) if a else "")


@router.get("/a/new", response_class=HTMLResponse)
def new_form(request: Request):
    if (r := guard(request)):
        return r
    return page(request, "edit.html", **_form_ctx(None))


@router.get("/a/{id}", response_class=HTMLResponse)
def edit_form(request: Request, id: int):
    if (r := guard(request)):
        return r
    a = db.get_article(id=id, include_unpublished=True)
    return page(request, "edit.html", **_form_ctx(a)) if a else RedirectResponse("/admin", 303)


@router.post("/a/save")
def save(request: Request, id: int = Form(0), title: str = Form(...), title_t: str = Form(""), slug: str = Form(""),
         lang: str = Form("en"), author: str = Form(...), cat: str = Form(...), source_url: str = Form(""),
         summary: str = Form(""), cover: str = Form(""), status: str = Form("draft"), published_at: str = Form(""),
         orig: str = Form(...), trans: str = Form(""), new_author_name: str = Form(""),
         editor_take: str | None = Form(None), license_status: str | None = Form(None),
         license_proof: str | None = Form(None), editorial_fields: bool = Form(False)):
    if (r := guard(request)):
        return r
    try:
        paras, img_err = join_blocks(orig.replace("\r", ""), trans.replace("\r", "")), ""
    except ValueError as e:
        paras, img_err = [], str(e)
    data = dict(title=title.strip(), title_t=title_t.strip(), lang=lang if lang in ("en", "zh") else "en",
                author=author.strip().lstrip("@"), cat=cat, source_url=source_url.strip(), summary=summary.strip(),
                cover=cover.strip(),
                status=status if status in db.STATUSES else "draft", paras=paras)
    a = db.get_article(id=id, include_unpublished=True) if id else None
    if editorial_fields or editor_take is not None:
        data["editor_take"] = (editor_take or "").strip()
    if editorial_fields or license_status is not None:
        data["license_status"] = license_status or "unknown"
    if editorial_fields or license_proof is not None:
        data["license_proof"] = (license_proof or "").strip()
    effective = {**(a or {}), **data}
    err = ""
    if effective.get("license_status", "unknown") not in ("unknown", "pending", "granted", "denied"):
        err = "授权状态无效"
    elif effective.get("license_status") == "granted" and not re.fullmatch(r"https?://[^\s]+", effective.get("license_proof", "")):
        err = "已授权必须填写有效的 http(s) 凭证链接"
    elif img_err:
        err = img_err
    elif not data["title"] or not paras:
        err = "标题和正文不能为空"
    elif cat not in db.category_keys():
        err = "类目不存在"
    elif not re.fullmatch(r"[A-Za-z0-9_]{1,60}", data["author"]):
        err = "作者 handle 只能包含字母、数字、下划线"
    elif data["source_url"] and not re.match(r"^https?://", data["source_url"]):
        err = "原帖链接必须是 http(s)"
    elif data["cover"] and not re.match(r"^https://\S+$", data["cover"]):
        err = "封面图必须是 https 链接"
    try:
        data["published_at"] = datetime.fromisoformat(published_at).isoformat() if published_at else db.now_iso()
    except ValueError:
        err = err or "发布时间格式应为 2026-11-12T08:00"
    s = db.slugify(slug) or (a["slug"] if a else "")
    if s and (other := db.get_article(s, include_unpublished=True)) and (not a or other["id"] != a["id"]):
        err = err or f"slug「{s}」已被占用"
    if err:
        return page(request, "edit.html", **{**_form_ctx({**(a or {}), **data, "slug": slug, "id": id}, err),
                                             "orig": orig, "trans": trans})
    if not db.author(data["author"]):
        with db.connect() as con:
            db.upsert_author(con, data["author"], new_author_name.strip() or data["author"])
    if a:
        data["mins"] = db.reading_minutes(paras)
        if s:
            data["slug"] = s
        db.update_article(a["id"], data)
        if "published" in (a["status"], data["status"]):  # 改了 slug 时旧网址也通知，让它下线
            ping(request, f"/a/{a['slug']}", f"/a/{data.get('slug', a['slug'])}")
        return RedirectResponse(f"/admin/a/{a['id']}?saved=1", 303)
    new_slug, _ = db.upsert_article({**data, "external_id": f"admin:{time.time_ns()}", "slug": s,
                                     "author": {"handle": data["author"], "name": new_author_name.strip()},  # 空 = 不改已有作者名
                                     "status": data["status"]})
    if data["status"] == "published":
        ping(request, f"/a/{new_slug}")
    return RedirectResponse(f"/admin/a/{db.get_article(new_slug, include_unpublished=True)['id']}?saved=1", 303)


@router.get("/authors", response_class=HTMLResponse)
def authors(request: Request):
    if (r := guard(request)):
        return r
    return page(request, "authors.html", authors=db.authors())


@router.post("/authors/{handle}")
def save_author(request: Request, handle: str, name: str = Form(...), bio_zh: str = Form(""), bio_en: str = Form("")):
    if (r := guard(request)):
        return r
    db.update_author(handle, name.strip() or handle, bio_zh.strip(), bio_en.strip())
    return RedirectResponse("/admin/authors", 303)


@router.get("/categories", response_class=HTMLResponse)
def categories(request: Request):
    if (r := guard(request)):
        return r
    return page(request, "categories.html", cats=db.categories())


@router.post("/categories")
def create_category(request: Request, key: str = Form(...), zh: str = Form(...), en: str = Form(...)):
    if (r := guard(request)):
        return r
    key, err = key.strip().lower(), ""
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", key):
        err = "key 只能用小写字母、数字、-、_（1–40 位）"
    elif not zh.strip() or not en.strip():
        err = "中文名和英文名都要填"
    elif not db.create_category(key, zh.strip(), en.strip()):
        err = f"key「{key}」已存在"
    if err:
        r = page(request, "categories.html", cats=db.categories(), error=err, form={"key": key, "zh": zh, "en": en})
        r.status_code = 400
        return r
    return RedirectResponse("/admin/categories", 303)


@router.post("/categories/{key}")
def save_category(request: Request, key: str, zh: str = Form(...), en: str = Form(...), tag_zh: str = Form(""),
                  tag_en: str = Form(""), sort: int = Form(0)):
    if (r := guard(request)):
        return r
    db.update_category(key, zh.strip(), en.strip(), tag_zh.strip(), tag_en.strip(), sort)
    return RedirectResponse("/admin/categories", 303)


@router.get("/webhooks", response_class=HTMLResponse)
def webhooks(request: Request):
    if (r := guard(request)):
        return r
    return page(request, "webhooks.html", logs=db.webhook_logs(), configured=bool(os.getenv("WEBHOOK_SECRET")))


def _topic_items(text):
    """每行「slug | 入选理由」；返回 (items, 错误)。slug 必须是已有文章。"""
    items, seen = [], set()
    for line in text.replace("\r", "").splitlines():
        if not line.strip():
            continue
        slug, _, note = line.partition("|")
        slug = slug.strip().split("?")[0].rstrip("/").rsplit("/a/", 1)[-1].removesuffix(".md")
        if not db.get_article(slug, include_unpublished=True):
            return items, f"找不到文章「{slug}」"
        if slug not in seen:
            seen.add(slug)
            items.append([slug, note.strip()])
    return items, ""


@router.get("/topics", response_class=HTMLResponse)
def topics(request: Request):
    if (r := guard(request)):
        return r
    return page(request, "topics.html", topics=db.topics())


@router.post("/topics")
def create_topic(request: Request, key: str = Form(...), title: str = Form(...)):
    if (r := guard(request)):
        return r
    key, err = key.strip().lower(), ""
    if not re.fullmatch(r"[a-z0-9_-]{1,60}", key):
        err = "key 只能用小写字母、数字、-、_（1–60 位）"
    elif key.startswith(("weekly-", "monthly-")) and not db.report_period(key):
        err = "周报 key 使用 weekly-YYYY-wNN，月报使用 monthly-YYYY-MM，日期必须有效"
    elif not title.strip():
        err = "标题不能为空"
    elif not db.create_topic(key, title.strip()):
        err = f"key「{key}」已存在"
    if err:
        r = page(request, "topics.html", topics=db.topics(), error=err, form={"key": key, "title": title})
        r.status_code = 400
        return r
    return RedirectResponse(f"/admin/topics/{key}", 303)


@router.get("/topics/{key}", response_class=HTMLResponse)
def topic_form(request: Request, key: str):
    if (r := guard(request)):
        return r
    tp = db.topic(key, include_draft=True)
    if not tp:
        return RedirectResponse("/admin/topics", 303)
    text = "\n".join(f"{s} | {n}" if n else s for s, n in tp["items"])
    return page(request, "topic.html", tp=tp, text=text, statuses=db.TOPIC_STATUSES, error="")


@router.post("/topics/{key}")
def save_topic(request: Request, key: str, title: str = Form(...), intro: str = Form(""), items: str = Form(""),
               status: str = Form("draft"), sort: int = Form(0)):
    if (r := guard(request)):
        return r
    tp = db.topic(key, include_draft=True)
    if not tp:
        return RedirectResponse("/admin/topics", 303)
    parsed, err = _topic_items(items)
    if not title.strip():
        err = err or "标题不能为空"
    status = status if status in db.TOPIC_STATUSES else "draft"
    if db.report_period(key) and status == "published":
        if not intro.strip() or not parsed or not all(note.strip() and db.get_article(slug) for slug, note in parsed):
            err = err or "发布周报/月报需填写真实总结，并为每篇已发布文章填写编辑判断与适用人群"
    if err:
        r = page(request, "topic.html", tp={**tp, "title": title, "intro": intro, "status": status, "sort": sort},
                 text=items, statuses=db.TOPIC_STATUSES, error=err)
        r.status_code = 400
        return r
    db.save_topic(key, title.strip(), intro.strip(), parsed, status, sort)
    if "published" in (tp["status"], status):
        ping(request, db.topic_path(key))
    return RedirectResponse(f"/admin/topics/{key}?saved=1", 303)


@router.post("/topics/{key}/delete")
def delete_topic(request: Request, key: str):
    if (r := guard(request)):
        return r
    tp = db.topic(key, include_draft=True)
    db.delete_topic(key)
    if tp and tp["status"] == "published":
        ping(request, db.topic_path(key))
    return RedirectResponse("/admin/topics", 303)


@router.get("/geo", response_class=HTMLResponse)
def geo_dashboard(request: Request, run_id: int | None = None):
    if (r := guard(request)):
        return r
    response=page(request,"geo.html",**geo.dashboard(run_id), enabled=os.getenv("GEO_ENABLED")=="1", platforms=geo.PLATFORMS,bots=geo.BOTS)
    response.headers["Cache-Control"]="no-store"
    return response


def geo_back():
    return RedirectResponse("/admin/geo",303)


@router.post("/geo/run")
def geo_queue(request: Request):
    if (r := guard(request)): return r
    db.geo_enqueue()
    return geo_back()


@router.post("/geo/question")
def geo_save_question(request: Request, key: str=Form(...), text: str=Form(...), lang: str=Form(...),
                      kind: str=Form(...),keyword: str=Form(""),path: str=Form(""),active: bool=Form(False)):
    if (r := guard(request)): return r
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}",key) or not text.strip() or len(text)>2000 or lang not in ("zh","en") or kind not in ("position","category","problem","english") or (path and (not path.startswith("/") or path.startswith("//") or len(path)>500)):
        raise HTTPException(400,"invalid question")
    db.geo_question(key,text.strip(),lang,kind,keyword[:200],path,active)
    return geo_back()


@router.post("/geo/competitor")
def geo_save_competitor(request: Request,domain: str=Form(...),name: str=Form(...),note: str=Form(""),status: str=Form("confirmed")):
    if (r := guard(request)): return r
    domain=domain.lower().strip().removeprefix("www.")
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+",domain) or domain==geo.SELF or len(domain)>253 or not name.strip() or status not in ("confirmed","pending","ignored"):
        raise HTTPException(400,"invalid competitor")
    db.geo_write("INSERT INTO geo_competitors VALUES (?,?,?,?) ON CONFLICT(domain) DO UPDATE SET name=excluded.name,note=excluded.note,status=excluded.status",(domain,name[:200],note[:2000],status))
    return geo_back()


@router.post("/geo/event")
def geo_save_event(request: Request, day: str=Form(...),kind: str=Form(...),note: str=Form(...)):
    if (r := guard(request)): return r
    try: day=date.fromisoformat(day).isoformat()
    except ValueError: raise HTTPException(400,"invalid date")
    if not kind.strip() or not note.strip(): raise HTTPException(400,"event is empty")
    db.geo_write("INSERT INTO geo_events(date,kind,note) VALUES (?,?,?)",(day,kind[:100],note[:4000]))
    return geo_back()


def check_week(week):
    try:
        if not re.fullmatch(r"\d{4}-W\d{2}",week): raise ValueError()
        year,number=map(int,week.split("-W")); date.fromisocalendar(year,number,1)
    except ValueError: raise HTTPException(400,"invalid ISO week")


@router.post("/geo/crawler")
def geo_save_crawler(request: Request,week: str=Form(...),bot: str=Form(...),requests: int=Form(...)):
    if (r := guard(request)): return r
    check_week(week)
    if bot not in geo.BOTS or not 0<=requests<=10**12: raise HTTPException(400,"invalid crawler count")
    db.geo_write("INSERT INTO crawler_weekly VALUES (?,?,?) ON CONFLICT(week,bot) DO UPDATE SET requests=excluded.requests",(week,bot,requests))
    return geo_back()


@router.post("/geo/index")
def geo_save_index(request: Request,week: str=Form(...),engine: str=Form(...),checked: int=Form(...),indexed: int=Form(...),total: int=Form(...),note: str=Form("")):
    if (r := guard(request)): return r
    check_week(week)
    if engine not in ("google","bing") or not 0<=indexed<=checked<=total<=10**7: raise HTTPException(400,"invalid index sample")
    db.geo_write("INSERT INTO geo_index_weekly VALUES (?,?,?,?,?,?) ON CONFLICT(week,engine) DO UPDATE SET checked=excluded.checked,indexed=excluded.indexed,sitemap_total=excluded.sitemap_total,note=excluded.note",(week,engine,checked,indexed,total,note[:2000]))
    return geo_back()


@router.post("/geo/review")
def geo_review(request: Request,result_id: int=Form(...),accuracy: str=Form(...),confusion: bool=Form(False),recommended: bool=Form(False),rank: int=Form(0)):
    if (r := guard(request)): return r
    if accuracy not in ("accurate","partial","wrong","unknown") or rank<0 or rank>1000: raise HTTPException(400,"invalid review")
    db.geo_write("UPDATE geo_results SET accuracy=?,judged_by='human',entity_confusion=?,recommended=?,rank_pos=? WHERE id=? AND status='success'",(accuracy,int(confusion),int(recommended),rank if recommended and rank else None,result_id))
    rows=db.geo_read("SELECT run_id FROM geo_results WHERE id=?",(result_id,))
    return RedirectResponse("/admin/geo?run_id="+str(rows[0]["run_id"]),303) if rows else geo_back()


@router.post("/geo/task")
def geo_task(request: Request,task_id: int=Form(...),status: str=Form(...)):
    if (r := guard(request)): return r
    if status not in ("open","done","dismissed"): raise HTTPException(400,"invalid task status")
    db.geo_write("UPDATE geo_tasks SET status=?,updated_at=? WHERE id=?",(status,db.now_iso(),task_id))
    return geo_back()
