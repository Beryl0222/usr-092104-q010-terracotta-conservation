import json
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from src.api import ApiApp, build_server
from src.store import EventStore


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = build_server("127.0.0.1", 0, app=ApiApp(EventStore()))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()

    def conn(self) -> HTTPConnection:
        return HTTPConnection("127.0.0.1", self.port, timeout=5)

    def post(self, path: str, body: dict, headers: dict | None = None):
        conn = self.conn()
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        conn.request("POST", path, body=json.dumps(body).encode(), headers=hdrs)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())

    def get(self, path: str, role: str = "public", scopes: str | None = None):
        conn = self.conn()
        headers = {"X-Role": role}
        if scopes:
            headers["X-Scopes"] = scopes
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())

    def test_raw_event_ingest_replays_into_read_model(self) -> None:
        # 先建探方与地层，满足服务层不校验原始入口之外的引用；原始入口只做契约校验
        trench_status, trench = self.post("/commands/open-trench", {
            "trench_code": "T1", "sensitive": False, "grid_coordinates": {},
            "actor": {"id": "u1", "role": "duty_officer"}})
        self.assertEqual(trench_status, 201)
        trench_id = trench["events"][0]["aggregate_id"]
        status, _ = self.post("/commands/record-stratum", {
            "trench_id": trench_id, "stratum_id": "s1", "depth_top": 0.4,
            "depth_bottom": 1.0, "period_label": "秦",
            "actor": {"id": "u1", "role": "duty_officer"}})
        self.assertEqual(status, 201)

        raw = {
            "event_id": "raw-1",
            "event_type": "OBJECT_LIFTED",
            "aggregate_type": "excavated_object",
            "aggregate_id": "raw-fig",
            "occurred_at": "2026-10-04T10:00:00+08:00",
            "version": 1,
            "summary": "现场系统直送事件",
            "payload": {"object_kind": "figure", "trench_id": trench_id,
                        "stratum_id": "s1", "publication_state": "unpublished"},
        }
        status, payload = self.post("/events", raw)
        self.assertEqual(status, 201)
        status, body = self.get("/objects/raw-fig/lineage", role="duty_officer")
        self.assertEqual(status, 200)
        self.assertEqual(body["object_id"], "raw-fig")

    def test_invalid_raw_event_rejected(self) -> None:
        status, payload = self.post("/events", {"event_id": "bad"})
        self.assertEqual(status, 422)
        self.assertIn("缺少字段", payload["error"])

    def test_unknown_command_404(self) -> None:
        status, _ = self.post("/commands/nope", {})
        self.assertEqual(status, 404)

    def test_domain_violation_returns_422(self) -> None:
        status, payload = self.post("/commands/open-trench", {
            "trench_code": "T2", "sensitive": False, "grid_coordinates": {},
            "actor": {"id": "p", "role": "public"}})
        self.assertEqual(status, 422)
        self.assertIn("无权", payload["error"])

    def test_authorization_headers_enforced_on_get(self) -> None:
        # 空库下公众查对象 → 404；未知角色 → 401
        status, _ = self.get("/objects/ghost/lineage", role="ghost")
        self.assertEqual(status, 401)
        status, _ = self.get("/objects/ghost/lineage", role="public")
        self.assertEqual(status, 404)

    def test_full_command_chain_over_http(self) -> None:
        officer = {"id": "u1", "role": "duty_officer"}
        cons = {"id": "u2", "role": "conservator"}
        _, trench = self.post("/commands/open-trench", {
            "trench_code": "T1", "sensitive": True,
            "grid_coordinates": {"x": 1}, "actor": officer})
        trench_id = trench["events"][0]["aggregate_id"]
        self.post("/commands/record-stratum", {
            "trench_id": trench_id, "stratum_id": "s1", "depth_top": 0.4,
            "depth_bottom": 1.0, "period_label": "秦", "actor": officer})
        self.post("/commands/lift-object", {
            "object_id": "fig", "object_kind": "figure",
            "trench_id": trench_id, "stratum_id": "s1", "actor": officer})
        self.post("/commands/detach-fragment", {
            "fragment_id": "fa", "source_object_id": "fig", "figure_id": "fig",
            "reason": "r", "actor": cons})
        _, inst = self.post("/commands/register-instrument", {
            "instrument_code": "RH", "metric": "humidity",
            "valid_from": "2026-09-01T00:00:00+08:00",
            "valid_to": "2026-10-15T00:00:00+08:00",
            "cert_id": "c", "actor": officer})
        inst_id = inst["events"][0]["aggregate_id"]
        self.post("/commands/set-monitoring-limits", {
            "object_id": "fa", "limits": {"humidity": [40, 60]},
            "actor": officer})
        status, _ = self.post("/commands/record-sample", {
            "series_id": "s", "object_id": "fa", "instrument_id": inst_id,
            "metric": "humidity", "value": 90,
            "collected_at": "2026-10-04T10:00:00+08:00",
            "quality": "ok", "actor": officer})
        self.assertEqual(status, 201)
        status, dash = self.get("/alerts", role="duty_officer")
        self.assertEqual(status, 200)
        self.assertEqual(dash["frozen_object_ids"], ["fa"])

        # 公众拿不到告警面板
        status, _ = self.get("/alerts", role="public")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
