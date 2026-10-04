import unittest

from src.auth import AccessDenied, context
from src.service import HandoffService
from src.store import EventStore
from src.views import Queries

OFFICER = {"id": "u-officer", "role": "duty_officer"}
CONS = {"id": "u-cons", "role": "conservator"}


def build_world() -> tuple[HandoffService, Queries, str]:
    svc = HandoffService(EventStore())
    trench = svc.open_trench(trench_code="T9", sensitive=True,
                             grid_coordinates={"n": 34.38, "e": 109.27},
                             actor=OFFICER)["aggregate_id"]
    svc.record_stratum(trench_id=trench, stratum_id="st", depth_top=0.4,
                       depth_bottom=1.0, period_label="秦", actor=OFFICER)
    svc.lift_object(object_id="fig", object_kind="figure", trench_id=trench,
                    stratum_id="st", publication_state="unpublished",
                    actor=OFFICER)
    svc.detach_fragment(fragment_id="frag-a", source_object_id="fig",
                        figure_id="fig", reason="起翘", actor=CONS)
    svc.record_examination(examination_id="ex-1", object_id="frag-a",
                           kind="pigment",
                           examined_at="2026-10-01T08:00:00+08:00",
                           data={"color": "中国紫"}, actor=CONS)
    return svc, Queries(svc.read), trench


class LineageTest(unittest.TestCase):
    def test_lineage_walks_origin_relations_exams(self) -> None:
        _, q, _ = build_world()
        lineage = q.fragment_lineage("frag-a", context("duty_officer"))
        self.assertEqual([o["object_id"] for o in lineage["origin_chain"]],
                         ["frag-a", "fig"])
        self.assertEqual([e["examination_id"] for e in lineage["examinations"]],
                         ["ex-1"])
        self.assertTrue(any(r["kind"] == "detached" for r in lineage["relations"]))

    def test_figure_overview_lists_fragments(self) -> None:
        _, q, _ = build_world()
        overview = q.figure_overview("fig", context("conservator"))
        self.assertEqual([f["object_id"] for f in overview["fragments"]],
                         ["frag-a"])


class AuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc, self.q, self.trench = build_world()

    def test_public_cannot_see_unpublished(self) -> None:
        with self.assertRaises(AccessDenied):
            self.q.fragment_lineage("frag-a", context("public"))
        self.assertEqual(self.q.list_objects(context("public")), [])

    def test_researcher_needs_scope_for_unpublished(self) -> None:
        self.assertEqual(self.q.list_objects(context("researcher")), [])
        rows = self.q.list_objects(
            context("researcher", {"unpublished_finds"}))
        self.assertEqual({o["object_id"] for o in rows}, {"fig", "frag-a"})

    def test_sensitive_coordinates_gated_independently(self) -> None:
        researcher = context("researcher", {"unpublished_finds"})
        view = self.q.trench(self.trench, researcher)
        self.assertIsNone(view["grid_coordinates"])
        self.assertTrue(view["location_restricted"])

        allowed = context("researcher",
                          {"unpublished_finds", "sensitive_locations"})
        view2 = self.q.trench(self.trench, allowed)
        self.assertEqual(view2["grid_coordinates"],
                         {"n": 34.38, "e": 109.27})

    def test_staff_sees_coordinates(self) -> None:
        view = self.q.trench(self.trench, context("duty_officer"))
        self.assertIsNotNone(view["grid_coordinates"])

    def test_alert_dashboard_staff_only(self) -> None:
        with self.assertRaises(AccessDenied):
            self.q.alert_dashboard(context("researcher", {"unpublished_finds"}))
        dash = self.q.alert_dashboard(context("duty_officer"))
        self.assertEqual(dash["open_alert_count"], 0)

    def test_missing_object_is_key_error_not_leaked(self) -> None:
        with self.assertRaises(KeyError):
            self.q.fragment_lineage("ghost", context("public"))


if __name__ == "__main__":
    unittest.main()
