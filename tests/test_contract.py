import json
import unittest
from pathlib import Path

from src.contract import validate_event
from src.validator import validate_event as validate_event_legacy


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        path = Path(__file__).parents[1] / "data" / "sample.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(validate_event(record), [])

    def test_legacy_validator_entry_still_works(self) -> None:
        path = Path(__file__).parents[1] / "data" / "sample.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(validate_event_legacy(record), [])

    def test_envelope_required_fields(self) -> None:
        errors = validate_event({})
        self.assertTrue(any("event_id" in e for e in errors))
        self.assertEqual(len(errors), 7)

    def test_version_must_be_positive_int(self) -> None:
        base = self._valid_envelope()
        for bad in (0, -1, 1.0, True, "1"):
            record = dict(base, version=bad)
            self.assertTrue(
                any("version" in e for e in validate_event(record)),
                f"非法 version 未被拒绝：{bad!r}")

    def test_occurred_at_requires_timezone(self) -> None:
        base = self._valid_envelope()
        base["occurred_at"] = "2026-10-04T10:00:00"
        self.assertTrue(any("时区" in e for e in validate_event(base)))

    def test_event_type_must_match_aggregate(self) -> None:
        base = self._valid_envelope()
        base["event_type"] = "ENVIRONMENT_SAMPLED"
        base["aggregate_type"] = "excavated_object"
        errors = validate_event(base)
        self.assertTrue(any("aggregate_type" in e for e in errors))

    def test_payload_required_and_enums(self) -> None:
        base = self._valid_envelope()
        base["event_type"] = "ENVIRONMENT_SAMPLED"
        base["aggregate_type"] = "environment_series"
        base["payload"] = {}
        errors = validate_event(base)
        # 7 个必填载荷字段
        self.assertEqual(sum("载荷缺少字段" in e for e in errors), 7)

        base["payload"] = {
            "series_id": "s1", "object_id": "o1", "instrument_id": "i1",
            "metric": "humidity", "value": 55,
            "collected_at": "2026-10-04T10:00:00+08:00", "quality": "bogus",
        }
        errors = validate_event(base)
        self.assertTrue(any("quality" in e for e in errors))

    def test_actor_role_enum(self) -> None:
        base = self._valid_envelope()
        base["actor"] = {"id": "u1", "role": "ghost"}
        self.assertTrue(any("角色" in e for e in validate_event(base)))

    @staticmethod
    def _valid_envelope() -> dict:
        return {
            "event_id": "e1",
            "event_type": "OBJECT_LIFTED",
            "aggregate_type": "excavated_object",
            "aggregate_id": "o1",
            "occurred_at": "2026-10-04T10:00:00+08:00",
            "version": 1,
            "summary": "样例",
        }


if __name__ == "__main__":
    unittest.main()
