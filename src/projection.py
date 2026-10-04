"""只读投影：值班视图、残片谱系与可回溯历史。

投影不产生新事件，也不修改历史；拆分、暂拼、确认、撤销全程保留，
早期检测在任何拼合状态下都可从残片本身追溯。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Projection:
    def __init__(self, events: list[dict]) -> None:
        self.events = sorted(events, key=lambda e: _ts(e["occurred_at"]))
        self.objects: dict[str, dict] = {}
        self.units: dict[str, dict] = {}
        self.children: dict[str, list[str]] = defaultdict(list)
        self.relations: dict[str, dict] = {}
        self.proposals: dict[str, dict] = {}
        self.measurements: dict[str, list[dict]] = defaultdict(list)
        self.alerts: dict[str, dict] = {}
        self.object_alerts: dict[str, list[str]] = defaultdict(list)
        self.packaging_events: dict[str, list[dict]] = defaultdict(list)
        self._build()

    def _build(self) -> None:
        for event in self.events:
            event_type = event["event_type"]
            p = event.get("payload", {})
            if event_type == "UNIT_REGISTERED":
                self.units[event["aggregate_id"]] = {"unit_code": p.get("unit_code"), "layer": p.get("layer_code")}
            elif event_type == "OBJECT_LIFTED":
                self.objects[event["aggregate_id"]] = {
                    "object_id": event["aggregate_id"],
                    "object_code": p.get("object_code"),
                    "object_kind": p.get("object_kind"),
                    "unit_id": p.get("unit_id"),
                    "frozen": False,
                    "sensitivity": "normal",
                    "publication_status": "unpublished",
                }
            elif event_type == "FRAGMENT_SPLIT":
                self.children[p["parent_object_id"]].extend(p["fragment_object_ids"])
            elif event_type == "JOIN_TENTATIVE":
                self.relations[event["aggregate_id"]] = {
                    "relation_id": event["aggregate_id"],
                    "fragments": list(p["fragment_object_ids"]),
                    "status": "tentative",
                    "matched_by": p.get("matched_by"),
                    "history": [{"at": event["occurred_at"], "change": "tentative"}],
                }
            elif event_type in ("JOIN_CONFIRMED", "JOIN_REVOKED"):
                relation = self.relations.get(p["relation_id"])
                if relation is None:
                    continue
                relation["status"] = "confirmed" if event_type == "JOIN_CONFIRMED" else "revoked"
                relation["history"].append({
                    "at": event["occurred_at"],
                    "change": relation["status"],
                    "by": p.get("confirmed_by") or p.get("revoked_by"),
                    "reason": p.get("reason"),
                })
            elif event_type == "IDENTITY_PROPOSED":
                self.proposals[event["aggregate_id"]] = {
                    "object_id": p["object_id"],
                    "figure_id": p["candidate_figure_id"],
                    "resolution": None,
                }
            elif event_type == "IDENTITY_RESOLVED":
                proposal = self.proposals.get(p["proposal_id"])
                if proposal:
                    proposal["resolution"] = p["resolution"]
            elif event_type in ("ENVIRONMENT_SAMPLED", "MEASUREMENT_BACKFILLED"):
                self.measurements[p["object_id"]].append({
                    "at": p["measured_at"],
                    "ingested_at": event["occurred_at"],
                    "sensor_id": p["sensor_id"],
                    "metric": p["metric"],
                    "value": p["value"],
                    "quality": p["quality"],
                    "backfilled": event_type == "MEASUREMENT_BACKFILLED",
                })
            elif event_type == "NITROGEN_HUMIDITY_RECORDED":
                self.packaging_events[p["object_id"]].append({
                    "at": p["measured_at"], "kind": "nitrogen_humidity",
                    "humidity_pct": p["humidity_pct"], "quality": p.get("quality"),
                })
            elif event_type == "PACKAGING_ANOMALY_DETECTED":
                self.packaging_events[p["object_id"]].append({
                    "at": p["detected_at"], "kind": "anomaly",
                    "anomaly_type": p["anomaly_type"],
                })
            elif event_type == "OBJECT_FROZEN":
                if p["object_id"] in self.objects:
                    self.objects[p["object_id"]]["frozen"] = True
            elif event_type == "OBJECT_UNFROZEN":
                if p["object_id"] in self.objects:
                    self.objects[p["object_id"]]["frozen"] = False
            elif event_type == "OBJECT_ACCESSIONED":
                if p["object_id"] in self.objects:
                    self.objects[p["object_id"]].update(
                        accession_code=p.get("accession_code"), location_id=p.get("location_id")
                    )
            elif event_type == "LOCATION_ASSIGNED":
                if p["object_id"] in self.objects:
                    self.objects[p["object_id"]]["location_id"] = p["location_id"]
            elif event_type == "SENSITIVITY_CHANGED":
                target = self.objects.get(p["scope_id"]) if p["scope"] == "object" else self.units.get(p["scope_id"])
                if target is not None:
                    target["sensitivity"] = p["sensitivity"]
            elif event_type == "PUBLICATION_STATUS_CHANGED":
                if p["object_id"] in self.objects:
                    self.objects[p["object_id"]]["publication_status"] = p["publication_status"]
            elif event_type == "ALERT_RAISED":
                self.alerts[event["aggregate_id"]] = {
                    "alert_id": event["aggregate_id"],
                    "object_id": p["object_id"],
                    "category": p["category"],
                    "severity": p["severity"],
                    "raised_at": event["occurred_at"],
                    "open": True,
                }
                self.object_alerts[p["object_id"]].append(event["aggregate_id"])
            elif event_type == "ALERT_ACKNOWLEDGED":
                alert = self.alerts.get(p["alert_id"])
                if alert:
                    alert["open"] = False
                    alert["disposition"] = p.get("disposition")
                    alert["handled_by"] = p.get("handled_by")

        # 补数按采集时间（measured_at）进入时序，而不是按入库时间。
        for rows in self.measurements.values():
            rows.sort(key=lambda m: m["at"])

    # ---- 值班视图：先看到“哪块残片”受影响 --------------------------------

    def duty_board(self) -> list[dict]:
        """按对象聚合未闭环的湿度越界、设备失准、封装异常。"""
        board: dict[str, dict] = {}
        for alert in self.alerts.values():
            if not alert["open"]:
                continue
            entry = board.setdefault(alert["object_id"], {
                "object_id": alert["object_id"],
                "object_code": self.objects.get(alert["object_id"], {}).get("object_code"),
                "frozen": self.objects.get(alert["object_id"], {}).get("frozen", False),
                "issues": [],
            })
            issue = {"category": alert["category"], "severity": alert["severity"], "raised_at": alert["raised_at"]}
            if alert["category"] == "humidity_threshold":
                latest = self._latest_measurement(alert["object_id"], "humidity")
                if latest:
                    issue["latest_humidity"] = latest
            entry["issues"].append(issue)
        return sorted(board.values(), key=lambda row: row["object_id"])

    def _latest_measurement(self, object_id: str, metric: str) -> dict | None:
        rows = [m for m in self.measurements.get(object_id, []) if m["metric"] == metric]
        return rows[-1] if rows else None

    # ---- 谱系：从当前俑体定位任一残片经历 --------------------------------

    def fragments_of_figure(self, figure_id: str) -> list[str]:
        """经拆分（含多级）与已确认身份归属，收集俑体当前的残片集合。"""
        result: set[str] = set()
        # 拆分谱系（父 -> 子，逐级展开）。
        stack = list(self.children.get(figure_id, []))
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            stack.extend(self.children.get(current, []))
        # 已确认的候选身份：残片/对象归属于该俑体。
        for proposal in self.proposals.values():
            if proposal["figure_id"] == figure_id and proposal["resolution"] == "confirmed":
                object_id = proposal["object_id"]
                if object_id not in result:
                    result.add(object_id)
                    stack.extend(self.children.get(object_id, []))
        return sorted(result)

    def trace_lineage(self, figure_id: str) -> dict:
        fragments = self.fragments_of_figure(figure_id)
        return {
            "figure_id": figure_id,
            "fragments": fragments,
            "relations": [
                relation for relation in self.relations.values()
                if set(relation["fragments"]) & set(fragments)
            ],
            "fragment_histories": {fragment: self.fragment_history(fragment) for fragment in fragments},
        }

    def fragment_history(self, fragment_id: str) -> list[dict]:
        """残片的全部经历：检测、充氮保湿、采集、封装、交接、干预、告警与冻结均不丢失。"""
        history: list[dict] = []
        touch_events = {
            "ENVIRONMENT_SAMPLED", "MEASUREMENT_BACKFILLED", "NITROGEN_HUMIDITY_RECORDED",
            "PACKAGING_ANOMALY_DETECTED", "ACQUISITION_RECORDED", "ACTION_AUTHORIZED",
            "ACTION_EXECUTED", "OBJECT_FROZEN", "OBJECT_UNFROZEN",
            "OBJECT_ACCESSIONED", "LOCATION_ASSIGNED",
        }
        for event in self.events:
            event_type = event["event_type"]
            p = event.get("payload", {})
            if event_type in touch_events and p.get("object_id") == fragment_id:
                history.append(_brief(event))
            elif event_type in ("FRAGMENT_SPLIT", "JOIN_TENTATIVE") and fragment_id in (
                p.get("fragment_object_ids") or []
            ) or (event_type == "FRAGMENT_SPLIT" and p.get("parent_object_id") == fragment_id):
                history.append(_brief(event))
            elif event_type in ("JOIN_CONFIRMED", "JOIN_REVOKED"):
                relation = self.relations.get(p.get("relation_id"))
                if relation and fragment_id in relation["fragments"]:
                    history.append(_brief(event))
            elif event_type == "HANDOFF_SIGNED" and fragment_id in (p.get("object_ids") or []):
                history.append(_brief(event))
            elif event_type == "ALERT_RAISED" and p.get("object_id") == fragment_id:
                history.append(_brief(event))
        return history


def _brief(event: dict) -> dict:
    return {
        "at": event["occurred_at"],
        "event_type": event["event_type"],
        "aggregate_id": event["aggregate_id"],
        "payload": event.get("payload", {}),
    }
