import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)

    def tearDown(self):
        self.repo.close(); self.tmp.cleanup()

    def _item(self, ref):
        return self.service.create_item(
            {"title": "review item", "description": "review flow",
             "severity": "exceedance", "quantity": 12, "threshold": 6,
             "external_ref": ref}, "creator", "operator")

    def _to_inspection(self, item):
        current = item
        for target, role in (("assessing", "compliance_officer"),
                             ("remediation", "operator"),
                             ("inspection", "compliance_officer")):
            current = self.service.transition(
                current["id"], target, current["version"], "reviewer", role)
        return current

    def test_review_pass_closes_item(self):
        item = self._to_inspection(self._item("RV-PASS"))
        outcome = self.service.submit_review(
            item["id"], {"result": "pass", "opinion": "复检达标，同意关闭",
                         "expected_version": item["version"]}, "leader", "director")
        closed = outcome["item"]
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["version"], item["version"] + 1)
        self.assertEqual(closed["review_result"], "pass")
        self.assertEqual(closed["review_opinion"], "复检达标，同意关闭")
        self.assertEqual(closed["review_by"], "leader")
        self.assertIsNotNone(closed["review_at"])
        self.assertIsNone(outcome["record"])
        events = self.service.audit("viewer", item["id"])
        self.assertEqual(events[-1]["action"], "review")
        self.assertEqual(events[-1]["detail"]["to"], "closed")
        self.assertTrue(self.repo.verify_audit_chain())

    def test_review_fail_returns_to_remediation_with_record(self):
        item = self._to_inspection(self._item("RV-FAIL"))
        outcome = self.service.submit_review(
            item["id"], {"result": "fail", "opinion": "现场复检仍超标",
                         "expected_version": item["version"]}, "leader", "director")
        reopened = outcome["item"]
        record = outcome["record"]
        self.assertEqual(reopened["status"], "remediation")
        self.assertEqual(reopened["version"], item["version"] + 1)
        self.assertEqual(reopened["review_result"], "fail")
        self.assertEqual(reopened["review_opinion"], "现场复检仍超标")
        self.assertEqual(record["status"], "open")
        self.assertEqual(record["kind"], "remediation")
        self.assertIn("现场复检仍超标", record["detail"])
        self.assertEqual(self.repo.open_record_count(item["id"]), 1)
        events = self.service.audit("viewer", item["id"])
        self.assertEqual(events[-1]["action"], "review")
        self.assertEqual(events[-1]["detail"]["record_id"], record["id"])
        self.assertTrue(self.repo.verify_audit_chain())

    def test_review_pass_blocked_by_open_records(self):
        item = self._to_inspection(self._item("RV-BLOCK"))
        self.service.add_record(item["id"], {"kind": "action",
                                             "detail": "unfinished fix",
                                             "status": "open"}, "recorder", "operator")
        with self.assertRaises(ConflictError):
            self.service.submit_review(
                item["id"], {"result": "pass", "opinion": "试图关闭",
                             "expected_version": item["version"]}, "leader", "director")
        current = self.service.get_item(item["id"], "viewer")
        self.assertEqual(current["status"], "inspection")
        self.assertIsNone(current["review_result"])

    def test_stale_version_cannot_override_saved_review(self):
        item = self._to_inspection(self._item("RV-STALE"))
        with self.assertRaises(ConflictError):
            self.service.submit_review(
                item["id"], {"result": "fail", "opinion": "过期版本",
                             "expected_version": item["version"] + 9}, "leader", "director")
        saved = self.service.submit_review(
            item["id"], {"result": "fail", "opinion": "先保存的结论",
                         "expected_version": item["version"]}, "leader", "director")
        with self.assertRaises(ConflictError):
            self.service.submit_review(
                item["id"], {"result": "pass", "opinion": "覆盖他人结论",
                             "expected_version": item["version"]}, "other", "director")
        current = self.service.get_item(item["id"], "viewer")
        self.assertEqual(current["status"], "remediation")
        self.assertEqual(current["review_opinion"], "先保存的结论")
        self.assertEqual(current["version"], saved["item"]["version"])

    def test_review_role_state_and_payload_guards(self):
        item = self._to_inspection(self._item("RV-GUARD"))
        with self.assertRaises(PermissionDenied):
            self.service.submit_review(
                item["id"], {"result": "pass", "opinion": "x",
                             "expected_version": item["version"]}, "attacker", "operator")
        with self.assertRaises(ValueError):
            self.service.submit_review(
                item["id"], {"result": "maybe", "opinion": "x",
                             "expected_version": item["version"]}, "leader", "director")
        with self.assertRaises(ValidationError):
            self.service.submit_review(
                item["id"], {"result": "pass", "opinion": "  ",
                             "expected_version": item["version"]}, "leader", "director")
        with self.assertRaises(ValueError):
            self.service.submit_review(
                item["id"], {"result": "pass", "opinion": "x",
                             "expected_version": 0}, "leader", "director")
        fresh = self._item("RV-GUARD-2")
        with self.assertRaises(ConflictError):
            self.service.submit_review(
                fresh["id"], {"result": "pass", "opinion": "x",
                              "expected_version": fresh["version"]}, "leader", "director")


if __name__ == "__main__":
    unittest.main()
