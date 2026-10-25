"""Offline fourth-round summary, retaining failures, partial work and first drafts."""
import html
import json
import sys
from pathlib import Path

from .prepare import ROOT, save_json
from .evaluation_v4 import three_rounds_preserved


def summarize(path):
    run=json.loads(Path(path).read_text())
    if run["status"]=="running":
        raise ValueError("评测仍在运行。")
    definitions=json.loads((ROOT/"evals/v4/cases.json").read_text())
    entries={e["id"]:e for e in run["cases"]}
    notes_path=Path(path).with_suffix(".review-notes.json")
    notes=json.loads(notes_path.read_text()) if notes_path.exists() else {}
    rows=[]
    for c in definitions:
        e=entries.get(c["id"],{})
        rows.append({**c,"attempted":bool(e),"answered":e.get("answered",False),
                     "grading":e.get("grading",{}),"raw_model_grading":e.get("raw_model_grading"),
                     "first_draft_grading":e.get("first_draft_grading"),"result":e.get("result",{}),
                     "error":e.get("error"),"assistant_review":notes.get(c["id"],"未单独复核；自动字段检查不是完整语义验收。")})
    models=[r for r in rows if r["answered"] and r["result"].get("model_called")]
    guards=[r for r in rows if r["answered"] and r["result"].get("mode")=="guardrail"]
    metrics=[r["grading"].get("metric_checks",{}).get(k,False) for r in rows for k in r["expected_metrics"]]
    sources=[r["grading"].get("source_mapping_checks",{}).get(k,False) for r in rows for k in r["expected_metrics"]]
    b0,b1=run["budget_before"],run["budget_after"]
    report={"run_id":run["id"],"status":run["status"],"planned":run["planned"],"attempted":run["attempted"],
            "completed":run["completed"],"unrun":run["unrun"],"not_started":run["not_started"],"incomplete":run["incomplete"],
            "stop_reason":run.get("stop_reason"),"delivery_passed":run["automatic_passed"],
            "model_answered":len(models),"model_final_draft_passed":sum(r["raw_model_grading"]["automatic_pass"] for r in models),
            "model_first_draft_passed":sum(r["first_draft_grading"]["automatic_pass"] for r in models),
            "guard_answered":len(guards),"guard_passed":sum(r["grading"]["automatic_pass"] for r in guards),
            "metrics_passed":sum(metrics),"metrics_planned":len(metrics),"sources_passed":sum(sources),
            "format_recoveries":sum(r["result"].get("format_recoveries",0) for r in rows),
            "reserved_requests_added":b1["requests"]-b0["requests"],
            "new_actual_estimate_usd":round(b1["known_actual_usd"]-b0["known_actual_usd"],8),
            "budget_before":b0,"budget_after":b1,"prior_rounds_preserved":three_rounds_preserved(),
            "assistant_summary":notes.get("_summary",[]),"cases":rows,
            "limitations":["新题为同一历史公开数据上的内部针对性组合，非外部盲测，不能与前三轮不同题库分数直接比较。",
                           "工具范围、直接拒答、格式纠正及最终模板有应用规则参与，规则题不能计为模型成功。",
                           "最多一次格式纠正可能收费；预算不足会停止，首次草稿与未完成轨迹均保留，不自动替换错误数字。",
                           "预算预留未释放且不重置；估计费用不计缓存优惠，实际账单以平台为准。",
                           "词汇和局部否定规则仍有限，不能证明任意表述或复杂混合请求可靠。助手复核不是独立人工验收。"]}
    save_json(ROOT/"reports/v4修复与预算内验证.json",report)
    esc=lambda s:html.escape(str(s))
    details=[]
    for row in rows:
        label="未开始" if not row["attempted"] else "未完成" if not row["answered"] else "通过" if row["grading"].get("automatic_pass") else "未通过"
        details.append(f'<details><summary>{esc(row["id"])} · {esc(row["category"])} · {label}</summary><p>{esc(row["question"])}</p><p>{esc(row["assistant_review"])}</p><pre>{esc(json.dumps(row,ensure_ascii=False,indent=2))}</pre></details>')
    markup=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v4修复与预算内验证</title><style>body{{margin:0;background:#f3f6fa;color:#19354b;font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{max-width:1000px;margin:30px auto;padding:0 25px 50px}}section,details{{padding:22px;background:white;border:1px solid #dbe4ec;border-radius:12px;margin:16px 0}}h1{{font-size:30px}}.notice{{background:#fff3dd}}.big{{font-size:24px;font-weight:600}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:16px;font:12px/1.65 ui-monospace,monospace}}summary{{font-weight:600;cursor:pointer}}</style><main>
<p>OLIST · SCOPE-V4 · GPT-5 mini＋应用约束</p><h1>误拒、漏拒与格式恢复：预算内新题验证</h1><p>{esc(report['run_id'])}</p>
<section><p class="big">严格交付通过 {report['delivery_passed']}/{report['planned']}</p><p>完成{report['completed']}，未完成{report['unrun']}（其中尝试后中止{report['incomplete']}，未开始{report['not_started']}）。状态：{esc(report['status'])}。</p><p>{esc(report['stop_reason'] or '全部计划题已完成')}</p><p>完成模型题{len(models)}：首次草稿字段通过{report['model_first_draft_passed']}，最后草稿字段通过{report['model_final_draft_passed']}；规则直接处理{len(guards)}题，通过{report['guard_passed']}。规则题不计为模型成功。</p><p>所需数值{report['metrics_passed']}/{report['metrics_planned']}，来源{report['sources_passed']}/{report['metrics_planned']}；未交付/未开始仍计入计划分母。格式恢复{report['format_recoveries']}次。</p></section>
<section><h2>已修复及回归</h2><p>完整月份边界；覆盖率不视作覆盖数据；标识符规范化及中英混排边界；局部否定不掩盖后续映射提议；重复指标/JSON异常最多纠正一次，错误数字不自动替换。已知失败用本地回归，不计为新题成功。</p><p>前三轮原始结果与源码归档校验：{esc(report['prior_rounds_preserved'])}。</p></section>
<section class="notice"><h2>助手复核与限制</h2><ul>{''.join('<li>'+esc(s)+'</li>' for s in report['assistant_summary']+report['limitations'])}</ul></section>
<section><h2>累计预算</h2><p>本轮新增预留请求{report['reserved_requests_added']}次，新增费用估计${report['new_actual_estimate_usd']:.6f}。</p><p>累计费用估计${b1['known_actual_usd']:.6f}，保守预留${b1['reserved_usd']:.6f}，累计上限${b1['limit_usd']:.2f}。旧预留未释放，没有提高预算。</p></section><h2>逐题记录</h2>{''.join(details)}</main></html>'''
    (ROOT/"reports/v4修复与预算内验证.html").write_text(markup)
    return report


if __name__=="__main__":
    print(json.dumps({k:v for k,v in summarize(sys.argv[1]).items() if k!="cases"},ensure_ascii=False,indent=2))
