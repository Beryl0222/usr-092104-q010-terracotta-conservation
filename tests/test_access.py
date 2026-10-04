import json
import unittest
from pathlib import Path

from src.access import AccessPolicy
from src.projection import Projection

ROOT = Path(__file__).parents[1]


def load_events():
    return json.loads((ROOT / "data" / "scenario.json").read_text(encoding="utf-8"))


class AccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.projection = Projection(load_events())

    def test_conservator_sees_everything(self) -> None:
        policy = AccessPolicy(self.projection, "conservator")
        self.assertEqual(len(policy.visible_objects()), 4)
        self.assertEqual(len(policy.view_events()), len(self.projection.events))

    def test_public_sees_only_published_normal_finds(self) -> None:
        policy = AccessPolicy(self.projection, "public")
        visible = {obj["object_id"] for obj in policy.visible_objects()}
        self.assertEqual(visible, {"obj-FIG01", "obj-FRA", "obj-FRB"})
        # 未发布发现 FR-C 不随普通接口泄露。
        self.assertIsNone(policy.view_object("obj-FRC"))

        events = policy.view_events()
        object_ids_touched = {e.get("payload", {}).get("object_id") for e in events}
        self.assertNotIn("obj-FRC", object_ids_touched)
        # 敏感坑位探方不出现在普通接口。
        unit_events = [e for e in events if e["event_type"] in ("UNIT_REGISTERED", "STRATUM_RECORDED")]
        self.assertTrue(all(e["aggregate_id"] != "unit-U001" for e in unit_events))
        # 涉及 FR-C 的错误拼合事件（暂拼/确认/撤销）一并过滤。
        types_and_relations = {(e["event_type"], e["aggregate_id"]) for e in events}
        self.assertNotIn(("JOIN_TENTATIVE", "rel-J1"), types_and_relations)
        self.assertNotIn(("JOIN_REVOKED", "rel-J1"), types_and_relations)
        # 公众视图不暴露运营字段。
        fra = policy.view_object("obj-FRA")
        self.assertNotIn("frozen", fra)
        self.assertNotIn("sensitivity", fra)

    def test_researcher_sees_unpublished_but_pit_location_masked(self) -> None:
        policy = AccessPolicy(self.projection, "researcher")
        self.assertIsNotNone(policy.view_object("obj-FRC"))
        # FR-C 来自敏感坑位，精确定位脱敏而非整条删除。
        frc = policy.view_object("obj-FRC")
        self.assertEqual(frc["unit_id"], "【授权后可见】")
        # 普通坑位对象不脱敏。
        fra = policy.view_object("obj-FRA")
        self.assertEqual(fra["unit_id"], "unit-U002")
        # 事件流中的敏感坑位层位同样脱敏。
        lifted_frc = next(
            e for e in policy.view_events()
            if e["event_type"] == "OBJECT_LIFTED" and e.get("aggregate_id") == "obj-FRC"
        )
        self.assertEqual(lifted_frc["payload"]["unit_id"], "【授权后可见】")

    def test_unknown_role_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AccessPolicy(self.projection, "director")


if __name__ == "__main__":
    unittest.main()
