"""Loopback-only workbench; no external publishing or browser-exposed API keys."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import json
import re
import threading

from .analysis import business_diagnosis, seller_diagnosis
from .agent import run_offline, run_llm, save_run, provider_available
from .prepare import ROOT
from .views import shell, panel, esc, business_view, seller_view, audit_view, agent_view, settings_view, evaluation_view
from .model_config import configure, public_status
from .evaluation import freeze, public_eval_status, start_background
from .evaluation_v2 import freeze_v2, start_background_v2
from .views import evaluation_view_v2
from .evaluation_v3 import freeze_v3, start_background_v3
from .views import evaluation_view_v3
from .evaluation_v4 import freeze_v4, start_background_v4
from .views import evaluation_view_v4
from .views import archive_view

REQUEST_LOCK = threading.Lock()
REQUESTS = {}


class Handler(BaseHTTPRequestHandler):
    def send_html(self, html, status=200):
        encoded = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(encoded)

    def safe_host(self):
        return self.headers.get("Host") in [f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"]

    def do_GET(self):
        if not self.safe_host():
            self.send_error(403)
            return
        parsed = urlparse(self.path)
        args = parse_qs(parsed.query)
        value = lambda key, default: args.get(key, [default])[0]
        try:
            if parsed.path in ["/", "/business"]:
                html = business_view(business_diagnosis(value("month", "2018-07")), True)
            elif parsed.path == "/seller":
                html = seller_view(seller_diagnosis(value("start", "2018-05"), value("end", "2018-07"), int(value("minimum", "30")), ranking=value("ranking", "late_burden")), True)
            elif parsed.path == "/audit":
                html = audit_view(json.loads((ROOT / "reports/data_quality.json").read_text()), True)
            elif parsed.path == "/agent":
                run = value("run", "")
                if run and not re.fullmatch(r"\d{8}T\d{6}Z_[a-f0-9]{8}", run):
                    raise ValueError("无效运行记录编号。")
                result = json.loads((ROOT/"reports/runs"/(run+".json")).read_text()) if run else None
                html = agent_view(self.server.allow_paid and provider_available(), result)
            elif parsed.path == "/demo":
                from .showcase import demo_page
                html = demo_page(interactive=True)
            elif parsed.path == "/settings":
                html = settings_view(public_status())
            elif parsed.path == "/evaluation":
                from .showcase import historical_summary
                html = archive_view(historical_summary(), public_status(), report_available=(ROOT/"reports/v4修复与预算内验证.html").exists())
            elif parsed.path == "/evaluation/report" and (ROOT/"reports/v4修复与预算内验证.html").exists():
                html = (ROOT/"reports/v4修复与预算内验证.html").read_text()
            else:
                self.send_error(404)
                return
            self.send_html(html)
        except (ValueError, RuntimeError, FileNotFoundError) as error:
            self.send_html(shell("无法分析", panel("请调整输入", f'<p>{esc(error)}</p>'), interactive=True), 400)

    def do_POST(self):
        allowed_origins = [f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"]
        if not self.safe_host() or self.headers.get("Origin") not in allowed_origins or self.path not in ["/agent", "/settings", "/evaluation"]:
            self.send_error(403)
            return
        if not self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            self.send_error(415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 20000:
                raise ValueError("请求长度不合法。")
            data = parse_qs(self.rfile.read(length).decode())
            if self.path == "/evaluation":
                raise ValueError("历史评测已归档，不允许重跑；v5本轮不追加API费用。")
            if self.path == "/settings":
                limit = data.get("limit", ["0"])[0]
                if limit not in ["keep", "0", "0.5", "1", "2"]:
                    raise ValueError("请从给定预算选项中选择。")
                configure(data.get("key", [""])[0].strip(), data.get("model", ["gpt-5-mini"])[0],
                          None if limit == "keep" else float(limit), data.get("allow_paid", [""])[0] == "yes")
                # POST/redirect/GET prevents redisplaying the key or retaining it in generated HTML.
                self.send_response(303)
                self.send_header("Location", "/settings?saved=1")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            question = data.get("question", [""])[0]
            mode = data.get("mode", ["offline"])[0]
            if mode not in ["offline", "llm"]:
                raise ValueError("未知运行模式。")
            if mode == "llm" and not self.server.allow_paid:
                raise ValueError("当前服务禁止新增API调用；仅可免费本地分析或查看历史档案。")
            request_id = data.get("request_id", [""])[0]
            if not re.fullmatch(r"[a-f0-9]{32}", request_id):
                raise ValueError("请求编号缺失，请重新打开分析页。")
            with REQUEST_LOCK:
                if request_id in REQUESTS:
                    target = REQUESTS[request_id]
                    if not target:
                        raise ValueError("该请求已接收或执行失败，不会重复调用；请查看原结果页或重新打开表单。")
                    self.redirect(target)
                    return
                if len(REQUESTS) >= 4096:
                    raise ValueError("会话请求数已达上限，请重新启动服务。")
                REQUESTS[request_id] = None
            result = (run_llm if mode == "llm" else run_offline)(question)
            path = save_run(result)
            target = "/agent?run="+path.stem
            with REQUEST_LOCK:
                REQUESTS[request_id] = target
            self.redirect(target)
        except (ValueError, RuntimeError) as error:
            self.send_html(shell("无法分析", panel("未生成结果", f'<p>{esc(error)}</p>'), interactive=True), 400)

    def log_message(self, fmt, *args):
        # Avoid persisting user questions or environment configuration in HTTP access logs.
        pass

    def redirect(self, target):
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()


def serve(port, allow_paid=False):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.allow_paid = allow_paid
    print(f"本地工作台：http://127.0.0.1:{port}/demo（只读；新增API调用{'启用' if allow_paid else '禁用'}；Ctrl+C停止）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
