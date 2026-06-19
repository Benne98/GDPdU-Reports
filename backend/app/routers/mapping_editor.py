"""Mapping editor — read/update chart of accounts hierarchy and sort order.

Prefix : /api/v1/mapping-editor
Auth   : require_admin
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import User, require_admin
from app.db import get_session
from app.services import mapping_editor as svc

router = APIRouter(prefix="/api/v1/mapping-editor", tags=["mapping-editor"])


class AccountRow(BaseModel):
    account_number_group: str
    fiscal_year: int
    gl_account_id: str = ""
    account_name: str = ""
    level_0: str = ""
    level_1: str = ""
    level_2: str = ""
    level_3: str = ""
    level_4: str = ""
    l4_sub: str = ""
    level_1_sort: Optional[int] = None
    level_2_sort: Optional[int] = None
    level_3_sort: Optional[int] = None
    level_4_sort: Optional[int] = None


class AccountsListResponse(BaseModel):
    fiscal_year: int
    statement: Optional[str] = None
    accounts: list[AccountRow]


class AccountPatchItem(BaseModel):
    account_number_group: str
    account_name: Optional[str] = None
    level_0: Optional[str] = None
    level_1: Optional[str] = None
    level_2: Optional[str] = None
    level_3: Optional[str] = None
    level_4: Optional[str] = None
    l4_sub: Optional[str] = None


class AccountsPatchRequest(BaseModel):
    fiscal_year: int
    apply_scope: Literal["all_years", "single_year"] = "all_years"
    updates: list[AccountPatchItem]


class AccountsPatchResponse(BaseModel):
    updated: int


class StructureNode(BaseModel):
    id: str
    label: str
    level_key: str
    ui_level: str
    path: dict[str, str]
    sort_order: Optional[int] = None
    account_count: int
    children: list["StructureNode"] = Field(default_factory=list)


StructureNode.model_rebuild()


class StructureResponse(BaseModel):
    fiscal_year: int
    statement: Literal["BS", "PL"]
    nodes: list[StructureNode]


class StructureReorderRequest(BaseModel):
    fiscal_year: int
    apply_scope: Literal["all_years", "single_year"] = "all_years"
    statement: Literal["BS", "PL"]
    parent_path: dict[str, str] = Field(default_factory=dict)
    level_key: str
    ordered_labels: list[str]


class StructureReorderResponse(BaseModel):
    updated_accounts: int


@router.get("/accounts", response_model=AccountsListResponse)
def get_accounts(
    session: Annotated[Session, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
    fiscal_year: int = Query(..., ge=2000, le=2099),
    statement: Optional[Literal["BS", "PL"]] = Query(None),
) -> AccountsListResponse:
    rows = svc.list_accounts(session, fiscal_year=fiscal_year, statement=statement)
    return AccountsListResponse(
        fiscal_year=fiscal_year,
        statement=statement,
        accounts=[AccountRow(**r) for r in rows],
    )


@router.patch("/accounts", response_model=AccountsPatchResponse)
def patch_accounts(
    body: AccountsPatchRequest,
    session: Annotated[Session, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> AccountsPatchResponse:
    if not body.updates:
        raise HTTPException(status_code=422, detail="updates must not be empty")
    try:
        touched = svc.patch_accounts(
            session,
            fiscal_year=body.fiscal_year,
            updates=[u.model_dump(exclude_none=True) for u in body.updates],
            apply_scope=body.apply_scope,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AccountsPatchResponse(updated=touched)


@router.get("/structure", response_model=StructureResponse)
def get_structure(
    session: Annotated[Session, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
    fiscal_year: int = Query(..., ge=2000, le=2099),
    statement: Literal["BS", "PL"] = Query(...),
) -> StructureResponse:
    nodes = svc.build_structure_tree(session, fiscal_year=fiscal_year, statement=statement)
    return StructureResponse(fiscal_year=fiscal_year, statement=statement, nodes=nodes)


@router.post("/structure/reorder", response_model=StructureReorderResponse)
def reorder_structure(
    body: StructureReorderRequest,
    session: Annotated[Session, Depends(get_session)],
    _admin: Annotated[User, Depends(require_admin)],
) -> StructureReorderResponse:
    if not body.ordered_labels:
        raise HTTPException(status_code=422, detail="ordered_labels must not be empty")
    try:
        touched = svc.reorder_structure_siblings(
            session,
            fiscal_year=body.fiscal_year,
            statement=body.statement,
            parent_path=body.parent_path,
            level_key=body.level_key,
            ordered_labels=body.ordered_labels,
            apply_scope=body.apply_scope,
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return StructureReorderResponse(updated_accounts=touched)
