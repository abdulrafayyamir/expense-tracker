# app.py
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
import time
import hashlib
import json

from config import AGENT_API_KEY, LLM_ENABLED, LLM_PROVIDER
from db import fetch_month_budget, fetch_entries_for_month
from analytics import (
    compute_insights,
    range_bounds_utc,
    parse_yyyy_mm,
    parse_yyyy_mm_dd,
    prev_month_str,
    split_range_by_month,
    prorate_monthly_budget_for_range,
)
from llm_ai import llm_summarize

app = Flask(__name__)

# ----------------------------
# SMART CACHE (fingerprint-based)
# ----------------------------
_LLM_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

def _fingerprint(insights: Dict[str, Any]) -> str:
    core = {
        "budget_amount": insights.get("budget_amount"),
        "spent_total": insights.get("spent_total"),
        "warnings": insights.get("warnings"),
        "top_categories": insights.get("top_categories"),
    }
    raw = json.dumps(core, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()

def _maybe_llm_summary(user_id: str, period: str, period_key: str, insights: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not LLM_ENABLED:
        return None

    fp = _fingerprint(insights)
    cache_key = f"{user_id}::{period}::{period_key}::{fp}"

    cached = _LLM_CACHE.get(cache_key)
    if cached:
        return cached[1]

    out = llm_summarize(insights)

    if isinstance(out, dict):
        _LLM_CACHE[cache_key] = (time.time(), out)
        return out

    return None

def require_key(req) -> bool:
    return req.headers.get("x-agent-api-key", "") == AGENT_API_KEY

def _safe_bool(x: Any, default: bool) -> bool:
    if x is None:
        return default
    if isinstance(x, bool):
        return x
    return str(x).strip().lower() in ("1", "true", "yes", "y", "on")

# ----------------------------
# MONTHLY ENDPOINT
# ----------------------------
@app.post("/agent/monthly")
def agent_monthly():
    if not require_key(request):
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(force=True) or {}
    user_id = body.get("user_id")
    month = body.get("month")
    include_ai = _safe_bool(body.get("include_ai"), True)
    include_compare = _safe_bool(body.get("include_compare"), True)

    if not user_id or not month:
        return jsonify({"error": "user_id and month required"}), 400

    try:
        parse_yyyy_mm(month)
    except Exception:
        return jsonify({"error": "month must be YYYY-MM"}), 400

    budget = fetch_month_budget(user_id, month)
    if not budget:
        return jsonify({"error": "monthly_budgets row not found"}), 404

    entries = fetch_entries_for_month(user_id, month)
    insights = compute_insights(entries, budget, period="month", period_key=month)

    if include_compare:
        insights = _add_compare_section(user_id=user_id, month=month, insights=insights)

    ai = None
    if include_ai:
        ai = _maybe_llm_summary(user_id, "month", month, insights)

    return jsonify({"insights": insights, "ai": ai})

# ----------------------------
# COMPARE SECTION
# ----------------------------
def _add_compare_section(*, user_id: str, month: str, insights: Dict[str, Any]) -> Dict[str, Any]:
    pm = prev_month_str(month)
    prev_budget = fetch_month_budget(user_id, pm)
    if not prev_budget:
        insights["compare_prev"] = None
        return insights

    prev_entries = fetch_entries_for_month(user_id, pm)
    prev_ins = compute_insights(prev_entries, prev_budget, period="month", period_key=pm)

    cur_spent = float(insights.get("spent_total", 0.0))
    prev_spent = float(prev_ins.get("spent_total", 0.0))

    def pct_change(cur: float, prev: float):
        if prev <= 0:
            return None
        return round(((cur - prev) / prev) * 100.0, 2)

    insights["compare_prev"] = {
        "prev_month": pm,
        "spent_prev": round(prev_spent, 2),
        "spent_change_pct": pct_change(cur_spent, prev_spent),
    }
    return insights

if __name__ == "__main__":
    print("Starting Flask on http://127.0.0.1:5050 ...")
    app.run(host="0.0.0.0", port=5050, debug=False)
