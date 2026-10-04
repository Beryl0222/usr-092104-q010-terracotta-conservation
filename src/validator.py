"""校验领域事件信封的基础字段与静态领域规则。

信封校验 ``validate_event`` 保持原有行为，供现场数据入口直接使用；
``validate_domain_event`` 在其之上追加事件目录、payload 约定与跨字段规则。
需要结合既有事件流才能判断的规则（版本、引用、校准窗口、冻结等）见 ``store``。
"""
from __future__ import annotations

from datetime import datetime

from .contract import (
    AGGREGATE_BY_EVENT,
    AGGREGATE_ID_FIELD_BY_EVENT,
    AGGREGATE_TYPES,
    EVENT_TYPES,
    PAYLOAD_REQUIRED_BY_EVENT,
)

REQUIRED = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "summary",
)

_TIME_FIELDS = ("occurred_at",)
_PAYLOAD_TIME_FIELDS = ("measured_at", "acquired_at", "detected_at", "valid_from", "valid_until", "expired_at")


def validate_event(record: dict) -> list[str]:
    """事件信封基础字段校验（现场入口的既有行为，保持兼容）。"""
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    return errors


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def validate_domain_event(record: object) -> list[str]:
    """完整静态校验：信封 + 事件目录 + payload 必填 + 跨字段规则。"""
    if not isinstance(record, dict):
        return ["事件必须是对象"]
    errors = list(validate_event(record))

    event_type = record.get("event_type")
    aggregate_type = record.get("aggregate_type")

    if event_type not in EVENT_TYPES:
        errors.append(f"未知 event_type：{event_type}")
    if aggregate_type not in AGGREGATE_TYPES:
        errors.append(f"未知 aggregate_type：{aggregate_type}")

    occurred_at = _parse_time(record.get("occurred_at"))
    if "occurred_at" in record and occurred_at is None:
        errors.append("occurred_at 必须是 ISO-8601 日期时间")

    if event_type not in AGGREGATE_BY_EVENT:
        return errors

    expected_aggregate = AGGREGATE_BY_EVENT[event_type]
    allowed = (expected_aggregate,) if isinstance(expected_aggregate, str) else tuple(expected_aggregate)
    if aggregate_type not in allowed:
        errors.append(f"{event_type} 的 aggregate_type 必须是 {'/'.join(allowed)}")

    payload = record.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload 必须是对象")
        return errors

    for field in PAYLOAD_REQUIRED_BY_EVENT[event_type]:
        value = payload.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"payload 缺少必填字段：{field}")

    for field in _PAYLOAD_TIME_FIELDS:
        if field in payload and _parse_time(payload[field]) is None:
            errors.append(f"payload.{field} 必须是 ISO-8601 日期时间")

    id_field = AGGREGATE_ID_FIELD_BY_EVENT.get(event_type)
    if id_field and id_field in payload and payload[id_field] != record.get("aggregate_id"):
        errors.append(f"payload.{id_field} 必须与 aggregate_id 一致")

    errors.extend(_validate_event_rules(event_type, payload, occurred_at))
    return errors


def _validate_event_rules(event_type: str, payload: dict, occurred_at: datetime | None) -> list[str]:
    errors: list[str] = []

    if event_type == "FRAGMENT_SPLIT":
        fragments = payload.get("fragment_object_ids")
        parent = payload.get("parent_object_id")
        if not isinstance(fragments, list) or not fragments:
            errors.append("fragment_object_ids 必须是非空数组")
        elif parent in fragments:
            errors.append("拆分残片不能与父对象相同")

    if event_type == "JOIN_TENTATIVE":
        fragments = payload.get("fragment_object_ids")
        if not isinstance(fragments, list) or len(fragments) < 2:
            errors.append("暂拼关系至少涉及两块残片")

    if event_type in ("ENVIRONMENT_SAMPLED", "MEASUREMENT_BACKFILLED"):
        quality = payload.get("quality")
        measured_at = _parse_time(payload.get("measured_at"))
        if event_type == "MEASUREMENT_BACKFILLED":
            # 断线补数：按采集时间进入，并必须带补数质量标记。
            if quality != "backfilled":
                errors.append("补数必须带 quality=backfilled 质量标记")
            if occurred_at and measured_at and measured_at >= occurred_at:
                errors.append("补数 measured_at 必须早于入库 occurred_at")
        elif quality == "backfilled":
            errors.append("补数只能通过 MEASUREMENT_BACKFILLED 进入")

    if event_type == "DEVICE_CALIBRATED":
        valid_from = _parse_time(payload.get("valid_from"))
        valid_until = _parse_time(payload.get("valid_until"))
        if valid_from and valid_until and valid_until <= valid_from:
            errors.append("校准 valid_until 必须晚于 valid_from")

    if event_type == "HANDOFF_SIGNED":
        role = payload.get("signer_role")
        if role not in ("relinquishing", "receiving"):
            errors.append("交接签署 signer_role 必须是 relinquishing 或 receiving")
        if not isinstance(payload.get("object_ids"), list) or not payload["object_ids"]:
            errors.append("交接必须包含至少一个对象")

    if event_type == "ALERT_RAISED":
        if payload.get("category") not in ("humidity_threshold", "device_inaccurate", "packaging_anomaly"):
            errors.append("告警 category 不在约定范围")

    if event_type == "ACTION_AUTHORIZED":
        # 阈值告警只能促成人工处置：清理/加固必须由修复人员人工确认，禁止自动授权。
        if payload.get("action_type") in ("cleaning", "reinforcement") and payload.get("human_confirmed") is not True:
            errors.append("清理/加固授权必须人工确认（human_confirmed=true），告警不得自动决定")

    if event_type == "OBJECT_LIFTED":
        if payload.get("object_kind") not in ("fragment", "figure", "sample"):
            errors.append("object_kind 不在约定范围")

    return errors
