"""GEO analysis and anonymous AI referral collection; no provider calls on HTTP requests."""
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import db

PLATFORMS = ("chatgpt", "gemini", "perplexity", "grok")
BOTS = ("OAI-SearchBot","PerplexityBot","Claude-SearchBot","DuckAssistBot","Applebot")
SOURCES = {"chatgpt.com":"chatgpt","chat.openai.com":"chatgpt","perplexity.ai":"perplexity",
           "gemini.google.com":"gemini","grok.com":"grok","copilot.microsoft.com":"copilot","claude.ai":"claude"}
SELF = "chuhainews.com"
BRAND = re.compile(r"\bchuhai\s*news\b",re.I)
URL_RE = re.compile(r"https?://[^\s<>\[\]\"'）)]+",re.I)
DAY_ZONE = timezone(timedelta(hours=8))
_lock = threading.Lock()
_day = None
_salt = b""
_seen = set()
_counts = defaultdict(int)
FACTS = """chuhainews (chuhainews.com) curates long-form X posts about practical AI, going global,
business, tools, thinking and creation. Public reading is free; bilingual originals and AI
translations (not individually human reviewed) are available when translated. Some articles
have editorial notes/analysis. Do not assume every article has permission, human review,
or that the site owns original posts. Brand spelling chuhainews. Judge only descriptions of this site."""


def normalize_url(raw):
    try:
        p = urlsplit(raw.strip().rstrip(".,;。；"))
        host = (p.hostname or "").lower().rstrip(".")
        if p.scheme not in ("https","http") or not host or p.username or p.password:
            return ""
        if not re.fullmatch(r"[a-z0-9.-]+",host) or not "." in host or p.port not in (None,80,443):
            return ""
        if host.startswith("www."):
            host = host[4:]
        path = p.path.rstrip("/")
        query = urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                           if not k.lower().startswith("utm_") and k.lower() not in ("fbclid","gclid","ref","source","mc_cid","mc_eid")])
        return urlunsplit(("https",host,path,query,""))
    except (ValueError,AttributeError):
        return ""


def domain(url):
    try: return urlsplit(url).hostname or ""
    except ValueError: return ""


def page_type(url):
    path = urlsplit(url).path
    if not path: return "home"
    if path.endswith(".md"): return "markdown"
    if path == "/rank": return "rank"
    for prefix,kind in (("/c/","category"),("/t/","topic"),("/a/","article"),("/u/","author"),
                        ("/weekly/","weekly"),("/monthly/","monthly")):
        if path.startswith(prefix): return kind
    return "other"


def match_competitor(host, competitors):
    for c in competitors:
        if c["status"] == "confirmed" and (host == c["domain"] or host.endswith("."+c["domain"])):
            return c["domain"]
    return ""


def analyze(text, urls, competitors):
    citations = []
    for raw in urls:
        url = normalize_url(raw)
        if not url or any(c["url"] == url for c in citations):
            continue
        host = domain(url)
        citations.append(dict(url=url,domain=host,is_self=int(host==SELF),
                              page_type=page_type(url) if host==SELF else "other",
                              competitor=match_competitor(host,competitors)))
    # First occurrence of distinct website names/domains in the answer. Strip
    # a trailing source-only section so citations are not mistaken for recommendations.
    body = re.split(r"(?im)^\s*(?:#{1,6}\s*)?(?:sources|references|citations|参考资料|引用来源|来源)\s*[:：]?\s*$",text,maxsplit=1)[0]
    # Numeric citation anchors are sources, not a textual recommendation.
    body = re.sub(r"\[\[?\d+\]?\]\(https?://[^)]+\)", "", body)
    occurrences = [(m.start(),SELF) for m in BRAND.finditer(body)]
    for m in URL_RE.finditer(body):
        url=normalize_url(m.group())
        if url: occurrences.append((m.start(),domain(url)))
    for c in competitors:
        if c["status"]=="confirmed":
            for term in (c["domain"],c["name"]):
                for m in re.finditer(re.escape(term),body,re.I):
                    occurrences.append((m.start(),c["domain"]))
    order=[]
    for _,host in sorted(occurrences):
        if host not in order: order.append(host)
    # This is a deterministic proxy for recommendation; manual review can correct it.
    # Exclude explicit negation near brand references, not merely source-only citations.
    negative=re.search(r"(?:不推荐|不要使用|not recommend|avoid)\s*(?:使用\s*)?chuhai\s*news",body,re.I)
    rank=order.index(SELF)+1 if SELF in order and not negative else None
    return dict(brand_hit=int(bool(BRAND.search(text) or any(c["is_self"] for c in citations) or SELF in order)),
                recommended=int(rank is not None),rank_pos=rank),citations


def source_for(utm, referer):
    u=(utm or "").strip().lower()
    if u in PLATFORMS or u in ("copilot","claude"): return u
    for val in (u,domain(referer or "")):
        for host,source in SOURCES.items():
            if val==host or val=="www."+host: return source
    return ""


def track(request):
    """Dedup per day/platform, first landing page; only aggregate counts persist."""
    global _day,_salt
    source=source_for(request.query_params.get("utm_source"),request.headers.get("referer"))
    if not source: return
    path=request.url.path
    if not (path in ("/","/rank","/about","/search","/saved") or path.startswith(("/a/","/c/","/u/","/t/","/weekly/","/monthly/","/tools/"))): return
    if len(path)>500: return
    ua=request.headers.get("user-agent","")[:1000]
    if re.search(r"bot|crawler|spider|curl|wget",ua,re.I): return
    ip=request.client.host if request.client else "?"
    # Uvicorn already consumes trusted proxy XFF. CF header is opt-in only
    # for deployments whose reverse proxy strips untrusted inbound headers.
    if os.getenv("GEO_TRUST_CF_IP")=="1":
        try: ip=str(ipaddress.ip_address(request.headers.get("cf-connecting-ip","")))
        except ValueError: pass
    day=datetime.now(DAY_ZONE).date().isoformat()
    with _lock:
        if _day!=day:
            _day=day; _salt=secrets.token_bytes(32); _seen.clear(); _counts.clear()
        who=hmac.new(_salt,(day+"|"+ip+"|"+ua).encode(),hashlib.sha256).digest()
        key=(source,who)
        if key in _seen: return
        # Bound RAM; remaining events are omitted, preserving the lower-bound definition.
        if len(_seen)>=200000: return
        bucket=(source,path)
        count=_counts[bucket]+1
        db.geo_referral_count(day,source,path,count)
        _seen.add(key); _counts[bucket]=count


def rate(n,d):
    return f"{n}/{d}"+(f" ({100*n/d:.1f}%)" if d>=20 else "")


def metrics(rows, citations):
    ok=[r for r in rows if r["status"]=="success"]
    ids={r["id"] for r in ok}
    cited={c["result_id"] for c in citations if c["is_self"] and c["result_id"] in ids}
    ranks=[r["rank_pos"] for r in ok if r["rank_pos"] is not None and r["recommended"]]
    n=len(ok)
    brand_n=sum(r["brand_hit"] for r in ok); recommend_n=sum(r["recommended"] for r in ok)
    return dict(n=n,expected=sum(r["status"]!="skipped" for r in rows),brand=rate(brand_n,n),
                recommend=rate(recommend_n,n),citation=rate(len(cited),n),
                brand_pct=100*brand_n/n if n>=20 else None, recommend_pct=100*recommend_n/n if n>=20 else None,
                citation_pct=100*len(cited)/n if n>=20 else None,
                success=rate(n,sum(r["status"]!="skipped" for r in rows)),rank=f"{sum(ranks)/len(ranks):.2f} (n={len(ranks)})" if ranks else "—")


def calibration(run,rows,citations):
    if not run: return []
    month=run["started_at"][:7]
    manual=db.geo_read("""SELECT r.* FROM geo_results r JOIN geo_runs g ON r.run_id=g.id
        WHERE g.source='manual' AND substr(g.started_at,1,7)=? AND r.status='success' ORDER BY r.id DESC""",(month,))
    api=db.geo_read("""SELECT r.* FROM geo_results r JOIN geo_runs g ON r.run_id=g.id
        WHERE g.source='api' AND g.status='finished' AND substr(g.started_at,1,7)=? AND r.status='success' ORDER BY r.run_id DESC""",(month,))
    # Compare the latest available round, not a mixture of multiple runs.
    if api:
        last=api[0]["run_id"]; api=[r for r in api if r["run_id"]==last]
    if manual:
        last=manual[0]["run_id"]; manual=[r for r in manual if r["run_id"]==last]
    all_ids={r["id"] for r in manual+api}
    domains=defaultdict(set)
    if all_ids:
        for c in db.geo_read("SELECT result_id,domain FROM geo_citations WHERE result_id IN ("+",".join("?" for _ in all_ids)+")",tuple(all_ids)):
            domains[c["result_id"]].add(c["domain"])
    out=[]
    for p in PLATFORMS:
        agreements=0; n=0; overlap=[]
        for q in sorted({r["question_id"] for r in manual if r["platform"]==p}):
            a=[r for r in api if r["platform"]==p and r["question_id"]==q]
            m=next((r for r in manual if r["platform"]==p and r["question_id"]==q),None)
            if not m or len(a)!=3: continue
            n+=1; agreements+=int((sum(r["brand_hit"] for r in a)>=2)==bool(m["brand_hit"]))
            aset=set().union(*(domains[r["id"]] for r in a)); mset=domains[m["id"]]
            if aset|mset: overlap.append(len(aset&mset)/len(aset|mset))
        out.append(dict(platform=p,n=n,agreement=rate(agreements,n),
                        overlap=f"{sum(overlap)/len(overlap):.2f} (n={len(overlap)})" if overlap else "—",
                        warning=n<5 or agreements<n or (bool(overlap) and sum(overlap)/len(overlap)<.5)))
    return out


def dashboard(run_id=None):
    runs=db.geo_read("SELECT * FROM geo_runs ORDER BY id DESC LIMIT 60")
    run=next((r for r in runs if r["id"]==run_id),None) if run_id else next((r for r in runs if r["status"] in ("finished","limited","partial","imported")),None)
    rows=[]; citations=[]
    if run:
        rows=db.geo_read("""SELECT r.*,q.text,q.lang,q.type,q.version,q.question_key,q.expect_path FROM geo_results r JOIN geo_questions q ON q.id=r.question_id WHERE run_id=? ORDER BY q.question_key,r.platform,r.repeat_no""",(run["id"],))
        citations=db.geo_read("SELECT c.* FROM geo_citations c JOIN geo_results r ON r.id=c.result_id WHERE r.run_id=? ORDER BY c.order_no",(run["id"],))
    competitors=db.geo_read("SELECT * FROM geo_competitors ORDER BY status,domain")
    grouped=[]
    for key in sorted({(r["platform"],r["type"],r["lang"],r["version"]) for r in rows}):
        group=[r for r in rows if (r["platform"],r["type"],r["lang"],r["version"])==key]
        grouped.append(dict(key=key,**metrics(group,citations)))
    def comparable_signature(rs):
        return {(r["question_id"],r["platform"],r["repeat_no"],r["region"],r["model"]) for r in rs if r["status"]!="skipped"}
    history=[]
    for old in reversed([r for r in runs if r["status"] in ("finished","partial","limited","imported")]):
        rr=db.geo_read("SELECT * FROM geo_results WHERE run_id=?",(old["id"],))
        cc=db.geo_read("SELECT c.* FROM geo_citations c JOIN geo_results r ON r.id=c.result_id WHERE run_id=?",(old["id"],))
        # Do not compare changed question sets, or API results with manual calibration.
        if run and (old["source"]!=run["source"] or comparable_signature(rr)!=comparable_signature(rows)): continue
        history.append(dict(run=old,**metrics(rr,cc)))
    for r in rows:
        r["citations"]=[c for c in citations if c["result_id"]==r["id"]]
    confirmed=[]
    okids={r["id"] for r in rows if r["status"]=="success"}
    for c in competitors:
        hit={x["result_id"] for x in citations if (x["domain"]==c["domain"] or x["domain"].endswith("."+c["domain"])) and x["result_id"] in okids}
        confirmed.append(dict(c,frequency=rate(len(hit),len(okids))))
    pages=defaultdict(int)
    for c in citations:
        if c["is_self"] and c["result_id"] in okids: pages[(c["url"],c["page_type"])]+=1
    # Stable random ordering of the human QA sample for this run, independent of outcome.
    judged=[r for r in rows if r["judged_by"] in ("deepseek","human")]
    sample=sorted(judged,key=lambda r:hashlib.sha256(f'{run["id"]}:{r["id"]}'.encode()).digest())[:(len(judged)+9)//10] if run else []
    sample_ids={r["id"] for r in sample}
    for r in rows: r["review_sample"]=r["id"] in sample_ids
    return dict(runs=runs,run=run,rows=rows,stats=metrics(rows,citations),groups=grouped,history=history,
                calibration=calibration(run,rows,citations),review_required=len(sample),review_done=sum(r["judged_by"]=="human" for r in sample),
                competitors=confirmed,pages=sorted(pages.items(),key=lambda x:-x[1]),
                questions=db.geo_read("SELECT * FROM geo_questions ORDER BY question_key,version DESC"),
                referrals=db.geo_read("SELECT date,source,path,uv FROM ai_referrals ORDER BY date DESC,uv DESC LIMIT 200"),
                crawlers=db.geo_read("SELECT * FROM crawler_weekly ORDER BY week DESC,bot LIMIT 100"),
                indexing=db.geo_read("SELECT * FROM geo_index_weekly ORDER BY week DESC LIMIT 100"),
                events=db.geo_read("SELECT * FROM geo_events ORDER BY date DESC,id DESC LIMIT 100"),
                tasks=db.geo_read("SELECT * FROM geo_tasks ORDER BY status,priority DESC,id DESC LIMIT 100"))


def generate_tasks(run_id):
    runs=db.geo_read("SELECT id FROM geo_runs WHERE source='api' AND status='finished' ORDER BY id DESC LIMIT 2")
    for c in db.geo_read("SELECT DISTINCT c.url FROM geo_citations c JOIN geo_results r ON r.id=c.result_id WHERE r.run_id=? AND r.status='success' AND c.is_self=1",(run_id,)):
        path=urlsplit(c["url"]).path or "/"
        db.geo_write("INSERT OR IGNORE INTO geo_tasks(path,kind,priority,note,updated_at) VALUES (?,'maintenance','high','优先维护：更新、编辑拆解及索引检查',?)",(path,db.now_iso()))
    if len(runs)!=2 or runs[0]["id"]!=run_id: return
    prior=runs[1]["id"]
    for q in db.geo_read("SELECT * FROM geo_questions WHERE active=1"):
        rows=db.geo_read("SELECT * FROM geo_results WHERE question_id=? AND run_id IN (?,?)",(q["id"],run_id,prior))
        # Require all four platforms and all three repetitions, both rounds.
        if len(rows)!=24 or any(r["status"]!="success" or r["brand_hit"] for r in rows) or len({(r["run_id"],r["platform"],r["repeat_no"]) for r in rows})!=24: continue
        cs=db.geo_read("SELECT DISTINCT c.url,c.competitor FROM geo_citations c JOIN geo_results r ON r.id=c.result_id WHERE r.question_id=? AND r.run_id IN (?,?) AND c.competitor!=''",(q["id"],run_id,prior))
        path=q["expect_path"]
        exists=path in ("/","/rank","/about") or (path.startswith("/a/") and db.get_article(path[3:])) or (path.startswith("/t/") and db.topic(path[3:])) or (path.startswith("/c/") and any(c["key"]==path[3:] for c in db.categories()))
        note=("补强 "+path if exists else "创建问题型专题")+"："+q["text"]
        if cs: note+="；参考 "+", ".join(c["url"] for c in cs[:5])
        db.geo_write("INSERT INTO geo_tasks(question_id,path,kind,priority,note,updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(question_id,kind) DO UPDATE SET note=excluded.note,priority=excluded.priority,updated_at=excluded.updated_at",
                     (q["id"],path,"content","high" if cs else "observe",note,db.now_iso()))
