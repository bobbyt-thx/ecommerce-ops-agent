"""Dependency-free, escaped HTML views for reports and the local workbench."""
from html import escape
import json
from urllib.parse import urlencode
from uuid import uuid4

from .analysis import METRICS, RANKINGS

CSS = """
:root{color-scheme:light;--ink:#172433;--muted:#5d6b79;--line:#dce3e9;--blue:#2458b5;--bg:#f5f7fa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif}
header{border-bottom:1px solid var(--line);background:white;padding:25px max(25px,calc((100vw - 1160px)/2))}header p{margin:4px 0;color:var(--muted)}h1{font-size:26px;letter-spacing:-.4px;margin:0}h2{font-size:19px;margin:0 0 15px}h3{font-size:15px;margin:0 0 8px}
main{max-width:1200px;margin:25px auto;padding:0 20px 45px}nav{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 0}a{color:var(--blue);text-decoration:none}nav a{padding:7px 15px;border:1px solid var(--line);border-radius:7px;color:var(--ink)}nav a.active{background:#eaf0fc;border-color:#abc0e8;color:var(--blue)}
.panel{background:white;border:1px solid var(--line);border-radius:10px;padding:23px;margin:18px 0}.muted{color:var(--muted);font-size:13px}.notice{background:#fff7e7;border-left:3px solid #d49e35;padding:12px 16px;margin:18px 0;color:#6c501c}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.stat{background:white;border:1px solid var(--line);padding:18px;border-radius:9px}.stat span{display:block;font-size:13px;color:var(--muted)}.stat strong{display:block;font-size:27px;margin:6px 0}.stat small{color:var(--muted)}
.split{display:grid;grid-template-columns:1fr 1fr;gap:18px}.split .panel{margin:0}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:10px 11px;border-bottom:1px solid #e8ecf1;text-align:right;vertical-align:top}th{background:#f0f3f7;font-weight:600;white-space:nowrap}td:first-child,th:first-child{text-align:left}td:first-child{overflow-wrap:anywhere}tr:last-child td{border-bottom:0}.scroll{overflow:auto}.negative{color:#a74639}.positive{color:#28735b}.bar{height:9px;background:#c9d6ef;border-radius:3px;margin-top:5px}.checks{display:flex;gap:15px;flex-wrap:wrap;font-size:13px;color:#42634e}
form{display:flex;gap:14px;align-items:end;flex-wrap:wrap}label{font-size:13px;color:var(--muted);display:flex;flex-direction:column;gap:6px}input,select,textarea,button{font:inherit;border:1px solid #bcc8d5;border-radius:6px;padding:8px 11px;background:white;color:var(--ink)}button{background:var(--blue);color:white;border-color:var(--blue);cursor:pointer}textarea{width:100%;min-height:100px}button:disabled{opacity:.5;cursor:not-allowed}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f9;padding:14px;border-radius:7px;font:12px/1.6 ui-monospace,monospace}details{border-top:1px solid var(--line);padding:12px 0}summary{cursor:pointer}ul,ol{padding-left:21px;margin:8px 0}footer{color:var(--muted);font-size:12px;margin-top:22px}.wide{width:100%}
@media(max-width:850px){.grid{grid-template-columns:1fr 1fr}.split{grid-template-columns:1fr}h1{font-size:23px}.panel{padding:17px}}
@media print{nav,form{display:none}body{background:white}.panel,.stat{break-inside:avoid}main{margin:0}}
"""


def esc(value):
    return escape(str(value), quote=True)


def number(value, decimals=0):
    return "暂无数据" if value is None else f"{value:,.{decimals}f}"


def pct(value):
    return "暂无数据" if value is None else f"{value:.2%}"


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def panel(title, content):
    return f'<section class="panel"><h2>{esc(title)}</h2>{content}</section>'


def shell(title, content, active="", interactive=False):
    nav = ''
    if interactive:
        nav = '<nav>' + ''.join(f'<a class="{"active" if active == route else ""}" href="/{route}">{label}</a>' for route, label in [
            ("demo", "演示案例"), ("business", "经营变化"), ("seller", "商家履约"), ("audit", "数据检查"), ("agent", "问Agent"), ("settings", "模型设置"), ("evaluation", "评测档案")]) + '</nav>'
    return f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Olist经营诊断</title><style>{CSS}</style></head><body><header><h1>电商经营诊断助手</h1><p>Olist公开数据项目 · 经营变化与商家履约 · SQL证据可核对</p>{nav}</header><main>{content}<footer>数据来源：<a href="https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce">Olist官方订单数据</a>、<a href="https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist">官方商家营销数据</a>。数据由Olist提供，已作归并、聚合和指标计算；数据衍生内容按<a href="https://creativecommons.org/licenses/by-nc-sa/4.0/">CC BY-NC-SA 4.0</a>共享。<a href="https://github.com/bobbyt-thx/ecommerce-ops-agent/blob/main/THIRD_PARTY_NOTICES.md">署名、加工与权利说明</a>。非收费学习演示，无Olist认可或担保，不是生产系统。</footer></main></body></html>'


def stat(label, value, subtitle):
    return f'<div class="stat"><span>{esc(label)}</span><strong>{esc(value)}</strong><small>{esc(subtitle)}</small></div>'


def evidence_view(report):
    content = ''
    for e in report["evidence"]:
        content += '<details><summary>'+esc(e["id"]+' · '+e["label"])+'</summary><pre>'+esc(e["sql"])+'</pre><p class="muted">绑定参数：'+esc(json.dumps(e["parameters"], ensure_ascii=False))+'</p>'
        targets = {r["seller_id"] for r in report.get("sellers", [])[:3]}
        selected = [(index, row) for index, row in enumerate(e["rows"], 1) if e["id"] == "Q01" and row.get("seller_id") in targets]
        if selected:
            content += '<h3>本次优先对象对应的SQL原始结果行</h3>'+table(["结果行号"]+list(selected[0][1]), [[index]+list(row.values()) for index, row in selected])
        content += table(list(e["rows"][0]) if e["rows"] else ["结果"], [list(row.values()) for row in e["rows"][:12]])
        content += f'<p class="muted">共{len(e["rows"])}行；展示优先对象及首12行，完整结果保存在报告JSON。</p></details>'
    return panel("查询证据", content)


def business_view(report, interactive=False):
    r, p = report["current"], report["previous"]
    content = ''
    if interactive:
        content += panel("选择分析月份", f'<form action="/business"><label>与上月比较<input type="month" name="month" min="2017-02" max="2018-08" value="{esc(report["month"])}" required></label><button>运行只读分析</button><span class="muted">默认2018-07；尾部月份另作风险提示</span></form>')
    content += f'<p class="muted">{esc(report["month"])} 对比 {esc(report["previous_month"])} · 最终状态为delivered的下单月份队列 · 金额不含运费</p>'
    content += '<div class="grid">' + stat("完成订单商品金额（BRL）", number(r["item_amount_brl"], 2), f'上月 {number(p["item_amount_brl"], 2)}') + stat("金额环比", pct(report["change_rate"]), f'净变化 {number(report["delta_brl"], 2)} BRL') + stat("完成订单数", number(r["orders"]), f'上月 {number(p["orders"])}') + stat("商品客单价（BRL/单）", number(r["aov_brl"], 2), f'上月 {number(p["aov_brl"], 2)}') + '</div>'
    content += panel("本期发现", '<ul>' + ''.join(f'<li>{esc(x)}</li>' for x in report["findings"]) + '</ul>')
    d = report["decomposition"]
    content += panel("金额变化如何构成", table(["对称分解因素", "贡献（BRL）"], [["订单量变化", number(d["order_volume_brl"], 2)], ["客单价变化", number(d["aov_brl"], 2)], ["合计", number(report["delta_brl"], 2)]]) + '<p class="muted">两项贡献加总等于净变化。属于算术拆解，不是因果归因。</p>')
    content += '<div class="split">'
    labels = {"category": "品类", "state": "客户地区", "seller": "商家"}
    for dim, rows in report["contributions"].items():
        decreases = [x for x in rows if x["delta_cents"] < 0][:5]
        increases = sorted([x for x in rows if x["delta_cents"] > 0], key=lambda x: -x["delta_cents"])[:5]
        display = decreases + increases
        content += panel(labels[dim] + "变化贡献", table([labels[dim], "本期BRL", "上期BRL", "变化BRL"], [[x["dimension_value"], number(x["current_brl"], 2), number(x["previous_brl"], 2), number(x["delta_brl"], 2)] for x in display]) + '<p class="muted">各展示最多5个下降项和5个增长项；不同维度不能相加。</p>')
    content += panel("履约与评价", table(["指标", "本期", "上期"], [
        ["延迟交付率", pct(r["late_rate"]), pct(p["late_rate"])],
        ["有效送达样本", number(r["delivery_n"]), number(p["delivery_n"])],
        ["低评分率（≤2分）", pct(r["low_review_rate"]), pct(p["low_review_rate"])],
        ["评价覆盖率", pct(r["review_coverage"]), pct(p["review_coverage"])],
    ])) + '</div>'
    content += common_tail(report) + evidence_view(report)
    return shell("经营变化", content, "business", interactive)


def common_tail(report):
    checks = '<div class="checks">' + ''.join(f'<span>{esc(k)}：{"一致" if v else "未通过，停止解读"}</span>' for k, v in report["checks"].items()) + '</div>'
    return panel("下一步运营核查", '<ol>' + ''.join(f'<li>{esc(s)}</li>' for s in report["actions"]) + '</ol>' + checks) + '<aside class="notice"><strong>解读边界</strong><ul>' + ''.join(f'<li>{esc(s)}</li>' for s in report["warnings"]) + '</ul></aside>'


def seller_view(report, interactive=False):
    content = ''
    if interactive:
        options = ''.join(f'<option value="{key}"'+(' selected' if key == report.get("ranking", "late_burden") else '')+f'>{label}</option>' for key, (_, _, label) in RANKINGS.items())
        content += panel("选择观察窗口", f'<form action="/seller"><label>开始月份<input type="month" name="start" min="2017-01" max="2018-08" value="{esc(report["start_month"])}" required></label><label>结束月份<input type="month" name="end" min="2017-01" max="2018-08" value="{esc(report["end_month"])}" required></label><label>最低样本<input type="number" name="minimum" min="10" max="500" value="{report["min_orders"]}" required></label><label>排查目标<select name="ranking">{options}</select></label><button>运行只读分析</button></form>')
    c, b = report["coverage"], report["baseline"]
    content += f'<p class="muted">{esc(report["start_month"])} 至 {esc(report["end_month"])} · 仅单商家完成订单 · 每项体验指标至少{report["min_orders"]}个有效样本</p>'
    content += '<div class="grid">' + stat("可归属订单", number(c["attributable_orders"]), f'多商家排除 {number(c["multi_seller_excluded"])} 单') + stat("达到订单门槛的商家", number(report["eligible_sellers"]), f'小样本排除 {number(report["small_sample_sellers_excluded"])} 家') + stat("区间加权延迟率", pct(b["late_rate"]), "所有可归属订单作为基准") + stat("区间加权低评分率", pct(b["low_review_rate"]), "分母为实际有评价订单") + '</div>'
    content += panel("区间发现", '<ul>' + ''.join(f'<li>{esc(x)}</li>' for x in report["findings"]) + '</ul>')
    low_target = report.get("ranking", "late_burden").startswith("low_review")
    burden_key = "above_baseline_low_review_orders" if low_target else "above_baseline_late_orders"
    content += panel("优先排查清单", f'<p class="muted">按{esc(report.get("ranking_label", "超基准延迟单量"))}降序；对应样本不足不参与排名。达标商家订单覆盖率{pct(report.get("eligible_order_coverage"))}。不代表责任认定，最多展示50家。</p>' + table(
        ["商家ID", "订单", "延迟率", "有效送达", "低评分率", "有评价", "评价覆盖", "超基准低评分单量" if low_target else "超基准延迟单量"],
        [[r["seller_id"], number(r["orders"]), pct(r["late_rate"]), number(r["delivery_n"]), pct(r["low_review_rate"]), number(r["reviewed_n"]), pct(r["review_coverage"]), number(r[burden_key], 2)] for r in report["sellers"]]))
    content += common_tail(report) + evidence_view(report)
    return shell("商家履约", content, "seller", interactive)


def audit_view(audit, interactive=False):
    s, f, m = audit["special_cases"], audit["join_fanout"], audit["marketing_linkage"]
    content = '<div class="grid">' + stat("订单", number(audit["tables"]["orders"]["rows"]), "订单主键无重复") + stat("商品明细", number(audit["tables"]["items"]["rows"]), "商家+订单关联前先明确粒度") + stat("多商家订单", number(s["orders_with_multiple_sellers"]), "商家体验排名排除") + stat("可关联签约商家", f'{m["closed_sellers_with_observed_order_items"]}/{m["unique_closed_sellers"]}', "商家营销扩展暂不纳入核心") + '</div>'
    content += panel("检查结论", f'<p>数据可用于前两个场景，但不能直接拼接后求和。原始商品金额为 <strong>{number(f["raw_item_amount_brl"], 2)} BRL</strong>；直接拼接支付与评价后为 <strong>{number(f["naive_items_payments_reviews_left_join_brl"], 2)} BRL</strong>。本项目先分别归并，再按订单主键关联。</p><p>下单范围：{esc(audit["purchase_range"][0])} 至 {esc(audit["purchase_range"][1])}；最后观测送达：{esc(audit["last_observed_delivery"])}。默认采用2018-07演示，明确保留截数风险，不宣称数据已证实完整。</p>')
    content += panel("原始表检查", table(["表", "行数", "完全重复行", "候选键重复行", "含缺失的字段数"], [[name, number(t["rows"]), number(t["exact_duplicate_rows"]), number(t["duplicate_key_rows"]) if t["candidate_key"] else "非单键表", len(t["nulls"])] for name, t in audit["tables"].items()]))
    content += panel("关联覆盖", table(["来源字段", "目标字段", "非空行", "成功关联", "未匹配", "空值"], [[r["from"], r["to"], r["non_null_rows"], r["matched_rows"], r["unmatched_rows"], r["null_rows"]] for r in audit["relations"]]))
    content += panel("异常与缺失", table(["检查项", "记录数"], [[k, v] for k, v in s.items()]) + '<details><summary>各表字段缺失明细</summary><pre>' + esc(json.dumps({n: t["nulls"] for n, t in audit["tables"].items()}, ensure_ascii=False, indent=2)) + '</pre></details>')
    content += panel("逐月状态分布", table(["月份", "各状态订单数"], [[month, ', '.join(f'{k}: {v}' for k, v in counts.items() if v)] for month, counts in audit["monthly_status_counts"].items()]))
    content += panel("指标口径", table(["指标", "定义", "粒度", "单位"], [[m["name"], m["definition"], m["grain"], m["unit"]] for m in METRICS]))
    content += '<aside class="notice"><ul>' + ''.join(f'<li>{esc(s)}</li>' for s in audit["warnings"]) + '</ul></aside>'
    return shell("数据检查", content, "audit", interactive)


def agent_view(available, result=None):
    content = panel("提出业务问题", '<p>默认免费，只读SQL＋有限规则解析，不是实时大模型。<a href="/demo">先看完整演示案例</a>；窗口有歧义时会澄清，不会默认猜日期。</p><form method="post" action="/agent"><input type="hidden" name="request_id" value="'+uuid4().hex+'"><label class="wide">问题<textarea name="question" maxlength="3000" required placeholder="先检查2018-05至2018-07商家，门槛30单，再报告2018-08总体商品金额。"></textarea></label><label>运行方式<select name="mode"><option value="offline">免费本地分析（不是大模型）</option>' + ('<option value="llm">大模型工具调用（收费）</option>' if available else '') + '</select></label><button>运行分析</button></form><p class="muted">本轮不追加API费用；真实模型效果请查看原始评测档案。刷新结果页不会重新执行分析。</p>')
    if result:
        try:
            answer = json.loads(result["answer"])
        except (ValueError, TypeError):
            answer = {"findings": str(result["answer"]).splitlines(), "limitations": []}
        content += panel("结论与待核查对象", '<p class="muted">模式：' + esc(result["mode"]) + ' · '+esc(result.get("question", ""))+'</p><ul>' + ''.join('<li>'+esc(s)+'</li>' for s in answer.get("findings", [])) + '</ul>')
        plan = result.get("request_contract", {})
        if plan.get("assumptions"):
            content += '<aside class="notice">'+esc('；'.join(plan["assumptions"]))+'</aside>'
        warnings = list(dict.fromkeys(answer.get("limitations", []) + [w for r in result.get("reports", []) for w in r.get("warnings", [])]))
        critical = [w for w in warnings if "截数" in w or "成熟度" in w]
        if critical:
            content += '<aside class="notice"><strong>窗口风险：</strong>'+esc('；'.join(critical))+'</aside>'
        actions = list(dict.fromkeys(a for r in result.get("reports", []) for a in r.get("actions", [])))[:4]
        if actions:
            content += panel("下一步核查——尚未实施", '<ol>'+''.join('<li>'+esc(a)+'</li>' for a in actions)+'</ol>')
        if warnings:
            content += '<details><summary>数据口径与解读限制</summary><ul>'+''.join('<li>'+esc(w)+'</li>' for w in warnings)+'</ul></details>'
        if result.get("validation"):
            content += '<details><summary>程序校验与实体来源</summary><pre>' + esc(json.dumps(result["validation"], ensure_ascii=False, indent=2)) + '</pre></details>'
        if result.get("raw_answer"):
            content += '<details><summary>查看未经程序修正的模型原始草稿（不是已核验结论）</summary><pre>' + esc(result["raw_answer"]) + '</pre></details>'
        for report in result.get("reports", []):
            params = {"month": report["month"]} if report["scenario"] == "business" else {"start": report["start_month"], "end": report["end_month"], "minimum": report["min_orders"], "ranking": report.get("ranking", "late_burden")}
            content += panel(report["title"], '<ul>' + ''.join(f'<li>{esc(x)}</li>' for x in report.get("findings", [])) + '</ul><p><a href="/' + esc(report["scenario"]+'?'+urlencode(params)) + '">打开相同窗口和条件的完整场景</a></p>')
            content += '<details><summary>展开'+esc(report.get("tool", ""))+' SQL证据</summary>'+evidence_view(report)+'</details>'
        if result.get("trace"):
            content += '<details><summary>工具轨迹与原始JSON</summary><pre>' + esc(json.dumps([{k: t[k] for k in ["name", "arguments"]} for t in result["trace"]], ensure_ascii=False, indent=2)) + '</pre><pre>'+esc(result["answer"])+'</pre></details>'
    return shell("问Agent", content, "agent", True)


def settings_view(status):
    content = panel("连接真实大模型", '''
      <ol><li>在<a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer">OpenAI官方API平台</a>登录，创建本项目专用API密钥。</li>
      <li>确认API账户有可用额度。充值及付款由你自己操作，不需要给本项目付款信息。</li>
      <li>把密钥粘贴到下面的遮蔽输入框；不要发到聊天里。配置本身不会调用模型。</li></ol>
      <p class="muted">密钥只保留在当前本地服务的内存中，不写入文件，不回显。服务重启后需要重填。仅请求OpenAI官方API，不上传简历或求职资料。</p>
      <form method="post" action="/settings" autocomplete="off">
      <label class="wide">API密钥（不会显示或保存到磁盘）<input name="key" type="password" autocomplete="off" maxlength="400" spellcheck="false" required placeholder="仅在此处粘贴，不要发到聊天里"></label>
      <label>模型<select name="model"><option value="gpt-5-mini">GPT-5 mini（先跑低成本基线）</option></select></label>
      <label>预算变更<select name="limit"><option value="keep">保持现有累计预算（仅更换密钥）</option><option value="0">暂停授权：0美元</option><option value="0.5">0.50美元</option><option value="1">1美元</option><option value="2">2美元</option></select></label>
      <label style="flex-direction:row;align-items:center"><input type="checkbox" name="allow_paid" value="yes">我允许按所选预算发起API调用</label>
      <button>应用到当前会话</button></form>''')
    content += panel("当前状态", table(["项目", "状态"], [
        ["密钥与模型", "当前会话已配置；不据此推断认证有效" if status["configured"] else "当前会话未配置（重启后需重填）"],
        ["历史调用", "已有返回用量的历史调用；不代表当前密钥已验证" if status["known_actual_usd"] > 0 else "暂无返回用量的调用记录"],
        ["模型", status["model"]], ["预算上限（USD）", number(status["limit_usd"], 2)],
        ["保守预留消耗（USD）", number(status["reserved_usd"], 6)],
        ["已收到用量的费用估计（USD）", number(status["known_actual_usd"], 6)],
        ["已预留的请求次数", status["requests"]],
    ]) + '<p class="muted">配置状态不代表密钥有效或账户有额度。预算预留按输入字节上界及最大输出保守估计，失败或超时仍占预留额度，不自动释放；实际账单以OpenAI平台为准。改变配置不重置已用预留。</p>')
    content += '<aside class="notice">只使用普通项目API密钥，不使用管理员密钥。建议在平台限制到本项目所需权限；若使用受限密钥，需允许Responses写入。模型价格参考：输入$0.25／百万tokens、输出$2／百万tokens（2026-10-25核对），不计缓存折扣。<a href="https://developers.openai.com/api/docs/models/gpt-5-mini">官方模型说明</a>。</aside>'
    return shell("模型设置", content, "settings", True)


def archive_view(summary, budget, report_available=True):
    rows = [[r["version"], r["score"], r["note"], r["sha256"]] for r in summary]
    content = panel("历史真实模型评测：只读档案", '<p>当前为v5本地修复版，不追加API费用。下列分数属于各自冻结实现，不能移作v5模型成绩，也不能横向解释为同题准确率提升。</p>' + table(["版本", "内部检查通过", "说明", "原结果SHA256"], rows))
    historical_link = '<a href="/evaluation/report">查看v4历史复核报告</a> · ' if report_available else '公共展示包仅含摘要与指纹，原始轨迹保留于本机。'
    content += panel("当前验证边界", '<ul><li>v5仅有本地SQL、回归与mock协议验证；尚未做新一轮真实模型验证。</li><li>规则直接拒答不计作模型成功；技术测试通过不等于业务收益。</li><li>原结果和失败草稿保留，不提供重跑旧题按钮。</li></ul><p>'+historical_link+'<a href="/demo">查看免费演示案例</a></p>')
    content += panel("累计预算未重置", table(["项目", "美元"], [["上限", number(budget["limit_usd"], 2)], ["保守预留", number(budget["reserved_usd"], 6)], ["用量费用估计", number(budget["known_actual_usd"], 6)]]))
    return shell("评测档案", content, "evaluation", True)


def evaluation_view(state, status, manifest):
    disabled = ' disabled' if state["running"] or not status["configured"] or status["limit_usd"] <= 0 else ''
    content = panel("冻结题库", f'<p>开发题{manifest["development"]}道、内部保留题{manifest["holdout"]}道。参考数字由原始表独立SQL计算，不传入模型上下文。题库与数据库已保存校验值。</p><p class="muted">冻结时间：{esc(manifest["frozen_utc"])}<br>题库校验：{esc(manifest["cases_sha256"])}</p>')
    content += panel("真实调用", '<p>先做3道开发题确认认证、工具调用与输出格式，再做首轮保留题。首轮保留题不自动重跑或覆盖；看到结果后修改方案，不能再把同一套题当未见题。</p><form method="post" action="/evaluation"><button name="split" value="smoke"'+disabled+'>先做3题连通测试</button><button name="split" value="holdout"'+disabled+'>启动首轮30题保留评测</button></form><p class="muted">真实API调用计入已确认预算；没有配置或未授权付费时按钮不可用。</p>')
    content += panel("当前进展", f'<p>{esc(state["message"])}</p><p>已处理 {state["completed"]} / {state["planned"]} 题</p><p><a href="/evaluation">刷新评测进展</a></p>' + (f'<p class="muted">报告保存于项目目录：{esc(state["report"])}</p>' if state.get("report") else ''))
    content += '<aside class="notice">自动评分仅覆盖所问数字、所需工具参数和边界判断。叙述是否有据、是否过度归因仍需人工复核。API错误或预算不足会停止，未运行题数也会报告，不只统计成功题。</aside>'
    return shell("保留题评测", content, "evaluation", True)


def evaluation_view_v2(state, status, manifest):
    disabled = ' disabled' if state["running"] or not status["configured"] or status["limit_usd"] <= 0 else ''
    content = panel("v2修复与新题", '<p>字段级SQL证据映射、数值校验、统一拒答格式、缺失数据前置边界已启用。模型原文、程序改动与最终输出分别留存。</p><p class="notice">首轮结果21/30保留不变。本页是新的内部验证，含程序规则与模板，不是纯模型准确率，也不是外部独立盲测。</p>'
                    + f'<p>3道开发验通＋30道新参数/指标组合或新表述；冻结时间 {esc(manifest["frozen_utc"])}。</p><p class="muted">题库校验 {esc(manifest["cases_sha256"])}</p>')
    content += panel("新一轮验证", '<form method="post" action="/evaluation"><button name="split" value="smoke_v2"'+disabled+'>运行v2三题验通</button><button name="split" value="holdout_v2"'+disabled+'>运行v2首轮30道新题</button></form>'
                     + '<p class="muted">沿用累计预算，不能重置原消耗。新保留题只运行一次，不覆盖首轮结果。</p>')
    content += panel("累计预算", table(["项目", "美元"], [["累计上限", number(status["limit_usd"],2)], ["保守预留",number(status["reserved_usd"],6)], ["用量费用估计",number(status["known_actual_usd"],6)]]))
    content += panel("当前进展", f'<p>{esc(state["message"])}</p><p>已处理 {state["completed"]} / {state["planned"]} 题</p><a href="/evaluation">刷新评测进展</a>' + (f'<p class="muted">{esc(state["report"])}</p>' if state.get("report") else ''))
    content += '<aside class="notice">报告同时统计：端到端交付通过数、模型原始通过数、规则直接拒答数、引用重绑定数及拒答格式规范化数。没有调用模型的规则题不计作模型成功。叙述仍需独立人工验收。</aside>'
    return shell("v2新题验证", content, "evaluation", True)


def evaluation_view_v3(state,status,manifest):
    disabled = ' disabled' if state["running"] or not status["configured"] or status["limit_usd"]<=0 else ''
    content = panel("v3工具范围与分析对象校验", '<p>总体评价只走总体诊断，单商家排查不替代总体；错工具/错窗口先拒绝执行并反馈纠正。B2B商家线索不能替代B2C消费者访问，用户同意也不放行。</p><p class="notice">前两轮原始结果与代码归档保留。本轮使用应用tool_choice约束，不宣称无约束模型自主路由成功。</p>'+f'<p>3道已知开发回归＋20道针对性新题；冻结时间 {esc(manifest["frozen_utc"])}。</p>')
    content += panel("本轮验证", '<form method="post" action="/evaluation"><button name="split" value="smoke_v3"'+disabled+'>运行v3三题回归</button><button name="split" value="holdout_v3"'+disabled+'>运行v3首轮20道新题</button></form><p class="muted">沿用累计2美元上限，原预留不重置。新题只运行一次；预算不足停止并报告未运行数。</p>')
    content += panel("累计预算",table(["项目","美元"],[["上限",number(status["limit_usd"],2)],["保守预留",number(status["reserved_usd"],6)],["用量费用估计",number(status["known_actual_usd"],6)]]))
    content += panel("当前进展",f'<p>{esc(state["message"])}</p><p>已处理 {state["completed"]} / {state["planned"]} 题</p><a href="/evaluation">刷新评测进展</a>'+(f'<p class="muted">{esc(state["report"])}</p>' if state.get("report") else ''))
    content += '<aside class="notice">分别记录最终交付检查、受工具范围约束的模型原始检查、规则直接拒答、错误请求拦截与恢复。规则题不计为模型成功；有限语义规则不等于全面语义安全认证。</aside>'
    return shell("v3范围与口径验证",content,"evaluation",True)


def evaluation_view_v4(state,status,manifest):
    disabled=' disabled' if state["running"] or not status["configured"] or status["limit_usd"]<=0 else ''
    content=panel("v4误拒、漏拒与格式恢复",'<p>完整月份边界、覆盖率不当成写操作、统一英文标识符及中英词边界、局部语义检查。重复/格式异常最多纠正一次，首次草稿与纠正分别保存；错误数字不自动替换。</p><p class="notice">前三轮原始记录、评分及源码保留。已知5个失败在本地回归验证，新10题只运行一次；有应用规则和模板参与，不是纯模型准确率。</p>'+f'<p>冻结时间：{esc(manifest["frozen_utc"])}。</p>')
    content+=panel("本轮验证",'<form method="post" action="/evaluation"><button name="split" value="holdout_v4"'+disabled+'>运行v4首轮10道新题</button></form><p>累计上限2美元，旧预留不释放；预算不足在下一请求前停止，部分题轨迹也保留。</p>')
    content+=panel("累计预算",table(["项目","美元"],[["上限",number(status["limit_usd"],2)],["保守预留",number(status["reserved_usd"],6)],["用量费用估计",number(status["known_actual_usd"],6)]]))
    content+=panel("当前进展",f'<p>{esc(state["message"])}</p><p>已完成 {state["completed"]} / {state["planned"]} 题</p><a href="/evaluation">刷新评测进展</a>'+(f'<p class="muted">{esc(state["report"])}</p>' if state.get("report") else '')+'<p><a href="/evaluation/report">查看本轮完整复核报告（生成后可用）</a></p>')
    return shell("v4修复验证",content,"evaluation",True)
