"""
detective_agent.py — CrewAI-backed incident detective
──────────────────────────────────────────────────────
Runs the incident response crew on a local Ollama model through CrewAI.
The app expects Python 3.12 for CrewAI compatibility.

Setup (one time):
    1. Install CrewAI in Python 3.12
    2. Make sure Ollama is running locally
    3. Pull a model such as qwen2.5:0.5b
"""
import re
import os
import time
import sys
import socket
import subprocess
import urllib.request
import urllib.error
import json
import shutil
import uuid
from urllib.parse import urlparse
from typing import Any

from env_loader import ensure_env_loaded
from pydantic import BaseModel, Field
from tool_runner import ToolRunner
from incident_memory import get_incident_memory
from structured_logger import log_incident_event
from jira_integration import create_jira_ticket_if_enabled
from metrics_exporter import observe_incident

if sys.version_info >= (3, 14):
    # CrewAI currently depends on Pydantic v1 internals that are not compatible with Python 3.14+.
    # Skip native import here so we do not emit runtime warnings; the Python 3.12 bridge path will be used.
    CrewAgent = Crew = LLM = Process = Task = None
    _CREWAI_IMPORT_ERROR = RuntimeError(
        f"CrewAI native runtime is disabled on Python {sys.version_info.major}.{sys.version_info.minor}; use Python 3.12 bridge."
    )
else:
    try:
        from crewai import Agent as CrewAgent, Crew, LLM, Process, Task
    except Exception as crewai_import_error:  # pragma: no cover - environment dependent
        CrewAgent = Crew = LLM = Process = Task = None
        _CREWAI_IMPORT_ERROR = crewai_import_error
    else:
        _CREWAI_IMPORT_ERROR = None

_PY312_BRIDGE_CODE = (
    "import json,sys;"
    "from detective_agent import _run_diagnostic_crew_native;"
    "ctx=json.loads(sys.stdin.read());"
    "print(json.dumps(_run_diagnostic_crew_native(ctx)))"
)

_runner = ToolRunner()
ensure_env_loaded()

# ── config ────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
OLLAMA_FALLBACK_MODEL = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5:0.5b")
OLLAMA_EXE = os.getenv("OLLAMA_EXE", "").strip()
OLLAMA_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT_S", "240"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "160"))
OLLAMA_REASONING_TURNS = max(2, int(os.getenv("OLLAMA_REASONING_TURNS", "3")))
TRIAGE_MODEL = os.getenv("OLLAMA_TRIAGE_MODEL", OLLAMA_MODEL)
DEEP_RCA_MODEL = os.getenv("OLLAMA_DEEP_RCA_MODEL", OLLAMA_MODEL)


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
def _call_ollama(prompt: str, model: str | None = None) -> str:
    """
    Sends a prompt to the local Ollama server and returns the response.
    Ollama runs on port 11434 by default.
    """
    _start_ollama_if_needed()

    try:
        active_model = model or OLLAMA_MODEL
        return _post_ollama(active_model, prompt)

    except (TimeoutError, socket.timeout) as e:
        if OLLAMA_FALLBACK_MODEL and OLLAMA_FALLBACK_MODEL != (model or OLLAMA_MODEL):
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

        if low_memory and OLLAMA_FALLBACK_MODEL and OLLAMA_FALLBACK_MODEL != (model or OLLAMA_MODEL):
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


class IncidentPlan(BaseModel):
    urgency: str = "normal"
    specialists: list[str] = Field(default_factory=lambda: ["process", "resources", "reporter"])
    focus: str = "general"
    rationale: str = ""
    followup_hours: int = 2


class IncidentVerdict(BaseModel):
    candidate_pid: str = "N/A"
    confidence: str = "medium"
    verdict: str = ""
    notes: str = ""


def _create_crewai_llm() -> LLM:
    model_choice = DEEP_RCA_MODEL or OLLAMA_MODEL
    model_name = model_choice if model_choice.startswith("ollama/") else f"ollama/{model_choice}"
    return LLM(
        model=model_name,
        base_url=_ollama_base_url(),
        api_key=os.getenv("CREWAI_API_KEY", "ollama"),
        temperature=0.1,
        timeout=OLLAMA_TIMEOUT_S,
    )


def _collect_evidence(context: dict) -> tuple[dict[str, str], list[str], list[str]]:
    evidence: dict[str, str] = {}
    tool_trace: list[str] = []
    candidate_pids: list[str] = []

    process_out = TOOLS["check_processes"]()
    evidence["check_processes"] = process_out
    tool_trace.append("CREWAI evidence: check_processes")
    pid = _extract_pid(process_out)
    if pid != "unknown":
        candidate_pids.append(pid)
        tool_trace.append(f"PID_CANDIDATE: {pid} (check_processes)")

    top_out = TOOLS["inspect_top_process"]()
    evidence["inspect_top_process"] = top_out
    tool_trace.append("CREWAI evidence: inspect_top_process")
    pid2 = _extract_pid(top_out)
    if pid2 != "unknown":
        candidate_pids.append(pid2)
        tool_trace.append(f"PID_CANDIDATE: {pid2} (inspect_top_process)")

    ev = context.get("primary_event", "NORMAL")
    if ev in {"CPU_SPIKE", "MEMORY_SPIKE", "DISK_SPIKE", "HIGH_PROCESS_COUNT", "LOG_ALERT"}:
        mem_out = TOOLS["check_memory"]()
        disk_out = TOOLS["check_disk"]()
        evidence["check_memory"] = mem_out
        evidence["check_disk"] = disk_out
        tool_trace.append("CREWAI evidence: check_memory")
        tool_trace.append("CREWAI evidence: check_disk")
        pid3 = _extract_pid(mem_out + "\n" + disk_out)
        if pid3 != "unknown":
            candidate_pids.append(pid3)
            tool_trace.append(f"PID_CANDIDATE: {pid3} (resources)")

    if ev in {"HIGH_PROCESS_COUNT", "LOG_ALERT"}:
        open_files = TOOLS["check_open_files"]()
        net = TOOLS["check_network"]()
        evidence["check_open_files"] = open_files
        evidence["check_network"] = net
        tool_trace.append("CREWAI evidence: check_open_files")
        tool_trace.append("CREWAI evidence: check_network")
        pid4 = _extract_pid(open_files + "\n" + net)
        if pid4 != "unknown":
            candidate_pids.append(pid4)
            tool_trace.append(f"PID_CANDIDATE: {pid4} (forensics)")

    return evidence, tool_trace, candidate_pids


def _build_crewai_agents(llm: LLM) -> dict[str, CrewAgent]:
    return {
        "manager": CrewAgent(
            role="Incident Manager",
            goal="Plan the incident response from the collected system evidence.",
            backstory="You coordinate a local Windows incident-response crew and keep the investigation focused.",
            allow_delegation=False,
            verbose=False,
            llm=llm,
        ),
        "process": CrewAgent(
            role="Process Specialist",
            goal="Identify the most suspicious process and candidate PID from the process evidence.",
            backstory="You read process lists carefully and avoid guessing when the evidence is weak.",
            allow_delegation=False,
            verbose=False,
            llm=llm,
        ),
        "resources": CrewAgent(
            role="Resources Specialist",
            goal="Explain whether CPU, memory, or disk pressure is driving the incident.",
            backstory="You translate resource pressure into actionable findings.",
            allow_delegation=False,
            verbose=False,
            llm=llm,
        ),
        "verifier": CrewAgent(
            role="PID Verifier",
            goal="Cross-check the candidate PID against all evidence and reject weak matches.",
            backstory="You are strict about evidence quality and only accept a PID when the data supports it.",
            allow_delegation=False,
            verbose=False,
            llm=llm,
        ),
        "reporter": CrewAgent(
            role="Incident Reporter",
            goal="Write a concise diagnosis and plain-English summary for the operator.",
            backstory="You turn technical findings into a clear next action for the user.",
            allow_delegation=False,
            verbose=False,
            llm=llm,
        ),
    }


def _run_crewai_crew(context: dict) -> tuple[str, str, list[str]]:
    if Crew is None or Task is None or Process is None:
        raise RuntimeError(
            "CrewAI is not available in this Python environment. Use Python 3.12 with crewai installed.\n"
            f"Interpreter: {sys.executable}\n"
            f"Original import error: {_CREWAI_IMPORT_ERROR}"
        )

    ev = context["primary_event"]
    cpu = context["cpu_usage"]
    ram = context["memory_usage"]
    disk = context["disk_usage"]
    logs = "\n".join(context.get("recent_logs", [])) or "None"

    evidence, tool_trace, candidate_pids = _collect_evidence(context)
    llm = _create_crewai_llm()
    agents = _build_crewai_agents(llm)
    evidence_blob = json.dumps(evidence, indent=2, ensure_ascii=False)

    manager_task = Task(
        description=(
            "Create an incident plan from this Windows evidence.\n\n"
            f"Context:\n{json.dumps(context, indent=2, ensure_ascii=False)}\n\n"
            f"Evidence:\n{evidence_blob}\n\n"
            "Return JSON with urgency, specialists, focus, rationale, and followup_hours."
        ),
        expected_output="A JSON plan for the incident response.",
        agent=agents["manager"],
        output_json=IncidentPlan,
    )

    process_task = Task(
        description=(
            "Analyze the process evidence and identify the most likely culprit PID.\n\n"
            f"Evidence:\n{evidence_blob}\n\n"
            "Focus on check_processes and inspect_top_process. Do not guess if the PID is not supported."
        ),
        expected_output="A short process analysis that names the likely culprit PID when supported.",
        agent=agents["process"],
        context=[manager_task],
    )

    resources_task = Task(
        description=(
            "Analyze the resource pressure and explain whether CPU, memory, or disk is the main issue.\n\n"
            f"Evidence:\n{evidence_blob}\n\n"
            "Use only the supplied evidence. Mention the pressure pattern and its likely effect."
        ),
        expected_output="A short resource analysis with the most relevant pressure signal.",
        agent=agents["resources"],
        context=[manager_task, process_task],
    )

    verifier_task = Task(
        description=(
            "Verify the candidate PID against the evidence and reject weak matches.\n\n"
            f"Candidate PIDs from evidence: {candidate_pids or ['N/A']}\n\n"
            f"Evidence:\n{evidence_blob}\n\n"
            "Return a concise verdict that starts with VERIFIED or NOT VERIFIED."
        ),
        expected_output="A verification note that confirms or rejects the candidate PID.",
        agent=agents["verifier"],
        context=[manager_task, process_task, resources_task],
        output_json=IncidentVerdict,
    )

    reporter_task = Task(
        description=(
            "Write the final incident diagnosis for the operator.\n\n"
            f"Alert: {ev}\nCPU={cpu}%\nRAM={ram}%\nDisk={disk}%\nLogs={logs}\n\n"
            f"Evidence:\n{evidence_blob}\n\n"
            "Manager plan, specialist findings, and verifier output are available in context.\n"
            "Return exactly one DIAGNOSIS line plus 2-4 short bullets."
        ),
        expected_output="A technical diagnosis with one DIAGNOSIS line and short bullets.",
        agent=agents["reporter"],
        context=[manager_task, process_task, resources_task, verifier_task],
    )

    crew = Crew(
        agents=list(agents.values()),
        tasks=[manager_task, process_task, resources_task, verifier_task, reporter_task],
        process=Process.sequential,
        verbose=False,
        memory=False,
        cache=False,
    )

    crew_result = crew.kickoff()
    manager_output = getattr(manager_task.output, "json_dict", None) or _default_crew_plan(context)
    plan = _extract_json_object(json.dumps(manager_output), _default_crew_plan(context))

    diagnosis = getattr(reporter_task.output, "raw", "") or getattr(crew_result, "raw", "") or str(crew_result)
    verifier_raw = getattr(verifier_task.output, "raw", "")
    process_raw = getattr(process_task.output, "raw", "")
    resources_raw = getattr(resources_task.output, "raw", "")

    final_pid = next((p for p in candidate_pids if p not in {"0", "4"}), "N/A")
    pid_from_text = _extract_pid("\n".join([process_raw, resources_raw, verifier_raw, diagnosis]))
    if pid_from_text != "unknown":
        final_pid = pid_from_text
    if not str(final_pid).isdigit():
        final_pid = _fallback_pid_from_tools()
        tool_trace.append(f"PID_FALLBACK: {final_pid} (from check_processes)")
    else:
        tool_trace.append(f"PID_VERIFIED: {final_pid}")

    tool_trace.append(f"CREWAI manager plan: {plan.get('urgency', 'normal')} / {plan.get('focus', 'general')}")
    tool_trace.append(f"CREWAI process specialist: {process_raw[:220]}")
    tool_trace.append(f"CREWAI resources specialist: {resources_raw[:220]}")
    tool_trace.append(f"CREWAI verifier: {verifier_raw[:220]}")
    tool_trace.append(f"CREWAI reporter: {diagnosis[:220]}")

    return diagnosis, final_pid, tool_trace


def _python312_commands() -> list[list[str]]:
    """Build command candidates for running a helper in Python 3.12."""
    custom = os.getenv("CREWAI_PYTHON_EXE", "").strip()
    candidates: list[list[str]] = []
    if custom:
        candidates.append([custom])

    candidates.append([r"C:\Users\shankar\AppData\Local\Programs\Python\Python312\python.exe"])

    py_launcher = shutil.which("py")
    if py_launcher:
        candidates.append([py_launcher, "-3.12"])

    return candidates


def _run_crewai_via_python312(context: dict) -> dict:
    """Run the CrewAI diagnostic in a Python 3.12 subprocess and return parsed JSON."""
    payload = json.dumps(context)
    errors: list[str] = []

    for base_cmd in _python312_commands():
        try:
            proc = subprocess.run(
                [*base_cmd, "-c", _PY312_BRIDGE_CODE],
                input=payload,
                text=True,
                capture_output=True,
                timeout=OLLAMA_TIMEOUT_S + 30,
            )
        except FileNotFoundError:
            errors.append(f"not found: {' '.join(base_cmd)}")
            continue
        except Exception as exc:
            errors.append(f"failed to launch {' '.join(base_cmd)}: {exc}")
            continue

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            detail = stderr or stdout or f"exit code {proc.returncode}"
            errors.append(f"{' '.join(base_cmd)} -> {detail}")
            continue

        out = (proc.stdout or "").strip()
        if not out:
            errors.append(f"{' '.join(base_cmd)} -> empty output")
            continue

        # Keep this tolerant: if there is extra logging, parse the last JSON object.
        start = out.find("{")
        end = out.rfind("}")
        if start == -1 or end == -1 or end <= start:
            errors.append(f"{' '.join(base_cmd)} -> invalid JSON output")
            continue

        try:
            return json.loads(out[start:end + 1])
        except Exception as exc:
            errors.append(f"{' '.join(base_cmd)} -> JSON parse error: {exc}")
            continue

    raise RuntimeError(
        "CrewAI is unavailable in this interpreter and auto-bridge to Python 3.12 failed.\n"
        f"Current interpreter: {sys.executable}\n"
        "Bridge attempts:\n- " + "\n- ".join(errors)
    )


def _run_diagnostic_crew_native(context: dict) -> dict:
    """Native path that requires CrewAI import to be available in this interpreter."""
    diagnosis, pid, tool_trace = _run_crewai_crew(context)
    return _finalize_result(context, diagnosis, pid, tool_trace, pipeline_mode="crewai")


def _triage_incident(context: dict) -> dict:
    """Fast triage stage: decide whether deep RCA is required."""
    fallback = {
        "severity": context.get("risk_level", "caution"),
        "deep_rca_required": context.get("risk_level", "safe") != "safe",
        "reason": "Rule-based fallback triage",
    }

    prompt = f"""You are a FAST TRIAGE model for incident routing.

Context:
{json.dumps(context, indent=2)}

Return JSON only with keys:
- severity: safe | caution | dangerous
- deep_rca_required: true or false
- reason: short sentence

Rules:
- deep_rca_required must be true for dangerous.
- deep_rca_required should be false for safe unless logs show hard failure.
"""
    try:
        raw = _call_ollama(prompt, model=TRIAGE_MODEL)
        triage = _extract_json_object(raw, fallback)
        triage.setdefault("severity", fallback["severity"])
        triage.setdefault("deep_rca_required", fallback["deep_rca_required"])
        triage.setdefault("reason", fallback["reason"])
        return triage
    except Exception:
        return fallback


def _enrich_context(context: dict) -> tuple[dict, list[dict], str]:
    enriched = dict(context or {})
    correlation_id = str(enriched.get("correlation_id") or uuid.uuid4())
    enriched["correlation_id"] = correlation_id
    enriched["incident_id"] = str(enriched.get("incident_id") or correlation_id)

    store = get_incident_memory()
    similar = store.find_similar_incidents(enriched, limit=3)
    enriched["similar_incidents"] = similar
    suggestion = store.build_previous_fix_suggestion(similar)
    if suggestion:
        enriched["previously_fixed_by"] = suggestion

    return enriched, similar, suggestion


def _finalize_result(context: dict, diagnosis: str, pid: str, tool_trace: list[str], pipeline_mode: str) -> dict:
    if not str(pid).isdigit():
        pid = _fallback_pid_from_tools()
        tool_trace.append(f"PID_FALLBACK: {pid} (from check_processes)")

    risk = str(context.get("risk_level") or "caution")
    triage = context.get("triage", {}) or {}
    if triage:
        tool_trace.append(
            f"TRIAGE: severity={triage.get('severity', risk)} deep_rca_required={triage.get('deep_rca_required', True)} reason={triage.get('reason', '')}"
        )

    suggestion = context.get("previously_fixed_by", "")
    rca = _write_rca(diagnosis, pid)
    if suggestion:
        rca = f"{rca}\n\n{suggestion}"
        tool_trace.append(f"HISTORICAL_FIX: {suggestion}")

    result = {
        "diagnostic_result": diagnosis,
        "rca": rca,
        "pid": pid,
        "tool_trace": tool_trace,
        "context": context,
        "risk_level": triage.get("severity", risk),
        "incident_id": context.get("incident_id", context.get("correlation_id", "")),
        "correlation_id": context.get("correlation_id", ""),
        "previously_fixed_by": suggestion,
        "pipeline_mode": pipeline_mode,
    }

    observe_incident(context.get("primary_event", "UNKNOWN"), result.get("risk_level", "caution"))

    try:
        store = get_incident_memory()
        incident_id = store.record_incident(context, result)
        result["incident_id"] = incident_id
        log_incident_event(
            "INFO",
            "incident_recorded",
            result.get("correlation_id", ""),
            incident_id=incident_id,
            primary_event=context.get("primary_event", "UNKNOWN"),
            risk_level=result.get("risk_level", "caution"),
            pid=pid,
            pipeline_mode=pipeline_mode,
        )
    except Exception as exc:
        tool_trace.append(f"INCIDENT_MEMORY_WARN: {exc}")

    jira_result = create_jira_ticket_if_enabled(context, result)
    if jira_result.get("created"):
        result["jira_ticket"] = jira_result
        ticket_key = jira_result.get("key", "")
        if ticket_key:
            tool_trace.append(f"JIRA_TICKET: {ticket_key}")
            result["rca"] = f"{result['rca']}\n\nJira ticket: {ticket_key}"
            log_incident_event(
                "INFO",
                "jira_ticket_created",
                result.get("correlation_id", ""),
                incident_id=result.get("incident_id", ""),
                ticket_key=ticket_key,
            )
    else:
        tool_trace.append(f"JIRA_SKIPPED: {jira_result.get('reason', 'not created')}")

    return result


def _run_diagnostic_lightweight(context: dict) -> dict:
    """Lightweight mode that skips CrewAI and uses the compact agentic loop."""
    diagnosis, pid, tool_trace = _run_agentic_loop(context)
    return _finalize_result(context, diagnosis, pid, tool_trace, pipeline_mode="lightweight")


def _extract_json_object(text: str, fallback: dict) -> dict:
    """Best-effort JSON extractor for manager/agent outputs."""
    if not text:
        return fallback
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return fallback
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return fallback


def _default_crew_plan(context: dict) -> dict:
    ev = context.get("primary_event", "NORMAL")
    plan = {
        "urgency": "normal",
        "specialists": ["process", "resources", "reporter"],
        "focus": "general",
        "slack_policy": "single_message_then_wait",
        "followup_hours": 2,
    }
    if ev == "CPU_SPIKE":
        plan.update({"urgency": "urgent", "focus": "cpu"})
    elif ev == "MEMORY_SPIKE":
        plan.update({"urgency": "urgent", "focus": "memory"})
    elif ev == "DISK_SPIKE":
        plan.update({"urgency": "urgent", "focus": "disk"})
    elif ev == "HIGH_PROCESS_COUNT":
        plan.update({"urgency": "urgent", "focus": "process"})
    elif ev == "LOG_ALERT":
        plan.update({"urgency": "urgent", "focus": "logs"})
    return plan


def _manager_agent(context: dict) -> tuple[dict, str]:
    """Manager agent decides which specialist agents to deploy."""
    prompt = f"""You are the MANAGER in a local autonomous Windows incident-response crew.

Your job is to decide which specialist agents should work this incident.

Context:
{json.dumps(context, indent=2)}

Return ONLY JSON with keys:
- urgency: urgent or normal
- specialists: list of roles to run from [process, resources, disk, forensics, reporter]
- focus: one short string describing the main pressure (cpu, memory, disk, processes, logs, general)
- rationale: one sentence
- followup_hours: integer (2 if non-urgent)

Rules:
- Always include process and reporter.
- Include resources when memory or CPU looks bad.
- Include disk when disk usage is suspicious.
- Include forensics when logs or process count look suspicious.
"""
    response = _call_ollama(prompt)
    plan = _extract_json_object(response, _default_crew_plan(context))
    plan.setdefault("specialists", ["process", "resources", "reporter"])
    plan.setdefault("urgency", "normal")
    plan.setdefault("focus", "general")
    plan.setdefault("followup_hours", 2)
    plan.setdefault("rationale", "Crew manager generated a default incident plan.")
    return plan, response


def _specialist_agent(role: str, context: dict, plan: dict, evidence: dict) -> str:
    """Run a specialist agent against the collected evidence."""
    evidence_blob = json.dumps(evidence, indent=2)
    prompt = f"""You are the {role.upper()} specialist in a local autonomous incident-response crew.

Mission:
Investigate the incident and summarize only what your specialty can prove.

Incident context:
{json.dumps(context, indent=2)}

Manager plan:
{json.dumps(plan, indent=2)}

Evidence:
{evidence_blob}

Rules:
- Be concise.
- Do not invent PIDs.
- Use only the evidence provided.
- End with one sentence beginning with RESULT: that states your conclusion.
"""
    return _call_ollama(prompt)


def _run_multi_agent_crew(context: dict) -> tuple[str, str, list[str]]:
    """Local CrewAI-style orchestrator using role-separated Ollama agents."""
    ev = context["primary_event"]
    cpu = context["cpu_usage"]
    ram = context["memory_usage"]
    disk = context["disk_usage"]
    logs = "\n".join(context.get("recent_logs", [])) or "None"

    tool_trace: list[str] = []
    plan, manager_raw = _manager_agent(context)
    tool_trace.append(f"MANAGER_PLAN: {plan.get('urgency', 'normal')} | {plan.get('focus', 'general')}")
    tool_trace.append(f"MANAGER_RAW: {manager_raw[:220]}")

    evidence: dict[str, str] = {}
    candidate_pids: list[str] = []

    # Process specialist is always involved.
    process_out = TOOLS["check_processes"]()
    evidence["check_processes"] = process_out
    tool_trace.append("PROCESS_AGENT: check_processes")
    pid = _extract_pid(process_out)
    if pid != "unknown":
        candidate_pids.append(pid)
        tool_trace.append(f"PID_CANDIDATE: {pid} (check_processes)")

    top_out = TOOLS["inspect_top_process"]()
    evidence["inspect_top_process"] = top_out
    tool_trace.append("PROCESS_AGENT: inspect_top_process")
    pid2 = _extract_pid(top_out)
    if pid2 != "unknown":
        candidate_pids.append(pid2)
        tool_trace.append(f"PID_CANDIDATE: {pid2} (inspect_top_process)")

    if "resources" in plan.get("specialists", []):
        mem_out = TOOLS["check_memory"]()
        evidence["check_memory"] = mem_out
        tool_trace.append("RESOURCE_AGENT: check_memory")
        disk_out = TOOLS["check_disk"]()
        evidence["check_disk"] = disk_out
        tool_trace.append("RESOURCE_AGENT: check_disk")
        pid3 = _extract_pid(mem_out + "\n" + disk_out)
        if pid3 != "unknown":
            candidate_pids.append(pid3)
            tool_trace.append(f"PID_CANDIDATE: {pid3} (resources)")

    if "disk" in plan.get("specialists", []):
        disk_out = evidence.get("check_disk") or TOOLS["check_disk"]()
        evidence["disk_focus"] = disk_out
        tool_trace.append("DISK_AGENT: check_disk")

    if "forensics" in plan.get("specialists", []):
        open_files = TOOLS["check_open_files"]()
        net = TOOLS["check_network"]()
        evidence["check_open_files"] = open_files
        evidence["check_network"] = net
        tool_trace.append("FORENSICS_AGENT: check_open_files")
        tool_trace.append("FORENSICS_AGENT: check_network")
        pid4 = _extract_pid(open_files + "\n" + net)
        if pid4 != "unknown":
            candidate_pids.append(pid4)
            tool_trace.append(f"PID_CANDIDATE: {pid4} (forensics)")

    # Specialist analysis pass.
    process_analysis = _specialist_agent("process", context, plan, {"process": process_out, "top_process": top_out})
    tool_trace.append(f"PROCESS_ANALYST: {process_analysis[:220]}")

    resources_analysis = ""
    if "resources" in plan.get("specialists", []):
        resources_analysis = _specialist_agent(
            "resources",
            context,
            plan,
            {"memory": evidence.get("check_memory", ""), "disk": evidence.get("check_disk", "")},
        )
        tool_trace.append(f"RESOURCE_ANALYST: {resources_analysis[:220]}")

    disk_analysis = ""
    if "disk" in plan.get("specialists", []):
        disk_analysis = _specialist_agent("disk", context, plan, {"disk": evidence.get("disk_focus", "")})
        tool_trace.append(f"DISK_ANALYST: {disk_analysis[:220]}")

    forensics_analysis = ""
    if "forensics" in plan.get("specialists", []):
        forensics_analysis = _specialist_agent(
            "forensics",
            context,
            plan,
            {"open_files": evidence.get("check_open_files", ""), "network": evidence.get("check_network", "")},
        )
        tool_trace.append(f"FORENSICS_ANALYST: {forensics_analysis[:220]}")

    diagnosis_seed = "\n\n".join([x for x in [process_analysis, resources_analysis, disk_analysis, forensics_analysis] if x])

    # Verifier: prefer a PID seen in evidence; otherwise fall back to the top tool PID.
    final_pid = next((p for p in candidate_pids if p not in {"0", "4"}), "N/A")
    if final_pid == "N/A":
        final_pid = _fallback_pid_from_tools()
        tool_trace.append(f"PID_FALLBACK: {final_pid} (from check_processes)")
    else:
        tool_trace.append(f"PID_VERIFIED: {final_pid}")

    verifier_prompt = f"""You are the VERIFIER in a local autonomous Windows crew.

Context:
{json.dumps(context, indent=2)}

Manager plan:
{json.dumps(plan, indent=2)}

Candidate PID: {final_pid}

Evidence summary:
{diagnosis_seed}

Confirm whether the candidate PID is consistent with the evidence. If not, say what is inconsistent.
Return a short sentence starting with VERIFIED:.
"""
    verifier_out = _call_ollama(verifier_prompt)
    tool_trace.append(f"VERIFIER: {verifier_out[:220]}")

    diagnosis_prompt = f"""You are the REPORTER in a local autonomous Windows crew.

Write a technical diagnosis from the evidence below.

Context:
Alert={ev}
CPU={cpu}%
RAM={ram}%
Disk={disk}%
Logs={logs}

Manager plan:
{json.dumps(plan, indent=2)}

Evidence summary:
{diagnosis_seed}

Verifier output:
{verifier_out}

Final PID: {final_pid}

Return exactly one DIAGNOSIS line plus 2-4 short supporting bullets.
"""
    diagnosis = _call_ollama(diagnosis_prompt)
    tool_trace.append(f"REPORTER: {diagnosis[:220]}")

    if not str(final_pid).isdigit():
        final_pid = _fallback_pid_from_tools()
        tool_trace.append(f"PID_FALLBACK: {final_pid} (from check_processes)")

    return diagnosis, final_pid, tool_trace


# ── agentic reasoning loop ────────────────────────────────────
def _run_agentic_loop(context: dict) -> tuple:
    """
    The AI reasoning loop — Ollama reads tool output and
    decides what to investigate next. No hardcoded steps.
    Returns: (diagnosis_text, culprit_pid, tool_trace)
    """
    ev   = context["primary_event"]
    cpu  = context["cpu_usage"]
    ram  = context["memory_usage"]
    disk = context["disk_usage"]
    logs = "\n".join(context["recent_logs"]) or "None"

    conversation = []
    pid = "N/A"
    seen_pids = set()
    tool_trace = []

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
                tool_trace.append(f"TOOL: {tool_name}")
                tool_output = TOOLS[tool_name]()
                # Trust only real PIDs that appear in tool output, not model text.
                found = _extract_pid(tool_output)
                if found != "unknown":
                    seen_pids.add(found)
                    if pid == "N/A":
                        pid = found
                        tool_trace.append(f"PID_CANDIDATE: {pid} (from {tool_name})")
                conversation.append(f"Tool result ({tool_name}):\n{tool_output}")
            else:
                tool_trace.append(f"TOOL_INVALID: {tool_name}")
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
                tool_trace.append(f"PID_CONFIRMED: {pid} (from diagnosis)")
            return diagnosis, pid, tool_trace

    # If 6 turns pass without DIAGNOSIS, use last response
    last  = conversation[-1].replace("Assistant: ", "")
    found = _extract_pid(" ".join(conversation))
    if found != "unknown" and found in seen_pids:
        pid = found
        tool_trace.append(f"PID_CONFIRMED: {pid} (from conversation)")
    return last, pid, tool_trace


# ── RCA writer ────────────────────────────────────────────────
def _write_rca(diagnosis: str, pid: str) -> str:
    """Turns the technical diagnosis into plain English for normal users."""
    return (
        f"Your computer was slowed down by the process behind PID {pid}. "
        f"The crew traced that process during live monitoring, and the diagnosis is: {diagnosis}. "
        "Stop that process now and check whether CPU, memory, and disk return to normal."
    )


# ── PID extractor ─────────────────────────────────────────────
def _extract_pid(text: str) -> str:
    """Extract the first real PID from text, skipping Windows pseudo-processes."""
    # PID= or PID: pattern from tool output
    for m in re.finditer(r"PID[=:\s]+(\d+)", text, re.IGNORECASE):
        p = m.group(1)
        if p not in ("0", "4"):
            return p
    # Explicit process-style rows such as "PID 1234" / "pid:1234" / "process 1234"
    for m in re.finditer(r"\b(?:process|proc|pid)\b\D{0,8}(\d{2,7})\b", text, re.IGNORECASE):
        p = m.group(1)
        if p not in ("0", "4"):
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
    Returns: { diagnostic_result, rca, pid, tool_trace, context }
    """
    context, similar, suggestion = _enrich_context(context)
    log_incident_event(
        "INFO",
        "incident_detected",
        context.get("correlation_id", ""),
        primary_event=context.get("primary_event", "UNKNOWN"),
        risk_level=context.get("risk_level", "caution"),
        similar_found=len(similar),
    )

    mode = os.getenv("SYSADMIN_DIAGNOSTIC_MODE", "crewai").strip().lower()
    if mode in {"lightweight", "lite", "agentic-loop", "legacy"}:
        return _run_diagnostic_lightweight(context)

    triage = _triage_incident(context)
    context["triage"] = triage
    if suggestion:
        context.setdefault("steps_taken", []).append(suggestion)

    deep_required = bool(triage.get("deep_rca_required", True))
    if not deep_required:
        return _run_diagnostic_lightweight(context)

    if Crew is None:
        return _run_crewai_via_python312(context)
    return _run_diagnostic_crew_native(context)