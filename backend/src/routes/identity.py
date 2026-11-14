from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import READ_ROLES, MERGE_WRITE_ROLES
from src.controllers import identity_controller
from src.middlewares.rbac import require_roles
from src.schemas.identity import MergeIn
from src.utils.pagination import page_params

router = APIRouter(prefix="/identity-merges", tags=["identity-merges"])


@router.get("")
def list_batches(
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return identity_controller.list_batches(db, limit=page.limit, offset=page.offset)


@router.get("/summary")
def summary(db: Session = Depends(get_db), principal: dict = Depends(require_roles(*READ_ROLES))) -> dict:
    return identity_controller.summary(db)


@router.get("/links")
def list_links(
    q: str | None = None,
    limit: int = Query(50),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return identity_controller.list_links(db, q=q, limit=page.limit, offset=page.offset)


@router.get("/{batch_id}")
def get_batch(
    batch_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    return identity_controller.get_batch(db, batch_id, status=status)


@router.post("", status_code=201)
def create_merge(
    payload: MergeIn,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*MERGE_WRITE_ROLES)),
) -> dict:
    return identity_controller.create_merge(db, payload.model_dump(), principal, request)
