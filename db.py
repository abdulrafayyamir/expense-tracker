# db.py
import os
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row

# Read DB connection settings from .env
DB_HOST = os.getenv("DB_HOST", "").strip()
DB_PORT = int((os.getenv("DB_PORT", "5432").strip() or "5432"))
DB_NAME = os.getenv("DB_NAME", "postgres").strip()
DB_USER = os.getenv("DB_USER", "postgres").strip()
DB_PASSWORD = os.getenv("DB_PASSWORD", "").strip()
DB_SSLMODE = os.getenv("DB_SSLMODE", "require").strip()

# Optional tuning
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))  # seconds
DB_MAX_RETRIES = int(os.getenv("DB_MAX_RETRIES", "3"))
DB_RETRY_BASE_SLEEP = float(os.getenv("DB_RETRY_BASE_SLEEP", "0.6"))  # seconds

def _require_env() -> None:
    if not DB_HOST or not DB_PASSWORD:
        raise RuntimeError("DB_HOST / DB_PASSWORD not set (check .env)")

def _resolve_hostaddr_ipv4(host: str) -> Optional[str]:
    """
    Prefer IPv4 to avoid Windows+SSL issues when DNS returns IPv6 only/first.
    Returns an IPv4 address string or None if not found.
    """
    try:
        infos = socket.getaddrinfo(host, DB_PORT, family=socket.AF_INET, type=socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception:
        pass
    return None

@contextmanager
def get_conn():
    _require_env()

    hostaddr = _resolve_hostaddr_ipv4(DB_HOST)

    last_exc: Optional[Exception] = None
    for attempt in range(1, DB_MAX_RETRIES + 1):
        try:
            conn = psycopg.connect(
                host=DB_HOST,
                hostaddr=hostaddr,          # <-- forces IPv4 when available
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
                sslmode=DB_SSLMODE,
                connect_timeout=DB_CONNECT_TIMEOUT,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
                row_factory=dict_row,
            )
            try:
                yield conn
            finally:
                conn.close()
            return
        except psycopg.OperationalError as e:
            last_exc = e
            # small backoff
            if attempt < DB_MAX_RETRIES:
                time.sleep(DB_RETRY_BASE_SLEEP * attempt)
            else:
                raise

    # Should never reach here
    if last_exc:
        raise last_exc

def _month_bounds(month: str) -> Tuple[datetime, datetime]:
    """
    month: 'YYYY-MM'
    returns UTC-aware [start, end)
    """
    y, m = month.split("-")
    year = int(y)
    mon = int(m)

    start = datetime(year, mon, 1, 0, 0, 0, tzinfo=timezone.utc)
    if mon == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        end = datetime(year, mon + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return start, end

def fetch_month_budget(user_id: str, month: str) -> Optional[Dict[str, Any]]:
    """
    Expects table: monthly_budgets
      columns typically: user_id (uuid), month (text), amount (numeric), home_city (text)
    """
    sql = """
    select
      user_id,
      month,
      amount::float8 as budget_amount,
      home_city
    from monthly_budgets
    where user_id = %s and month = %s
    limit 1;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, month))
            row = cur.fetchone()
            return dict(row) if row else None

def fetch_entries_for_month(user_id: str, month: str) -> List[Dict[str, Any]]:
    """
    Expects table: entries
      must have: user_id, created_at, amount, category, plus optional fields used by analytics
    """
    start, end = _month_bounds(month)

    sql = """
    select
      id,
      user_id,
      entry_type,
      category,
      title,
      amount::float8 as amount,
      created_at,
      raw_text,
      beneficiary_name,
      image_path,
      location_name,
      category_normalized
    from entries
    where user_id = %s
      and created_at >= %s
      and created_at < %s
    order by created_at asc;
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, start, end))
            rows = cur.fetchall() or []
            return [dict(r) for r in rows]

def fetch_entry(entry_id: str) -> Optional[Dict[str, Any]]:
    sql = """
    select
      id,
      user_id,
      entry_type,
      category,
      title,
      amount::float8 as amount,
      created_at,
      raw_text,
      beneficiary_name,
      image_path,
      location_name,
      category_normalized
    from entries
    where id = %s
    limit 1;
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (entry_id,))
            row = cur.fetchone()
            return dict(row) if row else None
