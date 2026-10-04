"""查询视图：从读模型组装业务视图，全部为实时重放结果，不依赖日终汇总。

- :func:`fragment_lineage`：修复人员从任一残片回溯它经历的全部事件，
  或从当前俑体下钻到任一残片，检测记录贯穿拆分/暂拼/确认/撤销；
- :func:`alert_dashboard`：值班人员实时看到湿度越界、设备失准、封装异常
  分别影响哪块残片，以及冻结/签收状态；
- :func:`figure_overview`：当前俑体的残片清单与现行拼合状态。
"""
from __future__ import annotations

from src.auth import AccessContext, can_view_object, require_object_visible, trench_view
from src.contract import parse_dt
from src.projection import ReadModel

# 各告警种类的中文说明，供值班面板直接展示
ALERT_LABELS = {
    "humidity_breach": "湿度越界",
    "instrument_uncalibrated": "设备失准（校准失效）",
    "package_anomaly": "封装异常",
}


def _sorted_by_time(items: list[dict], key: str) -> list[dict]:
    return sorted(items, key=lambda x: parse_dt(x[key]))


class Queries:
    def __init__(self, read: ReadModel) -> None:
        self.read = read

    # ── 残片 / 对象谱系 ───────────────────────────────────────────────
    def fragment_lineage(self, object_id: str, ctx: AccessContext) -> dict:
        obj = self.read.objects.get(object_id)
        if not obj:
            raise KeyError(f"对象不存在：{object_id}")
        require_object_visible(ctx, obj)
        read = self.read

        # 出身链：残片 → 拆分来源 → … → 俑体
        origin_chain: list[dict] = []
        cursor = obj
        seen: set[str] = set()
        while cursor and cursor["object_id"] not in seen:
            seen.add(cursor["object_id"])
            entry = {
                "object_id": cursor["object_id"],
                "object_kind": cursor["object_kind"],
                "trench_id": cursor.get("trench_id"),
                "stratum_id": cursor.get("stratum_id"),
                "lifted_at": cursor.get("lifted_at"),
                "detached_at": cursor.get("detached_at"),
                "detach_reason": cursor.get("detach_reason"),
            }
            origin_chain.append(entry)
            parent_id = cursor.get("source_object_id")
            cursor = read.objects.get(parent_id) if parent_id else None

        figure_id = obj.get("figure_id")

        # 关系：涉及本对象的全部关系（含已撤销），按时间排列
        relations = [r for r in read.relations.values()
                     if obj["object_id"] in r.get("fragment_ids", [])
                     or r.get("fragment_id") == obj["object_id"]]
        relation_view = [{
            "relation_id": r["relation_id"],
            "kind": r["relation_kind"],
            "status": r["status"],
            "members": r["fragment_ids"],
            "opened_at": r["opened_at"],
            "confirmed_at": r.get("confirmed_at"),
            "original_relation_id": r.get("original_relation_id"),
            "reason": r.get("reason"),
        } for r in _sorted_by_time(relations, "opened_at")]

        # 历次检测：挂在对象自身流上，拼合/撤销均不影响
        examinations = _sorted_by_time(obj.get("examinations", []), "examined_at")

        # 环境时序按采集时间归位，质量标记原样返回
        sample_rows = [s for s in read.samples_by_object.get(object_id, [])]
        sample_rows.sort(key=lambda s: parse_dt(s["collected_at"]))

        alerts = [a for a in read.alerts.values()
                  if object_id in a["affected_object_ids"]]

        packages = [p for p in read.packages.values()
                    if object_id in p["object_ids"]]
        actions = [a for a in read.actions.values()
                   if a["target_object_id"] == object_id]

        handoffs = []
        for h in read.handoffs.values():
            if object_id in h["object_ids"]:
                handoffs.append({
                    "handoff_id": h["handoff_id"],
                    "code": h["handoff_code"],
                    "from_party": h["from_party"],
                    "to_party": h["to_party"],
                    "status": h["status"],
                    "responsibility_transferred": object_id in h["transferred_object_ids"],
                    "frozen_at_signing": object_id in h["frozen_object_ids"],
                    "signed_at": h.get("signed_at"),
                })

        return {
            "object_id": object_id,
            "figure_id": figure_id,
            "publication_state": obj.get("publication_state"),
            "status": obj.get("status"),
            "current_party": obj.get("current_party"),
            "frozen": read.is_frozen(object_id),
            "active_holds": read.active_holds(object_id),
            "origin_chain": origin_chain,
            "relations": relation_view,
            "examinations": examinations,
            "environment_samples": sample_rows,
            "alerts": [{
                "alert_id": a["alert_id"], "kind": a["alert_kind"],
                "status": a["status"], "raised_at": a["raised_at"],
            } for a in alerts],
            "packages": [{
                "package_id": p["package_id"], "code": p["package_code"],
                "status": p["status"], "sealed_at": p.get("sealed_at"),
                "latest_seal_integrity": p.get("latest_seal_integrity"),
            } for p in packages],
            "conservation_actions": [{
                "action_id": a["action_id"], "kind": a["action_kind"],
                "status": a["status"], "proposed_at": a["proposed_at"],
                "related_alert_id": a.get("related_alert_id"),
            } for a in actions],
            "handoffs": handoffs,
            "storage_location_id": obj.get("storage_location_id"),
            "accession_code": obj.get("accession_code"),
        }

    # ── 当前俑体总览 ──────────────────────────────────────────────────
    def figure_overview(self, figure_id: str, ctx: AccessContext) -> dict:
        figure = self.read.objects.get(figure_id)
        if not figure:
            raise KeyError(f"俑体不存在：{figure_id}")
        require_object_visible(ctx, figure)
        fragments = []
        for o in self.read.fragments_of_figure(figure_id):
            if not can_view_object(ctx, o):
                continue
            fragments.append({
                "object_id": o["object_id"],
                "object_kind": o["object_kind"],
                "status": o.get("status"),
                "frozen": self.read.is_frozen(o["object_id"]),
                "current_party": o.get("current_party"),
                "storage_location_id": o.get("storage_location_id"),
            })
        current = self.read.current_assembly(figure_id)
        revoked = [r["relation_id"] for r in self.read.relations.values()
                   if r.get("figure_id") == figure_id
                   and r["relation_kind"] == "join_revoked"]
        return {
            "figure_id": figure_id,
            "fragments": fragments,
            "current_confirmed_relations": current["confirmed_relations"],
            "revocations": revoked,
            "trench_id": figure.get("trench_id"),
            "stratum_id": figure.get("stratum_id"),
            "publication_state": figure.get("publication_state"),
        }

    # ── 值班实时告警面板 ──────────────────────────────────────────────
    def alert_dashboard(self, ctx: AccessContext, *, include_resolved: bool = False) -> dict:
        """值班面板：每条未结告警直接落到受影响残片及其现场上下文。"""
        if not ctx.is_staff:
            from src.auth import AccessDenied
            raise AccessDenied("告警面板仅限值班/修复/库房人员")
        panels = []
        for alert in self.read.alerts.values():
            if not include_resolved and alert["status"] == "resolved":
                continue
            affected = []
            for oid in alert["affected_object_ids"]:
                obj = self.read.objects.get(oid)
                if not obj:
                    continue
                trench = self.read.trenches.get(obj.get("trench_id") or "", None)
                affected.append({
                    "object_id": oid,
                    "object_kind": obj["object_kind"],
                    "figure_id": obj.get("figure_id"),
                    "trench_code": trench["trench_code"] if trench else None,
                    "stratum_id": obj.get("stratum_id"),
                    "frozen": self.read.is_frozen(oid),
                    "current_party": obj.get("current_party"),
                    "storage_location_id": obj.get("storage_location_id"),
                })
            panels.append({
                "alert_id": alert["alert_id"],
                "kind": alert["alert_kind"],
                "label": ALERT_LABELS.get(alert["alert_kind"], alert["alert_kind"]),
                "status": alert["status"],
                "raised_at": alert["raised_at"],
                "acknowledged_at": alert.get("acknowledged_at"),
                "metric": alert.get("metric"),
                "value": alert.get("value"),
                "limit": alert.get("limit"),
                "instrument_id": alert.get("instrument_id"),
                "package_id": alert.get("package_id"),
                "note": alert.get("note"),
                "affected_fragments": affected,
            })
        panels.sort(key=lambda p: parse_dt(p["raised_at"]), reverse=True)
        open_count = sum(1 for p in panels if p["status"] != "resolved")
        frozen_ids = sorted({o["object_id"] for p in panels
                             for o in p["affected_fragments"] if o["frozen"]})
        return {
            "open_alert_count": open_count,
            "frozen_object_ids": frozen_ids,
            "alerts": panels,
        }

    # ── 对象检索（按授权过滤） ────────────────────────────────────────
    def list_objects(self, ctx: AccessContext, *, figure_id: str | None = None) -> list[dict]:
        rows = []
        for obj in self.read.objects.values():
            if figure_id and obj.get("figure_id") != figure_id:
                continue
            if not can_view_object(ctx, obj):
                continue
            rows.append({
                "object_id": obj["object_id"],
                "object_kind": obj["object_kind"],
                "figure_id": obj.get("figure_id"),
                "status": obj.get("status"),
                "frozen": self.read.is_frozen(obj["object_id"]),
                "publication_state": obj.get("publication_state"),
            })
        return rows

    def trench(self, trench_id: str, ctx: AccessContext) -> dict:
        trench = self.read.trenches[trench_id]
        return trench_view(ctx, trench)

    def environment_series(self, series_id: str, ctx: AccessContext) -> dict:
        rows = self.read.sorted_samples(series_id)
        visible = []
        for s in rows:
            obj = self.read.objects.get(s["object_id"])
            if obj and not can_view_object(ctx, obj):
                continue
            row = dict(s)
            if not ctx.is_staff:
                row.pop("calibration_cert_id", None)
                row.pop("received_at", None)
            visible.append(row)
        return {"series_id": series_id, "samples": visible}
