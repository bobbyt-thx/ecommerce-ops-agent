"""Development regressions, independent raw-table checks and mock protocol.

These tests are NOT a new paid/held-out model evaluation.
"""
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from ops_agent.agent import run_offline, run_llm
from ops_agent.analysis import seller_diagnosis, compact_tool_result
from ops_agent.contracts import validate_answer, ANSWER_SCHEMA
from ops_agent.prepare import ROOT, DB
from ops_agent.request_policy import plan_request, tool_request_error
from ops_agent.views import agent_view, seller_view, settings_view


class TaskBindingTests(unittest.TestCase):
    def test_seller_first_and_business_first_same_scopes(self):
        questions = [
            "先检查2018-05至2018-07商家，门槛30单，再报告2018-08总体商品金额。",
            "先报告2018-08总体商品金额，再检查2018-05至2018-07商家，门槛30单。",
            "先检查2018年5月至2018年7月商家，门槛30单；再报告2018年8月总体商品金额。",
        ]
        expected = {"diagnose_business": {"month": "2018-08"}, "diagnose_sellers": {"start_month": "2018-05", "end_month": "2018-07", "min_orders": 30}}
        for question in questions:
            with self.subTest(question=question):
                plan = plan_request(question)
                self.assertEqual(plan["clarification"], [])
                self.assertEqual(plan["explicit_arguments"], expected)
                self.assertIsNone(tool_request_error(plan, "diagnose_business", {"month": "2018-08"}))

    def test_free_demo_runs_both_tasks_in_user_order(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            result = run_offline("分析2018-07经营变化，再检查需要优先核查的商家。")
        self.assertEqual([r["scenario"] for r in result["reports"]], ["business", "seller"])
        self.assertEqual(result["reports"][1]["start_month"], "2018-07")
        self.assertTrue(result["request_contract"]["assumptions"])
        self.assertFalse(result["model_called"])

    def test_explicit_threshold_is_not_ignored(self):
        result = run_offline("2017-10到2017-12商家履约最低50单")
        self.assertEqual(result["reports"][0]["min_orders"], 50)

    def test_overall_review_routes_to_business(self):
        result = run_offline("2018-07总体评价覆盖率是多少？")
        self.assertEqual(result["reports"][0]["scenario"], "business")

    def test_ambiguous_windows_fail_before_model_or_sql(self):
        questions = ["检查2018-05至2018-07商家，再报告总体商品金额", "2018-05经营和2018-08经营", "检查2018-05至2018-07商家，门槛30单，最低50单"]
        for question in questions:
            with self.subTest(question=question):
                with patch("ops_agent.agent.execute_tool", side_effect=AssertionError("SQL forbidden")):
                    result = run_offline(question)
                self.assertEqual(result["decision"], "needs_clarification")
                with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
                    result = run_llm(question, transport=lambda _: self.fail("model forbidden"), model="mock", answer_schema=ANSWER_SCHEMA)
                self.assertFalse(result["model_called"])

    def test_same_tool_two_separate_windows_clarifies(self):
        plan = plan_request("2018-05总体商品金额，再报告2018-08总体商品金额")
        self.assertTrue(plan["clarification"])

    def test_wrong_rank_cannot_override_user_target(self):
        plan = plan_request("2018-05至2018-07商家低评分率最高，至少30单")
        self.assertEqual(plan["explicit_arguments"]["diagnose_sellers"]["ranking"], "low_review_rate")
        args = {"start_month": "2018-05", "end_month": "2018-07", "min_orders": 30, "ranking": "late_burden"}
        self.assertTrue(tool_request_error(plan, "diagnose_sellers", args))


class RankingAndDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = {"tool": "T2", **seller_diagnosis(ranking="low_review_rate")}

    def test_low_review_winner_matches_raw_sql_before_truncation(self):
        with sqlite3.connect(DB.as_uri()+"?mode=ro", uri=True) as con:
            winner = con.execute("""WITH single_seller AS (
                SELECT order_id, MIN(seller_id) seller_id FROM raw_items GROUP BY order_id HAVING COUNT(DISTINCT seller_id)=1
              ), selected AS (
                SELECT order_id, review_score, ROW_NUMBER() OVER(PARTITION BY order_id ORDER BY review_answer_timestamp DESC, review_id DESC, review_score DESC) pos
                FROM raw_reviews
              ) SELECT s.seller_id, COUNT(r.review_score) n, SUM(r.review_score<=2) low
                FROM single_seller s JOIN raw_orders o USING(order_id)
                LEFT JOIN selected r ON r.order_id=o.order_id AND r.pos=1
                WHERE o.order_status='delivered' AND o.order_purchase_timestamp>='2018-05-01' AND o.order_purchase_timestamp<'2018-08-01'
                GROUP BY s.seller_id HAVING COUNT(*)>=30 AND n>=30
                ORDER BY CAST(low AS REAL)/n DESC, s.seller_id LIMIT 1""").fetchone()
        self.assertEqual(winner[0], self.report["sellers"][0]["seller_id"])
        self.assertEqual(winner[1:], (146, 66))
        self.assertEqual(compact_tool_result(self.report)["sellers"][0]["seller_id"], winner[0])

    def test_other_targets_sorted_after_denominator_filter(self):
        fields = {"late_burden": ("above_baseline_late_orders", "delivery_n"), "late_rate": ("late_rate", "delivery_n"), "low_review_burden": ("above_baseline_low_review_orders", "reviewed_n")}
        for ranking, (key, denominator) in fields.items():
            report = seller_diagnosis(ranking=ranking)
            self.assertTrue(all(row[denominator]>=30 for row in report["sellers"]))
            values = [r[key] for r in report["sellers"]]
            self.assertEqual(values, sorted(values, reverse=True))

    def test_verified_answer_keeps_entity_and_exact_source_row(self):
        raw = json.dumps({"decision": "supported", "metrics": [{"key": "eligible_sellers", "value": self.report["eligible_sellers"], "evidence": "T2/Q01"}], "findings": ["虚构商家FAKE，收入999999999"], "limitations": []})
        final, audit = validate_answer(raw, [self.report])
        self.assertIn(self.report["sellers"][0]["seller_id"], str(final))
        self.assertNotIn("FAKE", str(final))
        self.assertNotIn("999999999", str(final))
        entity = audit["verified_entities"][0]
        row = self.report["evidence"][0]["rows"][entity["source_row"]-1]
        self.assertEqual(entity["seller_id"], row["seller_id"])
        self.assertEqual(entity["evidence"], "T2/Q01")

    def test_invalid_rank_rejected(self):
        with self.assertRaises(ValueError):
            seller_diagnosis(ranking="invented_score")

    def test_entity_only_request_does_not_need_unasked_scalar_metric(self):
        calls = 0
        def fake(payload):
            nonlocal calls
            calls += 1
            if calls <= 2:
                name, args = ("metric_dictionary", {}) if calls == 1 else ("diagnose_sellers", {"start_month": "2018-05", "end_month": "2018-07", "min_orders": 30, "ranking": "low_review_rate"})
                return {"output": [{"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": str(calls)}]}
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({"decision": "supported", "metrics": [], "findings": [], "limitations": []})}]}]}
        result = run_llm("2018-05至2018-07商家低评分率最高，至少30单", transport=fake, model="mock", answer_schema=ANSWER_SCHEMA)
        answer = json.loads(result["answer"])
        self.assertEqual(answer["decision"], "supported")
        self.assertEqual(answer["metrics"], [])
        self.assertIn("2eb70248d66e0e3ef83659f71b244378", str(answer))
        self.assertEqual(len(result["validation"]["verified_entities"]), 3)

    def test_presentation_keeps_scope_and_hides_debug(self):
        result = run_offline("2018-05至2018-07商家低评分率最高，至少30单")
        html = agent_view(False, result)
        self.assertIn("start=2018-05&amp;end=2018-07", html)
        self.assertIn("ranking=low_review_rate", html)
        self.assertIn("结论与待核查对象", html)
        self.assertIn("<details>", html)
        self.assertNotIn('<option value="llm">', html)
        self.assertIn('name="ranking"', seller_view(self.report, True))

    def test_all_historical_artifacts_and_snapshot_preserved(self):
        target = ROOT/"reports/baselines/v4"
        if not (target/"manifest.json").exists():
            self.skipTest("Public package omits local historical traces; this is not a model score.")
        manifest = json.loads((target/"manifest.json").read_text())
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        for name, sha in manifest["preserved_results"].items():
            self.assertEqual(digest(ROOT/name), sha, name)
        for name, sha in manifest["snapshot_hashes"].items():
            self.assertEqual(digest(target/name), sha, name)
        self.assertEqual(digest(DB), manifest["database_sha256"])

    def test_key_rotation_keeps_budget_authorization(self):
        import tempfile
        from pathlib import Path
        from ops_agent import model_config as config
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "LOCAL", Path(tmp)), patch.object(config, "_session", {}):
            config.configure("sk-test-not-real-123456789012345", limit=2, allow_paid=True)
            config.reserve_request({"model": "gpt-5-mini"})
            before = config.public_status()
            config.configure("sk-test-not-real-987654321012345", limit=None)
            after = config.public_status()
            self.assertEqual(before, after)
            self.assertIn('value="keep"', settings_view(after))
