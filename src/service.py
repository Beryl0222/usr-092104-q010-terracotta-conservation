"""应用服务：现场命令 → 领域事件。

全部业务不变量在这一层强制，存储层之外的任何写入入口都应经过这里
（仓库原有事件入口可直接写存储，但读模型与告警的语义以本模块为准）。

关键规则：
- 环境数据以采集时间为准，断线补数照常追加版本并带质量标记；
- 校准失效的测量自动降级为 uncalibrated，永不驱动告警；
- 越界只产生“告警 + 人工处置”，系统从不自动生成清理/加固动作；
- 冻结只落在受影响对象上，交接签署时未冻结对象正常转移；
- 撤销错误拼合产生一条新的 join_revoked 关系，不删除历史；
- 交接必须交出、接收双方在同一事件中共同签署，责任才变化。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.contract import (
    ACTION_AUTHORIZED, ACTION_COMPLETED, ACTION_PROPOSED, ALERT_HUMIDITY,
    ALERT_INSTRUMENT, ALERT_PACKAGE, ALERT_ACKNOWLEDGED, ALERT_RAISED,
    ALERT_RESOLVED,
    CALIBRATION_RECORDED, CANDIDATE_CONFIRMED, CANDIDATE_PROPOSED,
    CANDIDATE_REJECTED, ENVIRONMENT_SAMPLED, EVENT_AGGREGATE,
    EXAMINATION_RECORDED, FRAGMENT_DETACHED, HANDOFF_PREPARED, HANDOFF_SIGNED,
    HOLD_PLACED, HOLD_RELEASED, INSTRUMENT_REGISTERED, LIMITS_SET,
    METRIC_HUMIDITY, OBJECT_ACCESSIONED, OBJECT_LIFTED, PACKAGE_PREPARED,
    PACKAGE_SEALED, PACKAGE_N2_REFRESHED, PACKAGE_STATUS_RECORDED,
    QUALITY_BACKFILL, QUALITY_INTERPOLATED, QUALITY_OK, QUALITY_SUSPECT,
    QUALITY_UNCALIBRATED, RELIABLE_QUALITIES, RELATION_CONFIRMED,
    RELATION_OPENED, RELATION_TENTATIVE, RELATION_REVOKED, STORAGE_REGISTERED,
    STRATUM_RECORDED, TRENCH_OPENED, ROLE_CONSERVATOR, ROLE_DUTY_OFFICER,
    ROLE_REGISTRAR, parse_dt,
)
from src.projection import ReadModel
from src.store import EventStore, StoreError


class DomainError(Exception):
    """命令违反领域不变量。"""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class HandoffService:
    def __init__(self, store: EventStore, *, clock=None) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.read = ReadModel()
        self.read.apply_many(store.all_events())

    # ── 内部工具 ──────────────────────────────────────────────────────
    def _now(self) -> str:
        return self.clock()

    def _next_version(self, aggregate_id: str) -> int:
        return self.store.stream_version(aggregate_id) + 1

    def _event(self, event_type: str, aggregate_id: str, payload: dict,
               summary: str, *, actor: dict | None = None,
               occurred_at: str | None = None) -> dict:
        event = {
            "event_id": _new_id("evt"),
            "event_type": event_type,
            "aggregate_type": EVENT_AGGREGATE[event_type],
            "aggregate_id": aggregate_id,
            "occurred_at": occurred_at or self._now(),
            "version": self._next_version(aggregate_id),
            "summary": summary,
            "payload": payload,
        }
        if actor:
            event["actor"] = actor
        return event

    def _commit(self, events: list[dict]) -> list[dict]:
        if not events:
            return []
        # 同一批可能跨多个聚合（如样本 + 告警 + 冻结），逐条重算版本
        planned: dict[str, int] = {}
        for event in events:
            aid = event["aggregate_id"]
            event["version"] = self._next_version(aid) + planned.get(aid, 0)
            planned[aid] = planned.get(aid, 0) + 1
        try:
            stored = self.store.append_batch(events)
        except StoreError as exc:
            raise DomainError(str(exc)) from exc
        self.read.apply_many(stored)
        return stored

    def _require_role(self, actor: dict, roles: tuple[str, ...], action: str) -> None:
        if not actor or not actor.get("id") or not actor.get("role"):
            raise DomainError(f"{action}需要操作者身份")
        if actor["role"] not in roles:
            raise DomainError(f"角色 {actor['role']} 无权执行：{action}")

    def _require_object(self, object_id: str) -> dict:
        obj = self.read.objects.get(object_id)
        if not obj:
            raise DomainError(f"对象不存在：{object_id}")
        return obj

    def _require_not_frozen(self, object_id: str, action: str) -> None:
        if self.read.is_frozen(object_id):
            raise DomainError(f"对象 {object_id} 处于异常冻结状态，不能{action}")

    # ── 探方 / 地层 / 出土 ────────────────────────────────────────────
    def open_trench(self, *, trench_code: str, sensitive: bool,
                    grid_coordinates: dict, actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER,), "开设探方")
        trench_id = _new_id("trench")
        return self._commit([self._event(
            TRENCH_OPENED, trench_id,
            {"trench_code": trench_code, "sensitive": sensitive,
             "grid_coordinates": grid_coordinates},
            f"开设探方 {trench_code}", actor=actor)])[0]

    def record_stratum(self, *, trench_id: str, stratum_id: str, depth_top: float,
                       depth_bottom: float, period_label: str, actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER,), "登记地层")
        if trench_id not in self.read.trenches:
            raise DomainError(f"探方不存在：{trench_id}")
        if depth_top >= depth_bottom:
            raise DomainError("地层深度必须 depth_top < depth_bottom")
        return self._commit([self._event(
            STRATUM_RECORDED, trench_id,
            {"trench_id": trench_id, "stratum_id": stratum_id,
             "depth_top": depth_top, "depth_bottom": depth_bottom,
             "period_label": period_label},
            f"登记地层 {stratum_id}", actor=actor)])[0]

    def lift_object(self, *, object_id: str, object_kind: str, trench_id: str,
                    stratum_id: str, publication_state: str = "unpublished",
                    initial_custodian: str | None = None, actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER,), "起取对象")
        if trench_id not in self.read.trenches:
            raise DomainError(f"探方不存在：{trench_id}")
        if stratum_id not in self.read.trenches[trench_id]["strata"]:
            raise DomainError(f"地层不存在：{stratum_id}")
        if object_id in self.read.objects:
            raise DomainError(f"对象已存在：{object_id}")
        payload = {"object_kind": object_kind, "trench_id": trench_id,
                   "stratum_id": stratum_id,
                   "publication_state": publication_state}
        if initial_custodian:
            payload["initial_custodian"] = initial_custodian
        return self._commit([self._event(
            OBJECT_LIFTED, object_id, payload,
            f"起取{('俑体' if object_kind == 'figure' else '对象')} {object_id}",
            actor=actor)])[0]

    # ── 残片拆分（同时产生 detached 关系，历史独立成流） ───────────────
    def detach_fragment(self, *, fragment_id: str, source_object_id: str,
                        figure_id: str, reason: str, actor: dict,
                        trench_id: str | None = None,
                        stratum_id: str | None = None) -> list[dict]:
        self._require_role(actor, (ROLE_DUTY_OFFICER, ROLE_CONSERVATOR), "拆分残片")
        source = self._require_object(source_object_id)
        self._require_not_frozen(source_object_id, "拆分")
        if fragment_id in self.read.objects:
            raise DomainError(f"残片已存在：{fragment_id}")
        relation_id = _new_id("rel")
        fragment_event = self._event(
            FRAGMENT_DETACHED, fragment_id,
            {"fragment_id": fragment_id, "source_object_id": source_object_id,
             "figure_id": figure_id, "reason": reason,
             "trench_id": trench_id or source.get("trench_id"),
             "stratum_id": stratum_id or source.get("stratum_id")},
            f"从 {source_object_id} 拆分残片 {fragment_id}", actor=actor)
        relation_event = self._event(
            RELATION_OPENED, relation_id,
            {"relation_kind": "detached", "fragment_id": fragment_id,
             "source_object_id": source_object_id, "figure_id": figure_id,
             "fragment_ids": [fragment_id, source_object_id], "reason": reason},
            f"登记拆分关系 {relation_id}", actor=actor)
        return self._commit([fragment_event, relation_event])

    # ── 早期/历次检测（挂在对象自身流上，永不因拼合变化而丢失） ─────────
    def record_examination(self, *, examination_id: str, object_id: str, kind: str,
                           examined_at: str, data: dict | None = None,
                           technician: str | None = None, actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER, ROLE_CONSERVATOR), "登记检测")
        self._require_object(object_id)
        parse_dt(examined_at)
        return self._commit([self._event(
            EXAMINATION_RECORDED, object_id,
            {"examination_id": examination_id, "object_id": object_id, "kind": kind,
             "examined_at": examined_at, "data": data or {}, "technician": technician},
            f"{kind} 检测 {examination_id}（对象 {object_id}）",
            actor=actor, occurred_at=examined_at)])[0]

    # ── 候选身份 ──────────────────────────────────────────────────────
    def propose_candidate(self, *, object_id: str, proposed_form: str,
                          actor: dict, note: str | None = None) -> dict:
        self._require_role(actor, (ROLE_CONSERVATOR, ROLE_DUTY_OFFICER), "提出候选身份")
        self._require_object(object_id)
        candidate_id = _new_id("cand")
        return self._commit([self._event(
            CANDIDATE_PROPOSED, candidate_id,
            {"object_id": object_id, "proposed_form": proposed_form,
             "proposed_by": actor["id"], "note": note},
            f"提出候选身份：{proposed_form}", actor=actor)])[0]

    def decide_candidate(self, *, candidate_id: str, approve: bool, actor: dict,
                         reason: str | None = None) -> dict:
        self._require_role(actor, (ROLE_CONSERVATOR,), "确认/否决候选身份")
        candidate = self.read.candidates.get(candidate_id)
        if not candidate:
            raise DomainError(f"候选身份不存在：{candidate_id}")
        if candidate["status"] != "proposed":
            raise DomainError(f"候选身份已了结：{candidate['status']}")
        etype = CANDIDATE_CONFIRMED if approve else CANDIDATE_REJECTED
        payload = ({"confirmed_by": actor["id"]} if approve
                   else {"rejected_by": actor["id"], "reason": reason or "未说明"})
        word = "确认" if approve else "否决"
        return self._commit([self._event(
            etype, candidate_id, payload, f"{word}候选身份 {candidate_id}",
            actor=actor)])[0]

    # ── 残片拼合：暂拼 → 确认；撤销 = 新关系 ──────────────────────────
    def open_join(self, *, fragment_ids: list[str], figure_id: str, actor: dict,
                  note: str | None = None) -> dict:
        self._require_role(actor, (ROLE_CONSERVATOR,), "暂拼残片")
        self._validate_join_members(fragment_ids, figure_id)
        for fid in fragment_ids:
            self._require_not_frozen(fid, "暂拼")
        relation_id = _new_id("rel")
        return self._commit([self._event(
            RELATION_OPENED, relation_id,
            {"relation_kind": RELATION_TENTATIVE, "fragment_ids": list(fragment_ids),
             "figure_id": figure_id, "reason": note},
            f"暂拼关系 {relation_id}：{', '.join(fragment_ids)}", actor=actor)])[0]

    def confirm_join(self, *, relation_id: str, actor: dict) -> dict:
        self._require_role(actor, (ROLE_CONSERVATOR,), "确认拼合")
        relation = self.read.relations.get(relation_id)
        if not relation:
            raise DomainError(f"关系不存在：{relation_id}")
        if relation["relation_kind"] != RELATION_TENTATIVE:
            raise DomainError("只有暂拼关系可以确认")
        if relation["status"] != "open":
            raise DomainError(f"关系当前状态不可确认：{relation['status']}")
        for fid in relation["fragment_ids"]:
            self._require_object(fid)
            self._require_not_frozen(fid, "确认拼合")
        return self._commit([self._event(
            RELATION_CONFIRMED, relation_id, {"confirmed_by": actor["id"]},
            f"确认拼合 {relation_id}", actor=actor)])[0]

    def revoke_join(self, *, relation_id: str, actor: dict, reason: str) -> dict:
        """撤销错误拼合：不修改原关系，产生一条新的 join_revoked 关系。"""
        self._require_role(actor, (ROLE_CONSERVATOR,), "撤销拼合")
        original = self.read.relations.get(relation_id)
        if not original:
            raise DomainError(f"关系不存在：{relation_id}")
        if original["relation_kind"] != RELATION_TENTATIVE:
            raise DomainError("只能撤销暂拼/拼合关系")
        if original["status"] == "revoked":
            raise DomainError("关系已被撤销")
        new_relation_id = _new_id("rel")
        return self._commit([self._event(
            RELATION_OPENED, new_relation_id,
            {"relation_kind": RELATION_REVOKED, "original_relation_id": relation_id,
             "fragment_ids": list(original["fragment_ids"]),
             "figure_id": original.get("figure_id"), "reason": reason},
            f"撤销拼合 {relation_id}，原因：{reason}", actor=actor)])[0]

    def _validate_join_members(self, fragment_ids: list[str], figure_id: str) -> None:
        if len(set(fragment_ids)) < 2:
            raise DomainError("暂拼至少需要两块不同残片")
        for fid in fragment_ids:
            obj = self._require_object(fid)
            if obj.get("figure_id") != figure_id:
                raise DomainError(f"残片 {fid} 不属于俑体 {figure_id}")

    # ── 设备与校准 ────────────────────────────────────────────────────
    def register_instrument(self, *, instrument_code: str, metric: str,
                            valid_from: str, valid_to: str, cert_id: str,
                            actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER,), "登记探头")
        parse_dt(valid_from)
        parse_dt(valid_to)
        if parse_dt(valid_from) >= parse_dt(valid_to):
            raise DomainError("校准有效期起点必须早于终点")
        instrument_id = _new_id("inst")
        events = [self._event(
            INSTRUMENT_REGISTERED, instrument_id,
            {"instrument_code": instrument_code, "metric": metric,
             "valid_from": valid_from, "valid_to": valid_to},
            f"登记探头 {instrument_code}", actor=actor)]
        events.append(self._event(
            CALIBRATION_RECORDED, instrument_id,
            {"valid_from": valid_from, "valid_to": valid_to, "cert_id": cert_id},
            f"登记校准证书 {cert_id}", actor=actor))
        return self._commit(events)[0]

    def record_calibration(self, *, instrument_id: str, valid_from: str,
                           valid_to: str, cert_id: str, actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER,), "登记校准")
        if instrument_id not in self.read.instruments:
            raise DomainError(f"探头不存在：{instrument_id}")
        if parse_dt(valid_from) >= parse_dt(valid_to):
            raise DomainError("校准有效期起点必须早于终点")
        return self._commit([self._event(
            CALIBRATION_RECORDED, instrument_id,
            {"valid_from": valid_from, "valid_to": valid_to, "cert_id": cert_id},
            f"探头 {instrument_id} 新校准 {cert_id}", actor=actor)])[0]

    # ── 监测阈值 ──────────────────────────────────────────────────────
    def set_monitoring_limits(self, *, object_id: str, limits: dict, actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER, ROLE_CONSERVATOR), "设置监测阈值")
        self._require_object(object_id)
        humidity = limits.get(METRIC_HUMIDITY)
        if humidity is None or humidity[0] >= humidity[1]:
            raise DomainError("湿度阈值必须形如 humidity:[下限, 上限] 且下限 < 上限")
        return self._commit([self._event(
            LIMITS_SET, object_id, {"object_id": object_id, "limits": dict(limits)},
            f"设置对象 {object_id} 监测阈值", actor=actor)])[0]

    # ── 环境采样（补数 / 校准判定 / 越界告警） ────────────────────────
    def record_sample(self, *, series_id: str, object_id: str, instrument_id: str,
                      metric: str, value: float, collected_at: str,
                      quality: str = QUALITY_OK, actor: dict | None = None,
                      note: str | None = None) -> list[dict]:
        """写入一条环境测量。

        无论实时数据还是断线补数，都按 stream 末尾追加版本；
        投影按 ``collected_at`` 归位。质量标记与校准状态在此被强制。
        """
        parse_dt(collected_at)
        self._require_object(object_id)
        instrument = self.read.instruments.get(instrument_id)
        if not instrument:
            raise DomainError(f"探头不存在：{instrument_id}")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise DomainError("测量值必须是数值")

        calibration = self.read.calibration_valid(instrument_id, collected_at)
        final_quality = quality
        notes = [note] if note else []
        if calibration is None:
            # 校准失效：任何“可靠”声称都被强制降级，数据保留但不能驱动告警
            if quality in RELIABLE_QUALITIES:
                notes.append(f"采集时刻无有效校准，质量由 {quality} 强制降级")
            final_quality = QUALITY_UNCALIBRATED
        elif quality not in (QUALITY_OK, QUALITY_BACKFILL, QUALITY_INTERPOLATED,
                             QUALITY_SUSPECT, QUALITY_UNCALIBRATED):
            raise DomainError(f"未知质量标记：{quality}")

        payload = {"series_id": series_id, "object_id": object_id,
                   "instrument_id": instrument_id, "metric": metric, "value": value,
                   "collected_at": collected_at, "quality": final_quality,
                   "note": "；".join(notes) or None}
        if calibration:
            payload["calibration_cert_id"] = calibration["cert_id"]
        sample_summary = f"{metric}={value}（{final_quality}）@ {collected_at}"
        sample_event = self._event(
            ENVIRONMENT_SAMPLED, series_id, payload, sample_summary,
            actor=actor, occurred_at=self._now())
        events = [sample_event]

        # 失效校准 → 设备失准告警（按探头去重），影响的是该条数据对应的残片
        if calibration is None:
            if not self._open_alert_exists(ALERT_INSTRUMENT, instrument_id=instrument_id):
                alert_id = _new_id("alert")
                events.append(self._event(
                    ALERT_RAISED, alert_id,
                    {"alert_kind": ALERT_INSTRUMENT,
                     "affected_object_ids": [object_id], "raised_by": "system",
                     "instrument_id": instrument_id,
                     "note": "测量时刻无有效校准，数据标记为 uncalibrated"},
                    f"探头 {instrument_id} 校准失效，影响 {object_id}",
                    actor={"id": "system", "role": "system"}))

        # 越界判定：只有可靠质量（实时或真实补数）才能驱动告警
        if final_quality in RELIABLE_QUALITIES and metric == METRIC_HUMIDITY:
            limits = self.read.objects[object_id].get("monitoring_limits") or {}
            band = limits.get(METRIC_HUMIDITY)
            if band and not (band[0] <= value <= band[1]):
                if not self._open_alert_exists(ALERT_HUMIDITY, object_id=object_id):
                    alert_id = _new_id("alert")
                    events.append(self._event(
                        ALERT_RAISED, alert_id,
                        {"alert_kind": ALERT_HUMIDITY,
                         "affected_object_ids": [object_id], "raised_by": "system",
                         "metric": metric, "value": value, "limit": band,
                         "related_event_id": sample_event["event_id"]},
                        f"湿度越界：{object_id} 实测 {value}，允许 {band}",
                        actor={"id": "system", "role": "system"}))
                    # 异常只冻结对应对象
                    events.append(self._event(
                        HOLD_PLACED, object_id,
                        {"object_id": object_id,
                         "reason": f"湿度越界 {value} 超出 {band}",
                         "placed_by": "system", "alert_id": alert_id},
                        f"冻结对象 {object_id}（湿度越界）",
                        actor={"id": "system", "role": "system"}))
        stored = self._commit(events)
        return stored

    def _open_alert_exists(self, kind: str, *, object_id: str | None = None,
                           instrument_id: str | None = None) -> bool:
        for alert in self.read.alerts.values():
            if alert["status"] == "resolved":
                continue
            if alert["alert_kind"] != kind:
                continue
            if instrument_id is not None and alert.get("instrument_id") == instrument_id:
                return True
            if object_id is not None and object_id in alert["affected_object_ids"]:
                return True
        return False

    # ── 告警处置（只能人工；系统不生成修复动作） ──────────────────────
    def acknowledge_alert(self, *, alert_id: str, actor: dict,
                          note: str | None = None) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER, ROLE_CONSERVATOR), "签收告警")
        alert = self._require_alert(alert_id)
        if alert["status"] != "raised":
            raise DomainError(f"告警状态为 {alert['status']}，无需签收")
        return self._commit([self._event(
            ALERT_ACKNOWLEDGED, alert_id,
            {"acknowledged_by": actor["id"], "note": note},
            f"{actor['id']} 签收告警 {alert_id}", actor=actor)])[0]

    def resolve_alert(self, *, alert_id: str, actor: dict, resolution: str) -> list[dict]:
        """人工处置完成：关闭告警并解除其带来的对象冻结。"""
        self._require_role(actor, (ROLE_DUTY_OFFICER, ROLE_CONSERVATOR), "处置告警")
        alert = self._require_alert(alert_id)
        if alert["status"] == "resolved":
            raise DomainError("告警已处置")
        events = [self._event(
            ALERT_RESOLVED, alert_id,
            {"resolved_by": actor["id"], "resolution": resolution},
            f"处置告警 {alert_id}：{resolution}", actor=actor)]
        # 释放关联冻结
        for oid in alert["affected_object_ids"]:
            for hold in self.read.active_holds(oid):
                if hold.get("alert_id") == alert_id:
                    events.append(self._event(
                        HOLD_RELEASED, oid,
                        {"object_id": oid, "hold_id": hold["hold_id"],
                         "released_by": actor["id"]},
                        f"解除对象 {oid} 冻结（告警 {alert_id} 已处置）", actor=actor))
        return self._commit(events)

    def _require_alert(self, alert_id: str) -> dict:
        alert = self.read.alerts.get(alert_id)
        if not alert:
            raise DomainError(f"告警不存在：{alert_id}")
        return alert

    # ── 充氮保湿 / 运输封装 ───────────────────────────────────────────
    def prepare_package(self, *, package_code: str, object_ids: list[str],
                        humidity_limits: dict, actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER, ROLE_REGISTRAR), "建立封装")
        for oid in object_ids:
            self._require_object(oid)
        package_id = _new_id("pkg")
        return self._commit([self._event(
            PACKAGE_PREPARED, package_id,
            {"package_code": package_code, "object_ids": list(object_ids),
             "humidity_limits": humidity_limits},
            f"建立封装 {package_code}", actor=actor)])[0]

    def seal_package(self, *, package_id: str, actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER,), "封箱")
        package = self.read.packages.get(package_id)
        if not package:
            raise DomainError(f"封装不存在：{package_id}")
        if package["status"] != "prepared":
            raise DomainError(f"封装状态为 {package['status']}，不能封箱")
        for oid in package["object_ids"]:
            self._require_not_frozen(oid, "封箱发运")
        return self._commit([self._event(
            PACKAGE_SEALED, package_id, {"sealed_by": actor["id"]},
            f"封装 {package_id} 充氮封箱", actor=actor)])[0]

    def refresh_nitrogen(self, *, package_id: str, nitrogen_purity: float,
                         actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER,), "补充氮气")
        if package_id not in self.read.packages:
            raise DomainError(f"封装不存在：{package_id}")
        if not 0 < nitrogen_purity <= 100:
            raise DomainError("氮气纯度必须在 (0, 100]")
        return self._commit([self._event(
            PACKAGE_N2_REFRESHED, package_id,
            {"refreshed_by": actor["id"], "nitrogen_purity": nitrogen_purity},
            f"封装 {package_id} 补充氮气，纯度 {nitrogen_purity}", actor=actor)])[0]

    def record_package_status(self, *, package_id: str, seal_integrity: str,
                              actor: dict, note: str | None = None) -> list[dict]:
        self._require_role(actor, (ROLE_DUTY_OFFICER,), "记录封装状态")
        package = self.read.packages.get(package_id)
        if not package:
            raise DomainError(f"封装不存在：{package_id}")
        events = [self._event(
            PACKAGE_STATUS_RECORDED, package_id,
            {"seal_integrity": seal_integrity, "recorded_by": actor["id"],
             "note": note},
            f"封装 {package_id} 密封状态：{seal_integrity}", actor=actor)]
        if seal_integrity == "leaking":
            # 封装异常：冻结该封装内的对象（且仅这些对象）
            for oid in package["object_ids"]:
                if self._open_alert_exists(ALERT_PACKAGE, object_id=oid):
                    continue
                alert_id = _new_id("alert")
                events.append(self._event(
                    ALERT_RAISED, alert_id,
                    {"alert_kind": ALERT_PACKAGE, "affected_object_ids": [oid],
                     "raised_by": actor["id"], "package_id": package_id,
                     "note": note or "封装密封异常"},
                    f"封装 {package_id} 异常，影响 {oid}", actor=actor))
                events.append(self._event(
                    HOLD_PLACED, oid,
                    {"object_id": oid, "reason": f"封装 {package_id} 密封异常",
                     "placed_by": actor["id"], "alert_id": alert_id},
                    f"冻结对象 {oid}（封装异常）", actor=actor))
        return self._commit(events)

    # ── 修复干预（只能由修复人员发起；告警永远不会自动变成动作） ───────
    def propose_action(self, *, action_kind: str, target_object_id: str, actor: dict,
                       rationale: str, related_alert_id: str | None = None) -> dict:
        self._require_role(actor, (ROLE_CONSERVATOR,), "提出修复干预")
        self._require_object(target_object_id)
        if related_alert_id:
            self._require_alert(related_alert_id)
        action_id = _new_id("act")
        return self._commit([self._event(
            ACTION_PROPOSED, action_id,
            {"action_kind": action_kind, "target_object_id": target_object_id,
             "proposed_by": actor["id"], "rationale": rationale,
             "related_alert_id": related_alert_id},
            f"{actor['id']} 提议{action_kind}（{target_object_id}）", actor=actor)])[0]

    def authorize_action(self, *, action_id: str, actor: dict) -> dict:
        self._require_role(actor, (ROLE_CONSERVATOR,), "批准修复干预")
        action = self.read.actions.get(action_id)
        if not action:
            raise DomainError(f"干预不存在：{action_id}")
        if action["status"] != "proposed":
            raise DomainError(f"干预状态为 {action['status']}，不能批准")
        return self._commit([self._event(
            ACTION_AUTHORIZED, action_id, {"authorizer_id": actor["id"]},
            f"批准干预 {action_id}", actor=actor)])[0]

    def complete_action(self, *, action_id: str, actor: dict, result: str) -> dict:
        self._require_role(actor, (ROLE_CONSERVATOR,), "完成修复干预")
        action = self.read.actions.get(action_id)
        if not action:
            raise DomainError(f"干预不存在：{action_id}")
        if action["status"] != "authorized":
            raise DomainError(f"干预状态为 {action['status']}，不能记录完成")
        return self._commit([self._event(
            ACTION_COMPLETED, action_id,
            {"completed_by": actor["id"], "result": result},
            f"完成干预 {action_id}：{result}", actor=actor)])[0]

    # ── 责任交接（双方共同签署；冻结对象不转移，其余正常交接） ─────────
    def prepare_handoff(self, *, handoff_code: str, from_party: str, to_party: str,
                        object_ids: list[str], actor: dict) -> dict:
        self._require_role(actor, (ROLE_DUTY_OFFICER, ROLE_REGISTRAR), "发起交接")
        if from_party == to_party:
            raise DomainError("交出方与接收方不能相同")
        for oid in object_ids:
            self._require_object(oid)
        handoff_id = _new_id("hand")
        return self._commit([self._event(
            HANDOFF_PREPARED, handoff_id,
            {"handoff_code": handoff_code, "from_party": from_party,
             "to_party": to_party, "object_ids": list(object_ids)},
            f"发起交接 {handoff_code}：{from_party} → {to_party}", actor=actor)])[0]

    def sign_handoff(self, *, handoff_id: str, releaser: dict, receiver: dict) -> dict:
        """交出与接收双方共同签署；责任只对未冻结对象发生变化。"""
        if not releaser.get("id") or not receiver.get("id"):
            raise DomainError("交接必须由交出方与接收方共同签署")
        if releaser["id"] == receiver["id"]:
            raise DomainError("交出方与接收方不能为同一人")
        handoff = self.read.handoffs.get(handoff_id)
        if not handoff:
            raise DomainError(f"交接不存在：{handoff_id}")
        if handoff["status"] != "prepared":
            raise DomainError(f"交接状态为 {handoff['status']}，不能签署")
        frozen = [oid for oid in handoff["object_ids"] if self.read.is_frozen(oid)]
        transferred = [oid for oid in handoff["object_ids"] if oid not in frozen]
        if not transferred:
            raise DomainError("全部对象处于冻结状态，本次交接无对象可转移")
        return self._commit([self._event(
            HANDOFF_SIGNED, handoff_id,
            {"releaser": releaser["id"], "receiver": receiver["id"],
             "transferred_object_ids": transferred, "frozen_object_ids": frozen},
            f"双方签署交接 {handoff_id}，转移 {len(transferred)} 件"
            + (f"，冻结留存 {len(frozen)} 件" if frozen else ""),
            actor={"id": f"{releaser['id']}+{receiver['id']}", "role": "duty_officer"})])[0]

    # ── 入库 ──────────────────────────────────────────────────────────
    def register_storage_location(self, *, location_code: str, area: str,
                                  actor: dict) -> dict:
        self._require_role(actor, (ROLE_REGISTRAR,), "登记入库位置")
        location_id = _new_id("loc")
        return self._commit([self._event(
            STORAGE_REGISTERED, location_id,
            {"location_code": location_code, "area": area},
            f"登记库位 {location_code}", actor=actor)])[0]

    def accession(self, *, object_id: str, storage_location_id: str,
                  accession_code: str, actor: dict) -> dict:
        self._require_role(actor, (ROLE_REGISTRAR,), "入库")
        self._require_object(object_id)
        self._require_not_frozen(object_id, "入库")
        if storage_location_id not in self.read.storage:
            raise DomainError(f"库位不存在：{storage_location_id}")
        if self.read.objects[object_id].get("accession_code"):
            raise DomainError("对象已入库")
        return self._commit([self._event(
            OBJECT_ACCESSIONED, object_id,
            {"object_id": object_id, "storage_location_id": storage_location_id,
             "accession_code": accession_code, "accessioned_by": actor["id"]},
            f"对象 {object_id} 入库 {accession_code}", actor=actor)])[0]
