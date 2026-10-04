import tempfile
import unittest
from pathlib import Path

from src.contract import OBJECT_LIFTED
from src.store import ConcurrencyError, EventStore, StoreError


def lifted(event_id: str, aggregate_id: str, version: int) -> dict:
    return {
        "event_id": event_id,
        "event_type": OBJECT_LIFTED,
        "aggregate_type": "excavated_object",
        "aggregate_id": aggregate_id,
        "occurred_at": "2026-10-04T10:00:00+08:00",
        "version": version,
        "summary": "起取",
        "payload": {"object_kind": "figure", "trench_id": "t1",
                    "stratum_id": "s1", "publication_state": "unpublished"},
    }


class StoreTest(unittest.TestCase):
    def test_stream_versions_are_gapless(self) -> None:
        store = EventStore()
        store.append(lifted("e1", "o1", 1))
        store.append(lifted("e2", "o1", 2))
        with self.assertRaises(ConcurrencyError):
            store.append(lifted("e3", "o1", 5))

    def test_different_streams_have_independent_versions(self) -> None:
        store = EventStore()
        store.append(lifted("e1", "o1", 1))
        store.append(lifted("e2", "o2", 1))
        self.assertEqual(store.stream_version("o1"), 1)
        self.assertEqual(store.stream_version("o2"), 1)

    def test_event_id_dedup(self) -> None:
        store = EventStore()
        store.append(lifted("dup", "o1", 1))
        with self.assertRaises(StoreError):
            store.append(lifted("dup", "o2", 1))

    def test_expected_version_optimistic_lock(self) -> None:
        store = EventStore()
        store.append(lifted("e1", "o1", 1))
        store.append(lifted("e2", "o1", 2), expected_version=1)
        with self.assertRaises(ConcurrencyError):
            store.append(lifted("e3", "o1", 3), expected_version=1)

    def test_invalid_event_rejected(self) -> None:
        store = EventStore()
        with self.assertRaises(StoreError):
            store.append({"event_id": "bad"})

    def test_append_batch_is_atomic(self) -> None:
        store = EventStore()
        store.append(lifted("e1", "o1", 1))
        good = lifted("e2", "o1", 2)
        bad = lifted("e3", "o1", 9)  # 版本断裂
        with self.assertRaises(StoreError):
            store.append_batch([good, bad])
        self.assertEqual(store.stream_version("o1"), 1)
        self.assertFalse(store.exists("e2"))

    def test_jsonl_replay_restores_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            store = EventStore(path)
            store.append(lifted("e1", "o1", 1))
            store.append(lifted("e2", "o1", 2))
            store.append(lifted("e3", "o2", 1))
            reloaded = EventStore(path)
            self.assertEqual(reloaded.stream_version("o1"), 2)
            self.assertEqual(reloaded.stream_version("o2"), 1)
            self.assertEqual(len(reloaded.all_events()), 3)


if __name__ == "__main__":
    unittest.main()
