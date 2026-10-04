import json
import unittest
from pathlib import Path

from src.projection import Projection

ROOT = Path(__file__).parents[1]


def load_events():
    return json.loads((ROOT / "data" / "scenario.json").read_text(encoding="utf-8"))


class ProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.projection = Projection(load_events())

    def test_duty_board_points_to_fragment(self) -> None:
        # 取告警刚提出、尚未签收的时点（截至 10:27）。
        events = [e for e in load_events() if e["occurred_at"] <= "2026-09-20T10:27:00+08:00"]
        board = Projection(events).duty_board()
        by_object = {row["object_id"]: row for row in board}
        self.assertIn("obj-FRA", by_object)
        self.assertIn("obj-FRB", by_object)
        categories = {issue["category"] for issue in by_object["obj-FRA"]["issues"]}
        self.assertEqual(categories, {"humidity_threshold", "packaging_anomaly"})
        # 湿度越界行带上最近一次湿度值，值班人员先看到的是“哪块残片”。
        humidity = next(i for i in by_object["obj-FRA"]["issues"] if i["category"] == "humidity_threshold")
        self.assertEqual(humidity["latest_humidity"]["value"], 95.0)

    def test_closed_alerts_leaves_board(self) -> None:
        self.assertEqual(self.projection.duty_board(), [])

    def test_figure_lineage_covers_all_fragments_including_confirmed_identity(self) -> None:
        lineage = self.projection.trace_lineage("obj-FIG01")
        self.assertEqual(set(lineage["fragments"]), {"obj-FRA", "obj-FRB", "obj-FRC"})
        relation_ids = {r["relation_id"] for r in lineage["relations"]}
        # 错误拼合 rel-J1（已撤销）与正确拼合 rel-J2 都留在谱系中。
        self.assertEqual(relation_ids, {"rel-J1", "rel-J2"})
        revoked = next(r for r in lineage["relations"] if r["relation_id"] == "rel-J1")
        self.assertEqual(revoked["status"], "revoked")

    def test_early_detections_survive_split_and_revoked_join(self) -> None:
        history = self.projection.fragment_history("obj-FRA")
        types = [h["event_type"] for h in history]
        # 拆分前/暂拼前的超声与早期湿度检测在后续确认、撤销之后仍可追溯。
        self.assertIn("ENVIRONMENT_SAMPLED", types)
        self.assertIn("ACQUISITION_RECORDED", types)
        self.assertEqual(types.count("ENVIRONMENT_SAMPLED"), 3)
        # 撤销错误拼合本身也是残片经历的一部分。
        self.assertIn("JOIN_REVOKED", types)
        self.assertIn("JOIN_TENTATIVE", types)
        # 全部经历按时间有序。
        times = [h["at"] for h in history]
        self.assertEqual(times, sorted(times))

    def test_backfill_quality_visible_in_history(self) -> None:
        rows = self.projection.measurements["obj-FRA"]
        backfill = next(r for r in rows if r["backfilled"])
        self.assertEqual(backfill["quality"], "backfilled")
        expired = next(r for r in rows if r["quality"] == "calibration_expired")
        self.assertEqual(expired["value"], 99.0)


if __name__ == "__main__":
    unittest.main()
