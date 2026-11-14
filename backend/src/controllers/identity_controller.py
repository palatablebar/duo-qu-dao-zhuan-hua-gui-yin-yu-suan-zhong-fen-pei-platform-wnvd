"""身份合并控制器：受理、明细、汇总。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, identity_service


def list_batches(db: Session, **filters) -> dict:
    return identity_service.list_batches(db, **filters)


def get_batch(db: Session, batch_id: int, status: str | None = None) -> dict:
    return identity_service.get_batch(db, batch_id, event_status=status)


def summary(db: Session) -> dict:
    return identity_service.overview_summary(db)


def list_links(db: Session, **filters) -> dict:
    return identity_service.list_links(db, **filters)


def create_merge(db: Session, payload: dict, principal: dict, request: Request) -> dict:
    batch = identity_service.merge(
        db,
        events=payload["events"],
        operator_email=principal["email"],
        source="manual",
        note=payload.get("note"),
        batch_key=payload.get("batch_key"),
    )
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="identity.merge",
        entity="identity_merge_batch",
        entity_id=batch.id,
        detail={
            "total": batch.total_events,
            "accepted": batch.accepted_count,
            "conflict": batch.conflict_count,
            "failed": batch.failed_count,
            "duplicate": batch.duplicate_count,
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return identity_service.serialize_batch(batch)
