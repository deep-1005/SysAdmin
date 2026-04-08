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
