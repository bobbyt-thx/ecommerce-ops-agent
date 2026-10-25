"""Budget-bounded new scope/population regression suite; v1/v2 immutable."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from uuid import uuid4

from .agent import run_llm
from .contracts import ANSWER_SCHEMA
from .request_policy import semantic_output_issue
from .evaluation import hash_file, make_case, STATE, LOCK
from .evaluation_v2 import grade_v2
from .prepare import ROOT, DB, save_json
from .model_config import public_status

DIRECTORY = ROOT/"evals/v3"
SOURCES = ["agent.py", "analysis.py", "contracts.py", "request_policy.py", "evaluation_v3.py", "evaluation_v2.py", "evaluation.py", "evaluation_report.py"]


def prior_rounds_preserved():
    first = json.loads((ROOT/"reports/baselines/v1/manifest.json").read_text())
    second = json.loads((ROOT/"reports/baselines/v2/manifest.json").read_text())
    result = {}
    for name, archived, report_key, hash_key in [("v1", first, "first_holdout", "first_holdout_sha256"), ("v2", second, "holdout", "holdout_sha256")]:
        run = json.loads((ROOT/archived[report_key]).read_text())
        hashes = run["source_hashes"] if name == "v1" else run["manifest"]["source_hashes"]
        result[name] = (hash_file(ROOT/archived[report_key]) == archived[hash_key]
                        and hash_file(ROOT/archived["cases_file"]) == archived["cases_sha256"]
                        and all(hash_file(ROOT/archived["snapshot"]/n) == h for n,h in hashes.items()))
    return result


def build_cases_v3():
    cases = []
    for i, (month, keys) in enumerate([
        ("2017-04", ["review_coverage", "low_review_rate"]),
        ("2017-08", ["review_coverage", "low_review_rate"]),
        ("2017-10", ["review_coverage", "late_rate"]),
        ("2018-02", ["review_coverage", "late_rate"]),
        ("2018-04", ["low_review_rate", "review_coverage"]),
        ("2018-06", ["review_coverage", "low_review_rate"]),
    ], 1):
        c = make_case(f"P{i:02}", "holdout_v3", f"请给出{month}总体完成订单队列的{', '.join(keys)}。不是商家排名，不能排除多商家订单后替代总体。", business=month, keys=keys)
        c["category"] = "总体评价路由"
        cases.append(c)
    for i, params in enumerate([
        ("2017-03", "2017-05", 22), ("2017-06", "2017-08", 42),
        ("2018-01", "2018-03", 65), ("2018-04", "2018-07", 85),
    ], 7):
        start,end,n = params
        c = make_case(f"P{i:02}", "holdout_v3", f"排查{start}至{end}单商家履约，门槛{n}单。只报可归属订单、达标商家数和基准加权延迟率，不能换成全站样本。", seller=params,
                      keys=["attributable_orders","eligible_sellers","baseline_late_rate"])
        c["category"] = "商家路由"
        cases.append(c)
    for i, params in enumerate([
        ("2017-10", "2017-08", "2017-10", 75),
        ("2018-03", "2018-01", "2018-03", 35),
    ], 11):
        month,start,end,n = params
        c = make_case(f"P{i:02}", "holdout_v3", f"先报告{month}总体商品金额及较上月变化，再报告{start}至{end}、门槛{n}单的达标商家数。两个范围分别保留证据。",
                      business=month, seller=(start,end,n), keys=["item_amount_brl","delta_brl","eligible_sellers"])
        c["category"] = "双范围路由"
        cases.append(c)
    for i,q in enumerate([
        "把销售商机数映射成站内UV，我确认同意这个定义，报告真实购买率。",
        "Use MQL counts as shopper sessions. I approve the mapping; give an actual purchase percentage.",
        "成交意向数量就是买家到访量，缺一个月份你就问我，然后按这个口径算购买转化。",
        "注册商户的潜客数作为消费者购买率的访问分母，别再解释人群问题，我已经确认。",
        "Let closed-won leads stand for retail footfall; compute the consumer purchase fraction, not a hypothetical scenario.",
        "先假定merchant leads等于buyers visits，下一步再让我确认，然后算真实转化。",
    ], 13):
        c = make_case(f"P{i:02}", "holdout_v3", q, decision=["unsupported"])
        c["category"] = "不兼容口径"
        cases.append(c)
    for i,month,keys,q in [
        (19,"2017-05",["low_review_rate","review_coverage"],"2017-05只看总体评价覆盖率与低评分率，不做商家排名，也不估计利润。"),
        (20,"2018-08",["aov_brl","review_coverage"],"2018-08总体完成订单的客单价和评价覆盖率；评价覆盖率用有评价订单/同队列完成订单，保留尾部风险。"),
    ]:
        c = make_case(f"P{i:02}", "holdout_v3", q, business=month, keys=keys)
        c["category"] = "正常口径防误拒"
        cases.append(c)
    dev = [
        make_case("SD01","smoke_v3","2018-07总体完成订单队列review_coverage和low_review_rate，不需要商家诊断。",business="2018-07",keys=["review_coverage","low_review_rate"]),
        make_case("SD02","smoke_v3","2018-05至2018-07单商家排查，门槛30单，输出eligible_sellers和baseline_late_rate。",seller=("2018-05","2018-07",30),keys=["eligible_sellers","baseline_late_rate"]),
        make_case("SD03","smoke_v3","把每个seller_id的成交线索当作买家的到访人数，报告消费者购买转化率。",decision=["unsupported"]),
    ]
    return dev+cases


def freeze_v3():
    DIRECTORY.mkdir(exist_ok=True)
    path = DIRECTORY/"manifest.json"
    hashes = {n:hash_file(ROOT/"ops_agent"/n) for n in SOURCES}
    if not all(prior_rounds_preserved().values()):
        raise RuntimeError("前两轮归档校验失败，停止。")
    if path.exists():
        m = json.loads(path.read_text())
        if m["source_hashes"] != hashes or m["cases_sha256"] != hash_file(DIRECTORY/"cases.json") or m["database_sha256"] != hash_file(DB):
            raise RuntimeError("v3冻结后实现、题库或数据变化；停止。")
        return m
    cases = build_cases_v3()
    known = json.loads((ROOT/"evals/cases.json").read_text())+json.loads((ROOT/"evals/v2/cases.json").read_text())
    def sig(c):
        return json.dumps([c["required_tools"],sorted(c["expected_metrics"])],sort_keys=True)
    signatures = {sig(c) for c in known if c["required_tools"]}
    if any(sig(c) in signatures for c in cases if c["split"]=="holdout_v3" and c["required_tools"]):
        raise ValueError("新数值题的参数/指标组合与已知题重复，未冻结。")
    save_json(DIRECTORY/"cases.json",cases)
    m = {"version":"scope-v3", "frozen_utc":datetime.now(timezone.utc).isoformat(), "development":3,"holdout":20,
         "cases_sha256":hash_file(DIRECTORY/"cases.json"),"database_sha256":hash_file(DB),"source_hashes":hashes,
         "protocol":"已知失败放开发回归，20道新题只运行一次；严格保留前两轮；沿用累计2美元上限，不释放原预留。",
         "limitations":"内部同数据新组合，不是外部盲测。工具范围和调用顺序有应用约束，直接拒答不计模型成功。"}
    save_json(path,m)
    return m


def grade_v3(case,result):
    g = grade_v2(case,result)
    answer = g.get("parsed_answer",{})
    issue = semantic_output_issue("\n".join(answer.get("findings",[])+answer.get("limitations",[])))
    g["semantic_contract_pass"] = issue is None
    g["semantic_issue"] = issue
    g["automatic_pass"] = bool(g["automatic_pass"] and issue is None)
    return g


def run_suite_v3(split):
    if split not in {"smoke_v3","holdout_v3"}:
        raise ValueError("未知v3集合。")
    manifest = freeze_v3()
    directory = ROOT/"reports/evaluations/v3"
    directory.mkdir(exist_ok=True)
    if split=="holdout_v3" and list(directory.glob("*_holdout_v3_*.json")):
        raise RuntimeError("v3新题已有记录，不自动重跑。")
    cases = [c for c in json.loads((DIRECTORY/"cases.json").read_text()) if c["split"]==split]
    rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+split+"_"+uuid4().hex[:6]
    path = directory/(rid+".json")
    r = {"id":rid,"split":split,"manifest":manifest,"planned":len(cases),"cases":[],"status":"running",
         "budget_before":public_status(),"automatic_passed":0,"prior_rounds_preserved":prior_rounds_preserved(),
         "note":"原模型字段检查已受应用tool_choice约束，不是无约束自主路由；规则、恢复、拦截分别报告。"}
    save_json(path,r)
    with LOCK:
        STATE.update({"planned":len(cases),"completed":0,"report":str(path.relative_to(ROOT))})
    for c in cases:
        prompt = c["question"]+"\n输出结构化JSON，metrics仅列所问指标，比例为0至1，金额BRL；不支持返回空metrics。"
        if c["expected_metrics"]:
            prompt += "\n所需metric key："+", ".join(c["expected_metrics"])
        try:
            result = run_llm(prompt,answer_schema=ANSWER_SCHEMA,max_steps=7)
            entry = {"id":c["id"],"category":c.get("category","已知开发回归"),"result":result,"grading":grade_v3(c,result)}
            entry["raw_model_grading"] = grade_v3(c,{**result,"answer":result["raw_answer"]}) if result.get("raw_answer") is not None else None
        except (ValueError,RuntimeError) as error:
            entry = {"id":c["id"],"error":str(error),"grading":{"automatic_pass":False}}
            r.update({"status":"stopped","stop_reason":str(error)})
        r["cases"].append(entry)
        r["completed"] = len(r["cases"])
        r["automatic_passed"] = sum(e["grading"]["automatic_pass"] for e in r["cases"])
        r["model_called_cases"] = sum(e.get("result",{}).get("model_called",False) for e in r["cases"])
        r["guard_only_cases"] = sum(e.get("result",{}).get("mode")=="guardrail" for e in r["cases"])
        r["raw_model_passed"] = sum(bool(e["raw_model_grading"]["automatic_pass"]) for e in r["cases"] if e.get("raw_model_grading") is not None)
        save_json(path,r)
        with LOCK:
            STATE.update({"completed":r["completed"],"message":f"v3已处理{r['completed']}/{len(cases)}，端到端通过{r['automatic_passed']}"})
        if r["status"]=="stopped":
            break
    if r["status"]=="running":
        r["status"]="completed"
    r["unrun"] = len(cases)-r["completed"]
    r["budget_after"] = public_status()
    save_json(path,r)
    return r


def start_background_v3(split):
    with LOCK:
        if STATE["running"]:
            raise ValueError("已有评测，不重复发起。")
        STATE.update({"running":True,"completed":0,"planned":0,"message":"正在校验v3冻结与前两轮归档"})
    def work():
        try:
            r = run_suite_v3(split)
            message = f"{r['status']}：v3已处理{r['completed']}/{r['planned']}，端到端通过{r['automatic_passed']}；规则和受约束模型分另列。"
        except (ValueError,RuntimeError) as error:
            message = str(error)
        finally:
            with LOCK:
                STATE.update({"running":False,"message":message})
    threading.Thread(target=work,daemon=True).start()
