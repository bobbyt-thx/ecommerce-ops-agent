"""Application-level data boundaries and verifiable answer contracts.

No reference answers or evaluation case IDs are imported here. The model plans
tool use; factual metrics and their provenance must match executed tool results.
"""
from __future__ import annotations

import json
import math
import re
from .request_policy import substitution_reason, semantic_output_issue

ANSWER_SCHEMA = {"type": "object", "properties": {
    "decision": {"type": "string", "enum": ["supported", "unsupported", "needs_clarification"]},
    "metrics": {"type": "array", "items": {"type": "object", "properties": {
        "key": {"type": "string"}, "value": {"type": ["number", "null"]},
        "evidence": {"type": "string"}}, "required": ["key", "value", "evidence"],
        "additionalProperties": False}},
    "findings": {"type": "array", "items": {"type": "string"}},
    "limitations": {"type": "array", "items": {"type": "string"}},
}, "required": ["decision", "metrics", "findings", "limitations"], "additionalProperties": False}

LIMITS = ["Olist公开历史数据，不是实时经营；商品金额不等于平台营收或利润。",
          "缺失不等于0；无成本、广告费、消费者访问日志及干预实验，不支持补成真实经营指标。",
          "只读分析，不改写数据、不执行处罚；描述性统计不证明因果或商家责任。"]

METRIC_LABELS = {"orders": "完成订单数", "customers": "完成队列客户数", "items": "商品明细数",
    "delivery_n": "有效送达订单数", "late_n": "延迟订单数", "reviewed_n": "有评价订单数", "low_n": "低评分订单数",
    "item_amount_brl": "完成订单商品金额", "aov_brl": "商品客单价", "freight_brl": "运费",
    "late_rate": "延迟率", "low_review_rate": "低评分率", "review_coverage": "评价覆盖率",
    "delta_brl": "商品金额环比净变化", "change_rate": "商品金额环比变化率", "attributable_orders": "可归属订单数",
    "excluded_multi_seller_orders": "排除的多商家订单数", "eligible_sellers": "达到订单门槛的商家数", "baseline_late_rate": "区间加权延迟率"}


def business_delivery(reports):
    """Verified report entities and linked source rows, never model prose.

    These are deterministic presentation records. They do not establish that
    the model selected an entity, explained a cause or implemented an action.
    """
    findings, entities = [], []
    for report in reports:
        if not report.get("checks") or not all(report["checks"].values()):
            continue
        tag = report.get("tool", "T1")
        if report["scenario"] == "business":
            current, previous = report["current"], report["previous"]
            findings += [f"{report['month']}对比{report['previous_month']}：商品金额{current['item_amount_brl']:,.2f} BRL，环比{report['change_rate']:+.2%}（{tag}/Q01，{tag}/Q02）。",
                         f"延迟率{previous['late_rate']:.2%}→{current['late_rate']:.2%}；低评分率{previous['low_review_rate']:.2%}→{current['low_review_rate']:.2%}。金额与体验分开判断，不据此认定因果（{tag}/Q01，{tag}/Q02）。"] if all(current.get(k) is not None and previous.get(k) is not None for k in ["late_rate", "low_review_rate"]) else report["findings"][:2]
        else:
            label = report.get("ranking_label", "超基准延迟单量")
            findings.append(f"{report['start_month']}至{report['end_month']}，门槛{report['min_orders']}单；按{label}在完整合格样本内排序，以下为待核查对象，不是责任认定。")
            # Join each presented entity back to the executed raw aggregate row.
            evidence = next(e for e in report["evidence"] if e["id"] == "Q01")
            source_rows = {row["seller_id"]: (i, row) for i, row in enumerate(evidence["rows"], 1)}
            for row in report["sellers"][:3]:
                index, raw = source_rows[row["seller_id"]]
                if any(row[k] != raw[k] for k in ["orders", "delivery_n", "late_n", "reviewed_n", "low_n"]):
                    raise ValueError("商家展示实体与SQL行不一致，停止展示。")
                entity = {"seller_id": row["seller_id"], "orders": row["orders"], "delivery_n": row["delivery_n"],
                          "late_n": row["late_n"], "reviewed_n": row["reviewed_n"], "low_n": row["low_n"],
                          "evidence": f"{tag}/Q01", "source_row": index, "ranking": report.get("ranking", "late_burden")}
                entities.append(entity)
                findings.append(f"待核查商家{row['seller_id']}：订单{row['orders']}单；有效送达{row['delivery_n']}单、延迟{row['late_n']}单；有评价{row['reviewed_n']}单、低评分{row['low_n']}单（{tag}/Q01，第{index}行）。")
    return findings, entities


class AnswerFormatError(ValueError):
    """Retryable serialization/shape error, never a wrong numeric value."""


def boundary_reason(question):
    """Conservative scope screen; it does not execute or fabricate diagnostics.

    Explicit opt-outs are masked to avoid rejecting a supported order analysis
    merely because the user says '不要算利润'. Novel phrasing can still escape
    this finite guard, so the model and output checks remain additional layers.
    """
    type_reason = substitution_reason(question)
    if type_reason:
        return type_reason
    q = re.sub(r"(?:不需要|无需|不要|不用|不做|不)(?:计算|估算|估计|分析|算)?(?:净利润|利润率|利润|广告费|广告ROI)", "", question)
    q = re.sub(r"(?:do not|don't|no need to)\s+(?:calculate|estimate)\s+(?:profit|ad spend)", "", q, flags=re.I)
    q = re.sub(r"(?:不|不要|不需要|禁止)(?:修改|改写|删除|覆盖(?!率)|更新)[^，,。;；\n]*", "", q)
    tests = [
        (r"净利润|利润率|毛利率|(?:算|计算|估算).{0,8}利润|\bprofit\b|\bmargin\b",
         "缺少成本与费用，不能计算真实利润或利润率，也不能用行业假设冒充真实值。"),
        (r"广告费|广告费用|广告支出|投放费用|广告.*ROI|投放.*ROI|ROAS|ad\s*spend|advertising\s*(?:cost|expense)|marketing\s*spend",
         "没有广告费用数据；缺失广告费不能当作0，也不能输出真实投放收益或费用占比。"),
        (r"(?:访问|浏览|点击|访客|流量).{0,18}(?:转化|下单率)|(?:visit|visitor|session|click).{0,25}conversion",
         "缺少消费者访问/点击分母；商家营销线索与消费者访问不是同一人群，不能替代。"),
        (r"(?:优惠券|治理|干预).{0,18}(?:带来|导致|提升|归因)|证明.{0,18}(?:提升|改善)|causal|causation",
         "缺少干预事件与可信对照，不能证明优惠券、治理或其他干预的因果收益。"),
        (r"(?:修改|改写|删除|覆盖(?!率)|更新).{0,18}(?:数据库|原始|评分|记录)|(?:数据库|低评分|差评|原始评价).{0,18}(?:改成|改为|删除|更新)|\b(?:delete|update|overwrite)\b.{0,20}(?:database|rating|record)",
         "工具只读；不能改写评分、删除记录或伪造原始数据。"),
        (r"(?:称为|当成|改叫|改称|视为).{0,12}(?:营收|收入)|(?:call|rename|label).{0,30}(?:platform revenue|platform income)",
         "商品金额不是平台营收或收入，不能改换名称掩盖指标口径。"),
        (r"(?:一单|1单|一条|单笔).{0,25}(?:处罚|责任|认定)|(?:自动|直接)处罚",
         "单条评价与订单终态不能确定商家责任，也不能自动处罚；需足够样本和人工核查。"),
    ]
    for pattern, reason in tests:
        if re.search(pattern, q, re.I):
            return reason
    for match in re.finditer(r"(?:min_orders\s*[=:]?|门槛|最低|至少|最少|样本量)[^\d\n]{0,8}(\d+)\s*(?:单|orders?)?", q, re.I):
        if not 10 <= int(match[1]) <= 500:
            return "商家样本门槛仅支持10至500单；不能用极小样本输出可靠治理结论。"
    months = re.findall(r"(20\d{2})[-年](\d{1,2})(?:月)?", q)
    for year, month in months:
        value = f"{int(year):04d}-{int(month):02d}"
        if not 1 <= int(month) <= 12 or not "2017-01" <= value <= "2018-08":
            return "请求月份不在当前工具支持的2017-01至2018-08历史区间，不能外推真实数据。"
    if re.search(r"2017[-年]0?1(?!\d)(?:月)?.{0,20}(?:上月|环比|比较)", q):
        return "经营月环比从2017-02开始；当前工具不直接提供2017-01相对上月的诊断。"
    return None


def unsupported_answer(reason):
    return {"decision": "unsupported", "metrics": [], "findings": [reason], "limitations": LIMITS.copy()}


def metric_catalog(report):
    """Map actual report fields to their specific executed SQL and formula."""
    rows = {}
    def add(key, value, sources, formula, unit):
        rows[key] = {"key": key, "value": value, "sources": sources, "formula": formula, "unit": unit}
    if report["scenario"] == "business":
        current = report["current"]
        count_keys = ["orders", "customers", "items", "delivery_n", "late_n", "reviewed_n", "low_n"]
        for key in count_keys:
            add(key, current[key], ["Q01"], f"current.{key}", "计数")
        for key in ["item_amount_brl", "aov_brl", "freight_brl"]:
            add(key, current[key], ["Q01"], f"current.{key}；分转BRL，客单价为商品金额/订单数", "BRL")
        for key, formula in {"late_rate":"late_n/delivery_n；分母为有效送达订单数",
                             "low_review_rate":"low_n/reviewed_n；分母为有选定评价订单数",
                             "review_coverage":"reviewed_n/orders；分母为同队列完成订单数"}.items():
            add(key, current[key], ["Q01"], formula, "比例0–1")
        add("delta_brl", report["delta_brl"], ["Q01", "Q02"], "本期商品金额-上期商品金额", "BRL")
        add("change_rate", report["change_rate"], ["Q01", "Q02"], "(本期商品金额-上期商品金额)/上期商品金额", "比例0–1")
    else:
        add("attributable_orders", report["coverage"]["attributable_orders"], ["Q02"], "coverage.attributable_orders", "单")
        add("excluded_multi_seller_orders", report["coverage"]["multi_seller_excluded"], ["Q02"], "coverage.multi_seller_excluded", "单")
        add("eligible_sellers", report["eligible_sellers"], ["Q01"], f"逐商家orders>={report['min_orders']}的商家数；不是展示清单长度", "家")
        add("baseline_late_rate", report["baseline"]["late_rate"], ["Q01"], "全部单商家订单sum(late_n)/sum(delivery_n)，包括小样本商家", "比例0–1")
    return rows


def validate_answer(text, reports, allow_entity_only=False):
    """Validate numbers; bind provenance deterministically, retaining raw text.

    Never repairs a wrong numeric answer using an oracle. The verified findings
    are template-rendered metrics and developer-authored tool actions/warnings;
    unchecked model prose is retained only as a raw draft, not passed as fact.
    """
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise AnswerFormatError("JSON对象包含重复字段，不能任意取最后一个。")
            obj[key] = value
        return obj
    try:
        answer = json.loads(text, object_pairs_hook=unique_object)
    except json.JSONDecodeError as error:
        raise AnswerFormatError("输出不是完整有效的JSON。") from error
    if not isinstance(answer, dict) or set(answer) != set(ANSWER_SCHEMA["required"]):
        raise AnswerFormatError("模型输出缺少约定字段。")
    if answer["decision"] not in {"supported", "unsupported", "needs_clarification"}:
        raise ValueError("无效支持性判断。")
    if not all(isinstance(answer[k], list) for k in ["metrics", "findings", "limitations"]):
        raise AnswerFormatError("无效模型输出列表。")
    if not all(isinstance(v,str) for k in ["findings","limitations"] for v in answer[k]):
        raise AnswerFormatError("叙述字段必须为字符串列表。")
    if not all(isinstance(m,dict) and set(m)=={"key","value","evidence"} and isinstance(m["key"],str) and isinstance(m["evidence"],str) for m in answer["metrics"]):
        raise AnswerFormatError("指标行字段格式不正确。")
    if len({m["key"] for m in answer["metrics"]}) != len(answer["metrics"]):
        raise AnswerFormatError("指标key重复；请每项仅输出一次，不能自动选取或合并。")
    audit = {"policy_version": "scope-v5-local", "changes": [], "metric_checks": [],
             "presentation": "数值经工具校验；来源和含数值叙述采用程序模板。模型原文单独保留。"}
    semantic_issue = semantic_output_issue("\n".join(answer["findings"]+answer["limitations"]))
    audit["semantic_contract_pass"] = semantic_issue is None
    if semantic_issue:
        audit["semantic_violation"] = semantic_issue
        audit["changes"].append("原模型建议了不兼容的指标替代；已隔离，不以用户确认放行。")
        return unsupported_answer(semantic_issue), audit
    if answer["decision"] != "supported":
        if answer["metrics"]:
            audit["changes"].append("拒答中的null、布尔或建议参数行移出metrics；未生成替代数值。")
        final = {"decision": answer["decision"], "metrics": [],
                 "findings": ["本次请求无法按现有数据和工具输出可验证的经营指标。"],
                 "limitations": LIMITS.copy()}
        return final, audit
    catalogs = [(r["tool"], metric_catalog(r)) for r in reports if all(r.get("checks", {}).values())]
    verified = []
    seen = set()
    for candidate in answer["metrics"]:
        key, value = candidate.get("key"), candidate.get("value")
        options = [(tag, cat[key]) for tag, cat in catalogs if key in cat]
        if key in seen or len(options) != 1:
            raise ValueError("指标重复、无工具来源或跨窗口歧义；停止展示而非猜测。")
        seen.add(key)
        tag, fact = options[0]
        expected = fact["value"]
        tolerance = .00005 if fact["unit"] == "比例0–1" else .005 if fact["unit"] == "BRL" else 0
        if type(value) not in [int, float] or not math.isfinite(value) or expected is None or abs(value-expected) > tolerance:
            raise ValueError(f"指标{key}未通过工具数值校验；未替换错误答案。")
        refs = [f"{tag}/{source}" for source in fact["sources"]]
        raw_refs = re.findall(r"T\d+/Q\d+", candidate.get("evidence", ""))
        raw_correct = set(raw_refs) == set(refs)
        audit["metric_checks"].append({"key": key, "numeric_pass": True,
                                       "model_sources_pass": raw_correct, "bound_sources": refs})
        if not raw_correct:
            audit["changes"].append(f"{key}的模型引用由程序重新绑定；原引用仍保存在raw_answer。")
        verified.append({"key": key, "value": value, "evidence": ", ".join(refs)})
    delivery, entities = business_delivery(reports)
    if not verified and not (allow_entity_only and entities):
        raise ValueError("支持的回答没有可验证指标，未生成事实结论。")
    facts = []
    for row in verified:
        fact = next(cat[row["key"]] for _, cat in catalogs if row["key"] in cat)
        value = f"{row['value']:.2%}" if fact["unit"] == "比例0–1" else str(row["value"])
        facts.append(f"{METRIC_LABELS.get(row['key'], row['key'])}：{value}（{row['evidence']}；{fact['formula']}）。")
    audit["verified_entities"] = entities
    audit["entity_origin"] = "由通过检查的工具报告和SQL行生成；不是模型自由叙述通过验证。"
    actions = list(dict.fromkeys(a for r in reports for a in r.get("actions", [])))[:4]
    warnings = list(dict.fromkeys(LIMITS + [w for r in reports for w in r.get("warnings", [])]))
    return {"decision": "supported", "metrics": verified,
            "findings": delivery + facts + ["建议核查，不代表已实施或验证收益：" + a for a in actions],
            "limitations": warnings}, audit
