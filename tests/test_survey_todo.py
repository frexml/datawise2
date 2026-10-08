"""Survey Todo + live progress + Multi-estate (banking vs telecom)"""

from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from dsxlineage.db.database import Base, get_db
import dsxlineage.estate.models
import dsxlineage.db.models

def _client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
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
    c = TestClient(app)
    return c, engine

def test_survey_todo_banking():
    client, _ = _client()
    r = client.post('/api/estates', json={'name':'BANK_SURVEY','display_name':'Bank Survey','source_type':'synthetic','estate_type':'banking'})
    assert r.status_code == 200
    bid = r.json()['id']
    # Check initial todo
    r = client.get(f'/api/estates/{bid}/survey/todo')
    assert r.status_code == 200
    todo = r.json()['todo']
    assert len(todo) == 8
    assert all(t['status'] == 'done' for t in todo)  # auto-surveyed on create (P0 backward compat)
    assert r.json()['current_stage'] == 'done'

def test_survey_todo_telecom():
    client, _ = _client()
    r = client.post('/api/estates', json={'name':'TELCO_SURVEY','display_name':'Telco Survey','source_type':'synthetic','estate_type':'telecom'})
    assert r.status_code == 200
    assert r.json()['estate_type'] == 'telecom'
    assert r.json()['node_count'] == 35  # telco is smaller
    tid = r.json()['id']
    r = client.get(f'/api/estates/{tid}/survey/todo')
    todo = r.json()['todo']
    assert len(todo) == 8
    # Telecom should have fewer tables etc
    assert r.json()['current_stage'] == 'done'

def test_test_connection_and_survey_flow():
    client, _ = _client()
    r = client.post('/api/estates', json={'name':'CONN_TEST','display_name':'Conn Test','source_type':'synthetic','estate_type':'banking'})
    eid = r.json()['id']
    # Test connection
    r = client.post(f'/api/estates/{eid}/test-connection', json={'host':'oracle-prod.bank.local','port':1521,'user':'ESTATE_RO','db_type':'oracle'})
    assert r.status_code == 200
    assert r.json()['status'] == 'connected'
    assert 'latency_ms' in r.json()['connection']
    # Survey — first creates pending_approval Todo
    r = client.post(f'/api/estates/{eid}/survey', json={})
    assert r.status_code == 200
    # If already done via auto, it may be done; otherwise pending_approval
    assert r.json()['status'] in ('pending_approval', 'done', 'surveying')
    if r.json()['status'] == 'pending_approval':
        assert len(r.json()['todo']) == 8
        assert all(t['status'] == 'pending' for t in r.json()['todo'])
        # Approve and wait for done
        r2 = client.post(f'/api/estates/{eid}/survey/approve', json={})
        assert r2.status_code == 200
        # Poll until done (with timeout)
        import time
        for _ in range(15):
            time.sleep(1)
            r3 = client.get(f'/api/estates/{eid}/survey/todo')
            if r3.json()['status'] == 'ACTIVE' and r3.json()['current_stage'] == 'done':
                break
        r = client.get(f'/api/estates/{eid}/survey/todo')
        assert r.json()['status'] == 'ACTIVE'
        assert r.json()['current_stage'] == 'done'
    else:
        assert len(r.json()['todo']) == 8

def test_multi_estate_allowance():
    client, _ = _client()
    r1 = client.post('/api/estates', json={'name':'BANK_M1','display_name':'Bank M1','source_type':'synthetic','estate_type':'banking'})
    r2 = client.post('/api/estates', json={'name':'TELCO_M1','display_name':'Telco M1','source_type':'synthetic','estate_type':'telecom'})
    assert r1.json()['estate_type'] == 'banking'
    assert r2.json()['estate_type'] == 'telecom'
    assert r1.json()['node_count'] == 126
    assert r2.json()['node_count'] == 35
    # List should contain both with correct types
    r = client.get('/api/estates')
    names = {e['name']: e['estate_type'] for e in r.json()}
    assert names['BANK_M1'] == 'banking'
    assert names['TELCO_M1'] == 'telecom'
    # Analytics should be distinct
    id_bank = r1.json()['id']
    id_telco = r2.json()['id']
    r_bank = client.get(f"/api/estates/{id_bank}/analytics")
    r_telco = client.get(f"/api/estates/{id_telco}/analytics")
    counts = {r_bank.json()['total_nodes'], r_telco.json()['total_nodes']}
    assert 126 in counts
    assert 35 in counts

def test_connection_failure_mock():
    client, _ = _client()
    r = client.post('/api/estates', json={'name':'FAIL_TEST','display_name':'Fail','source_type':'synthetic','estate_type':'banking'})
    eid = r.json()['id']
    r = client.post(f'/api/estates/{eid}/test-connection', json={'host':'fail-host','port':1521,'user':'ESTATE_RO','db_type':'oracle'})
    assert r.status_code == 502
