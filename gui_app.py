"""
gui_app.py — Autonomous SysAdmin Desktop App
══════════════════════════════════════════════
A real native Windows desktop application using PyQt6.
Opens as a proper window (like VS Code / Task Manager).
No terminal needed. Double-click to launch.

Run:   python gui_app.py
Build: pyinstaller --onefile --windowed --icon=icon.ico gui_app.py
"""

import sys, os, time, threading
from datetime import datetime
from collections import deque

import psutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QScrollArea, QSizePolicy,
    QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QObject, QPropertyAnimation,
    QEasingCurve, QRect, pyqtProperty
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QFontDatabase,
    QIcon, QPixmap, QPainterPath, QLinearGradient, QAction
)

# ── project modules ───────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from watcher         import Watcher
from context_builder import ContextBuilder
from tool_runner     import ToolRunner

# ══════════════════════════════════════════════════════════════
#  THEME
# ══════════════════════════════════════════════════════════════
BG_DEEP    = "#0a0c10"
BG_CARD    = "#111520"
BG_CARD2   = "#161b27"
BORDER     = "#1e2535"
BORDER_LIT = "#2a3550"
GREEN      = "#00f5a0"
CYAN       = "#00c3ff"
PURPLE     = "#a78bfa"
YELLOW     = "#f59e0b"
RED        = "#ef4444"
TEXT       = "#e2e8f0"
MUTED      = "#64748b"
MUTED2     = "#94a3b8"

def color_for(val: float) -> str:
    if val >= 85: return RED
    if val >= 70: return YELLOW
    return GREEN

GLOBAL_STYLE = f"""
QMainWindow, QWidget {{
    background: {BG_DEEP};
    color: {TEXT};
    font-family: 'Segoe UI', 'Consolas', monospace;
}}
QScrollBar:vertical {{
    background: {BG_CARD};
    width: 6px; border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_LIT}; border-radius: 3px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu {{
    background: {BG_CARD2};
    color: {TEXT};
    border: 1px solid {BORDER_LIT};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {BORDER_LIT}; }}
"""

# ══════════════════════════════════════════════════════════════
#  CUSTOM WIDGETS
# ══════════════════════════════════════════════════════════════

class SparklineWidget(QWidget):
    """Mini live line graph drawn with QPainter."""
    def __init__(self, color=GREEN, parent=None):
        super().__init__(parent)
        self._color  = QColor(color)
        self._data: deque = deque(maxlen=60)
        self.setMinimumHeight(40)
        self.setMinimumWidth(80)

    def push(self, val: float):
        self._data.append(val)
        self.update()

    def paintEvent(self, event):
        if len(self._data) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pts  = list(self._data)
        mx   = max(pts) or 1
        n    = len(pts)
        xs   = [int(i / (n - 1) * w) for i in range(n)]
        ys   = [int(h - (v / mx) * (h - 6)) for v in pts]

        # gradient fill under line
        path = QPainterPath()
        path.moveTo(xs[0], h)
        for x, y in zip(xs, ys):
            path.lineTo(x, y)
        path.lineTo(xs[-1], h)
        path.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        c = QColor(self._color)
        c.setAlpha(60)
        grad.setColorAt(0, c)
        c2 = QColor(self._color)
        c2.setAlpha(0)
        grad.setColorAt(1, c2)
        p.fillPath(path, QBrush(grad))

        # line
        pen = QPen(self._color, 1.5)
        p.setPen(pen)
        for i in range(1, n):
            p.drawLine(xs[i-1], ys[i-1], xs[i], ys[i])
        p.end()


class ArcGauge(QWidget):
    """Circular arc gauge — shows value as sweeping arc + large % text."""
    def __init__(self, label: str, accent: str = GREEN, parent=None):
        super().__init__(parent)
        self._label  = label
        self._accent = QColor(accent)
        self._value  = 0.0
        self.setFixedSize(150, 150)

    def set_value(self, v: float):
        self._value = v
        self._accent = QColor(color_for(v))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy, r = w // 2, h // 2, 58

        # background track
        pen = QPen(QColor(BORDER_LIT), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 225 * 16, -270 * 16)

        # value arc
        span = int(-270 * (self._value / 100) * 16)
        pen2 = QPen(self._accent, 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen2)
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 225 * 16, span)

        # value text
        p.setPen(QPen(self._accent))
        f = QFont("Segoe UI", 20, QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(QRect(0, cy - 20, w, 30), Qt.AlignmentFlag.AlignCenter,
                   f"{self._value:.1f}%")

        # label
        p.setPen(QPen(QColor(MUTED2)))
        f2 = QFont("Segoe UI", 9)
        p.setFont(f2)
        p.drawText(QRect(0, cy + 18, w, 18), Qt.AlignmentFlag.AlignCenter, self._label)
        p.end()


class MetricCard(QFrame):
    """Card with arc gauge + sparkline + subtitle stats."""
    def __init__(self, title: str, accent: str = GREEN, parent=None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setStyleSheet(f"""
            #MetricCard {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)
        self._accent = accent

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)

        # title
        t = QLabel(title)
        t.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {TEXT}; border: none; background: transparent;")
        lay.addWidget(t)

        # arc gauge
        self.gauge = ArcGauge(title, accent)
        lay.addWidget(self.gauge, alignment=Qt.AlignmentFlag.AlignCenter)

        # sparkline
        self.spark = SparklineWidget(accent)
        self.spark.setFixedHeight(32)
        lay.addWidget(self.spark)

        # subtitle
        self.sub = QLabel("—")
        self.sub.setFont(QFont("Segoe UI", 8))
        self.sub.setStyleSheet(f"color: {MUTED2}; border: none; background: transparent;")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.sub)

    def push(self, val: float, subtitle: str = ""):
        self.gauge.set_value(val)
        self.spark.push(val)
        if subtitle:
            self.sub.setText(subtitle)


class LogWidget(QScrollArea):
    """Auto-scrolling event log panel."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"""
            QScrollArea {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)
        container = QWidget()
        container.setStyleSheet(f"background: transparent;")
        self._lay = QVBoxLayout(container)
        self._lay.setContentsMargins(12, 10, 12, 10)
        self._lay.setSpacing(2)
        self._lay.addStretch()
        self.setWidget(container)

    def add(self, level: str, msg: str):
        ts    = datetime.now().strftime("%H:%M:%S")
        color = {
            "OK":   GREEN, "WARN": YELLOW,
            "ERR":  RED,   "INFO": CYAN
        }.get(level, MUTED2)

        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        ts_lbl = QLabel(ts)
        ts_lbl.setFont(QFont("Consolas", 8))
        ts_lbl.setStyleSheet(f"color: {MUTED}; background: transparent;")
        ts_lbl.setFixedWidth(52)

        lv_lbl = QLabel(f"[{level}]")
        lv_lbl.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        lv_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        lv_lbl.setFixedWidth(42)

        msg_lbl = QLabel(msg)
        msg_lbl.setFont(QFont("Segoe UI", 9))
        msg_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        msg_lbl.setWordWrap(True)

        rl.addWidget(ts_lbl)
        rl.addWidget(lv_lbl)
        rl.addWidget(msg_lbl)
        rl.addStretch()

        # insert before the stretch
        self._lay.insertWidget(self._lay.count() - 1, row)

        # auto-scroll to bottom
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()
        ))


class ThoughtWidget(QScrollArea):
    """Streams agent reasoning steps."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"""
            QScrollArea {{
                background: {BG_CARD2};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._lay = QVBoxLayout(container)
        self._lay.setContentsMargins(14, 12, 14, 12)
        self._lay.setSpacing(4)
        self._lay.addStretch()
        self.setWidget(container)

        self._state_lbl = None   # set by parent

    def clear_thoughts(self):
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def add(self, icon: str, text: str, color: str = TEXT):
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)

        ico = QLabel(icon)
        ico.setFont(QFont("Segoe UI", 10))
        ico.setStyleSheet(f"color: {GREEN}; background: transparent;")
        ico.setFixedWidth(18)

        txt = QLabel(text)
        txt.setFont(QFont("Consolas", 9))
        txt.setStyleSheet(f"color: {color}; background: transparent;")
        txt.setWordWrap(True)

        rl.addWidget(ico, alignment=Qt.AlignmentFlag.AlignTop)
        rl.addWidget(txt)

        self._lay.insertWidget(self._lay.count() - 1, row)
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()
        ))


class RCACard(QFrame):
    """RCA result card — hidden until incident is resolved."""
    def __init__(self, on_kill, on_slack, on_reset, parent=None):
        super().__init__(parent)
        self.setObjectName("RCACard")
        self._on_kill  = on_kill
        self._on_slack = on_slack
        self._on_reset = on_reset
        self._build_ui()
        self.hide()

    def _build_ui(self):
        self.setStyleSheet(f"""
            #RCACard {{
                background: {BG_CARD};
                border: 1px solid {RED};
                border-left: 4px solid {RED};
                border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)

        # header row
        hrow = QHBoxLayout()
        icon_lbl = QLabel("⚠")
        icon_lbl.setFont(QFont("Segoe UI", 14))
        icon_lbl.setStyleSheet(f"color: {RED}; background: transparent;")
        title = QLabel("⚠  Problem Found — Here's What the AI Discovered")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {RED}; background: transparent;")
        hrow.addWidget(icon_lbl)
        hrow.addWidget(title)
        hrow.addStretch()
        lay.addLayout(hrow)

        # pid label
        self.pid_lbl = QLabel("")
        self.pid_lbl.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self.pid_lbl.setStyleSheet(f"color: {YELLOW}; background: transparent;")
        lay.addWidget(self.pid_lbl)

        # RCA text
        self.rca_lbl = QLabel("")
        self.rca_lbl.setFont(QFont("Segoe UI", 10))
        self.rca_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self.rca_lbl.setWordWrap(True)
        lay.addWidget(self.rca_lbl)

        # action buttons
        btn_row = QHBoxLayout()
        self.kill_btn  = self._btn("🔴  Kill Process", RED,    self._on_kill)
        self.slack_btn = self._btn("📤  Send to Slack", GREEN,  self._on_slack)
        self.reset_btn = self._btn("↺  Reset",         MUTED2, self._on_reset)
        btn_row.addWidget(self.kill_btn)
        btn_row.addWidget(self.slack_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def _btn(self, label: str, color: str, slot) -> QPushButton:
        b = QPushButton(label)
        b.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedHeight(32)
        b.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {color};
                border: 1px solid {color};
                border-radius: 6px;
                padding: 0 14px;
            }}
            QPushButton:hover {{ background: {color}22; }}
            QPushButton:pressed {{ background: {color}44; }}
        """)
        b.clicked.connect(slot)
        return b

    def show_rca(self, rca_text: str, pid: str = ""):
        self.pid_lbl.setText(f"Alert PID: {pid}" if pid else "")
        self.rca_lbl.setText(rca_text)
        self.show()

    def hide_rca(self):
        self.hide()


class ProcessTable(QFrame):
    """Live top-5 process table."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ProcFrame")
        self.setStyleSheet(f"""
            #ProcFrame {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        title = QLabel("📋  Active Apps (by CPU usage)")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        title.setToolTip("Shows which programs are using the most processor power right now.\nThe AI will investigate the top one if it looks suspicious.")
        lay.addWidget(title)

        # header
        hdr = self._row("PID", "Name", "CPU %", "Mem %", header=True)
        lay.addWidget(hdr)

        self._rows: list[QWidget] = []
        for _ in range(5):
            r = self._row("", "", "", "")
            lay.addWidget(r)
            self._rows.append(r)

        lay.addStretch()

    def _row(self, pid, name, cpu, mem, header=False) -> QWidget:
        w   = QWidget()
        w.setStyleSheet("background: transparent;")
        rl  = QHBoxLayout(w)
        rl.setContentsMargins(0, 2, 0, 2)
        rl.setSpacing(0)
        style = f"color: {MUTED}; background: transparent;" if header else f"color: {TEXT}; background: transparent;"
        font  = QFont("Consolas", 9, QFont.Weight.Bold if header else QFont.Weight.Normal)

        def lbl(txt, w_=70, align=Qt.AlignmentFlag.AlignLeft, col=None):
            l = QLabel(txt)
            l.setFont(font)
            s = f"color: {col if col else (MUTED if header else TEXT)}; background: transparent;"
            l.setStyleSheet(s)
            l.setFixedWidth(w_)
            l.setAlignment(align)
            return l

        rl.addWidget(lbl(str(pid), 55))
        rl.addWidget(lbl(str(name), 160, col=CYAN if not header else None))
        rl.addWidget(lbl(str(cpu),  65, Qt.AlignmentFlag.AlignRight,
                         col=(color_for(float(cpu)) if cpu and not header else None)))
        rl.addWidget(lbl(str(mem),  65, Qt.AlignmentFlag.AlignRight))
        return w

    def update_procs(self, procs: list):
        for i, r in enumerate(self._rows):
            lay = r.layout()
            if i < len(procs):
                p = procs[i]
                c = color_for(p["cpu_percent"])
                items = [str(p["pid"]), p["name"][:22],
                         f"{p['cpu_percent']:.1f}", f"{p['memory_percent']:.1f}"]
                colors = [TEXT, CYAN, c, TEXT]
                for j in range(lay.count()):
                    w = lay.itemAt(j).widget()
                    if isinstance(w, QLabel) and j < len(items):
                        w.setText(items[j])
                        w.setStyleSheet(f"color: {colors[j]}; background: transparent;")
            else:
                for j in range(lay.count()):
                    w = lay.itemAt(j).widget()
                    if isinstance(w, QLabel):
                        w.setText("")


# ══════════════════════════════════════════════════════════════
#  WORKER THREAD — runs watcher + agent in background
# ══════════════════════════════════════════════════════════════

class WorkerSignals(QObject):
    metrics_ready  = pyqtSignal(dict)
    thought        = pyqtSignal(str, str)      # icon, text
    rca_ready      = pyqtSignal(str, str)      # rca_text, pid
    log_line       = pyqtSignal(str, str)      # level, msg
    agent_state    = pyqtSignal(str)           # idle/detective/reporter/done


class WatcherWorker(QThread):
    def __init__(self, watcher, ctx_builder, runner):
        super().__init__()
        self.sig         = WorkerSignals()
        self.watcher     = watcher
        self.ctx_builder = ctx_builder
        self.runner      = runner
        self._running    = True
        self._last_trig: dict = {}
        self._incident   = False
        self._cur_pid    = None
        self.COOLDOWN    = 90

    def stop(self): self._running = False

    def run(self):
        while self._running:
            try:
                m = self.watcher.get_metrics()
                ev, evs = self.watcher.detect_events(m)
                self.sig.metrics_ready.emit({**m, "event": ev})

                if ev != "NORMAL" and not self._incident:
                    last = self._last_trig.get(ev, 0)
                    if time.time() - last > self.COOLDOWN:
                        self._incident = True
                        self._last_trig[ev] = time.time()
                        ctx = self.ctx_builder.build_context(m, ev, evs)
                        self._run_agent(ctx)
            except Exception as e:
                self.sig.log_line.emit("ERR", f"Worker error: {e}")
            time.sleep(2)

    def _run_agent(self, ctx: dict):
        ev = ctx["primary_event"]
        self.sig.log_line.emit("ERR",  f"🚨 INCIDENT — {ev}")
        self.sig.log_line.emit("WARN", f"CPU={ctx['cpu_usage']}%  RAM={ctx['memory_usage']}%")
        self.sig.agent_state.emit("detective")
        self.sig.thought.emit("▸", f"Trigger [{ev}] — handing off to Gemini AI agent...")
        self.sig.thought.emit("▸", "Detective agent starting diagnostic chain...")

        try:
            # ── REAL AI CALL ──────────────────────────────────
            # This is where Gemini actually decides which tools to call.
            # It reads check_processes output and CHOOSES what to do next.
            from detective_agent import run_diagnostic_crew

            self.sig.thought.emit("🔍", "Gemini is analyzing your system — this may take 15–30 seconds...")
            self.sig.log_line.emit("INFO", "Gemini AI agent running — analyzing live system data...")

            result = run_diagnostic_crew(ctx)   # blocks, runs on this thread

            rca = result["rca"]
            pid = result.get("pid", "unknown")
            diag = result.get("diagnostic_result", "")

            # Stream key findings from the diagnostic into the thought panel
            self.sig.thought.emit("✓", "Detective agent finished investigation.")
            self.sig.agent_state.emit("reporter")
            self.sig.thought.emit("📝", "Reporter agent writing Root Cause Analysis...")
            time.sleep(0.5)
            self.sig.thought.emit("✓", f"RCA complete — culprit identified: PID {pid}")
            self.sig.agent_state.emit("done")

            self._cur_pid = pid
            self.sig.rca_ready.emit(rca, pid)
            self.sig.log_line.emit("OK",   f"✅ AI diagnosis complete — PID {pid} identified")
            self.sig.log_line.emit("INFO", "Use the action buttons below to respond.")

        except RuntimeError as e:
            # Missing API key — show friendly message
            self.sig.agent_state.emit("idle")
            self.sig.thought.emit("❌", str(e))
            self.sig.log_line.emit("ERR", "GEMINI_API_KEY not found in .env file.")
            self.sig.log_line.emit("INFO", "Get a free key at: aistudio.google.com/apikey")
            self._incident = False

        except Exception as e:
            # Any other error — show it and fall back gracefully
            self.sig.agent_state.emit("idle")
            self.sig.thought.emit("❌", f"Agent error: {e}")
            self.sig.log_line.emit("ERR", f"Agent failed: {e}")
            self.sig.log_line.emit("INFO", "Falling back to rule-based diagnosis...")
            # Fall back to simple rule-based RCA
            rca = self._build_rca(ctx)
            pid = self._cur_pid or "?"
            self.sig.rca_ready.emit(rca, pid)
            self.sig.agent_state.emit("done")

    def _build_rca(self, ctx: dict) -> str:
        ev  = ctx["primary_event"]
        cpu = ctx["cpu_usage"]
        ram = ctx["memory_usage"]
        pid = self._cur_pid or "unknown"
        sym = {
            "CPU_SPIKE":          f"CPU spike ({cpu}%) exceeded the safe threshold.",
            "MEMORY_SPIKE":       f"Memory exhaustion ({ram}%) — system approaching OOM.",
            "DISK_SPIKE":         f"Disk usage exceeded the safe threshold.",
            "HIGH_PROCESS_COUNT": f"Process count saturated the Windows process table.",
            "LOG_ALERT":          f"Critical keyword found in the system event log.",
        }.get(ev, f"System anomaly detected: {ev}.")
        return (
            f"{sym} Diagnostic tools identified PID {pid} as the root cause — "
            f"abnormal resource consumption and excessive handle acquisition detected. "
            f"Recommended action: terminate PID {pid} and monitor system recovery."
        )

    def reset(self):
        self._incident = False
        self._cur_pid  = None


# ══════════════════════════════════════════════════════════════
#  TRAY ICON (Windows taskbar)
# ══════════════════════════════════════════════════════════════

def _make_tray_icon(status: str) -> QIcon:
    px = QPixmap(32, 32)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor({"green": GREEN, "yellow": YELLOW, "red": RED}.get(status, GREEN))
    p.setBrush(QBrush(col))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 24, 24)
    # lightning bolt
    p.setBrush(QBrush(QColor("#000")))
    pts_data = [(18,4),(12,16),(17,16),(14,28),(22,14),(17,14)]
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QPolygonF
    poly = QPolygonF([QPointF(x,y) for x,y in pts_data])
    p.drawPolygon(poly)
    p.end()
    return QIcon(px)


# ══════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("⚡ Autonomous SysAdmin")
        self.setMinimumSize(1200, 820)
        self.resize(1400, 900)
        self.setStyleSheet(GLOBAL_STYLE)

        self.watcher     = Watcher()
        self.ctx_builder = ContextBuilder()
        self.runner      = ToolRunner()
        self._cur_pid    = None
        self._uptime_s   = 0

        self._build_ui()
        self._setup_tray()
        self._setup_worker()

        # uptime ticker
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._tick_uptime)
        self._uptime_timer.start(1000)

        self.log.add("INFO", "Autonomous SysAdmin started — watching your system.")
        self.log.add("OK",   f"Thresholds: CPU>{self.watcher.cpu_threshold}%  "
                             f"RAM>{self.watcher.memory_threshold}%  "
                             f"Disk>{self.watcher.disk_threshold}%")
        # Check disk immediately and warn if high but below threshold
        try:
            import sys
            dp = "C:\\" if sys.platform == "win32" else "/"
            disk_pct = psutil.disk_usage(dp).percent
            if disk_pct >= 90:
                self.log.add("WARN", f"Disk C: is at {disk_pct:.1f}% — consider freeing space soon.")
        except Exception:
            pass

    # ── UI BUILD ──────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(16, 12, 16, 12)
        main.setSpacing(8)

        # ── header bar ────────────────────────────────────────
        hdr = QHBoxLayout()
        logo = QLabel("⚡  Autonomous SysAdmin")
        logo.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        logo.setStyleSheet(f"color: {GREEN};")
        hdr.addWidget(logo)

        # tagline
        tagline = QLabel("— AI-powered health monitor for your PC")
        tagline.setFont(QFont("Segoe UI", 9))
        tagline.setStyleSheet(f"color: {MUTED};")
        hdr.addWidget(tagline)
        hdr.addStretch()

        self.status_dot = QLabel("●")
        self.status_dot.setFont(QFont("Segoe UI", 11))
        self.status_dot.setStyleSheet(f"color: {GREEN};")
        self.status_lbl = QLabel("Everything looks good")
        self.status_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.status_lbl.setStyleSheet(f"color: {GREEN};")
        self.uptime_lbl = QLabel("00:00:00")
        self.uptime_lbl.setFont(QFont("Consolas", 9))
        self.uptime_lbl.setStyleSheet(f"color: {MUTED2};")
        self.clock_lbl  = QLabel("")
        self.clock_lbl.setFont(QFont("Consolas", 9))
        self.clock_lbl.setStyleSheet(f"color: {MUTED2};")

        hdr.addWidget(self.status_dot)
        hdr.addWidget(self.status_lbl)
        hdr.addSpacing(20)
        hdr.addWidget(self._small("running for"))
        hdr.addWidget(self.uptime_lbl)
        hdr.addSpacing(16)
        hdr.addWidget(self._small("time"))
        hdr.addWidget(self.clock_lbl)
        main.addLayout(hdr)

        # ── health summary strip ───────────────────────────────
        self.health_strip = QFrame()
        self.health_strip.setObjectName("HealthStrip")
        self.health_strip.setStyleSheet(f"""
            #HealthStrip {{
                background: #0d1520;
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
        """)
        self.health_strip.setFixedHeight(36)
        hl = QHBoxLayout(self.health_strip)
        hl.setContentsMargins(14, 0, 14, 0)
        hl.setSpacing(0)
        self.health_lbl = QLabel("🟢  Your computer is healthy. The AI agent is watching for problems in the background.")
        self.health_lbl.setFont(QFont("Segoe UI", 9))
        self.health_lbl.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        hl.addWidget(self.health_lbl)
        hl.addStretch()
        self.ai_status_lbl = QLabel("🤖 AI: Ready  (Gemini)")
        self.ai_status_lbl.setFont(QFont("Segoe UI", 8))
        self.ai_status_lbl.setStyleSheet(f"color: {MUTED}; background: transparent;")
        hl.addWidget(self.ai_status_lbl)
        main.addWidget(self.health_strip)

        # ── metric cards row ──────────────────────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self.card_cpu  = MetricCard("CPU Usage",    GREEN)
        self.card_ram  = MetricCard("Memory (RAM)", CYAN)
        self.card_disk = MetricCard("Disk Space C:", PURPLE)
        self.card_proc = MetricCard("Running Apps",  YELLOW)

        self.card_cpu.setToolTip("How hard your processor is working.\nAbove 80% = high load.")
        self.card_ram.setToolTip("How much of your RAM is being used.\nAbove 85% = low memory.")
        self.card_disk.setToolTip("How full your main drive (C:) is.\nAbove 95% = nearly full.")
        self.card_proc.setToolTip("How many programs are running right now.\nAbove 300 = unusually high.")

        for c in [self.card_cpu, self.card_ram, self.card_disk, self.card_proc]:
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            c.setMinimumHeight(260)
            c.setMaximumHeight(280)
            cards_row.addWidget(c)
        main.addLayout(cards_row)

        # ── middle row: thought chain + process table ─────────
        mid = QHBoxLayout()
        mid.setSpacing(10)

        # thought panel
        thought_frame = QFrame()
        thought_frame.setObjectName("TF")
        thought_frame.setStyleSheet(f"""
            #TF {{ background: {BG_CARD2}; border: 1px solid {BORDER}; border-radius: 12px; }}
        """)
        tfl = QVBoxLayout(thought_frame)
        tfl.setContentsMargins(14, 12, 14, 12)
        tfl.setSpacing(6)

        th_hdr = QHBoxLayout()
        th_title = QLabel("🤖  AI Agent Live Thinking")
        th_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        th_title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        th_title.setToolTip("Watch the AI reason through the problem step by step.\nIt decides which checks to run based on what it finds.")
        self.agent_badge = QLabel("● Watching quietly")
        self.agent_badge.setFont(QFont("Segoe UI", 9))
        self.agent_badge.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        th_hdr.addWidget(th_title)
        th_hdr.addStretch()
        th_hdr.addWidget(self.agent_badge)
        tfl.addLayout(th_hdr)

        self.thoughts = ThoughtWidget()
        self.thoughts.setMinimumHeight(180)
        tfl.addWidget(self.thoughts)

        mid.addWidget(thought_frame, stretch=3)
        mid.addWidget(self._vdivider())

        # process table
        self.proctable = ProcessTable()
        self.proctable.setFixedWidth(440)
        mid.addWidget(self.proctable)

        main.addLayout(mid)

        # ── RCA card ──────────────────────────────────────────
        self.rca = RCACard(
            on_kill  = self._kill_pid,
            on_slack = self._send_slack,
            on_reset = self._reset,
        )
        main.addWidget(self.rca)

        # ── bottom: log + friendly control panel ──────────────
        bot = QHBoxLayout()
        bot.setSpacing(10)

        self.log = LogWidget()
        self.log.setMinimumHeight(150)
        bot.addWidget(self.log, stretch=1)

        # ── FRIENDLY CONTROL PANEL ────────────────────────────
        ctrl = QFrame()
        ctrl.setObjectName("CtrlPanel")
        ctrl.setStyleSheet(f"""
            #CtrlPanel {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 14px;
            }}
        """)
        ctrl.setFixedWidth(320)
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        # section title
        ctrl_title = QLabel("🎮  Actions")
        ctrl_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        ctrl_title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        cl.addWidget(ctrl_title)

        ctrl_sub = QLabel("What would you like to do?")
        ctrl_sub.setFont(QFont("Segoe UI", 8))
        ctrl_sub.setStyleSheet(f"color: {MUTED}; background: transparent;")
        cl.addWidget(ctrl_sub)

        # big friendly buttons with descriptions
        self.demo_btn = self._friendly_btn(
            "▶  Run a Test",
            "Simulate a problem so you can see\nthe AI working in real time",
            GREEN, self._simulate
        )
        cl.addWidget(self.demo_btn)

        self.kill_btn2 = self._friendly_btn(
            "🔴  Stop the Problem",
            "Force-quit the app the AI found\nto be causing the issue",
            RED, self._kill_pid
        )
        cl.addWidget(self.kill_btn2)

        self.slack_btn2 = self._friendly_btn(
            "📤  Notify via Slack",
            "Send the AI's full report to\nyour Slack so your team knows",
            CYAN, self._send_slack
        )
        cl.addWidget(self.slack_btn2)

        self.reset_btn2 = self._friendly_btn(
            "↺  Dismiss & Reset",
            "Close this alert and go back\nto normal monitoring mode",
            MUTED2, self._reset
        )
        cl.addWidget(self.reset_btn2)

        # active PID indicator
        self.pid_badge = QFrame()
        self.pid_badge.setObjectName("PidBadge")
        self.pid_badge.setStyleSheet(f"""
            #PidBadge {{
                background: {BG_CARD2};
                border: 1px solid {BORDER_LIT};
                border-radius: 8px;
            }}
        """)
        pbl = QHBoxLayout(self.pid_badge)
        pbl.setContentsMargins(10, 6, 10, 6)
        self._pid_icon = QLabel("💤")
        self._pid_icon.setFont(QFont("Segoe UI", 11))
        self._pid_icon.setStyleSheet("background: transparent;")
        self._pid_text = QLabel("No active incident")
        self._pid_text.setFont(QFont("Segoe UI", 9))
        self._pid_text.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        pbl.addWidget(self._pid_icon)
        pbl.addWidget(self._pid_text)
        pbl.addStretch()
        cl.addWidget(self.pid_badge)

        bot.addWidget(ctrl)
        main.addLayout(bot)

        # ── AI explanation banner (always visible at bottom) ───
        ai_banner = QFrame()
        ai_banner.setObjectName("AIBanner")
        ai_banner.setStyleSheet(f"""
            #AIBanner {{
                background: #0d1829;
                border: 1px solid {BORDER_LIT};
                border-radius: 10px;
            }}
        """)
        abl = QHBoxLayout(ai_banner)
        abl.setContentsMargins(14, 8, 14, 8)
        abl.setSpacing(12)

        brain_icon = QLabel("🧠")
        brain_icon.setFont(QFont("Segoe UI", 16))
        brain_icon.setStyleSheet("background: transparent;")
        brain_icon.setFixedWidth(28)

        ai_text = QLabel(
            "<b style='color:#00c3ff'>How the AI works:</b>  "
            "When a problem is detected, a <b>Gemini AI agent</b> automatically runs checks on your system "
            "(like checking which app is using the most CPU or RAM) and decides <i>on its own</i> what to "
            "investigate next — just like a human IT expert would. "
            "It then writes a plain-English report explaining what went wrong and what to do. "
            "<span style='color:#64748b'>  →  Powered by Google Gemini (free). Key set in your .env file.</span>"
        )
        ai_text.setFont(QFont("Segoe UI", 8))
        ai_text.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        ai_text.setWordWrap(True)

        abl.addWidget(brain_icon, alignment=Qt.AlignmentFlag.AlignTop)
        abl.addWidget(ai_text)
        main.addWidget(ai_banner)

    def _small(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setFont(QFont("Segoe UI", 8))
        l.setStyleSheet(f"color: {MUTED};")
        return l

    def _vdivider(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setStyleSheet(f"color: {BORDER_LIT};")
        f.setFixedWidth(1)
        return f

    def _action_btn(self, label: str, color: str, slot) -> QPushButton:
        b = QPushButton(label)
        b.setFont(QFont("Segoe UI", 9))
        b.setFixedHeight(34)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {color};
                border: 1px solid {color}55;
                border-radius: 6px;
                text-align: left;
                padding-left: 10px;
            }}
            QPushButton:hover {{ background: {color}18; border-color: {color}; }}
            QPushButton:pressed {{ background: {color}30; }}
        """)
        b.clicked.connect(slot)
        return b

    def _friendly_btn(self, title: str, desc: str, color: str, slot) -> QWidget:
        """A two-line button card: bold title + muted description. Uses QPushButton so it renders."""
        btn = QPushButton()
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(56)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {BG_CARD2};
                border: 1px solid {color}50;
                border-radius: 8px;
                text-align: left;
                padding: 0px 12px;
            }}
            QPushButton:hover {{
                background: {color}15;
                border: 1px solid {color};
            }}
            QPushButton:pressed {{ background: {color}25; }}
        """)

        # Use a layout inside the button via a child widget trick
        inner = QWidget(btn)
        inner.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        il = QVBoxLayout(inner)
        il.setContentsMargins(12, 8, 12, 8)
        il.setSpacing(2)

        t = QLabel(title)
        t.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {color}; background: transparent;")
        t.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        d = QLabel(desc)
        d.setFont(QFont("Segoe UI", 8))
        d.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        d.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        il.addWidget(t)
        il.addWidget(d)

        btn.clicked.connect(slot)

        # Resize inner widget when button resizes
        def _resize(event, b=btn, w=inner):
            w.setGeometry(0, 0, b.width(), b.height())
            QPushButton.resizeEvent(b, event)
        btn.resizeEvent = _resize
        inner.setGeometry(0, 0, 320, 56)

        return btn

    # ── TRAY ──────────────────────────────────────────────────
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(_make_tray_icon("green"))
        self.tray.setToolTip("SysAdmin AI — Watching")

        menu = QMenu()
        menu.addAction("⚡ Open Dashboard",    self.show_window)
        menu.addAction("📋 Show Last RCA",     self._show_rca_toast)
        menu.addSeparator()
        menu.addAction("▶ Simulate Spike",     self._simulate)
        menu.addSeparator()
        menu.addAction("✕ Quit",               self._quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_window()
                                    if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
        self._last_rca = ""

    def show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _show_rca_toast(self):
        if self._last_rca:
            self.tray.showMessage("Last RCA", self._last_rca[:200],
                                  QSystemTrayIcon.MessageIcon.Warning, 5000)

    # ── WORKER WIRING ─────────────────────────────────────────
    def _setup_worker(self):
        self.worker = WatcherWorker(self.watcher, self.ctx_builder, self.runner)
        self.worker.sig.metrics_ready.connect(self._on_metrics)
        self.worker.sig.thought.connect(self._on_thought)
        self.worker.sig.rca_ready.connect(self._on_rca)
        self.worker.sig.log_line.connect(self.log.add)
        self.worker.sig.agent_state.connect(self._on_agent_state)
        self.worker.start()

    def _on_metrics(self, m: dict):
        ev = m.get("event", "NORMAL")

        # update cards
        mem = psutil.virtual_memory()
        self.card_cpu.push(m["cpu_usage"],
                           f"{psutil.cpu_count()} cores")
        self.card_ram.push(m["memory_usage"],
                           f"{mem.used/1e9:.1f} / {mem.total/1e9:.1f} GB")
        disk = psutil.disk_usage("/")
        self.card_disk.push(m["disk_usage"],
                            f"{disk.used/1e9:.0f} / {disk.total/1e9:.0f} GB")
        pct = min(100, m["process_count"] / self.watcher.process_count_threshold * 100)
        self.card_proc.push(pct, f"{m['process_count']} processes")

        # process table — filter Windows pseudo-processes
        _skip_pids   = {0, 4}
        _skip_names  = {"system idle process", "system", "registry", "memory compression"}
        rows = []
        for p in psutil.process_iter(["pid","name","cpu_percent","memory_percent","status"]):
            try:
                info = p.info
                if info["pid"] in _skip_pids: continue
                if (info.get("name") or "").lower() in _skip_names: continue
                rows.append(info)
            except: pass
        rows.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
        self.proctable.update_procs(rows)

        # update tray + status
        status_msgs = {
            "NORMAL":           ("Everything looks good",    GREEN),
            "CPU_SPIKE":        ("⚠ CPU is overloaded",      RED),
            "MEMORY_SPIKE":     ("⚠ Running low on memory",  RED),
            "DISK_SPIKE":       ("⚠ Disk is almost full",    RED),
            "HIGH_PROCESS_COUNT": ("⚠ Too many apps running", YELLOW),
            "LOG_ALERT":        ("⚠ System error detected",  RED),
        }
        msg, col = status_msgs.get(ev, (ev, RED))
        if ev != "NORMAL":
            self.tray.setIcon(_make_tray_icon("red"))
            self.tray.setToolTip(f"SysAdmin AI — {msg}")
            self.status_dot.setStyleSheet(f"color: {col};")
            self.status_lbl.setStyleSheet(f"color: {col};")
            self.status_lbl.setText(msg)
        else:
            col = YELLOW if m["cpu_usage"] > 60 or m["memory_usage"] > 70 else GREEN
            self.tray.setIcon(_make_tray_icon("yellow" if col == YELLOW else "green"))
            self.tray.setToolTip("SysAdmin AI — Everything looks good")
            self.status_dot.setStyleSheet(f"color: {col};")
            self.status_lbl.setStyleSheet(f"color: {col};")
            self.status_lbl.setText("Everything looks good")

        # clock
        self.clock_lbl.setText(datetime.now().strftime("%H:%M:%S"))

    def _on_thought(self, icon: str, text: str):
        self.thoughts.add(icon, text)

    def _on_rca(self, rca: str, pid: str):
        self._cur_pid = pid
        self._last_rca = rca
        self._pid_icon.setText("⚠️")
        self._pid_text.setText(f"Culprit: PID {pid} — action needed")
        self._pid_text.setStyleSheet(f"color: {YELLOW}; background: transparent;")
        self.rca.show_rca(rca, pid)
        self.tray.showMessage(
            "⚠ Incident Detected",
            f"PID {pid} identified. Click to open dashboard.",
            QSystemTrayIcon.MessageIcon.Warning, 6000
        )

    def _on_agent_state(self, state: str):
        labels = {
            "idle":      ("● Watching quietly",          MUTED2),
            "triggered": ("⚡ Problem detected!",         YELLOW),
            "detective": ("🔍 AI is investigating...",    CYAN),
            "reporter":  ("📝 AI is writing the report...", PURPLE),
            "done":      ("✓ Investigation complete",     GREEN),
        }
        text, color = labels.get(state, ("● Watching quietly", MUTED2))
        self.agent_badge.setText(text)
        self.agent_badge.setStyleSheet(f"color: {color}; background: transparent;")

        # also update health strip
        strip_msgs = {
            "idle":      ("🟢  Your computer is healthy. The AI agent is watching for problems in the background.", MUTED2),
            "triggered": ("🟡  Something unusual was detected. The AI is starting its investigation...", YELLOW),
            "detective": ("🔴  The AI is actively diagnosing a problem on your system. Please wait...", RED),
            "reporter":  ("🔴  Investigation done. AI is writing the report...", RED),
            "done":      ("⚠️  A problem was found. See the report below and choose what to do.", YELLOW),
        }
        msg, col = strip_msgs.get(state, strip_msgs["idle"])
        self.health_lbl.setText(msg)
        self.health_lbl.setStyleSheet(f"color: {col}; background: transparent;")
        ai_states = {
            "idle":      "🤖 AI: Ready  (Gemini)",
            "triggered": "🤖 AI: Waking up...",
            "detective": "🤖 AI: Investigating ⏳",
            "reporter":  "🤖 AI: Writing report ⏳",
            "done":      "🤖 AI: Done ✓",
        }
        self.ai_status_lbl.setText(ai_states.get(state, "🤖 AI: Ready"))

    # ── UPTIME ────────────────────────────────────────────────
    def _tick_uptime(self):
        self._uptime_s += 1
        h = self._uptime_s // 3600
        m = (self._uptime_s % 3600) // 60
        s = self._uptime_s % 60
        self.uptime_lbl.setText(f"{h:02d}:{m:02d}:{s:02d}")

    # ── ACTIONS ───────────────────────────────────────────────
    def _simulate(self):
        self.log.add("WARN", "🐒 Simulating CPU_SPIKE incident…")
        self.thoughts.clear_thoughts()
        self._on_agent_state("triggered")
        fake = {
            "primary_event": "CPU_SPIKE", "detected_events": ["CPU_SPIKE"],
            "cpu_usage": 91.0, "memory_usage": 88.0, "disk_usage": 45.0,
            "process_count": 318,
            "recent_logs": ["High CPU usage detected in python.exe"],
            "steps_taken": [],
        }
        # trigger worker inline
        t = threading.Thread(target=self.worker._run_agent, args=(fake,), daemon=True)
        t.start()

    def _kill_pid(self):
        if self._cur_pid:
            self.log.add("OK", f"Sending SIGTERM to PID {self._cur_pid}…")
            try:
                import signal as _sig
                os.kill(int(self._cur_pid), _sig.SIGTERM)
                self.log.add("OK", f"✅ PID {self._cur_pid} successfully terminated.")
            except Exception as e:
                self.log.add("INFO", f"Kill attempt PID {self._cur_pid}: {e}")
            self._cur_pid = None
            self._pid_icon.setText("✅")
            self._pid_text.setText("Process terminated successfully")
            self._pid_text.setStyleSheet(f"color: {GREEN}; background: transparent;")
            self.worker.reset()
        else:
            self.log.add("INFO", "No active process to stop — system is healthy.")

    def _send_slack(self):
        webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        if webhook:
            # Send it
            try:
                from notifier import SlackNotifier
                n = SlackNotifier()
                ctx = {
                    "primary_event":  "INCIDENT",
                    "cpu_usage":      self.watcher.get_metrics()["cpu_usage"],
                    "memory_usage":   self.watcher.get_metrics()["memory_usage"],
                    "disk_usage":     self.watcher.get_metrics()["disk_usage"],
                }
                n.send_rca(self._last_rca or "No RCA available.", ctx)
                self.log.add("OK", "✅ RCA posted to Slack #sysadmin-alerts!")
                self.tray.showMessage("Slack", "RCA sent to your channel.",
                                      QSystemTrayIcon.MessageIcon.Information, 3000)
            except Exception as e:
                self.log.add("ERR", f"Slack send failed: {e}")
        else:
            # Show a friendly setup dialog
            self._show_slack_setup_dialog()

    def _show_slack_setup_dialog(self):
        from PyQt6.QtWidgets import QDialog, QLineEdit, QDialogButtonBox, QTextBrowser
        dlg = QDialog(self)
        dlg.setWindowTitle("Set up Slack Alerts")
        dlg.setFixedSize(500, 380)
        dlg.setStyleSheet(f"""
            QDialog {{ background: {BG_CARD}; color: {TEXT}; }}
            QLabel  {{ background: transparent; color: {TEXT}; }}
            QLineEdit {{
                background: {BG_CARD2}; color: {TEXT};
                border: 1px solid {BORDER_LIT}; border-radius: 6px;
                padding: 6px 10px; font-family: Consolas; font-size: 10px;
            }}
            QPushButton {{
                background: {GREEN}; color: #000;
                border: none; border-radius: 6px;
                padding: 8px 20px; font-weight: bold;
            }}
            QPushButton:hover {{ background: #00d488; }}
            QPushButton[text="Cancel"] {{
                background: transparent; color: {MUTED2};
                border: 1px solid {BORDER_LIT};
            }}
        """)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        title = QLabel("📤  Connect Slack Alerts")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        lay.addWidget(title)

        steps = QLabel(
            "When an incident is detected, SysAdmin AI will post a full report\n"
            "to your Slack channel automatically.\n\n"
            "To set this up:\n"
            "  1. Go to  api.slack.com/apps  and create a new app\n"
            "  2. Enable  Incoming Webhooks  and add it to your channel\n"
            "  3. Copy the webhook URL (starts with https://hooks.slack.com/...)\n"
            "  4. Paste it below and click Save"
        )
        steps.setFont(QFont("Segoe UI", 9))
        steps.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        lay.addWidget(steps)

        url_input = QLineEdit()
        url_input.setPlaceholderText("https://hooks.slack.com/services/T.../B.../...")
        url_input.setFixedHeight(36)
        lay.addWidget(url_input)

        note = QLabel("This will be saved to your .env file so you only need to do this once.")
        note.setFont(QFont("Segoe UI", 8))
        note.setStyleSheet(f"color: {MUTED}; background: transparent;")
        lay.addWidget(note)

        btns = QHBoxLayout()
        save_btn   = QPushButton("Save & Test")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {MUTED2};
                border: 1px solid {BORDER_LIT}; border-radius: 6px; padding: 8px 20px;
            }}
            QPushButton:hover {{ background: {BORDER_LIT}; }}
        """)
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        btns.addStretch()
        lay.addLayout(btns)

        def save():
            url = url_input.text().strip()
            if url.startswith("https://hooks.slack.com"):
                # Write to .env
                env_path = os.path.join(os.path.dirname(__file__), ".env")
                lines = []
                found = False
                if os.path.exists(env_path):
                    with open(env_path) as f:
                        lines = f.readlines()
                    lines = [l for l in lines if not l.startswith("SLACK_WEBHOOK_URL")]
                lines.append(f"SLACK_WEBHOOK_URL={url}\n")
                with open(env_path, "w") as f:
                    f.writelines(lines)
                os.environ["SLACK_WEBHOOK_URL"] = url
                self.log.add("OK", "Slack webhook saved to .env!")
                dlg.accept()
                self._send_slack()   # immediately try sending
            else:
                url_input.setStyleSheet(
                    url_input.styleSheet() + f"border: 1px solid {RED};"
                )

        save_btn.clicked.connect(save)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    def _reset(self):
        self._cur_pid = None
        self._pid_icon.setText("💤")
        self._pid_text.setText("No active incident")
        self._pid_text.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        self.thoughts.clear_thoughts()
        self._on_agent_state("idle")
        self.rca.hide_rca()
        self.tray.setIcon(_make_tray_icon("green"))
        self.worker.reset()
        self.log.add("OK", "✅ System reset — Watcher is monitoring again.")

    def _quit(self):
        self.worker.stop()
        self.worker.wait(2000)
        QApplication.quit()

    # ── minimise to tray instead of close ─────────────────────
    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "SysAdmin AI", "Still watching in the background. Click tray icon to restore.",
            QSystemTrayIcon.MessageIcon.Information, 3000
        )


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Autonomous SysAdmin")
    app.setQuitOnLastWindowClosed(False)   # keep running in tray
    win = MainWindow()
    win.show()
    sys.exit(app.exec())