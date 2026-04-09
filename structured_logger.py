import json
import os
import threading
from datetime import datetime
from typing import Any

_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
_INCIDENT_LOG = os.path.join(_LOG_DIR, "incidents.jsonl")
_AUDIT_LOG = os.path.join(_LOG_DIR, "audit.jsonl")
_LOCK = threading.Lock()


def _ts() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _write_jsonl(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(_LOG_DIR, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_incident_event(level: str, event: str, correlation_id: str, **details: Any) -> None:
    record = {
        "ts": _ts(),
        "kind": "incident",
        "level": level,
        "event": event,
        "correlation_id": correlation_id,
        "details": details,
    }
    _write_jsonl(_INCIDENT_LOG, record)


def audit_action(action: str, status: str, correlation_id: str = "", **details: Any) -> None:
    record = {
        "ts": _ts(),
        "kind": "audit",
        "action": action,
        "status": status,
        "correlation_id": correlation_id,
        "details": details,
    }
    _write_jsonl(_AUDIT_LOG, record)
