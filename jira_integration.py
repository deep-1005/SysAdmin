import json
import os
import urllib.error
import urllib.request
from typing import Any


def _jira_enabled() -> bool:
    return os.getenv("JIRA_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _is_dangerous(risk_level: str) -> bool:
    return str(risk_level or "").strip().lower() == "dangerous"


def create_jira_ticket_if_enabled(context: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Create a Jira issue for dangerous incidents when explicitly enabled by env."""
    risk = str(result.get("risk_level") or context.get("risk_level") or "caution")
    if not _jira_enabled() or not _is_dangerous(risk):
        return {"created": False, "reason": "disabled or non-dangerous"}

    base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")
    email = os.getenv("JIRA_EMAIL", "")
    token = os.getenv("JIRA_API_TOKEN", "")
    project_key = os.getenv("JIRA_PROJECT_KEY", "")
    issue_type = os.getenv("JIRA_ISSUE_TYPE", "Task")

    if not all([base_url, email, token, project_key]):
        return {"created": False, "reason": "missing Jira environment configuration"}

    incident_id = str(result.get("incident_id") or context.get("incident_id") or "")
    correlation_id = str(result.get("correlation_id") or context.get("correlation_id") or incident_id)
    event = str(context.get("primary_event", "UNKNOWN"))
    pid = str(result.get("pid", "N/A"))
    diagnosis = str(result.get("diagnostic_result", ""))

    title = f"[{risk.upper()}] SysAdmin Incident {event} (PID {pid})"
    description = (
        "h3. SysAdmin Incident\n"
        f"*Incident ID*: {incident_id}\n"
        f"*Correlation ID*: {correlation_id}\n"
        f"*Event*: {event}\n"
        f"*Risk*: {risk}\n"
        f"*PID*: {pid}\n"
        f"*CPU*: {context.get('cpu_usage', '?')}\n"
        f"*Memory*: {context.get('memory_usage', '?')}\n"
        f"*Disk*: {context.get('disk_usage', '?')}\n\n"
        "h4. Diagnosis\n"
        f"{diagnosis[:2000]}\n"
    )

    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": title,
            "description": description,
            "issuetype": {"name": issue_type},
            "labels": ["sysadmin-ai", "incident", risk, event.lower()],
        }
    }

    api_url = f"{base_url}/rest/api/3/issue"
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic " + _basic_auth(email, token),
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return {
                "created": True,
                "key": body.get("key", ""),
                "id": body.get("id", ""),
                "url": f"{base_url}/browse/{body.get('key', '')}" if body.get("key") else "",
            }
    except urllib.error.HTTPError as exc:
        text = ""
        try:
            text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            text = str(exc)
        return {"created": False, "reason": f"http {exc.code}", "detail": text[:500]}
    except Exception as exc:
        return {"created": False, "reason": str(exc)}


def _basic_auth(email: str, token: str) -> str:
    import base64

    raw = f"{email}:{token}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")
