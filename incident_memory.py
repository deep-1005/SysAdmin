import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any

_DB_LOCK = threading.Lock()
_DB_PATH = os.path.join(os.path.dirname(__file__), "logs", "incidents.db")


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


class IncidentMemory:
    def __init__(self, db_path: str = _DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with _DB_LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS incidents (
                        incident_id TEXT PRIMARY KEY,
                        correlation_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        primary_event TEXT,
                        risk_level TEXT,
                        cpu_usage REAL,
                        memory_usage REAL,
                        disk_usage REAL,
                        process_count INTEGER,
                        culprit_pid TEXT,
                        diagnosis TEXT,
                        rca TEXT,
                        suggested_action TEXT,
                        action_taken TEXT,
                        outcome TEXT,
                        outcome_notes TEXT,
                        context_json TEXT,
                        tool_trace_json TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_incidents_event_created ON incidents(primary_event, created_at DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_incidents_risk_created ON incidents(risk_level, created_at DESC)"
                )
                conn.commit()
            finally:
                conn.close()

    def record_incident(self, context: dict[str, Any], result: dict[str, Any]) -> str:
        incident_id = str(result.get("incident_id") or context.get("incident_id") or uuid.uuid4())
        correlation_id = str(context.get("correlation_id") or result.get("correlation_id") or incident_id)
        now = _utc_now()

        with _DB_LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO incidents (
                        incident_id, correlation_id, created_at, updated_at,
                        primary_event, risk_level, cpu_usage, memory_usage, disk_usage, process_count,
                        culprit_pid, diagnosis, rca, suggested_action, action_taken, outcome, outcome_notes,
                        context_json, tool_trace_json
                    ) VALUES (?, ?,
                              COALESCE((SELECT created_at FROM incidents WHERE incident_id = ?), ?), ?,
                              ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?,
                              COALESCE((SELECT action_taken FROM incidents WHERE incident_id = ?), ''),
                              COALESCE((SELECT outcome FROM incidents WHERE incident_id = ?), 'open'),
                              COALESCE((SELECT outcome_notes FROM incidents WHERE incident_id = ?), ''),
                              ?, ?)
                    """,
                    (
                        incident_id,
                        correlation_id,
                        incident_id,
                        now,
                        now,
                        context.get("primary_event", "UNKNOWN"),
                        result.get("risk_level", context.get("risk_level", "caution")),
                        float(context.get("cpu_usage", 0.0) or 0.0),
                        float(context.get("memory_usage", 0.0) or 0.0),
                        float(context.get("disk_usage", 0.0) or 0.0),
                        int(context.get("process_count", 0) or 0),
                        str(result.get("pid", "N/A")),
                        str(result.get("diagnostic_result", "")),
                        str(result.get("rca", "")),
                        str(result.get("previously_fixed_by", "")),
                        incident_id,
                        incident_id,
                        incident_id,
                        json.dumps(context, ensure_ascii=False),
                        json.dumps(result.get("tool_trace", []), ensure_ascii=False),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        return incident_id

    def update_incident_outcome(
        self,
        incident_id: str,
        action_taken: str,
        outcome: str,
        outcome_notes: str = "",
    ) -> None:
        if not incident_id:
            return

        with _DB_LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE incidents
                    SET action_taken = ?, outcome = ?, outcome_notes = ?, updated_at = ?
                    WHERE incident_id = ?
                    """,
                    (action_taken, outcome, outcome_notes, _utc_now(), incident_id),
                )
                conn.commit()
            finally:
                conn.close()

    def find_similar_incidents(self, context: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
        primary_event = str(context.get("primary_event", "UNKNOWN"))
        cpu = float(context.get("cpu_usage", 0.0) or 0.0)
        mem = float(context.get("memory_usage", 0.0) or 0.0)
        disk = float(context.get("disk_usage", 0.0) or 0.0)

        with _DB_LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT incident_id, created_at, primary_event, risk_level, culprit_pid,
                           diagnosis, suggested_action, action_taken, outcome,
                           ABS(cpu_usage - ?) + ABS(memory_usage - ?) + ABS(disk_usage - ?) AS distance
                    FROM incidents
                    WHERE primary_event = ?
                    ORDER BY
                        CASE WHEN outcome = 'resolved' THEN 0 ELSE 1 END,
                        distance ASC,
                        created_at DESC
                    LIMIT ?
                    """,
                    (cpu, mem, disk, primary_event, max(1, int(limit))),
                ).fetchall()
            finally:
                conn.close()

        return [dict(r) for r in rows]

    def build_previous_fix_suggestion(self, similar_incidents: list[dict[str, Any]]) -> str:
        if not similar_incidents:
            return ""

        best = similar_incidents[0]
        action = (best.get("action_taken") or best.get("suggested_action") or "terminate culprit process").strip()
        pid = str(best.get("culprit_pid") or "N/A")
        outcome = (best.get("outcome") or "unknown").strip()
        created = (best.get("created_at") or "recently").strip()

        return f"Previously fixed by: {action} (historical PID {pid}, outcome={outcome}, seen {created})."


_STORE: IncidentMemory | None = None


def get_incident_memory() -> IncidentMemory:
    global _STORE
    if _STORE is None:
        _STORE = IncidentMemory()
    return _STORE
