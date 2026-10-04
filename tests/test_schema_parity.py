"""防止 contracts/domain.schema.json 的枚举与 Python 领域契约漂移。"""
import json
import unittest
from pathlib import Path

from src.contract import AGGREGATE_TYPES, EVENT_TYPES

ROOT = Path(__file__).parents[1]


class SchemaParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(
            (ROOT / "contracts" / "domain.schema.json").read_text(encoding="utf-8"))

    def test_event_types_match(self) -> None:
        enum = set(self.schema["properties"]["event_type"]["enum"])
        self.assertEqual(enum, set(EVENT_TYPES))

    def test_aggregate_types_match(self) -> None:
        enum = set(self.schema["properties"]["aggregate_type"]["enum"])
        self.assertEqual(enum, set(AGGREGATE_TYPES))

    def test_sample_json_valid_against_enums(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        self.assertIn(sample["event_type"], EVENT_TYPES)
        self.assertIn(sample["aggregate_type"], AGGREGATE_TYPES)


if __name__ == "__main__":
    unittest.main()
