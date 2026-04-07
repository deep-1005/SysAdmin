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
import time
import socket
import subprocess
import urllib.request
import urllib.error
import json
from urllib.parse import urlparse
from dotenv import load_dotenv
from tool_runner import ToolRunner

_runner = ToolRunner()
load_dotenv()

# ── config ────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5:0.5b")
OLLAMA_EXE = os.getenv("OLLAMA_EXE", "").strip()
OLLAMA_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT_S", "240"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "160"))
OLLAMA_REASONING_TURNS = max(2, int(os.getenv("OLLAMA_REASONING_TURNS", "3")))


def _resolve_ollama_exe() -> str:
    """Find the Ollama executable, preferring user-provided and E-drive installs."""
    if OLLAMA_EXE and os.path.isfile(OLLAMA_EXE):
        return OLLAMA_EXE

    # User can point to a custom location in env if needed.
    from_env = os.getenv("OLLAMA_PATH", "").strip()
    if from_env and os.path.isfile(from_env):
        return from_env

    candidates = [
        r"E:\\Program Files\\Ollama\\ollama.exe",
        r"E:\\Ollama\\ollama.exe",
        r"E:\\Apps\\Ollama\\ollama.exe",
        r"C:\\Program Files\\Ollama\\ollama.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path

    return ""


def _ollama_base_url() -> str:
    """Convert /api/generate endpoint into base URL used for health checks."""
    parsed = urlparse(OLLAMA_URL)
    if not parsed.scheme or not parsed.netloc:
        return "http://localhost:11434"
    return f"{parsed.scheme}://{parsed.netloc}"


def _is_ollama_running(timeout: int = 3) -> bool:
    try:
        with urllib.request.urlopen(f"{_ollama_base_url()}/api/version", timeout=timeout):
            return True
    except Exception:
        return False


def _start_ollama_if_needed() -> None:
    """Ensure Ollama service is online; auto-start it when installed locally."""
    if _is_ollama_running():
        return

    exe = _resolve_ollama_exe()
    if not exe:
        return

    try:
        # Start detached so GUI/worker threads are not blocked by the server process.
        subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            ),
        )
    except Exception:
        return

    # Wait briefly for startup; most local setups are ready within a few seconds.
    for _ in range(12):
        if _is_ollama_running(timeout=2):
            return
        time.sleep(1)


def _post_ollama(model: str, prompt: str) -> str:
    payload = json.dumps({
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": OLLAMA_NUM_PREDICT,
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "").strip()


# ── core LLM call ─────────────────────────────────────────────
def _call_ollama(prompt: str) -> str:
    """
    Sends a prompt to the local Ollama server and returns the response.
    Ollama runs on port 11434 by default.
    """
    _start_ollama_if_needed()

    try:
        return _post_ollama(OLLAMA_MODEL, prompt)

    except (TimeoutError, socket.timeout) as e:
        if OLLAMA_FALLBACK_MODEL and OLLAMA_FALLBACK_MODEL != OLLAMA_MODEL:
            try:
                return _post_ollama(OLLAMA_FALLBACK_MODEL, prompt)
            except Exception:
                pass
        raise RuntimeError(
            "Ollama request timed out.\n\n"
            f"Current model: {OLLAMA_MODEL}\n"
            f"Timeout: {OLLAMA_TIMEOUT_S}s\n\n"
            "Fix options:\n"
            "1) Increase timeout in .env: OLLAMA_TIMEOUT_S=360\n"
            "2) Reduce generation size: OLLAMA_NUM_PREDICT=160\n"
            "3) Use a smaller model (for low RAM systems): qwen2.5:0.5b\n"
            f"Technical detail: {e}"
        )

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
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            raise RuntimeError(
                "Ollama request timed out while waiting for the model response.\n\n"
                f"Current model: {OLLAMA_MODEL}\n"
                f"Timeout: {OLLAMA_TIMEOUT_S}s\n\n"
                "Try in .env:\n"
                "OLLAMA_TIMEOUT_S=360\n"
                "OLLAMA_NUM_PREDICT=160"
            )
        if "Connection refused" in str(e) or "actively refused" in str(e):
            ollama_exe = _resolve_ollama_exe()
            exe_hint = (
                f"Detected Ollama executable: {ollama_exe}\n"
                if ollama_exe
                else "Could not auto-detect ollama.exe.\n"
            )
            raise RuntimeError(
                "Ollama is not running!\n\n"
                f"{exe_hint}"
                "Fix options:\n"
                "1) Open a terminal and run: ollama serve\n"
                "2) If Ollama is on drive E, set .env like:\n"
                "   OLLAMA_EXE=E:\\Program Files\\Ollama\\ollama.exe\n"
                "   OLLAMA_URL=http://localhost:11434/api/generate"
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

TOOL_DOCS_COMPACT = """
Tools: check_processes, inspect_top_process, check_memory, check_disk, check_open_files, check_network.
Rules: first call check_processes. Avoid PID 0 and System Idle Process. End with DIAGNOSIS: ...
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
    pid = "N/A"
    seen_pids = set()

    # Opening prompt — give the AI the situation
    conversation.append(f"""You are a Windows system expert diagnosing a computer problem.

{TOOL_DOCS_COMPACT}

Issue:
  Alert : {ev}
  CPU   : {cpu}%
  RAM   : {ram}%
  Disk  : {disk}%
  Logs  : {logs}

Investigate briefly. Start with check_processes.""")

    # Keep the loop short so the user gets results quickly.
    for turn in range(OLLAMA_REASONING_TURNS):
        full_prompt = "\n\n".join(conversation) + "\n\nYour response:"
        response = _call_ollama(full_prompt)
        conversation.append(f"Assistant: {response}")

        # ── Did the AI call a tool? ───────────────────────────
        tool_match = re.search(r"TOOL:\s*(\w+)", response, re.IGNORECASE)
        if tool_match:
            tool_name = tool_match.group(1).strip().lower()
            if tool_name in TOOLS:
                tool_output = TOOLS[tool_name]()
                # Trust only real PIDs that appear in tool output, not model text.
                found = _extract_pid(tool_output)
                if found != "unknown":
                    seen_pids.add(found)
                    if pid == "N/A":
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
            if found != "unknown" and found in seen_pids:
                pid = found
            return diagnosis, pid

    # If 6 turns pass without DIAGNOSIS, use last response
    last  = conversation[-1].replace("Assistant: ", "")
    found = _extract_pid(" ".join(conversation))
    if found != "unknown" and found in seen_pids:
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

    try:
        return _call_ollama(prompt)
    except Exception:
        # Never block incident reporting on a second LLM call.
        return (
            f"Your system showed an issue that was investigated using live diagnostics. "
            f"The likely process involved is PID {pid}. "
            f"Please stop PID {pid} and monitor whether CPU, memory, and disk usage return to normal."
        )


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


def _fallback_pid_from_tools() -> str:
    """Return top live PID from tool output when model didn't return one."""
    try:
        out = _runner.check_processes()
        found = _extract_pid(out)
        if found != "unknown":
            return found
    except Exception:
        pass
    return "N/A"


# ── public entry point ────────────────────────────────────────
def run_diagnostic_crew(context: dict) -> dict:
    """
    Called by gui_app.py when an incident is detected.
    Returns: { diagnostic_result, rca, pid, context }
    """
    diagnosis, pid = _run_agentic_loop(context)
    if not str(pid).isdigit():
        pid = _fallback_pid_from_tools()
    rca            = _write_rca(diagnosis, pid)

    return {
        "diagnostic_result": diagnosis,
        "rca":               rca,
        "pid":               pid,
        "context":           context,
    }