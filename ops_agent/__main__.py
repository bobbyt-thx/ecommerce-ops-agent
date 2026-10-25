import argparse
import json
from .analysis import business_diagnosis, seller_diagnosis
from .agent import run_offline, run_llm, save_run
from .prepare import ROOT, save_json
from .views import business_view, seller_view, audit_view


def export_reports():
    business, seller = business_diagnosis(), seller_diagnosis()
    audit = json.loads((ROOT / "reports/data_quality.json").read_text())
    for name, result, render in [("经营变化诊断", business, business_view), ("商家履约诊断", seller, seller_view), ("数据质量检查", audit, audit_view)]:
        save_json(ROOT / f"reports/{name}.json", result)
        (ROOT / f"reports/{name}.html").write_text(render(result), encoding="utf-8")
    print("已生成三份HTML报告与对应JSON证据：", ROOT / "reports")


def main():
    parser = argparse.ArgumentParser(description="Olist经营诊断：只读分析与可选模型工具调用")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export", help="生成数据检查和两个场景报告")
    serve = sub.add_parser("serve", help="启动仅本机可访问的工作台")
    serve.add_argument("--port", type=int, default=8765)
    ask = sub.add_parser("ask", help="提出业务问题；默认本地规则路由")
    ask.add_argument("question")
    ask.add_argument("--llm", action="store_true", help="使用已配置模型，会消耗API额度")
    args = parser.parse_args()
    if args.command == "export":
        export_reports()
    elif args.command == "serve":
        from .server import serve
        serve(args.port)
    else:
        result = (run_llm if args.llm else run_offline)(args.question)
        path = save_run(result)
        print(result["answer"])
        print("完整证据：", path)


if __name__ == "__main__":
    main()
