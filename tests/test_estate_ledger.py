"""Ledger tests — append-only, hash-chained, gate checks."""

import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dsxlineage.db.database import Base
import dsxlineage.estate.models  # noqa: F401 — register models
from dsxlineage.estate.ledger import append_ledger_event, can_promote, verify_ledger_chain
from dsxlineage.estate.models import Estate, LedgerEvent


def _fresh_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session


def test_ledger_append_and_verify():
    Session = _fresh_db()
    db = Session()
    estate = Estate(name="TEST_LEDGER", display_name="Test")
    db.add(estate)
    db.commit()
    db.refresh(estate)

    ev1 = append_ledger_event(db, estate.id, "ir_generated", {"version": "1.0.0"})
    ev2 = append_ledger_event(db, estate.id, "plan_created", {"plan_id": 1})
    ev3 = append_ledger_event(db, estate.id, "plan_approved", {"plan_id": 1, "approver": "Alice"})
    db.commit()

    assert ev1.prev_hash == "GENESIS"
    assert ev2.prev_hash == ev1.event_hash
    assert ev3.prev_hash == ev2.event_hash

    ok, reason = verify_ledger_chain(db, estate.id)
    assert ok, reason
    db.close()


def test_ledger_tamper_detection():
    Session = _fresh_db()
    db = Session()
    estate = Estate(name="TEST_TAMPER", display_name="Test")
    db.add(estate)
    db.commit()
    db.refresh(estate)

    ev1 = append_ledger_event(db, estate.id, "ir_generated", {"version": "1.0.0"})
    ev2 = append_ledger_event(db, estate.id, "plan_created", {"plan_id": 1})
    db.commit()

    # Tamper
    ev2.payload = {"plan_id": 999}
    db.commit()

    ok, reason = verify_ledger_chain(db, estate.id)
    assert not ok
    assert "mismatch" in reason.lower()
    db.close()


def test_can_promote_gate():
    Session = _fresh_db()
    db = Session()
    estate = Estate(name="TEST_GATE", display_name="Test")
    db.add(estate)
    db.commit()
    db.refresh(estate)

    ok, missing = can_promote(db, estate.id)
    assert not ok
    assert "diff_run.passed" in missing

    append_ledger_event(db, estate.id, "diff_run", {"status": "passed"})
    append_ledger_event(db, estate.id, "continuity_checked", {"passed": True})
    append_ledger_event(db, estate.id, "plan_approved", {"plan_id": 1})
    db.commit()

    ok, missing = can_promote(db, estate.id)
    assert ok, f"Should be promotable, missing: {missing}"
    db.close()


def test_ledger_hash_is_deterministic():
    Session = _fresh_db()
    db = Session()
    estate = Estate(name="TEST_HASH", display_name="Test")
    db.add(estate)
    db.commit()
    db.refresh(estate)

    payload = {"plan_id": 1, "approver": "Bob"}
    h1 = LedgerEvent.compute_hash("GENESIS", payload)
    h2 = LedgerEvent.compute_hash("GENESIS", payload)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex
    db.close()
