"""WebHook 载荷校验与归一化。信任边界：这里之外的代码只接收合法 dict。"""
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import db, indexnow

# 推送参数的 JSON Schema：GET /api/webhook 原样返回，MCP 写入工具也用它作参数定义
SCHEMA = json.loads((Path(__file__).parent / "webhook_schema.json").read_text())

LANGS = ("en", "zh")
MAX_TEXT = 20000


def _s(v, field, required=False, max_len=MAX_TEXT):
    if v is None or v == "":
        if required:
            raise ValueError(f"缺少字段 {field}")
        return ""
    if not isinstance(v, str):
        raise ValueError(f"{field} 必须是字符串")
    v = v.strip()
    if len(v) > max_len:
        raise ValueError(f"{field} 过长（上限 {max_len}）")
    return v


def _split(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


IMG_RE = re.compile(r"!\[(.*?)\]\((\S+)\)", re.S)


def make_para(text, trans="", kind="p", src=""):
    """段落存储形状：[原文, 译文]；小标题 [原文, 译文, "h2"]；配图 [图注, 图注译文, "img", 图片地址]。"""
    if kind == "img" and not re.match(r"^https://\S+$", src or ""):
        raise ValueError("图片地址必须是 https 链接")
    return [text, trans] + (["h2"] if kind == "h2" else ["img", src] if kind == "img" else [])


def parse_block(block):
    """纯文本段落约定（body 字段与后台编辑共用）：`## 小标题`、`![图注](https://图片)`，其余为正文。返回 (kind, text, src)。"""
    if block.startswith("##"):
        return "h2", block.lstrip("#").strip(), ""
    if m := IMG_RE.fullmatch(block):
        return "img", m.group(1).strip(), m.group(2)
    return "p", block, ""


def join_blocks(orig, trans):
    """把原文、译文两组纯文本段落按序号合成段落列表；段落类型以原文为准。"""
    o, t = [parse_block(x) for x in _split(orig)], [parse_block(x) for x in _split(trans)]
    return [make_para(text, t[i][1] if i < len(t) else "", kind, src) for i, (kind, text, src) in enumerate(o)]


def block_text(p, i):
    """段落转回纯文本（后台编辑框）。i=0 原文，1 译文；图片两侧都带地址，保证译文段号对齐。"""
    kind = p[2] if len(p) > 2 else "p"
    return f"## {p[i]}" if kind == "h2" else f"![{p[i]}]({p[3]})" if kind == "img" else p[i]


def _count(v, field):
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 10**12:
        raise ValueError(f"{field} 必须是非负整数")
    return v


def normalize(raw):
    if not isinstance(raw, dict):
        raise ValueError("每篇文章必须是 JSON 对象")
    src = _s(raw.get("source_url"), "source_url", max_len=2000)
    if src and not re.match(r"^https?://", src):
        raise ValueError("source_url 必须是 http(s) 链接")
    ext = _s(raw.get("external_id"), "external_id", max_len=300) or src
    if not ext:
        raise ValueError("external_id 和 source_url 至少提供一个")

    au = raw.get("author")
    if not isinstance(au, dict):
        raise ValueError("缺少 author 对象")
    handle = _s(au.get("handle"), "author.handle", True, 60).lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,60}", handle):
        raise ValueError("author.handle 只能包含字母、数字、下划线")
    author = {"handle": handle, "name": _s(au.get("name"), "author.name", max_len=120),  # 空 = 不改已有作者名
              "bio_zh": _s(au.get("bio_zh"), "author.bio_zh", max_len=500),
              "bio_en": _s(au.get("bio_en"), "author.bio_en", max_len=500)}

    lang = raw.get("lang", "en")
    if lang not in LANGS:
        raise ValueError("lang 只能是 en 或 zh")
    cat = _s(raw.get("category"), "category", True, 40)
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", cat):
        raise ValueError("category 只能用小写字母、数字、-、_")
    if cat not in (keys := db.category_keys()):  # 写错的 key 不再自动建类目
        raise ValueError(f"未知类目 {cat}；可用：{'/'.join(keys)}。新类目请先在后台「类目」页创建")

    paras = []
    if raw.get("paragraphs") is not None:
        if not isinstance(raw["paragraphs"], list):
            raise ValueError("paragraphs 必须是数组")
        for i, p in enumerate(raw["paragraphs"]):
            if isinstance(p, str):
                paras.append([_s(p, f"paragraphs[{i}]", True), ""])
            elif isinstance(p, dict):
                kind = {"paragraph": "p", "heading": "h2", "image": "img"}.get(p.get("type", "paragraph"))
                if not kind:
                    raise ValueError(f"paragraphs[{i}].type 只能是 paragraph、heading 或 image")
                try:
                    paras.append(make_para(_s(p.get("text"), f"paragraphs[{i}].text", kind != "img"),
                                           _s(p.get("translation"), f"paragraphs[{i}].translation"), kind,
                                           _s(p.get("src"), f"paragraphs[{i}].src", kind == "img", 2000)))
                except ValueError as e:
                    raise ValueError(f"paragraphs[{i}]：{e}")
            else:
                raise ValueError(f"paragraphs[{i}] 必须是字符串或 {{text, translation}}")
    else:
        paras = join_blocks(_s(raw.get("body"), "body", True, 200000),
                            _s(raw.get("body_translated"), "body_translated", max_len=200000))
    if not paras:
        raise ValueError("正文为空")

    pub = _s(raw.get("published_at"), "published_at", max_len=40)
    if pub:
        try:
            pub = datetime.fromisoformat(pub.replace("Z", "+00:00")).isoformat()
        except ValueError:
            raise ValueError("published_at 必须是 ISO 8601 时间")
    cover = _s(raw.get("cover"), "cover", max_len=2000)
    if cover and not re.match(r"^https://\S+$", cover):
        raise ValueError("cover 必须是 https 图片链接")
    status = raw.get("status", "published")
    if status not in ("published", "draft"):
        raise ValueError("status 只能是 published 或 draft")
    mins = raw.get("mins")
    if mins is not None and not (isinstance(mins, int) and 0 < mins < 600):
        raise ValueError("mins 必须是 1–599 的整数")

    return {"external_id": ext, "source_url": src, "title": _s(raw.get("title"), "title", True, 300),
            "title_t": _s(raw.get("title_translated"), "title_translated", max_len=300), "lang": lang,
            "author": author, "cat": cat, "paras": paras, "summary": _s(raw.get("summary"), "summary", max_len=600),
            "slug": _s(raw.get("slug"), "slug", max_len=80), "cover": cover, "published_at": pub, "status": status, "mins": mins,
            "views": _count(raw.get("views"), "views"), "likes": _count(raw.get("likes"), "likes"),
            "saves": _count(raw.get("saves"), "saves")}


class BatchError(ValueError):
    def __init__(self, msg, index=None):
        super().__init__(msg)
        self.index = index


def publish(items, base, via="WebHook"):
    """整批校验后入库（WebHook 与 MCP 共用）：一篇不合法则整批不写入，抛 BatchError。结果写进后台 WebHook 日志。"""
    if not isinstance(items, list) or not 0 < len(items) <= 100:
        db.log_webhook(False, 400, f"{via}：articles 必须是 1–100 篇")
        raise BatchError("articles must be a list of 1-100 items")
    clean = []
    for i, raw in enumerate(items):
        try:
            clean.append(normalize(raw))
        except ValueError as e:
            db.log_webhook(False, 422, f"{via}：第 {i} 篇：{e}")
            raise BatchError(str(e), i)
    results, changed = [], []
    for d in clean:
        was = db.article_status(d["external_id"])
        slug, created = db.upsert_article(d)
        results.append({"external_id": d["external_id"], "slug": slug, "created": created, "url": f"{base}/a/{quote(slug)}"})
        if "published" in (was, d["status"]):  # 新发布、更新或下架都通知；一直是草稿的不外泄
            changed.append(results[-1]["url"])
    indexnow.notify(changed)
    db.log_webhook(True, 200, f"{via}：入库 {len(results)} 篇（新建 {sum(r['created'] for r in results)}）",
                   [r["external_id"] for r in results])
    return results
