"""Optional browser acceptance check using public article content in a temporary DB.

Run from the repository root with Python + Playwright and Chrome installed:
    python scripts/check_home_ui.py
The live API does not expose created_at; collection dates are simulated locally.
No requests write to the live website.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
SEED = """
import json, sys
from datetime import datetime, timedelta, timezone
from app import db
db.init()
now = datetime.now(timezone.utc)
for i, a in enumerate(json.load(sys.stdin)):
    slug, _ = db.upsert_article({
        **a, 'external_id': 'preview:' + a['slug'], 'cat': a['category'],
        'title_t': a['title_translated'], 'paras': [[a['title'], a['title_translated'] or ''], [a['summary'] or a['title'], '']],
        'mins': 10,
    })
    with db.connect() as con:
        con.execute('UPDATE articles SET created_at=? WHERE slug=?',
                    ((now - timedelta(hours=i * 5)).isoformat(), slug))
first = db.list_articles(sort='collected', limit=1)[0]['slug']
for key in ('ui-check', 'weekly-2026-w48', 'monthly-2026-11'):
    db.create_topic(key, 'Local acceptance fixture')
    db.save_topic(key, 'Local acceptance fixture', 'Fixture introduction', [[first, 'Fixture editorial note']], 'published', 0)
"""


def main():
    payload = json.loads(subprocess.check_output([
        "curl", "-fsSL", "--max-time", "20",
        "https://www.chuhainews.com/api/articles?limit=100",
    ]))["articles"]
    assert len(payload) >= 5, "At least five live articles required"
    with tempfile.TemporaryDirectory(prefix="chuhai-home-") as tmp:
        env = {**os.environ, "CHUHAI_DB": str(Path(tmp) / "preview.db"),
               "SITE_URL": "http://127.0.0.1", "WEBHOOK_SECRET": "local-preview-only"}
        subprocess.run([str(PYTHON), "-c", SEED], cwd=ROOT, env=env,
                       input=json.dumps(payload), text=True, check=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        env["SITE_URL"] = url
        server = subprocess.Popen([str(PYTHON), "-m", "uvicorn", "app.main:app",
                                   "--host", "127.0.0.1", "--port", str(port)],
                                  cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    with urlopen(url, timeout=1):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("Preview server failed to start")
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome", headless=True)
                page = browser.new_page(viewport={"width": 1512, "height": 812},
                                        reduced_motion="reduce")
                errors = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                page.goto(url, wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                metrics = page.evaluate("""() => ({
                    firstScreen: [...document.querySelectorAll('.hot5-title')].filter(e => {
                        const r = e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight;
                    }).length,
                    feedTop: Math.round(document.querySelector('.home-feed').getBoundingClientRect().top),
                    mastHeight: Math.round(document.querySelector('.home-mast').getBoundingClientRect().height),
                    width: document.documentElement.scrollWidth,
                    fontSizes: [...new Set([...document.querySelectorAll('body *')].filter(e =>
                      e.getClientRects().length && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
                    ).map(e => getComputedStyle(e).fontSize))].sort(),
                    sectionSpacing: [...new Set([...document.querySelectorAll('.home-section, body[data-view=home] .sec')].map(e => getComputedStyle(e).paddingBottom))]
                })""")
                print("desktop:", metrics, flush=True)
                assert metrics["firstScreen"] >= 5
                assert metrics["feedTop"] <= 700
                assert metrics["mastHeight"] <= 160
                assert metrics["width"] == 1512
                assert len(metrics["fontSizes"]) <= 8
                assert metrics["sectionSpacing"] == ["64px"]
                page.screenshot(path="/tmp/chuhai-home-desktop.png")
                first = page.locator(".card").first
                slug = first.get_attribute("data-slug")
                like = first.locator("[data-like]")
                save = first.locator("[data-save]")
                like.click()
                assert like.get_attribute("aria-pressed") == "true"
                save.click()
                assert save.get_attribute("aria-pressed") == "true"
                page.goto(url + "/saved", wait_until="networkidle")
                page.locator(f'.card[data-slug="{slug}"]').wait_for()
                page.goto(url, wait_until="networkidle")
                page.locator(f'.card[data-slug="{slug}"] .stretch').click()
                page.wait_for_url(f"**/a/{slug}")
                page.wait_for_function("(slug) => JSON.parse(localStorage.getItem('chuhai.v3')).read[slug]", arg=slug)
                page.goto(url, wait_until="networkidle")
                assert "is-read" in page.locator(f'.card[data-slug="{slug}"]').get_attribute("class")
                page.locator("[data-unread-only]").check()
                assert not page.locator(f'.card[data-slug="{slug}"]').is_visible()
                page.locator("[data-unread-only]").uncheck()
                assert page.locator(f'.card[data-slug="{slug}"]').is_visible()
                page.locator("#feed-heading").click()
                page.keyboard.press("/")
                assert page.locator("#home-q").evaluate("e => e === document.activeElement")
                page.set_viewport_size({"width": 375, "height": 812})
                page.goto(url, wait_until="networkidle")
                mobile = page.evaluate("""() => ({
                    width: document.documentElement.scrollWidth,
                    viewport: innerWidth,
                    chipScroll: document.querySelector('.home-cats').scrollWidth,
                    chipWidth: document.querySelector('.home-cats').clientWidth,
                    chipWrap: getComputedStyle(document.querySelector('.home-cats')).flexWrap
                })""")
                print("mobile:", mobile, flush=True)
                assert mobile["width"] == mobile["viewport"] == 375
                assert mobile["chipScroll"] > mobile["chipWidth"] and mobile["chipWrap"] == "nowrap"
                page.screenshot(path="/tmp/chuhai-home-mobile.png")
                # Cross-page acceptance: identical header and article-card tokens.
                sample = payload[0]
                routes = ["/", "/c/" + sample["category"], "/u/" + sample["author"]["handle"],
                          "/search?q=" + sample["title"][:20], "/saved", "/rank", "/about",
                          "/tools/excel-ratio-converter", "/t/ui-check",
                          "/weekly/2026-w48", "/monthly/2026-11"]
                from urllib.parse import quote
                for width in (375, 768, 1512):
                    page.set_viewport_size({"width": width, "height": 812})
                    page.goto(url, wait_until="networkidle")
                    baseline = page.locator(".logo-w").evaluate("e => getComputedStyle(e).fontSize")
                    card_size = page.locator(".home-feed .card-title").first.evaluate("e => getComputedStyle(e).fontSize")
                    for route in routes:
                        page.goto(url + quote(route, safe="/?=&"), wait_until="networkidle")
                        assert page.locator(".home-nav").count() == 1, route
                        assert page.locator(".logo-w").evaluate("e => getComputedStyle(e).fontSize") == baseline, route
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, route)
                        if route.startswith(("/c/", "/u/", "/search", "/saved", "/t/", "/weekly/", "/monthly/")):
                            page.locator(".card-title").first.wait_for()
                            assert page.locator(".card-title").first.evaluate("e => getComputedStyle(e).fontSize") == card_size, route
                        if route == "/saved":
                            page.locator('[data-saved-tab="liked"]').click()
                            page.locator(".card-title").first.wait_for()
                            assert page.locator('[data-saved-tab="liked"]').get_attribute("aria-selected") == "true"
                            page.locator('[data-saved-tab="saved"]').click()
                            page.locator(".card-title").first.wait_for()
                        print("shared UI:", width, route, flush=True)
                    page.goto(url + "/a/" + slug, wait_until="networkidle")
                    assert page.locator(".art-h").count() >= 1
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, "reader")
                    switch = page.locator(".trans-btn")
                    if switch.is_visible():
                        state = switch.get_attribute("aria-pressed")
                        switch.click()
                        assert switch.get_attribute("aria-pressed") != state
                        switch.click()
                        assert switch.get_attribute("aria-pressed") == state
                    print("reader UI:", width, flush=True)
                assert not errors, errors
                browser.close()
            print(f"PASS: {len(payload)} live article records, desktop/mobile layout and interactions")
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
