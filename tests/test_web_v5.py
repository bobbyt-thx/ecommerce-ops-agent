"""HTTP handler unit tests: no socket, API, key or budget changes."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import patch

from ops_agent.server import Handler, REQUESTS


class WebFlowTests(unittest.TestCase):
    def setUp(self):
        REQUESTS.clear()

    def handler(self, path="/agent", fields=None):
        h = object.__new__(Handler)
        h.path = path
        h.server = SimpleNamespace(server_port=8765, allow_paid=False)
        body = urlencode(fields or {}).encode()
        h.headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765",
                     "Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        h.wfile = io.BytesIO()
        h.response = {}
        h.send_html = lambda html, status=200: h.response.update(html=html, status=status)
        h.redirect = lambda target: h.response.update(location=target, status=303)
        h.send_error = lambda status: h.response.update(status=status)
        return h

    def test_post_redirect_and_same_nonce_does_not_repeat_analysis(self):
        fields = {"question": "2018-03经营", "mode": "offline", "request_id": "a"*32}
        with patch("ops_agent.server.run_offline", return_value={"answer": "ok"}) as run, patch("ops_agent.server.save_run", return_value=Path("20261025T000000Z_12345678.json")):
            first, repeated = self.handler(fields=fields), self.handler(fields=fields)
            first.do_POST()
            repeated.do_POST()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(first.response, repeated.response)
            self.assertEqual(first.response["status"], 303)

    def test_get_result_does_not_call_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"reports/runs").mkdir(parents=True)
            (root/"reports/runs/20261025T000000Z_12345678.json").write_text(json.dumps({"mode": "offline", "answer": "已完成", "reports": [], "trace": []}))
            with patch("ops_agent.server.ROOT", root), patch("ops_agent.server.run_offline", side_effect=AssertionError("no re-execution")):
                h = self.handler("/agent?run=20261025T000000Z_12345678")
                h.do_GET()
                self.assertEqual(h.response["status"], 200)
                self.assertIn("已完成", h.response["html"])

    def test_no_paid_mode_even_with_configured_credentials(self):
        with patch("ops_agent.server.run_llm", side_effect=AssertionError("paid calls forbidden")):
            h = self.handler(fields={"question": "2018-03经营", "mode": "llm", "request_id": "b"*32})
            h.do_POST()
            self.assertEqual(h.response["status"], 400)
            self.assertIn("禁止新增API", h.response["html"])

    def test_old_evaluation_button_cannot_run(self):
        with patch("ops_agent.server.start_background_v4", side_effect=AssertionError("no rerun")):
            h = self.handler("/evaluation", {"split": "holdout_v4"})
            h.do_POST()
            self.assertEqual(h.response["status"], 400)

    def test_bad_origin_and_missing_nonce_fail_closed(self):
        h = self.handler(fields={"question": "2018-03经营"})
        h.headers["Origin"] = "https://external.invalid"
        h.do_POST()
        self.assertEqual(h.response["status"], 403)
        h = self.handler(fields={"question": "2018-03经营"})
        h.do_POST()
        self.assertEqual(h.response["status"], 400)

    def test_result_path_cannot_escape_run_directory(self):
        h = self.handler("/agent?run=../../.local/budget")
        h.do_GET()
        self.assertEqual(h.response["status"], 400)

    def test_static_demo_has_no_forms_or_secret(self):
        from ops_agent.showcase import demo_page, historical_summary
        html = demo_page()
        self.assertNotIn("<form", html)
        self.assertIn("不调用模型", html)
        self.assertIn("953,356.25", html)
        self.assertIn("v5", html)
        self.assertEqual([r["score"] for r in historical_summary()], ["21/30", "29/30", "15/20", "10/10"])
