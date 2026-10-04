"""从 contracts/domain.schema.json 加载事件目录与约定，避免代码与契约双份维护。"""
from __future__ import annotations
import json
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts" / "domain.schema.json"


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


SCHEMA = _load_schema()

EVENT_TYPES: tuple[str, ...] = tuple(
    SCHEMA["properties"]["event_type"]["enum"]
)
AGGREGATE_TYPES: tuple[str, ...] = tuple(
    SCHEMA["properties"]["aggregate_type"]["enum"]
)

# 事件 -> 聚合类型 / payload 必填字段，解析自 allOf 的 if/then 子句。
AGGREGATE_BY_EVENT: dict[str, str] = {}
PAYLOAD_REQUIRED_BY_EVENT: dict[str, tuple[str, ...]] = {}

for clause in SCHEMA.get("allOf", []):
    event_type = clause["if"]["properties"]["event_type"]["const"]
    then = clause["then"]
    aggregate_constraint = then["properties"]["aggregate_type"]
    # 多数事件聚合唯一（const）；个别事件允许多个聚合（enum）。
    if "const" in aggregate_constraint:
        AGGREGATE_BY_EVENT[event_type] = aggregate_constraint["const"]
    else:
        AGGREGATE_BY_EVENT[event_type] = aggregate_constraint["enum"]
    PAYLOAD_REQUIRED_BY_EVENT[event_type] = tuple(
        then["properties"].get("payload", {}).get("required", [])
    )

# payload 中的标识字段，取值必须与事件所在 aggregate_id 一致。
AGGREGATE_ID_FIELD_BY_EVENT: dict[str, str] = {
    "DEVICE_CALIBRATED": "sensor_id",
    "DEVICE_CALIBRATION_EXPIRED": "sensor_id",
    "IDENTITY_RESOLVED": "proposal_id",
    "JOIN_CONFIRMED": "relation_id",
    "JOIN_REVOKED": "relation_id",
    "PACKAGE_PREPARED": "package_id",
    "ALERT_ACKNOWLEDGED": "alert_id",
    "ACTION_EXECUTED": "action_id",
    "HANDOFF_SIGNED": "handoff_id",
    "OBJECT_FROZEN": "object_id",
    "OBJECT_UNFROZEN": "object_id",
    "OBJECT_ACCESSIONED": "object_id",
    "LOCATION_ASSIGNED": "object_id",
    "PUBLICATION_STATUS_CHANGED": "object_id",
    "SENSITIVITY_CHANGED": "scope_id",
}

# 事件 payload 中以 object_id 指代发掘对象的事件，要求对象已起取登记。
REFERENCES_OBJECT_EVENTS: frozenset[str] = frozenset(
    event
    for event, fields in PAYLOAD_REQUIRED_BY_EVENT.items()
    if "object_id" in fields
)
