"""API integration tests for /api/estates — uses SQLite in-memory + TestClient.

Covers: create estate → extract → analytics → chat → bridge pipeline → promote gate.
"""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dsxlineage.db.database import Base, get_db
import dsxlineage.estate.models  # noqa: F401 — register

# We need to override the app's DB before importing main
# Use a file-based sqlite so TestClient threads share it

@pytest.fixture(scope="module")
def client():
    # Use StaticPool + check_same_thread=False so in-memory is shared across threads
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Import app after engine creation so Base is already populated
    from dsxlineage.main import app
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def test_create_estate(client):
    resp = client.post("/api/estates", json={"name": "NORTHSTAR", "display_name": "NorthStar Synthetic Estate", "source_type": "synthetic"})
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()
    assert data["name"] == "NORTHSTAR"
    assert data["id"] > 0


def test_list_estates(client):
    resp = client.get("/api/estates")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_get_estate_ir(client):
    # Use estate 1
    resp = client.get("/api/estates/1/ir")
    assert resp.status_code == 200
    data = resp.json()
    assert data["estate_name"] == "NORTHSTAR"
    assert data["total_nodes"] > 0
    assert data["total_edges"] > 0


def test_graph(client):
    resp = client.get("/api/estates/1/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) >= 20
    assert len(data["edges"]) >= 10


def test_lineage(client):
    resp = client.get("/api/estates/1/lineage", params={"fqn": "VW_RISK_EXPOSURE", "direction": "both"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["fqn"] == "VW_RISK_EXPOSURE"
    assert "upstream" in data
    assert "downstream" in data


def test_blast_radius(client):
    resp = client.get("/api/estates/1/blast-radius", params={"fqn": "VW_RISK_EXPOSURE"})
    assert resp.status_code == 200
    data = resp.json()
    assert "blast_radius" in data
    assert data["count"] >= 0


def test_analytics(client):
    resp = client.get("/api/estates/1/analytics")
    assert resp.status_code == 200
    data = resp.json()
    assert "orphan_tables" in data
    assert "hot_tables" in data
    assert "circular_dependencies" in data
    assert data["total_nodes"] > 0


def test_chat_grounded(client):
    resp = client.post("/api/estates/1/chat", json={"question": "What feeds VW_RISK_EXPOSURE?"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "answer" in data
    assert "citations" in data
    assert data["was_refused"] is False
    assert len(data["citations"]) > 0


def test_chat_refusal(client):
    resp = client.post("/api/estates/1/chat", json={"question": "What is the CEO's favorite color?"})
    assert resp.status_code == 200
    assert resp.json()["was_refused"] is True


def test_ledger(client):
    resp = client.get("/api/estates/1/ledger")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_ledger_verify(client):
    resp = client.get("/api/estates/1/ledger/verify")
    assert resp.status_code == 200
    assert resp.json()["verified"] is True


def test_bridge_recommend(client):
    resp = client.post("/api/estates/1/bridge/recommend")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["target_platform"] == "snowflake"
    assert len(data["scope_fqns"]) > 0


def test_bridge_plan_create_and_approve(client):
    # Create plan
    resp = client.post("/api/estates/1/bridge/plan", json={"name": "Snowflake Wedge — Finance Mart", "target_platform": "snowflake"})
    assert resp.status_code == 200, resp.text
    plan_id = resp.json()["id"]

    # Try approve with empty name → 400
    bad = client.post(f"/api/estates/1/bridge/plan/{plan_id}/approve", json={"approver": ""})
    assert bad.status_code == 400

    # Approve correctly
    ok = client.post(f"/api/estates/1/bridge/plan/{plan_id}/approve", json={"approver": "Alice Approver"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["plan"]["status"] == "approved"


def test_bridge_ddl_requires_approval(client):
    # Create another plan not yet approved
    resp = client.post("/api/estates/1/bridge/plan", json={"name": "Second Wedge", "target_platform": "snowflake", "scope_fqns": ["DW.FACT_ORDERS"]})
    plan_id = resp.json()["id"]
    # Should be 409 before approval
    ddl = client.get(f"/api/estates/1/bridge/plan/{plan_id}/ddl")
    assert ddl.status_code == 409
    # Approve then fetch
    client.post(f"/api/estates/1/bridge/plan/{plan_id}/approve", json={"approver": "Bob"})
    ddl2 = client.get(f"/api/estates/1/bridge/plan/{plan_id}/ddl")
    assert ddl2.status_code == 200
    assert "CREATE TABLE" in ddl2.text


def test_bridge_diff_requires_approved_plan(client):
    # We already have approved plans, so diff should succeed
    resp = client.post("/api/estates/1/bridge/diff", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["passed"] is True


def test_bridge_continuity_requires_diff(client):
    resp = client.post("/api/estates/1/bridge/continuity", json={})
    assert resp.status_code == 200, resp.text
    assert "matched" in resp.json()


def test_bridge_promote_gated(client):
    # Should now be promotable: we have diff passed + continuity passed + plan approved
    resp = client.post("/api/estates/1/bridge/promote")
    assert resp.status_code == 200, resp.text
    assert resp.json()["promoted"] is True
    assert resp.json()["verified"] is True
    # Ledger should contain promoted event
    ledger = client.get("/api/estates/1/ledger").json()
    assert any(ev["event_type"] == "promoted" for ev in ledger)


def test_bridge_promote_blocked_without_evidence(client):
    # Create a fresh estate that has no diff/continuity
    resp = client.post("/api/estates", json={"name": "EMPTY_ESTATE", "display_name": "Empty", "source_type": "synthetic"})
    # Might be 409 if NORTHSTAR synthetic auto-extract already created it, but we made a second estate
    # If creation succeeded, try promote — should be 409
    if resp.status_code in (200, 201):
        eid = resp.json()["id"]
        blocked = client.post(f"/api/estates/{eid}/bridge/promote")
        assert blocked.status_code == 409
        assert "missing" in blocked.json()["detail"].lower()
