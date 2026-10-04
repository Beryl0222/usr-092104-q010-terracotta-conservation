import json
import unittest
from pathlib import Path

from src.store import EventStore, ValidationError

ROOT = Path(__file__).parents[1]


def scenario_store() -> EventStore:
    events = json.loads((ROOT / "data" / "scenario.json").read_text(encoding="utf-8"))
    store = EventStore()
    store.append_many(events)
    return store


def envelope(**overrides) -> dict:
    record = {
        "event_id": "e-new",
        "event_type": "OBJECT_FROZEN",
        "aggregate_type": "excavated_object",
        "aggregate_id": "obj-FRA",
        "occurred_at": "2026-09-20T20:00:00+08:00",
        "version": 99,
        "summary": "测试事件",
        "payload": {"object_id": "obj-FRA", "reason": "x"},
    }
    record.update(overrides)
    return record


class StoreRulesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = scenario_store()

    def test_versions_strictly_increment_per_aggregate(self) -> None:
        bad = envelope(event_id="e-bad", aggregate_id="obj-FRA", version=99,
                       occurred_at="2026-09-20T20:00:00+08:00")
        with self.assertRaises(ValidationError) as ctx:
            self.store.append(bad)
        self.assertTrue(any("版本冲突" in e for e in ctx.exception.errors))

    def test_duplicate_event_id_rejected(self) -> None:
        duplicate = dict(envelope(event_id="evt-070"))
        with self.assertRaises(ValidationError):
            self.store.append(duplicate)

    def test_backfill_must_carry_backfilled_quality(self) -> None:
        from src.validator import validate_domain_event
        bad = {
            "event_id": "e-bf", "event_type": "MEASUREMENT_BACKFILLED",
            "aggregate_type": "environment_series", "aggregate_id": "env-X",
            "occurred_at": "2026-09-20T15:00:00+08:00", "version": 1, "summary": "x",
            "payload": {"object_id": "obj-FRA", "sensor_id": "sensor-S2",
                        "metric": "humidity", "value": 80.0,
                        "measured_at": "2026-09-20T14:00:00+08:00", "quality": "reliable"},
        }
        errors = validate_domain_event(bad)
        self.assertTrue(any("backfilled" in e for e in errors))

    def test_backfill_enters_by_measured_time_not_ingest_time(self) -> None:
        from src.projection import Projection
        projection = Projection(self.store.events)
        rows = projection.measurements["obj-FRA"]
        measured = [datetime_of(m["at"]) for m in rows]
        self.assertEqual(measured, sorted(measured))
        backfill = next(m for m in rows if m["backfilled"])
        # 08:30 采集的补数排在 09:00 之前，尽管 11:00 才入库。
        self.assertLess(datetime_of(backfill["at"]), datetime_of("2026-09-20T09:00:00+08:00"))
        self.assertEqual(backfill["quality"], "backfilled")

    def test_expired_calibration_cannot_claim_reliable(self) -> None:
        bad = envelope(
            event_id="e-exp", event_type="ENVIRONMENT_SAMPLED",
            aggregate_type="environment_series", aggregate_id="env-FRA",
            version=5, occurred_at="2026-09-20T13:00:00+08:00",
            payload={"object_id": "obj-FRA", "sensor_id": "sensor-S1",
                     "metric": "humidity", "value": 90.0,
                     "measured_at": "2026-09-20T12:40:00+08:00", "quality": "reliable"},
        )
        with self.assertRaises(ValidationError) as ctx:
            self.store.append(bad)
        self.assertTrue(any("校准" in e for e in ctx.exception.errors))

    def test_uncalibrated_sensor_rejected(self) -> None:
        bad = envelope(
            event_id="e-unc", event_type="ENVIRONMENT_SAMPLED",
            aggregate_type="environment_series", aggregate_id="env-9",
            version=1,
            payload={"object_id": "obj-FRA", "sensor_id": "sensor-GHOST",
                     "metric": "humidity", "value": 90.0,
                     "measured_at": "2026-09-20T13:00:00+08:00", "quality": "suspect"},
        )
        with self.assertRaises(ValidationError) as ctx:
            self.store.append(bad)
        self.assertTrue(any("从未校准" in e for e in ctx.exception.errors))

    def test_alert_cannot_auto_authorize_cleaning(self) -> None:
        from src.validator import validate_domain_event
        bad = envelope(
            event_id="e-auto", event_type="ACTION_AUTHORIZED",
            aggregate_type="conservation_action", aggregate_id="act-auto",
            version=1,
            payload={"object_id": "obj-FRB", "action_type": "cleaning",
                     "authorized_by": "ALERT-SYSTEM", "human_confirmed": False},
        )
        errors = validate_domain_event(bad)
        self.assertTrue(any("人工确认" in e for e in errors))

    def test_frozen_object_blocks_handoff_and_action_only_for_that_object(self) -> None:
        # obj-FRA 在 13:00-13:35 之间处于冻结状态；回放重放该时点的尝试。
        store = EventStore()
        events = json.loads((ROOT / "data" / "scenario.json").read_text(encoding="utf-8"))
        upto = [e for e in events if e["occurred_at"] <= "2026-09-20T13:00:00+08:00"]
        store.append_many(upto)
        self.assertTrue(store.is_frozen("obj-FRA"))
        self.assertFalse(store.is_frozen("obj-FRB"))

        action = envelope(
            event_id="e-act-frozen", event_type="ACTION_AUTHORIZED",
            aggregate_type="conservation_action", aggregate_id="act-x",
            version=1, occurred_at="2026-09-20T13:02:00+08:00",
            payload={"object_id": "obj-FRA", "action_type": "reinforcement",
                     "authorized_by": "赵修复", "human_confirmed": True},
        )
        with self.assertRaises(ValidationError) as ctx:
            store.append(action)
        self.assertTrue(any("冻结" in e for e in ctx.exception.errors))

        handoff = envelope(
            event_id="e-ho-frozen", event_type="HANDOFF_SIGNED",
            aggregate_type="custody_handoff", aggregate_id="handoff-X",
            version=1, occurred_at="2026-09-20T13:03:00+08:00",
            payload={"handoff_id": "handoff-X", "object_ids": ["obj-FRA"],
                     "signer_role": "relinquishing", "signer": "钱值班"},
        )
        with self.assertRaises(ValidationError) as ctx:
            store.append(handoff)
        self.assertTrue(any("冻结" in e for e in ctx.exception.errors))

    def test_revoking_join_creates_new_event_never_deletes_history(self) -> None:
        relation_events = self.store.for_aggregate("rel-J1")
        self.assertEqual(
            [e["event_type"] for e in relation_events],
            ["JOIN_TENTATIVE", "JOIN_CONFIRMED", "JOIN_REVOKED"],
        )

    def test_cannot_revoke_twice_or_confirm_without_tentative(self) -> None:
        duplicate = envelope(
            event_id="e-rev2", event_type="JOIN_REVOKED",
            aggregate_type="fragment_relation", aggregate_id="rel-J1",
            version=4, occurred_at="2026-09-20T20:00:00+08:00",
            payload={"relation_id": "rel-J1", "reason": "再撤一次", "revoked_by": "李修复"},
        )
        with self.assertRaises(ValidationError) as ctx:
            self.store.append(duplicate)
        self.assertTrue(any("撤销" in e for e in ctx.exception.errors))

    def test_handoff_requires_both_signatures_to_transfer(self) -> None:
        partial = {
            "event_id": "e-h2", "event_type": "HANDOFF_SIGNED",
            "aggregate_type": "custody_handoff", "aggregate_id": "handoff-H2",
            "occurred_at": "2026-09-20T15:00:00+08:00", "version": 1, "summary": "x",
            "payload": {"handoff_id": "handoff-H2", "object_ids": ["obj-FRC"],
                        "signer_role": "relinquishing", "signer": "甲"},
        }
        self.store.append(partial)
        status = self.store.handoff_status("handoff-H2")
        self.assertFalse(status["custody_transferred"])
        # 接收方必须在交出方之后副署。
        wrong_order = dict(partial, event_id="e-h3", aggregate_id="handoff-H3",
                           version=1,
                           payload={**partial["payload"], "handoff_id": "handoff-H3",
                                    "signer_role": "receiving", "signer": "乙"})
        with self.assertRaises(ValidationError) as ctx:
            self.store.append(wrong_order)
        self.assertTrue(any("交出方" in e for e in ctx.exception.errors))


def datetime_of(value: str):
    from datetime import datetime
    return datetime.fromisoformat(value)


if __name__ == "__main__":
    unittest.main()
