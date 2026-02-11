# config.py
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Always load .env from this folder (no CWD issues)
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)


def _b(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")


AGENT_API_KEY = os.getenv("AGENT_API_KEY", "dev-secret").strip()

# Kept for backward compatibility (not used now if you rely on LLM_ENABLED)
GEMINI_ENABLED = _b("GEMINI_ENABLED", "false")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free").strip()
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "").strip()
OPENROUTER_SITE_NAME = os.getenv("OPENROUTER_SITE_NAME", "").strip()

# Soft local limiter (safe defaults for free-tier)
OPENROUTER_MAX_RPM = int(os.getenv("OPENROUTER_MAX_RPM", "3").strip() or "3")

LLM_ENABLED = (LLM_PROVIDER == "openrouter") and bool(OPENROUTER_API_KEY)
