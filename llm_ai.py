# llm_ai.py
import json
import time
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from openai import OpenAI

# ---------- load .env ----------
# This is REQUIRED so OPENROUTER_API_KEY is picked up correctly
load_dotenv()

from config import (
    OPENROUTER_SITE_URL,
    OPENROUTER_SITE_NAME,
    OPENROUTER_MAX_RPM,
)

# Read API key directly after loading .env
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Use the model you confirmed works
SUMMARY_MODEL = "openai/gpt-oss-120b:free"

# ---------- rate limiting (safe for free tier) ----------
MAX_RPM = int(OPENROUTER_MAX_RPM or 5)

_COOLDOWN_UNTIL = 0.0
_WINDOW_START = 0.0
_WINDOW_COUNT = 0


def _now() -> float:
    return time.time()


def _in_cooldown() -> bool:
    return _now() < _COOLDOWN_UNTIL


def _set_cooldown(seconds: int) -> None:
    global _COOLDOWN_UNTIL
    _COOLDOWN_UNTIL = _now() + max(1, int(seconds))


def _rate_limit_wait() -> None:
    global _WINDOW_START, _WINDOW_COUNT
    t = _now()

    if _WINDOW_START == 0.0 or (t - _WINDOW_START) >= 60.0:
        _WINDOW_START = t
        _WINDOW_COUNT = 0

    if _WINDOW_COUNT >= MAX_RPM:
        sleep_s = max(1, int(60 - (t - _WINDOW_START)))
        time.sleep(sleep_s)
        _WINDOW_START = _now()
        _WINDOW_COUNT = 0

    _WINDOW_COUNT += 1


def _client() -> OpenAI:
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )


def _headers() -> Dict[str, str]:
    h: Dict[str, str] = {}
    if OPENROUTER_SITE_URL:
        h["HTTP-Referer"] = OPENROUTER_SITE_URL
    if OPENROUTER_SITE_NAME:
        h["X-Title"] = OPENROUTER_SITE_NAME
    return h


def _extract_first_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None

    # Strict parse
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except Exception:
        pass

    # Fallback: first {...}
    s = text.find("{")
    e = text.rfind("}")
    if s >= 0 and e > s:
        try:
            v = json.loads(text[s : e + 1])
            return v if isinstance(v, dict) else None
        except Exception:
            return None
    return None


# ---------- public API ----------

def llm_summarize(insights: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    ONE call per report (monthly/weekly).
    Returns structured summary dict or None.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not found. Did you load .env?")

    if _in_cooldown():
        return None

    _rate_limit_wait()

    system = (
        "You are an AI financial advisor inside an expense tracker.\n"
        "Use ONLY the numbers provided.\n"
        "Do NOT invent data.\n"
        "Focus on overspending, rent burden, discretionary spend, and spikes.\n"
        "Return STRICT JSON (no markdown, no extra text) with keys:\n"
        "headline, summary, bullets (array), actions (array), risk_level (low|medium|high).\n"
    )

    user_payload = {
        "task": "monthly_or_weekly_summary",
        "insights": insights,
    }

    try:
        resp = _client().chat.completions.create(
            model=SUMMARY_MODEL,
            extra_headers=_headers(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            temperature=0.3,
        )

        text = (resp.choices[0].message.content or "").strip()
        return _extract_first_json(text)

    except Exception as e:
        msg = str(e).lower()

        if "429" in msg or "rate limit" in msg:
            _set_cooldown(60)
        elif "no endpoints found matching your data policy" in msg:
            _set_cooldown(10 * 60)

        return None
