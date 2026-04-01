"""
detective_agent.py
──────────────────
CrewAI Detective + Reporter agents — powered by Google Gemini (free).
Requires: pip install crewai crewai-tools langchain-google-genai google-generativeai
.env:      GEMINI_API_KEY=your-key-from-aistudio.google.com
"""
import os
from dotenv import load_dotenv
load_dotenv()

from crewai import Agent, Task, Crew, Process
from crewai.tools import tool
from tool_runner import ToolRunner

_runner = ToolRunner()

# ── Gemini LLM setup ─────────────────────────────────────────
# CrewAI needs the API key in the environment AND as a string model name.
# Format: "gemini/gemini-1.5-flash"  (CrewAI's LiteLLM format)
def _get_llm_string() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY not found.\n"
            "Get a free key at: aistudio.google.com/apikey\n"
            "Add to .env:  GEMINI_API_KEY=your-key-here"
        )
    os.environ["GEMINI_API_KEY"] = key
    return "gemini/gemini-2.0-flash"   # current stable free model

# ── tool wrappers ──────────────────────────────────────────────
@tool("check_processes")
def check_processes(x: str = "") -> str:
    """Top 5 real processes by CPU usage (System Idle excluded).
    Call this FIRST on any CPU or memory spike to find the culprit."""
    return _runner.check_processes()

@tool("check_memory")
def check_memory(x: str = "") -> str:
    """Full RAM summary: total, used, available, swap.
    Call this when memory usage is above 85%."""
    return _runner.check_memory()

@tool("check_disk")
def check_disk(x: str = "") -> str:
    """Disk usage for all Windows drives (C:, D:, etc).
    Call this when disk usage is above 90%."""
    return _runner.check_disk()

@tool("inspect_top_process")
def inspect_top_process(x: str = "") -> str:
    """Deep-inspect the highest CPU process: PID, EXE path, threads, command line.
    Call this after check_processes to investigate the top suspect."""
    return _runner.inspect_top_process()

@tool("check_open_files")
def check_open_files(x: str = "") -> str:
    """Count open file handles and network connections for the top process.
    Call this after inspect_top_process to detect file descriptor leaks."""
    return _runner.check_open_files()

@tool("check_network")
def check_network(x: str = "") -> str:
    """Active TCP/UDP connections and listening ports.
    Call this when network anomalies or CONNECTION_REFUSED errors appear."""
    return _runner.check_network()

# ── agent definitions ─────────────────────────────────────────
def _make_agents(llm: str):
    detective = Agent(
        role="Senior Windows SRE",
        goal=(
            "Autonomously diagnose Windows system anomalies by chaining tool "
            "calls in a logical sequence. Always start with check_processes, "
            "then narrow down using inspect_top_process and check_open_files. "
            "Identify the exact PID, executable name, and root behaviour. "
            "Never suggest PID 0 or System Idle Process — those are not real processes."
        ),
        backstory=(
            "You are a 10-year Windows SRE veteran. You know the difference between a "
            "legitimate svchost.exe and a rogue python.exe eating RAM. "
            "You run tools, read their output carefully, and reason step by step. "
            "You never guess — you always verify with another tool call."
        ),
        tools=[check_processes, check_memory, check_disk,
               inspect_top_process, check_open_files, check_network],
        verbose=True,
        allow_delegation=False,
        llm=llm,
        max_iter=8,   # max tool calls before forced conclusion
    )

    reporter = Agent(
        role="Incident Reporter",
        goal=(
            "Write a 3-sentence Root Cause Analysis that a non-technical person "
            "can understand and act on immediately."
        ),
        backstory=(
            "You translate raw diagnostic findings into plain English reports. "
            "You always include the exact process name and what the user should do."
        ),
        verbose=False,
        allow_delegation=False,
        llm=llm,
    )

    return detective, reporter

# ── main entry point ──────────────────────────────────────────
def run_diagnostic_crew(context: dict) -> dict:
    """
    Called from gui_app.py WatcherWorker._run_agent().
    Returns dict with keys: diagnostic_result, rca, pid, context
    """
    llm = _get_llm_string()          # "gemini/gemini-1.5-flash"
    detective, reporter = _make_agents(llm)

    ev   = context["primary_event"]
    cpu  = context["cpu_usage"]
    ram  = context["memory_usage"]
    disk = context["disk_usage"]
    logs = "\n".join(context["recent_logs"]) or "None"

    # ── Task 1: Detective diagnoses ───────────────────────────
    diag_task = Task(
        description=f"""
A Windows system anomaly has been detected. Here is the situation:

PRIMARY EVENT : {ev}
CPU Usage     : {cpu}%
Memory Usage  : {ram}%
Disk Usage    : {disk}%
Recent logs   :
{logs}

Your job:
1. Run check_processes to find which REAL process (not System Idle, not PID 0) 
   is consuming the most resources.
2. Run inspect_top_process to get its full details.
3. Run additional tools (check_memory, check_disk, check_open_files) based 
   on what you find.
4. Return a structured summary with:
   - Culprit PID and process name
   - What the process is doing
   - Why it is causing the anomaly
   - Severity: LOW / MEDIUM / HIGH / CRITICAL
""",
        expected_output=(
            "A structured diagnostic report with: culprit PID, process name (exe), "
            "what it is doing, root cause of the anomaly, and severity level."
        ),
        agent=detective,
    )

    diag_result = str(Crew(
        agents=[detective],
        tasks=[diag_task],
        process=Process.sequential,
        verbose=True,
    ).kickoff())

    # Extract PID from diagnostic result
    pid = _extract_pid(diag_result)

    # ── Task 2: Reporter writes RCA ───────────────────────────
    rca_task = Task(
        description=f"""
The diagnostic investigation found the following:

{diag_result}

Write a Root Cause Analysis in EXACTLY this format — 3 plain English sentences:
1. What happened (the symptom a normal person would notice).
2. Why it happened (name the process, its PID, and what it was doing).
3. What to do right now (one clear action the user should take).

Keep it under 80 words. Use simple language — imagine explaining to someone 
who is not a programmer.
""",
        expected_output=(
            "3 plain-English sentences: symptom, root cause with PID and process name, "
            "and recommended action."
        ),
        agent=reporter,
    )

    rca = str(Crew(
        agents=[reporter],
        tasks=[rca_task],
        process=Process.sequential,
        verbose=False,
    ).kickoff())

    return {
        "diagnostic_result": diag_result,
        "rca": rca,
        "pid": pid,
        "context": context,
    }


def _extract_pid(text: str) -> str:
    """Pull first PID number mentioned in diagnostic output."""
    import re
    # look for PID= pattern first (from tool output format)
    m = re.search(r"PID[=:\s]+(\d+)", text)
    if m:
        pid = m.group(1)
        if pid not in ("0", "4"):   # never return pseudo-processes
            return pid
    # fallback: any standalone number that looks like a PID
    for match in re.finditer(r"\b(\d{3,6})\b", text):
        pid = match.group(1)
        if pid not in ("0", "4"):
            return pid
    return "unknown"


    return {"diagnostic_result": diag_result, "rca": rca, "context": context}