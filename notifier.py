"""
notifier.py — Slack Webhook Notifier
─────────────────────────────────────
Posts RCA blocks to a Slack channel when an incident is resolved.
No extra SDK needed — uses stdlib urllib only.

Setup:
  1. Go to api.slack.com/apps → create app → Incoming Webhooks → add to channel
  2. Add SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... to your .env
"""
import os, json, urllib.request, urllib.error
from datetime import datetime
import sqlite3


def _resolve_webhook_url(explicit_url: str | None = None) -> str:
    url = (explicit_url or os.getenv("SLACK_WEBHOOK_URL") or "").strip()
    if url:
        return url

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return ""

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key.strip() == "SLACK_WEBHOOK_URL":
                    url = val.strip().strip('"').strip("'")
                    if url:
                        os.environ["SLACK_WEBHOOK_URL"] = url
                        return url
    except Exception:
        return ""
    return ""


class SlackNotifier:
    def __init__(self, webhook_url: str | None = None):
        self.url = _resolve_webhook_url(webhook_url)
        if not self.url:
            raise ValueError("SLACK_WEBHOOK_URL not set in environment.")

    def send_rca(self, rca: str, context: dict):
        ev   = context.get("primary_event", "UNKNOWN")
        cpu  = context.get("cpu_usage",    "?")
        ram  = context.get("memory_usage", "?")
        ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        col  = {"CPU_SPIKE":"#f59e0b","MEMORY_SPIKE":"#ef4444",
                "DISK_SPIKE":"#a78bfa","HIGH_PROCESS_COUNT":"#f59e0b",
                "LOG_ALERT":"#ef4444"}.get(ev, "#6b7280")

        payload = {
            "attachments": [{
                "color": col,
                "blocks": [
                    {"type":"header","text":{"type":"plain_text","text":f"🚨 SysAdmin AI — {ev}","emoji":True}},
                    {"type":"section","fields":[
                        {"type":"mrkdwn","text":f"*CPU*\n{cpu}%"},
                        {"type":"mrkdwn","text":f"*RAM*\n{ram}%"},
                        {"type":"mrkdwn","text":f"*Time*\n{ts}"},
                        {"type":"mrkdwn","text":f"*Host*\n{os.environ.get('COMPUTERNAME','unknown')}"},
                    ]},
                    {"type":"divider"},
                    {"type":"section","text":{"type":"mrkdwn","text":f"*📋 RCA*\n{rca}"}},
                    {"type":"actions","elements":[
                        {"type":"button","text":{"type":"plain_text","text":"✅ Acknowledge"},
                         "style":"primary","value":"ack"},
                        {"type":"button","text":{"type":"plain_text","text":"🔴 Kill Process"},
                         "style":"danger","value":"kill"},
                    ]},
                    {"type":"context","elements":[
                        {"type":"mrkdwn","text":"Sent by *Autonomous SysAdmin AI* (Windows) • human-in-the-loop"}
                    ]},
                ],
            }]
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            raise RuntimeError(f"Slack HTTP {e.code}: {body or e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Slack URL error: {e.reason}") from e

    def send_summary(self, period: str = "daily"):
        """Send daily/weekly summary from SQLite incident memory."""
        period = (period or "daily").strip().lower()
        if period not in {"daily", "weekly"}:
            period = "daily"

        since_expr = "-1 day" if period == "daily" else "-7 day"
        db_path = os.path.join(os.path.dirname(__file__), "logs", "incidents.db")

        total = resolved = failed = 0
        top_event = "N/A"
        top_action = "N/A"

        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN outcome = 'resolved' THEN 1 ELSE 0 END) AS resolved,
                           SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) AS failed
                    FROM incidents
                    WHERE datetime(created_at) >= datetime('now', ?)
                    """,
                    (since_expr,),
                ).fetchone()
                if row:
                    total = int(row[0] or 0)
                    resolved = int(row[1] or 0)
                    failed = int(row[2] or 0)

                event_row = conn.execute(
                    """
                    SELECT primary_event, COUNT(*) AS n
                    FROM incidents
                    WHERE datetime(created_at) >= datetime('now', ?)
                    GROUP BY primary_event
                    ORDER BY n DESC
                    LIMIT 1
                    """,
                    (since_expr,),
                ).fetchone()
                if event_row:
                    top_event = str(event_row[0] or "N/A")

                action_row = conn.execute(
                    """
                    SELECT action_taken, COUNT(*) AS n
                    FROM incidents
                    WHERE datetime(created_at) >= datetime('now', ?) AND action_taken <> ''
                    GROUP BY action_taken
                    ORDER BY n DESC
                    LIMIT 1
                    """,
                    (since_expr,),
                ).fetchone()
                if action_row:
                    top_action = str(action_row[0] or "N/A")
            finally:
                conn.close()

        payload = {
            "text": f"SysAdmin AI {period} summary",
            "attachments": [
                {
                    "color": "#0ea5e9",
                    "blocks": [
                        {"type": "header", "text": {"type": "plain_text", "text": f"SysAdmin AI {period.title()} Summary", "emoji": True}},
                        {
                            "type": "section",
                            "fields": [
                                {"type": "mrkdwn", "text": f"*Incidents*\n{total}"},
                                {"type": "mrkdwn", "text": f"*Resolved*\n{resolved}"},
                                {"type": "mrkdwn", "text": f"*Failed*\n{failed}"},
                                {"type": "mrkdwn", "text": f"*Top Event*\n{top_event}"},
                                {"type": "mrkdwn", "text": f"*Top Action*\n{top_action}"},
                                {"type": "mrkdwn", "text": f"*Generated*\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"},
                            ],
                        },
                    ],
                }
            ],
        }

        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            raise RuntimeError(f"Slack HTTP {e.code}: {body or e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Slack URL error: {e.reason}") from e
