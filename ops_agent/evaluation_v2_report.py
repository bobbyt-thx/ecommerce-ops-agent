"""Read-only aggregation of v2 results, keeping raw-model and system scores apart."""
from __future__ import annotations

import html
import json
import statistics
import sys
from pathlib import Path

from .prepare import ROOT, save_json
from .evaluation import hash_file


def summarize(path):
    path = Path(path).resolve()
    run = json.loads(path.read_text())
    if run["status"] == "running":
        raise ValueError("尚未结束，不生成最终总结。")
    cases = {c["id"]: c for c in json.loads((ROOT/"evals/v2/cases.json").read_text())}
    notes_path = path.with_suffix(".review-notes.json")
    notes = json.loads(notes_path.read_text()) if notes_path.exists() else {}
    archive = json.loads((ROOT/"reports/baselines/v1/manifest.json").read_text())
    first = json.loads((ROOT/archive["first_holdout"]).read_text())
    preserved = (hash_file(ROOT/archive["first_holdout"]) == archive["first_holdout_sha256"]
                 and hash_file(ROOT/archive["cases_file"]) == archive["cases_sha256"]
                 and all(hash_file(ROOT/archive["snapshot"]/n) == h for n,h in first["source_hashes"].items()))
    rows, usages, latencies = [], [], []
    for entry in run["cases"]:
        result = entry.get("result", {})
        delivered = entry["grading"]
        raw = entry.get("raw_model_grading")
        audit = result.get("validation") or {}
        rows.append({"id": entry["id"], "category": cases[entry["id"]].get("category", "开发"),
                     "question": cases[entry["id"]]["question"],
                     "expected_metric_keys": list(cases[entry["id"]]["expected_metrics"]),
                     "delivery_pass": delivered["automatic_pass"],
                     "raw_model_pass": raw["automatic_pass"] if raw is not None else None,
                     "mode": result.get("mode"), "model_called": result.get("model_called", False),
                     "raw_model_answer": raw.get("parsed_answer") if raw else None,
                     "delivered_answer": delivered.get("parsed_answer"),
                     "validation": audit, "grading": delivered,
                     "raw_model_grading": raw, "error": entry.get("error"),
                     "metric_sources_rebound": sum(not c["model_sources_pass"] for c in audit.get("metric_checks", [])),
                     "refusal_normalized": any("移出metrics" in c for c in audit.get("changes", [])),
                     "assistant_review_note": notes.get(entry["id"], "仅完成列明的自动检查；原模型自由叙述不视为已独立验收。"),
                     "narrative_status": "模板事实与工具建议；原模型叙述未视为已独立验收"})
        usages.extend(result.get("usage", []))
        if result.get("model_called"):
            latencies.append(result["elapsed_seconds"])
    final_metrics = [b for row in rows for b in row["grading"].get("metric_checks", {}).values()]
    source_checks = [row["grading"].get("source_mapping_checks", {}).get(key, False)
                     for row in rows for key in row["expected_metric_keys"]]
    groups = []
    for name in dict.fromkeys(row["category"] for row in rows):
        selected = [r for r in rows if r["category"] == name]
        model_rows = [r for r in selected if r["model_called"]]
        groups.append({"category": name, "completed": len(selected), "delivery_passed": sum(r["delivery_pass"] for r in selected),
                       "model_called": len(model_rows), "raw_model_passed": sum(bool(r["raw_model_pass"]) for r in model_rows),
                       "guard_only": sum(r["mode"] == "guardrail" for r in selected)})
    ins = sum(u.get("input_tokens", 0) for u in usages)
    outs = sum(u.get("output_tokens", 0) for u in usages)
    report = {"run_id": run["id"], "status": run["status"], "planned": run["planned"],
              "processed": run["completed"], "unrun": run["unrun"], "first_run_preserved": preserved,
              "delivery_passed": sum(r["delivery_pass"] for r in rows),
              "model_called_cases": sum(r["model_called"] for r in rows),
              "raw_model_passed": sum(bool(r["raw_model_pass"]) for r in rows),
              "guard_only_cases": sum(r["mode"] == "guardrail" for r in rows),
              "metric_checks_passed": sum(final_metrics), "metric_checks_total": len(final_metrics),
              "source_checks_passed": sum(source_checks), "source_checks_total": len(source_checks),
              "metric_sources_rebound": sum(r["metric_sources_rebound"] for r in rows),
              "refusals_normalized": sum(r["refusal_normalized"] for r in rows),
              "validation_rejected": sum(bool(r["validation"].get("rejected")) for r in rows),
              "requests": len(usages), "input_tokens": ins, "output_tokens": outs,
              "new_holdout_cost_estimate_usd": ins*.25/1e6 + outs*2/1e6,
              "median_model_case_seconds": statistics.median(latencies) if latencies else None,
              "budget_before": run["budget_before"], "budget_after": run["budget_after"],
              "categories": groups, "cases": rows,
              "limitations": ["这是同一历史数据上的内部新题组合，不是外部独立盲测。",
                              "最终交付分包含前置规则、工具数值校验、程序证据绑定及模板叙述，不能称作纯模型准确率。",
                              "原始模型输出评分只针对实际调用模型的题目，规则直接拒答不计入模型成功。",
                              "原模型通过也仅覆盖所问指标、SQL来源、工具参数和边界约定，不表示其全部自由叙述都正确。",
                              "不支持题须不输出经营数值；拒答格式被程序规范化的情况单独计数。",
                              "这轮改变了系统和评分要求，且不是同一测试集合，不能直接拿百分比宣称严格同题提升。",
                              "数值校验不是业务干预收益验证；没有证明利润、投放ROI或因果效果。",
                              "有限规则可能存在误拒或漏拒，未证明任意新表述或复杂混合请求都安全。",
                              "费用根据API返回用量估计，不计缓存折扣；平台账单为准。"]}
    save_json(ROOT/"reports/v2修复与新题验证.json", report)
    render(report, ROOT/"reports/v2修复与新题验证.html")
    return report


def render(r, path):
    esc = lambda value: html.escape(str(value))
    body = []
    for row in r["cases"]:
        raw_label = "未调用模型" if row["raw_model_pass"] is None else "通过" if row["raw_model_pass"] else "未通过"
        body.append(f'''<details><summary>{esc(row['id'])} · {esc(row['category'])} · 交付{'通过' if row['delivery_pass'] else '未通过'} · 原模型{raw_label}</summary>
<p>{esc(row['question'])}</p><h3>程序校验与改动</h3><pre>{esc(json.dumps(row['validation'],ensure_ascii=False,indent=2))}</pre>
<p class="note">助手复核：{esc(row['assistant_review_note'])}</p>
<h3>交付输出</h3><pre>{esc(json.dumps(row['delivered_answer'] or row['error'],ensure_ascii=False,indent=2))}</pre>
<h3>原模型草稿（规则题没有模型原文）</h3><pre>{esc(json.dumps(row['raw_model_answer'],ensure_ascii=False,indent=2))}</pre>
<h3>严格自动检查明细</h3><pre>{esc(json.dumps(row['grading'],ensure_ascii=False,indent=2))}</pre></details>''')
    groups = ''.join(f"<tr><td>{esc(g['category'])}</td><td>{g['delivery_passed']}/{g['completed']}</td><td>{g['raw_model_passed']}/{g['model_called']}</td><td>{g['guard_only']}</td></tr>" for g in r["categories"])
    b = r["budget_after"]
    markup = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v2修复与新题验证</title>
<style>body{{font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f4f7fb;color:#183047;margin:0}}main{{max-width:1080px;margin:30px auto;padding:0 22px 50px}}section,details,.card{{background:white;border:1px solid #dce4ee;border-radius:12px;padding:22px;margin:16px 0}}h1{{font-size:30px;margin-bottom:6px}}h2{{font-size:21px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.big{{font-size:32px;color:#135a85;font-weight:700}}.muted{{color:#607085}}.note{{background:#fff3db;padding:14px;border-left:4px solid #c58d25}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #e1e8ef}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.65 ui-monospace,monospace;background:#f4f7fa;padding:14px}}summary{{cursor:pointer;font-weight:600}}@media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}</style>
<main><p class="muted">OLIST · GPT-5 mini + 有界工具 + 应用校验</p><h1>v2修复与新题验证</h1><p>{esc(r['run_id'])}；已处理{r['processed']}/{r['planned']}，未运行{r['unrun']}。首轮原始结果保留校验：{r['first_run_preserved']}。</p>
<div class="grid"><div class="card">最终交付检查<div class="big">{r['delivery_passed']}/{r['planned']}</div></div><div class="card">实际调用模型题的原始检查<div class="big">{r['raw_model_passed']}/{r['model_called_cases']}</div></div><div class="card">规则直接拒答<div class="big">{r['guard_only_cases']}</div>未计作模型成功</div></div>
<p class="note">交付分不等于纯模型准确率：包含程序证据绑定、拒答规范化和模板事实；模型原文及修正均保留。本题库不同于首轮，不能当作严格同题前后对照。</p>
<section><h2>仍需改进</h2><p>N08选错工具，把总体队列问题送入商家诊断，随后拒答，未完成任务。开发验通VD03虽正确拒答，却多查默认月份，严格检查也未通过；未重跑或回改。</p><p>N23原模型字段自动检查通过，但语义复核发现它提出将商家线索当消费者到访量，并询问用户确认，仍是不正确的替代口径。最终程序交付没有展示这段错误建议或输出转化数值。这个例子说明：原模型自动通过数不等于语义可靠数，应用限制也未证明任意问法都安全。</p><p>原模型还会在自由叙述中混淆完成订单与有效送达样本（例如N03、N05、N07、N10）；当前交付改用字段公式和程序模板，不能把原草稿包装成完整验收通过。</p></section>
<section><h2>覆盖与检查</h2><table><tr><th>类别</th><th>最终交付</th><th>原模型/实际调用</th><th>规则直接拒答</th></tr>{groups}</table><p>交付数值检查{r['metric_checks_passed']}/{r['metric_checks_total']}；SQL精确来源检查{r['source_checks_passed']}/{r['source_checks_total']}。程序重绑定引用{r['metric_sources_rebound']}个；规范化拒答{r['refusals_normalized']}题；校验拦截错误草稿{r['validation_rejected']}题。</p></section>
<section><h2>预算与调用</h2><p>新30题共{r['requests']}次API请求，输入{r['input_tokens']:,}、输出{r['output_tokens']:,} tokens，费用估计${r['new_holdout_cost_estimate_usd']:.6f}。模型题耗时中位{r['median_model_case_seconds']}秒。</p><p>从首轮开始累计费用估计${b['known_actual_usd']:.6f}；保守预留${b['reserved_usd']:.6f}；累计授权上限${b['limit_usd']:.2f}。失败预留未重置，平台账单为准。</p></section>
<section><h2>局限</h2><ul>{''.join('<li>'+esc(v)+'</li>' for v in r['limitations'])}</ul></section><h2>逐题原文、交付与改动</h2>{''.join(body)}</main></html>'''
    path.write_text(markup)


if __name__ == "__main__":
    result = summarize(sys.argv[1])
    print(json.dumps({k:v for k,v in result.items() if k != "cases"}, ensure_ascii=False, indent=2))
