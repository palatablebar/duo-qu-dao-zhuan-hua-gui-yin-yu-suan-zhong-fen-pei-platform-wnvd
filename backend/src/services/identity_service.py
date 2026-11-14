"""身份合并服务：设备 × 会员关联的受理、去重、冲突标记、归因重算与快照。

流程（一次 POST /api/identity-merges）：
1. 校验事件并做入库去重：event_key 已存在 -> duplicate（不新增关联、不重复计触点）；
2. 按 (arrived_at, event_key) 顺序处理：设备首条事件建立关联，同会员重试记 link_exists，
   设备已绑定其他会员记 conflict（保留先到关联，不覆盖）；
3. 合并口径 = 全量 identity_links（跨批次累积）+ 本批新建立的关联；
4. 合并前后各构建一次路径集合，产出用户数 / 触点数 / 渠道占比快照；
5. 历史明细不删除、不改写：触点与订单保留原 user_id 与 device_id，归因每次按当时的
   关联集合重新构建（合并后口径），旧运行快照原样保留。
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import CHANNEL_TYPE_LABELS
from src.models.channel import Channel
from src.models.conversion import Conversion
from src.models.identity import IdentityLink, IdentityMergeBatch, IdentityMergeEvent
from src.models.touchpoint import Touchpoint
from src.services import algorithms, identity_rules
from src.utils.errors import AppError
from src.utils.timeutil import iso_utc, utcnow

MAX_MERGE_EVENTS = 500


# --------------------------------------------------------------------------
# 关联读取
# --------------------------------------------------------------------------
def load_links(db: Session) -> dict[str, str]:
    return {row.device_id: row.member_id for row in db.scalars(select(IdentityLink)).all()}


def _parse_stamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip().replace("Z", "")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# 路径快照（合并前 / 合并后）
# --------------------------------------------------------------------------
def event_rows(db: Session) -> tuple[list[dict], list[dict], list[str]]:
    channels = db.scalars(select(Channel).order_by(Channel.id)).all()
    code_by_id = {channel.id: channel.code for channel in channels}
    touchpoints = db.scalars(select(Touchpoint).order_by(Touchpoint.touched_at, Touchpoint.id)).all()
    conversions = db.scalars(select(Conversion).order_by(Conversion.converted_at)).all()
    tp_rows = [
        {
            "user_id": row.user_id,
            "device_id": row.device_id,
            "channel_code": code_by_id.get(row.channel_id, ""),
            "touched_at": row.touched_at.isoformat(),
        }
        for row in touchpoints
        if row.channel_id in code_by_id
    ]
    cv_rows = [
        {"user_id": row.user_id, "device_id": row.device_id, "amount": row.amount}
        for row in conversions
    ]
    return tp_rows, cv_rows, [channel.code for channel in channels]


def _channel_breakdown(db: Session, journeys: Sequence[dict], summary: dict | None) -> list[dict]:
    channels = db.scalars(select(Channel).order_by(Channel.id)).all()
    touch_by_code: dict[str, int] = defaultdict(int)
    for journey in journeys:
        for code in journey["path"]:
            touch_by_code[code] += 1
    converted_by_code: dict[str, int] = defaultdict(int)
    for journey in journeys:
        if journey["converted"] and journey["path"]:
            converted_by_code[journey["path"][-1]] += 1
    items = []
    for channel in channels:
        items.append(
            {
                "channel_id": channel.id,
                "channel_code": channel.code,
                "channel_name": channel.name,
                "channel_type": channel.type,
                "channel_type_label": CHANNEL_TYPE_LABELS.get(channel.type, channel.type),
                "touchpoints": touch_by_code.get(channel.code, 0),
                "conversions": converted_by_code.get(channel.code, 0),
                "last_click_share": round(
                    float((summary or {}).get("last_click_share", {}).get(channel.code, 0.0)), 6
                ),
                "markov_share": round(
                    float((summary or {}).get("markov_share", {}).get(channel.code, 0.0)), 6
                ),
            }
        )
    return items


def snapshot(db: Session, links: dict[str, str] | None) -> dict:
    """某一关联集合下的规模与渠道占比快照（合并前 / 合并后各算一次）。"""
    tp_rows, cv_rows, codes = event_rows(db)
    journeys = algorithms.build_journeys(tp_rows, cv_rows, links)
    summary = algorithms.summarize(journeys, codes) if codes else {
        "users": 0,
        "touchpoints": 0,
        "conversions": 0,
        "conversion_rate": 0.0,
        "last_click_share": {},
        "markov_share": {},
    }
    return {
        "users": summary["users"],
        "touchpoints": summary["touchpoints"],
        "conversions": summary["conversions"],
        "conversion_rate": summary.get("conversion_rate", 0.0),
        "last_click_share": summary.get("last_click_share", {}),
        "markov_share": summary.get("markov_share", {}),
        "channels": codes,
        "channel_breakdown": _channel_breakdown(db, journeys, summary),
    }


def channel_shares(db: Session, links: dict[str, str] | None) -> list[dict]:
    """渠道占比（按给定关联集合重建路径），带触点数与转化数，供前端图表使用。"""
    tp_rows, cv_rows, codes = event_rows(db)
    journeys = algorithms.build_journeys(tp_rows, cv_rows, links)
    summary = algorithms.summarize(journeys, codes) if codes else None
    return _channel_breakdown(db, journeys, summary)


# --------------------------------------------------------------------------
# 受理
# --------------------------------------------------------------------------
def _serialize_event(row: IdentityMergeEvent) -> dict:
    return {
        "id": row.id,
        "event_key": row.event_key,
        "device_id": row.device_id,
        "member_id": row.member_id,
        "anonymous_user_id": row.anonymous_user_id,
        "arrived_at": iso_utc(row.arrived_at),
        "status": row.status,
        "status_label": identity_rules.MERGE_STATUS_LABELS.get(row.status, row.status),
        "reason": row.reason,
        "reason_label": identity_rules.MERGE_REASON_LABELS.get(row.reason, row.reason or ""),
        "kept_member_id": row.kept_member_id,
        "kept_event_key": row.kept_event_key,
        "touchpoint_count": row.touchpoint_count,
        "created_at": iso_utc(row.created_at),
    }


def serialize_batch(row: IdentityMergeBatch, events: Sequence[IdentityMergeEvent] | None = None) -> dict:
    payload = {
        "id": row.id,
        "batch_key": row.batch_key,
        "operator_email": row.operator_email,
        "source": row.source,
        "note": row.note,
        "total_events": row.total_events,
        "accepted_count": row.accepted_count,
        "conflict_count": row.conflict_count,
        "failed_count": row.failed_count,
        "duplicate_count": row.duplicate_count,
        "before": row.snapshot_before or {},
        "after": row.snapshot_after or {},
        "created_at": iso_utc(row.created_at),
    }
    if events is not None:
        payload["events"] = [_serialize_event(event) for event in events]
    return payload


def _existing_by_key(db: Session, keys: Iterable[str]) -> dict[str, IdentityMergeEvent]:
    keys = [key for key in keys if key]
    if not keys:
        return {}
    rows = db.scalars(select(IdentityMergeEvent).where(IdentityMergeEvent.event_key.in_(keys))).all()
    return {row.event_key: row for row in rows}


def _placeholder_key(event: dict, position: int) -> str:
    import hashlib

    seed = "|".join(
        str(event.get(field) or "")
        for field in ("device_id", "member_id", "anonymous_user_id", "arrived_at", "position")
    )
    return "invalid:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:32]


def _touchpoints_for_device(db: Session, device_id: str | None) -> int:
    if not device_id:
        return 0
    return int(
        db.scalar(select(func.count()).select_from(Touchpoint).where(Touchpoint.device_id == device_id)) or 0
    )


def merge(
    db: Session,
    *,
    events: Sequence[dict],
    operator_email: str,
    source: str = "manual",
    note: str | None = None,
    batch_key: str | None = None,
) -> IdentityMergeBatch:
    if not events:
        raise AppError("VALIDATION", 422, {"field": "events"})
    if len(events) > MAX_MERGE_EVENTS:
        raise AppError("VALIDATION", 422, {"field": "events", "max": MAX_MERGE_EVENTS})

    if batch_key:
        existing = db.scalar(select(IdentityMergeBatch).where(IdentityMergeBatch.batch_key == batch_key))
        if existing is not None:
            existing.duplicate_count = existing.duplicate_count or 0
            return existing

    # 「合并前」= 本批受理前的关联集合（首批为空，后续批次含前序关联）
    before = snapshot(db, load_links(db))
    raw_keys = [str(event.get("event_key") or "") for event in events]
    stored_keys = [key or _placeholder_key(event, position) for position, (event, key) in enumerate(zip(events, raw_keys))]
    prior = _existing_by_key(db, stored_keys)

    batch = IdentityMergeBatch(
        batch_key=batch_key,
        operator_email=operator_email,
        source=source,
        note=note,
        total_events=len(events),
        snapshot_before=before,
    )
    db.add(batch)
    db.flush()

    duplicate_count = 0
    fresh: list[dict] = []
    seen_keys: set[str] = set()
    for position, (event, key, stored_key) in enumerate(zip(events, raw_keys, stored_keys)):
        # 同一 event_key 重试：不新增关联、不重复计触点；缺事件号用内容派生占位键，同样幂等
        if stored_key in prior or stored_key in seen_keys:
            duplicate_count += 1
            continue
        seen_keys.add(stored_key)
        fresh.append(
            {
                "event_key": stored_key,
                "device_id": str(event.get("device_id") or "").strip() or None,
                "member_id": str(event.get("member_id") or "").strip() or None,
                "anonymous_user_id": event.get("anonymous_user_id"),
                "arrived_at": _parse_stamp(event.get("arrived_at")) or utcnow(),
                "position": position,
                "key_valid": bool(key),
            }
        )

    decisions, merged_links, first_events = identity_rules.resolve_merges(
        fresh,
        existing_links=load_links(db),
        existing_first_events={
            row.device_id: row.first_event_key for row in db.scalars(select(IdentityLink)).all()
        },
    )

    accepted = 0
    conflicts = 0
    failed = 0
    for row in fresh:
        decision = decisions.get(row["event_key"] or "", {})
        status = decision.get("status", identity_rules.FAILED)
        if status == identity_rules.ACCEPTED:
            accepted += 1
        elif status == identity_rules.CONFLICT:
            conflicts += 1
        else:
            failed += 1
        db.add(
            IdentityMergeEvent(
                batch_id=batch.id,
                event_key=row["event_key"],
                device_id=row["device_id"],
                member_id=row["member_id"],
                anonymous_user_id=row["anonymous_user_id"],
                arrived_at=row["arrived_at"],
                status=status,
                reason=decision.get("reason"),
                kept_member_id=decision.get("kept_member_id"),
                kept_event_key=decision.get("kept_event_key"),
                touchpoint_count=_touchpoints_for_device(db, row["device_id"]),
            )
        )

    # 只落新建立的关联：冲突不覆盖、重复不新增
    existing_links = load_links(db)
    for device, member in merged_links.items():
        if device in existing_links:
            continue
        db.add(
            IdentityLink(
                device_id=device,
                member_id=member,
                first_event_key=first_events.get(device) or "",
                established_at=next(
                    (
                        row["arrived_at"]
                        for row in fresh
                        if row["device_id"] == device
                        and decisions.get(row["event_key"] or "", {}).get("reason")
                        == identity_rules.REASON_LINK_CREATED
                    ),
                    utcnow(),
                ),
            )
        )
    db.flush()

    batch.accepted_count = accepted
    batch.conflict_count = conflicts
    batch.failed_count = failed
    batch.duplicate_count = duplicate_count
    batch.snapshot_after = snapshot(db, merged_links)
    db.flush()
    return batch


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------
def list_batches(db: Session, limit: int = 20, offset: int = 0) -> dict:
    total = int(db.scalar(select(func.count()).select_from(IdentityMergeBatch)) or 0)
    rows = db.scalars(
        select(IdentityMergeBatch).order_by(IdentityMergeBatch.id.desc()).limit(limit).offset(offset)
    ).all()
    return {
        "items": [serialize_batch(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_batch(db: Session, batch_id: int, event_status: str | None = None) -> dict:
    batch = db.get(IdentityMergeBatch, batch_id)
    if batch is None:
        raise AppError("NOT_FOUND", 404, {"entity": "identity_merge_batch", "id": batch_id})
    stmt = select(IdentityMergeEvent).where(IdentityMergeEvent.batch_id == batch.id)
    if event_status:
        stmt = stmt.where(IdentityMergeEvent.status == event_status)
    events = db.scalars(stmt.order_by(IdentityMergeEvent.arrived_at, IdentityMergeEvent.event_key)).all()
    payload = serialize_batch(batch, events)
    # 渠道占比取该批次受理时落下的快照，历史批次不随后续合并变化
    payload["channels"] = (batch.snapshot_after or {}).get("channel_breakdown", [])
    payload["before_channels"] = (batch.snapshot_before or {}).get("channel_breakdown", [])
    payload["status_counts"] = {
        status: int(
            db.scalar(
                select(func.count())
                .select_from(IdentityMergeEvent)
                .where(IdentityMergeEvent.batch_id == batch.id, IdentityMergeEvent.status == status)
            )
            or 0
        )
        for status in (
            identity_rules.ACCEPTED,
            identity_rules.CONFLICT,
            identity_rules.FAILED,
        )
    }
    return payload


def list_links(db: Session, *, q: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """当前生效的 device -> member 关联，可回查首次建立关联的事件号与时间。"""
    stmt = select(IdentityLink)
    count_stmt = select(func.count()).select_from(IdentityLink)
    if q:
        like = f"%{q.strip()}%"
        clause = IdentityLink.device_id.ilike(like) | IdentityLink.member_id.ilike(like)
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)
    total = int(db.scalar(count_stmt) or 0)
    rows = db.scalars(stmt.order_by(IdentityLink.device_id).limit(limit).offset(offset)).all()
    return {
        "items": [
            {
                "id": row.id,
                "device_id": row.device_id,
                "member_id": row.member_id,
                "first_event_key": row.first_event_key,
                "established_at": iso_utc(row.established_at),
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def live_comparison(db: Session) -> dict:
    """当前全量明细在「合并前 / 合并后」两种口径下的规模与渠道占比。"""
    links = load_links(db)
    before = snapshot(db, None)
    after = snapshot(db, links)
    return {
        "links_total": len(links),
        "before": before,
        "after": after,
        "merged_users": max(0, before["users"] - after["users"]),
        "channels": {
            "before": channel_shares(db, None),
            "after": channel_shares(db, links),
        },
    }


def overview_summary(db: Session) -> dict:
    """页面顶部汇总：各状态总量、最新批次、合并前后身份规模。"""
    counts = {
        status: int(
            db.scalar(
                select(func.count()).select_from(IdentityMergeEvent).where(IdentityMergeEvent.status == status)
            )
            or 0
        )
        for status in (identity_rules.ACCEPTED, identity_rules.CONFLICT, identity_rules.FAILED)
    }
    links_total = int(db.scalar(select(func.count()).select_from(IdentityLink)) or 0)
    batches_total = int(db.scalar(select(func.count()).select_from(IdentityMergeBatch)) or 0)
    latest = db.scalar(select(IdentityMergeBatch).order_by(IdentityMergeBatch.id.desc()).limit(1))
    latest_payload = serialize_batch(latest) if latest else None
    return {
        "batches_total": batches_total,
        "links_total": links_total,
        "accepted_total": counts[identity_rules.ACCEPTED],
        "conflict_total": counts[identity_rules.CONFLICT],
        "failed_total": counts[identity_rules.FAILED],
        "latest": latest_payload,
        "status_labels": identity_rules.MERGE_STATUS_LABELS,
        "live": live_comparison(db),
    }
