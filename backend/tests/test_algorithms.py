"""归因算法与预算重分配单测（纯函数，不依赖数据库）。"""
from __future__ import annotations

import pytest

from src.services import algorithms

TOUCHPOINTS = [
    {"user_id": "u1", "channel_code": "a", "touched_at": "2026-10-01T10:00:00"},
    {"user_id": "u1", "channel_code": "b", "touched_at": "2026-10-01T11:00:00"},
    {"user_id": "u2", "channel_code": "b", "touched_at": "2026-10-02T10:00:00"},
    {"user_id": "u3", "channel_code": "a", "touched_at": "2026-10-03T10:00:00"},
]
CONVERSIONS = [
    {"user_id": "u1", "amount": 100.0},
    {"user_id": "u2", "amount": 50.0},
]
CHANNELS = ["a", "b"]


def journeys():
    return algorithms.build_journeys(TOUCHPOINTS, CONVERSIONS)


def test_build_journeys_sorts_and_aggregates():
    rows = algorithms.build_journeys(
        [
            {"user_id": "u9", "channel_code": "b", "touched_at": "2026-10-02T09:00:00"},
            {"user_id": "u9", "channel_code": "a", "touched_at": "2026-10-01T09:00:00"},
        ],
        [{"user_id": "u9", "amount": 30.0}, {"user_id": "u9", "amount": 20.0}],
    )
    assert rows[0]["path"] == ["a", "b"]
    assert rows[0]["revenue"] == 50.0
    assert rows[0]["converted"] is True


def test_build_transitions_has_start_conversion_and_null():
    transitions = algorithms.build_transitions(journeys())
    assert transitions[(algorithms.START, "a")] == 2
    assert transitions[(algorithms.START, "b")] == 1
    assert transitions[("a", "b")] == 1
    assert transitions[("a", algorithms.NULL)] == 1
    assert transitions[("b", algorithms.CONVERSION)] == 2


def test_conversion_probability_matches_manual_absorption():
    transitions = algorithms.build_transitions(journeys())
    assert algorithms.conversion_probability(transitions, CHANNELS) == pytest.approx(2 / 3, abs=1e-9)


def test_conversion_probability_without_channels_is_zero():
    transitions = algorithms.build_transitions(journeys())
    assert algorithms.conversion_probability(transitions, CHANNELS, allowed=[]) == 0.0


def test_removal_effects_and_normalization():
    effects = algorithms.removal_effects(journeys(), CHANNELS)
    assert effects["b"] == pytest.approx(1.0)
    assert effects["a"] == pytest.approx(0.5)
    shares = algorithms.normalization(effects)
    assert shares["a"] == pytest.approx(1 / 3)
    assert shares["b"] == pytest.approx(2 / 3)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_last_click_only_counts_converted_paths():
    shares = algorithms.last_click_shares(journeys(), CHANNELS)
    assert shares == {"a": 0.0, "b": 1.0}


def test_shapley_sums_to_full_coalition_value():
    transitions = algorithms.build_transitions(journeys())
    values = algorithms.shapley_values(transitions, CHANNELS)
    assert values["a"] == pytest.approx(1 / 6, abs=1e-9)
    assert values["b"] == pytest.approx(1 / 2, abs=1e-9)
    assert sum(values.values()) == pytest.approx(2 / 3, abs=1e-9)


def test_shapley_of_single_channel_equals_its_value():
    transitions = algorithms.build_transitions(journeys())
    values = algorithms.shapley_values(transitions, ["b"])
    assert values["b"] == pytest.approx(1 / 3, abs=1e-9)


def test_path_length_distribution_rates():
    buckets = algorithms.path_length_distribution(journeys())
    first = next(item for item in buckets if item["key"] == "1")
    second = next(item for item in buckets if item["key"] == "2")
    assert first["count"] == 2 and first["converted"] == 1
    assert second["count"] == 1 and second["converted"] == 1
    assert second["rate"] == pytest.approx(1.0)


def _channels():
    return [
        {"code": "brand", "name": "品牌搜索", "type": "search", "monthly_cost": 60.0, "budget": 60.0},
        {"code": "content", "name": "内容种草", "type": "content", "monthly_cost": 80.0, "budget": 120.0},
        {"code": "live", "name": "达人直播", "type": "live", "monthly_cost": 120.0, "budget": 120.0},
    ]


def test_reallocate_keeps_total_and_clamps_delta():
    plan = algorithms.reallocate_budget(
        _channels(), {"brand": 2.0, "content": 3.0, "live": 0.5}, 300.0
    )
    assert sum(item["suggested_budget"] for item in plan) == pytest.approx(300.0, abs=0.01)
    for item in plan:
        assert abs(item["delta_pct"]) <= 30.0001
        assert item["suggested_budget"] >= 0


def test_reallocate_applies_brand_floor():
    channels = _channels()
    for item in channels:
        item["budget"] = 100.0
    channels[0]["type"] = "search"
    channels[0]["name"] = "品牌搜索"
    plan = algorithms.reallocate_budget(channels, {"brand": 0.01, "content": 5.0, "live": 3.0}, 300.0)
    brand = next(item for item in plan if item["code"] == "brand")
    assert brand["brand_floor"] is True
    assert brand["suggested_budget"] >= 36.0 - 0.01
    assert sum(item["suggested_budget"] for item in plan) == pytest.approx(300.0, abs=0.01)


def test_reallocate_empty_channels():
    assert algorithms.reallocate_budget([], {}, 100.0) == []


def test_summarize_is_deterministic():
    first = algorithms.summarize(journeys(), CHANNELS)
    second = algorithms.summarize(journeys(), CHANNELS)
    assert first == second
    assert first["conversion_rate"] == pytest.approx(2 / 3)
    assert first["revenue"] == 150.0


# --------------------------------------------------------------------------
# 身份合并：device -> member 重建路径
# --------------------------------------------------------------------------
ANONYMOUS_AND_MEMBER = [
    {"user_id": "anon-1", "device_id": "dev-1", "channel_code": "a", "touched_at": "2026-10-01T10:00:00"},
    {"user_id": "anon-1", "device_id": "dev-1", "channel_code": "b", "touched_at": "2026-10-01T11:00:00"},
    {"user_id": "m1", "device_id": "dev-1", "channel_code": "a", "touched_at": "2026-10-01T12:00:00"},
]
MEMBER_ORDERS = [{"user_id": "m1", "device_id": "dev-1", "amount": 80.0}]


def test_identity_links_merge_anonymous_and_member_paths():
    raw = algorithms.build_journeys(ANONYMOUS_AND_MEMBER, MEMBER_ORDERS)
    assert [journey["user_id"] for journey in raw] == ["anon-1", "m1"]
    assert raw[1]["path"] == ["a"] and raw[0]["converted"] is False

    merged = algorithms.build_journeys(ANONYMOUS_AND_MEMBER, MEMBER_ORDERS, {"dev-1": "m1"})
    assert len(merged) == 1
    assert merged[0]["user_id"] == "m1"
    assert merged[0]["path"] == ["a", "b", "a"]
    assert merged[0]["converted"] is True


def test_identity_merge_keeps_touchpoint_count_and_dedupes_duplicates():
    rows = [
        {"user_id": "anon-1", "device_id": "dev-1", "channel_code": "a", "touched_at": "2026-10-01T10:00:00"},
        # 匿名会话与登录后 SDK 各记一次同设备同渠道同一时刻
        {"user_id": "m1", "device_id": "dev-1", "channel_code": "a", "touched_at": "2026-10-01T10:00:00"},
        {"user_id": "m1", "device_id": "dev-1", "channel_code": "b", "touched_at": "2026-10-01T11:00:00"},
    ]
    merged = algorithms.build_journeys(rows, MEMBER_ORDERS, {"dev-1": "m1"})
    assert merged[0]["path"] == ["a", "b"]


def test_identity_merge_never_drops_touchpoints():
    merged = algorithms.build_journeys(ANONYMOUS_AND_MEMBER, MEMBER_ORDERS, {"dev-1": "m1"})
    assert sum(len(journey["path"]) for journey in merged) == len(ANONYMOUS_AND_MEMBER)


def test_identity_merge_is_noop_without_links():
    assert algorithms.build_journeys(ANONYMOUS_AND_MEMBER, MEMBER_ORDERS) == algorithms.build_journeys(
        ANONYMOUS_AND_MEMBER, MEMBER_ORDERS, {}
    )


def test_identity_merge_restores_full_path_and_channel_share():
    # 匿名首触 + 会员末跳：合并前后 markov 与 last-click 都会变化，合并后路径才完整
    rows = [
        {"user_id": "anon-1", "device_id": "dev-1", "channel_code": "a", "touched_at": "2026-10-01T10:00:00"},
        {"user_id": "m1", "device_id": "dev-1", "channel_code": "b", "touched_at": "2026-10-01T11:00:00"},
        {"user_id": "m2", "device_id": "dev-2", "channel_code": "b", "touched_at": "2026-10-02T11:00:00"},
    ]
    orders = [
        {"user_id": "m1", "device_id": "dev-1", "amount": 100.0},
        {"user_id": "m2", "device_id": "dev-2", "amount": 100.0},
    ]
    before = algorithms.summarize(algorithms.build_journeys(rows, orders), ["a", "b"])
    after = algorithms.summarize(algorithms.build_journeys(rows, orders, {"dev-1": "m1"}), ["a", "b"])
    assert before["users"] == 3 and after["users"] == 2
    assert before["conversions"] == after["conversions"] == 2
    assert after["last_click_share"] == {"a": 0.0, "b": 1.0}
    assert before["last_click_share"] == {"a": 0.0, "b": 1.0}
    # 合并后 a 成为完整路径的首触，移除效应才会被识别
    assert before["removal_effects"]["a"] == 0.0
    assert after["removal_effects"]["a"] > 0.0
