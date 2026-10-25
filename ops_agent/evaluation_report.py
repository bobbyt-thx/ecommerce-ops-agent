"""Offline review of an immutable first-run evaluation; never calls a model.

This adds reporting checks, not retroactive changes to the frozen baseline score.
"""
from __future__ import annotations

import html
import json
import math
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from .prepare import ROOT, save_json


def category(cid):
    n = int(cid[1:])
    if n <= 10:
        return "经营变化"
    if n <= 18:
        return "商家履约"
    if n <= 24:
        return "数据边界"
    if n <= 28:
        return "安全与参数约束"
    return "双场景串联"


def required_sources(case, result, key):
    """Explicit SQL provenance, stricter than merely checking an ID exists.

    Current snapshots need Q01; changes need both months, Q01 and Q02.
    Seller thresholds/rates derive from all single-seller rows (Q01), whereas
    attribution coverage is queried in Q02. No LLM judges are used here.
    """
    seller_keys = {"attributable_orders", "excluded_multi_seller_orders",
                   "eligible_sellers", "baseline_late_rate"}
    name = "diagnose_sellers" if key in seller_keys else "diagnose_business"
    required = next((t for t in case["required_tools"] if t["name"] == name), None)
    if required is None:
        return set()
    trace = next((t for t in result.get("trace", []) if t["name"] == name
                  and t.get("arguments") == required["arguments"]
                  and t.get("outcome", {}).get("ok")), None)
    if trace is None:
        return set()
    tag = trace["tool"]
    if key in {"delta_brl", "change_rate"}:
        return {f"{tag}/Q01", f"{tag}/Q02"}
    if key in {"attributable_orders", "excluded_multi_seller_orders"}:
        return {f"{tag}/Q02"}
    return {f"{tag}/Q01"}


def review(run_path):
    run_path = Path(run_path).resolve()
    run = json.loads(run_path.read_text())
    if run["status"] == "running":
        raise ValueError("运行尚未结束，不生成最终报告。")
    cases = {c["id"]: c for c in json.loads((ROOT/"evals/cases.json").read_text())}
    note_path = run_path.with_suffix(".review-notes.json")
    notes = json.loads(note_path.read_text()) if note_path.exists() else {}
    reviewed = []
    for entry in run["cases"]:
        case = cases[entry["id"]]
        result = entry.get("result", {})
        answer = entry["grading"].get("parsed_answer", {})
        values = {m["key"]: m for m in answer.get("metrics", [])}
        rows = []
        for key, expected in case["expected_metrics"].items():
            candidate = values.get(key, {})
            value = candidate.get("value")
            tol = .00005 if "rate" in key or "coverage" in key else .015
            if key.endswith("orders") or key in {"late_n", "eligible_sellers"}:
                tol = 0
            numeric_ok = type(value) in (int, float) and math.isfinite(value) and abs(value-expected) <= tol
            needed = required_sources(case, result, key)
            cited = set(re.findall(r"T\d+/Q\d+", candidate.get("evidence", "")))
            # Extra unrelated references are not counted as a precise mapping.
            source_ok = bool(needed) and cited == needed
            rows.append({"key": key, "expected": expected, "actual": value,
                         "numeric_pass": numeric_ok, "cited": sorted(cited),
                         "required_sources": sorted(needed), "source_mapping_pass": source_ok})
        reviewed.append({"id": entry["id"], "category": category(entry["id"]),
                         "question": case["question"], "automatic_pass": entry["grading"]["automatic_pass"],
                         "metrics": rows, "answer": answer, "error": entry.get("error"),
                         "elapsed_seconds": result.get("elapsed_seconds"),
                         "tool_trace": [{"tool": t["tool"], "name": t["name"], "arguments": t["arguments"]}
                                        for t in result.get("trace", [])],
                         "assistant_review": notes.get(entry["id"], {"status": "未复核"})})
    metrics = [m for c in reviewed for m in c["metrics"]]
    times = sorted(c["elapsed_seconds"] for c in reviewed if c["elapsed_seconds"] is not None)
    usages = [u for e in run["cases"] for u in e.get("result", {}).get("usage", [])]
    ins = sum(u.get("input_tokens", 0) for u in usages)
    outs = sum(u.get("output_tokens", 0) for u in usages)
    groups = []
    for label in dict.fromkeys(c["category"] for c in reviewed):
        selected = [c for c in reviewed if c["category"] == label]
        groups.append({"category": label, "completed": len(selected),
                       "automatic_passed": sum(c["automatic_pass"] for c in selected)})
    report = {"generated_utc": datetime.now(timezone.utc).isoformat(),
              "run_id": run["id"], "model": next((e["result"]["model"] for e in run["cases"] if "result" in e), None),
              "planned": run["planned"], "processed": run["completed"], "unrun": run["unrun"],
              "automatic_passed": run["automatic_passed"], "status": run["status"],
              "numeric_passed": sum(m["numeric_pass"] for m in metrics), "numeric_total": len(metrics),
              "source_mapping_passed": sum(m["source_mapping_pass"] for m in metrics),
              "metric_cases_with_precise_sources": sum(all(m["source_mapping_pass"] for m in c["metrics"])
                                                       for c in reviewed if c["metrics"]),
              "metric_cases": sum(bool(c["metrics"]) for c in reviewed),
              "latency_median_s": statistics.median(times) if times else None,
              "latency_p95_nearest_rank_s": times[math.ceil(.95*len(times))-1] if times else None,
              "input_tokens": ins, "output_tokens": outs, "api_requests": len(usages),
              "cost_estimate_usd_no_cache_discount": ins*.25/1e6 + outs*2/1e6,
              "budget_ledger_at_review": json.loads((ROOT/".local/budget.json").read_text()),
              "categories": groups, "cases": reviewed,
              "method": "冻结首轮分数不变；补充独立数字检查和逐指标SQL来源映射。助手逐题复核不等于独立人工验收。",
              "limitations": ["30题由本项目设计；不是外部独立认证或真实用户分布下的准确率。",
                              "结构化输出、所需指标key及有界工具降低了任务难度；未覆盖开放式SQL或复杂自由探索。",
                              "首3道开发题均为经营场景，未覆盖商家场景；后者由保留题首次验证。",
                              "原自动评分只检查引用ID存在，不验证其与具体指标对应；补充映射结果单独报告，未改写原分数。",
                              "历史数据描述性分析不等于验证了真实运营收益或因果效果。",
                              "费用按返回用量估算且忽略缓存折扣；平台账单为准，失败调用继续保留本地预算预留。"]}
    target = ROOT/"reports/首轮真实模型评测复核.json"
    save_json(target, report)
    render(report, ROOT/"reports/首轮真实模型评测复核.html", run_path)
    return report


def render(r, target, run_path):
    esc = lambda v: html.escape(str(v))
    group_rows = "".join(f'<tr><td>{esc(g["category"])}</td><td>{g["completed"]}</td><td>{g["automatic_passed"]}</td></tr>' for g in r["categories"])
    details = []
    for c in r["cases"]:
        metric_rows = "".join(f'<tr><td>{esc(m["key"])}</td><td>{esc(m["actual"])}</td><td>{esc(m["expected"])}</td><td>{"✓" if m["numeric_pass"] else "待修"}</td><td>{esc(", ".join(m["cited"]))}</td><td>{esc(", ".join(m["required_sources"]))}</td></tr>' for m in c["metrics"])
        note = c["assistant_review"]
        details.append(f'''<details><summary>{esc(c['id'])} · {esc(c['category'])} · 原自动检查{'通过' if c['automatic_pass'] else '未通过'} · {esc(note.get('status','未复核'))}</summary>
<p>{esc(c['question'])}</p><p class="note">助手复核：{esc(note.get('note','尚未复核，不能宣称已人工验收'))}</p>
<div class="scroll"><table><tr><th>指标</th><th>模型值</th><th>参考值</th><th>数字</th><th>所引查询</th><th>应对应查询</th></tr>{metric_rows}</table></div>
<pre>{esc(json.dumps(c['answer'] or c['error'],ensure_ascii=False,indent=2))}</pre>
<pre>{esc(json.dumps(c['tool_trace'],ensure_ascii=False,indent=2))}</pre></details>''')
    b = r["budget_ledger_at_review"]
    content = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>首轮真实模型评测复核</title><style>
body{{font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#193047;background:#f3f6fa;margin:0}}main{{max-width:1080px;margin:36px auto;padding:0 24px 60px}}h1{{font-size:30px;margin-bottom:6px}}h2{{font-size:21px;margin-top:0}}.muted{{color:#607085}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}}.card,section,details{{background:white;border:1px solid #dce4ee;border-radius:12px;padding:22px;margin-bottom:16px}}.grid .card{{margin:0}}.big{{font-size:30px;font-weight:700;color:#0d527e}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{text-align:left;border-bottom:1px solid #e4eaf1;padding:9px}}th{{background:#edf3f9}}summary{{font-weight:600;cursor:pointer}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f9fc;padding:14px;border-radius:8px;font-size:13px}}.note{{background:#fff4de;border-left:4px solid #c48618;padding:12px}}.scroll{{overflow-x:auto}}li{{margin:6px 0}}a{{color:#0d659e}}@media(max-width:720px){{.grid{{grid-template-columns:1fr}}main{{padding:0 12px}}}}
</style><main><p class="muted">OLIST · 内部冻结保留集 · {esc(r['model'])}</p><h1>首轮真实模型评测与证据复核</h1>
<p class="muted">运行 {esc(r['run_id'])}；已处理 {r['processed']}/{r['planned']}，未运行 {r['unrun']}。未重跑保留题，未回改首轮模型输出。</p>
<div class="grid"><div class="card">原自动检查通过<div class="big">{r['automatic_passed']}/{r['planned']}</div><span class="muted">不是整体回答准确率</span></div><div class="card">所问指标数值符合参考<div class="big">{r['numeric_passed']}/{r['numeric_total']}</div><span class="muted">raw表独立SQL参考值</span></div><div class="card">逐指标SQL来源精确对应<div class="big">{r['source_mapping_passed']}/{r['numeric_total']}</div><span class="muted">比“ID存在”更严格的补充检查</span></div></div>
<section><h2>如何理解结果</h2><p>工具调用与计算正确不代表每句解释、每个引用都已可靠。原评分保留不变；下方来源映射和助手复核单独披露，尚需独立人工验收。</p><table><tr><th>场景</th><th>已处理题数</th><th>原自动检查通过</th></tr>{group_rows}</table></section>
<section><h2>本轮问题与修正顺序</h2><p class="note">H19为实质边界失败：将缺失广告费按0处理并标为支持的指标。H20–H27基本正确拒答，但返回null指标行或布尔/参数建议，违反严格空指标约定；H21还进行了不必要的默认月份查询。这8题不能与H19等同看作虚构经营数字。</p><p>引用层面：应将本期指标、跨月计算及商家门槛各自绑定正确SQL来源，不能仅检查编号存在。附加解释层面：H09的AOV降幅及样本口径有误，H05、H09存在百分号遗漏。优先修复证据映射、边界校验与额外比例校验。</p><p>当前已可展示真实工具调用与评测过程；尚不宜宣称整体回答100%准确、独立验收通过或产生真实经营收益。下一轮保留首轮基线，以开发题和新冻结未见题验证修正，不把重跑本题库当作新保留集。</p></section>
<section><h2>消耗与延迟</h2><p>本次保留题：{r['api_requests']} 次API请求；输入 {r['input_tokens']:,} tokens，输出 {r['output_tokens']:,} tokens（含推理）。按用量估计 <strong>${r['cost_estimate_usd_no_cache_discount']:.6f}</strong>，未计缓存折扣。单题中位耗时 {r['latency_median_s']} 秒，P95 {r['latency_p95_nearest_rank_s']} 秒。</p>
<p>本轮累计（含3题连通和先前失败请求）：已收到用量的费用估计 <strong>${b['known_actual_usd']:.6f}</strong>；保守预留 <strong>${b['reserved_usd']:.6f}</strong>；授权上限 <strong>${b['limit_usd']:.2f}</strong>。实际账单以OpenAI平台为准。</p></section>
<section><h2>评测边界</h2><ul>{''.join('<li>'+esc(x)+'</li>' for x in r['limitations'])}</ul></section>
<h2>逐题原始输出与复核</h2>{''.join(details)}<p class="muted">冻结基线：{esc(str(run_path.relative_to(ROOT)))}。复核JSON：首轮真实模型评测复核.json。报告不包含密钥。</p></main></html>'''
    target.write_text(content)


if __name__ == "__main__":
    result = review(sys.argv[1])
    print(json.dumps({k:v for k,v in result.items() if k not in {"cases"}},ensure_ascii=False,indent=2))
