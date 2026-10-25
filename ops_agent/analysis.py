"""Bounded, read-only business tools with query evidence and reconciliations."""
from __future__ import annotations

import calendar
import json
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

from .prepare import DB, ROOT
from .contracts import metric_catalog

SUPPORTED_START = "2017-01"
SUPPORTED_END = "2018-08"
DEFAULT_MONTH = "2018-07"
COMMON_WARNINGS = [
    "历史公开数据；金额为巴西雷亚尔（BRL），不是平台营收或利润。",
    "完成订单商品金额按下单月份统计，最终状态为delivered；有状态筛选与截数偏差，不代表当时已知的实时经营数据。",
    "诊断为描述性拆解和相关性线索，不证明原因或干预收益。",
]
METRICS = [
    {"name": "完成订单商品金额", "definition": "delivered且有明细的订单，逐商品price转分求和；不含运费，按购买日归期。", "grain": "订单/商品", "unit": "BRL"},
    {"name": "完成订单数", "definition": "与金额同一人群的去重order_id数。", "grain": "订单", "unit": "单"},
    {"name": "客单价", "definition": "完成订单商品金额/完成订单数。", "grain": "订单", "unit": "BRL/单"},
    {"name": "付费去重客户数", "definition": "完成订单中customer_unique_id去重；不使用每单变化的customer_id。", "grain": "客户", "unit": "人"},
    {"name": "商品件数", "definition": "完成订单明细行数，每行一个order_item_id。", "grain": "商品", "unit": "件"},
    {"name": "运费", "definition": "同人群商品明细freight_value转分求和，与商品金额分列。", "grain": "商品", "unit": "BRL"},
    {"name": "支付记录金额", "definition": "支付表先按order_id归并；与商品金额/运费独立保留，不当作平台营收。", "grain": "支付记录", "unit": "BRL"},
    {"name": "延迟交付率", "definition": "有效送达样本中，实际送达日>预计送达日的订单占比，按日而非午夜时间比较。", "grain": "订单", "unit": "%"},
    {"name": "有效送达样本", "definition": "状态delivered，实际和预计日期齐全且送达日不早于购买日。", "grain": "订单", "unit": "单"},
    {"name": "低评分率", "definition": "存在选定评价的订单中review_score<=2的占比；多次评价取最新回答时间，再按review_id、分数确定性打破并列。", "grain": "订单", "unit": "%"},
    {"name": "评价覆盖率", "definition": "存在选定评价的订单数/同人群完成订单数，缺失不是好评。", "grain": "订单", "unit": "%"},
    {"name": "平均配送天数", "definition": "有效送达样本的送达日减购买日的平均天数。", "grain": "订单", "unit": "天"},
    {"name": "维度金额变化贡献", "definition": "该维度本期商品金额-上期商品金额；各组变化之和应等于整体变化。", "grain": "品类/客户地区/商家", "unit": "BRL"},
    {"name": "商家可归属体验样本", "definition": "只纳入seller_count=1的完成订单；先去重商家-订单，避免多件商品重复评价。", "grain": "商家-订单", "unit": "单"},
]


def month_bounds(month: str):
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("月份格式必须为YYYY-MM。")
    y, m = map(int, month.split("-"))
    start = date(y, m, 1)
    if not SUPPORTED_START <= month <= SUPPORTED_END:
        raise ValueError(f"当前支持{SUPPORTED_START}至{SUPPORTED_END}；首尾稀疏月份不用于默认比较。")
    end = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return start.isoformat(), end.isoformat()


def previous_month(month):
    y, m = map(int, month.split("-"))
    return f"{y-1:04d}-12" if m == 1 else f"{y:04d}-{m-1:02d}"


def safe_rate(n, d):
    return n / d if d else None


def money(cents):
    return round(float(cents or 0) / 100, 2)


class EvidenceDB:
    """No arbitrary model SQL: only developer-owned parameterized query templates."""
    def __init__(self, path: Path = DB):
        if not Path(path).exists():
            raise FileNotFoundError("请先运行数据准备。")
        self.con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA query_only=ON")
        self.evidence = []

    def query(self, sql, params=(), label=""):
        deadline = time.monotonic() + 10
        self.con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        try:
            cursor = self.con.execute(sql, params)
            rows = [dict(row) for row in cursor.fetchmany(10001)]
            if len(rows) > 10000:
                raise ValueError("查询结果超过10000行，请缩小范围。")
            evidence_id = f"Q{len(self.evidence)+1:02d}"
            self.evidence.append({"id": evidence_id, "label": label, "sql": sql.strip(), "parameters": list(params), "rows": rows})
            return rows
        finally:
            self.con.set_progress_handler(None, 0)

    def close(self):
        self.con.close()


def period_summary(db, start, end):
    return db.query("""
      SELECT COUNT(*) orders, COALESCE(SUM(item_cents),0) item_cents,
             COALESCE(SUM(freight_cents),0) freight_cents, COALESCE(SUM(item_count),0) items,
             COUNT(DISTINCT customer_unique_id) customers,
             SUM(delivery_valid) delivery_n, SUM(CASE WHEN delivery_valid=1 THEN late ELSE 0 END) late_n,
             COUNT(review_score) reviewed_n, SUM(CASE WHEN review_score<=2 THEN 1 ELSE 0 END) low_n,
             AVG(CASE WHEN delivery_valid=1 THEN delivery_days END) average_delivery_days
      FROM fact_orders WHERE order_status='delivered' AND item_cents IS NOT NULL
        AND purchase_date>=? AND purchase_date<?
    """, (start, end), "完成订单指标")[0]


def public_summary(raw):
    n = raw["orders"]
    return {
        "orders": n, "item_amount_brl": money(raw["item_cents"]),
        "freight_brl": money(raw["freight_cents"]), "items": raw["items"], "customers": raw["customers"],
        "aov_brl": money(raw["item_cents"] / n) if n else None,
        "delivery_n": raw["delivery_n"], "late_n": raw["late_n"],
        "late_rate": safe_rate(raw["late_n"], raw["delivery_n"]),
        "reviewed_n": raw["reviewed_n"], "low_n": raw["low_n"],
        "low_review_rate": safe_rate(raw["low_n"], raw["reviewed_n"]),
        "review_coverage": safe_rate(raw["reviewed_n"], n),
        "average_delivery_days": raw["average_delivery_days"],
    }


def dimension_contributions(db, current_start, current_end, prev_start, dimension):
    expressions = {"category": "i.category", "state": "o.customer_state", "seller": "i.seller_id"}
    if dimension not in expressions:
        raise ValueError("维度仅支持category/state/seller。")
    expr = expressions[dimension]
    rows = db.query(f"""
      SELECT COALESCE({expr},'未分类') dimension_value,
        SUM(CASE WHEN o.purchase_date>=? THEN i.item_cents ELSE 0 END) current_cents,
        SUM(CASE WHEN o.purchase_date<? THEN i.item_cents ELSE 0 END) previous_cents
      FROM fact_items i JOIN fact_orders o USING(order_id)
      WHERE o.order_status='delivered' AND o.purchase_date>=? AND o.purchase_date<?
      GROUP BY {expr}
      ORDER BY (SUM(CASE WHEN o.purchase_date>=? THEN i.item_cents ELSE 0 END)
               -SUM(CASE WHEN o.purchase_date<? THEN i.item_cents ELSE 0 END)) ASC
    """, (current_start, current_start, prev_start, current_end, current_start, current_start), f"{dimension}维度金额变化")
    for r in rows:
        r["delta_cents"] = r["current_cents"] - r["previous_cents"]
        r["current_brl"], r["previous_brl"], r["delta_brl"] = map(money, [r["current_cents"], r["previous_cents"], r["delta_cents"]])
    return rows


def business_diagnosis(month=DEFAULT_MONTH, dimensions=None, db_path=DB):
    start, end = month_bounds(month)
    prior = previous_month(month)
    ps, pe = month_bounds(prior)
    dimensions = dimensions or ["category", "state", "seller"]
    db = EvidenceDB(db_path)
    try:
        current, previous = period_summary(db, start, end), period_summary(db, ps, pe)
        if not current["orders"] or not previous["orders"]:
            raise ValueError("比较月份没有足够完成订单，不能计算变化诊断。")
        delta = current["item_cents"] - previous["item_cents"]
        a0 = previous["item_cents"] / previous["orders"]
        a1 = current["item_cents"] / current["orders"]
        # Symmetric (Shapley) two-factor decomposition: sums exactly without order bias.
        volume = (current["orders"] - previous["orders"]) * (a0 + a1) / 2
        ticket = (a1 - a0) * (current["orders"] + previous["orders"]) / 2
        contributions = {dim: dimension_contributions(db, start, end, ps, dim) for dim in dimensions}
        checks = {f"{dim}_reconciles": sum(r["delta_cents"] for r in rows) == delta for dim, rows in contributions.items()}
        checks["factor_decomposition_reconciles"] = abs(volume + ticket - delta) < 0.001
        # Context, never causal attribution: all eligible delivered orders, not only suspect merchants.
        context = db.query("""
          SELECT CASE WHEN late=1 THEN '延迟' ELSE '未延迟' END delivery_group,
                 COUNT(*) orders, COUNT(review_score) reviewed_n,
                 SUM(CASE WHEN review_score<=2 THEN 1 ELSE 0 END) low_n
          FROM fact_orders WHERE order_status='delivered' AND delivery_valid=1
            AND purchase_date>=? AND purchase_date<? GROUP BY late
        """, (start, end), "履约与低评分关联")
        warnings = COMMON_WARNINGS.copy()
        warnings += ["维度贡献是不同切面的完整拆解，不能把品类、地区、商家贡献相加；不说明变化的因果原因。",
                     "商品金额分解用未四舍五入客单价计算；订单数和客单价采用对称分解。"]
        findings = [f"{month}完成订单商品金额{money(current['item_cents']):,.2f} BRL，较{prior}变化{money(delta):+,.2f} BRL（{delta/previous['item_cents']:+.2%}）。",
                    f"对称分解：订单量变化贡献{volume/100:+,.2f} BRL，客单价变化贡献{ticket/100:+,.2f} BRL。"]
        actions = []
        for dim, label in [("category", "品类"), ("state", "客户地区"), ("seller", "商家")]:
            declines = [r for r in contributions.get(dim, []) if r["delta_cents"] < 0]
            if declines:
                head = declines[0]
                findings.append(f"{label}切面最大下降项为{head['dimension_value']}，商品金额变化{head['delta_brl']:+,.2f} BRL。")
                actions.append(f"优先核查{label}{head['dimension_value']}的供给及订单结构，复核{head['delta_brl']:+,.2f} BRL变化是否来自大额订单或商品组合变化。")
        findings.append(f"本期有效送达样本{current['delivery_n']}单，延迟{current['late_n']}单；评价{current['reviewed_n']}单，低评分{current['low_n']}单。")
        actions += ["对经营下降对象结合商家诊断复核；履约相关性不能直接认定是销售变化原因。",
                    "若安排优化，先设计小范围试点和对照观察，再评估真实收益。"]
        if month == "2018-08":
            warnings.append("2018-08接近交易截数尾部，完成状态与履约成熟度可能偏低，不能直接认定经营恶化。")
        return {"scenario": "business", "title": "经营变化诊断", "month": month, "previous_month": prior,
                "current": public_summary(current), "previous": public_summary(previous),
                "delta_brl": money(delta), "change_rate": safe_rate(delta, previous["item_cents"]),
                "decomposition": {"order_volume_brl": volume / 100, "aov_brl": ticket / 100},
                "contributions": contributions, "delivery_review_context": context,
                "checks": checks, "warnings": warnings, "findings": findings, "actions": actions,
                "evidence": db.evidence}
    finally:
        db.close()


RANKINGS = {
    "late_burden": ("above_baseline_late_orders", "delivery_n", "超基准延迟单量"),
    "late_rate": ("late_rate", "delivery_n", "延迟率"),
    "low_review_burden": ("above_baseline_low_review_orders", "reviewed_n", "超基准低评分单量"),
    "low_review_rate": ("low_review_rate", "reviewed_n", "低评分率"),
}


def seller_diagnosis(start_month="2018-05", end_month=DEFAULT_MONTH, min_orders=30, db_path=DB, ranking="late_burden"):
    start, _ = month_bounds(start_month)
    _, end = month_bounds(end_month)
    if start_month > end_month:
        raise ValueError("开始月份不能晚于结束月份。")
    if type(min_orders) is not int or not 10 <= min_orders <= 500:
        raise ValueError("最小样本量需为10至500的整数。")
    if ranking not in RANKINGS:
        raise ValueError("不支持的排序目标；请选择延迟/低评分的负担或比例。")
    db = EvidenceDB(db_path)
    try:
        # Single-seller orders only: one row per merchant-order before aggregation.
        rows = db.query("""
          WITH seller_orders AS (
            SELECT DISTINCT i.seller_id, o.order_id, o.item_cents, o.delivery_valid,
                   o.late, o.review_score, o.delivery_days
            FROM fact_items i JOIN fact_orders o USING(order_id)
            WHERE o.order_status='delivered' AND o.seller_count=1
              AND o.purchase_date>=? AND o.purchase_date<?
          )
          SELECT seller_id, COUNT(*) orders, SUM(item_cents) item_cents,
                 SUM(delivery_valid) delivery_n,
                 SUM(CASE WHEN delivery_valid=1 THEN late ELSE 0 END) late_n,
                 COUNT(review_score) reviewed_n,
                 SUM(CASE WHEN review_score<=2 THEN 1 ELSE 0 END) low_n,
                 AVG(CASE WHEN delivery_valid=1 THEN delivery_days END) average_delivery_days
          FROM seller_orders GROUP BY seller_id ORDER BY seller_id
        """, (start, end), "单商家订单履约体验指标")
        coverage = db.query("""
          SELECT COUNT(*) delivered_orders,
            SUM(CASE WHEN seller_count=1 THEN 1 ELSE 0 END) attributable_orders,
            SUM(CASE WHEN seller_count>1 THEN 1 ELSE 0 END) multi_seller_excluded
          FROM fact_orders WHERE order_status='delivered' AND item_cents IS NOT NULL
            AND purchase_date>=? AND purchase_date<?
        """, (start, end), "商家归属样本覆盖")[0]
        total = {k: sum(r[k] or 0 for r in rows) for k in ["orders", "delivery_n", "late_n", "reviewed_n", "low_n", "item_cents"]}
        late_base, low_base = safe_rate(total["late_n"], total["delivery_n"]), safe_rate(total["low_n"], total["reviewed_n"])
        eligible, small = [], 0
        for raw in rows:
            if raw["orders"] < min_orders:
                small += 1
                continue
            r = {"seller_id": raw["seller_id"], **public_summary({**raw, "items": None, "customers": None, "freight_cents": 0})}
            for unavailable in ["items", "customers", "freight_brl"]:
                r.pop(unavailable)
            # Transparent prioritization rule, not an ML probability or a causal estimate.
            excess_late = max(0, raw["late_n"] - raw["delivery_n"] * (late_base or 0)) if raw["delivery_n"] >= min_orders else None
            excess_low = max(0, raw["low_n"] - raw["reviewed_n"] * (low_base or 0)) if raw["reviewed_n"] >= min_orders else None
            r["above_baseline_late_orders"] = round(excess_late, 2) if excess_late is not None else None
            r["above_baseline_low_review_orders"] = round(excess_low, 2) if excess_low is not None else None
            r["priority_basis"] = f"按{RANKINGS[ranking][2]}降序；对应样本不足不参与排序，两类问题不相加，不代表商家责任。"
            r["insufficient_delivery_sample"] = raw["delivery_n"] < min_orders
            r["insufficient_review_sample"] = raw["reviewed_n"] < min_orders
            eligible.append(r)
        sort_key, denominator, ranking_label = RANKINGS[ranking]
        ranked = [r for r in eligible if r[denominator] >= min_orders and r[sort_key] is not None]
        ranked.sort(key=lambda r: (-r[sort_key], -(r["above_baseline_low_review_orders"] or 0), -r["orders"], r["seller_id"]))
        eligible_orders = sum(r["orders"] for r in eligible)
        checks = {"merchant_orders_reconcile": total["orders"] == coverage["attributable_orders"],
                  "late_not_above_denominator": all(r["late_n"] <= r["delivery_n"] for r in rows),
                  "low_not_above_denominator": all(r["low_n"] <= r["reviewed_n"] for r in rows)}
        warnings = COMMON_WARNINGS + [
            "体验和实际送达是订单级信号，不等于商家责任；多商家订单已从此排名剔除。",
            "基准为区间内全部可归属订单的加权比例（包括小样本商家）；尚未控制地区、品类和配送距离。",
            "优先级是明确的排查启发式，不是风险模型或统计显著性结论。样本量不足的指标不参与相应排序。",
            "缺失评价保留为缺失，不当作好评；评价覆盖率一并展示。",
        ]
        if end_month == "2018-08":
            warnings.append("区间包含2018-08，存在尾部成熟度风险。")
        findings = [f"观察区间共{coverage['delivered_orders']}笔完成订单，可归属单商家的有{coverage['attributable_orders']}笔；剔除多商家订单{coverage['multi_seller_excluded']}笔。",
                    f"{len(eligible)}家商家达到{min_orders}单门槛；排除{small}家小样本商家。"]
        findings.append(f"达标商家覆盖{eligible_orders}笔可归属订单（{safe_rate(eligible_orders, coverage['attributable_orders']) or 0:.2%}）；按{ranking_label}排序，{len(ranked)}家具备对应指标样本。")
        if ranked:
            head = ranked[0]
            value = f"{head[sort_key]:.2%}" if ranking.endswith("rate") else f"{head[sort_key]:.2f}单"
            findings.append(f"按{ranking_label}首位商家为{head['seller_id']}，{ranking_label}{value}；是否为商家责任仍需核查。")
        return {"scenario": "seller", "title": "商家履约与体验诊断", "start_month": start_month, "end_month": end_month,
                "min_orders": min_orders, "coverage": coverage, "baseline": {"late_rate": late_base, "low_review_rate": low_base},
                "eligible_sellers": len(eligible), "small_sample_sellers_excluded": small,
                "ranking": ranking, "ranking_label": ranking_label, "rankable_sellers": len(ranked),
                "eligible_order_coverage": safe_rate(eligible_orders, coverage["attributable_orders"]),
                "eligible_orders": eligible_orders, "sellers": ranked[:50], "ranking_truncated_to": 50, "checks": checks,
                "warnings": warnings, "findings": findings, "actions": [
                    "优先核查高问题规模商家的出库、承运及承诺时效，不能凭订单终态直接处罚商家。",
                    "结合品类和客户地区做同类比较，验证是否为结构差异；必要时人工抽查订单。",
                    "优化后跟踪延迟率、低评分率与覆盖率，使用对照设计检验是否真正改善。"],
                "evidence": db.evidence}
    finally:
        db.close()


def compact_tool_result(report):
    """Bound model context while retaining numerical evidence and definitions."""
    result = {k: v for k, v in report.items() if k not in ["evidence", "contributions", "sellers"]}
    if "contributions" in report:
        result["contributions"] = {dim: {"largest_decreases": rows[:5], "largest_increases": sorted(rows, key=lambda x: -x["delta_cents"])[:5],
                                            "total_groups": len(rows)} for dim, rows in report["contributions"].items()}
    if "sellers" in report:
        result["sellers"] = report["sellers"][:10]
    result["evidence_ids"] = [{"id": e["id"], "label": e["label"], "parameters": e["parameters"]} for e in report["evidence"]]
    result["metric_evidence"] = metric_catalog(report)
    result["output_contract"] = "metrics只列所问的metric_evidence.key，数值照工具，来源必须用当前工具序号加sources；跨月变化需Q01和Q02。商家候选已按本次ranking在完整达标样本内排序再截取，实体结论由工具记录渲染。"
    return result
