"""身份合并规则单测（纯函数）：重试幂等、先到先得、冲突标记、失败分类。"""
from __future__ import annotations

import pytest

from src.services import identity_rules

SAME_DEVICE_TWO_MEMBERS = [
    {"event_key": "e2", "device_id": "d1", "member_id": "m2", "arrived_at": "2026-10-02T10:00:00"},
    {"event_key": "e1", "device_id": "d1", "member_id": "m1", "arrived_at": "2026-10-01T10:00:00"},
]


def test_arrival_order_decides_the_kept_link():
    decisions, links, first = identity_rules.resolve_merges(SAME_DEVICE_TWO_MEMBERS)
    assert links == {"d1": "m1"}
    assert first == {"d1": "e1"}
    assert decisions["e1"]["status"] == identity_rules.ACCEPTED
    assert decisions["e1"]["reason"] == identity_rules.REASON_LINK_CREATED
    assert decisions["e2"]["status"] == identity_rules.CONFLICT
    assert decisions["e2"]["kept_member_id"] == "m1"
    assert decisions["e2"]["kept_event_key"] == "e1"


def test_same_member_repeat_does_not_create_a_new_link():
    decisions, links, _ = identity_rules.resolve_merges(
        [
            {"event_key": "e1", "device_id": "d1", "member_id": "m1", "arrived_at": "2026-10-01T10:00:00"},
            {"event_key": "e2", "device_id": "d1", "member_id": "m1", "arrived_at": "2026-10-01T11:00:00"},
        ]
    )
    assert links == {"d1": "m1"}
    assert decisions["e2"]["status"] == identity_rules.ACCEPTED
    assert decisions["e2"]["reason"] == identity_rules.REASON_LINK_EXISTS
    assert decisions["e2"]["kept_event_key"] == "e1"


def test_existing_link_wins_over_later_batch():
    decisions, links, _ = identity_rules.resolve_merges(
        [{"event_key": "e9", "device_id": "d1", "member_id": "m9", "arrived_at": "2026-10-05T10:00:00"}],
        existing_links={"d1": "m1"},
        existing_first_events={"d1": "e1"},
    )
    assert links == {"d1": "m1"}
    assert decisions["e9"]["status"] == identity_rules.CONFLICT
    assert decisions["e9"]["kept_member_id"] == "m1"
    assert decisions["e9"]["kept_event_key"] == "e1"


def test_invalid_events_are_failed_and_do_not_create_links():
    decisions, links, _ = identity_rules.resolve_merges(
        [
            {"event_key": "", "device_id": "d1", "member_id": "m1", "arrived_at": "2026-10-01T10:00:00"},
            {"event_key": "e2", "device_id": "bad device!", "member_id": "m1", "arrived_at": "2026-10-01T11:00:00"},
            {"event_key": "e3", "device_id": "d2", "member_id": "", "arrived_at": "2026-10-01T12:00:00"},
        ]
    )
    assert links == {}
    assert decisions[""]["status"] == identity_rules.FAILED
    assert decisions[""]["reason"] == identity_rules.REASON_INVALID_EVENT_KEY
    assert decisions["e2"]["reason"] == identity_rules.REASON_INVALID_DEVICE
    assert decisions["e3"]["reason"] == identity_rules.REASON_INVALID_MEMBER


def test_same_timestamp_is_broken_by_event_key():
    decisions, links, _ = identity_rules.resolve_merges(
        [
            {"event_key": "b", "device_id": "d1", "member_id": "m2", "arrived_at": "2026-10-01T10:00:00"},
            {"event_key": "a", "device_id": "d1", "member_id": "m1", "arrived_at": "2026-10-01T10:00:00"},
        ]
    )
    assert links == {"d1": "m1"}
    assert decisions["b"]["status"] == identity_rules.CONFLICT


def test_retry_of_same_event_yields_one_decision():
    # event_key 入库去重在服务层完成；规则层对同一 event_key 只保留一条决定，天然幂等。
    events = [
        {"event_key": "e1", "device_id": "d1", "member_id": "m1", "arrived_at": "2026-10-01T10:00:00"},
        {"event_key": "e1", "device_id": "d1", "member_id": "m1", "arrived_at": "2026-10-01T10:00:00"},
    ]
    decisions, links, _ = identity_rules.resolve_merges(events)
    assert links == {"d1": "m1"}
    assert decisions["e1"]["reason"] == identity_rules.REASON_LINK_CREATED
    assert len(decisions) == 1


def test_validate_merge_groups_errors():
    assert identity_rules.validate_merge(
        {"event_key": "e1", "device_id": "d1", "member_id": "m1"}
    ) == []
    errors = identity_rules.validate_merge({"event_key": "", "device_id": "", "member_id": ""})
    assert errors == [
        identity_rules.REASON_INVALID_EVENT_KEY,
        identity_rules.REASON_INVALID_DEVICE,
        identity_rules.REASON_INVALID_MEMBER,
    ]
