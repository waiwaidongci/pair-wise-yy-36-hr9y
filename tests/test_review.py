import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.repo=Repository(str(Path(self.tmp.name)/"test.db")); self.service=Service(self.repo)
        self.item=self.service.create_item({"title":"review item","description":"review conclusion flow","severity":"exceedance","quantity":12,"threshold":6,"external_ref":"RV-1"},"creator","operator")
    def tearDown(self): self.repo.close(); self.tmp.cleanup()
    def _to_inspection(self):
        current=self.service.get_item(self.item["id"],"viewer")
        for target,role in (("assessing","compliance_officer"),("remediation","operator"),("inspection","compliance_officer")):
            current=self.service.transition(current["id"],target,current["version"],"reviewer",role)
        return current
    def test_fail_returns_to_remediation_with_follow_up(self):
        current=self._to_inspection()
        result=self.service.submit_review(current["id"],{"result":"fail","opinion":"现场仍超标，需继续整改","expected_version":current["version"]},"boss","director")
        self.assertEqual(result["status"],"remediation"); self.assertEqual(result["version"],current["version"]+1)
        self.assertEqual(result["review"]["result"],"fail"); self.assertEqual(result["review"]["opinion"],"现场仍超标，需继续整改"); self.assertEqual(result["review"]["item_version"],result["version"])
        follow=result["follow_up_record"]; self.assertEqual(follow["status"],"open"); self.assertIn("现场仍超标",follow["detail"])
        self.assertEqual(len(self.service.list_records(current["id"],"viewer")),1); self.assertEqual(self.repo.open_record_count(current["id"]),1)
        self.assertEqual(len(self.service.list_reviews(current["id"],"viewer")),1)
        again=self.service.transition(result["id"],"inspection",result["version"],"reviewer","compliance_officer")
        with self.assertRaises(ConflictError): self.service.submit_review(again["id"],{"result":"pass","opinion":"未整改完不能关闭","expected_version":again["version"]},"boss","director")
        self.assertTrue(self.repo.verify_audit_chain())
    def test_pass_closes_and_records_conclusion(self):
        current=self._to_inspection()
        result=self.service.submit_review(current["id"],{"result":"pass","opinion":"复查合格，同意关闭","expected_version":current["version"]},"boss","director")
        self.assertEqual(result["status"],"closed"); self.assertEqual(result["version"],current["version"]+1)
        self.assertEqual(result["review"]["item_version"],result["version"]); self.assertNotIn("follow_up_record",result)
        events=self.service.audit("viewer",current["id"]); self.assertEqual(events[-1]["action"],"review"); self.assertEqual(events[-1]["detail"]["opinion"],"复查合格，同意关闭")
        self.assertTrue(self.repo.verify_audit_chain())
    def test_pass_blocked_by_open_records_leaves_no_trace(self):
        current=self._to_inspection()
        self.service.add_record(current["id"],{"kind":"action","detail":"unfinished fix","status":"open","external_ref":"RV-OPEN"},"recorder","operator")
        with self.assertRaises(ConflictError): self.service.submit_review(current["id"],{"result":"pass","opinion":"试图直接关闭","expected_version":current["version"]},"boss","director")
        after=self.service.get_item(current["id"],"viewer")
        self.assertEqual(after["status"],"inspection"); self.assertEqual(after["version"],current["version"])
        self.assertEqual(self.service.list_reviews(current["id"],"viewer"),[]); self.assertEqual(len(self.service.list_records(current["id"],"viewer")),1)
    def test_stale_version_cannot_overwrite_saved_conclusion(self):
        current=self._to_inspection()
        self.service.submit_review(current["id"],{"result":"pass","opinion":"复查合格","expected_version":current["version"]},"boss","director")
        with self.assertRaises(ConflictError): self.service.submit_review(current["id"],{"result":"fail","opinion":"过期版本提交","expected_version":current["version"]},"other","director")
        reviews=self.service.list_reviews(current["id"],"viewer"); self.assertEqual(len(reviews),1); self.assertEqual(reviews[0]["result"],"pass")
        after=self.service.get_item(current["id"],"viewer"); self.assertEqual(after["status"],"closed"); self.assertEqual(after["version"],current["version"]+1)
    def test_role_state_and_payload_guards(self):
        current=self._to_inspection()
        with self.assertRaises(PermissionDenied): self.service.submit_review(current["id"],{"result":"pass","opinion":"越权","expected_version":current["version"]},"ops","operator")
        with self.assertRaises(ValidationError): self.service.submit_review(current["id"],{"result":"maybe","opinion":"结果非法","expected_version":current["version"]},"boss","director")
        with self.assertRaises(ValidationError): self.service.submit_review(current["id"],{"result":"pass","opinion":"  ","expected_version":current["version"]},"boss","director")
        with self.assertRaises(ValueError): self.service.submit_review(current["id"],{"result":"pass","opinion":"版本非法","expected_version":0},"boss","director")
        fresh=self.service.create_item({"title":"not inspected","description":"still reported","severity":"watch","quantity":1,"threshold":10,"external_ref":"RV-2"},"creator","operator")
        with self.assertRaises(ConflictError): self.service.submit_review(fresh["id"],{"result":"pass","opinion":"阶段不对","expected_version":fresh["version"]},"boss","director")
        self.assertEqual(self.service.get_item(current["id"],"viewer")["status"],"inspection")
if __name__=="__main__": unittest.main()
