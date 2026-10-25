"""No-key, no-network portfolio demonstration over verified historical data."""
from functools import lru_cache
import hashlib
import json

from .analysis import business_diagnosis, seller_diagnosis
from .agent import run_offline
from .prepare import ROOT, save_json
from .views import shell, panel, stat, number, pct, table, esc, business_view, seller_view

HISTORIES = [
    ("v1", "reports/evaluations/20261025T185241Z_holdout_21cb57.json", "21/30", "内部首轮；含真实边界失败，未覆盖"),
    ("v2", "reports/evaluations/v2/20261025T191327Z_holdout_v2_e0eb7e.json", "29/30", "25道调用模型、5道规则拒答；不同题库"),
    ("v3", "reports/evaluations/v3/20261025T202428Z_holdout_v3_92fdf5.json", "15/20", "14道调用模型、6道规则处理；保留失败"),
    ("v4", "reports/evaluations/v4/20261025T204556Z_holdout_v4_e271f2.json", "10/10", "6道调用模型、4道规则拒答；不是全文100%正确"),
]


def historical_summary():
    if not (ROOT/"reports/baselines/v4/manifest.json").exists():
        # Public portfolio package carries summaries, not 57MB original traces.
        summary = json.loads((ROOT/"docs/evaluation-summary.json").read_text())
        return summary["rounds"]
    archive = json.loads((ROOT/"reports/baselines/v4/manifest.json").read_text())
    rows = []
    for version, path, score, note in HISTORIES:
        target = ROOT/path
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        if sha != archive["preserved_results"][path]:
            raise RuntimeError("历史评测校验失败，不展示分数。")
        rows.append({"version": version, "path": path, "score": score, "note": note, "sha256": sha})
    return rows


@lru_cache(maxsize=1)
def demo_data():
    business = business_diagnosis("2018-03")
    seller = seller_diagnosis("2018-03", "2018-03", 30)
    late = seller_diagnosis("2018-05", "2018-07", 30, ranking="late_burden")
    reviews = seller_diagnosis("2018-05", "2018-07", 30, ranking="low_review_rate")
    guard = run_offline("把商家线索作为消费者访问，计算真实购买转化率")
    return business, seller, late, reviews, guard


def _seller_rows(report):
    return [[r["seller_id"], r["orders"], pct(r["late_rate"]), pct(r["low_review_rate"]), r["delivery_n"], r["reviewed_n"]] for r in report["sellers"][:3]]


def demo_page(interactive=False):
    business, seller, late, reviews, guard = demo_data()
    current, previous = business["current"], business["previous"]
    content = panel("用AI把分析变成可核查的业务交付", '<p>电商经营诊断助手 · 个人项目 · Python / SQL / 受约束的LLM工具调用</p><p>从“金额变在哪里”到“先核查哪些对象”，展示业务口径、SQL证据和使用边界。</p><aside class="notice">本页由v5本地只读SQL生成，不调用模型，不产生API费用。不是实时AI回答，也不是已经实施的业务优化。真实模型调用证据另见历史评测。</aside><p><a href="#case-growth">① 增长与体验</a> · <a href="#case-ranking">② 按目标排查商家</a> · <a href="#case-boundary">③ 数据不足就拒答</a></p>')
    content += '<section id="case-growth">'
    content += panel("案例一：金额增长，是否说明经营更健康？", '<p>问题：2018年3月相比2月，增长来自订单量还是客单价？履约和评价是否同时改善？哪些对象值得进一步核查？</p>')
    content += '<div class="grid">'+stat("完成订单商品金额", number(current["item_amount_brl"], 2), "BRL，不等于平台收入")+stat("环比变化", pct(business["change_rate"]), "对比2018-02")+stat("延迟率", pct(current["late_rate"]), "上月 "+pct(previous["late_rate"]))+stat("低评分率", pct(current["low_review_rate"]), "上月 "+pct(previous["low_review_rate"]))+'</div>'
    content += panel("观察事实与证据", table(["问题", "SQL结果与口径"], [
        ["增长构成", f"订单量贡献{business['decomposition']['order_volume_brl']:,.2f} BRL；客单价贡献{business['decomposition']['aov_brl']:,.2f} BRL。两项之和等于{business['delta_brl']:,.2f} BRL净变化；是算术分解，不是因果归因。"],
        ["体验变化", f"延迟率{pct(previous['late_rate'])}→{pct(current['late_rate'])}；低评分率{pct(previous['low_review_rate'])}→{pct(current['low_review_rate'])}。分母分别为有效送达订单、有选定评价订单。"],
        ["证据", "经营Q01本期、Q02上期；完整报告可查看绑定参数和逐组计算结果。"],
    ])+'<p><strong>判断：</strong>不能只用金额增长评价经营质量；增长与体验风险需要分开判断。下一步核查订单结构、履约链路和目标商家，不直接归责。</p>')
    content += panel("同月优先核查对象", '<p class="muted">2018-03、至少30单；按超基准延迟单量排序。商家ID为公开数据中的匿名标识。</p>'+table(["商家", "订单", "延迟率", "低评分率", "有效送达", "有评价"], _seller_rows(seller)))
    content += panel("行动假设与验证计划——尚未实施", table(["待核查", "需要的补充证据", "后续观察"], [
        ["是否承诺时效或承运链路异常", "出库、承运、承诺时效记录；当前数据不足以认定责任", "同地区/品类延迟率、超时分布"],
        ["是否订单结构带来比例变化", "按地区、品类和订单组合做可比切分", "分组样本数、评价覆盖率、低评分率"],
        ["拟采取动作是否真的有效", "可行的小范围试点和可信对照；不是仅看前后变化", "预设指标与护栏，区分自然波动和干预效果"],
    ]))
    if interactive:
        content += '<p><a href="/business?month=2018-03">查看3月完整经营证据</a> · <a href="/seller?start=2018-03&amp;end=2018-03&amp;minimum=30&amp;ranking=late_burden">查看同月商家证据</a></p>'
    else:
        content += '<p><a href="business-march.html">查看完整经营证据</a> · <a href="sellers-march.html">查看同月商家证据</a></p>'
    content += '</section><section id="case-ranking">'
    content += panel("案例二：先确定排查目标，再选对象", '<p>同为2018-05至07、至少30单，按延迟问题规模和按低评分比例，会得到不同清单。不是先截取延迟榜前10家，再让模型从缺失候选中猜低评分榜。</p><div class="split">'+panel("延迟负担优先", table(["商家", "订单", "延迟率", "低评分率", "有效送达", "有评价"], _seller_rows(late)))+panel("低评分率优先", table(["商家", "订单", "延迟率", "低评分率", "有效送达", "有评价"], _seller_rows(reviews)))+'</div>'+f'<p>达到订单门槛{late["eligible_sellers"]}家，覆盖可归属订单{pct(late["eligible_order_coverage"])}。对应评价/送达样本不足的商家不参与该指标排序；未进榜不代表没有问题。</p>')
    content += '</section><section id="case-boundary">'
    content += panel("案例三：数据不支持的指标，不编造", '<p>请求：“把商家线索当消费者访问，计算真实购买转化率。”</p><aside class="notice">'+esc(guard["answer"])+'</aside><p>这是应用数据边界规则拒绝，未调用模型。商家营销线索是B2B，消费者访问是B2C；用户同意也不能修复分析对象不一致。</p>')
    content += '</section>'
    content += panel("AI到底负责什么？", '<p>业务任务 → 有限范围与参数约定 → LLM工具调用（可选收费） → 只读参数化SQL → 数值与SQL来源校验 → 带对象和证据的报告。</p><p>SQL负责计算；应用负责边界、校验及确定性展示；LLM在受约束范围内调用工具并生成草稿。不是任意Text-to-SQL，也没有自动实施运营动作。当前尚未证明相对BI的效率提升，不宣称因果收益。</p>')
    summary = historical_summary()
    content += panel("真实模型验证与当前版本的区别", table(["版本", "内部检查通过", "解释"], [[r["version"], r["score"], r["note"]] for r in summary])+'<p>四轮题库和应用约束不同，不能直接比较成准确率曲线。v5是审查后的本地修复，尚无新付费模型评测；不要把v4的10/10写成v5成绩。</p>')
    return shell("项目展示与演示案例", content, "demo", interactive)


def export_showcase():
    directory = ROOT/"docs/demo"
    directory.mkdir(parents=True, exist_ok=True)
    business, seller, _, reviews, _ = demo_data()
    pages = {"index.html": demo_page(), "business-march.html": business_view(business),
             "sellers-march.html": seller_view(seller), "sellers-low-review.html": seller_view(reviews)}
    for name, html in pages.items():
        (directory/name).write_text(html, encoding="utf-8")
    save_json(ROOT/"docs/evaluation-summary.json", {"type": "historical_real_calls_not_v5_model_evaluation", "note": "摘要中保留原始指纹；原始轨迹未随公共展示包分发，摘要本身不等于独立复核。", "rounds": historical_summary()})
    print("Generated free, static portfolio demonstration:", directory/"index.html")


if __name__ == "__main__":
    export_showcase()
