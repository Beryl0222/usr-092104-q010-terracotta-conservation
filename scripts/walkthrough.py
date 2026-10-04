#!/usr/bin/env python3
"""全链路联调演示：通过 HTTP 接口走完发掘 → 监测 → 告警 → 拼合 → 封装 → 交接 → 入库。

直接运行：``python3 scripts/walkthrough.py``
脚本在后台线程启动服务，不依赖任何第三方库。
"""
from __future__ import annotations

import json
import sys
import threading
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api import build_server  # noqa: E402
from src.api import ApiApp  # noqa: E402
from src.store import EventStore  # noqa: E402

OFFICER = {"id": "zhang-duty", "role": "duty_officer"}
CONS = {"id": "li-cons", "role": "conservator"}
REG = {"id": "wang-reg", "role": "registrar"}


def main() -> None:
    server = build_server("127.0.0.1", 0, app=ApiApp(EventStore()))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = ApiClient(port)
    step = Stepper()

    # 1 ── 探方 / 地层 / 出土（敏感坑位、未发布） ─────────────────────
    step("开设敏感探方并登记地层、起取俑体")
    trench = api.cmd("open-trench", {
        "trench_code": "K99-T03", "sensitive": True,
        "grid_coordinates": {"north": 34.384, "east": 109.273}, "actor": OFFICER})
    trench_id = trench["events"][0]["aggregate_id"]
    api.cmd("record-stratum", {"trench_id": trench_id, "stratum_id": "layer-2",
                               "depth_top": 1.1, "depth_bottom": 1.8,
                               "period_label": "秦代文化层", "actor": OFFICER})
    api.cmd("lift-object", {"object_id": "fig-07", "object_kind": "figure",
                            "trench_id": trench_id, "stratum_id": "layer-2",
                            "publication_state": "unpublished", "actor": OFFICER})

    # 2 ── 拆分残片，早期超声 + 三维检测挂在残片自身 ──────────────────
    step("拆分胸甲残片 frag-a、披膊残片 frag-b，登记早期检测")
    api.cmd("detach-fragment", {"fragment_id": "frag-a", "source_object_id": "fig-07",
                                "figure_id": "fig-07", "reason": "彩绘层起翘，现场拆分保护",
                                "actor": CONS})
    api.cmd("detach-fragment", {"fragment_id": "frag-b", "source_object_id": "fig-07",
                                "figure_id": "fig-07", "reason": "披膊断裂分离",
                                "actor": CONS})
    api.cmd("record-examination", {"examination_id": "us-001", "object_id": "frag-a",
                                   "kind": "ultrasonic",
                                   "examined_at": "2026-10-04T08:10:00+08:00",
                                   "data": {"velocity_m_s": 2150}, "actor": CONS})
    api.cmd("record-examination", {"examination_id": "scan-001", "object_id": "frag-a",
                                   "kind": "scan3d",
                                   "examined_at": "2026-10-04T08:40:00+08:00",
                                   "data": {"mesh": "frag-a-v1.ply"}, "actor": CONS})

    # 3 ── 探头、校准、阈值 ───────────────────────────────────────────
    step("登记湿度探头 RH-07（校准 9/1–10/15）并设置 frag-a 湿度阈值 45–60%")
    inst = api.cmd("register-instrument", {
        "instrument_code": "RH-07", "metric": "humidity",
        "valid_from": "2026-09-01T00:00:00+08:00",
        "valid_to": "2026-10-15T00:00:00+08:00",
        "cert_id": "CAL-2026-0901", "actor": OFFICER})
    inst_id = inst["events"][0]["aggregate_id"]
    api.cmd("set-monitoring-limits", {"object_id": "frag-a",
                                      "limits": {"humidity": [45, 60]}, "actor": OFFICER})

    # 4 ── 实时正常测量 → 无告警 ─────────────────────────────────────
    step("09:00 实时湿度 52%（正常）")
    api.cmd("record-sample", sample("ser-a", "frag-a", inst_id, 52.0,
                                    "2026-10-04T09:00:00+08:00", "ok"))

    # 5 ── 断线补数：08:30 的真实测量 11:00 才入库，按采集时间归位 ─────
    step("探头断线恢复，补传 08:30 实测 47%（measured_backfill）")
    api.cmd("record-sample", sample("ser-a", "frag-a", inst_id, 47.0,
                                    "2026-10-04T08:30:00+08:00", "measured_backfill",
                                    note="断线补数，手持终端核对"))

    # 6 ── 湿度越界：告警 + 仅冻结 frag-a ────────────────────────────
    step("09:20 实时湿度 78% 越界 → 系统告警并只冻结 frag-a")
    api.cmd("record-sample", sample("ser-a", "frag-a", inst_id, 78.0,
                                    "2026-10-04T09:20:00+08:00", "ok"))
    dash = api.get("/alerts", role="duty_officer")
    alert = dash["alerts"][0]
    print(f"    值班面板：{alert['label']}，受影响残片 → "
          f"{[f['object_id'] for f in alert['affected_fragments']]}，"
          f"冻结列表 {dash['frozen_object_ids']}")
    humidity_alert = alert["alert_id"]

    # 7 ── 冻结阻止拼合；告警不会自动产生修复动作 ─────────────────────
    step("冻结状态下暂拼被拒；系统未自动生成任何清理/加固干预")
    denied = api.cmd_expect_error("open-join", {
        "fragment_ids": ["frag-a", "frag-b"], "figure_id": "fig-07", "actor": CONS})
    print(f"    暂拼被拒：{denied}")
    lineage = api.get("/objects/frag-a/lineage", role="conservator")
    print(f"    系统自动产生的修复干预数：{len(lineage['conservation_actions'])}")

    # 8 ── 修复人员自主决定干预；值班签收 + 处置 → 解冻 ────────────────
    step("修复人员评估后主动提议清理，值班处置环境告警并解冻")
    api.cmd("propose-action", {"action_kind": "cleaning", "target_object_id": "frag-a",
                               "rationale": "湿度波动后表面轻微盐析",
                               "related_alert_id": humidity_alert, "actor": CONS})
    api.cmd("acknowledge-alert", {"alert_id": humidity_alert, "actor": OFFICER})
    api.cmd("resolve-alert", {"alert_id": humidity_alert, "actor": OFFICER,
                              "resolution": "现场更换保湿缓冲材料，湿度回落至 55%"})

    # 9 ── 校准失效数据：强制降级，不冒充可靠测量，不驱动湿度告警 ────────
    step("11:00 收到一条 10/20 的补传数据：校准已过期 → 强制 uncalibrated")
    api.cmd("record-sample", sample("ser-a", "frag-a", inst_id, 95.0,
                                    "2026-10-20T10:00:00+08:00", "measured_backfill",
                                    note="采集器时钟漂移，事后导出"))
    series = api.get("/series/ser-a", role="duty_officer")
    print("    时序按采集时间归位：")
    for row in series["samples"]:
        print(f"      {row['collected_at']}  湿度={row['value']:>4}%  质量={row['quality']}")
    dash2 = api.get("/alerts?all=1", role="duty_officer")
    kinds = {a["kind"] for a in dash2["alerts"]}
    print(f"    告警种类：{sorted(kinds)}（95% 越界值因校准失效未触发湿度越界告警）")

    # 10 ── 暂拼 → 确认 → 撤销（撤销只产生新关系） ───────────────────
    step("暂拼 frag-a/frag-b → 确认拼合 → 发现断面不匹配，撤销")
    rel = api.cmd("open-join", {"fragment_ids": ["frag-a", "frag-b"],
                                "figure_id": "fig-07", "note": "披膊与胸甲试拼",
                                "actor": CONS})
    rel_id = rel["events"][0]["aggregate_id"]
    api.cmd("confirm-join", {"relation_id": rel_id, "actor": CONS})
    api.cmd("revoke-join", {"relation_id": rel_id, "reason": "断面胎体纹路不连续",
                            "actor": CONS})
    lin = api.get("/objects/frag-a/lineage", role="conservator")
    print(f"    关系轨迹：{[(r['kind'], r['status']) for r in lin['relations']]}")
    print(f"    早期检测仍在：{[e['examination_id'] for e in lin['examinations']]}")

    # 11 ── 封装异常：仅封装内对象冻结 ───────────────────────────────
    step("frag-b 充氮封装运输，途中记录密封泄漏 → 只冻结 frag-b")
    pkg = api.cmd("prepare-package", {"package_code": "N2-PKG-12",
                                      "object_ids": ["frag-b"],
                                      "humidity_limits": {"humidity": [45, 60]},
                                      "actor": OFFICER})
    pkg_id = pkg["events"][0]["aggregate_id"]
    api.cmd("seal-package", {"package_id": pkg_id, "actor": OFFICER})
    api.cmd("record-package-status", {"package_id": pkg_id, "seal_integrity": "leaking",
                                      "note": "运输颠簸后封条翘边", "actor": OFFICER})
    fig = api.get("/figures/fig-07", role="conservator")
    freeze_state = ", ".join(f"{f['object_id']}={f['frozen']}" for f in fig["fragments"])
    print(f"    俑体残片冻结状态：{{{freeze_state}}}")

    # 12 ── 双方签署交接：冻结的 frag-b 留存，frag-a 正常转移 ──────────
    step("发掘队 → 修复实验室交接，双方共同签署")
    ho = api.cmd("prepare-handoff", {"handoff_code": "HO-20261004-01",
                                     "from_party": "field_excavation_team",
                                     "to_party": "conservation_lab",
                                     "object_ids": ["frag-a", "frag-b"],
                                     "actor": OFFICER})
    handoff_id = ho["events"][0]["aggregate_id"]
    api.cmd("sign-handoff", {"handoff_id": handoff_id,
                             "releaser": {"id": "zhang-duty"},
                             "receiver": {"id": "li-cons"}})
    lin_a = api.get("/objects/frag-a/lineage", role="conservator")
    lin_b = api.get("/objects/frag-b/lineage", role="conservator")
    print(f"    frag-a 责任方：{lin_a['current_party']}；"
          f"交接记录责任已转移={lin_a['handoffs'][0]['responsibility_transferred']}")
    print(f"    frag-b 责任方：{lin_b['current_party']}；"
          f"签署时冻结留存={lin_b['handoffs'][0]['frozen_at_signing']}")

    # 13 ── 入库：冻结对象拒绝入库，解冻后入库 ─────────────────────────
    step("frag-b 处置解冻后入库；冻结期间入库被拒")
    loc = api.cmd("register-storage-location", {"location_code": "B2-货架07",
                                                "area": "彩绘文物恒温库房",
                                                "actor": REG})
    loc_id = loc["events"][0]["aggregate_id"]
    print(f"    冻结时入库：{api.cmd_expect_error('accession', {
        'object_id': 'frag-b', 'storage_location_id': loc_id,
        'accession_code': 'ACC-2026-0502', 'actor': REG})}")
    pkg_alert = [a["alert_id"] for a in api.get("/alerts", role="duty_officer")["alerts"]
                 if a["kind"] == "package_anomaly"][0]
    api.cmd("acknowledge-alert", {"alert_id": pkg_alert, "actor": OFFICER})
    api.cmd("resolve-alert", {"alert_id": pkg_alert, "actor": OFFICER,
                              "resolution": "重新充氮封装并更换封条"})
    api.cmd("accession", {"object_id": "frag-b", "storage_location_id": loc_id,
                          "accession_code": "ACC-2026-0502", "actor": REG})

    # 14 ── 分级授权：研究者与公众 ───────────────────────────────────
    step("分级授权核对")
    print(f"    公众看未发布残片：{api.get_expect_status('/objects/frag-a/lineage', role='public')}")
    print(f"    普通研究者对象列表：{[o['object_id'] for o in api.get('/objects', role='researcher')['objects']]}")
    print(f"    授权未发布的研究者：{[o['object_id'] for o in api.get('/objects', role='researcher', scopes='unpublished_finds')['objects']]}")
    trench_pub = api.get(f"/trenches/{trench_id}", role="public")
    trench_res = api.get(f"/trenches/{trench_id}", role="researcher",
                         scopes="unpublished_finds,sensitive_locations")
    print(f"    敏感坑位坐标：公众={trench_pub['grid_coordinates']}，"
          f"授权研究者={trench_res['grid_coordinates']}")
    print(f"    公众访问告警面板：{api.get_expect_status('/alerts', role='public')}")

    server.shutdown()
    print("\n全部场景通过。")


def sample(series_id: str, object_id: str, inst_id: str, value: float,
           collected_at: str, quality: str, note: str | None = None) -> dict:
    body = {"series_id": series_id, "object_id": object_id, "instrument_id": inst_id,
            "metric": "humidity", "value": value, "collected_at": collected_at,
            "quality": quality, "actor": OFFICER}
    if note:
        body["note"] = note
    return body


class Stepper:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self, title: str) -> None:
        self.n += 1
        print(f"\n[{self.n:02d}] {title}")


class ApiClient:
    def __init__(self, port: int) -> None:
        self.port = port

    def _conn(self) -> HTTPConnection:
        return HTTPConnection("127.0.0.1", self.port, timeout=5)

    def cmd(self, name: str, body: dict) -> dict:
        conn = self._conn()
        conn.request("POST", f"/commands/{name}", body=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        if resp.status != 201:
            raise AssertionError(f"命令 {name} 失败（{resp.status}）：{payload}")
        return payload

    def cmd_expect_error(self, name: str, body: dict) -> str:
        conn = self._conn()
        conn.request("POST", f"/commands/{name}", body=json.dumps(body).encode(),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        assert resp.status == 422, f"预期 422，实际 {resp.status}：{payload}"
        return payload["error"]

    def get(self, path: str, *, role: str = "public", scopes: str | None = None) -> dict:
        conn = self._conn()
        headers = {"X-Role": role}
        if scopes:
            headers["X-Scopes"] = scopes
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        payload = json.loads(resp.read())
        if resp.status != 200:
            raise AssertionError(f"GET {path} 失败（{resp.status}）：{payload}")
        return payload

    def get_expect_status(self, path: str, *, role: str) -> int:
        conn = self._conn()
        conn.request("GET", path, headers={"X-Role": role})
        resp = conn.getresponse()
        resp.read()
        return resp.status


if __name__ == "__main__":
    main()
