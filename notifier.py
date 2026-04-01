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


class SlackNotifier:
    def __init__(self):
        self.url = os.getenv("SLACK_WEBHOOK_URL")
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
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type":"application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()
