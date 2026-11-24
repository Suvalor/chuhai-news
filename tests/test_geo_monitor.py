"""GEO monitor verification: no real paid API calls."""
import csv
import json
import os
from contextlib import closing
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from app import db, geo, geo_run
from app.main import app


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"geo.db")
    monkeypatch.setenv("ADMIN_PASSWORD","test-monitor")
    monkeypatch.setenv("SITE_URL","https://chuhainews.com")
    monkeypatch.setenv("GEO_ENABLED","0")
    for key,_,_,_ in geo_run.CONFIG.values(): monkeypatch.delenv(key,raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY",raising=False)
    monkeypatch.setattr(geo,"_day",None)
    db.init()
    return tmp_path


def test_referrals_and_admin(monitor):
    with closing(TestClient(app)) as c:
        for _ in range(2): assert c.get("/?utm_source=chatgpt.com",headers={"User-Agent":"Browser"}).status_code==200
        c.get("/about",headers={"Referer":"https://chatgpt.com/c/example","User-Agent":"Browser"})
        c.get("/?utm_source=chatgpt.com",headers={"User-Agent":"Another Browser"})
        c.get("/?utm_source=chatgpt.com",headers={"User-Agent":"OAI-SearchBot"})
        c.get("/?utm_source=unknown",headers={"User-Agent":"Browser"})
        c.get("/?utm_source=gemini",headers={"User-Agent":"Browser"})
        data=db.geo_read("SELECT source,path,uv FROM ai_referrals ORDER BY source")
        assert data==[{"source":"chatgpt","path":"/","uv":2},{"source":"gemini","path":"/","uv":1}]
        assert c.get("/admin/geo",follow_redirects=False).status_code==303
        assert c.post("/admin/geo/run",follow_redirects=False).status_code==303
        c.post("/admin/login",data={"password":"test-monitor"})
        r=c.get("/admin/geo")
        assert r.status_code==200 and r.headers["cache-control"]=="no-store" and "GEO Monitor" in r.text
        assert c.post("/admin/geo/run",follow_redirects=False).status_code==303
        c.post("/admin/geo/run",follow_redirects=False)
        assert len(db.geo_read("SELECT * FROM geo_runs"))==1
        assert c.post("/admin/geo/crawler",data={"week":"2026-W48","bot":"OAI-SearchBot","requests":12},follow_redirects=False).status_code==303
        c.post("/admin/geo/crawler",data={"week":"2026-W48","bot":"OAI-SearchBot","requests":20})
        assert db.geo_read("SELECT requests FROM crawler_weekly")==[{"requests":20}]
        assert c.post("/admin/geo/index",data={"week":"2026-W48","engine":"google","checked":10,"indexed":4,"total":30},follow_redirects=False).status_code==303
        assert c.get("/admin/geo").status_code==200
        assert c.post("/admin/geo/event",data={"day":"2026-11-25","kind":"标题","note":"test"},follow_redirects=False).status_code==303
        q=db.geo_read("SELECT * FROM geo_questions WHERE question_key='q01'")[0]
        c.post("/admin/geo/question",data={"key":"q01","text":"new question","lang":"zh","kind":"position","active":"true"})
        assert [(x["version"],x["active"]) for x in db.geo_read("SELECT * FROM geo_questions WHERE question_key='q01' ORDER BY version")]==[(1,0),(2,1)]
        db.init()
        assert db.geo_read("SELECT COUNT(*) n FROM geo_questions WHERE active=1")[0]["n"]==10


def test_parser_and_denominator(monitor):
    cs=db.geo_read("SELECT * FROM geo_competitors")
    assert geo.normalize_url("http://WWW.ChuhaiNews.com/a/test/?utm_source=chatgpt.com&x=2#part")=="https://chuhainews.com/a/test?x=2"
    assert not geo.normalize_url("https://user:password@chuhainews.com")
    marks,cites=geo.analyze("1. xread\n2. ChuhaiNews",["https://www.chuhainews.com/a/test.md?utm_source=x"],cs)
    assert marks=={"brand_hit":1,"recommended":1,"rank_pos":2} and cites[0]["page_type"]=="markdown"
    marks,_=geo.analyze("看出海新闻的渠道很多。",[],cs); assert not marks["brand_hit"]
    marks,_=geo.analyze("这个方法不错。[[1]](https://chuhainews.com/a/test)",["https://chuhainews.com/a/test"],cs)
    assert marks["brand_hit"] and not marks["recommended"]
    rows=[{"id":1,"status":"success","brand_hit":1,"recommended":0,"rank_pos":None},{"id":2,"status":"failed"},{"id":3,"status":"skipped"}]
    m=geo.metrics(rows,[{"result_id":1,"is_self":1}])
    assert m["brand"]=="1/1" and m["success"]=="1/2" and m["citation"]=="1/1"
    assert geo.rate(10,20)=="10/20 (50.0%)"


def test_provider_payload_parsing(monitor):
    raw={"output":[{"type":"message","content":[{"type":"output_text","text":"answer","annotations":[{"type":"url_citation","url":"https://xread.page"}]}]}]}
    assert geo_run.parse("chatgpt",raw)==("answer",["https://xread.page"])
    assert geo_run.parse("grok",{"output":[{"type":"message","content":[{"type":"output_text","text":"a[[1]](https://xread.page)"}]}]})[1]==["https://xread.page"]
    g={"candidates":[{"content":{"parts":[{"text":"answer"}]},"groundingMetadata":{"groundingChunks":[{"web":{"uri":"https://xread.page"}}]}}]}
    assert geo_run.parse("gemini",g)==("answer",["https://xread.page"])
    with pytest.raises(ValueError): geo_run.parse("chatgpt",{"status":"failed"})


def enable(monkeypatch,max_usd):
    monkeypatch.setenv("GEO_ENABLED","1")
    monkeypatch.setenv("GEO_MAX_USD",str(max_usd))
    for p,(key,_,_,_) in geo_run.CONFIG.items():
        monkeypatch.setenv(key,"fake")
        monkeypatch.setenv("GEO_"+p.upper()+"_CALL_USD","0.01")


def test_worker_600_requests_and_tasks(monitor,monkeypatch):
    for n in range(11,51): db.geo_question("q"+str(n),"Question "+str(n),"en","problem","","/t/test")
    enable(monkeypatch,20)
    calls=[]
    def fake(p,q,k,m):
        calls.append((p,q))
        return {"model":"fixture"},"Read xread",["https://xread.page"]
    monkeypatch.setattr(geo_run,"provider",fake)
    first=db.geo_enqueue()
    assert "finished" in geo_run.run()
    assert len(calls)==600
    assert db.geo_read("SELECT calls,expected,status FROM geo_runs WHERE id=?",(first,))==[{"calls":600,"expected":600,"status":"finished"}]
    assert db.geo_read("SELECT count(*) n FROM geo_results WHERE run_id=?",(first,))[0]["n"]==600
    assert "no queued" in geo_run.run()
    second=db.geo_enqueue()
    assert "finished" in geo_run.run()
    assert len(calls)==1200
    assert db.geo_read("SELECT count(*) n FROM geo_tasks WHERE priority='high' AND kind='content'")[0]["n"]==50
    assert geo.dashboard(second)["stats"]["success"]=="600/600 (100.0%)"


def test_preflight_and_retry_budget(monitor,monkeypatch):
    enable(monkeypatch,0.5)
    calls=[]
    monkeypatch.setattr(geo_run,"provider",lambda *a: calls.append(a))
    rid=db.geo_enqueue()
    assert "limited before" in geo_run.run() and not calls
    assert db.geo_read("SELECT calls,status FROM geo_runs WHERE id=?",(rid,))==[{"calls":0,"status":"limited"}]
    enable(monkeypatch,1.21)
    def fail(*args):
        calls.append(args)
        raise TimeoutError()
    monkeypatch.setattr(geo_run,"provider",fail)
    rid=db.geo_enqueue()
    assert "limited" in geo_run.run()
    r=db.geo_read("SELECT calls,cost_est FROM geo_runs WHERE id=?",(rid,))[0]
    assert r["calls"]==len(calls) and r["cost_est"]<=1.21 and len(calls)<=121
    assert geo.dashboard(rid)["stats"]["n"]==0


def test_csv_import(monitor):
    path=monitor/"manual.csv"
    fields=["date","question_key","version","platform","repeat_no","answer_text","citations_json","hit_mode","rank_pos","accuracy","entity_confusion"]
    with path.open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
        writer.writerow(dict(date="2026-11-25",question_key="q01",version=1,platform="chatgpt",repeat_no=1,answer_text="ChuhaiNews",citations_json=json.dumps(["https://www.chuhainews.com/?utm_source=x"]),hit_mode="recommended",rank_pos=1,accuracy="accurate"))
    assert geo_run.import_csv(path)==1
    assert geo_run.import_csv(path)==1
    assert db.geo_read("SELECT count(*) n FROM geo_runs")[0]["n"]==1
    assert db.geo_read("SELECT count(*) n FROM geo_results")[0]["n"]==1
    assert db.geo_read("SELECT count(*) n FROM geo_citations")[0]["n"]==1
    assert geo.dashboard()["stats"]["citation"]=="1/1"
