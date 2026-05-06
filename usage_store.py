import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from google.cloud import storage


TZ = ZoneInfo(os.getenv("USAGE_TIMEZONE", "America/Sao_Paulo"))

USAGE_BUCKET = os.getenv("USAGE_BUCKET", "").strip()
USAGE_PREFIX = os.getenv("USAGE_PREFIX", "usage").strip().strip("/")

MONTHLY_BUDGET_USD = float(os.getenv("MONTHLY_BUDGET_USD", "3"))
WORKER_HARD_STOP_USD = float(os.getenv("WORKER_HARD_STOP_USD", "2.50"))


def current_usage_key() -> str:
    now = datetime.now(TZ)
    return f"{USAGE_PREFIX}/usage_{now:%Y_%m}.json"


def empty_usage() -> dict:
    now = datetime.now(TZ).isoformat()
    return {
        "schema_version": "1.0",
        "service": os.getenv("SERVICE_NAME", "telegram-english-bot"),
        "month": datetime.now(TZ).strftime("%Y-%m"),
        "estimated_spent_usd": 0.0,
        "monthly_budget_usd": MONTHLY_BUDGET_USD,
        "worker_hard_stop_usd": WORKER_HARD_STOP_USD,
        "events": [],
        "created_at": now,
        "updated_at": now,
    }


def _blob():
    if not USAGE_BUCKET:
        raise RuntimeError("USAGE_BUCKET env var não configurada")

    client = storage.Client()
    bucket = client.bucket(USAGE_BUCKET)
    return bucket.blob(current_usage_key())


def load_usage() -> dict:
    blob = _blob()

    if not blob.exists():
        usage = empty_usage()
        save_usage(usage)
        return usage

    raw = blob.download_as_text(encoding="utf-8")
    if not raw.strip():
        usage = empty_usage()
        save_usage(usage)
        return usage

    data = json.loads(raw)

    data.setdefault("schema_version", "1.0")
    data.setdefault("service", os.getenv("SERVICE_NAME", "telegram-english-bot"))
    data.setdefault("month", datetime.now(TZ).strftime("%Y-%m"))
    data.setdefault("estimated_spent_usd", 0.0)
    data.setdefault("monthly_budget_usd", MONTHLY_BUDGET_USD)
    data.setdefault("worker_hard_stop_usd", WORKER_HARD_STOP_USD)
    data.setdefault("events", [])
    data.setdefault("created_at", datetime.now(TZ).isoformat())
    data.setdefault("updated_at", datetime.now(TZ).isoformat())

    return data


def save_usage(data: dict) -> dict:
    data["updated_at"] = datetime.now(TZ).isoformat()

    blob = _blob()
    blob.upload_from_string(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        content_type="application/json; charset=utf-8",
    )
    return data


def estimated_spent_usd() -> float:
    usage = load_usage()
    return float(usage.get("estimated_spent_usd", 0.0))


def cost_allowed(estimated_increment_usd: float = 0.0) -> tuple[bool, dict]:
    usage = load_usage()

    current = float(usage.get("estimated_spent_usd", 0.0))
    projected = current + float(estimated_increment_usd)

    allowed = projected <= WORKER_HARD_STOP_USD

    return allowed, {
        "allowed": allowed,
        "estimated_spent_usd": round(current, 6),
        "estimated_increment_usd": round(float(estimated_increment_usd), 6),
        "projected_spent_usd": round(projected, 6),
        "worker_hard_stop_usd": WORKER_HARD_STOP_USD,
        "monthly_budget_usd": MONTHLY_BUDGET_USD,
        "usage_object": f"gs://{USAGE_BUCKET}/{current_usage_key()}",
    }


def register_usage(
    estimated_increment_usd: float,
    event_type: str,
    metadata: dict | None = None,
) -> dict:
    usage = load_usage()

    increment = max(float(estimated_increment_usd), 0.0)
    current = float(usage.get("estimated_spent_usd", 0.0))
    new_total = current + increment

    event = {
        "ts": datetime.now(TZ).isoformat(),
        "event_type": event_type,
        "estimated_increment_usd": round(increment, 6),
        "estimated_spent_after_usd": round(new_total, 6),
        "metadata": metadata or {},
    }

    usage["estimated_spent_usd"] = round(new_total, 6)
    usage["monthly_budget_usd"] = MONTHLY_BUDGET_USD
    usage["worker_hard_stop_usd"] = WORKER_HARD_STOP_USD
    usage.setdefault("events", []).append(event)

    # Evita arquivo infinito. Ledger resumido, não romance russo.
    usage["events"] = usage["events"][-500:]

    return save_usage(usage)
