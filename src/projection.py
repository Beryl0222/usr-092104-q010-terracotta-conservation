"""事件重放读模型。

投影只从事件日志构建，自身不做任何授权之外的决策：
- 环境样本在查询视图中按 *采集时间* 排序（晚到补数自动归位），但保留入库顺序与质量标记；
- 残片关系全程追加，撤销把原关系标记为 revoked，历史不删除；
- 早期检测挂在对象（残片）上，拆分/拼合不改变检测归属。
"""
from __future__ import annotations

from datetime import datetime

from src.contract import (
    ALERT_ACKNOWLEDGED, ALERT_RAISED, ALERT_RESOLVED, CALIBRATION_RECORDED,
    CANDIDATE_CONFIRMED, CANDIDATE_PROPOSED, CANDIDATE_REJECTED,
    ENVIRONMENT_SAMPLED, EXAMINATION_RECORDED, FRAGMENT_DETACHED,
    HANDOFF_PREPARED, HANDOFF_SIGNED, HOLD_PLACED, HOLD_RELEASED,
    INSTRUMENT_REGISTERED, OBJECT_ACCESSIONED, OBJECT_LIFTED,
    PACKAGE_N2_REFRESHED, PACKAGE_PREPARED, PACKAGE_SEALED,
    PACKAGE_STATUS_RECORDED, RELATION_CONFIRMED, RELATION_OPENED,
    RELATION_REVOKED, STRATUM_RECORDED, TRENCH_OPENED, LIMITS_SET, parse_dt,
)


def _ts(value: str) -> datetime:
    return parse_dt(value)


class ReadModel:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.trenches: dict[str, dict] = {}
        self.objects: dict[str, dict] = {}
        self.candidates: dict[str, dict] = {}
        self.relations: dict[str, dict] = {}
        self.instruments: dict[str, dict] = {}
        self.series: dict[str, list[dict]] = {}
        self.samples_by_object: dict[str, list[dict]] = {}
        self.alerts: dict[str, dict] = {}
        self.packages: dict[str, dict] = {}
        self.actions: dict[str, dict] = {}
        self.handoffs: dict[str, dict] = {}
        self.storage: dict[str, dict] = {}
        self.applied_event_ids: set[str] = set()
        self.event_count = 0

    # ── 重放入口 ──────────────────────────────────────────────────────
    def apply(self, event: dict) -> None:
        if event["event_id"] in self.applied_event_ids:
            return
        self.applied_event_ids.add(event["event_id"])
        self.event_count += 1
        etype = event["event_type"]
        p = event.get("payload", {})
        handler = getattr(self, f"_on_{etype.lower()}", None)
        if handler:
            handler(event, p)

    def apply_many(self, events: list[dict]) -> None:
        for event in events:
            self.apply(event)

    # ── 探方 / 地层 ───────────────────────────────────────────────────
    def _on_trench_opened(self, e: dict, p: dict) -> None:
        self.trenches[e["aggregate_id"]] = {
            "trench_id": e["aggregate_id"],
            "trench_code": p["trench_code"],
            "sensitive": bool(p["sensitive"]),
            "grid_coordinates": p["grid_coordinates"],
            "opened_at": e["occurred_at"],
            "strata": {},
        }

    def _on_stratum_recorded(self, e: dict, p: dict) -> None:
        trench = self.trenches[p["trench_id"]]
        trench["strata"][p["stratum_id"]] = {
            "stratum_id": p["stratum_id"],
            "depth_top": p["depth_top"],
            "depth_bottom": p["depth_bottom"],
            "period_label": p["period_label"],
            "recorded_at": e["occurred_at"],
        }

    # ── 对象 / 残片 ───────────────────────────────────────────────────
    def _on_object_lifted(self, e: dict, p: dict) -> None:
        existing = self.objects.get(e["aggregate_id"], {})
        self.objects[e["aggregate_id"]] = {
            "object_id": e["aggregate_id"],
            "object_kind": p["object_kind"],
            "trench_id": p["trench_id"],
            "stratum_id": p["stratum_id"],
            "publication_state": p["publication_state"],
            "figure_id": e["aggregate_id"],
            "source_object_id": None,
            "lifted_at": e["occurred_at"],
            "current_party": p.get("initial_custodian", "field_excavation_team"),
            "holds": [],
            "status": "lifted",
            "storage_location_id": None,
            "accession_code": None,
            "detached_at": None,
            "monitoring_limits": existing.get("monitoring_limits"),
        }

    def _on_fragment_detached(self, e: dict, p: dict) -> None:
        # 事件聚合即新残片：残片从第一条事件起拥有独立历史流
        self.objects[e["aggregate_id"]] = {
            "object_id": e["aggregate_id"],
            "object_kind": "fragment",
            "trench_id": p.get("trench_id"),
            "stratum_id": p.get("stratum_id"),
            "publication_state": self.objects.get(
                p["source_object_id"], {}).get("publication_state", "unpublished"),
            "figure_id": p["figure_id"],
            "source_object_id": p["source_object_id"],
            "lifted_at": None,
            "detached_at": e["occurred_at"],
            "current_party": self.objects.get(
                p["source_object_id"], {}).get("current_party", "field_excavation_team"),
            "holds": [],
            "status": "detached",
            "storage_location_id": None,
            "accession_code": None,
            "detach_reason": p["reason"],
            "monitoring_limits": self.objects.get(
                p["source_object_id"], {}).get("monitoring_limits"),
        }

    def _on_examination_recorded(self, e: dict, p: dict) -> None:
        record = {
            "examination_id": p["examination_id"],
            "object_id": p["object_id"],
            "kind": p["kind"],
            "examined_at": p["examined_at"],
            "data": p.get("data", {}),
            "technician": p.get("technician"),
            "event_id": e["event_id"],
        }
        self.objects.setdefault(p["object_id"], {}).setdefault("examinations", []).append(record)

    # ── 候选身份 ──────────────────────────────────────────────────────
    def _on_candidate_identity_proposed(self, e: dict, p: dict) -> None:
        self.candidates[e["aggregate_id"]] = {
            "candidate_id": e["aggregate_id"],
            "object_id": p["object_id"],
            "proposed_form": p["proposed_form"],
            "status": "proposed",
            "history": [{"at": e["occurred_at"], "to": "proposed",
                         "by": p["proposed_by"], "note": p.get("note")}],
        }

    def _on_candidate_identity_confirmed(self, e: dict, p: dict) -> None:
        cand = self.candidates[e["aggregate_id"]]
        cand["status"] = "confirmed"
        cand["history"].append({"at": e["occurred_at"], "to": "confirmed",
                                "by": p["confirmed_by"]})

    def _on_candidate_identity_rejected(self, e: dict, p: dict) -> None:
        cand = self.candidates[e["aggregate_id"]]
        cand["status"] = "rejected"
        cand["history"].append({"at": e["occurred_at"], "to": "rejected",
                                "by": p["rejected_by"], "note": p["reason"]})

    # ── 残片关系（只追加；撤销产生新关系） ─────────────────────────────
    def _on_relation_opened(self, e: dict, p: dict) -> None:
        relation = {
            "relation_id": e["aggregate_id"],
            "relation_kind": p["relation_kind"],
            "status": "revoked" if p["relation_kind"] == RELATION_REVOKED else "open",
            "fragment_ids": list(p.get("fragment_ids", [])),
            "fragment_id": p.get("fragment_id"),
            "source_object_id": p.get("source_object_id"),
            "figure_id": p.get("figure_id"),
            "original_relation_id": p.get("original_relation_id"),
            "reason": p.get("reason"),
            "opened_at": e["occurred_at"],
            "confirmed_at": None,
        }
        self.relations[e["aggregate_id"]] = relation
        if p["relation_kind"] == RELATION_REVOKED and p.get("original_relation_id"):
            original = self.relations.get(p["original_relation_id"])
            if original:
                original["status"] = "revoked"
                original["revoked_by_relation"] = e["aggregate_id"]

    def _on_relation_confirmed(self, e: dict, p: dict) -> None:
        relation = self.relations[e["aggregate_id"]]
        relation["status"] = "confirmed"
        relation["confirmed_at"] = e["occurred_at"]
        relation["confirmed_by"] = p["confirmed_by"]

    # ── 监测阈值 ──────────────────────────────────────────────────────
    def _on_monitoring_limits_set(self, e: dict, p: dict) -> None:
        obj = self.objects.setdefault(p["object_id"], {"holds": []})
        obj["monitoring_limits"] = dict(p["limits"])
        obj.setdefault("monitoring_limits_history", []).append(
            {"at": e["occurred_at"], "limits": dict(p["limits"]),
             "set_by": e.get("actor", {}).get("id")})

    # ── 设备 / 校准 / 环境时序 ────────────────────────────────────────
    def _on_instrument_registered(self, e: dict, p: dict) -> None:
        self.instruments[e["aggregate_id"]] = {
            "instrument_id": e["aggregate_id"],
            "instrument_code": p["instrument_code"],
            "metric": p["metric"],
            "calibrations": [],
            "registered_at": e["occurred_at"],
        }

    def _on_calibration_recorded(self, e: dict, p: dict) -> None:
        instrument = self.instruments[e["aggregate_id"]]
        instrument["calibrations"].append({
            "valid_from": p["valid_from"],
            "valid_to": p["valid_to"],
            "cert_id": p["cert_id"],
            "recorded_at": e["occurred_at"],
        })

    def _on_environment_sampled(self, e: dict, p: dict) -> None:
        sample = {
            "sample_event_id": e["event_id"],
            "series_id": p["series_id"],
            "object_id": p["object_id"],
            "instrument_id": p["instrument_id"],
            "metric": p["metric"],
            "value": p["value"],
            "collected_at": p["collected_at"],
            "received_at": e["occurred_at"],
            "quality": p["quality"],
            "calibration_cert_id": p.get("calibration_cert_id"),
            "note": p.get("note"),
        }
        self.series.setdefault(p["series_id"], []).append(sample)
        self.samples_by_object.setdefault(p["object_id"], []).append(sample)

    # ── 告警 / 冻结 ───────────────────────────────────────────────────
    def _on_alert_raised(self, e: dict, p: dict) -> None:
        self.alerts[e["aggregate_id"]] = {
            "alert_id": e["aggregate_id"],
            "alert_kind": p["alert_kind"],
            "affected_object_ids": list(p["affected_object_ids"]),
            "status": "raised",
            "raised_at": e["occurred_at"],
            "raised_by": p["raised_by"],
            "metric": p.get("metric"),
            "value": p.get("value"),
            "limit": p.get("limit"),
            "related_event_id": p.get("related_event_id"),
            "package_id": p.get("package_id"),
            "instrument_id": p.get("instrument_id"),
            "acknowledged_at": None,
            "resolved_at": None,
            "note": p.get("note"),
        }

    def _on_alert_acknowledged(self, e: dict, p: dict) -> None:
        alert = self.alerts[e["aggregate_id"]]
        alert["status"] = "acknowledged"
        alert["acknowledged_at"] = e["occurred_at"]
        alert["acknowledged_by"] = p["acknowledged_by"]

    def _on_alert_resolved(self, e: dict, p: dict) -> None:
        alert = self.alerts[e["aggregate_id"]]
        alert["status"] = "resolved"
        alert["resolved_at"] = e["occurred_at"]
        alert["resolved_by"] = p["resolved_by"]
        alert["resolution"] = p["resolution"]

    def _on_object_hold_placed(self, e: dict, p: dict) -> None:
        obj = self.objects[p["object_id"]]
        obj["holds"].append({
            "hold_id": p.get("hold_id", f"hold-{e['event_id']}"),
            "reason": p["reason"],
            "alert_id": p.get("alert_id"),
            "placed_at": e["occurred_at"],
            "placed_by": p["placed_by"],
            "released_at": None,
        })

    def _on_object_hold_released(self, e: dict, p: dict) -> None:
        obj = self.objects[p["object_id"]]
        for hold in reversed(obj["holds"]):
            if hold["hold_id"] == p["hold_id"] and hold["released_at"] is None:
                hold["released_at"] = e["occurred_at"]
                hold["released_by"] = p["released_by"]
                break

    # ── 充氮保湿 / 运输封装 ───────────────────────────────────────────
    def _on_package_prepared(self, e: dict, p: dict) -> None:
        self.packages[e["aggregate_id"]] = {
            "package_id": e["aggregate_id"],
            "package_code": p["package_code"],
            "object_ids": list(p["object_ids"]),
            "humidity_limits": p["humidity_limits"],
            "status": "prepared",
            "sealed_at": None,
            "seal_events": [],
            "n2_refreshes": [],
        }

    def _on_package_sealed(self, e: dict, p: dict) -> None:
        package = self.packages[e["aggregate_id"]]
        package["status"] = "sealed"
        package["sealed_at"] = e["occurred_at"]
        package["sealed_by"] = p["sealed_by"]

    def _on_package_n2_refreshed(self, e: dict, p: dict) -> None:
        package = self.packages[e["aggregate_id"]]
        package["n2_refreshes"].append({
            "at": e["occurred_at"],
            "by": p["refreshed_by"],
            "nitrogen_purity": p["nitrogen_purity"],
        })

    def _on_package_status_recorded(self, e: dict, p: dict) -> None:
        package = self.packages[e["aggregate_id"]]
        package["seal_events"].append({
            "at": e["occurred_at"],
            "seal_integrity": p["seal_integrity"],
            "by": p["recorded_by"],
            "note": p.get("note"),
        })
        package["latest_seal_integrity"] = p["seal_integrity"]

    # ── 修复干预 ──────────────────────────────────────────────────────
    def _on_action_proposed(self, e: dict, p: dict) -> None:
        self.actions[e["aggregate_id"]] = {
            "action_id": e["aggregate_id"],
            "action_kind": p["action_kind"],
            "target_object_id": p["target_object_id"],
            "status": "proposed",
            "proposed_by": p["proposed_by"],
            "rationale": p["rationale"],
            "related_alert_id": p.get("related_alert_id"),
            "proposed_at": e["occurred_at"],
        }

    def _on_action_authorized(self, e: dict, p: dict) -> None:
        action = self.actions[e["aggregate_id"]]
        action["status"] = "authorized"
        action["authorizer_id"] = p["authorizer_id"]
        action["authorized_at"] = e["occurred_at"]

    def _on_action_completed(self, e: dict, p: dict) -> None:
        action = self.actions[e["aggregate_id"]]
        action["status"] = "completed"
        action["completed_by"] = p["completed_by"]
        action["result"] = p["result"]
        action["completed_at"] = e["occurred_at"]

    # ── 责任交接 ──────────────────────────────────────────────────────
    def _on_handoff_prepared(self, e: dict, p: dict) -> None:
        self.handoffs[e["aggregate_id"]] = {
            "handoff_id": e["aggregate_id"],
            "handoff_code": p["handoff_code"],
            "from_party": p["from_party"],
            "to_party": p["to_party"],
            "object_ids": list(p["object_ids"]),
            "status": "prepared",
            "transferred_object_ids": [],
            "frozen_object_ids": [],
        }

    def _on_handoff_signed(self, e: dict, p: dict) -> None:
        handoff = self.handoffs[e["aggregate_id"]]
        handoff["status"] = "signed"
        handoff["signed_at"] = e["occurred_at"]
        handoff["releaser"] = p["releaser"]
        handoff["receiver"] = p["receiver"]
        handoff["transferred_object_ids"] = list(p["transferred_object_ids"])
        handoff["frozen_object_ids"] = list(p.get("frozen_object_ids", []))
        for oid in p["transferred_object_ids"]:
            if oid in self.objects:
                self.objects[oid]["current_party"] = handoff["to_party"]

    # ── 入库 ──────────────────────────────────────────────────────────
    def _on_storage_location_registered(self, e: dict, p: dict) -> None:
        self.storage[e["aggregate_id"]] = {
            "location_id": e["aggregate_id"],
            "location_code": p["location_code"],
            "area": p["area"],
        }

    def _on_object_accessioned(self, e: dict, p: dict) -> None:
        obj = self.objects[p["object_id"]]
        obj["status"] = "accessioned"
        obj["storage_location_id"] = p["storage_location_id"]
        obj["accession_code"] = p["accession_code"]
        obj["accessioned_at"] = e["occurred_at"]

    # ── 派生查询辅助 ──────────────────────────────────────────────────
    def calibration_valid(self, instrument_id: str, collected_at: str) -> dict | None:
        """返回采集时刻生效的校准记录；无有效校准则返回 None。"""
        instrument = self.instruments.get(instrument_id)
        if not instrument:
            return None
        moment = _ts(collected_at)
        valid = [c for c in instrument["calibrations"]
                 if _ts(c["valid_from"]) <= moment <= _ts(c["valid_to"])]
        return valid[-1] if valid else None

    def sorted_samples(self, series_id: str) -> list[dict]:
        """按采集时间归位——断线补数无论何时入库都落到其采集时刻。"""
        return sorted(self.series.get(series_id, []), key=lambda s: _ts(s["collected_at"]))

    def active_holds(self, object_id: str) -> list[dict]:
        return [h for h in self.objects.get(object_id, {}).get("holds", [])
                if h["released_at"] is None]

    def is_frozen(self, object_id: str) -> bool:
        return bool(self.active_holds(object_id))

    def fragments_of_figure(self, figure_id: str) -> list[dict]:
        return [o for o in self.objects.values()
                if o.get("figure_id") == figure_id and o["object_kind"] == "fragment"]

    def current_assembly(self, figure_id: str) -> dict:
        """当前俑体的拼合状态：已确认且未被撤销的关系才算拼在一起。"""
        joined_pairs = [r for r in self.relations.values()
                        if r.get("figure_id") == figure_id
                        and r["relation_kind"] == "tentative_join"
                        and r["status"] == "confirmed"]
        return {"figure_id": figure_id,
                "confirmed_relations": [r["relation_id"] for r in joined_pairs]}
