"""身份合并链路集成测试（SQLite 内存库，不依赖 MySQL/Redis）。

覆盖：合并受理计数、重试幂等、冲突先到先得、失败分类、合并前后快照、
归因运行按合并后身份重建、历史明细可回查。
"""
from __future__ import annotations

import os

os.environ.setdefault("DB_KIND", "sqlite")
os.environ.setdefault("SEED_DEMO", "false")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")

import pytest  # noqa: E402

from src.config.database import SessionLocal, engine  # noqa: E402
from src.models import Base  # noqa: E402
from src.models.channel import Channel  # noqa: E402
from src.models.conversion import Conversion  # noqa: E402
from src.models.attribution_run import AttributionRun  # noqa: E402
from src.models.touchpoint import Touchpoint  # noqa: E402
from src.services import attribution_service, identity_service  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    session.add_all(
        [
            Channel(code="a", name="渠道 A", type="content", monthly_cost=100.0, budget=100.0),
            Channel(code="b", name="渠道 B", type="search", monthly_cost=100.0, budget=100.0),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _seed_events(db):
    channel_ids = {row.code: row.id for row in db.query(Channel).all()}
    db.add_all(
        [
            # 匿名浏览 a → 登录后 b 下单
            Touchpoint(user_id="AN1", device_id="D1", channel_id=channel_ids["a"], touched_at=__import__("datetime").datetime(2026, 10, 1, 10)),
            Touchpoint(user_id="U1", device_id="D1", channel_id=channel_ids["a"], touched_at=__import__("datetime").datetime(2026, 10, 1, 10)),
            Touchpoint(user_id="U1", device_id="D1", channel_id=channel_ids["b"], touched_at=__import__("datetime").datetime(2026, 10, 1, 11)),
            Touchpoint(user_id="U2", device_id="D2", channel_id=channel_ids["b"], touched_at=__import__("datetime").datetime(2026, 10, 2, 11)),
            Conversion(user_id="U1", device_id="D1", amount=100.0, order_no="Q1", converted_at=__import__("datetime").datetime(2026, 10, 1, 12)),
            Conversion(user_id="U2", device_id="D2", amount=100.0, order_no="Q2", converted_at=__import__("datetime").datetime(2026, 10, 2, 12)),
        ]
    )
    db.commit()


def test_merge_counts_and_before_after_snapshot(db):
    _seed_events(db)
    batch = identity_service.merge(
        db,
        events=[
            {"event_key": "M1", "device_id": "D1", "member_id": "U1", "arrived_at": "2026-10-01T10:30:00"},
        ],
        operator_email="analyst@mixcredit.io",
    )
    db.commit()
    assert batch.total_events == 1
    assert batch.accepted_count == 1 and batch.conflict_count == 0 and batch.failed_count == 0
    assert batch.snapshot_before["users"] == 3  # 本批受理前（无关联）3 条路径
    assert batch.snapshot_after["users"] == 2
    # 匿名与登录后同设备同渠道同一时刻的重复触点被去重，触点数 4 → 3
    assert batch.snapshot_before["touchpoints"] == 4
    assert batch.snapshot_after["touchpoints"] == 3
    assert batch.snapshot_before["conversions"] == batch.snapshot_after["conversions"] == 2


def test_retry_of_same_event_adds_no_link_or_touchpoint(db):
    _seed_events(db)
    first = identity_service.merge(
        db,
        events=[{"event_key": "M1", "device_id": "D1", "member_id": "U1", "arrived_at": "2026-10-01T10:30:00"}],
        operator_email="analyst@mixcredit.io",
    )
    db.commit()
    second = identity_service.merge(
        db,
        events=[{"event_key": "M1", "device_id": "D1", "member_id": "U1", "arrived_at": "2026-10-01T10:30:00"}],
        operator_email="analyst@mixcredit.io",
    )
    db.commit()
    assert identity_service.load_links(db) == {"D1": "U1"}
    assert second.duplicate_count == 1
    assert second.snapshot_after["touchpoints"] == first.snapshot_after["touchpoints"]
    assert db.query(Touchpoint).count() == 4  # 明细不因合并新增/改写


def test_conflict_keeps_first_arrival_and_is_marked(db):
    _seed_events(db)
    batch = identity_service.merge(
        db,
        events=[
            {"event_key": "LATE", "device_id": "D1", "member_id": "U9", "arrived_at": "2026-10-02T09:00:00"},
            {"event_key": "EARLY", "device_id": "D1", "member_id": "U1", "arrived_at": "2026-10-01T10:30:00"},
        ],
        operator_email="analyst@mixcredit.io",
    )
    db.commit()
    assert identity_service.load_links(db) == {"D1": "U1"}
    assert batch.accepted_count == 1
    assert batch.conflict_count == 1
    detail = identity_service.get_batch(db, batch.id)
    late = next(item for item in detail["events"] if item["event_key"] == "LATE")
    assert late["status"] == "conflict"
    assert late["kept_member_id"] == "U1"
    assert late["kept_event_key"] == "EARLY"


def test_invalid_event_counts_as_failed(db):
    _seed_events(db)
    batch = identity_service.merge(
        db,
        events=[
            {"event_key": "OK", "device_id": "D1", "member_id": "U1", "arrived_at": "2026-10-01T10:30:00"},
            {"event_key": "BAD", "device_id": "bad device!", "member_id": "U1", "arrived_at": "2026-10-01T11:00:00"},
        ],
        operator_email="analyst@mixcredit.io",
    )
    db.commit()
    assert (batch.accepted_count, batch.conflict_count, batch.failed_count) == (1, 0, 1)


def test_attribution_run_uses_merged_identity_and_keeps_history(db):
    _seed_events(db)

    def run_once(run_no):
        run = AttributionRun(run_no=run_no, period="2026-10", model="markov", operator_email="analyst@mixcredit.io")
        db.add(run)
        db.flush()
        return attribution_service.execute_run(db, run)

    raw = run_once("R0")
    identity_service.merge(
        db,
        events=[{"event_key": "M1", "device_id": "D1", "member_id": "U1", "arrived_at": "2026-10-01T10:30:00"}],
        operator_email="analyst@mixcredit.io",
    )
    db.flush()
    merged_run = run_once("R1")
    db.commit()
    assert raw.users_count == 3 and merged_run.users_count == 2
    identity = merged_run.metrics["identity"]
    assert identity["merged"] is True
    assert identity["links"] == 1
    assert identity["before_users"] == 3
    assert identity["after_users"] == 2
    # 历史明细原样保留，可按 user_id / device_id 回查
    assert db.query(Touchpoint).filter(Touchpoint.user_id == "AN1").count() == 1
    assert db.query(Touchpoint).filter(Touchpoint.device_id == "D1").count() == 3
