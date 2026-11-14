"""身份合并请求体。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MergeEventIn(BaseModel):
    event_key: str = Field(min_length=1, max_length=120, description="合并事件号，重试幂等键")
    device_id: str = Field(min_length=1, max_length=64, description="设备号")
    member_id: str = Field(min_length=1, max_length=64, description="登录会员号")
    anonymous_user_id: str | None = Field(default=None, max_length=16)
    arrived_at: datetime | None = None


class MergeIn(BaseModel):
    events: list[MergeEventIn] = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=500)
    batch_key: str | None = Field(default=None, max_length=120)
