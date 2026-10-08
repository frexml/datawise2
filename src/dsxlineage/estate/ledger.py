"""
Evidence Ledger - append-only, hash-chained.

Every estate state transition is recorded as a LedgerEvent. Verification
is via hash chain: event_hash = sha256(prev_hash + canonical_payload_hash).

Hard gate: POST /estates/{id}/promote checks ledger for required events.
P0 fix: FOR UPDATE + actor_id/email_hash + canonical_payload_hash.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.orm import Session

from dsxlineage.estate.models import LedgerEvent

GENESIS_HASH = "GENESIS"


def _hash_email(email: str | None) -> str | None:
    if not email:
        return None
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def append_ledger_event(
    db: Session,
    estate_id: int,
    event_type: str,
    payload: dict,
    actor: str = "system",
    actor_id: str | None = None,
    actor_email: str | None = None,
    idp_verified: bool = False,
) -> LedgerEvent:
    """Append a single ledger event, computing prev_hash + event_hash.

    Uses SELECT ... FOR UPDATE where the dialect supports it (Postgres) to
    prevent concurrent append races. SQLite falls back to plain SELECT.
    Actor is stored as display name + actor_id (UUID) + email hash for audit.
    """
    # Use FOR UPDATE on Postgres; SQLite will ignore it (or we catch)
    query = db.query(LedgerEvent).filter(LedgerEvent.estate_id == estate_id).order_by(LedgerEvent.id.desc())
    # Only use FOR UPDATE if dialect supports it (not SQLite)
    bind = db.get_bind()
    dialect = bind.dialect.name if bind else ""
    if dialect != "sqlite":
        try:
            query = query.with_for_update()
        except Exception:
            pass
    last = query.first()
    prev_hash = last.event_hash if last else GENESIS_HASH
    event_hash = LedgerEvent.compute_hash(prev_hash, payload)
    canonical_hash = LedgerEvent.compute_canonical_hash(payload)
    # Mock IdP verification for POC: if actor_email provided, generate actor_id
    if actor_email and not actor_id:
        actor_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, actor_email.lower()))
        idp_verified = True
    elif actor != "system" and not actor_id:
        # Mock: generate deterministic UUID from actor name for POC
        actor_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, actor.lower()))
    ev = LedgerEvent(
        estate_id=estate_id,
        event_type=event_type,
        actor=actor,
        actor_id=actor_id,
        actor_email_hash=_hash_email(actor_email),
        idp_verified=idp_verified,
        payload=payload,
        canonical_payload_hash=canonical_hash,
        prev_hash=prev_hash,
        event_hash=event_hash,
    )
    db.add(ev)
    db.flush()
    return ev


def verify_ledger_chain(db: Session, estate_id: int) -> tuple[bool, str]:
    """Verify hash chain integrity. Returns (ok, reason). Checks canonical hash too."""
    events = db.query(LedgerEvent).filter(LedgerEvent.estate_id == estate_id).order_by(LedgerEvent.id.asc()).all()
    prev = GENESIS_HASH
    for ev in events:
        expected = LedgerEvent.compute_hash(prev, ev.payload)
        if expected != ev.event_hash:
            return False, f"Event {ev.id} hash mismatch: expected {expected}, got {ev.event_hash}"
        if ev.prev_hash != prev:
            return False, f"Event {ev.id} prev_hash mismatch"
        # Also verify canonical_payload_hash if stored
        if ev.canonical_payload_hash:
            expected_canonical = LedgerEvent.compute_canonical_hash(ev.payload)
            if expected_canonical != ev.canonical_payload_hash:
                return False, f"Event {ev.id} canonical hash mismatch"
        prev = ev.event_hash
    return True, "ok"


def ledger_has(db: Session, estate_id: int, event_type: str, predicate=None) -> bool:
    q = db.query(LedgerEvent).filter(LedgerEvent.estate_id == estate_id, LedgerEvent.event_type == event_type)
    events = q.all()
    if not events:
        return False
    if predicate is None:
        return True
    return any(predicate(ev.payload) for ev in events)


def can_promote(db: Session, estate_id: int) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if not ledger_has(db, estate_id, "diff_run", lambda p: p.get("status") == "passed"):
        missing.append("diff_run.passed")
    if not ledger_has(db, estate_id, "continuity_checked", lambda p: p.get("passed") is True):
        missing.append("continuity_checked.passed")
    if not ledger_has(db, estate_id, "plan_approved"):
        missing.append("plan_approved")
    return (len(missing) == 0, missing)
