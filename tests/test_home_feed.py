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
        assert home.index('class="wrap home-section home-feed"') < home.index('class="s-authors home-au"')
        second = client.get("/?page=2").text
        assert re.findall(r'class="card" data-slug="([^"]+)"', second) == ["feed-30", "feed-31", old]
        rank = client.get("/rank?sort=views").text
        assert rank.index(f'href="/a/{old}"') < rank.index('href="/a/feed-31"')
        assert 'class="sorts"' in rank
        assert client.get("/?sort=views", follow_redirects=False).headers["location"] == "/rank?sort=views&page=1"
        assert 'class="row"' in client.get("/c/ai").text
        assert 'class="row"' in client.get("/u/writer").text
        assert 'class="row"' in client.get("/search?q=Original").text
        assert client.get("/saved").status_code == 200
        client.cookies.set("ui", "en")
        en = client.get("/").text
        assert "Recently collected" in en and "Unread only" in en and "Full ranking" in en
