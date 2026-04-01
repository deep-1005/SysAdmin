"""
detective_agent.py — Ollama Edition
─────────────────────────────────────
Runs the AI 100% locally on your laptop using Ollama.
No API key. No internet. No quota. Completely free forever.

Setup (one time):
  1. Download Ollama from ollama.com → install it
  2. Open a terminal and run:  ollama pull llama3.2
  3. Run the app:              python gui_app.py

Ollama must be running in the background (it starts automatically after install).
"""
import re
import os
import urllib.request
import urllib.error
import json
from dotenv import load_dotenv
from tool_runner import ToolRunner

_runner = ToolRunner()
load_dotenv()

# ── config ────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5:0.5b")


def _post_ollama(model: str, prompt: str) -> str:
    payload = json.dumps({
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 512,
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "").strip()


# ── core LLM call ─────────────────────────────────────────────
def _call_ollama(prompt: str) -> str:
    """
    Sends a prompt to the local Ollama server and returns the response.
    Ollama runs on port 11434 by default.
    """
    try:
        return _post_ollama(OLLAMA_MODEL, prompt)

    except urllib.error.HTTPError as e:
        body = ""
        server_error = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            server_error = parsed.get("error", "")
        except Exception:
            server_error = body

        err_text = (server_error or str(e)).lower()
        low_memory = (
            "requires more system memory" in err_text
            or "runner process has terminated" in err_text
        )

        if low_memory and OLLAMA_FALLBACK_MODEL and OLLAMA_FALLBACK_MODEL != OLLAMA_MODEL:
            try:
                return _post_ollama(OLLAMA_FALLBACK_MODEL, prompt)
            except Exception:
                pass

        if low_memory:
            raise RuntimeError(
                "Ollama failed because the selected model cannot run with current free RAM.\n\n"
                f"Current model: {OLLAMA_MODEL}\n"
                "Fix options:\n"
                "1) Free memory (close heavy apps) and retry\n"
                "2) Use a smaller model, e.g. qwen2.5:0.5b\n"
                "   Commands:\n"
                "   ollama pull qwen2.5:0.5b\n"
                "   set OLLAMA_MODEL=qwen2.5:0.5b\n\n"
                f"Ollama server message: {server_error or str(e)}"
            )

        raise RuntimeError(f"Ollama HTTP {e.code}: {server_error or str(e)}")

    except urllib.error.URLError as e:
        if "Connection refused" in str(e) or "actively refused" in str(e):
            raise RuntimeError(
                "Ollama is not running!\n\n"
                "Fix: Open a terminal and run:  ollama serve\n"
                "Or restart your computer — Ollama should start automatically."
            )
        raise RuntimeError(f"Ollama connection error: {e}")

    except Exception as e:
        raise RuntimeError(f"Ollama error: {e}")


# ── available tools ───────────────────────────────────────────
TOOLS = {
    "check_processes":     _runner.check_processes,
    "check_memory":        _runner.check_memory,
    "check_disk":          _runner.check_disk,
    "inspect_top_process": _runner.inspect_top_process,
    "check_open_files":    _runner.check_open_files,
    "check_network":       _runner.check_network,
}

TOOL_DOCS = """
You have these diagnostic tools. Call one per turn by writing exactly: TOOL: tool_name

  check_processes      - Lists the top 5 apps using the most CPU right now (ALWAYS call this first)
  inspect_top_process  - Gets full details (PID, exe path, threads) about the #1 CPU app
  check_memory         - Shows a RAM usage breakdown
  check_disk           - Shows disk space on all drives
  check_open_files     - Shows how many files the top app currently has open
  check_network        - Shows active network connections

Important rules:
- ALWAYS call check_processes as your very first action
- NEVER identify PID 0 or "System Idle Process" as the culprit — those are Windows internals
- When you have identified the root cause, write: DIAGNOSIS: [your findings including PID and process name]
"""


# ── agentic reasoning loop ────────────────────────────────────
def _run_agentic_loop(context: dict) -> tuple:
    """
    The AI reasoning loop — Ollama reads tool output and
    decides what to investigate next. No hardcoded steps.
    Returns: (diagnosis_text, culprit_pid)
    """
    ev   = context["primary_event"]
    cpu  = context["cpu_usage"]
    ram  = context["memory_usage"]
    disk = context["disk_usage"]
    logs = "\n".join(context["recent_logs"]) or "None"

    conversation = []
    pid = "unknown"

    # Opening prompt — give the AI the situation
    conversation.append(f"""You are a Windows system expert diagnosing a computer problem.

{TOOL_DOCS}

The monitoring system just detected an issue:
  Alert type : {ev}
  CPU usage  : {cpu}%
  RAM usage  : {ram}%
  Disk usage : {disk}%
  System logs: {logs}

Investigate this step by step. Start by calling check_processes.""")

    # Let the AI reason for up to 6 turns
    for turn in range(6):
        full_prompt = "\n\n".join(conversation) + "\n\nYour response:"
        response    = _call_ollama(full_prompt)
        conversation.append(f"Assistant: {response}")

        # ── Did the AI call a tool? ───────────────────────────
        tool_match = re.search(r"TOOL:\s*(\w+)", response, re.IGNORECASE)
        if tool_match:
            tool_name = tool_match.group(1).strip().lower()
            if tool_name in TOOLS:
                tool_output = TOOLS[tool_name]()
                # Grab PID from tool output as early as possible
                found = _extract_pid(tool_output)
                if found != "unknown":
                    pid = found
                conversation.append(f"Tool result ({tool_name}):\n{tool_output}")
            else:
                conversation.append(
                    f"Tool '{tool_name}' does not exist. "
                    f"Available tools: {', '.join(TOOLS.keys())}"
                )
            continue

        # ── Did the AI reach a conclusion? ────────────────────
        diag_match = re.search(r"DIAGNOSIS:(.*)", response, re.DOTALL | re.IGNORECASE)
        if diag_match:
            diagnosis = diag_match.group(1).strip()
            found = _extract_pid(response)
            if found != "unknown":
                pid = found
            return diagnosis, pid

    # If 6 turns pass without DIAGNOSIS, use last response
    last  = conversation[-1].replace("Assistant: ", "")
    found = _extract_pid(" ".join(conversation))
    if found != "unknown":
        pid = found
    return last, pid


# ── RCA writer ────────────────────────────────────────────────
def _write_rca(diagnosis: str, pid: str) -> str:
    """Turns the technical diagnosis into plain English for normal users."""
    prompt = f"""Write a short report for someone who is NOT a tech expert.

What the investigation found:
{diagnosis}

The problem process ID (PID) is: {pid}

Write EXACTLY 3 sentences:
1. What the person would have noticed (computer running slow, freezing, etc.)
2. Which app caused the problem — include its name and PID number
3. What they should do right now — one simple action

Keep it under 80 words. Use plain English. No technical jargon.
Example style: "Your computer was slowing down because Chrome (PID 4521) was using too much memory. Close Chrome and reopen it to fix the problem." """

    return _call_ollama(prompt)


# ── PID extractor ─────────────────────────────────────────────
def _extract_pid(text: str) -> str:
    """Extract the first real PID from text, skipping Windows pseudo-processes."""
    # PID= or PID: pattern from tool output
    for m in re.finditer(r"PID[=:\s]+(\d+)", text, re.IGNORECASE):
        p = m.group(1)
        if p not in ("0", "4"):
            return p
    # Any 3-6 digit standalone number
    for m in re.finditer(r"\b(\d{3,6})\b", text):
        p = m.group(1)
        if p not in ("0", "4", "100"):
            return p
    return "unknown"


# ── public entry point ────────────────────────────────────────
def run_diagnostic_crew(context: dict) -> dict:
    """
    Called by gui_app.py when an incident is detected.
    Returns: { diagnostic_result, rca, pid, context }
    """
    diagnosis, pid = _run_agentic_loop(context)
    rca            = _write_rca(diagnosis, pid)

    return {
        "diagnostic_result": diagnosis,
        "rca":               rca,
        "pid":               pid,
        "context":           context,
    }