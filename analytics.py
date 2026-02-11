# analytics.py
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# ----------------------------
# Base normalization buckets
# ----------------------------
NECESSITY_BASE = {"rent", "grocery", "utility", "fuel", "transport", "health", "education"}
DISCRETIONARY_BASE = {"shopping", "entertainment", "charity", "subscriptions", "travel"}

# Heuristics for restaurant vs home/grocery-like food (NO LLM)
RESTAURANT_KEYWORDS = {
    "kfc", "mcdonald", "mc donald", "dominos", "domino", "pizza", "burger", "shawarma",
    "restaurant", "cafe", "coffee", "bistro", "foodpanda", "food panda", "careem food",
    "delivery", "dine", "bbq", "karahi", "nihari"
}
HOME_FOOD_KEYWORDS = {
    "grocery", "super", "mart", "store", "cash&carry", "cash and carry", "imtiyaz", "metro",
    "utility store", "ration", "kirana"
}
FOOD_EXPENSIVE_RS = 1800

# ----------------------------
# Date helpers required by app.py
# ----------------------------
def parse_yyyy_mm(s: str) -> Tuple[int, int]:
    s = (s or "").strip()
    y, m = s.split("-")
    year = int(y)
    mon = int(m)
    if mon < 1 or mon > 12:
        raise ValueError("month out of range")
    return year, mon

def parse_yyyy_mm_dd(s: str) -> date:
    s = (s or "").strip()
    return date.fromisoformat(s)

def month_bounds_utc(month: str) -> Tuple[datetime, datetime]:
    y, m = parse_yyyy_mm(month)
    start = datetime(y, m, 1, 0, 0, 0, tzinfo=timezone.utc)
    if m == 12:
        end = datetime(y + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        end = datetime(y, m + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return start, end

def range_bounds_utc(start_d: date, end_d: date) -> Tuple[datetime, datetime]:
    """
    [start_d, end_d) in UTC
    """
    start = datetime(start_d.year, start_d.month, start_d.day, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(end_d.year, end_d.month, end_d.day, 0, 0, 0, tzinfo=timezone.utc)
    return start, end

def prev_month_str(month: str) -> str:
    y, m = parse_yyyy_mm(month)
    if m == 1:
        return f"{y-1}-12"
    return f"{y}-{m-1:02d}"

def _month_str(d: date) -> str:
    return f"{d.year}-{d.month:02d}"

def split_range_by_month(start: date, end: date) -> List[str]:
    """
    Returns list of YYYY-MM that overlap [start, end)
    """
    if end <= start:
        return []
    cur = date(start.year, start.month, 1)
    out: List[str] = []
    while cur < end:
        out.append(_month_str(cur))
        # next month
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    # ensure month containing start included
    sm = _month_str(start)
    if sm not in out:
        out.insert(0, sm)
    # de-dupe keep order
    seen = set()
    final = []
    for x in out:
        if x not in seen:
            seen.add(x)
            final.append(x)
    return final

def prorate_monthly_budget_for_range(*, user_id: str, start: date, end: date) -> float:
    """
    You store only monthly budgets; for weekly reports we prorate per day.
    This supports ranges crossing months.

    Proration:
      monthly_budget / days_in_month * overlap_days
    """
    if end <= start:
        return 0.0

    # local import to avoid circulars
    from db import fetch_month_budget

    total = 0.0
    months = split_range_by_month(start, end)

    for m in months:
        b = fetch_month_budget(user_id, m)
        if not b:
            continue
        monthly_budget = float(b.get("budget_amount") or 0.0)
        if monthly_budget <= 0:
            continue

        ms_dt, me_dt = month_bounds_utc(m)
        ms = ms_dt.date()
        me = me_dt.date()

        overlap_start = max(start, ms)
        overlap_end = min(end, me)
        if overlap_end <= overlap_start:
            continue

        days_in_month = (me - ms).days
        overlap_days = (overlap_end - overlap_start).days
        if days_in_month <= 0 or overlap_days <= 0:
            continue

        total += (monthly_budget / days_in_month) * overlap_days

    return round(total, 2)

# ----------------------------
# Analytics core
# ----------------------------
def _to_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0

def _text_blob(entry: Dict[str, Any]) -> str:
    parts = [
        entry.get("title"),
        entry.get("beneficiary_name"),
        entry.get("raw_text"),
        entry.get("category"),
        entry.get("category_normalized"),
    ]
    return " ".join([str(p) for p in parts if p]).lower()

def _parse_ts(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    s = str(ts)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def normalize_category(entry: Dict[str, Any]) -> str:
    cn = (entry.get("category_normalized") or "").strip().lower()
    if cn:
        return cn

    c = (entry.get("category") or "").strip().lower()

    if "utility" in c:
        return "utility"
    if "fund" in c or "transfer" in c:
        return "funds_transfer"
    if "groc" in c:
        return "grocery"
    if "shop" in c:
        return "shopping"
    if "fuel" in c or "petrol" in c:
        return "fuel"
    if "food" in c:
        return "food"
    if "rent" in c:
        return "rent"
    return c or "other"

def classify_food_unnecessary(entry: Dict[str, Any]) -> Tuple[bool, str]:
    blob = _text_blob(entry)
    amt = _to_float(entry.get("amount"))

    if any(k in blob for k in HOME_FOOD_KEYWORDS):
        return False, "home_food_keyword"
    if any(k in blob for k in RESTAURANT_KEYWORDS):
        return True, "restaurant_keyword"
    if amt >= FOOD_EXPENSIVE_RS:
        return True, "expensive_food_amount"
    return False, "unknown_food_type"

def _day_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).date().isoformat()

def compute_insights(
    entries: List[Dict[str, Any]],
    budget_row: Dict[str, Any],
    *,
    period: str = "month",
    period_key: Optional[str] = None,
) -> Dict[str, Any]:
    budget_amount = _to_float(budget_row.get("budget_amount") or budget_row.get("amount"))
    home_city = (budget_row.get("home_city") or "").strip() or None

    spent = 0.0
    totals_by_cat: Dict[str, float] = defaultdict(float)
    daily_totals: Dict[str, float] = defaultdict(float)

    discretionary = 0.0
    restaurant_food = 0.0

    EXCLUDE_CATS = {"funds_transfer"}

    for e in entries:
        amt = _to_float(e.get("amount"))
        if amt <= 0:
            continue

        catn = normalize_category(e)
        if catn in EXCLUDE_CATS:
            continue

        spent += amt
        totals_by_cat[catn] += amt

        dt = _parse_ts(e.get("created_at"))
        if dt:
            daily_totals[_day_key(dt)] += amt

        if catn in DISCRETIONARY_BASE:
            discretionary += amt

        if catn == "food":
            is_rest, _reason = classify_food_unnecessary(e)
            if is_rest:
                restaurant_food += amt
                discretionary += amt

    top = sorted(totals_by_cat.items(), key=lambda kv: kv[1], reverse=True)
    top5 = [{"category": k, "amount": round(v, 2)} for k, v in top[:5]]

    warnings: List[str] = []

    if budget_amount > 0 and spent > budget_amount:
        warnings.append("OVER_BUDGET")

    rent = totals_by_cat.get("rent", 0.0)
    if budget_amount > 0 and rent / budget_amount >= 0.45:
        warnings.append("RENT_HIGH")

    if spent > 0 and discretionary / spent >= 0.35 and discretionary >= 10000:
        warnings.append("DISCRETIONARY_HIGH")

    if spent > 0 and restaurant_food / spent >= 0.15 and restaurant_food >= 8000:
        warnings.append("RESTAURANT_FOOD_HIGH")

    if daily_totals:
        vals = list(daily_totals.values())
        avg_day = sum(vals) / max(1, len(vals))
        max_day = max(vals)
        if avg_day > 0 and max_day >= avg_day * 2.5 and max_day >= 5000:
            warnings.append("SPIKE_DETECTED")

    actions: List[str] = []
    if "OVER_BUDGET" in warnings and budget_amount > 0:
        over = spent - budget_amount
        actions.append(f"You are over budget by Rs. {over:,.0f}. Cut discretionary categories first.")

    if "RENT_HIGH" in warnings and budget_amount > 0:
        actions.append("Rent is taking a very large share of your budget. Consider negotiating rent, sharing accommodation, or relocating.")

    if "DISCRETIONARY_HIGH" in warnings:
        actions.append("Discretionary spending is high. Set a weekly cap and review non-essential transactions.")

    if "RESTAURANT_FOOD_HIGH" in warnings:
        actions.append("Restaurant/delivery food is high. Reduce dine-out frequency or switch to home meals for savings.")

    if "SPIKE_DETECTED" in warnings:
        actions.append("A spending spike was detected on one day. Review that day's transactions and tag the cause (shopping, dining, bills, etc.).")

    return {
        "period": period,
        "period_key": period_key,
        "home_city": home_city,
        "budget_amount": round(budget_amount, 2),
        "spent_total": round(spent, 2),
        "remaining": round((budget_amount - spent) if budget_amount > 0 else 0.0, 2),
        "totals_by_category": {k: round(v, 2) for k, v in totals_by_cat.items()},
        "top_categories": top5,
        "discretionary_total": round(discretionary, 2),
        "restaurant_food_total": round(restaurant_food, 2),
        "warnings": warnings,
        "actions": actions,
    }
