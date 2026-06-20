# SysAdmin

SysAdmin is a Windows-native system monitoring and incident-response app built in Python. It combines a live terminal dashboard, a native desktop GUI, system tray controls, local AI-assisted incident analysis, and optional observability/integration hooks.

## What it does

- Watches CPU, memory, disk, and running processes
- Detects incidents and builds context automatically
- Runs an AI-assisted diagnostic flow with local Ollama or supported LLM backends
- Shows alerts in a terminal dashboard or a PyQt6 desktop window
- Sends notifications through the system tray and optional Slack alerts
- Exports metrics for Prometheus and Grafana
- Supports Docker for local demo or containerized runs

## Screenshots

Add your four screenshots to the `screenshots/` folder and use the filenames below in this section.

Suggested layout:

- `screenshots/01-dashboard.png`
- `screenshots/02-incident.png`
- `screenshots/03-tray.png`
- `screenshots/04-rca.png`

Example markup:

```md
![Dashboard](screenshots/01-dashboard.png)
![Incident view](screenshots/02-incident.png)
![Tray menu](screenshots/03-tray.png)
![Root cause analysis](screenshots/04-rca.png)
```

## Tech stack

### Core runtime

- Python 3.12
- psutil for host monitoring
- python-dotenv for environment loading
- prometheus-client for metrics export

### User interfaces

- Textual for the full-screen terminal dashboard
- Rich for terminal rendering
- PyQt6 for the native desktop GUI
- pystray for the Windows tray icon and tray actions
- Pillow for tray icon image generation

### AI and incident workflow

- CrewAI for multi-agent incident analysis
- Ollama as the local model server
- Optional LangSmith tracing for agent visibility

### Integrations and ops

- Slack webhooks for notifications
- Jira integration for ticket creation on serious incidents
- Prometheus and Grafana for metrics and dashboards
- Docker and Docker Compose for containerized runs

## Project structure

```text
SysAdmin/
├── app.py                # Textual TUI + tray orchestrator
├── gui_app.py            # Native PyQt6 desktop app
├── tray.py               # System tray controller
├── watcher.py            # Incident watcher
├── context_builder.py    # Builds diagnostic context
├── detective_agent.py    # CrewAI diagnostic crew
├── tool_runner.py        # Safe tool execution layer
├── process_killer.py     # Process termination helpers
├── notifier.py           # Slack notifier
├── metrics_exporter.py   # Prometheus metrics endpoint
├── docker-compose.yml    # Ollama + app services
├── Dockerfile            # Container image for the app
├── requirements.txt      # Main Python dependencies
├── screenshots/          # Add the four README screenshots here
└── logs/                 # Runtime and incident logs
```

## Quick start

```bat
py -3.12 -m pip install -r requirements.txt
```

Create a `.env` file with the values you need. The README assumes a local Ollama setup by default:

```env
OLLAMA_URL=http://localhost:11434/api/generate
OLLAMA_MODEL=qwen2.5:0.5b
OLLAMA_MODELS=E:\ollama\models
# OLLAMA_EXE=E:\Program Files\Ollama\ollama.exe
```

Then run one of the entry points:

```bat
py -3.12 app.py
py -3.12 gui_app.py
```

## Docker

Start the local Ollama service and app with Docker Compose:

```bash
docker compose up -d ollama
docker exec -it sysadmin-ollama ollama pull qwen2.5:0.5b
docker compose run --rm sysadmin
```

The containerized app runs without the tray icon. For full Windows host monitoring and tray integration, run the app natively.

## Keyboard shortcuts

### Terminal dashboard

- `D` simulate a demo spike
- `K` kill the identified culprit process
- `S` send the RCA to Slack
- `M` minimise to tray
- `R` reset the incident
- `Q` quit

## Environment variables

Common settings used by the app:

- `SYSADMIN_LLM`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `SLACK_WEBHOOK_URL`
- `LANGCHAIN_TRACING_V2`
- `LANGCHAIN_API_KEY`
- `PROMETHEUS_ENABLED`
- `PROMETHEUS_PORT`
- `JIRA_ENABLED`
- `JIRA_BASE_URL`
- `JIRA_EMAIL`
- `JIRA_API_TOKEN`
- `JIRA_PROJECT_KEY`
- `JIRA_ISSUE_TYPE`

## Notes

- Use Python 3.12 for the CrewAI-backed workflow.
- If Ollama is installed but no model is present, pull a model first with `ollama pull qwen2.5:0.5b`.
- The codebase includes both a native GUI and a terminal-first experience, so choose the entry point that fits the demo.
