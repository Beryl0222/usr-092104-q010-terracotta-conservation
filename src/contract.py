"""秦俑现场保护交接领域契约：事件目录、稳定枚举与信封/载荷校验。

事件只追加（append-only）。所有跨系统交换的事件都必须通过 validate_event，
校验规则同时服务于仓库现有事件入口与新的命令式服务。
"""
from __future__ import annotations

from datetime import datetime

# ── 聚合类型 ───────────────────────────────────────────────────────────
AGG_EXCAVATION_UNIT = "excavation_unit"        # 探方（含地层）
AGG_OBJECT = "excavated_object"                # 出土对象：俑体或残片
AGG_CANDIDATE = "candidate_identity"           # 对象候选身份
AGG_RELATION = "fragment_relation"             # 残片关系（拆分/暂拼/拼合/撤销）
AGG_SERIES = "environment_series"              # 环境时序
AGG_INSTRUMENT = "instrument"                  # 探头/采集设备
AGG_ALERT = "monitoring_alert"                 # 阈值与异常告警
AGG_PACKAGE = "sealed_package"                 # 充氮保湿/运输封装
AGG_ACTION = "conservation_action"             # 修复干预
AGG_HANDOFF = "custody_handoff"                # 责任交接
AGG_STORAGE = "storage_location"               # 入库位置

AGGREGATE_TYPES = frozenset({
    AGG_EXCAVATION_UNIT, AGG_OBJECT, AGG_CANDIDATE, AGG_RELATION, AGG_SERIES,
    AGG_INSTRUMENT, AGG_ALERT, AGG_PACKAGE, AGG_ACTION, AGG_HANDOFF, AGG_STORAGE,
})

# ── 事件类型（新增值只允许追加，保持既有事件兼容） ────────────────────
OBJECT_LIFTED = "OBJECT_LIFTED"
TRENCH_OPENED = "TRENCH_OPENED"
STRATUM_RECORDED = "STRATUM_RECORDED"
FRAGMENT_DETACHED = "FRAGMENT_DETACHED"
EXAMINATION_RECORDED = "EXAMINATION_RECORDED"
CANDIDATE_PROPOSED = "CANDIDATE_IDENTITY_PROPOSED"
CANDIDATE_CONFIRMED = "CANDIDATE_IDENTITY_CONFIRMED"
CANDIDATE_REJECTED = "CANDIDATE_IDENTITY_REJECTED"
RELATION_OPENED = "RELATION_OPENED"
RELATION_CONFIRMED = "RELATION_CONFIRMED"
INSTRUMENT_REGISTERED = "INSTRUMENT_REGISTERED"
CALIBRATION_RECORDED = "CALIBRATION_RECORDED"
ENVIRONMENT_SAMPLED = "ENVIRONMENT_SAMPLED"
LIMITS_SET = "MONITORING_LIMITS_SET"
ALERT_RAISED = "ALERT_RAISED"
ALERT_ACKNOWLEDGED = "ALERT_ACKNOWLEDGED"
ALERT_RESOLVED = "ALERT_RESOLVED"
HOLD_PLACED = "OBJECT_HOLD_PLACED"
HOLD_RELEASED = "OBJECT_HOLD_RELEASED"
PACKAGE_PREPARED = "PACKAGE_PREPARED"
PACKAGE_SEALED = "PACKAGE_SEALED"
PACKAGE_N2_REFRESHED = "PACKAGE_N2_REFRESHED"
PACKAGE_STATUS_RECORDED = "PACKAGE_STATUS_RECORDED"
ACTION_PROPOSED = "ACTION_PROPOSED"
ACTION_AUTHORIZED = "ACTION_AUTHORIZED"
ACTION_COMPLETED = "ACTION_COMPLETED"
HANDOFF_PREPARED = "HANDOFF_PREPARED"
HANDOFF_SIGNED = "HANDOFF_SIGNED"
STORAGE_REGISTERED = "STORAGE_LOCATION_REGISTERED"
OBJECT_ACCESSIONED = "OBJECT_ACCESSIONED"

EVENT_TYPES = frozenset({
    OBJECT_LIFTED, TRENCH_OPENED, STRATUM_RECORDED, FRAGMENT_DETACHED,
    EXAMINATION_RECORDED, CANDIDATE_PROPOSED, CANDIDATE_CONFIRMED, CANDIDATE_REJECTED,
    RELATION_OPENED, RELATION_CONFIRMED, INSTRUMENT_REGISTERED, CALIBRATION_RECORDED,
    ENVIRONMENT_SAMPLED, LIMITS_SET, ALERT_RAISED, ALERT_ACKNOWLEDGED, ALERT_RESOLVED,
    HOLD_PLACED, HOLD_RELEASED, PACKAGE_PREPARED, PACKAGE_SEALED,
    PACKAGE_N2_REFRESHED, PACKAGE_STATUS_RECORDED, ACTION_PROPOSED, ACTION_AUTHORIZED,
    ACTION_COMPLETED, HANDOFF_PREPARED, HANDOFF_SIGNED, STORAGE_REGISTERED,
    OBJECT_ACCESSIONED,
})

# 事件与聚合的归属
EVENT_AGGREGATE = {
    TRENCH_OPENED: AGG_EXCAVATION_UNIT, STRATUM_RECORDED: AGG_EXCAVATION_UNIT,
    OBJECT_LIFTED: AGG_OBJECT, FRAGMENT_DETACHED: AGG_OBJECT,
    HOLD_PLACED: AGG_OBJECT, HOLD_RELEASED: AGG_OBJECT, OBJECT_ACCESSIONED: AGG_OBJECT,
    EXAMINATION_RECORDED: AGG_OBJECT,
    CANDIDATE_PROPOSED: AGG_CANDIDATE, CANDIDATE_CONFIRMED: AGG_CANDIDATE,
    CANDIDATE_REJECTED: AGG_CANDIDATE,
    RELATION_OPENED: AGG_RELATION, RELATION_CONFIRMED: AGG_RELATION,
    ENVIRONMENT_SAMPLED: AGG_SERIES, LIMITS_SET: AGG_OBJECT,
    INSTRUMENT_REGISTERED: AGG_INSTRUMENT, CALIBRATION_RECORDED: AGG_INSTRUMENT,
    ALERT_RAISED: AGG_ALERT, ALERT_ACKNOWLEDGED: AGG_ALERT, ALERT_RESOLVED: AGG_ALERT,
    PACKAGE_PREPARED: AGG_PACKAGE, PACKAGE_SEALED: AGG_PACKAGE,
    PACKAGE_N2_REFRESHED: AGG_PACKAGE, PACKAGE_STATUS_RECORDED: AGG_PACKAGE,
    ACTION_PROPOSED: AGG_ACTION, ACTION_AUTHORIZED: AGG_ACTION, ACTION_COMPLETED: AGG_ACTION,
    HANDOFF_PREPARED: AGG_HANDOFF, HANDOFF_SIGNED: AGG_HANDOFF,
    STORAGE_REGISTERED: AGG_STORAGE,
}

# ── 稳定枚举 ───────────────────────────────────────────────────────────
ROLE_SYSTEM = "system"
ROLE_DUTY_OFFICER = "duty_officer"   # 文保值班人员
ROLE_OFFICER = ROLE_DUTY_OFFICER     # 兼容短名
ROLE_CONSERVATOR = "conservator"     # 修复人员
ROLE_REGISTRAR = "registrar"         # 库房管理
ROLE_RESEARCHER = "researcher"       # 研究者
ROLE_PUBLIC = "public"               # 公众
STAFF_ROLES = frozenset({ROLE_DUTY_OFFICER, ROLE_CONSERVATOR, ROLE_REGISTRAR})
ROLES = frozenset({ROLE_SYSTEM, ROLE_DUTY_OFFICER, ROLE_CONSERVATOR,
                   ROLE_REGISTRAR, ROLE_RESEARCHER, ROLE_PUBLIC})

SCOPE_SENSITIVE_LOCATION = "sensitive_locations"   # 可见敏感坑位坐标
SCOPE_UNPUBLISHED = "unpublished_finds"            # 可见未发布发现

OBJECT_KIND_FIGURE = "figure"
OBJECT_KIND_FRAGMENT = "fragment"
OBJECT_KINDS = frozenset({OBJECT_KIND_FIGURE, OBJECT_KIND_FRAGMENT})

PUBLICATION_UNPUBLISHED = "unpublished"
PUBLICATION_PUBLISHED = "published"
PUBLICATION_STATES = frozenset({PUBLICATION_UNPUBLISHED, PUBLICATION_PUBLISHED})

# 检测数据质量标记
QUALITY_OK = "ok"                          # 在校准有效期内的实时测量
QUALITY_BACKFILL = "measured_backfill"     # 断线补数：真实测量，按采集时间归位
QUALITY_INTERPOLATED = "interpolated"      # 断线补数：插值，非原始测量
QUALITY_UNCALIBRATED = "uncalibrated"      # 校准已失效，不得当作可靠测量
QUALITY_SUSPECT = "suspect"                # 其他存疑
QUALITIES = frozenset({QUALITY_OK, QUALITY_BACKFILL, QUALITY_INTERPOLATED,
                       QUALITY_UNCALIBRATED, QUALITY_SUSPECT})
# 只有这两类数据可以驱动阈值告警
RELIABLE_QUALITIES = frozenset({QUALITY_OK, QUALITY_BACKFILL})

RELATION_DETACHED = "detached"
RELATION_TENTATIVE = "tentative_join"
RELATION_REVOKED = "join_revoked"
RELATION_KINDS = frozenset({RELATION_DETACHED, RELATION_TENTATIVE, RELATION_REVOKED})

ALERT_HUMIDITY = "humidity_breach"
ALERT_INSTRUMENT = "instrument_uncalibrated"
ALERT_PACKAGE = "package_anomaly"
ALERT_KINDS = frozenset({ALERT_HUMIDITY, ALERT_INSTRUMENT, ALERT_PACKAGE})

ACTION_CLEANING = "cleaning"
ACTION_REINFORCEMENT = "reinforcement"
ACTION_DESALINATION = "desalination"
ACTION_CONSOLIDATION = "consolidation"
ACTION_OTHER = "other"
ACTION_KINDS = frozenset({ACTION_CLEANING, ACTION_REINFORCEMENT,
                          ACTION_DESALINATION, ACTION_CONSOLIDATION, ACTION_OTHER})

EXAM_ULTRASONIC = "ultrasonic"
EXAM_SCAN3D = "scan3d"
EXAM_VISUAL = "visual"
EXAM_PIGMENT = "pigment"
EXAM_KINDS = frozenset({EXAM_ULTRASONIC, EXAM_SCAN3D, EXAM_VISUAL, EXAM_PIGMENT})

METRIC_HUMIDITY = "humidity"
METRICS = frozenset({METRIC_HUMIDITY, "temperature", "nitrogen_purity"})

ENVELOPE_REQUIRED = ("event_id", "event_type", "aggregate_type", "aggregate_id",
                     "occurred_at", "version", "summary")

# 每类事件的载荷必填字段
PAYLOAD_REQUIRED: dict[str, tuple[str, ...]] = {
    TRENCH_OPENED: ("trench_code", "sensitive", "grid_coordinates"),
    STRATUM_RECORDED: ("stratum_id", "depth_top", "depth_bottom", "period_label"),
    OBJECT_LIFTED: ("object_kind", "trench_id", "stratum_id", "publication_state"),
    FRAGMENT_DETACHED: ("fragment_id", "source_object_id", "figure_id", "reason"),
    EXAMINATION_RECORDED: ("examination_id", "object_id", "kind", "examined_at"),
    CANDIDATE_PROPOSED: ("object_id", "proposed_form", "proposed_by"),
    CANDIDATE_CONFIRMED: ("confirmed_by",),
    CANDIDATE_REJECTED: ("rejected_by", "reason"),
    RELATION_OPENED: ("relation_kind",),
    RELATION_CONFIRMED: ("confirmed_by",),
    INSTRUMENT_REGISTERED: ("instrument_code", "metric", "valid_from", "valid_to"),
    CALIBRATION_RECORDED: ("valid_from", "valid_to", "cert_id"),
    ENVIRONMENT_SAMPLED: ("series_id", "object_id", "instrument_id", "metric",
                          "value", "collected_at", "quality"),
    LIMITS_SET: ("object_id", "limits"),
    ALERT_RAISED: ("alert_kind", "affected_object_ids", "raised_by"),
    ALERT_ACKNOWLEDGED: ("acknowledged_by",),
    ALERT_RESOLVED: ("resolved_by", "resolution"),
    HOLD_PLACED: ("object_id", "reason", "placed_by"),
    HOLD_RELEASED: ("hold_id", "released_by"),
    PACKAGE_PREPARED: ("package_code", "object_ids", "humidity_limits"),
    PACKAGE_SEALED: ("sealed_by",),
    PACKAGE_N2_REFRESHED: ("refreshed_by", "nitrogen_purity"),
    PACKAGE_STATUS_RECORDED: ("seal_integrity", "recorded_by"),
    ACTION_PROPOSED: ("action_kind", "target_object_id", "proposed_by", "rationale"),
    ACTION_AUTHORIZED: ("authorizer_id",),
    ACTION_COMPLETED: ("completed_by", "result"),
    HANDOFF_PREPARED: ("handoff_code", "from_party", "to_party", "object_ids"),
    HANDOFF_SIGNED: ("releaser", "receiver"),
    STORAGE_REGISTERED: ("location_code", "area"),
    OBJECT_ACCESSIONED: ("object_id", "storage_location_id", "accession_code", "accessioned_by"),
}

# 载荷字段允许值
FIELD_ENUMS = {
    "object_kind": OBJECT_KINDS,
    "publication_state": PUBLICATION_STATES,
    "quality": QUALITIES,
    "relation_kind": RELATION_KINDS,
    "alert_kind": ALERT_KINDS,
    "action_kind": ACTION_KINDS,
    "kind": EXAM_KINDS,
    "metric": METRICS,
    "seal_integrity": frozenset({"ok", "leaking"}),
}

PAYLOAD_ENUMS_BY_EVENT = {
    OBJECT_LIFTED: {"object_kind", "publication_state"},
    FRAGMENT_DETACHED: set(),
    EXAMINATION_RECORDED: {"kind"},
    ENVIRONMENT_SAMPLED: {"quality", "metric"},
    RELATION_OPENED: {"relation_kind"},
    ALERT_RAISED: {"alert_kind"},
    PACKAGE_STATUS_RECORDED: {"seal_integrity"},
    ACTION_PROPOSED: {"action_kind"},
    INSTRUMENT_REGISTERED: {"metric"},
}


def parse_dt(value: str) -> datetime:
    """解析 ISO8601 时间，要求带时区，避免现场多设备时间错位。"""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("时间必须带时区偏移")
    return dt


def validate_event(record: dict) -> list[str]:
    """校验事件信封与按事件类型校验载荷，返回中文错误列表（空列表表示通过）。"""
    errors: list[str] = [f"缺少字段：{name}" for name in ENVELOPE_REQUIRED if name not in record]
    if errors:
        return errors

    if not isinstance(record["event_id"], str) or not record["event_id"].strip():
        errors.append("event_id 必须是非空字符串")
    if record["event_type"] not in EVENT_TYPES:
        errors.append(f"未知 event_type：{record['event_type']}")
    if record["aggregate_type"] not in AGGREGATE_TYPES:
        errors.append(f"未知 aggregate_type：{record['aggregate_type']}")
    expected_agg = EVENT_AGGREGATE.get(record["event_type"])
    if expected_agg and record.get("aggregate_type") != expected_agg:
        errors.append(f"{record['event_type']} 的 aggregate_type 必须是 {expected_agg}")
    if not isinstance(record["aggregate_id"], str) or not record["aggregate_id"].strip():
        errors.append("aggregate_id 必须是非空字符串")
    version = record["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("version 必须是正整数")
    if not isinstance(record.get("summary"), str) or not record["summary"].strip():
        errors.append("summary 必须是非空字符串")
    try:
        parse_dt(record["occurred_at"])
    except (ValueError, TypeError):
        errors.append("occurred_at 必须是带时区的 ISO8601 时间")

    actor = record.get("actor")
    if actor is not None:
        if not isinstance(actor, dict) or not actor.get("id") or not actor.get("role"):
            errors.append("actor 必须包含 id 与 role")
        elif actor["role"] not in ROLES:
            errors.append(f"未知角色：{actor['role']}")

    payload = record.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        errors.append("payload 必须是对象")
        return errors
    event_type = record["event_type"]
    if event_type in PAYLOAD_REQUIRED:
        for name in PAYLOAD_REQUIRED[event_type]:
            if name not in payload or payload[name] in (None, ""):
                errors.append(f"{event_type} 载荷缺少字段：{name}")
        for name in PAYLOAD_ENUMS_BY_EVENT.get(event_type, ()):  # type: ignore[arg-type]
            if name in payload and payload[name] not in FIELD_ENUMS[name]:
                errors.append(f"字段 {name} 取值非法：{payload[name]}")
    return errors
