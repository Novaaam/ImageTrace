"""Case management: validation, case IDs, folders, persistence and audit events."""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from imagetrace.audit import append_event
from imagetrace.db import Database, DatabaseError
from imagetrace.timefmt import utc_now_iso

log = logging.getLogger(__name__)

STATUSES = ("open", "archived")
CASE_ID_RE = re.compile(r"^C-\d{8}-[0-9A-F]{6}$")
CASE_SUBDIRS = ("originals", "derived", "reports", "metadata")
MAX_TITLE = 200
MAX_TEXT = 20_000
MAX_TAG = 40
MAX_TAGS = 20
SCOPE_FIELD = "Scope / authorization declaration"
DEFAULT_ACTOR = "Analyst"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_BREAK = re.compile(r"[\r\n\t]")


class ValidationError(ValueError):
    """User-supplied case data is invalid (message is safe to show to the user)."""


class CaseNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    notes: str
    scope_text: str
    status: str
    tags: tuple[str, ...]
    created_at: str
    updated_at: str

    @property
    def archived(self) -> bool:
        return self.status == "archived"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Case:
        return cls(
            row["id"],
            row["title"],
            row["notes"],
            row["scope_text"],
            row["status"],
            _load_tags(row["tags_json"]),
            row["created_at"],
            row["updated_at"],
        )


def _load_tags(raw: str) -> tuple[str, ...]:
    try:
        value = json.loads(raw)
    except ValueError:
        return ()
    return tuple(str(t) for t in value) if isinstance(value, list) else ()


def _dump_tags(tags: Iterable[str]) -> str:
    return json.dumps(list(tags), ensure_ascii=False)


# --- validation ---------------------------------------------------------------------------
def validate_title(title: str | None) -> str:
    t = (title or "").strip()
    if not t:
        raise ValidationError("Title is required.")
    if _CONTROL.search(t) or _LINE_BREAK.search(t):
        raise ValidationError("Title must be a single line without control characters.")
    if len(t) > MAX_TITLE:
        raise ValidationError(f"Title is too long (maximum {MAX_TITLE} characters).")
    return t


def validate_text(value: str | None, field: str, *, required: bool = False) -> str:
    v = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if required and not v:
        raise ValidationError(f"{field} is required.")
    if _CONTROL.search(v):
        raise ValidationError(f"{field} contains control characters.")
    if len(v) > MAX_TEXT:
        raise ValidationError(f"{field} is too long (maximum {MAX_TEXT} characters).")
    return v


def normalize_tags(tags: Iterable[str] | str | None) -> tuple[str, ...]:
    if tags is None:
        return ()
    items = tags.split(",") if isinstance(tags, str) else list(tags)
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        raw_s = str(raw)
        if _CONTROL.search(raw_s):
            raise ValidationError("Tags can't contain control characters.")
        tag = " ".join(raw_s.split())
        if not tag:
            continue
        if len(tag) > MAX_TAG:
            raise ValidationError(f"Tag is too long (maximum {MAX_TAG} characters): {tag[:20]}…")
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            out.append(tag)
    if len(out) > MAX_TAGS:
        raise ValidationError(f"Too many tags (maximum {MAX_TAGS}).")
    return tuple(out)


# --- ids and folders ----------------------------------------------------------------------
def new_case_id(today: date | None = None) -> str:
    """Generated ID with no personal data: C-<YYYYMMDD>-<6 random hex>."""
    d = today or date.today()
    return f"C-{d:%Y%m%d}-{secrets.token_hex(3).upper()}"


def case_dir(cases_root: Path, case_id: str) -> Path:
    """Folder of a case, guaranteed to be a direct child of *cases_root*."""
    if not CASE_ID_RE.fullmatch(case_id or ""):
        raise ValidationError(f"Invalid case ID: {case_id!r}")
    root = cases_root.resolve()
    path = (root / case_id).resolve()
    if path.parent != root:
        raise ValidationError("Case folder would be outside the cases root.")
    return path


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- repository ---------------------------------------------------------------------------
class CaseRepository:
    def __init__(
        self,
        db: Database,
        cases_root: Path,
        actor: Callable[[], str] = lambda: DEFAULT_ACTOR,
        id_factory: Callable[[], str] = new_case_id,
    ) -> None:
        self.db = db
        self.cases_root = cases_root
        self.actor = actor
        self.id_factory = id_factory

    # queries
    def get(self, case_id: str) -> Case | None:
        row = self.db.con.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return Case.from_row(row) if row else None

    def get_required(self, case_id: str) -> Case:
        case = self.get(case_id)
        if case is None:
            raise CaseNotFoundError(f"Case not found: {case_id}")
        return case

    def list(
        self, search: str = "", include_archived: bool = False, limit: int | None = None
    ) -> list[Case]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_archived:
            clauses.append("status = 'open'")
        text = (search or "").strip()
        if text:
            clauses.append(
                "(id LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' "
                "OR tags_json LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\')"
            )
            params += [_like_pattern(text)] * 4
        sql = "SELECT * FROM cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)  # fixed fragments only; values are bound
        sql += " ORDER BY updated_at DESC, id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [Case.from_row(r) for r in self.db.con.execute(sql, params)]

    def count(self, include_archived: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM cases"
        if not include_archived:
            sql += " WHERE status = 'open'"
        return int(self.db.con.execute(sql).fetchone()[0])

    def recent(self, limit: int = 5) -> list[Case]:
        return self.list(limit=limit)

    # commands
    def create(
        self,
        title: str,
        scope_text: str,
        notes: str = "",
        tags: Iterable[str] | str = (),
    ) -> Case:
        title = validate_title(title)
        scope = validate_text(scope_text, SCOPE_FIELD, required=True)
        notes = validate_text(notes, "Notes")
        tags_json = _dump_tags(normalize_tags(tags))
        now = utc_now_iso()
        for _attempt in range(5):
            case_id = self.id_factory()
            if not CASE_ID_RE.fullmatch(case_id):
                raise ValidationError(f"Generated case ID has an invalid format: {case_id!r}")
            with self.db.transaction() as con:
                if con.execute("SELECT 1 FROM cases WHERE id = ?", (case_id,)).fetchone():
                    continue  # extremely unlikely collision: try another ID
                con.execute(
                    "INSERT INTO cases (id, title, notes, scope_text, status, tags_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, 'open', ?, ?, ?)",
                    (case_id, title, notes, scope, tags_json, now, now),
                )
                append_event(
                    con,
                    "case_created",
                    case_id=case_id,
                    actor_label=self.actor(),
                    details={"status": "open"},
                )
            break
        else:
            raise DatabaseError("Could not generate a unique case ID.")
        self.ensure_case_dirs(case_id)
        log.info("Case created: %s", case_id)
        return self.get_required(case_id)

    def update(
        self,
        case_id: str,
        *,
        title: str | None = None,
        notes: str | None = None,
        scope_text: str | None = None,
        tags: Iterable[str] | str | None = None,
    ) -> Case:
        current = self.get_required(case_id)
        new: dict[str, str] = {}
        if title is not None:
            new["title"] = validate_title(title)
        if notes is not None:
            new["notes"] = validate_text(notes, "Notes")
        if scope_text is not None:
            new["scope_text"] = validate_text(scope_text, SCOPE_FIELD, required=True)
        if tags is not None:
            new["tags_json"] = _dump_tags(normalize_tags(tags))
        old = {
            "title": current.title,
            "notes": current.notes,
            "scope_text": current.scope_text,
            "tags_json": _dump_tags(current.tags),
        }
        changed = {k: v for k, v in new.items() if old[k] != v}
        if not changed:
            return current
        # Column names come from the fixed whitelist above; values are bound parameters.
        assignments = ", ".join(f"{col} = ?" for col in changed)
        sql = f"UPDATE cases SET {assignments}, updated_at = ? WHERE id = ?"  # noqa: S608
        with self.db.transaction() as con:
            con.execute(sql, (*changed.values(), utc_now_iso(), case_id))
            append_event(
                con,
                "case_updated",
                case_id=case_id,
                actor_label=self.actor(),
                # field names only: values may contain sensitive text
                details={"fields": sorted(k.removesuffix("_json") for k in changed)},
            )
        log.info("Case updated: %s (%s)", case_id, ", ".join(sorted(changed)))
        return self.get_required(case_id)

    def set_status(self, case_id: str, status: str) -> Case:
        if status not in STATUSES:
            raise ValidationError(f"Unknown status: {status!r}")
        current = self.get_required(case_id)
        if current.status == status:
            return current
        with self.db.transaction() as con:
            con.execute(
                "UPDATE cases SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now_iso(), case_id),
            )
            append_event(
                con,
                "case_archived" if status == "archived" else "case_reopened",
                case_id=case_id,
                actor_label=self.actor(),
                details={"from": current.status, "to": status},
            )
        log.info("Case %s status: %s -> %s", case_id, current.status, status)
        return self.get_required(case_id)

    def archive(self, case_id: str) -> Case:
        return self.set_status(case_id, "archived")

    def reopen(self, case_id: str) -> Case:
        return self.set_status(case_id, "open")

    def ensure_case_dirs(self, case_id: str) -> Path:
        base = case_dir(self.cases_root, case_id)
        for sub in CASE_SUBDIRS:
            (base / sub).mkdir(parents=True, exist_ok=True)
        return base
