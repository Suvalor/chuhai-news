"""Regression coverage for GEO quality gates, editorial reports and migrations."""
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from app import db
from app.main import app


@pytest.fixture
def geo(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "geo.db")
    monkeypatch.setenv("SITE_URL", "https://geo.test")
    monkeypatch.setenv("ADMIN_PASSWORD", "geo-test")
    monkeypatch.setenv("WEBHOOK_SECRET", "geo-token")
    monkeypatch.delenv("INDEXNOW_KEY", raising=False)
    db.init()
    with closing(TestClient(app)) as client:
        yield client


def seed(client, n, author="writer", summary="编辑导读"):
    payload = {
        "external_id": f"geo:{n}", "title": f"Original {n}", "title_translated": f"中文译题 {n}",
        "lang": "en", "category": "ai", "author": {"handle": author, "name": author},
        "summary": summary, "paragraphs": [{"text": "Original body", "translation": "中文正文"}],
    }
    response = client.post("/api/webhook", headers={"Authorization": "Bearer geo-token"}, json=payload)
    assert response.status_code == 200
    return db.list_articles(author=author, limit=100), payload


def locs(client):
    return {e.text for e in ET.fromstring(client.get("/sitemap.xml").content).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")}


def test_quality_gate_and_private_permission(geo):
    rows, payload = seed(geo, 1)
    a = rows[0]
    url = "/a/" + a["slug"]
    public = "https://geo.test" + url
    html = geo.get(url).text
    assert 'content="noindex,follow"' in html and public not in locs(geo)
    assert '<title>中文译题 1' in html and 'class="art translated"' in html
    assert "Original 1" in html and "中文正文" in html
    md = geo.head(url + ".md")
    assert md.status_code == 200 and md.headers["x-robots-tag"] == "noindex"
    assert md.headers["link"] == f'<{public}>; rel="canonical"'
    assert 'content="noindex,follow"' in geo.get("/u/writer").text
    assert "https://geo.test/u/writer" not in locs(geo)
    assert 'content="noindex,follow"' in geo.get("/c/global").text
    assert "https://geo.test/c/global" not in locs(geo)
    assert "出海与 AI 实战长文精选" in geo.get("/").text
    assert "AI 实战长文精选：模型、Agent 与工作流" in geo.get("/c/ai").text

    geo.post("/admin/login", data={"password": "geo-test"})
    form = {"id": a["id"], "title": a["title"], "title_t": a["title_t"], "author": "writer",
            "cat": "ai", "lang": "en", "status": "published", "slug": a["slug"],
            "orig": "Original body", "trans": "中文正文", "editorial_fields": "true",
            "summary": "导读" * 40, "editor_take": "析" * 599,
            "license_status": "unknown", "license_proof": ""}
    assert geo.post("/admin/a/save", data=form, follow_redirects=False).status_code == 303
    assert 'content="noindex,follow"' in geo.get(url).text
    form["editor_take"] += "析"
    assert geo.post("/admin/a/save", data=form, follow_redirects=False).status_code == 303
    assert 'content="index,follow,' in geo.get(url).text and public in locs(geo)
    assert "编辑深度拆解" in geo.get(url).text
    assert "编辑深度拆解" in geo.get(url + ".md").text

    proof = "https://example.test/private-proof"
    form.update(editor_take="", license_status="granted", license_proof=proof)
    assert geo.post("/admin/a/save", data=form, follow_redirects=False).status_code == 303
    assert 'content="index,follow,' in geo.get(url).text and public in locs(geo)
    for path in (url, url + ".md", "/api/articles/" + a["slug"], "/llms-full.txt"):
        assert proof not in geo.get(path).text
    assert "已获作者授权" in geo.get(url).text
    form["license_proof"] = ""
    assert "已授权必须填写" in geo.post("/admin/a/save", data=form).text
    assert db.get_article(a["slug"])["license_proof"] == proof
    # Re-pushing through the real webhook never changes editorial or license fields.
    payload["summary"] = "背景" * 40
    assert geo.post("/api/webhook", headers={"Authorization": "Bearer geo-token"}, json=payload).status_code == 200
    assert db.get_article(a["slug"])["license_proof"] == proof
    assert db.get_article(a["slug"])["license_status"] == "granted"
    form.update(license_status="unknown", license_proof="", editor_take="")
    assert geo.post("/admin/a/save", data=form, follow_redirects=False).status_code == 303
    assert public not in locs(geo)
    assert db.get_article(a["slug"])["license_proof"] == ""
    seed(geo, 2)
    assert 'content="index,follow"' in geo.get("/u/writer").text
    assert "https://geo.test/u/writer" in locs(geo)


def test_reports_publish_only_with_editorial_content(geo):
    rows, _ = seed(geo, 3)
    slug = rows[0]["slug"]
    with db.connect() as con:
        con.execute("UPDATE articles SET created_at=?, views=1234 WHERE slug=?", ("2026-11-24T01:00:00+00:00", slug))
    geo.post("/admin/login", data={"password": "geo-test"})
    for key, path in (("weekly-2026-w48", "/weekly/2026-w48"), ("monthly-2026-11", "/monthly/2026-11")):
        assert geo.get(path).status_code == 404
        assert geo.post("/admin/topics", data={"key": key, "title": key}, follow_redirects=False).status_code == 303
        bad = {"title": key, "status": "published", "intro": "", "items": slug}
        assert geo.post("/admin/topics/" + key, data=bad).status_code == 400
        assert geo.get(path).status_code == 404
        good = {**bad, "intro": "编辑真实观察：这个主题适用于初学者。", "items": slug + " | 编辑判断：有用；适用人群：初学者"}
        assert geo.post("/admin/topics/" + key, data=good, follow_redirects=False).status_code == 303
        page = geo.get(path)
        assert page.status_code == 200 and "编辑真实观察" in page.text and "适用人群" in page.text
        assert "https://geo.test" + path in locs(geo)
        assert geo.get("/t/" + key, follow_redirects=False).headers["location"] == path
        assert path in geo.get("/llms.txt").text
        if key.startswith("monthly"):
            assert "数据口径与样本" in page.text and "1 篇" in page.text
    assert geo.get("/weekly/2026-w99").status_code == 404
    assert geo.get("/monthly/2026-13").status_code == 404
    assert "最近 7 天收录的热读前三" in geo.get("/rank").text


def test_legacy_db_migration(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    # Create the existing schema without the three new columns.
    old = "\n".join(line for line in db.SCHEMA.splitlines() if "editor_take TEXT" not in line)
    assert "editor_take TEXT" not in old
    with sqlite3.connect(path) as conn:
        conn.executescript(old)
        assert "editor_take" not in {r[1] for r in conn.execute("PRAGMA table_info(articles)")}
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init()
    db.init()
    with db.connect() as con:
        cols = {r[1] for r in con.execute("PRAGMA table_info(articles)")}
    assert {"editor_take", "license_status", "license_proof"} <= cols
