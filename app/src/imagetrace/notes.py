"""Analyst notes, visual-clue observations and append-only revision history (Phase 7).

Rules (blueprint sections 4, 5E, 7, 11):
- Every note is attributable (``author_label``), timestamped (UTC ISO-8601), categorized, and
  tagged with one of the four blueprint confidence labels:
  ``Confirmed from file``, ``Corroborated``, ``Analyst observation``, ``Unverified hypothesis``.
- Conclusions (``Confirmed from file`` and ``Corroborated``) require a non-empty source/citation
  reference.
- Append-only at the application level: notes are never deleted (enforced by a SQLite trigger)
  and never silently overwritten. Revising a note marks the prior row ``superseded`` and inserts
  a linked new revision; retracting marks a note ``retracted`` while keeping its record intact.
- Privacy: raw note text and source citations are never logged to ``imagetrace.log`` or
  ``audit_events.details_json`` (only note ID, category, confidence label and status).
- Limitation: application-level append-only rules are not tamper-proof against direct edits to
  the SQLite file unless externally secured and validated.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from imagetrace.audit import append_event
from imagetrace.cases import CaseNotFoundError, ValidationError
from imagetrace.db import Database
from imagetrace.evidence import EvidenceNotFoundError
from imagetrace.timefmt import utc_now_iso

log = logging.getLogger(__name__)

CONFIDENCE_LABELS: tuple[str, ...] = (
    "Confirmed from file",
    "Corroborated",
    "Analyst observation",
    "Unverified hypothesis",
)

SOURCE_REQUIRED_LABELS = frozenset({"Confirmed from file", "Corroborated"})

NOTE_CATEGORIES: tuple[str, ...] = (
    "Visible landmark",
    "Signage / visible text",
    "Environmental detail",
    "Object / structure",
    "Metadata / technical finding",
    "General observation",
)

NOTE_STATUSES = ("active", "superseded", "retracted")
MAX_NOTE_TEXT = 10_000
MAX_SOURCE_REF = 1_000

ANALYST_NOTES_BANNER = (
    "Analyst visual-clue workspace: record observable landmarks, signage, environmental details "
    "or technical findings with a confidence label and source citation. Do not guess private "
    "individuals' identities or residential addresses, and do not promote hypotheses to facts."
)

APPEND_ONLY_LIMITATION = (
    "Notes and audit events are append-only within ImageTrace (revisions supersede earlier "
    "entries rather than deleting them). This is an application-level integrity control, not a "
    "tamper-proof forensic log unless the database and backups are externally secured and "
    "validated."
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class NoteNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class AnalystNote:
    id: int
    case_id: str
    evidence_id: str | None
    note_text: str
    category: str
    confidence_label: str
    source_reference: str
    author_label: str
    status: str
    supersedes_note_id: int | None
    created_at: str
    updated_at: str

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AnalystNote:
        return cls(
            id=int(row["id"]),
            case_id=row["case_id"],
            evidence_id=row["evidence_id"],
            note_text=row["note_text"],
            category=row["category"],
            confidence_label=row["confidence_label"],
            source_reference=row["source_reference"],
            author_label=row["author_label"],
            status=row["status"],
            supersedes_note_id=(
                int(row["supersedes_note_id"]) if row["supersedes_note_id"] is not None else None
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def validate_note_inputs(
    note_text: str | None,
    category: str | None,
    confidence_label: str | None,
    source_reference: str | None,
) -> tuple[str, str, str, str]:
    text = (note_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValidationError("Observation / note text is required.")
    if _CONTROL.search(text):
        raise ValidationError("Note text contains control characters.")
    if len(text) > MAX_NOTE_TEXT:
        raise ValidationError(f"Note text is too long (maximum {MAX_NOTE_TEXT} characters).")

    cat = (category or "").strip()
    if cat not in NOTE_CATEGORIES:
        raise ValidationError(f"Invalid note category: {cat!r}")

    conf = (confidence_label or "").strip()
    if conf not in CONFIDENCE_LABELS:
        raise ValidationError(f"Invalid confidence label: {conf!r}")

    src = (source_reference or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if _CONTROL.search(src):
        raise ValidationError("Source / citation reference contains control characters.")
    if len(src) > MAX_SOURCE_REF:
        raise ValidationError(
            f"Source / citation reference is too long (maximum {MAX_SOURCE_REF} characters)."
        )
    if conf in SOURCE_REQUIRED_LABELS and not src:
        raise ValidationError(
            f"A source / citation reference is required for '{conf}' notes so conclusions stay "
            "grounded in verifiable observations."
        )
    return text, cat, conf, src


class NotesRepository:
    def __init__(
        self,
        db: Database,
        actor: Callable[[], str] = lambda: "Analyst",
    ) -> None:
        self.db = db
        self.actor = actor

    def _verify_targets(self, case_id: str, evidence_id: str | None) -> None:
        case_row = self.db.con.execute(
            "SELECT status FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
        if case_row is None:
            raise CaseNotFoundError(f"Case not found: {case_id}")
        if case_row["status"] == "archived":
            raise ValidationError(
                "Cannot add or modify notes on an archived case. Reopen the case first."
            )
        if evidence_id is not None:
            ev_row = self.db.con.execute(
                "SELECT case_id FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
            if ev_row is None:
                raise EvidenceNotFoundError(f"Evidence not found: {evidence_id}")
            if ev_row["case_id"] != case_id:
                raise ValidationError("Evidence item does not belong to the specified case.")

    def get(self, note_id: int) -> AnalystNote | None:
        row = self.db.con.execute("SELECT * FROM notes WHERE id = ?", (int(note_id),)).fetchone()
        return AnalystNote.from_row(row) if row else None

    def get_required(self, note_id: int) -> AnalystNote:
        note = self.get(note_id)
        if note is None:
            raise NoteNotFoundError(f"Note not found: {note_id}")
        return note

    def list_for_evidence(
        self, evidence_id: str, *, include_history: bool = False
    ) -> list[AnalystNote]:
        if include_history:
            rows = self.db.con.execute(
                "SELECT * FROM notes WHERE evidence_id = ? ORDER BY id DESC", (evidence_id,)
            )
        else:
            rows = self.db.con.execute(
                "SELECT * FROM notes WHERE evidence_id = ? AND status = 'active' ORDER BY id DESC",
                (evidence_id,),
            )
        return [AnalystNote.from_row(r) for r in rows]

    def list_for_case(self, case_id: str, *, include_history: bool = False) -> list[AnalystNote]:
        if include_history:
            rows = self.db.con.execute(
                "SELECT * FROM notes WHERE case_id = ? ORDER BY id DESC", (case_id,)
            )
        else:
            rows = self.db.con.execute(
                "SELECT * FROM notes WHERE case_id = ? AND status = 'active' ORDER BY id DESC",
                (case_id,),
            )
        return [AnalystNote.from_row(r) for r in rows]

    def count_for_case(self, case_id: str) -> int:
        return int(
            self.db.con.execute(
                "SELECT COUNT(*) FROM notes WHERE case_id = ? AND status = 'active'", (case_id,)
            ).fetchone()[0]
        )

    def counts_by_confidence(self, case_id: str) -> dict[str, int]:
        counts = dict.fromkeys(CONFIDENCE_LABELS, 0)
        rows = self.db.con.execute(
            "SELECT confidence_label, COUNT(*) FROM notes "
            "WHERE case_id = ? AND status = 'active' GROUP BY confidence_label",
            (case_id,),
        )
        for lbl, cnt in rows:
            if lbl in counts:
                counts[lbl] = int(cnt)
        return counts

    def create(
        self,
        case_id: str,
        evidence_id: str | None,
        *,
        note_text: str,
        category: str,
        confidence_label: str,
        source_reference: str = "",
    ) -> AnalystNote:
        self._verify_targets(case_id, evidence_id)
        text, cat, conf, src = validate_note_inputs(
            note_text, category, confidence_label, source_reference
        )
        author = (self.actor() or "Analyst").strip() or "Analyst"
        now = utc_now_iso()
        with self.db.transaction() as con:
            cur = con.execute(
                "INSERT INTO notes ("
                "case_id, evidence_id, note_text, category, confidence_label, source_reference, "
                "author_label, status, supersedes_note_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?)",
                (case_id, evidence_id, text, cat, conf, src, author, now, now),
            )
            note_id = int(cur.lastrowid)
            # Privacy: log IDs, category and confidence only - never raw note text!
            append_event(
                con,
                "note_created",
                case_id=case_id,
                evidence_id=evidence_id,
                actor_label=author,
                details={
                    "note_id": note_id,
                    "category": cat,
                    "confidence_label": conf,
                    "has_source_reference": bool(src),
                },
            )
        log.info("Analyst note created: #%d (case=%s, conf=%s)", note_id, case_id, conf)
        return self.get_required(note_id)

    def revise(
        self,
        note_id: int,
        *,
        note_text: str,
        category: str,
        confidence_label: str,
        source_reference: str = "",
    ) -> AnalystNote:
        """Append a new revision and mark the previous entry 'superseded' (append-only)."""
        old = self.get_required(note_id)
        if not old.is_active:
            raise ValidationError(
                f"Only active notes can be revised (note #{old.id} is {old.status})."
            )
        self._verify_targets(old.case_id, old.evidence_id)
        text, cat, conf, src = validate_note_inputs(
            note_text, category, confidence_label, source_reference
        )
        if (
            text == old.note_text
            and cat == old.category
            and conf == old.confidence_label
            and src == old.source_reference
        ):
            return old

        author = (self.actor() or "Analyst").strip() or "Analyst"
        now = utc_now_iso()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE notes SET status = 'superseded', updated_at = ? WHERE id = ?",
                (now, old.id),
            )
            cur = con.execute(
                "INSERT INTO notes ("
                "case_id, evidence_id, note_text, category, confidence_label, source_reference, "
                "author_label, status, supersedes_note_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (old.case_id, old.evidence_id, text, cat, conf, src, author, old.id, now, now),
            )
            new_id = int(cur.lastrowid)
            append_event(
                con,
                "note_revised",
                case_id=old.case_id,
                evidence_id=old.evidence_id,
                actor_label=author,
                details={
                    "previous_note_id": old.id,
                    "new_note_id": new_id,
                    "category": cat,
                    "confidence_label": conf,
                    "has_source_reference": bool(src),
                },
            )
        log.info("Analyst note revised: #%d -> #%d (case=%s)", old.id, new_id, old.case_id)
        return self.get_required(new_id)

    def retract(self, note_id: int) -> AnalystNote:
        """Mark a note 'retracted' without deleting it (preserving full audit history)."""
        old = self.get_required(note_id)
        if not old.is_active:
            return old
        self._verify_targets(old.case_id, old.evidence_id)
        author = (self.actor() or "Analyst").strip() or "Analyst"
        now = utc_now_iso()
        with self.db.transaction() as con:
            con.execute(
                "UPDATE notes SET status = 'retracted', updated_at = ? WHERE id = ?",
                (now, old.id),
            )
            append_event(
                con,
                "note_retracted",
                case_id=old.case_id,
                evidence_id=old.evidence_id,
                actor_label=author,
                details={"note_id": old.id},
            )
        log.info("Analyst note retracted: #%d (case=%s)", old.id, old.case_id)
        return self.get_required(old.id)
