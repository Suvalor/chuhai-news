"""Homepage collection ordering, recent trending and legacy ranking regression."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import re

from fastapi.testclient import TestClient

from app import db
from app.main import app


def test_home_collection_feed(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "home.db")
    now = datetime.now(timezone.utc)
    db.init()
    with closing(TestClient(app)) as client:
        def article(n, collected, published, views, status="published"):
            slug, _ = db.upsert_article({
                "external_id": f"home:{n}", "title": f"Original {n}",
                "title_t": f"译文 {n}", "slug": f"feed-{n}", "lang": "en",
                "author": {"handle": "writer", "name": "Writer"}, "cat": "ai",
                "summary": f"导读 {n}", "paras": [["Original body", "原文正文"]],
                "published_at": published.isoformat(), "views": views, "status": status,
            })
            with db.connect() as con:
                con.execute("UPDATE articles SET created_at=? WHERE slug=?", (collected.isoformat(), slug))
            return slug

        for n in range(32):
            article(n, now - timedelta(days=n / 10), now - timedelta(days=10 + n), 100 + n)
        old = article(90, now - timedelta(days=4), now - timedelta(days=5), 9999999)
        article(91, now, now, 9999999, "draft")

        home = client.get("/").text
        hot = home.split('class="wrap home-section hot5"', 1)[1].split("</section>", 1)[0]
        assert hot.count('class="hot5-row"') == 5
        assert f"/a/{old}" not in hot and "/a/feed-91" not in home
        assert "译文" in hot and "热度" in hot
        slugs = re.findall(r'class="card" data-slug="([^"]+)"', home)
        assert slugs == [f"feed-{n}" for n in range(30)]
        assert "导读 0" in home and "只看未读" in home
        assert 'data-save="feed-0"' in home and 'data-like="feed-0"' in home
        assert 'href="?page=2#rank"' in home
        # Hide the homepage author directory, not article attribution or author pages.
        assert 'class="s-authors home-au"' not in home
        assert "跟着人读" not in home
        assert 'href="/u/writer"' in home and "Writer" in home
        assert "1位作者" in home
        second = client.get("/?page=2").text
        assert re.findall(r'class="card" data-slug="([^"]+)"', second) == ["feed-30", "feed-31", old]
        rank = client.get("/rank?sort=views").text
        assert rank.index(f'href="/a/{old}"') < rank.index('href="/a/feed-31"')
        assert 'class="sorts"' in rank
        assert client.get("/?sort=views", follow_redirects=False).headers["location"] == "/rank?sort=views&page=1"
        assert 'class="row"' in rank  # 专用榜单布局不变
        for path in ("/c/ai", "/u/writer", "/search?q=Original"):
            page = client.get(path).text
            assert 'class="card" data-slug=' in page
            assert 'class="wrap home-nav"' in page and 'class="home-chip' in page
            assert 'class="card-metrics mono"' in page
            assert 'data-like=' in page and 'data-save=' in page
            assert 'class="row"' not in page
        category = client.get("/c/ai").text
        assert 'href="/c/ai" class="home-chip on" aria-current="page"' in category
        saved = client.get("/saved")
        assert saved.status_code == 200 and 'class="card-list"' in saved.text
        fragment = client.get("/fragment/rows?layout=cards&slugs=feed-0").text
        assert 'class="card" data-slug="feed-0"' in fragment and 'data-save="feed-0"' in fragment
        assert 'class="row"' in client.get("/fragment/rows?slugs=feed-0").text
        client.cookies.set("ui", "en")
        en = client.get("/").text
        assert "Recently collected" in en and "Unread only" in en and "Full ranking" in en
        assert 'class="s-authors home-au"' not in en
        assert 'href="/u/writer"' in en and "Writer" in en


def test_hot5_keeps_chinese_original_title(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "hot5.db")
    db.init()
    with closing(TestClient(app)) as client:
        now = datetime.now(timezone.utc).isoformat()
        for n, lang, title, title_t in ((1, "zh", "中文原题", "English Title"), (2, "en", "Original EN", "英文译题")):
            db.upsert_article({
                "external_id": f"hot5:{n}", "title": title, "title_t": title_t, "slug": f"hot5-{n}",
                "lang": lang, "author": {"handle": "w", "name": "W"}, "cat": "ai", "summary": "导读",
                "paras": [["正文", "body"]], "published_at": now, "views": 100, "status": "published",
            })
        hot = client.get("/").text.split('class="wrap home-section hot5"', 1)[1].split("</section>", 1)[0]
        assert "中文原题" in hot and "English Title" not in hot
        assert "英文译题" in hot and "Original EN" not in hot


def test_author_without_published_articles_is_404(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "empty-author.db")
    db.init()
    with closing(TestClient(app)) as client:
        db.upsert_article({
            "external_id": "draft:1", "title": "Draft", "slug": "draft-1", "lang": "en",
            "author": {"handle": "ghost", "name": "Ghost"}, "cat": "ai", "summary": "s",
            "paras": [["body", "正文"]], "published_at": datetime.now(timezone.utc).isoformat(), "status": "draft",
        })
        assert db.author("ghost")["n"] == 0
        r = client.get("/u/ghost")
        assert r.status_code == 404 and 'content="noindex"' in r.text
