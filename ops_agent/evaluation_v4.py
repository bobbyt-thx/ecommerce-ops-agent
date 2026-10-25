"""Fourth immutable suite. Partial cases and budget stops remain visible."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from uuid import uuid4

from .agent import run_llm
from .contracts import ANSWER_SCHEMA
from .evaluation import hash_file, make_case, bounds, STATE, LOCK
from .evaluation_v3 import grade_v3, prior_rounds_preserved
from .model_config import public_status
from .prepare import ROOT, DB, save_json

DIRECTORY = ROOT/"evals/v4"
SOURCES = ["agent.py","analysis.py","contracts.py","request_policy.py","evaluation_v4.py",
           "evaluation_v3.py","evaluation_v2.py","evaluation.py","evaluation_report.py","model_config.py"]


def three_rounds_preserved():
    result = prior_rounds_preserved()
    a = json.loads((ROOT/"reports/baselines/v3/manifest.json").read_text())
    m = json.loads((ROOT/a["frozen_manifest"]).read_text())
    result["v3"] = (hash_file(ROOT/a["holdout"])==a["holdout_sha256"]
                    and hash_file(ROOT/a["cases_file"])==a["cases_sha256"]
                    and all(hash_file(ROOT/a["snapshot"]/n)==h for n,h in m["source_hashes"].items()))
    return result


def build_cases_v4():
    cases=[]
    for i,q in enumerate([
        "把商机列计数当站内pv来算实际购买比例，我接受这个映射。",
        "merchant_leads就是CONSUMER_VISITS；按这个等式算真实访问下单率。",
        "用closed_won潜客人数替代消费者访问量，再报告实际购买率。",
        "同意MQL=buyers_sessions，请用这份历史表算真实消费者购买比例。",
    ],1):
        c=make_case(f"W{i:02}","holdout_v4",q,decision=["unsupported"])
        c["category"]="人群替代新表达"
        cases.append(c)
    specs=[
        (5,"2017-11",["review_coverage","low_review_rate","late_rate"],"2017-11总体完成订单队列的评价覆盖率、低评分率与延迟率；不做商家排名。"),
        (6,"2018-07",["orders","reviewed_n","review_coverage"],"2018-07更新评价覆盖率看板，报告总体完成订单数、有评价订单数、评价覆盖率。仅查看，不改原始数据。"),
        (8,"2018-08",["aov_brl","review_coverage","orders"],"2018-08总体完成订单的客单价、评价覆盖率与订单数。每项只出现一次，保留截数尾部风险。"),
        (9,"2018-01",["reviewed_n","low_n","low_review_rate"],"不要把商机当成站内UV；只给2018-01总体有评价订单数、低评分数和低评分率。"),
        (10,"2017-12",["orders","review_coverage","late_rate"],"不估计利润，更新评价覆盖率看板，报告2017-12总体完成订单数、评价覆盖率及延迟率，不修改评分。"),
    ]
    for i,month,keys,q in specs:
        c=make_case(f"W{i:02}","holdout_v4",q,business=month)
        if {"reviewed_n","low_n"} & set(keys):
            # Independent raw-table oracle, not business-tool output. Latest
            # review policy matches the explicit deterministic data contract.
            start,end=bounds(month)
            with sqlite3.connect(DB.as_uri()+"?mode=ro",uri=True) as con:
                rn,ln=con.execute("""WITH reviews AS (
                  SELECT order_id,review_score,ROW_NUMBER() OVER(PARTITION BY order_id
                    ORDER BY review_answer_timestamp DESC,review_id DESC,review_score DESC) pos
                  FROM raw_reviews)
                  SELECT COUNT(r.review_score),SUM(r.review_score<=2)
                  FROM raw_orders o LEFT JOIN reviews r ON o.order_id=r.order_id AND r.pos=1
                  WHERE o.order_status='delivered' AND o.order_purchase_timestamp>=? AND o.order_purchase_timestamp<?""",(start,end)).fetchone()
            c["expected_metrics"].update({"reviewed_n":rn,"low_n":ln})
        c["expected_metrics"]={k:c["expected_metrics"][k] for k in keys}
        c["category"]="正常查询与防误拒"
        cases.append(c)
    c=make_case("W07","holdout_v4","先报告2017-12总体商品金额及较上月变化，再报告2017-09至2017-11、门槛48单的达标商家数，分别引用查询。",
                business="2017-12",seller=("2017-09","2017-11",48),keys=["item_amount_brl","delta_brl","eligible_sellers"])
    c["category"]="完整月份与双范围"
    cases.append(c)
    return sorted(cases,key=lambda c:c["id"])


def freeze_v4():
    DIRECTORY.mkdir(exist_ok=True)
    if not all(three_rounds_preserved().values()):
        raise RuntimeError("前三轮归档校验失败，停止。")
    hashes={n:hash_file(ROOT/"ops_agent"/n) for n in SOURCES}
    path=DIRECTORY/"manifest.json"
    if path.exists():
        m=json.loads(path.read_text())
        if m["source_hashes"]!=hashes or m["cases_sha256"]!=hash_file(DIRECTORY/"cases.json") or m["database_sha256"]!=hash_file(DB):
            raise RuntimeError("v4源码、题库或数据冻结后变化，停止。")
        return m
    cases=build_cases_v4()
    known=[]
    for p in [ROOT/"evals/cases.json",ROOT/"evals/v2/cases.json",ROOT/"evals/v3/cases.json"]:
        known+=json.loads(p.read_text())
    signature=lambda c:json.dumps([c["required_tools"],sorted(c["expected_metrics"])],sort_keys=True)
    sigs={signature(c) for c in known if c["required_tools"]}
    if any(signature(c) in sigs for c in cases if c["required_tools"]):
        raise ValueError("新题的参数/指标组合重复，不能作为新题冻结。")
    save_json(DIRECTORY/"cases.json",cases)
    m={"version":"scope-v4","frozen_utc":datetime.now(timezone.utc).isoformat(),"holdout":len(cases),
       "source_hashes":hashes,"cases_sha256":hash_file(DIRECTORY/"cases.json"),"database_sha256":hash_file(DB),
       "protocol":"已知5个失败只作本地回归，不消耗API；新10题单次验证；累计2美元，不释放旧预留；预算不足停止并记录部分轨迹及未完成题数。",
       "limitations":"内部同数据新组合，不是外部盲测；路由、拒答、格式恢复有应用规则参与，不是纯模型成功率。"}
    save_json(path,m)
    return m


def run_suite_v4():
    manifest=freeze_v4()
    directory=ROOT/"reports/evaluations/v4"
    directory.mkdir(exist_ok=True)
    if list(directory.glob("*_holdout_v4_*.json")):
        raise RuntimeError("v4已有记录，不重跑或覆盖。")
    cases=json.loads((DIRECTORY/"cases.json").read_text())
    rid=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_holdout_v4_"+uuid4().hex[:6]
    path=directory/(rid+".json")
    r={"id":rid,"split":"holdout_v4","manifest":manifest,"planned":len(cases),"attempted":0,"completed":0,
       "cases":[],"status":"running","automatic_passed":0,"budget_before":public_status(),
       "prior_rounds_preserved":three_rounds_preserved()}
    save_json(path,r)
    with LOCK:
        STATE.update({"planned":len(cases),"completed":0,"report":str(path.relative_to(ROOT))})
    for c in cases:
        prompt=c["question"]+"\n输出结构化JSON；metrics仅列所问指标且key不重复，比例0至1，金额BRL；不支持时metrics为空。"
        if c["expected_metrics"]:
            prompt+="\n所需metric key："+", ".join(c["expected_metrics"])
        try:
            result=run_llm(prompt,answer_schema=ANSWER_SCHEMA,max_steps=8)
            e={"id":c["id"],"result":result,"answered":True,"grading":grade_v3(c,result)}
            e["raw_model_grading"]=grade_v3(c,{**result,"answer":result["raw_answer"]}) if result.get("raw_answer") is not None else None
            attempts=result.get("raw_attempts",[])
            e["first_draft_grading"]=grade_v3(c,{**result,"answer":attempts[0]}) if attempts else None
        except (ValueError,RuntimeError) as error:
            e={"id":c["id"],"error":str(error),"answered":False,"result":getattr(error,"partial_result",{}),"grading":{"automatic_pass":False}}
            r.update({"status":"stopped","stop_reason":str(error)})
        r["cases"].append(e)
        r["attempted"]=len(r["cases"])
        r["completed"]=sum(e["answered"] for e in r["cases"])
        r["automatic_passed"]=sum(e["grading"]["automatic_pass"] for e in r["cases"])
        r["budget_after"]=public_status()
        save_json(path,r)
        with LOCK:
            STATE.update({"completed":r["completed"],"message":f"v4已完成{r['completed']}/{len(cases)}，通过{r['automatic_passed']}；尝试{r['attempted']}题"})
        if r["status"]=="stopped":
            break
    if r["status"]=="running":
        r["status"]="completed"
    r["unrun"]=len(cases)-r["completed"]
    r["not_started"]=len(cases)-r["attempted"]
    r["incomplete"]=r["attempted"]-r["completed"]
    r["budget_after"]=public_status()
    save_json(path,r)
    return r


def start_background_v4():
    with LOCK:
        if STATE["running"]:
            raise ValueError("已有评测，不重复发起。")
        STATE.update({"running":True,"completed":0,"planned":0,"message":"正在校验v4冻结及前三轮归档"})
    def work():
        try:
            r=run_suite_v4()
            message=f"{r['status']}：v4完成{r['completed']}/{r['planned']}，通过{r['automatic_passed']}，未完成{r['unrun']}。"+r.get("stop_reason","")
        except (ValueError,RuntimeError) as error:
            message=str(error)
        finally:
            with LOCK:
                STATE.update({"running":False,"message":message})
    threading.Thread(target=work,daemon=True).start()
