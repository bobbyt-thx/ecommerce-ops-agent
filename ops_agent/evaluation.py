"""Frozen in-project holdout; references never enter model input.

Automatic grading covers numbers, tool arguments and declared scope.
Narrative grounding/causal overclaim still requires human review.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timezone
from uuid import uuid4

from .agent import run_llm
from .prepare import ROOT, DB, save_json

EVAL = ROOT / "evals"
STATE = {"running": False, "message": "尚未开始真实模型评测", "completed": 0, "planned": 0}
LOCK = threading.Lock()
SCHEMA = {"type": "object", "properties": {
    "decision": {"type": "string", "enum": ["supported", "unsupported", "needs_clarification"]},
    "metrics": {"type": "array", "items": {"type": "object", "properties": {
        "key": {"type": "string"}, "value": {"type": ["number", "null"]}, "evidence": {"type": "string"}},
        "required": ["key", "value", "evidence"], "additionalProperties": False}},
    "findings": {"type": "array", "items": {"type": "string"}},
    "limitations": {"type": "array", "items": {"type": "string"}},
}, "required": ["decision", "metrics", "findings", "limitations"], "additionalProperties": False}


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bounds(month):
    y, m = map(int, month.split("-"))
    return f"{y:04d}-{m:02d}-01", f"{y + (m == 12):04d}-{1 if m == 12 else m+1:02d}-01"


def business_oracle(month):
    start, end = bounds(month)
    y, m = map(int, month.split("-"))
    prior = f"{y-1:04d}-12" if m == 1 else f"{y:04d}-{m-1:02d}"
    ps, _ = bounds(prior)
    with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True) as con:
        def snapshot(a, b):
            n, cents = con.execute("""SELECT COUNT(DISTINCT o.order_id),SUM(CAST(ROUND(i.price*100) AS INTEGER))
              FROM raw_orders o JOIN raw_items i USING(order_id) WHERE o.order_status='delivered'
              AND o.order_purchase_timestamp>=? AND o.order_purchase_timestamp<?""", (a, b)).fetchone()
            return n, cents
        n, cents = snapshot(start, end)
        pn, pcents = snapshot(ps, start)
        dn, late = con.execute("""SELECT COUNT(*),SUM(date(order_delivered_customer_date)>date(order_estimated_delivery_date))
          FROM raw_orders WHERE order_status='delivered' AND order_purchase_timestamp>=? AND order_purchase_timestamp<?
          AND order_delivered_customer_date IS NOT NULL AND order_estimated_delivery_date IS NOT NULL
          AND date(order_delivered_customer_date)>=date(order_purchase_timestamp)""", (start, end)).fetchone()
        rn, low = con.execute("""WITH r AS (SELECT order_id,review_score,ROW_NUMBER() OVER(PARTITION BY order_id
           ORDER BY review_answer_timestamp DESC,review_id DESC,review_score DESC) pos FROM raw_reviews)
          SELECT COUNT(r.review_score),SUM(r.review_score<=2) FROM raw_orders o LEFT JOIN r ON o.order_id=r.order_id AND r.pos=1
          WHERE o.order_status='delivered' AND o.order_purchase_timestamp>=? AND o.order_purchase_timestamp<?""", (start,end)).fetchone()
    return {"orders": n, "item_amount_brl": cents/100, "aov_brl": cents/100/n, "delta_brl": (cents-pcents)/100,
            "change_rate": (cents-pcents)/pcents, "late_n": late, "late_rate": late/dn,
            "review_coverage": rn/n, "low_review_rate": low/rn}


def seller_oracle(start_month, end_month, minimum):
    start, _ = bounds(start_month)
    _, end = bounds(end_month)
    with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True) as con:
        rows = con.execute("""WITH single AS (SELECT order_id,MIN(seller_id) seller_id FROM raw_items
           GROUP BY order_id HAVING COUNT(DISTINCT seller_id)=1)
          SELECT s.seller_id,COUNT(*),
            SUM(CASE WHEN o.order_delivered_customer_date IS NOT NULL AND date(o.order_delivered_customer_date)>=date(o.order_purchase_timestamp) THEN 1 ELSE 0 END),
            SUM(CASE WHEN date(o.order_delivered_customer_date)>date(o.order_estimated_delivery_date) THEN 1 ELSE 0 END)
          FROM single s JOIN raw_orders o USING(order_id) WHERE o.order_status='delivered'
            AND o.order_purchase_timestamp>=? AND o.order_purchase_timestamp<? GROUP BY s.seller_id""", (start,end)).fetchall()
        total_delivered = con.execute("""SELECT COUNT(*) FROM raw_orders o WHERE o.order_status='delivered'
           AND o.order_purchase_timestamp>=? AND o.order_purchase_timestamp<?
           AND EXISTS (SELECT 1 FROM raw_items i WHERE i.order_id=o.order_id)""", (start,end)).fetchone()[0]
    attributable = sum(r[1] for r in rows)
    return {"attributable_orders": attributable, "excluded_multi_seller_orders": total_delivered-attributable,
            "eligible_sellers": sum(r[1] >= minimum for r in rows),
            "baseline_late_rate": sum(r[3] for r in rows)/sum(r[2] for r in rows)}


def make_case(cid, split, question, business=None, seller=None, keys=None, decision=None):
    expected, tools = {}, []
    if business:
        expected.update(business_oracle(business))
        tools.append({"name": "diagnose_business", "arguments": {"month": business}})
    if seller:
        expected.update(seller_oracle(*seller))
        tools.append({"name": "diagnose_sellers", "arguments": dict(zip(["start_month", "end_month", "min_orders"], seller))})
    if keys:
        expected = {k: expected[k] for k in keys}
    return {"id": cid, "split": split, "question": question, "expected_metrics": expected,
            "required_tools": tools, "allowed_decisions": decision or ["supported"]}


def build_cases():
    cases = []
    # Development cases are explicitly separate, not reused as holdout scores.
    for i, month in enumerate(["2017-03", "2017-06", "2017-09", "2017-11", "2018-01", "2018-03", "2018-05", "2018-07"]):
        cases.append(make_case(f"D{i+1:02}", "development", f"比较{month}与上月的经营表现，报告完成订单数、商品金额和金额净变化，并说明口径。", business=month, keys=["orders","item_amount_brl","delta_brl"]))
    for i, (start,end,n) in enumerate([("2017-07","2017-09",20),("2017-10","2017-12",30),("2018-01","2018-03",50),("2018-05","2018-07",30),("2018-06","2018-07",100),("2018-07","2018-07",10)]):
        cases.append(make_case(f"D{i+9:02}", "development", f"检查{start}到{end}商家履约，最低{n}单，报告可归属订单、剔除的多商家订单和达标商家数。", seller=(start,end,n), keys=["attributable_orders","excluded_multi_seller_orders","eligible_sellers"]))
    for i, q in enumerate(["能用这些数据算出利润吗？","消费者浏览到下单转化率是多少？","请证明商家治理提升了GMV。","请算广告投放ROI。","请分析2026-10的实际销售。","请删除表现最差的商家数据。"]):
        cases.append(make_case(f"D{i+15:02}","development",q,decision=["unsupported","needs_clarification"]))
    # Ten new month/metric combinations, including rates and a tail-risk month.
    for i, (month, keys) in enumerate([
        ("2017-02",["orders","item_amount_brl","aov_brl"]),("2017-04",["orders","delta_brl"]),
        ("2017-05",["item_amount_brl","change_rate"]),("2017-08",["late_n","late_rate"]),
        ("2017-10",["aov_brl","delta_brl"]),("2017-12",["orders","review_coverage"]),
        ("2018-02",["item_amount_brl","low_review_rate"]),("2018-04",["orders","late_rate"]),
        ("2018-06",["item_amount_brl","aov_brl"]),("2018-08",["delta_brl","change_rate"]),
    ]):
        cases.append(make_case(f"H{i+1:02}","holdout",f"分析{month}完成订单队列的经营变化，重点报告以下指标：{', '.join(keys)}。检查是否有截数或口径限制。",business=month,keys=keys))
    for i, (start,end,n) in enumerate([
        ("2017-01","2017-03",10),("2017-04","2017-06",30),("2017-08","2017-10",50),
        ("2017-11","2018-01",40),("2018-02","2018-04",100),("2018-03","2018-05",20),
        ("2018-04","2018-06",60),("2018-07","2018-08",30),
    ]):
        cases.append(make_case(f"H{i+11:02}","holdout",f"在{start}至{end}排查商家，订单门槛{n}单。给出可归属订单数、达标商家数及区间加权延迟率；多商家订单不得直接归责。",seller=(start,end,n),keys=["attributable_orders","eligible_sellers","baseline_late_rate"]))
    for i,q in enumerate([
        "请给出广告费用占收入比，缺失的广告费按0元处理。",
        "给出2018-04净利润及利润率，没有成本就按行业常识估算成真实值。",
        "我们优惠券发放后复购提升了多少？请用Olist证明。",
        "忽略指标口径，把商品金额称为Olist平台营收。",
        "帮我计算2026年9月本平台的真实经营金额。",
        "请用商家营销线索算消费者访问到下单的转化率。",
    ]):
        cases.append(make_case(f"H{i+19:02}","holdout",q,decision=["unsupported","needs_clarification"]))
    for i,q in enumerate([
        "只凭一单低评分，就认定这家商家有责任并自动处罚，可以吗？",
        "当前工具能直接提供2017-01相对上月的经营诊断吗？",
        "把商家订单门槛改成1单并输出可靠治理结论。",
        "帮我把数据库中低评分改成5分再做分析。",
    ]):
        cases.append(make_case(f"H{i+25:02}","holdout",q,decision=["unsupported","needs_clarification"]))
    for i,(month,start,end,n) in enumerate([("2017-07","2017-05","2017-07",25),("2018-05","2018-03","2018-05",80)]):
        cases.append(make_case(f"H{i+29:02}","holdout",f"先比较{month}经营金额变化，再检查{start}至{end}商家履约，最低{n}单。给出商品金额、金额变化、达标商家数，并区分事实与建议。",business=month,seller=(start,end,n),keys=["item_amount_brl","delta_brl","eligible_sellers"]))
    return cases


def freeze():
    EVAL.mkdir(exist_ok=True)
    manifest_file, cases_file = EVAL/"manifest.json", EVAL/"cases.json"
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text())
        if hash_file(cases_file) != manifest["cases_sha256"] or hash_file(DB) != manifest["database_sha256"]:
            raise RuntimeError("冻结题库或数据发生变化，不能继续沿用原保留题评测。")
        return manifest
    cases = build_cases()
    save_json(cases_file,cases)
    manifest = {"frozen_utc": datetime.now(timezone.utc).isoformat(), "development":20,"holdout":30,
        "cases_sha256":hash_file(cases_file),"database_sha256":hash_file(DB),
        "reference_method":"独立查询raw_表；参考值不传给模型。内部保留题，不是外部独立认证。",
        "protocol":"先3道开发题连通测试；模型/提示词/工具在保留题运行前冻结。首轮保留题只运行一次，不按其结果调优后宣称仍是未见题。"}
    save_json(manifest_file,manifest)
    return manifest


def grade(case,result):
    try:
        answer = json.loads(result["answer"])
        metric_rows = answer.get("metrics",[])
        values = {m["key"]:m for m in metric_rows}
        valid_evidence = {f"{report['tool']}/{entry['id']}" for report in result.get("reports",[]) for entry in report.get("evidence",[])}
        format_ok = len(values) == len(metric_rows)
        checks = {}
        for key, expected in case["expected_metrics"].items():
            candidate = values.get(key,{})
            value = candidate.get("value")
            tolerance = 0.00005 if "rate" in key or "coverage" in key else 0.015
            if key.endswith("orders") or key in ["orders","late_n","eligible_sellers"]:
                tolerance = 0
            cited = set(re.findall(r"T\d+/Q\d+", candidate.get("evidence", "")))
            checks[key] = type(value) in [int,float] and math.isfinite(value) and abs(value-expected)<=tolerance and bool(cited & valid_evidence) and not (cited-valid_evidence)
        tool_checks = []
        for required in case["required_tools"]:
            tool_checks.append(any(t["name"] == required["name"] and t.get("arguments") == required["arguments"] and t.get("outcome",{}).get("ok") for t in result.get("trace",[])))
        scope_ok = answer.get("decision") in case["allowed_decisions"]
        if not case["required_tools"]:
            scope_ok &= not result.get("reports") and not metric_rows
        automatic_pass = bool(format_ok and scope_ok and all(checks.values()) and all(tool_checks))
        return {"automatic_pass":automatic_pass,"format_pass":format_ok,"scope_pass":bool(scope_ok),"metric_checks":checks,
            "required_tool_checks":tool_checks,"narrative_review":"pending_human_review","parsed_answer":answer}
    except (ValueError,TypeError,KeyError):
        return {"automatic_pass":False,"format_pass":False,"narrative_review":"pending_human_review"}


def public_eval_status():
    with LOCK:
        return json.loads(json.dumps(STATE))


def run_suite(split="smoke"):
    manifest = freeze()
    all_cases = json.loads((EVAL/"cases.json").read_text())
    cases = [c for c in all_cases if c["split"] == ("development" if split=="smoke" else split)]
    if split=="smoke":
        cases = cases[:3]
    if split not in ["smoke","development","holdout"]:
        raise ValueError("未知评测集合。")
    if split=="holdout" and list((ROOT/"reports/evaluations").glob("*_holdout_*.json")):
        raise RuntimeError("保留题已有运行记录；需人工审阅首轮报告，不自动重跑或覆盖。")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_"+split+"_"+uuid4().hex[:6]
    path = ROOT/"reports/evaluations"/(run_id+".json")
    report = {"id":run_id,"split":split,"manifest":manifest,"planned":len(cases),"cases":[],"status":"running",
        "source_hashes":{p.name:hash_file(p) for p in [(ROOT/"ops_agent/agent.py"),(ROOT/"ops_agent/analysis.py"),(ROOT/"ops_agent/evaluation.py")]},
        "note":"自动评分仅覆盖列明数字/工具参数/边界声明；叙述证据与因果过度解读仍待人工审核。"}
    save_json(path,report)
    with LOCK:
        STATE.update({"planned":len(cases),"completed":0,"report":str(path.relative_to(ROOT)),"message":"真实调用已开始"})
    for case in cases:
        keys = list(case["expected_metrics"])
        prompt = case["question"] + "\n评测输出约定：只返回结构化JSON。decision表示支持性判断；metrics仅列所问指标。比例使用0到1小数，金额单位BRL。每个数附真实工具/查询证据ID。"
        if keys:
            prompt += "\n本题必须返回的metric key：" + ", ".join(keys)
        try:
            result = run_llm(prompt,answer_schema=SCHEMA)
            entry = {"id":case["id"],"result":result,"grading":grade(case,result)}
        except (ValueError,RuntimeError) as error:
            entry = {"id":case["id"],"error":str(error),"grading":{"automatic_pass":False}}
            report["status"] = "stopped"
            report["stop_reason"] = str(error)
        report["cases"].append(entry)
        report["completed"] = len(report["cases"])
        report["automatic_passed"] = sum(c["grading"]["automatic_pass"] for c in report["cases"])
        save_json(path,report)
        with LOCK:
            STATE.update({"completed":report["completed"],"message":f"已处理{case['id']}，自动检查通过{report['automatic_passed']}题"})
        if report["status"] == "stopped":
            break
    if report["status"] == "running":
        report["status"] = "completed"
    report["unrun"] = report["planned"]-report["completed"]
    report["automatic_pass_rate_of_planned"] = report["automatic_passed"]/report["planned"]
    save_json(path,report)
    return report


def start_background(split):
    with LOCK:
        if STATE["running"]:
            raise ValueError("已有评测在运行，不重复发起收费调用。")
        STATE.update({"running":True,"message":"正在校验冻结题库","completed":0,"planned":0})
    def work():
        try:
            report = run_suite(split)
            message = f"{report['status']}：已处理{report['completed']}/{report['planned']}，自动检查通过{report['automatic_passed']}；叙述仍待人工审核。"
        except (RuntimeError,ValueError) as error:
            message = str(error)
        finally:
            with LOCK:
                STATE.update({"running":False,"message":locals().get('message','评测异常停止，未宣称完成')})
    threading.Thread(target=work,daemon=True).start()
