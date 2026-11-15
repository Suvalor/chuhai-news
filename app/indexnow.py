"""IndexNow：页面发布、修改、下架后通知 Bing、Yandex、Naver 等（Google 不参与）。
未设 INDEXNOW_KEY 或 SITE_URL 不是 https 时不推送；推送在后台线程里发，失败只记日志，不影响发布。"""
import json
import logging
import os
import re
import threading
import urllib.request
from urllib.parse import urlsplit

ENDPOINT = "https://api.indexnow.org/indexnow"
log = logging.getLogger("chuhai.indexnow")


def key():
    k = os.getenv("INDEXNOW_KEY", "").strip()
    return k if re.fullmatch(r"[A-Za-z0-9-]{8,128}", k) else ""


def _post(payload):
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            log.info("IndexNow %s：%d 个网址", r.status, len(payload["urlList"]))
    except Exception as e:  # 网络或 4xx 都只记日志
        log.warning("IndexNow 推送失败（%d 个网址）：%s", len(payload["urlList"]), e)


def _send(payload):
    threading.Thread(target=_post, args=(payload,), daemon=True).start()


def notify(urls):
    """推送本站网址；返回是否发出。只推 SITE_URL 下的网址，别的主机 IndexNow 会整批拒收。"""
    k, site = key(), os.getenv("SITE_URL", "").rstrip("/")
    if not (k and site.startswith("https://")):
        return False
    urls = sorted({u for u in urls if u and u.startswith(site + "/")})[:10000]
    if not urls:
        return False
    _send({"host": urlsplit(site).hostname, "key": k, "keyLocation": f"{site}/{k}.txt", "urlList": urls})
    return True
