"""P1/P2 gates — HTTP tests for ?fqn=, KG Overview (6), multi-target, review queue."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dsxlineage.db.database import Base, get_db
import dsxlineage.estate.models  # noqa: F401
import dsxlineage.db.models  # noqa: F401


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    def override():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    from dsxlineage.main import app
    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _loan_estate(c):
    r = c.post("/api/estates", json={
        "name": "LOAN_GATES", "display_name": "Loan Gates",
        "source_type": "synthetic", "estate_type": "loan_onboarding",
    })
    assert r.status_code == 200, r.text
    assert r.json()["node_count"] >= 55
    return r.json()["id"]


def test_retail_to_adw_chain_closed():
    c = _client()
    eid = _loan_estate(c)
    g = c.get(f"/api/estates/{eid}/graph").json()
    out = {}
    for e in g["edges"]:
        out.setdefault(e["source"], []).append(e["target"])
    seen = {"RETAIL_CUSTOMER.CUSTOMER"}
    q = ["RETAIL_CUSTOMER.CUSTOMER"]
    while q:
        cur = q.pop(0)
        for nb in out.get(cur, []):
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    assert "DW_LOAN.CONTACT" in seen
    assert "PROC_CONTACT_LINK" in seen
    assert "DW_LOAN.LOANAPPLICATIONCONTACT" in seen
    assert "VW_LOAN_RISK_SUMMARY" in seen
    assert "ADW.LOAN_RISK_MART" in seen
    assert "ADW_RISK_MART_DASH" in seen
    # account orphan wired
    assert any(e["source"] == "RETAIL_CUSTOMER.ACCOUNT" for e in g["edges"])
    assert any(e["target"] == "ADW.BORROWER_MART" for e in g["edges"])


def test_p1_fqn_filtering():
    c = _client()
    eid = _loan_estate(c)
    # structure
    all_s = c.get(f"/api/estates/{eid}/structure/facts").json()
    filt = c.get(f"/api/estates/{eid}/structure/facts", params={"fqn": "RETAIL_CUSTOMER.CUSTOMER"}).json()
    assert len(filt["facts"]) >= 1
    assert len(filt["facts"]) <= len(all_s["facts"])
    assert all("RETAIL_CUSTOMER.CUSTOMER" in f["evidence"]["fqn"] or "CUSTOMER" in f["evidence"]["fqn"] for f in filt["facts"])
    # semantic: risk view has the RISK_BAND rule
    sem = c.get(f"/api/estates/{eid}/semantic/rules", params={"fqn": "VW_LOAN_RISK_SUMMARY"}).json()
    assert len(sem["rules"]) >= 1
    # behavior: risk proc has failure paths
    beh = c.get(f"/api/estates/{eid}/behavior/flows", params={"fqn": "PROC_RISK_ASSESSMENT"}).json()
    assert len(beh["failures"]) >= 1
    # ADW mart has dependency edges into JOB_ADW_MART_LOAD
    beh2 = c.get(f"/api/estates/{eid}/behavior/flows", params={"fqn": "JOB_ADW_MART_LOAD"}).json()
    assert len(beh2["dependencies"]) >= 1


def test_knowledge_graph_six_subgraphs():
    c = _client()
    eid = _loan_estate(c)
    kg = c.get(f"/api/estates/{eid}/knowledge-graph").json()
    assert kg["central"] == "Knowledge Graph"
    assert len(kg["subgraphs"]) == 6, kg["subgraphs"]
    names = {s["name"] for s in kg["subgraphs"]}
    assert names == {"Rules", "Dependencies", "Metadata", "Datasets", "Behavior", "Lineage"}
    # ?fqn= filtered variant
    kg2 = c.get(f"/api/estates/{eid}/knowledge-graph", params={"fqn": "DW_LOAN.CONTACT"}).json()
    assert len(kg2["subgraphs"]) == 6
    assert kg2["fqn"] == "DW_LOAN.CONTACT"


def test_multi_target_recommend():
    c = _client()
    eid = _loan_estate(c)
    for target in ["snowflake", "databricks", "aws", "microsoft", "other"]:
        r = c.post(f"/api/estates/{eid}/migration/recommend", json={"target": target})
        assert r.status_code == 200, (target, r.text)
        body = r.json()
        assert body["target"] == target
        assert body["pattern"]
        assert len(body["alternatives"]) == 4
        assert target not in body["alternatives"]
    for target in ["snowflake", "databricks", "aws", "microsoft", "other"]:
        r = c.post(f"/api/estates/{eid}/bridge/recommend", json={"target": target})
        assert r.status_code == 200, (target, r.text)
        assert r.json()["target_platform"] == target
    # invalid target falls back to snowflake
    r = c.post(f"/api/estates/{eid}/migration/recommend", json={"target": "nope"})
    assert r.json()["target"] == "snowflake"


def test_chat_review_queue_and_feedback():
    c = _client()
    eid = _loan_estate(c)
    # vague question with human_loop triggers reachable <0.6 path
    r = c.post(f"/api/estates/{eid}/chat", json={
        "question": "I'm uncertain about VW_LOAN_RISK_SUMMARY lineage, please review",
        "human_loop": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["confidence"] is not None and body["confidence"] < 0.6
    assert body["needs_review"] is True
    assert "human_review" in (body["tool_calls"] or [])
    mid = body["message_id"]
    # reviews queue lists it as pending
    rev = c.get(f"/api/estates/{eid}/chat/reviews").json()
    assert any(x["message_id"] == mid and x["status"] == "pending" for x in rev)
    # feedback decision persists + ledgers
    fb = c.post(f"/api/estates/{eid}/chat/feedback", json={
        "message_id": mid, "decision": "approve", "reviewer": "Praveen",
    })
    assert fb.status_code == 200
    rev2 = c.get(f"/api/estates/{eid}/chat/reviews").json()
    assert any(x["message_id"] == mid and x["decision"] == "approve" for x in rev2)
    # audit humanizes the new events
    audit = c.get(f"/api/estates/{eid}/audit").json()
    texts = " ".join(a.get("what", "") for a in audit)
    assert "Pending review" in texts or "Reviewed" in texts
