import os
import threading
from typing import Any

try:
    from prometheus_client import Counter, Gauge, start_http_server
except Exception:  # pragma: no cover - optional dependency at runtime
    Counter = Gauge = None
    start_http_server = None

_LOCK = threading.Lock()
_STARTED = False
_SERVER_PORT = 0

SERVICE_INFO = None
SYSTEM_CPU = None
SYSTEM_MEMORY = None
SYSTEM_DISK = None
SYSTEM_PROCESS_COUNT = None
INCIDENT_TOTAL = None
ACTION_TOTAL = None


def _build_collectors() -> None:
    global SERVICE_INFO
    global SYSTEM_CPU
    global SYSTEM_MEMORY
    global SYSTEM_DISK
    global SYSTEM_PROCESS_COUNT
    global INCIDENT_TOTAL
    global ACTION_TOTAL

    if Counter is None or Gauge is None:
        return

    if SYSTEM_CPU is not None:
        return

    SERVICE_INFO = Gauge(
        "sysadmin_service_info",
        "Service metadata for SysAdmin app",
        ["service"],
    )
    SYSTEM_CPU = Gauge("sysadmin_cpu_usage_percent", "Current CPU usage percent")
    SYSTEM_MEMORY = Gauge("sysadmin_memory_usage_percent", "Current memory usage percent")
    SYSTEM_DISK = Gauge("sysadmin_disk_usage_percent", "Current disk usage percent")
    SYSTEM_PROCESS_COUNT = Gauge("sysadmin_process_count", "Current process count")

    INCIDENT_TOTAL = Counter(
        "sysadmin_incidents_total",
        "Number of incidents detected",
        ["event", "risk"],
    )
    ACTION_TOTAL = Counter(
        "sysadmin_actions_total",
        "Number of operator actions",
        ["action", "status"],
    )


def start_metrics_server(service_name: str = "sysadmin") -> dict[str, Any]:
    global _STARTED
    global _SERVER_PORT

    enabled = os.getenv("PROMETHEUS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"enabled": False, "started": False, "reason": "disabled by PROMETHEUS_ENABLED"}

    if start_http_server is None:
        return {"enabled": True, "started": False, "reason": "prometheus_client not installed"}

    port = int(os.getenv("PROMETHEUS_PORT", "9108"))

    with _LOCK:
        _build_collectors()
        if SERVICE_INFO is not None:
            SERVICE_INFO.labels(service=service_name).set(1)

        if _STARTED:
            return {"enabled": True, "started": True, "port": _SERVER_PORT, "reason": "already started"}

        try:
            start_http_server(port)
            _STARTED = True
            _SERVER_PORT = port
            return {"enabled": True, "started": True, "port": port, "reason": "started"}
        except OSError as exc:
            return {"enabled": True, "started": False, "port": port, "reason": f"port unavailable: {exc}"}
        except Exception as exc:
            return {"enabled": True, "started": False, "port": port, "reason": str(exc)}


def update_system_metrics(metrics: dict[str, Any], event: str = "NORMAL", risk: str = "safe") -> None:
    if SYSTEM_CPU is None:
        _build_collectors()
    if SYSTEM_CPU is None:
        return

    try:
        SYSTEM_CPU.set(float(metrics.get("cpu_usage", 0.0) or 0.0))
        SYSTEM_MEMORY.set(float(metrics.get("memory_usage", 0.0) or 0.0))
        SYSTEM_DISK.set(float(metrics.get("disk_usage", 0.0) or 0.0))
        SYSTEM_PROCESS_COUNT.set(float(metrics.get("process_count", 0) or 0))
    except Exception:
        return


def observe_incident(event: str, risk: str) -> None:
    if INCIDENT_TOTAL is None:
        _build_collectors()
    if INCIDENT_TOTAL is None:
        return
    try:
        INCIDENT_TOTAL.labels(event=(event or "UNKNOWN"), risk=(risk or "caution")).inc()
    except Exception:
        return


def observe_action(action: str, status: str) -> None:
    if ACTION_TOTAL is None:
        _build_collectors()
    if ACTION_TOTAL is None:
        return
    try:
        ACTION_TOTAL.labels(action=(action or "unknown"), status=(status or "unknown")).inc()
    except Exception:
        return
