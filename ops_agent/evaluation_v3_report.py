"""Offline v3 report: constrained model, application guards and raw drafts apart."""
from __future__ import annotations

import html
import json
import statistics
import sys
from pathlib import Path

from .prepare import ROOT, save_json
from .evaluation_v3 import prior_rounds_preserved


def summarize(path):
    path = Path(path).resolve()
    run = json.loads(path.read_text())
    if run["status"] == "running":
        raise ValueError("尚未运行结束。")
    cases = {c["id"]:c for c in json.loads((ROOT/"evals/v3/cases.json").read_text())}
    notes_path = path.with_suffix(".review-notes.json")
    notes = json.loads(notes_path.read_text()) if notes_path.exists() else {}
    rows, usage, times = [], [], []
    for e in run["cases"]:
        c = cases[e["id"]]
        result = e.get("result",{})
        raw = e.get("raw_model_grading")
        rows.append({"id":e["id"],"category":c.get("category","已知开发回归"),"question":c["question"],
                     "expected_metric_keys":list(c["expected_metrics"]),"grading":e["grading"],
                     "raw_model_grading":raw,"delivery_pass":e["grading"]["automatic_pass"],
                     "constrained_raw_pass":raw["automatic_pass"] if raw is not None else None,
                     "model_called":result.get("model_called",False),"mode":result.get("mode"),
                     "request_contract":result.get("request_contract"),"validation":result.get("validation"),
                     "raw_answer":result.get("raw_answer"),"answer":result.get("answer"),"error":e.get("error"),
                     "coverage_recoveries":result.get("coverage_recoveries",0),
                     "blocked_tool_calls":sum(not t.get("outcome",{}).get("ok") for t in result.get("trace",[])),
                     "trace":[{"name":t["name"],"arguments":t.get("arguments"),"ok":t.get("outcome",{}).get("ok"),"error":t.get("outcome",{}).get("error")} for t in result.get("trace",[])],
                     "assistant_review":notes.get(e["id"],"自动检查仅覆盖列明字段与有限语义规则；不视为独立人工验收。")})
        usage.extend(result.get("usage",[]))
        if result.get("model_called"):
            times.append(result["elapsed_seconds"])
    metrics = [row["grading"].get("metric_checks",{}).get(k,False) for row in rows for k in row["expected_metric_keys"]]
    sources = [row["grading"].get("source_mapping_checks",{}).get(k,False) for row in rows for k in row["expected_metric_keys"]]
    ins = sum(u.get("input_tokens",0) for u in usage)
    outs = sum(u.get("output_tokens",0) for u in usage)
    groups = []
    for cat in dict.fromkeys(row["category"] for row in rows):
        selected = [r for r in rows if r["category"]==cat]
        groups.append({"category":cat,"completed":len(selected),"passed":sum(r["delivery_pass"] for r in selected),
                       "model_called":sum(r["model_called"] for r in selected),
                       "guard_only":sum(r["mode"]=="guardrail" for r in selected)})
    r = {"run_id":run["id"],"status":run["status"],"planned":run["planned"],"processed":run["completed"],"unrun":run["unrun"],
         "prior_rounds_preserved":prior_rounds_preserved(),"delivery_passed":sum(row["delivery_pass"] for row in rows),
         "model_called_cases":sum(row["model_called"] for row in rows),
         "constrained_raw_passed":sum(bool(row["constrained_raw_pass"]) for row in rows),
         "guard_only_cases":sum(row["mode"]=="guardrail" for row in rows),
         "metric_passed":sum(metrics),"metric_total":len(metrics),"source_passed":sum(sources),"source_total":len(sources),
         "blocked_tool_calls":sum(row["blocked_tool_calls"] for row in rows),
         "coverage_recoveries":sum(row["coverage_recoveries"] for row in rows),
         "requests":len(usage),"input_tokens":ins,"output_tokens":outs,"new_suite_cost_estimate_usd":ins*.25/1e6+outs*2/1e6,
         "median_model_case_seconds":statistics.median(times) if times else None,
         "budget_before":run["budget_before"],"budget_after":run["budget_after"],"categories":groups,"cases":rows,
         "assistant_review_findings":notes.get("_summary",[]),
         "limitations":["同一历史数据上的内部针对性新组合，不是外部独立盲测，不能与前两轮直接比较百分比。",
                        "模型调用先受应用tool_choice、指标范围及用户显式窗口约束，不是无约束模型自主选对工具。",
                        "错误替代口径题可由应用规则直接拒答；不得把这些题计为模型语义推理成功。",
                        "程序交付使用数值/来源校验及模板，原草稿自由叙述不自动成为已核验事实。",
                        "有限词汇、否定句和语义规则可能漏拒或误拒，未证明任意新表达、长上下文或复杂混合请求都安全。",
                        "错工具恢复通过本地故障注入测试验证；真实模型本轮若没有选错，则不能声称在线观察到了自动恢复。",
                        "费用按API返回用量、不计缓存折扣估计；所有旧预留保留，实际账单以平台为准。"]}
    save_json(ROOT/"reports/v3工具范围与口径验证.json",r)
    render(r,ROOT/"reports/v3工具范围与口径验证.html")
    return r


def render(r,path):
    esc = lambda v:html.escape(str(v))
    details = []
    for row in r["cases"]:
        model_label = "未调用模型" if row["constrained_raw_pass"] is None else "字段检查通过" if row["constrained_raw_pass"] else "未通过"
        details.append(f'''<details><summary>{esc(row['id'])} · {esc(row['category'])} · 交付{'通过' if row['delivery_pass'] else '未通过'} · {model_label}</summary>
<p>{esc(row['question'])}</p><p class="note">助手复核：{esc(row['assistant_review'])}</p>
<h3>应用请求范围与工具轨迹</h3><pre>{esc(json.dumps(row['request_contract'],ensure_ascii=False,indent=2))}</pre><pre>{esc(json.dumps(row['trace'],ensure_ascii=False,indent=2))}</pre>
<h3>最终交付</h3><pre>{esc(row['answer'] or row['error'])}</pre><h3>受范围约束的模型原文</h3><pre>{esc(row['raw_answer'])}</pre>
<h3>校验与评分</h3><pre>{esc(json.dumps(row['validation'],ensure_ascii=False,indent=2))}</pre><pre>{esc(json.dumps(row['grading'],ensure_ascii=False,indent=2))}</pre></details>''')
    b = r["budget_after"]
    groups = ''.join(f"<tr><td>{esc(g['category'])}</td><td>{g['passed']}/{g['completed']}</td><td>{g['model_called']}</td><td>{g['guard_only']}</td></tr>" for g in r["categories"])
    markup = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v3工具范围与口径验证</title>
<style>body{{margin:0;background:#f3f6fa;color:#173047;font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{max-width:1080px;margin:30px auto;padding:0 24px 50px}}section,details,.card{{background:white;border:1px solid #dce4ed;border-radius:12px;padding:22px;margin:16px 0}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.big{{font-size:32px;font-weight:700;color:#145f85}}.muted{{color:#627386}}.note{{background:#fff3dc;padding:14px;border-left:4px solid #c48b26}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;font:13px/1.65 ui-monospace,monospace;padding:14px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #e1e8ef}}summary{{font-weight:600;cursor:pointer}}@media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}</style>
<main><p class="muted">OLIST · SCOPE-V3 · 应用范围约束＋GPT-5 mini</p><h1>工具范围与错误替代口径：新题验证</h1><p>{esc(r['run_id'])}；已处理{r['processed']}/{r['planned']}，未运行{r['unrun']}。前两轮归档校验：{esc(r['prior_rounds_preserved'])}。</p>
<div class="grid"><div class="card">端到端严格检查<div class="big">{r['delivery_passed']}/{r['planned']}</div></div><div class="card">受范围约束的原模型字段检查<div class="big">{r['constrained_raw_passed']}/{r['model_called_cases']}</div></div><div class="card">规则直接拒答<div class="big">{r['guard_only_cases']}</div>不计为模型成功</div></div>
<p class="note">工具调用有应用范围约束，不是无约束模型自主路由。源指标类型检查和拒答可能在模型调用前执行。分数不能表示整体语义100%准确，也不能与不同题库直接比较“提升”。</p>
<section><h2>失败与助手复核</h2><p>以下为读取原始轨迹后的助手复核，不是独立人工验收；未改动自动分数。</p><ul>{''.join('<li>'+esc(s)+'</li>' for s in r['assistant_review_findings'])}</ul></section>
<section><h2>修复与覆盖</h2><p>总体评价由总体诊断提供，商家工具不能替换总体样本；显式月份/门槛不符时拒绝执行SQL并反馈。B2B商家线索不能替代B2C访问分母，用户确认不改变数据实体类型。自由草稿还接受有限的错误替代建议检查。</p><table><tr><th>类别</th><th>交付通过</th><th>调用模型题</th><th>规则题</th></tr>{groups}</table><p>所需指标数值{r['metric_passed']}/{r['metric_total']}，精确来源{r['source_passed']}/{r['source_total']}。本轮实际错误工具/参数拦截{r['blocked_tool_calls']}次；覆盖恢复{r['coverage_recoveries']}次。故障注入单元测试与真实调用分别统计。</p></section>
<section><h2>预算</h2><p>新题{r['requests']}次API请求，费用估计${r['new_suite_cost_estimate_usd']:.6f}；模型题中位耗时{r['median_model_case_seconds']}秒。</p><p>从首轮起累计费用估计<strong>${b['known_actual_usd']:.6f}</strong>，保守预留<strong>${b['reserved_usd']:.6f}</strong>，累计上限<strong>${b['limit_usd']:.2f}</strong>。未重置、未释放旧预留，账单以平台为准。</p></section>
<section><h2>验证边界</h2><ul>{''.join('<li>'+esc(s)+'</li>' for s in r['limitations'])}</ul></section><h2>逐题原始记录</h2>{''.join(details)}</main></html>'''
    path.write_text(markup)


if __name__=="__main__":
    r = summarize(sys.argv[1])
    print(json.dumps({k:v for k,v in r.items() if k!="cases"},ensure_ascii=False,indent=2))
