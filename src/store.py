"""只追加事件存储与跨事件不变量。

事件一旦写入不可修改；错误结论（如错误拼合）只能用新事件撤销。
每个 aggregate_id 的 version 从 1 严格递增，跨聚合引用与冻结等规则在写入时校验。
"""
from __future__ import annotations

from datetime import datetime

from .validator import _parse_time, validate_domain_event


class ValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("；".join(errors))


class EventStore:
    def __init__(self) -> None:
        self._events: list[dict] = []
        self._event_ids: set[str] = set()
        self._versions: dict[str, int] = {}
        # 已起取对象 / 已登记探方。
        self._objects: set[str] = set()
        self._units: set[str] = set()
        # 传感器校准窗口：sensor_id -> [(valid_from, valid_until)]。
        self._calibrations: dict[str, list[tuple[datetime, datetime]]] = {}
        self._calibration_expired: set[str] = set()
        # 关系/候选/告警/授权 聚合生命周期。
        self._relations: dict[str, dict] = {}
        self._proposals: dict[str, str] = {}
        self._alerts: dict[str, bool] = {}
        self._actions: dict[str, bool] = {}
        self._frozen: set[str] = set()
        self._accessioned: set[str] = set()
        # handoff_id -> {"role_done": set, "objects": set}。
        self._handoffs: dict[str, dict] = {}

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def append(self, record: dict) -> dict:
        errors = validate_domain_event(record)
        if errors:
            raise ValidationError(errors)

        event_id = record["event_id"]
        aggregate_id = record["aggregate_id"]
        if event_id in self._event_ids:
            raise ValidationError([f"event_id 重复：{event_id}"])

        expected_version = self._versions.get(aggregate_id, 0) + 1
        if record["version"] != expected_version:
            raise ValidationError(
                [f"{aggregate_id} 版本冲突：期望 {expected_version}，收到 {record['version']}"]
            )

        errors = self._cross_checks(record)
        if errors:
            raise ValidationError(errors)

        self._event_ids.add(event_id)
        self._versions[aggregate_id] = expected_version
        self._events.append(record)
        self._apply(record)
        return record

    def append_many(self, records: list[dict]) -> list[dict]:
        return [self.append(record) for record in records]

    def for_aggregate(self, aggregate_id: str) -> list[dict]:
        return [e for e in self._events if e["aggregate_id"] == aggregate_id]

    def is_frozen(self, object_id: str) -> bool:
        return object_id in self._frozen

    def handoff_status(self, handoff_id: str) -> dict | None:
        handoff = self._handoffs.get(handoff_id)
        if handoff is None:
            return None
        roles = set(handoff["roles"])
        return {
            "handoff_id": handoff_id,
            "signed_by": sorted(roles),
            "objects": sorted(handoff["objects"]),
            # 责任在交出与接收双方共同签署后才发生变化。
            "custody_transferred": {"relinquishing", "receiving"} <= roles,
        }

    # ---- 写入前的跨事件引用/状态校验 -------------------------------------

    def _cross_checks(self, record: dict) -> list[str]:
        event_type = record["event_type"]
        payload = record["payload"]
        errors: list[str] = []

        def require_object(key: str = "object_id") -> None:
            object_id = payload.get(key)
            if object_id not in self._objects:
                errors.append(f"对象尚未起取登记：{object_id}")

        if event_type == "STRATUM_RECORDED":
            if payload["unit_id"] not in self._units:
                errors.append(f"探方尚未登记：{payload['unit_id']}")

        elif event_type == "OBJECT_LIFTED":
            unit_id = payload["unit_id"]
            if unit_id not in self._units:
                errors.append(f"探方地层尚未登记：{unit_id}")

        elif event_type in ("IDENTITY_PROPOSED", "FRAGMENT_SPLIT", "JOIN_TENTATIVE",
                            "ENVIRONMENT_SAMPLED", "MEASUREMENT_BACKFILLED",
                            "NITROGEN_HUMIDITY_RECORDED", "PACKAGING_ANOMALY_DETECTED",
                            "ACQUISITION_RECORDED", "ALERT_RAISED"):
            if event_type == "FRAGMENT_SPLIT":
                require_object("parent_object_id")
                for fragment_id in payload["fragment_object_ids"]:
                    if fragment_id not in self._objects:
                        errors.append(f"拆分残片尚未起取登记：{fragment_id}")
            elif event_type == "JOIN_TENTATIVE":
                for fragment_id in payload["fragment_object_ids"]:
                    if fragment_id not in self._objects:
                        errors.append(f"暂拼残片尚未起取登记：{fragment_id}")
            else:
                require_object()
            if event_type == "IDENTITY_PROPOSED":
                figure_id = payload["candidate_figure_id"]
                if figure_id not in self._objects:
                    errors.append(f"候选俑体尚未登记：{figure_id}")
            if event_type in ("ENVIRONMENT_SAMPLED", "MEASUREMENT_BACKFILLED"):
                errors.extend(self._check_measurement(payload))

        elif event_type in ("DEVICE_CALIBRATION_EXPIRED",):
            if payload["sensor_id"] not in self._calibrations:
                errors.append(f"探头从未校准：{payload['sensor_id']}")

        elif event_type in ("JOIN_CONFIRMED", "JOIN_REVOKED"):
            relation = self._relations.get(payload["relation_id"])
            if relation is None:
                errors.append("只能确认/撤销已存在的拼合关系（先暂拼）")
            elif event_type == "JOIN_CONFIRMED":
                if relation["status"] != "tentative":
                    errors.append("只能确认暂拼状态的关系")
            elif relation["status"] == "revoked":
                errors.append("拼合关系已撤销，不能重复撤销")

        elif event_type == "IDENTITY_RESOLVED":
            if payload["proposal_id"] not in self._proposals:
                errors.append("候选身份不存在，无法裁定")
            elif self._proposals[payload["proposal_id"]] != "pending":
                errors.append("候选身份已裁定，不能重复裁定")

        elif event_type == "PACKAGE_PREPARED":
            # 封装本身不因其他对象异常而整体禁止；冻结只在交接/修复环节逐对象生效。
            for object_id in payload["object_ids"]:
                if object_id not in self._objects:
                    errors.append(f"封装对象尚未登记：{object_id}")

        elif event_type == "HANDOFF_SIGNED":
            errors.extend(self._check_handoff(payload))

        elif event_type in ("OBJECT_FROZEN", "OBJECT_UNFROZEN"):
            require_object()
            object_id = payload["object_id"]
            if event_type == "OBJECT_FROZEN" and object_id in self._frozen:
                errors.append("对象已处于冻结状态")
            if event_type == "OBJECT_UNFROZEN" and object_id not in self._frozen:
                errors.append("对象未冻结，不能解冻")

        elif event_type == "ALERT_ACKNOWLEDGED":
            alert_id = payload["alert_id"]
            if alert_id not in self._alerts:
                errors.append("告警不存在，无法签收处置")
            elif self._alerts[alert_id]:
                errors.append("告警已处置，不能重复签收")

        elif event_type == "ACTION_AUTHORIZED":
            require_object()
            if payload["object_id"] in self._frozen:
                errors.append("对象已冻结，禁止授权修复干预")

        elif event_type == "ACTION_EXECUTED":
            require_object()
            object_id = payload["object_id"]
            action_id = payload["action_id"]
            if action_id not in self._actions:
                errors.append("执行前必须先有人工授权")
            elif self._actions[action_id]:
                errors.append("干预已执行，不能重复执行")
            if object_id in self._frozen:
                errors.append("对象已冻结，禁止执行修复干预")

        elif event_type == "OBJECT_ACCESSIONED":
            require_object()
            if payload["object_id"] in self._accessioned:
                errors.append("对象已入库，不能重复入库")

        elif event_type == "LOCATION_ASSIGNED":
            require_object()

        elif event_type == "PUBLICATION_STATUS_CHANGED":
            require_object()

        elif event_type == "SENSITIVITY_CHANGED":
            scope_id = payload["scope_id"]
            if payload["scope"] == "object" and scope_id not in self._objects:
                errors.append(f"敏感对象尚未登记：{scope_id}")
            if payload["scope"] == "pit" and scope_id not in self._units:
                errors.append(f"敏感坑位/探方尚未登记：{scope_id}")

        return errors

    def _check_measurement(self, payload: dict) -> list[str]:
        sensor_id = payload["sensor_id"]
        windows = self._calibrations.get(sensor_id, [])
        if not windows:
            return [f"探头从未校准，测量不得入库：{sensor_id}"]
        measured_at = _parse_time(payload["measured_at"])
        within = any(start <= measured_at <= end for start, end in windows)
        quality = payload["quality"]
        if quality == "reliable" and not within:
            return ["校准已失效的数据不得冒充可靠测量（quality 不能为 reliable）"]
        if quality == "calibration_expired" and within:
            return ["测量时间仍在校准有效期内，不应标记 calibration_expired"]
        return []

    def _check_handoff(self, payload: dict) -> list[str]:
        handoff_id = payload["handoff_id"]
        role = payload["signer_role"]
        object_ids = payload["object_ids"]
        for object_id in object_ids:
            if object_id not in self._objects:
                return [f"交接对象尚未登记：{object_id}"]
            if object_id in self._frozen:
                return [f"对象已冻结，禁止交接：{object_id}"]
        handoff = self._handoffs.get(handoff_id)
        if handoff is None:
            if role != "relinquishing":
                return ["新交接必须先由交出方（relinquishing）签署"]
        else:
            if role in handoff["roles"]:
                return [f"{role} 已签署，不能重复签署"]
            if role != "receiving":
                return ["交接须由接收方（receiving）完成副署"]
            if set(object_ids) != handoff["objects"]:
                return ["接收方签署的对象集合必须与交出方一致"]
        return []

    # ---- 写入后的状态推进 -------------------------------------------------

    def _apply(self, record: dict) -> None:
        event_type = record["event_type"]
        p = record["payload"]

        if event_type == "UNIT_REGISTERED":
            self._units.add(record["aggregate_id"])
        elif event_type == "OBJECT_LIFTED":
            self._objects.add(record["aggregate_id"])
        elif event_type == "DEVICE_CALIBRATED":
            self._calibrations.setdefault(p["sensor_id"], []).append(
                (_parse_time(p["valid_from"]), _parse_time(p["valid_until"]))
            )
            self._calibration_expired.discard(p["sensor_id"])
        elif event_type == "DEVICE_CALIBRATION_EXPIRED":
            self._calibration_expired.add(p["sensor_id"])
        elif event_type == "JOIN_TENTATIVE":
            self._relations[record["aggregate_id"]] = {
                "status": "tentative",
                "fragments": tuple(p["fragment_object_ids"]),
            }
        elif event_type == "JOIN_CONFIRMED":
            self._relations[p["relation_id"]]["status"] = "confirmed"
        elif event_type == "JOIN_REVOKED":
            # 撤销不删除历史，只把关系推进到 revoked（事件本身就是新关系记录）。
            self._relations[p["relation_id"]]["status"] = "revoked"
        elif event_type == "IDENTITY_PROPOSED":
            self._proposals[record["aggregate_id"]] = "pending"
        elif event_type == "IDENTITY_RESOLVED":
            self._proposals[p["proposal_id"]] = p["resolution"]
        elif event_type == "OBJECT_FROZEN":
            self._frozen.add(p["object_id"])
        elif event_type == "OBJECT_UNFROZEN":
            self._frozen.discard(p["object_id"])
        elif event_type == "ALERT_RAISED":
            self._alerts[record["aggregate_id"]] = False
        elif event_type == "ALERT_ACKNOWLEDGED":
            self._alerts[p["alert_id"]] = True
        elif event_type == "ACTION_AUTHORIZED":
            self._actions[record["aggregate_id"]] = False
        elif event_type == "ACTION_EXECUTED":
            self._actions[p["action_id"]] = True
        elif event_type == "OBJECT_ACCESSIONED":
            self._accessioned.add(p["object_id"])
        elif event_type in ("HANDOFF_SIGNED",):
            handoff = self._handoffs.setdefault(
                p["handoff_id"], {"roles": set(), "objects": set()}
            )
            handoff["roles"].add(p["signer_role"])
            handoff["objects"].update(p["object_ids"])
