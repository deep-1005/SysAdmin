# ⚡ Autonomous SysAdmin — Windows Native

**Textual TUI + Windows System Tray — no browser, no Electron, pure Python.**

A full-screen terminal dashboard that watches your Windows system,
fires an AI agent crew when something goes wrong, and pings you
via a tray icon notification + Slack message.

---

## Quick start

```bat
:: 1. Install deps
pip install -r requirements.txt

:: 2. Configure .env
:: Make sure these values exist:
:: OLLAMA_URL=http://localhost:11434/api/generate
:: OLLAMA_MODEL=qwen2.5:0.5b
:: OLLAMA_MODELS=E:\ollama\models
:: If Ollama is on drive E, also set:
:: OLLAMA_EXE=E:\Program Files\Ollama\ollama.exe

:: 3. Ensure Ollama model is available
ollama pull qwen2.5:0.5b

:: 4. Launch full dashboard
python app.py

:: 5. OR start as background tray daemon (minimised)
python app.py --tray
```

The app now attempts to auto-start Ollama if it is installed but not running.

---

## Keyboard shortcuts (inside TUI)

| Key | Action |
|-----|--------|
| `D` | **Demo** — simulate a CPU spike right now |
| `K` | **Kill** the identified culprit PID |
| `S` | **Slack** — post RCA to your channel |
| `M` | **Minimise** to system tray (keeps running) |
| `R` | **Reset** — clear incident, restart watcher |
| `Q` | **Quit** entirely |

---

## System tray (right-click menu)

| Menu item | What it does |
|-----------|-------------|
| Open Dashboard | Restores the full Textual TUI |
| Show Last RCA  | Windows toast notification with the RCA text |
| Simulate Spike | Triggers a fake CPU_SPIKE for demo |
| Quit           | Shuts down everything |

---

## Project structure

```
sysadmin/
├── app.py              ← Textual TUI + tray orchestrator (MAIN ENTRY)
├── tray.py             ← pystray Windows tray icon + menu
├── detective_agent.py  ← CrewAI Detective + Reporter agents
├── tool_runner.py      ← Windows-safe read-only tool sandbox
├── watcher.py          ← YOUR ORIGINAL — unchanged
├── context_builder.py  ← YOUR ORIGINAL — unchanged
├── chaos_monkey.py     ← Demo chaos scripts (CPU / RAM / disk / zombie)
├── notifier.py         ← Slack webhook sender
├── requirements.txt
├── .env                ← API keys (never commit this)
└── logs/
    └── system.log
```

---

## Technology stack

### What you're using

| Library | Purpose | Why it matters |
|---------|---------|----------------|
| **Textual** | Full-screen TUI framework | Panels, live reactive widgets, keyboard events — runs natively in Windows Terminal |
| **Rich** | Terminal rendering | Tables, coloured text, sparklines. Textual's rendering engine |
| **pystray** | Windows system tray icon | Taskbar icon with right-click menu + Windows toast notifications |
| **Pillow** | Image creation | pystray needs it to draw the coloured tray icon |
| **psutil** | System metrics | CPU, RAM, Disk, Network, Process list — fully cross-platform |
| **CrewAI** | Multi-agent orchestration | Detective + Reporter roles, tool access, sequential reasoning |
| **LangSmith** | Agent trace viewer | See exactly which tool was called and why at smith.langchain.com |
| **python-dotenv** | Secrets loader | Reads .env file so keys aren't hardcoded |

### Technologies to add next (ranked by impact)

| Library | What it adds | Difficulty |
|---------|-------------|------------|
| **textual-plotext** | Live 60-second CPU/RAM line graphs inside panels | Easy — 1 widget swap |
| **SQLite + peewee** | Case memory — agent checks history before diagnosing | Medium |
| **win10toast / winotify** | Native Windows 10/11 toast notifications (richer than pystray's) | Easy |
| **LangGraph** | Replace CrewAI — non-linear agent loops, retry on uncertainty | Medium |
| **Anthropic Claude** | Swap LLM — better at tool-use constraints than GPT-4o | Easy — 1 env var |
| **schedule** | Daily summary report emailed/Slacked at 9am | Easy |
| **pywin32** | Read Windows Event Log directly (instead of system.log file) | Medium |
| **wmi** | Windows-only deep system info (BIOS, hardware sensors, services) | Medium |

---

## .env file

```env
# ── LLM (pick one) ────────────────────
OPENAI_API_KEY=sk-...
SYSADMIN_LLM=gpt-4o

# OR for Claude:
# ANTHROPIC_API_KEY=sk-ant-...
# SYSADMIN_LLM=claude-3-5-sonnet-20241022

# ── Slack (optional) ──────────────────
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# ── LangSmith tracing (recommended for demos) ──
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls__...
LANGCHAIN_PROJECT=sysadmin-ai
```

---

## Demo script (show-off sequence)

```
Terminal 1:   python app.py
              (full dashboard opens)

Terminal 2:   python chaos_monkey.py cpu
              (CPU burns to ~95%)

Dashboard:    Watch Watcher fire → Detective chain tools live →
              RCA appears → press K to kill → tray turns green
```

Or just press **D** inside the dashboard for an instant simulation.
```
