import unittest

from src.contract import (
    ALERT_HUMIDITY, ALERT_INSTRUMENT, ALERT_PACKAGE, QUALITY_BACKFILL,
    QUALITY_OK, QUALITY_UNCALIBRATED,
)
from src.service import DomainError, HandoffService
from src.store import EventStore

OFFICER = {"id": "u-officer", "role": "duty_officer"}
CONS = {"id": "u-cons", "role": "conservator"}
REG = {"id": "u-reg", "role": "registrar"}


class ServiceFixture:
    def __init__(self) -> None:
        self.svc = HandoffService(EventStore())
        ev = self.svc.open_trench(trench_code="T1", sensitive=True,
                                  grid_coordinates={"x": 1, "y": 2}, actor=OFFICER)
        self.trench_id = ev["aggregate_id"]
        self.svc.record_stratum(trench_id=self.trench_id, stratum_id="st-1",
                                depth_top=0.4, depth_bottom=1.4,
                                period_label="秦", actor=OFFICER)
        self.svc.lift_object(object_id="fig", object_kind="figure",
                             trench_id=self.trench_id, stratum_id="st-1",
                             actor=OFFICER)

    def fragment(self, fragment_id: str = "frag-a", source: str = "fig") -> None:
        self.svc.detach_fragment(fragment_id=fragment_id, source_object_id=source,
                                 figure_id="fig", reason="测试拆分", actor=CONS)

    def instrument(self, valid_from="2026-09-01T00:00:00+08:00",
                   valid_to="2026-10-15T00:00:00+08:00") -> str:
        ev = self.svc.register_instrument(instrument_code="RH-1", metric="humidity",
                                          valid_from=valid_from, valid_to=valid_to,
                                          cert_id="cert-1", actor=OFFICER)
        return ev["aggregate_id"]


class RoleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServiceFixture()

    def test_public_cannot_open_trench(self) -> None:
        with self.assertRaises(DomainError):
            self.fx.svc.open_trench(trench_code="T2", sensitive=False,
                                    grid_coordinates={},
                                    actor={"id": "p", "role": "public"})

    def test_officer_cannot_propose_conservation_action(self) -> None:
        self.fx.fragment()
        with self.assertRaises(DomainError):
            self.fx.svc.propose_action(action_kind="cleaning",
                                       target_object_id="frag-a",
                                       actor=OFFICER, rationale="越权")

    def test_conservator_cannot_accession(self) -> None:
        self.fx.fragment()
        loc = self.fx.svc.register_storage_location(
            location_code="L1", area="库房", actor=REG)["aggregate_id"]
        with self.assertRaises(DomainError):
            self.fx.svc.accession(object_id="frag-a",
                                  storage_location_id=loc,
                                  accession_code="A1", actor=CONS)


class FragmentAndExaminationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServiceFixture()

    def test_early_examinations_survive_join_and_revoke(self) -> None:
        fx = self.fx
        fx.fragment("frag-a")
        fx.fragment("frag-b")
        fx.svc.record_examination(examination_id="ex-early", object_id="frag-a",
                                  kind="ultrasonic",
                                  examined_at="2026-10-01T08:00:00+08:00",
                                  data={"v": 1}, actor=CONS)
        rel = fx.svc.open_join(fragment_ids=["frag-a", "frag-b"],
                               figure_id="fig", actor=CONS)["aggregate_id"]
        fx.svc.confirm_join(relation_id=rel, actor=CONS)
        fx.svc.revoke_join(relation_id=rel, actor=CONS, reason="断面不符")
        exams = fx.svc.read.objects["frag-a"].get("examinations", [])
        self.assertEqual([e["examination_id"] for e in exams], ["ex-early"])
        # 原关系仍在，且被新关系标记撤销
        self.assertEqual(fx.svc.read.relations[rel]["status"], "revoked")
        revocations = [r for r in fx.svc.read.relations.values()
                       if r["relation_kind"] == "join_revoked"]
        self.assertEqual(len(revocations), 1)
        self.assertEqual(revocations[0]["original_relation_id"], rel)

    def test_cannot_join_twice_after_revoke_without_new_relation(self) -> None:
        fx = self.fx
        fx.fragment("frag-a")
        fx.fragment("frag-b")
        rel = fx.svc.open_join(fragment_ids=["frag-a", "frag-b"],
                               figure_id="fig", actor=CONS)["aggregate_id"]
        fx.svc.confirm_join(relation_id=rel, actor=CONS)
        fx.svc.revoke_join(relation_id=rel, actor=CONS, reason="错误拼合")
        with self.assertRaises(DomainError):
            fx.svc.revoke_join(relation_id=rel, actor=CONS, reason="重复撤销")

    def test_join_requires_members_of_same_figure(self) -> None:
        self.fx.fragment("frag-a")
        with self.assertRaises(DomainError):
            self.fx.svc.open_join(fragment_ids=["frag-a", "ghost"],
                                  figure_id="fig", actor=CONS)

    def test_candidate_lifecycle(self) -> None:
        fx = self.fx
        cand = fx.svc.propose_candidate(object_id="fig",
                                        proposed_form="铠甲武士俑", actor=CONS)
        cid = cand["aggregate_id"]
        with self.assertRaises(DomainError):
            fx.svc.decide_candidate(candidate_id=cid, approve=True, actor=OFFICER)
        fx.svc.decide_candidate(candidate_id=cid, approve=True, actor=CONS)
        self.assertEqual(fx.svc.read.candidates[cid]["status"], "confirmed")
        with self.assertRaises(DomainError):
            fx.svc.decide_candidate(candidate_id=cid, approve=False, actor=CONS)


class MonitoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServiceFixture()
        self.fx.fragment()
        self.inst_id = self.fx.instrument()
        self.fx.svc.set_monitoring_limits(object_id="frag-a",
                                          limits={"humidity": [40, 60]},
                                          actor=OFFICER)

    def _sample(self, value, collected_at, quality=QUALITY_OK, **kw):
        return self.fx.svc.record_sample(
            series_id="ser-1", object_id="frag-a", instrument_id=self.inst_id,
            metric="humidity", value=value, collected_at=collected_at,
            quality=quality, actor=OFFICER, **kw)

    def test_backfill_is_positioned_by_collection_time(self) -> None:
        self._sample(52.0, "2026-10-04T10:00:00+08:00")
        self._sample(48.0, "2026-10-04T08:00:00+08:00", quality=QUALITY_BACKFILL)
        rows = self.fx.svc.read.sorted_samples("ser-1")
        self.assertEqual([r["value"] for r in rows], [48.0, 52.0])
        # 存储顺序仍是追加顺序（补数事件版本号在末尾）
        stored = [e for e in self.fx.svc.store.all_events()
                  if e["event_type"] == "ENVIRONMENT_SAMPLED"]
        self.assertEqual([e["payload"]["value"] for e in stored], [52.0, 48.0])
        self.assertEqual(stored[1]["payload"]["quality"], QUALITY_BACKFILL)

    def test_humidity_breach_raises_alert_and_freezes_only_target(self) -> None:
        self.fx.fragment("frag-b")
        self._sample(80.0, "2026-10-04T10:00:00+08:00")
        alerts = [a for a in self.fx.svc.read.alerts.values()
                  if a["alert_kind"] == ALERT_HUMIDITY]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["affected_object_ids"], ["frag-a"])
        self.assertTrue(self.fx.svc.read.is_frozen("frag-a"))
        self.assertFalse(self.fx.svc.read.is_frozen("frag-b"))

    def test_alert_is_never_auto_action(self) -> None:
        self._sample(80.0, "2026-10-04T10:00:00+08:00")
        self.assertEqual(list(self.fx.svc.read.actions), [])

    def test_uncalibrated_measurement_is_downgraded_and_does_not_alert(self) -> None:
        events = self._sample(95.0, "2026-10-20T10:00:00+08:00",
                              quality=QUALITY_OK)
        sample = next(e for e in events if e["event_type"] == "ENVIRONMENT_SAMPLED")
        self.assertEqual(sample["payload"]["quality"], QUALITY_UNCALIBRATED)
        self.assertNotIn("calibration_cert_id", sample["payload"])
        self.assertFalse(any(a["alert_kind"] == ALERT_HUMIDITY
                             for a in self.fx.svc.read.alerts.values()))
        self.assertTrue(any(a["alert_kind"] == ALERT_INSTRUMENT
                            for a in self.fx.svc.read.alerts.values()))
        # 未校准数据不得冒充可靠测量：对象不被冻结（设备告警本身不冻结）
        self.assertFalse(self.fx.svc.read.is_frozen("frag-a"))

    def test_explicit_uncalibrated_stays_uncalibrated(self) -> None:
        events = self._sample(50.0, "2026-10-20T10:00:00+08:00",
                              quality=QUALITY_UNCALIBRATED)
        sample = next(e for e in events if e["event_type"] == "ENVIRONMENT_SAMPLED")
        self.assertEqual(sample["payload"]["quality"], QUALITY_UNCALIBRATED)

    def test_no_duplicate_open_alert(self) -> None:
        self._sample(80.0, "2026-10-04T10:00:00+08:00")
        self._sample(81.0, "2026-10-04T10:05:00+08:00")
        self.assertEqual(sum(1 for a in self.fx.svc.read.alerts.values()
                             if a["alert_kind"] == ALERT_HUMIDITY), 1)

    def test_resolve_alert_releases_its_hold(self) -> None:
        self._sample(80.0, "2026-10-04T10:00:00+08:00")
        alert_id = next(a["alert_id"] for a in self.fx.svc.read.alerts.values()
                        if a["alert_kind"] == ALERT_HUMIDITY)
        self.fx.svc.acknowledge_alert(alert_id=alert_id, actor=OFFICER)
        self.fx.svc.resolve_alert(alert_id=alert_id, actor=OFFICER,
                                  resolution="环境已调节")
        self.assertFalse(self.fx.svc.read.is_frozen("frag-a"))
        self.assertEqual(self.fx.svc.read.alerts[alert_id]["status"], "resolved")

    def test_interpolated_quality_does_not_drive_alert(self) -> None:
        self._sample(80.0, "2026-10-04T10:00:00+08:00", quality="interpolated")
        self.assertFalse(any(a["alert_kind"] == ALERT_HUMIDITY
                             for a in self.fx.svc.read.alerts.values()))


class FreezeAndHandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServiceFixture()
        self.fx.fragment("frag-a")
        self.fx.fragment("frag-b")
        self.inst_id = self.fx.instrument()
        self.fx.svc.set_monitoring_limits(object_id="frag-a",
                                          limits={"humidity": [40, 60]},
                                          actor=OFFICER)

    def _breach(self):
        self.fx.svc.record_sample(
            series_id="ser-1", object_id="frag-a", instrument_id=self.inst_id,
            metric="humidity", value=80.0,
            collected_at="2026-10-04T10:00:00+08:00",
            quality=QUALITY_OK, actor=OFFICER)

    def test_frozen_object_cannot_be_sealed_or_joined(self) -> None:
        self._breach()
        with self.assertRaises(DomainError):
            self.fx.svc.open_join(fragment_ids=["frag-a", "frag-b"],
                                  figure_id="fig", actor=CONS)
        pkg = self.fx.svc.prepare_package(
            package_code="P1", object_ids=["frag-a"],
            humidity_limits={"humidity": [40, 60]}, actor=OFFICER)["aggregate_id"]
        with self.assertRaises(DomainError):
            self.fx.svc.seal_package(package_id=pkg, actor=OFFICER)

    def test_handoff_requires_two_distinct_signers(self) -> None:
        ho = self.fx.svc.prepare_handoff(
            handoff_code="H1", from_party="field", to_party="lab",
            object_ids=["frag-a", "frag-b"], actor=OFFICER)["aggregate_id"]
        with self.assertRaises(DomainError):
            self.fx.svc.sign_handoff(
                handoff_id=ho, releaser={"id": "same"}, receiver={"id": "same"})

    def test_frozen_objects_stay_with_releaser_others_transfer(self) -> None:
        self._breach()
        ho = self.fx.svc.prepare_handoff(
            handoff_code="H1", from_party="field_excavation_team",
            to_party="conservation_lab",
            object_ids=["frag-a", "frag-b"], actor=OFFICER)["aggregate_id"]
        self.fx.svc.sign_handoff(handoff_id=ho,
                                 releaser={"id": "u-officer"},
                                 receiver={"id": "u-cons"})
        record = self.fx.svc.read.handoffs[ho]
        self.assertEqual(record["transferred_object_ids"], ["frag-b"])
        self.assertEqual(record["frozen_object_ids"], ["frag-a"])
        self.assertEqual(self.fx.svc.read.objects["frag-b"]["current_party"],
                         "conservation_lab")
        self.assertEqual(self.fx.svc.read.objects["frag-a"]["current_party"],
                         "field_excavation_team")

    def test_cannot_sign_same_handoff_twice(self) -> None:
        ho = self.fx.svc.prepare_handoff(
            handoff_code="H1", from_party="field", to_party="lab",
            object_ids=["frag-a"], actor=OFFICER)["aggregate_id"]
        self.fx.svc.sign_handoff(handoff_id=ho, releaser={"id": "r"},
                                 receiver={"id": "c"})
        with self.assertRaises(DomainError):
            self.fx.svc.sign_handoff(handoff_id=ho, releaser={"id": "r"},
                                     receiver={"id": "c"})


class PackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServiceFixture()
        self.fx.fragment("frag-a")
        self.fx.fragment("frag-b")
        self.pkg = self.fx.svc.prepare_package(
            package_code="P1", object_ids=["frag-a", "frag-b"],
            humidity_limits={"humidity": [40, 60]}, actor=OFFICER)["aggregate_id"]

    def test_leak_freezes_only_package_members(self) -> None:
        self.fx.svc.seal_package(package_id=self.pkg, actor=OFFICER)
        self.fx.svc.record_package_status(package_id=self.pkg,
                                          seal_integrity="leaking", actor=OFFICER)
        self.assertTrue(self.fx.svc.read.is_frozen("frag-a"))
        self.assertTrue(self.fx.svc.read.is_frozen("frag-b"))
        self.assertFalse(self.fx.svc.read.is_frozen("fig"))
        self.assertTrue(any(a["alert_kind"] == ALERT_PACKAGE
                            for a in self.fx.svc.read.alerts.values()))

    def test_ok_status_does_not_freeze(self) -> None:
        self.fx.svc.seal_package(package_id=self.pkg, actor=OFFICER)
        self.fx.svc.record_package_status(package_id=self.pkg,
                                          seal_integrity="ok", actor=OFFICER)
        self.assertFalse(self.fx.svc.read.is_frozen("frag-a"))


class ActionWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServiceFixture()
        self.fx.fragment()

    def test_action_lifecycle(self) -> None:
        act = self.fx.svc.propose_action(
            action_kind="reinforcement", target_object_id="frag-a",
            actor=CONS, rationale="胎体疏松")["aggregate_id"]
        with self.assertRaises(DomainError):
            self.fx.svc.complete_action(action_id=act, actor=CONS, result="x")
        self.fx.svc.authorize_action(action_id=act, actor=CONS)
        self.fx.svc.complete_action(action_id=act, actor=CONS, result="加固完成")
        self.assertEqual(self.fx.svc.read.actions[act]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
