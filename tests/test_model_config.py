import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ops_agent import model_config as config
from ops_agent.views import settings_view


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(config, "LOCAL", Path(self.temp.name))
        self.patch.start()
        self.old = config._session.copy()
        config._session.clear()

    def tearDown(self):
        config._session.clear()
        config._session.update(self.old)
        self.patch.stop()
        self.temp.cleanup()

    def test_key_never_in_html_or_ledger(self):
        fake = "sk-test-only-not-a-real-key-123456789"
        config.configure(fake, allow_paid=False)
        self.assertNotIn(fake, json.dumps(config.public_status()))
        self.assertNotIn(fake, settings_view(config.public_status()))
        self.assertNotIn(fake, (Path(self.temp.name) / "budget.json").read_text())

    def test_no_paid_calls_without_permission(self):
        config.configure("sk-test-only-not-a-real-key-123456789", allow_paid=False)
        with self.assertRaises(RuntimeError):
            config.reserve_request({"model": "gpt-5-mini", "max_output_tokens": 2000})

    def test_budget_stops_before_request(self):
        config.configure("sk-test-only-not-a-real-key-123456789", limit=0.01, allow_paid=True)
        request = {"model": "gpt-5-mini", "max_output_tokens": 2000}
        config.reserve_request(request)
        with self.assertRaises(RuntimeError):
            config.reserve_request(request)

    def test_only_verified_model(self):
        with self.assertRaises(ValueError):
            config.configure("sk-test-only-not-a-real-key-123456789", model="unpriced", allow_paid=True)

    def test_reconfiguration_does_not_reset_usage(self):
        fake = "sk-test-only-not-a-real-key-123456789"
        config.configure(fake, limit=1, allow_paid=True)
        config.reserve_request({"model": "gpt-5-mini", "max_output_tokens": 2000})
        before = config.public_status()["reserved_usd"]
        config.configure(fake, limit=2, allow_paid=True)
        self.assertEqual(config.public_status()["reserved_usd"], before)

    def test_local_files_private(self):
        config.public_status()
        mode = (Path(self.temp.name) / "budget.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
