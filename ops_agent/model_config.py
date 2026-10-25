"""Loopback session secrets and persistent, conservative API-budget reservations.

No key is persisted, returned to HTML, placed in a URL, or included in a log.
The local ledger stores only limits/counters, never a credential.
"""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import threading

from .prepare import ROOT

MODEL = "gpt-5-mini"
RATES = {MODEL: (0.25, 2.0)}  # USD per million tokens; verified official model card 2026-10-25.
LOCAL = ROOT / ".local"
_session = {}
_mutex = threading.Lock()


def credentials():
    with _mutex:
        return (_session.get("key") or os.getenv("OPENAI_API_KEY"),
                _session.get("model") or os.getenv("OPENAI_MODEL"))


@contextmanager
def ledger(path=None):
    directory = Path(path) if path is not None else LOCAL
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = directory / "budget.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        target = directory / "budget.json"
        current = json.loads(target.read_text()) if target.exists() else {"limit_usd": 0, "reserved_usd": 0, "known_actual_usd": 0, "requests": 0}
        try:
            yield current
        finally:
            temp = directory / "budget.pending"
            fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(current, output)
            temp.replace(target)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def configure(key, model=MODEL, limit=2.0, allow_paid=False):
    if not isinstance(key, str) or not key.startswith("sk-") or not 20 <= len(key.strip()) <= 400:
        raise ValueError("密钥格式不正确；请在官方平台复制完整API密钥。不会在页面回显。")
    if model not in RATES:
        raise ValueError("当前仅开放已核对价格的gpt-5-mini，避免使用未知价格模型。")
    if limit is not None:
        limit = float(limit)
        if not math.isfinite(limit) or not 0 <= limit <= 2:
            raise ValueError("本轮预算请选择0至2美元。")
        if not allow_paid:
            limit = 0.0
        with ledger() as budget:
            if limit and limit < budget["reserved_usd"]:
                raise ValueError("新预算低于已预留消耗，不能重置已用额度。")
            budget["limit_usd"] = limit
    with _mutex:
        _session.update({"key": key.strip(), "model": model})


def public_status():
    key, model = credentials()
    with ledger() as budget:
        return {"configured": bool(key and model), "model": model or MODEL,
                "limit_usd": budget["limit_usd"], "reserved_usd": budget["reserved_usd"],
                "known_actual_usd": budget["known_actual_usd"], "requests": budget["requests"]}


def reserve_request(payload, path=None):
    model = payload.get("model")
    if model not in RATES:
        raise RuntimeError("模型尚无已核对价格，停止付费调用。")
    input_rate, output_rate = RATES[model]
    # UTF-8 JSON bytes + ample protocol allowance conservatively bound text input.
    # No images/audio/hosted paid tools are used. Cache discounts are ignored.
    input_upper = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 4096
    output_upper = payload.get("max_output_tokens", 2000)
    upper_usd = math.ceil((input_upper * input_rate + output_upper * output_rate) / 1_000_000 * 1e6) / 1e6
    with ledger(path) as budget:
        if budget["limit_usd"] <= 0:
            raise RuntimeError("尚未授权付费调用。请在模型设置中确认预算。")
        if budget["reserved_usd"] + upper_usd > budget["limit_usd"]:
            raise RuntimeError("达到本轮保守预算预留上限，已在发起下一次请求前停止。")
        budget["reserved_usd"] = round(budget["reserved_usd"] + upper_usd, 6)
        budget["requests"] += 1
    return upper_usd


def record_usage(model, usage, path=None):
    input_rate, output_rate = RATES[model]
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        return None
    # Deliberately do not apply cache discounts: reported estimate remains conservative.
    actual_estimate = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    with ledger(path) as budget:
        budget["known_actual_usd"] = round(budget["known_actual_usd"] + actual_estimate, 8)
    return actual_estimate
