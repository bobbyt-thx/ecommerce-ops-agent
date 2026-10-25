"""Independent numerical regressions and protocol tests, not an LLM benchmark."""
import copy
import json
from pathlib import Path
import sqlite3
import unittest

from ops_agent.analysis import (DB, EvidenceDB, month_bounds, previous_month, safe_rate,
    business_diagnosis, seller_diagnosis, METRICS)
from ops_agent.agent import execute_tool, run_offline, run_llm
from ops_agent.views import business_view, seller_view, esc


class DateAndInputTests(unittest.TestCase):
    def test_leap_month_bounds(self):
        self.assertEqual(month_bounds("2018-02"), ("2018-02-01", "2018-03-01"))

    def test_year_boundary(self):
        self.assertEqual(previous_month("2018-01"), "2017-12")

    def test_bad_dates(self):
        for bad in ["2026-10", "2018-13", "2018-9", "DROP TABLE", None]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                month_bounds(bad)

    def test_zero_denominator(self):
        self.assertIsNone(safe_rate(0, 0))

    def test_negative_range(self):
        with self.assertRaises(ValueError):
            seller_diagnosis("2018-07", "2018-05")

    def test_invalid_sample(self):
        for n in [0, 9, 501, 30.5, True]:
            with self.subTest(n=n), self.assertRaises(ValueError):
                seller_diagnosis(min_orders=n)

    def test_missing_comparison_month(self):
        with self.assertRaises(ValueError):
            business_diagnosis("2017-01")

    def test_bad_dimension(self):
        with self.assertRaises(ValueError):
            business_diagnosis(dimensions=["DROP TABLE"])


class RealDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
        cls.business = business_diagnosis()
        cls.seller = seller_diagnosis()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_raw_counts(self):
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM raw_orders").fetchone()[0], 99441)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0], 112650)

    def test_raw_independent_amount(self):
        # No prepared fact tables; independently aggregate the raw order-item join.
        amount = self.db.execute("""SELECT SUM(CAST(ROUND(i.price*100) AS INTEGER))
          FROM raw_items i JOIN raw_orders o USING(order_id)
          WHERE o.order_status='delivered' AND o.order_purchase_timestamp>='2018-07-01'
            AND o.order_purchase_timestamp<'2018-08-01'""").fetchone()[0]
        self.assertAlmostEqual(amount / 100, self.business["current"]["item_amount_brl"], places=2)

    def test_raw_independent_order_count(self):
        count = self.db.execute("""SELECT COUNT(DISTINCT o.order_id) FROM raw_orders o
          JOIN raw_items i USING(order_id) WHERE o.order_status='delivered'
          AND o.order_purchase_timestamp>='2018-07-01' AND o.order_purchase_timestamp<'2018-08-01'""").fetchone()[0]
        self.assertEqual(count, self.business["current"]["orders"])

    def test_all_dimension_reconciliations(self):
        self.assertTrue(all(self.business["checks"].values()))
        delta_cents = round(self.business["delta_brl"] * 100)
        for rows in self.business["contributions"].values():
            self.assertEqual(sum(row["delta_cents"] for row in rows), delta_cents)

    def test_factor_decomposition(self):
        self.assertAlmostEqual(sum(self.business["decomposition"].values()), self.business["delta_brl"], places=6)

    def test_all_monthly_raw_reconciliation(self):
        # 19 independently checked month pairs; this does NOT measure model accuracy.
        for month in [f"2017-{m:02}" for m in range(2, 13)] + [f"2018-{m:02}" for m in range(1, 9)]:
            with self.subTest(month=month):
                result = business_diagnosis(month, ["category", "state"])
                self.assertTrue(all(result["checks"].values()))
                amount = self.db.execute("""SELECT SUM(CAST(ROUND(i.price*100) AS INTEGER))
                  FROM raw_items i JOIN raw_orders o USING(order_id)
                  WHERE o.order_status='delivered' AND substr(o.order_purchase_timestamp,1,7)=?""", (month,)).fetchone()[0]
                self.assertAlmostEqual(amount / 100, result["current"]["item_amount_brl"], places=2)

    def test_customer_identity(self):
        actual = self.db.execute("""SELECT COUNT(DISTINCT c.customer_unique_id) FROM raw_orders o
          JOIN raw_customers c USING(customer_id) WHERE o.order_status='delivered'
          AND substr(o.order_purchase_timestamp,1,7)='2018-07'""").fetchone()[0]
        self.assertEqual(actual, self.business["current"]["customers"])

    def test_late_calendar_day_denominator(self):
        n, late = self.db.execute("""SELECT COUNT(*),SUM(date(order_delivered_customer_date)>date(order_estimated_delivery_date))
          FROM raw_orders WHERE order_status='delivered'
          AND order_purchase_timestamp>='2018-07-01' AND order_purchase_timestamp<'2018-08-01'
          AND order_delivered_customer_date IS NOT NULL AND order_estimated_delivery_date IS NOT NULL
          AND date(order_delivered_customer_date)>=date(order_purchase_timestamp)""").fetchone()
        self.assertEqual((n, late), (self.business["current"]["delivery_n"], self.business["current"]["late_n"]))

    def test_review_policy_independently(self):
        # Window function independently implements the documented selected-review policy.
        actual = self.db.execute("""WITH ranked AS (
          SELECT order_id,review_score, ROW_NUMBER() OVER (PARTITION BY order_id
            ORDER BY review_answer_timestamp DESC,review_id DESC,review_score DESC) pos FROM raw_reviews)
          SELECT COUNT(r.review_score),SUM(r.review_score<=2) FROM raw_orders o
          LEFT JOIN ranked r ON o.order_id=r.order_id AND r.pos=1
          WHERE o.order_status='delivered' AND substr(o.order_purchase_timestamp,1,7)='2018-07'""").fetchone()
        self.assertEqual(actual, (self.business["current"]["reviewed_n"], self.business["current"]["low_n"]))

    def test_seller_coverage_independent(self):
        count = self.db.execute("""WITH eligible AS (SELECT order_id FROM raw_items GROUP BY order_id HAVING COUNT(DISTINCT seller_id)=1)
          SELECT COUNT(*) FROM raw_orders o JOIN eligible e USING(order_id)
          WHERE o.order_status='delivered' AND o.order_purchase_timestamp>='2018-05-01'
          AND o.order_purchase_timestamp<'2018-08-01'""").fetchone()[0]
        self.assertEqual(count, self.seller["coverage"]["attributable_orders"])

    def test_seller_independent_deduplication(self):
        seller = self.seller["sellers"][0]
        actual = self.db.execute("""WITH s AS (SELECT order_id,MIN(seller_id) seller_id FROM raw_items GROUP BY order_id
            HAVING COUNT(DISTINCT seller_id)=1)
          SELECT COUNT(*),SUM(CASE WHEN date(o.order_delivered_customer_date)>date(o.order_estimated_delivery_date)
            THEN 1 ELSE 0 END) FROM s JOIN raw_orders o USING(order_id)
          WHERE s.seller_id=? AND o.order_status='delivered' AND o.order_purchase_timestamp>='2018-05-01'
            AND o.order_purchase_timestamp<'2018-08-01'""", (seller["seller_id"],)).fetchone()
        self.assertEqual(actual, (seller["orders"], seller["late_n"]))

    def test_seller_checks(self):
        self.assertTrue(all(self.seller["checks"].values()))

    def test_higher_threshold_fewer_sellers(self):
        self.assertLessEqual(seller_diagnosis(min_orders=100)["eligible_sellers"], self.seller["eligible_sellers"])

    def test_tail_warning(self):
        r = business_diagnosis("2018-08", ["state"])
        self.assertTrue(any("成熟度" in s for s in r["warnings"]))

    def test_json_strict_serialization(self):
        for report in [self.business, self.seller]:
            json.dumps(report, allow_nan=False)

    def test_sqlite_readonly(self):
        db = EvidenceDB()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db.con.execute("DELETE FROM fact_orders WHERE 1=0")
        finally:
            db.close()

    def test_html_reports_escaped(self):
        altered = copy.deepcopy(self.business)
        altered["warnings"].append('<script>alert("bad")</script>')
        html = business_view(altered)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn("查询证据", html)
        self.assertIn("评价覆盖", seller_view(self.seller))


class ToolProtocolTests(unittest.TestCase):
    def test_unknown_tool_rejected(self):
        with self.assertRaises(ValueError):
            execute_tool("run_shell", {"code": "anything"})

    def test_dictionary(self):
        result, report = execute_tool("metric_dictionary", {})
        self.assertEqual(len(result["metrics"]), len(METRICS))
        self.assertIsNone(report)

    def test_no_fake_offline_roi(self):
        result = run_offline("帮我算广告ROI和利润")
        self.assertIn("不支持", result["answer"])
        self.assertEqual(result["reports"], [])

    def test_offline_mode_label(self):
        result = run_offline("2018-07经营变化")
        self.assertEqual(result["mode"], "offline")
        self.assertIn("不是大模型", result["answer"])

    def test_unrecognized_question(self):
        with self.assertRaises(ValueError):
            run_offline("今天天气怎么样")

    def test_mock_function_call_loop(self):
        # Protocol mock only; it does not establish real model reasoning quality.
        payloads = []
        def fake(payload):
            payloads.append(copy.deepcopy(payload))
            if len(payloads) == 1:
                return {"output": [{"type": "function_call", "name": "diagnose_business", "call_id": "mock-1", "arguments": '{"month":"2018-07"}'}]}
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "协议测试完成，依据T1/Q01。"}]}]}
        result = run_llm("分析经营", transport=fake, model="protocol-mock")
        self.assertEqual(result["mode"], "protocol_test")
        self.assertEqual(len(result["reports"]), 1)
        self.assertTrue(any(x.get("type") == "function_call_output" for x in payloads[1]["input"]))

    def test_mock_error_recovery(self):
        count = 0
        def fake(payload):
            nonlocal count
            count += 1
            if count <= 2:
                month = "2026-10" if count == 1 else "2018-07"
                return {"output": [{"type": "function_call", "name": "diagnose_business", "call_id": str(count), "arguments": json.dumps({"month": month})}]}
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "修正参数完成。"}]}]}
        result = run_llm("分析经营", transport=fake, model="protocol-mock")
        self.assertFalse(result["trace"][0]["outcome"]["ok"])
        self.assertTrue(result["trace"][1]["outcome"]["ok"])

    def test_bad_json_tool_call(self):
        count = 0
        def fake(payload):
            nonlocal count
            count += 1
            if count == 1:
                return {"output": [{"type": "function_call", "name": "diagnose_business", "call_id": "x", "arguments": "{"}]}
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "参数不合法。"}]}]}
        r = run_llm("测试", transport=fake, model="protocol-mock")
        self.assertFalse(r["trace"][0]["outcome"]["ok"])

    def test_step_budget(self):
        def fake(payload):
            return {"output": [{"type": "function_call", "name": "metric_dictionary", "call_id": "x", "arguments": "{}"}]}
        with self.assertRaises(RuntimeError):
            run_llm("反复调用", transport=fake, model="protocol-mock", max_steps=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
