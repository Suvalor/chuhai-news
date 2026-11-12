"""管理后台：单口令登录（ADMIN_PASSWORD），签名 Cookie 会话。"""
import hashlib
import hmac
import os
import re
import time
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import db, limits
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
    r = templates.TemplateResponse(request, "admin/" + name, {"base": site_url(request), **kw})
    r.headers["X-Robots-Tag"] = "noindex, nofollow"
    return r


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
    if status in db.STATUSES:
        db.update_article(id, {"status": status})
    return RedirectResponse(request.headers.get("referer") or "/admin", 303)


@router.post("/a/{id}/delete")
def delete(request: Request, id: int):
    if (r := guard(request)):
        return r
    db.delete_article(id)
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
         orig: str = Form(...), trans: str = Form(""), new_author_name: str = Form("")):
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
    err = ""
    if img_err:
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
        return RedirectResponse(f"/admin/a/{a['id']}?saved=1", 303)
    new_slug, _ = db.upsert_article({**data, "external_id": f"admin:{time.time_ns()}", "slug": s,
                                     "author": {"handle": data["author"], "name": new_author_name.strip()},  # 空 = 不改已有作者名
                                     "status": data["status"]})
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
