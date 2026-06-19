"""Action Notes workspace API (GDPdU)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import User, current_user
from app.db import get_session
from app.services.action_notes.llm_actions import draft_email, generate_board

router = APIRouter(prefix="/api/v1/action-notes", tags=["action-notes"])

_UserDep = Annotated[User, Depends(current_user)]
_SessionDep = Annotated[Session, Depends(get_session)]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SessionCreate(BaseModel):
    author_scope: str
    title: str
    route: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)


class NoteCreate(BaseModel):
    body: str = ""
    is_done: bool = False


class NotePatch(BaseModel):
    body: Optional[str] = None
    is_done: Optional[bool] = None
    sort_order: Optional[int] = None


class PinCreate(BaseModel):
    label: Optional[str] = None
    snapshot: dict[str, Any]


class DraftEmailRequest(BaseModel):
    contact_id: str
    note_ids: list[str] = Field(default_factory=list)
    pin_ids: list[str] = Field(default_factory=list)
    user_name: str
    user_email: str
    language: str = "en"
    app_base_url: Optional[str] = None
    simulate_reply: bool = False


class GenerateBoardRequest(BaseModel):
    note_ids: list[str] = Field(default_factory=list)
    pin_ids: list[str] = Field(default_factory=list)


def _schema_err(exc: Exception) -> HTTPException:
    msg = str(exc).lower()
    if "does not exist" in msg or "undefinedtable" in msg:
        return HTTPException(
            503,
            detail="Action Notes not initialized — run: alembic upgrade head",
        )
    return HTTPException(500, detail=str(exc))


def _row_dict(row) -> dict:
    return dict(row._mapping) if row else {}


@router.get("/sessions")
def list_sessions(
    _user: _UserDep,
    session: _SessionDep,
    author_scope: str = Query(...),
):
    try:
        rows = session.execute(
            text(
                """
                SELECT session_id, author_scope, title, route, filters_json, status,
                       created_at, updated_at
                FROM app_note_session
                WHERE author_scope = :scope AND status = 'open'
                ORDER BY updated_at DESC
                LIMIT 50
                """
            ),
            {"scope": author_scope},
        ).fetchall()
        out = [_row_dict(r) for r in rows]
        session.commit()
        return {"sessions": out}
    except Exception as exc:
        session.rollback()
        raise _schema_err(exc) from exc


@router.post("/sessions")
def create_session(_user: _UserDep, session: _SessionDep, body: SessionCreate):
    sid = _new_id("sess")
    try:
        session.execute(
            text(
                """
                INSERT INTO app_note_session (session_id, author_scope, title, route, filters_json)
                VALUES (:sid, :scope, :title, :route, CAST(:filters AS jsonb))
                """
            ),
            {
                "sid": sid,
                "scope": body.author_scope,
                "title": body.title,
                "route": body.route,
                "filters": json.dumps(body.filters),
            },
        )
        row = session.execute(
            text(
                "SELECT session_id, author_scope, title, route, filters_json, status, created_at, updated_at "
                "FROM app_note_session WHERE session_id = :sid"
            ),
            {"sid": sid},
        ).fetchone()
        session.commit()
        return _row_dict(row)
    except Exception as exc:
        session.rollback()
        raise _schema_err(exc) from exc


@router.get("/sessions/{session_id}")
def get_session(_user: _UserDep, session: _SessionDep, session_id: str):
    try:
        sess = session.execute(
            text(
                "SELECT session_id, author_scope, title, route, filters_json, status, created_at, updated_at "
                "FROM app_note_session WHERE session_id = :sid"
            ),
            {"sid": session_id},
        ).fetchone()
        if not sess:
            raise HTTPException(404, detail="Session not found")
        notes = session.execute(
            text(
                "SELECT note_id, session_id, body, is_done, sort_order, created_at, updated_at "
                "FROM app_note WHERE session_id = :sid ORDER BY sort_order, created_at"
            ),
            {"sid": session_id},
        ).fetchall()
        pins = session.execute(
            text(
                "SELECT pin_id, session_id, label, snapshot_json, created_at "
                "FROM app_note_pin WHERE session_id = :sid ORDER BY created_at DESC"
            ),
            {"sid": session_id},
        ).fetchall()
        boards = session.execute(
            text(
                "SELECT board_id, session_id, title, board_json, created_at, updated_at "
                "FROM app_action_board WHERE session_id = :sid ORDER BY updated_at DESC LIMIT 5"
            ),
            {"sid": session_id},
        ).fetchall()
        session.commit()
        return {
            "session": _row_dict(sess),
            "notes": [_row_dict(n) for n in notes],
            "pins": [_row_dict(p) for p in pins],
            "boards": [_row_dict(b) for b in boards],
        }
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        raise _schema_err(exc) from exc


@router.post("/sessions/{session_id}/notes")
def add_note(_user: _UserDep, session: _SessionDep, session_id: str, body: NoteCreate):
    nid = _new_id("note")
    try:
        max_row = session.execute(
            text("SELECT COALESCE(MAX(sort_order), -1) AS m FROM app_note WHERE session_id = :sid"),
            {"sid": session_id},
        ).fetchone()
        sort_order = int(max_row[0]) + 1 if max_row else 0
        session.execute(
            text(
                "INSERT INTO app_note (note_id, session_id, body, is_done, sort_order) "
                "VALUES (:nid, :sid, :body, :done, :ord)"
            ),
            {"nid": nid, "sid": session_id, "body": body.body, "done": body.is_done, "ord": sort_order},
        )
        session.execute(
            text("UPDATE app_note_session SET updated_at = NOW() WHERE session_id = :sid"),
            {"sid": session_id},
        )
        row = session.execute(
            text(
                "SELECT note_id, session_id, body, is_done, sort_order, created_at, updated_at "
                "FROM app_note WHERE note_id = :nid"
            ),
            {"nid": nid},
        ).fetchone()
        session.commit()
        return _row_dict(row)
    except Exception as exc:
        session.rollback()
        raise _schema_err(exc) from exc


@router.patch("/sessions/{session_id}/notes/{note_id}")
def patch_note(_user: _UserDep, session: _SessionDep, session_id: str, note_id: str, body: NotePatch):
    try:
        if body.body is not None:
            session.execute(
                text("UPDATE app_note SET body = :body, updated_at = NOW() WHERE note_id = :nid"),
                {"body": body.body, "nid": note_id},
            )
        if body.is_done is not None:
            session.execute(
                text("UPDATE app_note SET is_done = :done, updated_at = NOW() WHERE note_id = :nid"),
                {"done": body.is_done, "nid": note_id},
            )
        if body.sort_order is not None:
            session.execute(
                text("UPDATE app_note SET sort_order = :ord, updated_at = NOW() WHERE note_id = :nid"),
                {"ord": body.sort_order, "nid": note_id},
            )
        session.execute(
            text("UPDATE app_note_session SET updated_at = NOW() WHERE session_id = :sid"),
            {"sid": session_id},
        )
        row = session.execute(
            text(
                "SELECT note_id, session_id, body, is_done, sort_order, created_at, updated_at "
                "FROM app_note WHERE note_id = :nid"
            ),
            {"nid": note_id},
        ).fetchone()
        session.commit()
        return _row_dict(row)
    except Exception as exc:
        session.rollback()
        raise _schema_err(exc) from exc


@router.delete("/sessions/{session_id}/notes/{note_id}")
def delete_note(_user: _UserDep, session: _SessionDep, session_id: str, note_id: str):
    try:
        session.execute(
            text("DELETE FROM app_note WHERE note_id = :nid AND session_id = :sid"),
            {"nid": note_id, "sid": session_id},
        )
        session.commit()
        return {"ok": True}
    except Exception as exc:
        session.rollback()
        raise _schema_err(exc) from exc


@router.post("/sessions/{session_id}/pins")
def add_pin(_user: _UserDep, session: _SessionDep, session_id: str, body: PinCreate):
    pid = _new_id("pin")
    try:
        session.execute(
            text(
                "INSERT INTO app_note_pin (pin_id, session_id, label, snapshot_json) "
                "VALUES (:pid, :sid, :label, CAST(:snap AS jsonb))"
            ),
            {"pid": pid, "sid": session_id, "label": body.label, "snap": json.dumps(body.snapshot)},
        )
        session.execute(
            text("UPDATE app_note_session SET updated_at = NOW() WHERE session_id = :sid"),
            {"sid": session_id},
        )
        row = session.execute(
            text(
                "SELECT pin_id, session_id, label, snapshot_json, created_at "
                "FROM app_note_pin WHERE pin_id = :pid"
            ),
            {"pid": pid},
        ).fetchone()
        session.commit()
        return _row_dict(row)
    except Exception as exc:
        session.rollback()
        raise _schema_err(exc) from exc


@router.post("/sessions/{session_id}/draft-email")
def create_draft_email(_user: _UserDep, session: _SessionDep, session_id: str, body: DraftEmailRequest):
    try:
        contact = session.execute(
            text(
                """
                SELECT contact_id, display_name, email, department, role_title,
                       salutation_de, salutation_en
                FROM dim_internal_contact
                WHERE contact_id = :cid AND is_active = TRUE
                """
            ),
            {"cid": body.contact_id},
        ).fetchone()
        if not contact:
            raise HTTPException(404, detail="Contact not found")
        contact_d = _row_dict(contact)
        note_rows = session.execute(
            text(
                "SELECT body FROM app_note WHERE session_id = :sid ORDER BY sort_order"
            ),
            {"sid": session_id},
        ).fetchall()
        note_texts = [r[0] for r in note_rows if r[0]]
        draft = draft_email(
            body.user_name, body.user_email, contact_d, note_texts, [], body.language,
        )
        did = _new_id("draft")
        session.execute(
            text(
                """
                INSERT INTO app_email_draft (draft_id, session_id, contact_id, subject, body_text, body_html)
                VALUES (:did, :sid, :cid, :subj, :txt, :html)
                """
            ),
            {
                "did": did,
                "sid": session_id,
                "cid": body.contact_id,
                "subj": draft["subject"],
                "txt": draft["body_text"],
                "html": draft.get("body_html"),
            },
        )
        session.commit()
        return {
            "draft_id": did,
            "contact": contact_d,
            "subject": draft["subject"],
            "body_text": draft["body_text"],
            "body_html": draft.get("body_html"),
            "action_items": draft.get("action_items", []),
            "notes": note_texts,
            "pin_links": [],
            "mailto": f"mailto:{contact_d['email']}",
            "status": "draft",
        }
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        raise _schema_err(exc) from exc


@router.post("/sessions/{session_id}/generate-board")
def create_board(_user: _UserDep, session: _SessionDep, session_id: str, body: GenerateBoardRequest):
    try:
        note_rows = session.execute(
            text("SELECT body FROM app_note WHERE session_id = :sid ORDER BY sort_order"),
            {"sid": session_id},
        ).fetchall()
        note_texts = [r[0] for r in note_rows if r[0]]
        contacts = session.execute(
            text(
                "SELECT contact_id, display_name, department, role_title "
                "FROM dim_internal_contact WHERE is_active = TRUE ORDER BY sort_order"
            ),
        ).fetchall()
        board = generate_board(note_texts, [_row_dict(c) for c in contacts])
        bid = _new_id("board")
        session.execute(
            text(
                "INSERT INTO app_action_board (board_id, session_id, title, board_json) "
                "VALUES (:bid, :sid, :title, CAST(:bj AS jsonb))"
            ),
            {"bid": bid, "sid": session_id, "title": board.get("title", "Action board"), "bj": json.dumps(board)},
        )
        session.commit()
        return {"board_id": bid, "board": board}
    except Exception as exc:
        session.rollback()
        raise _schema_err(exc) from exc
