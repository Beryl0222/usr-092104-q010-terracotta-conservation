"""HTTP 接口（仅依赖标准库）。

写入分两类：
- ``POST /events``：现场系统的原始事件入口（仓库现有事件版本继续使用），
  只做契约校验后追加进事件存储，再纳入读模型；
- ``POST /commands/<command>``：面向人工操作的命令式接口，
  经 :class:`~src.service.HandoffService` 强制全部业务不变量。

身份与授权：请求头 ``X-Role`` 给出角色，``X-Scopes`` 以逗号分隔附加许可；
GET 接口按角色分级裁剪。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from src.auth import AccessContext, AccessDenied, context as make_context
from src.contract import ROLES
from src.service import DomainError, HandoffService
from src.store import EventStore, StoreError
from src.views import Queries

# 命令名 → 服务方法
COMMANDS = {
    "open-trench": "open_trench",
    "record-stratum": "record_stratum",
    "lift-object": "lift_object",
    "detach-fragment": "detach_fragment",
    "record-examination": "record_examination",
    "propose-candidate": "propose_candidate",
    "decide-candidate": "decide_candidate",
    "open-join": "open_join",
    "confirm-join": "confirm_join",
    "revoke-join": "revoke_join",
    "register-instrument": "register_instrument",
    "record-calibration": "record_calibration",
    "set-monitoring-limits": "set_monitoring_limits",
    "record-sample": "record_sample",
    "acknowledge-alert": "acknowledge_alert",
    "resolve-alert": "resolve_alert",
    "prepare-package": "prepare_package",
    "seal-package": "seal_package",
    "refresh-nitrogen": "refresh_nitrogen",
    "record-package-status": "record_package_status",
    "propose-action": "propose_action",
    "authorize-action": "authorize_action",
    "complete-action": "complete_action",
    "prepare-handoff": "prepare_handoff",
    "sign-handoff": "sign_handoff",
    "register-storage-location": "register_storage_location",
    "accession": "accession",
}


class ApiApp:
    def __init__(self, store: EventStore | None = None) -> None:
        self.store = store or EventStore()
        self.service = HandoffService(self.store)
        self.queries = Queries(self.service.read)

    def ingest_event(self, event: dict) -> dict:
        stored = self.store.append(event)
        self.service.read.apply(stored)
        return stored

    def run_command(self, name: str, body: dict) -> object:
        method_name = COMMANDS.get(name)
        if not method_name:
            raise KeyError(name)
        actor = body.get("actor")
        kwargs = {k: v for k, v in body.items() if k != "actor"}
        if actor is not None:
            kwargs["actor"] = actor
        result = getattr(self.service, method_name)(**kwargs)
        if isinstance(result, list):
            return [self._event_ref(e) for e in result]
        return [self._event_ref(result)]

    @staticmethod
    def _event_ref(event: dict) -> dict:
        return {"event_id": event["event_id"], "event_type": event["event_type"],
                "aggregate_id": event["aggregate_id"], "version": event["version"],
                "occurred_at": event["occurred_at"]}


def _ctx_from_headers(headers) -> AccessContext:
    role = headers.get("X-Role", "public")
    if role not in ROLES:
        raise AccessDenied(f"未知角色：{role}")
    scopes = {s.strip() for s in headers.get("X-Scopes", "").split(",") if s.strip()}
    return make_context(role, scopes)


def create_handler(app: ApiApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "TerracottaHandoff/1.0"

        def log_message(self, fmt, *args):  # 安静：测试输出不被访问日志污染
            pass

        def _send(self, status: int, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_POST(self) -> None:
            path = urlparse(self.path).path.strip("/")
            try:
                body = self._read_json()
                if path == "events":
                    stored = app.ingest_event(body)
                    self._send(201, {"status": "accepted", "event": app._event_ref(stored)})
                    return
                if path.startswith("commands/"):
                    name = path.split("/", 1)[1]
                    try:
                        result = app.run_command(name, body)
                    except KeyError:
                        self._send(404, {"error": f"未知命令：{name}"})
                        return
                    self._send(201, {"status": "ok", "events": result})
                    return
                self._send(404, {"error": "路径不存在"})
            except (StoreError, DomainError) as exc:
                self._send(422, {"error": str(exc)})
            except (ValueError, TypeError) as exc:
                self._send(400, {"error": f"请求不合法：{exc}"})

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.strip("/").split("/")
            try:
                ctx = _ctx_from_headers(self.headers)
            except AccessDenied as exc:
                self._send(401, {"error": str(exc)})
                return
            try:
                q = app.queries
                if path == ["objects"]:
                    figure_id = None
                    for part in parsed.query.split("&"):
                        if part.startswith("figure_id="):
                            from urllib.parse import unquote
                            figure_id = unquote(part.split("=", 1)[1])
                    self._send(200, {"objects": q.list_objects(ctx, figure_id=figure_id)})
                elif len(path) == 3 and path[0] == "objects" and path[2] == "lineage":
                    self._send(200, q.fragment_lineage(path[1], ctx))
                elif len(path) == 2 and path[0] == "figures":
                    self._send(200, q.figure_overview(path[1], ctx))
                elif path == ["alerts"]:
                    include_resolved = "all=1" in parsed.query.split("&")
                    self._send(200, q.alert_dashboard(ctx, include_resolved=include_resolved))
                elif len(path) == 2 and path[0] == "series":
                    self._send(200, q.environment_series(path[1], ctx))
                elif len(path) == 2 and path[0] == "trenches":
                    self._send(200, q.trench(path[1], ctx))
                elif path == ["health"]:
                    self._send(200, {"status": "ok",
                                     "events": app.service.read.event_count})
                else:
                    self._send(404, {"error": "路径不存在"})
            except AccessDenied as exc:
                self._send(403, {"error": str(exc)})
            except KeyError as exc:
                self._send(404, {"error": str(exc).strip("'\"")})

    return Handler


def build_server(host: str, port: int, app: ApiApp | None = None,
                 jsonl_path: str | None = None) -> ThreadingHTTPServer:
    app = app or ApiApp(EventStore(jsonl_path))
    return ThreadingHTTPServer((host, port), create_handler(app))


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="秦俑发掘保护交接后端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--store", help="事件 JSONL 持久化路径")
    args = parser.parse_args()
    server = build_server(args.host, args.port, jsonl_path=args.store)
    print(f"交接后端监听 http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
