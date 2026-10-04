import json
import unittest
from pathlib import Path

from src.contract import (
    AGGREGATE_BY_EVENT,
    EVENT_TYPES,
    PAYLOAD_REQUIRED_BY_EVENT,
    SCHEMA,
)
from src.store import EventStore
from src.validator import validate_domain_event, validate_event

ROOT = Path(__file__).parents[1]


def _load(name: str):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        self.assertEqual(validate_event(_load("sample.json")), [])

    def test_every_event_has_aggregate_and_clause(self) -> None:
        clause_events = {
            clause["if"]["properties"]["event_type"]["const"]
            for clause in SCHEMA["allOf"]
        }
        self.assertEqual(set(EVENT_TYPES), clause_events)
        for event_type in EVENT_TYPES:
            self.assertIn(event_type, AGGREGATE_BY_EVENT)

    def test_required_payload_fields_parse_from_schema(self) -> None:
        self.assertIn("object_id", PAYLOAD_REQUIRED_BY_EVENT["ALERT_RAISED"])
        self.assertIn("human_confirmed", PAYLOAD_REQUIRED_BY_EVENT["ACTION_AUTHORIZED"])
        self.assertIn("handoff_id", PAYLOAD_REQUIRED_BY_EVENT["HANDOFF_SIGNED"])

    def test_scenario_events_all_valid_and_replayable(self) -> None:
        events = _load("scenario.json")
        store = EventStore()
        for event in events:
            self.assertEqual(validate_domain_event(event), [], event["event_id"])
            store.append(event)
        self.assertEqual(len(store.events), len(events))


if __name__ == "__main__":
    unittest.main()
