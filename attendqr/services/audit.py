"""A tamper-evident activity log.

Every activity log entry is sealed into a hash chain: its hash is an HMAC, keyed with a
key derived from SECRET_KEY, over the entry and the previous entry's hash. Changing or
deleting a sealed entry breaks the chain from that point, and without the key (which is
not in the database) nobody can compute a valid replacement. `flask verify-audit`
walks the chain.

- Entries are sealed right after the request that wrote them, in a short transaction that
  first locks the one-row audit_chain table, so concurrent requests extend the chain one at a
  time. The chain follows sealing order (chain_seq), not row ids.
- The two columns a foreign key may set to NULL on its own (the actor's account and the
  department, when those are deleted) are not hashed; the actor's username is.
- Removing the newest entries and rewinding audit_chain to match is undetectable from the
  database alone. Write the head (`flask verify-audit` prints it) somewhere else, for
  example with each backup, and compare.
"""
import hashlib
import hmac
import json
from dataclasses import dataclass, field

from flask import current_app, g, has_request_context
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..extensions import db
from ..models import ActivityLog, AuditChain

GENESIS = ''


def _key():
    return hmac.new(str(current_app.config['SECRET_KEY']).encode(), b'attendqr-audit-chain', hashlib.sha256).digest()


def entry_digest(previous_hash, seq, entry, key=None):
    payload = json.dumps([
        seq, entry.actor_username, entry.action, entry.target_type, entry.target_id, entry.description,
        entry.created_at.isoformat(timespec='microseconds') if entry.created_at else None,
    ], ensure_ascii=False, separators=(',', ':'))
    message = f'{previous_hash}\n{payload}'.encode()
    return hmac.new(key or _key(), message, hashlib.sha256).hexdigest()


# ── Sealing after each request ───────────────────────────────
# A request seals only entries it has committed, and only when it has nothing else
# flushed or pending: sealing commits, and must never commit a view's half-done work.
# Entries it skips are sealed by the next request that seals (all pending ones go together).

@event.listens_for(Session, 'after_flush')
def _after_flush(session, flush_context):
    if not has_request_context():
        return
    g.audit_uncommitted = True
    if any(isinstance(obj, ActivityLog) for obj in session.new):
        g.audit_written = True


@event.listens_for(Session, 'after_commit')
def _after_commit(session):
    if not has_request_context():
        return
    g.audit_uncommitted = False
    if g.pop('audit_written', False):
        g.audit_pending = True


@event.listens_for(Session, 'after_rollback')
def _after_rollback(session):
    if has_request_context():
        g.audit_uncommitted = False
        g.pop('audit_written', None)


def init_app(app):
    @app.after_request
    def seal_audit_log(response):
        session = db.session
        if g.pop('audit_pending', False) and not g.get('audit_uncommitted')                 and not (session.new or session.dirty or session.deleted):
            try:
                seal_pending()
            except SQLAlchemyError:
                session.rollback()
                app.logger.exception("Could not seal the activity log; the next request will try again.")
        return response


def seal_pending():
    """Seal every entry that has no hash yet, in id order. Returns how many were sealed. Commits."""
    head = db.session.get(AuditChain, 1)
    if head is None:
        return 0
    # Taking the row lock first serialises sealers (SQLite: the write lock; PostgreSQL: the row).
    db.session.execute(db.update(AuditChain).where(AuditChain.id == 1).values(last_seq=AuditChain.last_seq))
    db.session.refresh(head)
    pending = ActivityLog.query.filter(ActivityLog.entry_hash.is_(None)).order_by(ActivityLog.id).all()
    if not pending:
        db.session.commit()
        return 0
    key = _key()
    seq, previous = head.last_seq, head.last_hash
    for entry in pending:
        seq += 1
        previous = entry_digest(previous, seq, entry, key)
        entry.chain_seq, entry.entry_hash = seq, previous
    head.last_seq, head.last_hash = seq, previous
    db.session.commit()
    return len(pending)


@dataclass
class ChainCheck:
    entries: int = 0
    unsealed: int = 0
    head_seq: int = 0
    head_hash: str = ''
    problems: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.problems


def verify_chain(max_problems=20):
    """Recompute the whole chain. Unsealed entries are counted, not trusted."""
    check = ChainCheck()
    head = db.session.get(AuditChain, 1)
    check.head_seq, check.head_hash = (head.last_seq, head.last_hash) if head else (0, GENESIS)
    check.unsealed = ActivityLog.query.filter(ActivityLog.entry_hash.is_(None)).count()

    key = _key()
    expected_seq, previous = 0, GENESIS
    for entry in ActivityLog.query.filter(ActivityLog.chain_seq.isnot(None)).order_by(ActivityLog.chain_seq).yield_per(500):
        expected_seq += 1
        check.entries += 1
        if entry.chain_seq != expected_seq:
            check.problems.append(f"Entries {expected_seq} to {entry.chain_seq - 1} of the chain are missing (deleted).")
            expected_seq = entry.chain_seq
        if entry_digest(previous, entry.chain_seq, entry, key) != entry.entry_hash:
            check.problems.append(f"Entry {entry.chain_seq} (log id {entry.id}, '{entry.action}') was changed "
                                  "after it was sealed.")
        previous = entry.entry_hash or ''
        if len(check.problems) >= max_problems:
            break
    if check.ok and (expected_seq, previous) != (check.head_seq, check.head_hash):
        check.problems.append(f"The chain ends at entry {expected_seq} but the head records entry {check.head_seq}: "
                              "the newest entries were removed, or the head was changed.")
    return check
