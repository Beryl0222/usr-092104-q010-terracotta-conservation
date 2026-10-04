"""分级授权视图。

- conservator（修复/文保人员）：全量，含未发布、敏感坑位与运营字段。
- researcher（研究者）：可见未发布资料用于研究；敏感坑位的精确位置脱敏。
- public（公众/普通接口）：仅已发布且非敏感发现；未发布发现与敏感坑位不随普通接口泄露。

脱敏只影响投影输出，不改动事件存储。
"""
from __future__ import annotations

from typing import Literal

Role = Literal["conservator", "researcher", "public"]

# 研究者视角下，敏感坑位相关的精确定位字段需要脱敏。
_PIT_LOCATION_FIELDS = ("unit_id", "layer", "location_id")
# 公众视角下不暴露运营/处置字段。
_OPERATIONAL_FIELDS = ("frozen", "sensitivity", "publication_status")


class AccessPolicy:
    def __init__(self, projection, role: Role):
        self.projection = projection
        if role not in ("conservator", "researcher", "public"):
            raise ValueError(f"未知角色：{role}")
        self.role = role

    def _unit_sensitivity(self, object_view: dict) -> str:
        unit = self.projection.units.get(object_view.get("unit_id"))
        return unit.get("sensitivity", "normal") if unit else "normal"

    def can_see_object(self, object_view: dict) -> bool:
        if self.role == "conservator":
            return True
        sensitivity = object_view.get("sensitivity", "normal")
        if self.role == "public":
            # 普通接口：未发布发现、敏感坑位发现一律不出现。
            return (
                object_view.get("publication_status") == "published"
                and sensitivity == "normal"
                and self._unit_sensitivity(object_view) == "normal"
            )
        # researcher：经授权可见未发布研究资料（敏感坑位位置另行脱敏）。
        return True

    def view_object(self, object_id: str) -> dict | None:
        object_view = self.projection.objects.get(object_id)
        if object_view is None or not self.can_see_object(object_view):
            return None
        view = dict(object_view)
        if self.role == "public":
            for field in _OPERATIONAL_FIELDS:
                view.pop(field, None)
            # 公众不展示坑位精确层位。
            view.pop("unit_id", None)
        elif self.role == "researcher":
            sensitive = (
                view.get("sensitivity") in ("sensitive_pit",)
                or self._unit_sensitivity(view) == "sensitive_pit"
            )
            if sensitive:
                for field in _PIT_LOCATION_FIELDS:
                    if field in view:
                        view[field] = "【授权后可见】"
        return view

    def visible_objects(self) -> list[dict]:
        return [
            view
            for object_id in sorted(self.projection.objects)
            if (view := self.view_object(object_id)) is not None
        ]

    def view_events(self) -> list[dict]:
        """按角色过滤/脱敏事件流。

        公众：只读到已发布普通对象的资料性事件，敏感坑位与未发布发现整体剔除。
        研究者：可读未发布资料，但敏感坑位的精确位置字段在事件中同样脱敏。
        """
        if self.role == "conservator":
            return list(self.projection.events)

        hidden_object_ids = {
            object_id
            for object_id, object_view in self.projection.objects.items()
            if not self.can_see_object(object_view)
        }
        sensitive_unit_ids = {
            unit_id for unit_id, unit in self.projection.units.items()
            if unit.get("sensitivity") == "sensitive_pit"
        }
        operational = {"ALERT_RAISED", "ALERT_ACKNOWLEDGED", "OBJECT_FROZEN",
                        "OBJECT_UNFROZEN", "HANDOFF_SIGNED", "SENSITIVITY_CHANGED",
                        "PUBLICATION_STATUS_CHANGED", "ACTION_AUTHORIZED", "ACTION_EXECUTED"}
        result: list[dict] = []
        for event in self.projection.events:
            event_type = event["event_type"]
            p = event.get("payload", {})
            object_id = p.get("object_id")
            if event_type in ("UNIT_REGISTERED", "STRATUM_RECORDED"):
                unit_id = event["aggregate_id"]
            else:
                unit_id = p.get("unit_id")

            if self.role == "public":
                if event_type in operational:
                    continue
                if object_id in hidden_object_ids:
                    continue
                if event_type in ("UNIT_REGISTERED", "STRATUM_RECORDED") and event["aggregate_id"] in sensitive_unit_ids:
                    continue
                if event_type == "SENSITIVITY_CHANGED":
                    continue
                ids_in_payload = p.get("object_ids") or p.get("fragment_object_ids") or []
                if hidden_object_ids.intersection(ids_in_payload):
                    continue
                # 拼合确认/撤销的 payload 只有 relation_id，需经关系表反查残片。
                if event_type in ("JOIN_CONFIRMED", "JOIN_REVOKED"):
                    relation = self.projection.relations.get(p.get("relation_id"))
                    if relation and hidden_object_ids.intersection(relation["fragments"]):
                        continue
                # 身份裁定只有 proposal_id，经候选表反查对象。
                if event_type == "IDENTITY_RESOLVED":
                    proposal = self.projection.proposals.get(p.get("proposal_id"))
                    if proposal and proposal["object_id"] in hidden_object_ids:
                        continue
                result.append(event)
            else:
                # researcher：复制后对敏感坑位位置脱敏。
                masked = dict(event)
                payload = dict(p)
                object_sensitive = (
                    self.projection.objects.get(object_id, {}).get("sensitivity") == "sensitive_pit"
                    if object_id else False
                )
                if object_sensitive or (unit_id in sensitive_unit_ids):
                    for field in ("unit_id", "layer_code", "location_id"):
                        if field in payload:
                            payload[field] = "【授权后可见】"
                if event_type in ("UNIT_REGISTERED", "STRATUM_RECORDED") and event["aggregate_id"] in sensitive_unit_ids:
                    for field in ("unit_code", "layer_code"):
                        if field in payload:
                            payload[field] = "【授权后可见】"
                masked["payload"] = payload
                result.append(masked)
        return result
