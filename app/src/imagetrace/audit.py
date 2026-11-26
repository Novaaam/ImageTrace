"""Hash-chained audit (chain-of-custody) events.

Every event stores the SHA-256 of its own canonical content plus the previous event's hash.
SQLite triggers block UPDATE and DELETE on ``audit_events``.

LIMITATION: this is append-only *at the application level*. Anyone with write access to the
database file can drop the triggers and rewrite the chain. ``verify_chain`` detects edits to
individual events and broken links, but not a fully recomputed chain. It is not a tamper-proof
forensic log unless the database and its backups are externally secured and validated.

Details must not contain sensitive content (e.g. case titles, notes or OCR text). Record
field names, IDs and file names instead.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from imagetrace.timefmt import utc_now_iso

HASH_FIELDS = (
    "previous_event_hash",
    "case_id",
    "evidence_id",
    "event_type",
    "event_time",
    "actor_label",
    "details_json",
)


def compute_event_hash(fields: dict[str, Any]) -> str:
    payload = {k: fields.get(k) for k in HASH_FIELDS}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_event(
    con: sqlite3.Connection,
    event_type: str,
    *,
    actor_label: str,
    case_id: str | None = None,
    evidence_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    """Append one event. Must be called inside a write transaction (BEGIN IMMEDIATE)."""
    if not con.in_transaction:
        raise RuntimeError("append_event must run inside a transaction")
    row = con.execute("SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()
    fields = {
        "previous_event_hash": row[0] if row else None,
        "case_id": case_id,
        "evidence_id": evidence_id,
        "event_type": event_type,
        "event_time": utc_now_iso(),
        "actor_label": actor_label,
        "details_json": json.dumps(details or {}, sort_keys=True, ensure_ascii=False),
    }
    event_hash = compute_event_hash(fields)
    con.execute(
        "INSERT INTO audit_events (case_id, evidence_id, event_type, event_time, actor_label, "
        "details_json, previous_event_hash, event_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            fields["case_id"],
            fields["evidence_id"],
            fields["event_type"],
            fields["event_time"],
            fields["actor_label"],
            fields["details_json"],
            fields["previous_event_hash"],
            event_hash,
        ),
    )
    return event_hash


@dataclass(frozen=True)
class AuditEvent:
    id: int
    case_id: str | None
    evidence_id: str | None
    event_type: str
    event_time: str
    actor_label: str
    details: dict[str, Any]
    previous_event_hash: str | None
    event_hash: str


def _row_to_event(row: sqlite3.Row) -> AuditEvent:
    try:
        details = json.loads(row["details_json"])
    except ValueError:
        details = {"_unparsed": row["details_json"]}
    return AuditEvent(
        row["id"],
        row["case_id"],
        row["evidence_id"],
        row["event_type"],
        row["event_time"],
        row["actor_label"],
        details if isinstance(details, dict) else {"_value": details},
        row["previous_event_hash"],
        row["event_hash"],
    )


def list_events(
    con: sqlite3.Connection,
    case_id: str | None = None,
    limit: int | None = None,
    *,
    evidence_id: str | None = None,
) -> list[AuditEvent]:
    sql = "SELECT * FROM audit_events"
    clauses: list[str] = []
    params: list[Any] = []
    if case_id is not None:
        clauses.append("case_id = ?")
        params.append(case_id)
    if evidence_id is not None:
        clauses.append("evidence_id = ?")
        params.append(evidence_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [_row_to_event(r) for r in con.execute(sql, params)]


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    checked: int
    message: str
    first_bad_id: int | None = None


def verify_chain(con: sqlite3.Connection) -> ChainReport:
    prev: str | None = None
    count = 0
    for row in con.execute("SELECT * FROM audit_events ORDER BY id"):
        count += 1
        fields = {k: row[k] for k in HASH_FIELDS}
        if row["previous_event_hash"] != prev:
            return ChainReport(False, count, f"Broken link at event {row['id']}", row["id"])
        if compute_event_hash(fields) != row["event_hash"]:
            return ChainReport(
                False, count, f"Content hash mismatch at event {row['id']}", row["id"]
            )
        prev = row["event_hash"]
    return ChainReport(True, count, f"Audit chain consistent ({count} events)")
