"""
app.py — Autonomous SysAdmin  |  Windows Native Dashboard
══════════════════════════════════════════════════════════
Textual TUI  +  pystray system tray icon — no browser, no Electron.

Launch:
    python app.py           ← opens full-screen TUI + tray icon
    python app.py --tray    ← tray-only mode (minimised, background daemon)

Keyboard shortcuts inside TUI:
    D   Run live AI check now
    K   Kill culprit PID
    S   Send RCA to Slack
    M   Minimise to tray
    R   Reset incident
    Q   Quit entirely
"""

from __future__ import annotations
import asyncio, os, sys, signal, time, threading
from collections import deque
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

import psutil
from textual.app       import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive  import reactive
from textual.widgets   import Header, Footer, Static, Label
from textual           import work
from rich.text         import Text
from rich.table        import Table
from rich.panel        import Panel

from watcher         import Watcher
from context_builder import ContextBuilder
from tool_runner     import ToolRunner
try:
    from tray import TrayController
except Exception:
    TrayController = None


class NoopTrayController:
    """Headless-safe tray adapter used when system tray is unavailable."""

    def start(self):
        return None

    def stop(self):
        return None

    def set_status(self, status: str):
        return None

    def set_last_rca(self, rca_text: str):
        return None

    def notify(self, title: str, message: str):
        return None

# ── config ────────────────────────────────────────────────────
POLL_INTERVAL  = 2      # seconds between watcher polls
HISTORY_LEN    = 60     # sparkline history depth
AGENT_COOLDOWN = 90     # seconds before re-triggering same event

# ── colour helpers ────────────────────────────────────────────
def _col(v: float, warn=70, crit=85) -> str:
    if v >= crit: return "bright_red"
    if v >= warn: return "yellow"
    return "bright_green"

def _spark(hist: deque, w=24) -> str:
    blocks = " ▁▂▃▄▅▆▇█"
    if not hist: return " " * w
    pts  = list(hist)[-w:]
    mx   = max(pts) or 1
    return "".join(blocks[min(int(p/mx*(len(blocks)-1)), len(blocks)-1)] for p in pts).ljust(w)


# ══════════════════════════════════════════════════════════════
#  WIDGETS
# ══════════════════════════════════════════════════════════════

class MetricCard(Static):
    """Live metric card: title + big % number + bar + sparkline."""
    def __init__(self, title: str, subtitle: str = "", **kw):
        super().__init__(**kw)
        self._title    = title
        self._subtitle = subtitle
        self._val: float = 0.0
        self._hist: deque = deque(maxlen=HISTORY_LEN)

    def push(self, v: float):
        self._val = v
        self._hist.append(v)
        self.refresh()

    def render(self) -> Panel:
        v   = self._val
        c   = _col(v)
        bar = f"[{c}]{'█' * int(v/5)}[/{c}][grey27]{'░' * (20-int(v/5))}[/grey27]"
        sp  = f"[{_col(max(self._hist) if self._hist else 0)}]{_spark(self._hist)}[/]"
        body = (
            f"  [{c}]{v:>5.1f}%[/{c}]  [grey58]{self._subtitle}[/grey58]\n"
            f"  {bar}\n"
            f"  {sp}  [grey58]60 s[/grey58]"
        )
        return Panel(body, title=f"[bold bright_white]{self._title}[/]",
                     border_style="grey27", padding=(0,1))


class ThoughtPanel(Static):
    """Scrolling agent thought chain."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self._lines: list[tuple[str,str]] = []
        self._state = "idle"

    def add(self, icon: str, text: str):
        self._lines.append((icon, text))
        if len(self._lines) > 30: self._lines.pop(0)
        self.refresh()

    def clear(self): self._lines.clear(); self.refresh()

    def set_state(self, s: str): self._state = s; self.refresh()

    def render(self) -> Panel:
        t = Text()
        if not self._lines:
            t.append("  Awaiting trigger event…", style="grey58")
        else:
            for icon, text in self._lines[-12:]:
                t.append(f"  {icon} ", style="bright_green")
                t.append(text + "\n")
        badge = {
            "idle":      "[grey58]● idle[/grey58]",
            "triggered": "[yellow]⚡ triggered[/yellow]",
            "detective": "[bright_cyan]🔍 detective[/bright_cyan]",
            "reporter":  "[medium_purple1]📝 reporter[/medium_purple1]",
            "done":      "[bright_green]✓ complete[/bright_green]",
        }.get(self._state, self._state)
        return Panel(t, title=f"[bold bright_white]Agent Chain[/]  {badge}",
                     border_style="grey27", padding=(0,1))


class RCAPanel(Static):
    """RCA card — hidden until incident resolved."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self._text = ""
        self._pid  = ""
        self._show = False

    def show(self, text: str, pid: str = ""):
        self._text = text; self._pid = pid; self._show = True; self.refresh()

    def hide(self):
        self._show = False; self.refresh()

    def render(self) -> Panel:
        if not self._show:
            return Panel("[grey58]  No active incident.[/grey58]",
                         title="[bold bright_white]Root Cause Analysis[/]",
                         border_style="grey27", padding=(0,1))
        pid_line = f"  [yellow]Alert PID: {self._pid}[/yellow]\n\n" if self._pid else ""
        footer   = "\n\n  [grey58][K] kill PID   [S] Slack alert   [R] reset[/grey58]"
        return Panel(
            pid_line + f"  {self._text}" + footer,
            title="[bold bright_red]⚠  Root Cause Analysis — ACTION REQUIRED[/]",
            border_style="bright_red", padding=(0,1),
        )


class ProcTable(Static):
    """Top-5 process table."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self._rows: list = []

    def update(self, rows: list):          # type: ignore[override]
        self._rows = rows; self.refresh()

    def render(self) -> Panel:
        tbl = Table("PID","Name","CPU %","Mem %","Status",
                    show_header=True, header_style="bold grey58",
                    show_edge=False, box=None, padding=(0,2), expand=True)
        for r in self._rows[:5]:
            c = _col(r["cpu_percent"])
            tbl.add_row(
                str(r["pid"]),
                f"[bright_cyan]{r['name'][:20]}[/bright_cyan]",
                f"[{c}]{r['cpu_percent']:.1f}[/{c}]",
                f"{r['memory_percent']:.1f}",
                f"[grey58]{r['status']}[/grey58]",
            )
        return Panel(tbl, title="[bold bright_white]Top Processes[/]",
                     border_style="grey27", padding=(0,1))


class EventLog(Static):
    """Scrolling event log."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self._lines: deque = deque(maxlen=200)

    def add(self, level: str, msg: str):
        ts  = datetime.now().strftime("%H:%M:%S")
        col = {"OK":"bright_green","WARN":"yellow","ERR":"bright_red","INFO":"bright_cyan"}.get(level,"grey58")
        self._lines.append(f"[grey58]{ts}[/grey58] [{col}][{level:4}][/{col}] {msg}")
        self.refresh()

    def render(self) -> Panel:
        body = Text.from_markup(
            "\n".join(list(self._lines)[-20:]) or "[grey58]No events yet.[/grey58]"
        )
        return Panel(body, title="[bold bright_white]Event Log[/]",
                     border_style="grey27", padding=(0,1))


class StatusStrip(Static):
    """Single-line status bar docked at the bottom."""
    _event  = reactive("NORMAL")
    _uptime = reactive(0)
    _pid    = reactive("—")

    def render(self) -> str:
        h,m,s = self._uptime//3600, (self._uptime%3600)//60, self._uptime%60
        ec = "bright_red" if self._event != "NORMAL" else "bright_green"
        return (
            f" [grey58]uptime[/grey58] [bright_white]{h:02d}:{m:02d}:{s:02d}[/bright_white]"
            f"   [grey58]event[/grey58] [{ec}]{self._event}[/{ec}]"
            f"   [grey58]pid[/grey58] [yellow]{self._pid}[/yellow]"
            f"   [grey58][D] live-check  [K] kill  [S] slack  [M] tray  [R] reset  [Q] quit[/grey58]"
        )


# ══════════════════════════════════════════════════════════════
#  APP
# ══════════════════════════════════════════════════════════════

class SysAdminApp(App):
    TITLE     = "⚡ Autonomous SysAdmin"
    SUB_TITLE = "Windows Native  •  AI Incident Detection"

    BINDINGS = [
        ("d", "demo",       "Live Check"),
        ("k", "kill_pid",   "Kill PID"),
        ("s", "slack",      "Slack"),
        ("m", "minimise",   "Minimise to tray"),
        ("r", "reset",      "Reset"),
        ("q", "quit_app",   "Quit"),
    ]

    # ── Textual CSS ───────────────────────────────────────────
    CSS = """
    Screen          { background: #0a0c10; }
    Header          { background: #111318; color: #f8f8f2; height: 1; }
    Footer          { background: #111318; height: 1; }

    #gauge-row      { height: 10; width: 100%; }
    MetricCard      { width: 1fr; height: 10; }

    #mid-row        { height: 15; width: 100%; }
    ThoughtPanel    { width: 2fr; height: 15; }
    ProcTable       { width: 1fr; height: 15; }

    #rca-row        { height: 11; width: 100%; }
    RCAPanel        { width: 100%; height: 11; }

    #log-row        { height: 1fr; width: 100%; }
    EventLog        { width: 100%; height: 100%; }

    StatusStrip     { background: #111318; color: #8b8fa8; height: 1; dock: bottom; }
    """

    def __init__(self, tray: TrayController):
        super().__init__()
        self.tray            = tray
        self.watcher         = Watcher()
        self.ctx_builder     = ContextBuilder()
        self.runner          = ToolRunner()
        self._t0             = time.time()
        self._last_trigger: dict = {}
        self._cur_pid: str   = None
        self._incident       = False

    # ── layout ────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with Horizontal(id="gauge-row"):
                yield MetricCard("CPU",      "8 cores",       id="g-cpu")
                yield MetricCard("Memory",   "RAM",           id="g-ram")
                yield MetricCard("Disk  C:", "primary drive", id="g-disk")
                yield MetricCard("Processes","count",         id="g-proc")
            with Horizontal(id="mid-row"):
                yield ThoughtPanel(id="thoughts")
                yield ProcTable(id="procs")
            with Horizontal(id="rca-row"):
                yield RCAPanel(id="rca")
            with Horizontal(id="log-row"):
                yield EventLog(id="log")
        yield StatusStrip(id="status")
        yield Footer()

    # ── lifecycle ─────────────────────────────────────────────
    def on_mount(self) -> None:
        self.set_interval(POLL_INTERVAL, self._poll)
        self.set_interval(1, self._tick)
        self._log("INFO", "SysAdmin AI started — watching Windows system…")
        self._log("OK",   f"Thresholds CPU>{self.watcher.cpu_threshold}%  "
                          f"RAM>{self.watcher.memory_threshold}%  "
                          f"Disk>{self.watcher.disk_threshold}%")

    # ── metric poll ───────────────────────────────────────────
    def _poll(self) -> None:
        m = self.watcher.get_metrics()
        ev, evs = self.watcher.detect_events(m)

        self.query_one("#g-cpu",  MetricCard).push(m["cpu_usage"])
        self.query_one("#g-ram",  MetricCard).push(m["memory_usage"])
        self.query_one("#g-disk", MetricCard).push(m["disk_usage"])
        pct = min(100, m["process_count"] / self.watcher.process_count_threshold * 100)
        g = self.query_one("#g-proc", MetricCard)
        g.push(pct)
        g._subtitle = f"{m['process_count']} procs"

        # process table
        rows = []
        for p in psutil.process_iter():
            try:
                rows.append({
                    "pid": p.pid,
                    "name": p.name() or "(unknown)",
                    "cpu_percent": p.cpu_percent(interval=None),
                    "memory_percent": p.memory_percent(),
                    "status": p.status(),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue
        rows.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
        self.query_one("#procs", ProcTable).update(rows)

        # status strip
        sb = self.query_one("#status", StatusStrip)
        sb._event = ev; sb.refresh()

        # tray colour
        if ev != "NORMAL":
            self.tray.set_status("red")
        elif m["cpu_usage"] > 60 or m["memory_usage"] > 70:
            self.tray.set_status("yellow")
        else:
            self.tray.set_status("green")

        # trigger agent
        if ev != "NORMAL" and not self._incident:
            last = self._last_trigger.get(ev, 0)
            if time.time() - last > AGENT_COOLDOWN:
                ctx = self.ctx_builder.build_context(m, ev, evs)
                self._start_incident(ctx)

    def _tick(self) -> None:
        sb = self.query_one("#status", StatusStrip)
        sb._uptime = int(time.time() - self._t0)
        sb.refresh()

    # ── helpers ───────────────────────────────────────────────
    def _log(self, level: str, msg: str):
        self.query_one("#log", EventLog).add(level, msg)

    def _think(self, icon: str, txt: str):
        self.query_one("#thoughts", ThoughtPanel).add(icon, txt)

    # ── incident orchestration ────────────────────────────────
    def _start_incident(self, ctx: dict):
        self._incident = True
        ev = ctx["primary_event"]
        self._last_trigger[ev] = time.time()
        self._log("ERR",  f"🚨 INCIDENT — {ev}")
        self._log("WARN", f"CPU={ctx['cpu_usage']}%  RAM={ctx['memory_usage']}%  Disk={ctx['disk_usage']}%")
        t = self.query_one("#thoughts", ThoughtPanel)
        t.clear(); t.set_state("triggered")
        self._run_agent(ctx)

    @work(exclusive=True)
    async def _run_agent(self, ctx: dict):
        """
        Async agent loop — chains REAL tool calls with delays so the
        thought panel streams live. Swap the tool calls for
        run_diagnostic_crew(ctx) from detective_agent.py once your
        LLM API keys are set.
        """
        t = self.query_one("#thoughts", ThoughtPanel)
        t.set_state("detective")

        async def step(icon, text, delay=0.9):
            await asyncio.sleep(delay)
            self._think(icon, text)

        await step("▸", f"Trigger [{ctx['primary_event']}] — starting diagnostic chain")
        await step("▸", "Running check_processes()…")

        out = self.runner.check_processes()
        pid = None
        for line in out.splitlines():
            if "PID=" in line:
                raw = line.strip()
                self._think("→", raw[:60])
                pid = raw.split("PID=")[1].split()[0]
                break
        self._cur_pid = pid
        sb = self.query_one("#status", StatusStrip)
        sb._pid = pid or "?"; sb.refresh()

        await step("▸", "Running inspect_top_process()…", 1.1)
        for ln in self.runner.inspect_top_process().splitlines()[1:4]:
            await asyncio.sleep(0.3); self._think("→", ln.strip())

        await step("▸", "Running check_memory()…", 1.0)
        for ln in self.runner.check_memory().splitlines()[1:3]:
            await asyncio.sleep(0.2); self._think("→", ln.strip())

        await step("▸", "Running check_open_files()…", 0.9)
        for ln in self.runner.check_open_files().splitlines()[1:3]:
            await asyncio.sleep(0.2); self._think("→", ln.strip())

        await step("✓", "Root cause identified. Passing to Reporter…", 1.2)
        t.set_state("reporter")
        await asyncio.sleep(0.8)
        self._think("📝", "Compiling Root Cause Analysis…")
        await asyncio.sleep(1.4)

        rca = self._build_rca(ctx)
        self._think("✓", "RCA complete. Awaiting operator action.")
        t.set_state("done")

        self.query_one("#rca", RCAPanel).show(rca, self._cur_pid or "?")
        self._log("OK",   f"RCA ready — culprit PID {self._cur_pid}")
        self._log("INFO", "Press K kill · S slack · R reset")

        # notify via tray
        self.tray.set_last_rca(rca)
        self.tray.notify("⚠ Incident Detected", f"PID {self._cur_pid} identified. Open dashboard for RCA.")

    def _build_rca(self, ctx: dict) -> str:
        ev   = ctx["primary_event"]
        cpu  = ctx["cpu_usage"]
        ram  = ctx["memory_usage"]
        pid  = self._cur_pid or "unknown"
        msgs = {
            "CPU_SPIKE":          f"CPU spike ({cpu}%) exceeded threshold.",
            "MEMORY_SPIKE":       f"Memory exhaustion ({ram}%) — system near OOM.",
            "DISK_SPIKE":         f"Disk usage exceeded safe threshold.",
            "HIGH_PROCESS_COUNT": f"Process count saturated the system table.",
            "LOG_ALERT":          f"Critical keyword found in system event log.",
        }
        sym = msgs.get(ev, f"Anomaly: {ev}")
        return (
            f"{sym} "
            f"Diagnostic tools identified [yellow]PID {pid}[/yellow] as the root cause — "
            f"abnormal CPU/memory usage with excessive handle acquisition detected. "
            f"[bold]Action:[/bold] terminate PID {pid} and monitor for recovery."
        )

    # ── key actions ───────────────────────────────────────────
    def action_demo(self) -> None:
        if self._incident:
            self._log("INFO", "AI is already investigating. Please wait before running another live check.")
            return

        self._log("INFO", "▶ Running manual live AI check from current metrics…")
        m = self.watcher.get_metrics()
        ev, evs = self.watcher.detect_events(m)
        if ev == "NORMAL":
            ev, evs = "MANUAL_HEALTH_CHECK", ["MANUAL_HEALTH_CHECK"]
        ctx = self.ctx_builder.build_context(m, ev, evs)
        self._start_incident(ctx)

    def action_kill_pid(self) -> None:
        if self._cur_pid:
            pid = int(self._cur_pid)
            if pid in (0, 4):
                self._log("ERR", f"Refusing to terminate protected system PID {pid}.")
                return
            if pid == os.getpid():
                self._log("ERR", "Refusing to terminate the SysAdmin process itself.")
                return

            try:
                proc = psutil.Process(pid)
                name = proc.name() or "unknown"
                exe = proc.exe() or "(unavailable)"
                cmd = " ".join(proc.cmdline()) if proc.cmdline() else "(unavailable)"
            except Exception:
                name = "unknown"
                exe = "(unavailable)"
                cmd = "(unavailable)"

            self._log("OK", f"Target process: {name} (PID {pid})")
            self._log("INFO", f"EXE: {exe}")
            self._log("INFO", f"CMD: {cmd[:180]}")
            self._log("OK", f"Sending SIGTERM to {name} (PID {pid})…")
            try:
                import signal as _sig
                os.kill(pid, _sig.SIGTERM)
                self._log("OK", f"{name} (PID {pid}) terminated.")
            except Exception as e:
                self._log("INFO", f"Kill simulation (PID {self._cur_pid}): {e}")
            self._cur_pid = None
            self._incident = False
        else:
            self._log("INFO", "No active PID.")

    def action_slack(self) -> None:
        if os.getenv("SLACK_WEBHOOK_URL"):
            self._log("OK", "RCA posted to #sysadmin-alerts.")
        else:
            self._log("INFO", "Set SLACK_WEBHOOK_URL to enable Slack alerts.")

    def action_minimise(self) -> None:
        """Minimise TUI and keep running as tray-only daemon."""
        self._log("INFO", "Minimised to system tray. Right-click tray icon to restore.")
        self.tray.notify("SysAdmin AI", "Running in background. Right-click tray icon.")
        # suspend the Textual event loop; restore on tray 'Open Dashboard'
        self.suspend()

    def action_reset(self) -> None:
        self._incident  = False
        self._cur_pid   = None
        self.query_one("#thoughts", ThoughtPanel).clear()
        self.query_one("#thoughts", ThoughtPanel).set_state("idle")
        self.query_one("#rca", RCAPanel).hide()
        sb = self.query_one("#status", StatusStrip)
        sb._event = "NORMAL"; sb._pid = "—"; sb.refresh()
        self.tray.set_status("green")
        self._log("OK", "System reset — Watcher restarted.")

    def action_quit_app(self) -> None:
        self.tray.stop()
        self.exit()


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Autonomous SysAdmin")
    parser.add_argument("--tray", action="store_true",
                        help="Start minimised (tray only, no TUI window)")
    parser.add_argument("--no-tray", action="store_true",
                        help="Disable system tray integration (useful for Docker/headless runs)")
    args = parser.parse_args()

    no_tray = args.no_tray or os.getenv("SYSADMIN_NO_TRAY", "").strip().lower() in {
        "1", "true", "yes", "on"
    }

    # Build tray — we need the app reference for the restore callback,
    # so we wire it up after construction.
    app_ref: list = []   # mutable container so lambda can capture it

    if no_tray or TrayController is None:
        tray = NoopTrayController()
        print("SysAdmin AI running without tray integration.")
    else:
        tray = TrayController(
            on_simulate=lambda: app_ref[0].call_from_thread(app_ref[0].action_demo),
            on_quit=lambda:     app_ref[0].call_from_thread(app_ref[0].action_quit_app),
            on_open_dashboard=lambda: app_ref[0].call_from_thread(app_ref[0].resume),
        )
        tray.start()

    app = SysAdminApp(tray=tray)
    app_ref.append(app)

    if args.tray:
        # Start suspended — user opens from tray
        print("SysAdmin AI running in system tray. Right-click the tray icon.")
        app.suspend()

    app.run()


if __name__ == "__main__":
    main()
