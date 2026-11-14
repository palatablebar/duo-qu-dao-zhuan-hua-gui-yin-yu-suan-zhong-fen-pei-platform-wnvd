"""设备 × 会员身份合并：批次、事件、当前关联。

- identity_merge_batches：一次受理的合并请求（批量事件），带受理/冲突/失败计数与前后快照；
- identity_merge_events：逐事件的决定（event_key 全局唯一，重试直接复用，不再新增关联/触点）；
- identity_links：当前生效的 device -> member 关联，先到先得，冲突只记录不覆盖。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class IdentityMergeBatch(Base):
    __tablename__ = "identity_merge_batches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    operator_email: Mapped[str] = mapped_column(String(160), default="system")
    source: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_events: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    # MySQL 中 BEFORE 是保留字，列名加前缀；API 仍以 before / after 输出
    snapshot_before: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_after: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class IdentityMergeEvent(Base):
    __tablename__ = "identity_merge_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("identity_merge_batches.id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    member_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    anonymous_user_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="accepted", index=True)
    reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    kept_member_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kept_event_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    touchpoint_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class IdentityLink(Base):
    __tablename__ = "identity_links"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    member_id: Mapped[str] = mapped_column(String(64), index=True)
    first_event_key: Mapped[str] = mapped_column(String(120), index=True)
    established_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
