"""只追加事件存储。

- 事件一旦写入不可修改、不可删除（撤销类业务通过新事件表达）。
- 每个聚合一条流，``version`` 是流内从 1 开始的严格递增序号，
  与业务时间（如采集时间 ``collected_at``）无关：断线补数晚到，
  版本号仍然追加在末尾，由投影按采集时间归位。
- 可选 JSONL 持久化，每行一个事件信封，重启后重放恢复。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from src.contract import parse_dt, validate_event


class StoreError(Exception):
    """存储层拒绝写入。"""


class ConcurrencyError(StoreError):
    """聚合版本冲突（expected_version 与当前流尾不一致）。"""


class EventStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._streams: dict[str, list[dict]] = {}
        self._log: list[dict] = []
        self._event_ids: set[str] = set()
        self._lock = threading.RLock()
        self._path = Path(path) if path else None
        if self._path and self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._load(json.loads(line))

    # ── 写入 ──────────────────────────────────────────────────────────
    def append(self, event: dict, *, expected_version: int | None = None) -> dict:
        """校验并追加一个事件；成功返回写入的事件（补充 stored_at）。"""
        errors = validate_event(event)
        if errors:
            raise StoreError("；".join(errors))
        aggregate_id = event["aggregate_id"]
        with self._lock:
            if event["event_id"] in self._event_ids:
                raise StoreError(f"event_id 重复：{event['event_id']}")
            stream = self._streams.setdefault(aggregate_id, [])
            next_version = len(stream) + 1
            if event["version"] != next_version:
                raise ConcurrencyError(
                    f"聚合 {aggregate_id} 版本应为 {next_version}，收到 {event['version']}")
            if expected_version is not None and expected_version != len(stream):
                raise ConcurrencyError(
                    f"聚合 {aggregate_id} 期望基线版本 {expected_version}，实际 {len(stream)}")
            stored = dict(event)
            stored["stored_at"] = stored.get("stored_at") or _now_iso()
            stream.append(stored)
            self._log.append(stored)
            self._event_ids.add(stored["event_id"])
            if self._path:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(stored, ensure_ascii=False) + "\n")
            return stored

    def append_batch(self, events: list[dict]) -> list[dict]:
        """原子提交同一命令产生的多个事件：全部成功或全部不落库。"""
        if not events:
            return []
        for event in events:
            errors = validate_event(event)
            if errors:
                raise StoreError("；".join(errors))
        aggregate_id = events[0]["aggregate_id"]
        if any(e["aggregate_id"] != aggregate_id for e in events):
            # 不同聚合的事件通过同一把锁串行化，这里保持简单：仍允许，但不做跨流假设
            pass
        with self._lock:
            planned: dict[str, int] = {}
            for event in events:
                aid = event["aggregate_id"]
                base = len(self._streams.get(aid, [])) + planned.get(aid, 0)
                if event["event_id"] in self._event_ids:
                    raise StoreError(f"event_id 重复：{event['event_id']}")
                if event["version"] != base + 1:
                    raise ConcurrencyError(
                        f"聚合 {aid} 版本应为 {base + 1}，收到 {event['version']}")
                planned[aid] = planned.get(aid, 0) + 1
            stored_events: list[dict] = []
            for event in events:
                stored = dict(event)
                stored["stored_at"] = stored.get("stored_at") or _now_iso()
                self._streams.setdefault(event["aggregate_id"], []).append(stored)
                self._log.append(stored)
                self._event_ids.add(stored["event_id"])
                stored_events.append(stored)
            if self._path:
                with self._path.open("a", encoding="utf-8") as fh:
                    for stored in stored_events:
                        fh.write(json.dumps(stored, ensure_ascii=False) + "\n")
            return stored_events

    # ── 读取 ──────────────────────────────────────────────────────────
    def stream(self, aggregate_id: str) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._streams.get(aggregate_id, [])]

    def stream_version(self, aggregate_id: str) -> int:
        with self._lock:
            return len(self._streams.get(aggregate_id, []))

    def all_events(self) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._log]

    def exists(self, event_id: str) -> bool:
        return event_id in self._event_ids

    def _load(self, event: dict) -> None:
        # 重启重放走相同校验，拒绝任何被篡改的历史行
        errors = validate_event(event)
        if errors:
            raise StoreError(f"持久化事件 {event.get('event_id')} 校验失败：{'；'.join(errors)}")
        aggregate_id = event["aggregate_id"]
        if event["event_id"] in self._event_ids:
            raise StoreError(f"持久化 event_id 重复：{event['event_id']}")
        stream = self._streams.setdefault(aggregate_id, [])
        if event["version"] != len(stream) + 1:
            raise StoreError(f"持久化流 {aggregate_id} 版本不连续")
        self._streams[aggregate_id].append(event)
        self._log.append(event)
        self._event_ids.add(event["event_id"])


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def event_time(event: dict) -> float:
    """事件发生时间的可比较标量。"""
    return parse_dt(event["occurred_at"]).timestamp()
