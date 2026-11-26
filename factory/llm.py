"""Per-task Claude model choice and per-video API cost tracking.

Models per task come from config.json -> "models" (see DEFAULTS). Every response's usage is added
to USAGE so the renderer can report an estimated cost for each video.
"""
import json
from pathlib import Path

DEFAULTS = {
    "factcheck": "claude-opus-5-5",   # research + fact-check: quality matters most
    "convert": "claude-opus-5-5",   # turn a finished draft into JSON
    "reading": "claude-opus-5-5",   # spot TTS misreadings
    "qa": "claude-opus-5-5",        # review the rendered frames
    "verify": "claude-opus-5-5",     # does this image fit the scene? (yes/no)
}
# USD per 1M tokens (input, output, cache read) and per web search
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
WEB_SEARCH_USD = 0.01
USD_JPY = 155

_cfg = None
USAGE = {}  # task -> {"model", "in", "out", "cache", "searches"}


def _config():
    global _cfg
    if _cfg is None:
        p = Path(__file__).resolve().parent.parent / "config.json"
        try:
            _cfg = json.loads(p.read_text(encoding="utf-8")).get("models", {})
        except Exception:
            _cfg = {}
    return _cfg


def model(task):
    return _config().get(task) or DEFAULTS[task]


def effort(task):
    return _config().get(f"{task}_effort") or "high"


def record(task, msg):
    """Add a response's usage (works for create/parse responses)."""
    u = getattr(msg, "usage", None)
    if u is None:
        return
    d = USAGE.setdefault(task, {"model": getattr(msg, "model", model(task)), "in": 0, "out": 0,
                                "cache": 0, "searches": 0})
    d["in"] += (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
    d["out"] += getattr(u, "output_tokens", 0) or 0
    d["cache"] += getattr(u, "cache_read_input_tokens", 0) or 0
    stu = getattr(u, "server_tool_use", None)
    if stu is not None:
        d["searches"] += getattr(stu, "web_search_requests", 0) or 0


def reset():
    USAGE.clear()


def cost_summary():
    """{"usd", "jpy", "by_task": {task: usd}} from USAGE."""
    by = {}
    for task, d in USAGE.items():
        pin, pout, pc = PRICES.get(d["model"], PRICES["claude-opus-5-5"])
        usd = d["in"] / 1e6 * pin + d["out"] / 1e6 * pout + d["cache"] / 1e6 * pc + d["searches"] * WEB_SEARCH_USD
        by[task] = round(usd, 4)
    total = sum(by.values())
    return {"usd": round(total, 4), "jpy": round(total * USD_JPY), "by_task": by,
            "tokens": {k: {kk: v[kk] for kk in ("model", "in", "out", "searches")} for k, v in USAGE.items()}}
