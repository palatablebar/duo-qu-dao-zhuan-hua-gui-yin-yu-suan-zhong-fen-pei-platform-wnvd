"""设备 × 会员身份合并规则（纯函数，无 IO、无随机、可单测）。

语义：
- 合并事件按到达时间（arrived_at，同一时刻再按 event_key）排序处理，先到先得；
- 某设备第一条合法事件建立 device -> member 关联，后续同会员事件视为重复关联（不新增）；
- 同一设备后续绑定另一个会员 -> 记为 conflict，不覆盖先到关联，并保留被拒绝的会员号；
- 事件号缺失/格式非法 -> failed，不参与关联；
- 同一 event_key 重放由入库层去重，不会进入本函数重复计算。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

ACCEPTED = "accepted"
CONFLICT = "conflict"
FAILED = "failed"

MERGE_STATUS_LABELS: dict[str, str] = {
    ACCEPTED: "已受理",
    CONFLICT: "冲突",
    FAILED: "失败",
}

REASON_LINK_CREATED = "link_created"
REASON_LINK_EXISTS = "link_exists"
REASON_DEVICE_BOUND = "device_bound_to_other_member"
REASON_INVALID_EVENT_KEY = "invalid_event_key"
REASON_INVALID_DEVICE = "invalid_device_id"
REASON_INVALID_MEMBER = "invalid_member_id"

MERGE_REASON_LABELS: dict[str, str] = {
    REASON_LINK_CREATED: "新建设备关联",
    REASON_LINK_EXISTS: "同会员重复关联，未新增",
    REASON_DEVICE_BOUND: "设备已关联其他会员，保留先到关联",
    REASON_INVALID_EVENT_KEY: "事件号缺失或格式不合法",
    REASON_INVALID_DEVICE: "设备号缺失或格式不合法",
    REASON_INVALID_MEMBER: "会员号缺失或格式不合法",
}

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def valid_event_key(value: str | None) -> bool:
    return bool(value) and bool(_KEY_PATTERN.match(str(value)))


def valid_device_id(value: str | None) -> bool:
    return bool(value) and bool(_ID_PATTERN.match(str(value)))


def valid_member_id(value: str | None) -> bool:
    return bool(value) and bool(_ID_PATTERN.match(str(value)))


def validate_merge(event: dict) -> list[str]:
    """返回失败原因码列表；空列表代表事件可参与关联。

    key_valid=False 表示入库时事件号原本缺失（服务层用占位键落库留痕）。
    """
    reasons: list[str] = []
    if event.get("key_valid") is False or not valid_event_key(event.get("event_key")):
        reasons.append(REASON_INVALID_EVENT_KEY)
    if not valid_device_id(event.get("device_id")):
        reasons.append(REASON_INVALID_DEVICE)
    if not valid_member_id(event.get("member_id")):
        reasons.append(REASON_INVALID_MEMBER)
    return reasons


def event_order_key(event: dict) -> tuple[str, str]:
    return (str(event.get("arrived_at") or ""), str(event.get("event_key") or ""))


def resolve_merges(
    events: Iterable[dict],
    existing_links: dict[str, str] | None = None,
    existing_first_events: dict[str, str] | None = None,
) -> tuple[dict[str, dict], dict[str, str], dict[str, str]]:
    """按到达顺序计算每个合并事件的结果，并返回 device -> member 关联。

    events: [{"event_key","device_id","member_id","arrived_at"}]
    existing_links: 历史上已生效的 device -> member（跨批次），先到者优先
    返回:
      decisions: event_key -> {status, reason, kept_member_id, kept_event_key}
      links: device_id -> member_id（含历史关联与本次新建立）
      first_events: device_id -> 建立关联的事件号
    """
    decisions: dict[str, dict] = {}
    links: dict[str, str] = dict(existing_links or {})
    first_event: dict[str, str] = dict(existing_first_events or {})

    for event in sorted(events, key=event_order_key):
        key = str(event.get("event_key") or "")
        if key and key in decisions:
            continue  # 同一事件重放：保留首次决定，天然幂等
        device = str(event.get("device_id") or "").strip()
        member = str(event.get("member_id") or "").strip()
        errors = validate_merge(event)
        if errors:
            decisions[key] = {
                "status": FAILED,
                "reason": errors[0],
                "kept_member_id": None,
                "kept_event_key": None,
            }
            continue
        if device not in links:
            links[device] = member
            first_event[device] = key
            decisions[key] = {
                "status": ACCEPTED,
                "reason": REASON_LINK_CREATED,
                "kept_member_id": member,
                "kept_event_key": key,
            }
        elif links[device] == member:
            decisions[key] = {
                "status": ACCEPTED,
                "reason": REASON_LINK_EXISTS,
                "kept_member_id": member,
                "kept_event_key": first_event.get(device, key),
            }
        else:
            decisions[key] = {
                "status": CONFLICT,
                "reason": REASON_DEVICE_BOUND,
                "kept_member_id": links[device],
                "kept_event_key": first_event.get(device),
            }
    return decisions, links, first_event
