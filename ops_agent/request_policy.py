"""Request-derived capability/scope contracts, independent of evaluation cases.

Application constraints guide the model's tool choice. They are not evidence
that a model autonomously selected the correct tool. Unknown requests retain
the bounded tool set; these finite policies are not a universal semantic guard.
"""
from __future__ import annotations

import re
import unicodedata

BUSINESS_KEYS = {"orders", "customers", "items", "delivery_n", "late_n", "reviewed_n", "low_n",
                 "item_amount_brl", "aov_brl", "freight_brl", "late_rate", "low_review_rate",
                 "review_coverage", "delta_brl", "change_rate"}
SELLER_KEYS = {"attributable_orders", "excluded_multi_seller_orders", "eligible_sellers", "baseline_late_rate"}
ALIASES = {"评价覆盖率": "review_coverage", "低评分率": "low_review_rate", "延迟率": "late_rate",
           "客单价": "aov_brl", "商品金额": "item_amount_brl", "完成订单数": "orders",
           "达标商家数": "eligible_sellers", "可归属订单": "attributable_orders"}
POPULATIONS = {
    "merchant_leads": {"population": "B2B商家潜客", "grain": "商家营销线索，不是消费者"},
    "consumer_visits": {"population": "B2C消费者访问事件", "available": False},
    "delivered_orders": {"population": "最终完成订单队列", "grain": "去重订单"},
    "reviewed_orders": {"population": "同完成队列中有选定评价的订单", "grain": "去重订单"},
    "valid_delivery_orders": {"population": "日期有效的完成订单子集", "grain": "去重订单"},
    "single_seller_orders": {"population": "单商家可归属完成订单", "grain": "商家-订单"},
}

TYPE_REASON = ("商家营销线索属于B2B商家潜客，消费者到访属于B2C访问事件，分析对象与粒度不兼容。"
               "没有消费者访问日志，不能把线索、订单或付费客户数改作访问分母；用户同意也不能使其成为真实消费者转化率。")


def normalized_terms(text):
    # ASCII boundaries work beside Chinese; underscores/hyphens in field names
    # are separators, not a way to hide the population represented by a field.
    return re.sub(r"[_\-/]+", " ", unicodedata.normalize("NFKC", text).casefold())


LEADS = r"线索|商机|潜客|成交意向|(?<![a-z0-9])leads?(?![a-z0-9])|(?<![a-z0-9])mql(?![a-z0-9])|closed\s*won"
VISITS = r"到访|访问|浏览|访客|流量|(?<![a-z0-9])(?:uv|pv)(?![a-z0-9])|visits?|visitors?|sessions?|footfall|shopper"
RETAIL = r"消费者|买家|用户购买|购买比例|购买百分比|consumer|shopper|retail|purchase percentage"


def substitution_reason(text):
    text = normalized_terms(text)
    # Explicitly declining a substitution must not block an unrelated valid
    # metric query. Clauses are local: a later 'but use X as Y' is still seen.
    clauses = re.split(r"[，,。！？；;\n]|(?:但是|但仍|然而|however|\bbut\b)", text)
    text = "\n".join(c for c in clauses if not re.search(
        r"不能|不可|不应|不得|不使用|不用|不要|不把|不将|不等同|不兼容|cannot|must not|do not|not equivalent", c))
    lead = bool(re.search(LEADS, text))
    visit = bool(re.search(VISITS, text))
    retail = bool(re.search(RETAIL, text))
    if lead and (visit or retail):
        return TYPE_REASON
    return None


def semantic_output_issue(text):
    """Flag affirmative proposals of the known type error, not mere mention.

    Negative explanations such as '不能将商家线索当消费者访问' are allowed.
    This is a narrow deterministic supplement, not an independent LLM judge.
    """
    text = normalized_terms(text)
    if not (re.search(LEADS, text) and re.search(VISITS+"|"+RETAIL, text)):
        return None
    for sentence in re.split(r"[，,。！？；;\n]|(?:但是|但仍|然而|however|\bbut\b)", text):
        if not (re.search(LEADS, sentence) or re.search(VISITS+"|"+RETAIL, sentence)):
            continue
        negative = re.search(r"不能|不可|不应|不得|不使用|不用|无法|不是|不等同|不同人群|不兼容|缺少|缺失|没有|cannot|can't|must not|do not|don't|not equivalent|not valid|not the same|unavailable|missing", sentence)
        # Mapping proposals override a preceding 'missing' disclaimer in the
        # same clause; negation specifically attached to the mapping still wins.
        mapping = re.search(r"当作|当成|视为|等同|映射|替代|作为|计算为|口径定义|定义将|treat|\buse\b|define|assume|\blead\s*=", sentence)
        deny_mapping = re.search(r"(?:不能|不可|不应|不得|不使用|不用|不是|不等同|不兼容|cannot|must not|do not|don't|not equivalent).{0,35}(?:当作|视为|映射|替代|作为|treat|use|define)", sentence)
        affirmative = mapping or re.search(r"将使用|会使用|确认.{0,20}(?:视|作|映射|使用)", sentence)
        if mapping and not deny_mapping:
            negative = None
        if affirmative and not negative:
            return TYPE_REASON
    return None


def _scope(q):
    # Remove explicit opt-outs before matching scope terms, not numeric facts.
    q = re.sub(r"(?:不需要|无需|不用|不要|不做|不看)(?:调用|分析|排查)?商家(?:工具|诊断|排名)?", "", q)
    q = re.sub(r"(?:不能|不可|不是|不应|不得)[^，。；;\n]{0,45}", "", q)
    keys = {key for key in BUSINESS_KEYS | SELLER_KEYS if re.search(r"(?<![A-Za-z_])"+key+r"(?![A-Za-z_])", q)}
    seller = bool(keys & SELLER_KEYS or re.search(r"商家|商户|seller|merchant|可归属|门槛", q, re.I))
    global_scope = bool(re.search(r"总体|全站|整个平台|全量|完整队列|完成订单队列|all delivered|platform.wide|overall|entire", q, re.I))
    business = bool((keys & BUSINESS_KEYS and (not seller or global_scope or keys & SELLER_KEYS))
                    or global_scope or re.search(r"经营|环比|商品金额|销售额", q))
    if not seller and re.search(r"评价|评分|覆盖率|review|订单|order|客单价|aov|延迟|late", q, re.I):
        business = True
    if not keys:
        keys = {key for alias, key in ALIASES.items() if alias in q}
        # Merchant free-text asks remain merchant-scoped; don't import global
        # low-review keys just because a merchant ranking mentions low scores.
        if seller and not global_scope:
            keys &= SELLER_KEYS
    return business, seller, keys


def _months(text):
    return [f"{int(y):04d}-{int(m):02d}" for y, m in re.findall(r"(20\d{2})[-年](\d{1,2})(?:月)?", text)]


def seller_ranking(text):
    if re.search(r"低评分|差评|low.review", text, re.I):
        return "low_review_rate" if re.search(r"率.{0,8}(?:最高|排序|排名)|highest.{0,15}rate", text, re.I) else "low_review_burden"
    if re.search(r"延迟率.{0,8}(?:最高|排序|排名)|highest.{0,15}late.rate", text, re.I):
        return "late_rate"
    return "late_burden"


def plan_request(question):
    """Finite task parser, not general language understanding.

    Attach dates to their task clause, never assign first/last dates globally.
    Ambiguous or repeated same-tool windows are clarified, not silently merged.
    """
    business, seller, keys = _scope(question)
    tools = []
    if business:
        tools.append("diagnose_business")
    if seller:
        tools.append("diagnose_sellers")
    # Ignore machine-output instructions when attaching dates to task clauses.
    q = re.split(r"\n(?:所需metric|输出结构化)", question)[0]
    fragments = [x.strip() for x in re.split(r"[，,。；;\n]|然后|随后|接着|再(?=报告|分析|检查|看|查)|\bthen\b", q, flags=re.I) if x.strip()]
    segments = []
    pending = []
    for fragment in fragments:
        b, s, _ = _scope(fragment)
        # Threshold-only fragments modify the preceding merchant task.
        if re.search(r"门槛|最低|至少|最少|样本量|min_orders", fragment, re.I) and not re.search(r"商家|商户|seller|merchant|经营|总体", fragment, re.I) and segments:
            segments[-1]["text"] += "，" + fragment
        elif b or s:
            segments.append({"text": "，".join(pending + [fragment]), "business": b, "seller": s})
            pending = []
        elif segments:
            segments[-1]["text"] += "，" + fragment
        else:
            pending.append(fragment)
    args, tasks, clarification, assumptions = {}, [], [], []
    all_months = _months(q)
    for segment in segments:
        dates = _months(segment["text"])
        scopes = [n for n, yes in [("diagnose_business", segment["business"]), ("diagnose_sellers", segment["seller"])] if yes and n in tools]
        if len(scopes) > 1 and len(set(dates)) > 1:
            clarification.append("同一任务片段含多个分析对象和多个日期，请分别指定经营月份与商家区间。")
            continue
        if not dates and len(set(all_months)) == 1:
            dates = all_months[:1]
            assumptions.append("未单独给日期的任务沿用唯一明确月份；商家任务为该单月，不扩展为三个月。")
        if not dates:
            clarification.append("请为每个任务明确月份或观察区间；不会默认采用其他任务的日期。")
            continue
        for name in scopes:
            if name == "diagnose_business":
                if len(set(dates)) != 1:
                    clarification.append("经营工具一次只支持一个月份与上月比较，请明确分析月份。")
                    continue
                values = {"month": dates[0]}
            else:
                if len(dates) > 2:
                    clarification.append("商家任务包含多于两个日期，请明确单一连续区间。")
                    continue
                minimum = re.findall(r"(?:min_orders\s*[=:]?|门槛|最低|至少|最少|样本量)[^\d\n]{0,8}(\d+)", segment["text"], re.I)
                if len(set(minimum)) > 1:
                    clarification.append("同一商家任务存在冲突门槛，请只指定一个值。")
                    continue
                values = {"start_month": dates[0], "end_month": dates[-1], "min_orders": int(minimum[0]) if minimum else 30}
                ranking = seller_ranking(segment["text"])
                if ranking != "late_burden":
                    values["ranking"] = ranking
                if values["start_month"] > values["end_month"]:
                    clarification.append("商家开始月份不能晚于结束月份。")
            if name in args and args[name] != values:
                clarification.append("同一工具的多个不同窗口暂不合并，请分次提问。")
                continue
            if name not in args:
                tasks.append({"name": name, "arguments": values, "source_clause": segment["text"]})
            args[name] = values
    if set(tools) - set(args) and not clarification:
        clarification.append("请为各分析任务提供明确月份，无法安全推断。")
    return {"scope": "both" if business and seller else "business" if business else "seller" if seller else "unknown",
            "required_tools": tools, "allowed_tools": ["metric_dictionary"]+(tools or ["diagnose_business", "diagnose_sellers"]),
            "requested_metric_keys": sorted(keys), "explicit_arguments": args,
            "tasks": tasks, "clarification": list(dict.fromkeys(clarification)), "assumptions": list(dict.fromkeys(assumptions)),
            "population_contract": POPULATIONS,
            "notice": "有限规则按任务片段绑定日期；歧义需澄清，不是通用语义理解或纯模型自主路由。"}


def tool_choice(plan, trace):
    successes = {t["name"] for t in trace if t.get("outcome", {}).get("ok")}
    if "metric_dictionary" not in successes:
        return {"type": "function", "name": "metric_dictionary"}
    pending = [name for name in plan["required_tools"] if name not in successes]
    if len(pending) == 1:
        return {"type": "function", "name": pending[0]}
    if pending:
        return {"type": "allowed_tools", "mode": "required", "tools": [{"type": "function", "name": n} for n in pending]}
    return "none" if plan["required_tools"] else {"type": "allowed_tools", "mode": "auto", "tools": [{"type": "function", "name": n} for n in plan["allowed_tools"]]}


def tool_request_error(plan, name, args):
    if name not in plan["allowed_tools"]:
        return f"本请求scope={plan['scope']}，不允许{name}；应使用{plan['required_tools']}，不能替换总体/单商家样本。"
    explicit = plan["explicit_arguments"].get(name)
    actual = dict(args)
    if name == "diagnose_sellers" and actual.get("ranking") == "late_burden":
        actual.pop("ranking")
    if explicit is not None and actual != explicit:
        return f"参数与用户指定的窗口/门槛不一致；请调用{name}，参数为{explicit}。未执行错误参数SQL。"
    return None
