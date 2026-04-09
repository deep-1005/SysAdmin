"""
gui_app.py  Autonomous SysAdmin Desktop App

A real native Windows desktop application using PyQt6.
Opens as a proper window (like VS Code / Task Manager).
No terminal needed. Double-click to launch.

Run:   python gui_app.py
Build: pyinstaller --onefile --windowed --icon=icon.ico gui_app.py
"""

import sys, os, time, threading
import traceback
from datetime import datetime
from collections import deque
from env_loader import ensure_env_loaded, validate_runtime_environment
from process_killer import terminate_process_tree, resolve_termination_target
from incident_memory import get_incident_memory
from structured_logger import audit_action, log_incident_event
from metrics_exporter import start_metrics_server, update_system_metrics, observe_action

ensure_env_loaded()

import psutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QPushButton, QScrollArea, QSizePolicy,
    QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QStackedWidget
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QObject, QPropertyAnimation,
    QEasingCurve, QRect, pyqtProperty, QPointF, QParallelAnimationGroup, QEvent
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QFontDatabase,
    QIcon, QPixmap, QPainterPath, QLinearGradient, QRadialGradient, QAction
)
from PyQt6.QtSvg import QSvgRenderer

_RUNTIME_LOG_PATH = os.path.join(os.path.dirname(__file__), "logs", "runtime.log")
_RUNTIME_LOG_LOCK = threading.Lock()


def _write_runtime_log(level: str, message: str):
    try:
        os.makedirs(os.path.dirname(_RUNTIME_LOG_PATH), exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _RUNTIME_LOG_LOCK:
            with open(_RUNTIME_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] [{level}] {message}\n")
    except Exception:
        pass

#  project modules 
sys.path.insert(0, os.path.dirname(__file__))
from watcher         import Watcher
from context_builder import ContextBuilder
from tool_runner     import ToolRunner

# 
#  THEME (SciFi Admin Palette)
# 
BG_BASE       = "#0a0f1e"
BG_CARD       = "#0d1528"
BG_ELEVATED   = "#111d35"
BG_HOVER      = "#162040"
ACCENT_TEAL   = "#00d4aa"
ACCENT_CYAN   = "#00b8d4"
ACCENT_RED    = "#e05252"
ACCENT_AMBER  = "#e8a020"
BORDER_DIM    = "rgba(0,212,170,0.12)"
BORDER_MID    = "rgba(0,212,170,0.25)"
BORDER_STRONG = "rgba(0,212,170,0.5)"
TEXT_BRIGHT   = "#e8f4f0"
TEXT_MID      = "#7ab8a8"
TEXT_DIM      = "#3d6b5e"

# Backward-compatible aliases used throughout the file.
BG_DEEP    = BG_BASE
BG_PANEL   = BG_CARD
BG_CARD2   = BG_ELEVATED
BORDER     = BORDER_DIM
BORDER_LIT = BORDER_MID
GREEN      = ACCENT_TEAL
CYAN       = ACCENT_CYAN
PURPLE     = "#7f6cff"
YELLOW     = ACCENT_AMBER
RED        = ACCENT_RED
TEXT       = TEXT_BRIGHT
MUTED      = TEXT_MID
MUTED2     = TEXT_DIM
BG_AURA_1  = "#0f1830"
BG_AURA_2  = "#131f3a"
GLOW_SOFT  = ACCENT_CYAN
NEON_BLUE  = ACCENT_CYAN

def color_for(val: float) -> str:
    if val >= 85: return RED
    if val >= 70: return YELLOW
    return GREEN


class ButtonGlowFilter(QObject):
    """Hover filter that gives buttons a lightweight electric glow."""
    def __init__(self, button: QPushButton, accent: str = NEON_BLUE, glow: str = NEON_BLUE):
        super().__init__(button)
        self._button = button
        self._accent = QColor(accent)
        self._glow = QColor(glow)
        self._effect = QGraphicsDropShadowEffect(button)
        self._effect.setBlurRadius(18)
        self._effect.setOffset(0, 0)
        self._effect.setColor(QColor(self._glow.red(), self._glow.green(), self._glow.blue(), 0))
        button.setGraphicsEffect(self._effect)

        self._anim = QPropertyAnimation(self._effect, b"blurRadius", self)
        self._button.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._button:
            if event.type() == QEvent.Type.Enter:
                self._effect.setColor(QColor(self._glow.red(), self._glow.green(), self._glow.blue(), 180))
                self._run_anim(18, 36)
                self._button.setCursor(Qt.CursorShape.PointingHandCursor)
            elif event.type() == QEvent.Type.Leave:
                self._effect.setColor(QColor(self._glow.red(), self._glow.green(), self._glow.blue(), 0))
                self._run_anim(36, 18)
        return super().eventFilter(obj, event)

    def _run_anim(self, start: int, end: int):
        self._anim.stop()
        self._anim.setDuration(160)
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()


def svg_icon_pixmap(path_d: str, size: int = 16, color: str = "#ffffff") -> QPixmap:
        """Render a simple white SVG path into a pixmap for action buttons."""
        svg = f"""
        <svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' viewBox='0 0 24 24'>
            <path fill='{color}' d='{path_d}'/>
        </svg>
        """
        renderer = QSvgRenderer(bytes(svg, "utf-8"))
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        renderer.render(p)
        p.end()
        return px

GLOBAL_STYLE = f"""
QMainWindow, QWidget {{
    background: transparent;
    color: {TEXT};
    font-family: 'Segoe UI Variable', 'Segoe UI', 'Consolas', monospace;
}}
QLabel {{
    background: transparent;
}}
QFrame {{
    border: none;
}}
QPushButton {{
    outline: none;
}}
QScrollBar:vertical {{
    background: {BG_CARD};
    width: 10px; border-radius: 5px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_LIT}; border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {PURPLE};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QToolTip {{
    background: {BG_CARD2};
    color: {TEXT};
    border: 1px solid {BORDER_LIT};
    padding: 6px 8px;
    border-radius: 6px;
}}
QMenu {{
    background: {BG_CARD2};
    color: {TEXT};
    border: 1px solid {BORDER_LIT};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {PURPLE}22; }}
QToolButton {{
    background: transparent;
    color: {TEXT};
    border: 1px solid {BORDER_LIT};
    border-radius: 8px;
    padding: 6px 12px;
}}
QToolButton:hover {{
    background: {PURPLE}1C;
    border-color: {PURPLE};
}}
"""

# 
#  CUSTOM WIDGETS
# 

class GradientPanel(QWidget):
    """Atmospheric background with layered gradients for a premium dashboard look."""
    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = self.rect()

            base = QLinearGradient(0, 0, r.width(), r.height())
            base.setColorAt(0.0, QColor(BG_DEEP))
            base.setColorAt(0.5, QColor(BG_AURA_1))
            base.setColorAt(1.0, QColor(BG_AURA_2))
            p.fillRect(r, QBrush(base))

            # Soft light blooms to avoid a flat background.
            glow_a = QRadialGradient(QPointF(r.width() * 0.12, r.height() * 0.06), r.width() * 0.45)
            c1 = QColor(GLOW_SOFT)
            c1.setAlpha(58)
            c2 = QColor(GLOW_SOFT)
            c2.setAlpha(0)
            glow_a.setColorAt(0.0, c1)
            glow_a.setColorAt(1.0, c2)
            p.fillRect(r, QBrush(glow_a))

            glow_b = QRadialGradient(QPointF(r.width() * 0.88, r.height() * 0.88), r.width() * 0.40)
            c3 = QColor(PURPLE)
            c3.setAlpha(42)
            c4 = QColor(PURPLE)
            c4.setAlpha(0)
            glow_b.setColorAt(0.0, c3)
            glow_b.setColorAt(1.0, c4)
            p.fillRect(r, QBrush(glow_b))

            # Subtle vignette to keep focus on content.
            vignette = QRadialGradient(QPointF(r.center()), max(r.width(), r.height()) * 0.72)
            v1 = QColor(0, 0, 0, 0)
            v2 = QColor(0, 0, 0, 120)
            vignette.setColorAt(0.72, v1)
            vignette.setColorAt(1.0, v2)
            p.fillRect(r, QBrush(vignette))
        finally:
            p.end()


class BrandSigil(QWidget):
    """Small circular emblem used in the hero banner."""
    def __init__(self, accent=CYAN, parent=None):
        super().__init__(parent)
        self._accent = QColor(accent)
        self.setFixedSize(70, 70)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = self.rect().adjusted(5, 5, -5, -5)
            center = r.center()

            ring = QColor(self._accent)
            ring.setAlpha(210)
            p.setPen(QPen(ring, 2.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r)

            inner = QColor(self._accent)
            inner.setAlpha(120)
            p.setPen(QPen(inner, 1.1))
            p.drawEllipse(r.adjusted(8, 8, -8, -8))

            glow = QColor(self._accent)
            glow.setAlpha(60)
            p.setPen(QPen(glow, 1.4))
            p.drawLine(center.x(), r.top() + 10, center.x(), r.bottom() - 10)
            p.drawLine(r.left() + 10, center.y(), r.right() - 10, center.y())
            p.drawEllipse(center, 10, 10)

            bolt = QColor(TEXT)
            bolt.setAlpha(225)
            p.setBrush(QBrush(bolt))
            p.setPen(Qt.PenStyle.NoPen)
            pts = [
                QPointF(center.x() - 2, center.y() - 13),
                QPointF(center.x() - 11, center.y() + 2),
                QPointF(center.x() - 2, center.y() + 2),
                QPointF(center.x() - 8, center.y() + 15),
                QPointF(center.x() + 10, center.y() - 4),
                QPointF(center.x() + 2, center.y() - 4),
                QPointF(center.x() + 8, center.y() - 13),
            ]
            from PyQt6.QtGui import QPolygonF
            p.drawPolygon(QPolygonF(pts))
        finally:
            p.end()


class BrandHero(QWidget):
    """Top-of-window hero banner inspired by the reference splash screen."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(320)
        self.setMaximumHeight(320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("HeroShell")
        shell.setStyleSheet(f"""
            #HeroShell {{
                background: transparent;
                border: 1px solid {BORDER_LIT}55;
                border-radius: 26px;
            }}
        """)
        shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(34, 28, 34, 24)
        shell_lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(14)

        left = BrandSigil(CYAN)
        right = BrandSigil(CYAN)
        left.setToolTip("Autonomous SysAdmin")
        right.setToolTip("Autonomous SysAdmin")

        center = QVBoxLayout()
        center.setSpacing(10)
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title = QLabel("AUTONOMOUS\nSYSADMIN")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setFont(QFont("Century Gothic", 30, QFont.Weight.Bold))
        self.title.setStyleSheet(f"color: {CYAN}; background: transparent; letter-spacing: 4px;")

        title_fx = QGraphicsDropShadowEffect(self)
        title_fx.setBlurRadius(26)
        title_fx.setOffset(0, 0)
        title_fx.setColor(QColor(0, 195, 255, 160))
        self.title.setGraphicsEffect(title_fx)

        self.subtitle = QLabel("AI that detects, diagnoses, and explains system failures  instantly")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.subtitle.setFont(QFont("Century Gothic", 10))
        self.subtitle.setStyleSheet(f"color: {TEXT}; background: transparent;")

        self.footer_title = QLabel("AUTONOMOUS SYSADMIN")
        self.footer_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_title.setFont(QFont("Century Gothic", 9, QFont.Weight.Bold))
        self.footer_title.setStyleSheet(f"color: {TEXT}; background: transparent; letter-spacing: 3px;")

        self.footer_line = QLabel("SELF-HEALING INFRASTRUCTURE STARTS HERE")
        self.footer_line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_line.setFont(QFont("Century Gothic", 9, QFont.Weight.Bold))
        self.footer_line.setStyleSheet(f"color: {CYAN}; background: transparent; letter-spacing: 2px;")

        center.addWidget(self.title)
        center.addWidget(self.subtitle)
        center.addSpacing(64)
        center.addWidget(self.footer_title)
        center.addWidget(self.footer_line)

        top.addWidget(left, alignment=Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        top.addLayout(center, stretch=6)
        top.addStretch(1)
        top.addWidget(right, alignment=Qt.AlignmentFlag.AlignVCenter)
        shell_lay.addLayout(top)

        self.helper = QLabel("LOCAL CREWAI  WINDOWS NATIVE  LIVE SYSTEM WATCH")
        self.helper.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.helper.setFont(QFont("Century Gothic", 8, QFont.Weight.Bold))
        self.helper.setStyleSheet(f"color: {MUTED2}; background: transparent; letter-spacing: 2px;")
        shell_lay.addWidget(self.helper)

        outer.addWidget(shell)

        self._shell = shell
        self._hero_parts = [self.subtitle, self.footer_title, self.footer_line, self.helper]

    def set_intro_mode(self, compact: bool):
        target_height = 220 if compact else 320
        self._animate_height(target_height)

        if compact:
            self.footer_title.hide()
            self.footer_line.hide()
            self.helper.hide()
            self.subtitle.setText("AI that detects, diagnoses, and explains system failures  instantly")
        else:
            self.footer_title.show()
            self.footer_line.show()
            self.helper.show()

    def _animate_height(self, target_height: int):
        start_height = self.maximumHeight()
        if start_height == target_height:
            return

        anim_min = QPropertyAnimation(self, b"minimumHeight", self)
        anim_min.setDuration(900)
        anim_min.setStartValue(start_height)
        anim_min.setEndValue(target_height)
        anim_min.setEasingCurve(QEasingCurve.Type.InOutCubic)

        anim_max = QPropertyAnimation(self, b"maximumHeight", self)
        anim_max.setDuration(900)
        anim_max.setStartValue(start_height)
        anim_max.setEndValue(target_height)
        anim_max.setEasingCurve(QEasingCurve.Type.InOutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(anim_min)
        group.addAnimation(anim_max)
        group.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._height_anim = group

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = self.rect()

            base = QLinearGradient(0, 0, r.width(), r.height())
            base.setColorAt(0.0, QColor("#041329"))
            base.setColorAt(0.55, QColor("#08213b"))
            base.setColorAt(1.0, QColor("#07101f"))
            p.fillRect(r, QBrush(base))

            bloom = QRadialGradient(QPointF(r.width() * 0.5, r.height() * 0.35), r.width() * 0.42)
            c1 = QColor(CYAN)
            c1.setAlpha(72)
            c2 = QColor(CYAN)
            c2.setAlpha(0)
            bloom.setColorAt(0.0, c1)
            bloom.setColorAt(1.0, c2)
            p.fillRect(r, QBrush(bloom))

            violet = QRadialGradient(QPointF(r.width() * 0.82, r.height() * 0.78), r.width() * 0.34)
            v1 = QColor(PURPLE)
            v1.setAlpha(55)
            v2 = QColor(PURPLE)
            v2.setAlpha(0)
            violet.setColorAt(0.0, v1)
            violet.setColorAt(1.0, v2)
            p.fillRect(r, QBrush(violet))

            center = QPointF(r.width() * 0.5, r.height() * 0.38)
            globe_r = min(r.width(), r.height()) * 0.26

            globe_fill = QRadialGradient(center, globe_r * 1.35)
            g1 = QColor(7, 195, 255, 120)
            g2 = QColor(7, 195, 255, 20)
            g3 = QColor(7, 195, 255, 0)
            globe_fill.setColorAt(0.0, g1)
            globe_fill.setColorAt(0.6, g2)
            globe_fill.setColorAt(1.0, g3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(globe_fill))
            p.drawEllipse(QRect(int(center.x() - globe_r), int(center.y() - globe_r), int(globe_r * 2), int(globe_r * 2)))

            clip = QPainterPath()
            clip.addEllipse(center, globe_r, globe_r)
            p.save()
            p.setClipPath(clip)

            p.setPen(QPen(QColor(140, 235, 255, 36), 1.0))
            for frac in (-0.68, -0.48, -0.26, 0.0, 0.26, 0.48, 0.68):
                y = center.y() + globe_r * frac
                span = max(0.0, globe_r * globe_r - (y - center.y()) * (y - center.y())) ** 0.5
                p.drawLine(QPointF(center.x() - span, y), QPointF(center.x() + span, y))

            for angle in (-62, -38, -14, 14, 38, 62):
                p.save()
                p.translate(center)
                p.rotate(angle)
                p.setPen(QPen(QColor(140, 235, 255, 34), 1.0))
                p.drawEllipse(int(-globe_r), int(-globe_r * 0.72), int(globe_r * 2), int(globe_r * 1.44))
                p.restore()

            p.restore()

            p.setPen(QPen(QColor(120, 220, 255, 38), 1.4))
            p.drawEllipse(QRect(int(center.x() - globe_r * 1.24), int(center.y() - globe_r * 0.92), int(globe_r * 2.48), int(globe_r * 1.84)))
            p.drawEllipse(QRect(int(center.x() - globe_r * 1.46), int(center.y() - globe_r * 1.12), int(globe_r * 2.92), int(globe_r * 2.24)))
            p.setPen(QPen(QColor(120, 220, 255, 24), 1.0, Qt.PenStyle.DotLine))
            p.drawEllipse(QRect(int(center.x() - globe_r * 1.62), int(center.y() - globe_r * 1.18), int(globe_r * 3.24), int(globe_r * 2.36)))
        finally:
            p.end()


class SplashIntro(QWidget):
    """Full-screen intro screen. Click anywhere to open the dashboard."""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        panel = QFrame()
        panel.setStyleSheet("background: transparent;")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        pl = QVBoxLayout(panel)
        pl.setContentsMargins(48, 24, 48, 28)
        pl.setSpacing(10)

        nav = QHBoxLayout()
        brand = QLabel("  SysAdmin")
        brand.setFont(QFont("Century Gothic", 10, QFont.Weight.Bold))
        brand.setStyleSheet(f"color: {TEXT_BRIGHT}; background: transparent;")
        nav.addWidget(brand)
        nav.addStretch()
        pl.addLayout(nav)

        pl.addStretch(1)

        title = QLabel("SysAdmin")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Century Gothic", 60, QFont.Weight.Bold))
        title.setStyleSheet(f"color: #8fd2ff; background: transparent;")

        subtitle = QLabel("Sense the glitch. Name the culprit. Solve the mystery.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Century Gothic", 24, QFont.Weight.DemiBold))
        subtitle.setStyleSheet(f"color: #98dfff; background: transparent;")

        hint = QLabel("Click anywhere to open")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setFont(QFont("Century Gothic", 9, QFont.Weight.Bold))
        hint.setStyleSheet(f"color: {TEXT_DIM}; background: transparent;")

        pl.addSpacing(8)
        pl.addWidget(title)
        pl.addWidget(subtitle)
        pl.addSpacing(10)
        pl.addWidget(hint)
        pl.addStretch(2)

        lay.addWidget(panel)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = self.rect()

            base = QLinearGradient(0, 0, r.width(), r.height())
            base.setColorAt(0.0, QColor("#01030b"))
            base.setColorAt(0.52, QColor("#02102b"))
            base.setColorAt(1.0, QColor("#051633"))
            p.fillRect(r, QBrush(base))

            horizon = QLinearGradient(0, r.height() * 0.58, 0, r.height())
            h1 = QColor("#123f74")
            h1.setAlpha(54)
            h2 = QColor("#040d1f")
            h2.setAlpha(28)
            horizon.setColorAt(0.0, h1)
            horizon.setColorAt(1.0, h2)
            p.fillRect(r, QBrush(horizon))

            bloom = QRadialGradient(QPointF(r.width() * 0.56, r.height() * 0.55), r.width() * 0.38)
            c1 = QColor("#2a78bf")
            c1.setAlpha(38)
            c2 = QColor("#2a78bf")
            c2.setAlpha(0)
            bloom.setColorAt(0.0, c1)
            bloom.setColorAt(1.0, c2)
            p.fillRect(r, QBrush(bloom))

            # Multi-layer cyan wave ribbons.
            wave_col = QColor("#2f87c7")
            for layer, alpha in enumerate((56, 88, 122)):
                y0 = r.height() * (0.60 + layer * 0.03)
                amp = r.height() * (0.05 + layer * 0.014)
                path = QPainterPath()
                path.moveTo(0, y0)
                path.cubicTo(r.width() * 0.18, y0 - amp,
                             r.width() * 0.35, y0 + amp,
                             r.width() * 0.52, y0)
                path.cubicTo(r.width() * 0.70, y0 - amp,
                             r.width() * 0.84, y0 + amp,
                             r.width(), y0 - amp * 0.2)

                glow = QColor(wave_col)
                glow.setAlpha(alpha)
                p.setPen(QPen(glow, 2.2 + layer * 0.8))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)

            # Lower-field micro lights and streaks.
            p.setPen(Qt.PenStyle.NoPen)
            for gy in range(0, 12):
                y = int(r.height() * 0.63 + gy * (r.height() * 0.03))
                for gx in range(0, 40):
                    x = int((gx / 39.0) * r.width())
                    val = (gx * 17 + gy * 29) % 100
                    if val < 16:
                        dot = QColor("#2d88c9")
                        dot.setAlpha(55 + (val * 6))
                        p.setBrush(QBrush(dot))
                        size = 2 if (val % 4) else 3
                        p.drawEllipse(x, y, size, size)
                    elif 16 <= val < 20:
                        streak = QColor("#2d88c9")
                        streak.setAlpha(62)
                        p.setPen(QPen(streak, 1.0))
                        p.drawLine(x - 8, y + 1, x + 8, y - 1)
                        p.setPen(Qt.PenStyle.NoPen)

            # Sparse stars in upper background.
            p.setPen(Qt.PenStyle.NoPen)
            for sy in range(0, 10):
                y = int((sy / 9.0) * r.height() * 0.45)
                for sx in range(0, 24):
                    x = int((sx / 23.0) * r.width())
                    v = (sx * 31 + sy * 13) % 97
                    if v < 8:
                        star = QColor("#95dfff")
                        star.setAlpha(88)
                        p.setBrush(QBrush(star))
                        p.drawEllipse(x, y, 2, 2)
        finally:
            p.end()


class ThinkingIndicator(QWidget):
    """Animated AI-thinking status with pulsing dots and a scan ring."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._phase = 0.0
        self._accent = QColor(CYAN)
        self.setFixedSize(42, 18)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_active(self, active: bool, accent: str = CYAN):
        self._active = active
        self._accent = QColor(accent)
        if active:
            if not self._timer.isActive():
                self._timer.start(40)
        else:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.08) % 1.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w = self.width()
            h = self.height()
            center_y = h // 2

            base_x = 4
            spacing = 12
            for idx in range(3):
                pulse = (self._phase + idx * 0.22) % 1.0
                scale = 0.45 + 0.55 * (0.5 - abs(pulse - 0.5)) * 2
                alpha = 90 + int(140 * scale)
                dot_col = QColor(self._accent)
                dot_col.setAlpha(alpha if self._active else 70)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(dot_col))
                radius = 2 + int(2 * scale)
                p.drawEllipse(base_x + idx * spacing - radius, center_y - radius, radius * 2, radius * 2)
        finally:
            p.end()

class SparklineWidget(QWidget):
    """Mini live line graph drawn with QPainter."""
    def __init__(self, color=GREEN, parent=None):
        super().__init__(parent)
        self._color  = QColor(color)
        self._data: deque = deque(maxlen=60)
        self._phase = 0.0
        self.setMinimumHeight(44)
        self.setMinimumWidth(80)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(35)

    def push(self, val: float):
        self._data.append(val)
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.018) % 1.0
        if self._data:
            self.update()

    def paintEvent(self, event):
        if len(self._data) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pts  = list(self._data)
        # Keep sparkline scale stable across updates so motion feels smooth.
        mx   = 100.0
        n    = len(pts)
        left_pad, right_pad = 2, 2
        top_pad, bottom_pad = 4, 6
        draw_w = max(1, w - left_pad - right_pad)
        draw_h = max(1, h - top_pad - bottom_pad)
        xs   = [int(left_pad + i / (n - 1) * draw_w) for i in range(n)]
        ys   = [int(top_pad + (1.0 - min(max(v, 0), mx) / mx) * draw_h) for v in pts]

        # Baseline track below the sparkline.
        base_col = QColor(self._color)
        base_col.setAlpha(85)
        base_pen = QPen(base_col, 1)
        p.setPen(base_pen)
        p.drawLine(left_pad, h - 2, w - right_pad, h - 2)

        # gradient fill under line
        path = QPainterPath()
        path.moveTo(xs[0], h)
        for x, y in zip(xs, ys):
            path.lineTo(x, y)
        path.lineTo(xs[-1], h)
        path.closeSubpath()
        grad = QLinearGradient(0, top_pad, 0, h)
        c = QColor(self._color)
        c.setAlpha(88)
        grad.setColorAt(0, c)
        c2 = QColor(self._color)
        c2.setAlpha(0)
        grad.setColorAt(1, c2)
        p.fillPath(path, QBrush(grad))

        # Soft glow pass behind the main line.
        glow_col = QColor(self._color)
        glow_col.setAlpha(90)
        glow_pen = QPen(glow_col, 4.2)
        glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(glow_pen)
        for i in range(1, n):
            p.drawLine(xs[i-1], ys[i-1], xs[i], ys[i])

        # Crisp foreground line.
        pen = QPen(self._color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for i in range(1, n):
            p.drawLine(xs[i-1], ys[i-1], xs[i], ys[i])

        scan_x = int(left_pad + self._phase * draw_w)
        scan_col = QColor(self._color)
        scan_col.setAlpha(110)
        p.setPen(QPen(scan_col, 1.1))
        p.drawLine(scan_x, top_pad, scan_x, h - bottom_pad)

        interp_idx = min(n - 1, max(0, int(self._phase * (n - 1))))
        pulse_x = xs[interp_idx]
        pulse_y = ys[interp_idx]
        pulse_col = QColor(self._color)
        pulse_col.setAlpha(220)
        p.setBrush(QBrush(pulse_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(pulse_x - 3, pulse_y - 3, 7, 7)

        # Pulse dot on latest point.
        dot_col = QColor(self._color)
        dot_col.setAlpha(220)
        p.setBrush(QBrush(dot_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(xs[-1] - 2, ys[-1] - 2, 5, 5)
        p.end()


class ArcGauge(QWidget):
    """Circular arc gauge  shows value as sweeping arc + large % text."""
    def __init__(self, label: str, accent: str = GREEN, parent=None):
        super().__init__(parent)
        self._label  = label
        self._accent = QColor(accent)
        self._base_accent = QColor(accent)
        self._value  = 0.0
        self._target_value = 0.0  # Target value for smooth animation
        self._phase  = 0.0
        self.setFixedSize(240, 240)

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(40)

    def _tick(self):
        self._phase = (self._phase + 0.012) % 1.0
        
        # Smoothly animate value towards target
        if abs(self._value - self._target_value) > 0.1:
            diff = self._target_value - self._value
            self._value += diff * 0.15  # Easing factor for smooth transition
        else:
            self._value = self._target_value
        
        self.update()

    def _aura_colors(self):
        name = (self._label or "").lower()
        if "cpu" in name:
            return QColor("#22d3ee"), QColor("#00b8d4")
        if "memory" in name:
            return QColor("#ff7a45"), QColor("#ff3f6d")
        if "disk" in name:
            return QColor("#ffd166"), QColor("#9b6dff")
        if "running" in name or "apps" in name:
            return QColor("#ff5c5c"), QColor("#ff2f54")
        return QColor("#22d3ee"), QColor("#00b8d4")

    def set_value(self, v: float):
        self._target_value = v
        # Keep semantic thresholds while preserving category-specific aura palette.
        self._accent = QColor(color_for(v))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            import math
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = self.width(), self.height()
            cx, cy = w // 2, h // 2
            r = 86
            arc_r = 72  # Arc radius, moved inward to keep away from center text
            aura_a, aura_b = self._aura_colors()
            pulse = 0.5 + 0.5 * math.sin(self._phase * math.tau)

            # Outer atmospheric glow.
            halo = QRadialGradient(QPointF(cx, cy), r + 26 + pulse * 5)
            c0 = QColor(aura_a)
            c0.setAlpha(78 + int(pulse * 28))
            c1 = QColor(aura_b)
            c1.setAlpha(18)
            c2 = QColor(aura_b)
            c2.setAlpha(0)
            halo.setColorAt(0.0, c0)
            halo.setColorAt(0.55, c1)
            halo.setColorAt(1.0, c2)
            p.setBrush(QBrush(halo))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRect(cx - (r + 18), cy - (r + 18), (r + 18) * 2, (r + 18) * 2))

            # Core dark track.
            track_pen = QPen(QColor("#04070e"), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(track_pen)
            p.drawArc(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2, 225 * 16, -270 * 16)

            span = int(-270 * (self._value / 100.0) * 16)

            # Soft glow pass for the value arc.
            glow = QColor(self._accent)
            glow.setAlpha(120)
            glow_pen = QPen(glow, 13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(glow_pen)
            p.drawArc(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2, 225 * 16, span)

            # Main value arc.
            main_pen = QPen(self._accent, 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(main_pen)
            p.drawArc(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2, 225 * 16, span)

            # Bright hot-spot at arc end.
            angle_deg = 225 + (-270 * (self._value / 100.0))
            ex = cx + arc_r * math.cos(math.radians(angle_deg))
            ey = cy - arc_r * math.sin(math.radians(angle_deg))
            hotspot = QRadialGradient(QPointF(ex, ey), 9)
            h0 = QColor("#ffffff")
            h0.setAlpha(220)
            h1 = QColor(self._accent)
            h1.setAlpha(0)
            hotspot.setColorAt(0.0, h0)
            hotspot.setColorAt(1.0, h1)
            p.setBrush(QBrush(hotspot))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRect(int(ex - 9), int(ey - 9), 18, 18))

            # Value text (centered higher)
            p.setPen(QPen(self._accent))
            f = QFont("Century Gothic", 24, QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(QRect(0, cy - 18, w, 34), Qt.AlignmentFlag.AlignCenter,
                       f"{self._value:.1f}%")

            # Label (pushed further down with more space)
            p.setPen(QPen(QColor(MUTED2)))
            f2 = QFont("Century Gothic", 12)
            p.setFont(f2)
            p.drawText(QRect(0, cy + 68, w, 20), Qt.AlignmentFlag.AlignCenter, self._label)
        finally:
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
        self.setContentsMargins(0, 0, 0, 0)
        self._accent = accent

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)

        # title
        t = QLabel(title)
        t.setFont(QFont("Century Gothic", 10, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {TEXT}; border: none; background: transparent;")
        lay.addWidget(t)

        # arc gauge
        self.gauge = ArcGauge(title, accent)
        lay.addWidget(self.gauge, alignment=Qt.AlignmentFlag.AlignCenter)

        # sparkline
        self.spark = SparklineWidget(accent)
        self.spark.setFixedHeight(46)
        self.spark.setObjectName("SparkBand")
        self.spark.setStyleSheet(f"""
            #SparkBand {{
                background: {BG_CARD2};
                border: 1px solid {accent}55;
                border-radius: 8px;
            }}
        """)
        lay.addWidget(self.spark)

        # subtitle
        self.sub = QLabel("")
        self.sub.setFont(QFont("Century Gothic", 8))
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
        self._lay.setSpacing(6)

        hdr = QWidget()
        hdr.setStyleSheet(f"""
            background: {BG_CARD2};
            border: 1px solid {BORDER_LIT};
            border-radius: 8px;
        """)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.setSpacing(0)

        def hlabel(text, width, align=Qt.AlignmentFlag.AlignLeft):
            lbl = QLabel(text)
            lbl.setFont(QFont("Century Gothic", 8, QFont.Weight.Bold))
            lbl.setStyleSheet(f"color: {MUTED2}; background: transparent;")
            lbl.setFixedWidth(width)
            lbl.setAlignment(align)
            return lbl

        hl.addWidget(hlabel("Time", 70))
        hl.addWidget(hlabel("Level", 52))
        hl.addWidget(hlabel("Message", 1))
        self._lay.addWidget(hdr)
        self._lay.addStretch()
        self.setWidget(container)

    def add(self, level: str, msg: str):
        ts    = datetime.now().strftime("%H:%M:%S")
        color = {
            "OK":   GREEN, "WARN": YELLOW,
            "ERR":  RED,   "INFO": CYAN
        }.get(level, MUTED2)

        row = QWidget()
        row.setStyleSheet(f"""
            background: {BG_CARD2};
            border: 1px solid {BORDER_DIM};
            border-radius: 8px;
        """)
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(10, 6, 10, 6)
        rl.setSpacing(0)

        ts_lbl = QLabel(ts)
        ts_lbl.setFont(QFont("Century Gothic", 8))
        ts_lbl.setStyleSheet(f"color: {MUTED}; background: transparent;")
        ts_lbl.setFixedWidth(70)

        lv_lbl = QLabel(f"[{level}]")
        lv_lbl.setFont(QFont("Century Gothic", 8, QFont.Weight.Bold))
        lv_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        lv_lbl.setFixedWidth(52)

        msg_lbl = QLabel(msg)
        msg_lbl.setFont(QFont("Century Gothic", 9))
        msg_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        msg_lbl.setWordWrap(True)

        rl.addWidget(ts_lbl)
        rl.addWidget(lv_lbl)
        rl.addWidget(msg_lbl)
        rl.addStretch()

        # insert before the stretch
        self._lay.insertWidget(self._lay.count() - 1, row)
        if self._lay.count() > 120:
            item = self._lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        # auto-scroll to bottom
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()
        ))

        _write_runtime_log(level, msg)


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
        self._lay.setSpacing(6)

        hdr = QWidget()
        hdr.setStyleSheet(f"""
            background: {BG_CARD2};
            border: 1px solid {BORDER_LIT};
            border-radius: 8px;
        """)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.setSpacing(0)

        def hlabel(text, width, align=Qt.AlignmentFlag.AlignLeft):
            lbl = QLabel(text)
            lbl.setFont(QFont("Century Gothic", 8, QFont.Weight.Bold))
            lbl.setStyleSheet(f"color: {MUTED2}; background: transparent;")
            lbl.setFixedWidth(width)
            lbl.setAlignment(align)
            return lbl

        hl.addWidget(hlabel("Time", 70))
        hl.addWidget(hlabel("Detail", 520))
        self._lay.addWidget(hdr)
        self._lay.addStretch()
        self.setWidget(container)

        self._state_lbl = None   # set by parent

    def clear_thoughts(self):
        while self._lay.count() > 2:
            item = self._lay.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

    def add(self, icon: str, text: str, color: str = TEXT):
        ts = datetime.now().strftime("%H:%M:%S")
        row = QWidget()
        row.setStyleSheet(f"""
            background: {BG_CARD2};
            border: 1px solid {BORDER_DIM};
            border-radius: 8px;
        """)
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(10, 6, 10, 6)
        rl.setSpacing(0)

        ts_lbl = QLabel(ts)
        ts_lbl.setFont(QFont("Century Gothic", 8))
        ts_lbl.setStyleSheet(f"color: {MUTED}; background: transparent;")
        ts_lbl.setFixedWidth(70)

        txt = QLabel(text)
        txt.setFont(QFont("Century Gothic", 9))
        txt.setStyleSheet(f"color: {color}; background: transparent;")
        txt.setWordWrap(True)

        rl.addWidget(ts_lbl)
        rl.addWidget(txt)

        self._lay.insertWidget(self._lay.count() - 1, row)
        if self._lay.count() > 120:
            item = self._lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()
        ))


class RCACard(QFrame):
    """RCA result card  hidden until incident is resolved."""
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
        icon_lbl = QLabel("")
        icon_lbl.setFont(QFont("Century Gothic", 14))
        icon_lbl.setStyleSheet(f"color: {RED}; background: transparent;")
        title = QLabel("Problem Found\nHere's What the AI Discovered")
        title.setFont(QFont("Century Gothic", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {RED}; background: transparent;")
        hrow.addWidget(icon_lbl)
        hrow.addWidget(title)
        hrow.addStretch()
        lay.addLayout(hrow)

        # pid label
        self.pid_lbl = QLabel("")
        self.pid_lbl.setFont(QFont("Century Gothic", 10, QFont.Weight.Bold))
        self.pid_lbl.setStyleSheet(f"color: {YELLOW}; background: transparent;")
        lay.addWidget(self.pid_lbl)

        # RCA text
        self.rca_lbl = QLabel("")
        self.rca_lbl.setFont(QFont("Century Gothic", 10))
        self.rca_lbl.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self.rca_lbl.setWordWrap(True)
        self.rca_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.rca_lbl)

        # action buttons
        btn_row = QHBoxLayout()
        self.kill_btn  = self._btn("  Kill Process", RED,    self._on_kill)
        self.reset_btn = self._btn("  Reset",         MUTED2, self._on_reset)
        btn_row.addWidget(self.kill_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self.slack_status = QLabel("Slack: pending")
        self.slack_status.setFont(QFont("Century Gothic", 9, QFont.Weight.Bold))
        self.slack_status.setStyleSheet(f"color: {TEXT_MID}; background: transparent;")
        lay.addWidget(self.slack_status)

        self.setGraphicsEffect(None)

    def _btn(self, label: str, color: str, slot) -> QPushButton:
        b = QPushButton(label)
        b.setFont(QFont("Century Gothic", 9, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedHeight(32)
        hover_color = NEON_BLUE if "Reset" in label else color
        b.setStyleSheet(f"""
            QPushButton {{
                background: {BG_CARD2};
                color: {color};
                border: 1px solid {color}80;
                border-radius: 6px;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background: {hover_color}24;
                border: 1px solid {hover_color};
                color: {TEXT};
            }}
            QPushButton:pressed {{ background: {NEON_BLUE}33; }}
        """)
        b._glow_filter = ButtonGlowFilter(b, accent=NEON_BLUE, glow=NEON_BLUE)
        b.clicked.connect(slot)
        return b

    def show_rca(self, rca_text: str, pid: str = ""):
        self.pid_lbl.setText(f"Alert PID: {pid}" if pid else "")
        self.rca_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.rca_lbl.setText(self._format_rca_text(rca_text))
        self.set_slack_status("Slack: preparing alert", ACCENT_CYAN)
        self.show()

    def _format_rca_text(self, text: str) -> str:
        import html
        import re

        safe = html.escape(text or "")
        safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
        safe = safe.replace("\n", "<br>")
        return safe

    def hide_rca(self):
        self.hide()

    def set_slack_status(self, text: str, color: str = TEXT_MID):
        self.slack_status.setText(text)
        self.slack_status.setStyleSheet(f"color: {color}; background: transparent;")


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

        title = QLabel("  Active Apps (by CPU usage)")
        title.setFont(QFont("Century Gothic", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        title.setToolTip("Shows which programs are using the most processor power right now.\nThe AI will investigate the top one if it looks suspicious.")
        lay.addWidget(title)

        subtitle = QLabel("The list updates continuously and filters Windows system pseudo-processes.")
        subtitle.setFont(QFont("Century Gothic", 8))
        subtitle.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        subtitle.setWordWrap(True)
        lay.addWidget(subtitle)

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
        if header:
            w.setStyleSheet(f"""
                background: {BG_CARD2};
                border: 1px solid {BORDER_LIT};
                border-radius: 8px;
            """)
        else:
            w.setStyleSheet(f"""
                background: {BG_CARD2};
                border: 1px solid {BORDER_DIM};
                border-radius: 6px;
            """)
        rl  = QHBoxLayout(w)
        rl.setContentsMargins(8, 6, 8, 6)
        rl.setSpacing(0)
        font  = QFont("Century Gothic", 9, QFont.Weight.Bold if header else QFont.Weight.Normal)

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
                cpu_raw = float(p.get("cpu_percent") or 0.0)
                # Show raw per-process CPU from psutil; dividing by core count hides real usage.
                cpu_disp = max(0.0, cpu_raw)
                c = color_for(min(100.0, cpu_disp))
                items = [str(p["pid"]), p["name"][:22],
                         f"{cpu_disp:.1f}", f"{p['memory_percent']:.1f}"]
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


class WorkflowStrip(QFrame):
    """Shows the current AI investigation stage as a simple visual stepper."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("WorkflowStrip")
        self.setStyleSheet(f"""
            #WorkflowStrip {{
                background: #0d1520;
                border: 1px solid {BORDER};
                border-radius: 10px;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        self._steps = {}
        steps = [
            ("idle", "Watching"),
            ("detective", "Checking"),
            ("reporter", "Writing report"),
            ("done", "Done"),
        ]

        lay.addStretch()
        for key, label in steps:
            chip = QLabel(label)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFont(QFont("Century Gothic", 8, QFont.Weight.Bold))
            chip.setMinimumHeight(24)
            chip.setStyleSheet(f"""
                QLabel {{
                    background: {BG_CARD2};
                    color: {MUTED2};
                    border: 1px solid {BORDER_LIT};
                    border-radius: 12px;
                    padding: 0 10px;
                }}
            """)
            self._steps[key] = chip
            lay.addWidget(chip)
        lay.addStretch()

        self.set_state("idle")

    def set_state(self, state: str):
        active_colors = {
            "idle": (GREEN, GREEN),
            "detective": (CYAN, CYAN),
            "reporter": (PURPLE, PURPLE),
            "done": (GREEN, GREEN),
        }
        order = ["idle", "detective", "reporter", "done"]
        active_index = order.index(state) if state in order else 0

        for idx, key in enumerate(order):
            chip = self._steps[key]
            if idx == active_index:
                # Active tab: big, glowing border box
                accent = active_colors.get(state, (CYAN, CYAN))[0]
                chip.setFont(QFont("Century Gothic", 11, QFont.Weight.Bold))
                chip.setMinimumHeight(38)
                chip.setStyleSheet(f"""
                    QLabel {{
                        background: {accent}22;
                        color: {accent};
                        border: 2px solid {accent};
                        border-radius: 13px;
                        padding: 0 16px;
                    }}
                """)
            else:
                # Inactive tabs: plain text, no border
                chip.setFont(QFont("Century Gothic", 8, QFont.Weight.Bold))
                chip.setMinimumHeight(24)
                chip.setStyleSheet(f"""
                    QLabel {{
                        background: transparent;
                        color: {MUTED2};
                        border: none;
                        padding: 0 10px;
                    }}
                """)

# 
#  WORKER THREAD  runs watcher + agent in background
# 

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
        self._last_incident_id = ""
        self._last_correlation_id = ""
        self._agent_thread: threading.Thread | None = None
        self._last_proc_refresh_ts = 0.0
        self._last_proc_rows: list[dict] = []
        self._proc_cpu_primed = False
        self.COOLDOWN    = 90
        self.DIAGNOSTIC_TIMEOUT_S = max(60, int(os.getenv("DIAGNOSTIC_TIMEOUT_S", "240")))

    def _collect_process_rows(self, max_rows: int = 8) -> list[dict]:
        """Collect top process rows with a strict time budget so UI never stalls."""
        skip_pids = {0, 4}
        skip_names = {"system idle process", "system", "registry", "memory compression"}
        rows: list[dict] = []
        deadline = time.time() + 0.35

        for p in psutil.process_iter(["pid", "name", "status"]):
            if time.time() >= deadline:
                break
            try:
                pid = int(p.info.get("pid") or 0)
                if pid in skip_pids:
                    continue

                name = (p.info.get("name") or "(unknown)").strip() or "(unknown)"
                if name.lower() in skip_names:
                    continue

                try:
                    cpu = float(p.cpu_percent(interval=None) or 0.0)
                except Exception:
                    cpu = 0.0

                try:
                    mem_pct = float(p.memory_percent() or 0.0)
                except Exception:
                    mem_pct = 0.0

                status = p.info.get("status") or "unknown"

                rows.append({
                    "pid": pid,
                    "name": name,
                    "cpu_percent": cpu,
                    "memory_percent": mem_pct,
                    "status": status,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue

        rows.sort(key=lambda x: x.get("cpu_percent", 0.0), reverse=True)
        return rows[:max_rows]

    def _extract_pid_from_text(self, text: str) -> str:
        import re
        for m in re.finditer(r"PID[=:\s]+(\d+)", text, re.IGNORECASE):
            p = m.group(1)
            if p not in ("0", "4"):
                return p
        return "N/A"

    def _fallback_pid(self) -> str:
        """Best-effort PID when model output misses it: use live top CPU process."""
        try:
            out = self.runner.check_processes()
            pid = self._extract_pid_from_text(out)
            if pid != "N/A":
                return pid
        except Exception:
            pass
        return "N/A"

    def stop(self): self._running = False

    def _start_agent_async(self, ctx: dict):
        """Run incident diagnosis without blocking live metric refresh."""
        t = self._agent_thread
        if t is not None and t.is_alive():
            return
        self._agent_thread = threading.Thread(target=self._run_agent, args=(ctx,), daemon=True)
        self._agent_thread.start()

    def run(self):
        # Prime process CPU counters once so the first visible sample is meaningful.
        if not self._proc_cpu_primed:
            for p in psutil.process_iter():
                try:
                    p.cpu_percent(interval=None)
                except Exception:
                    pass
            time.sleep(0.25)
            self._proc_cpu_primed = True

        while self._running:
            loop_started = time.time()
            try:
                m = self.watcher.get_metrics()
                ev, evs = self.watcher.detect_events(m)
                now = time.time()
                if now - self._last_proc_refresh_ts >= 3.0:
                    self._last_proc_rows = self._collect_process_rows(max_rows=8)
                    self._last_proc_refresh_ts = now

                self.sig.metrics_ready.emit({**m, "event": ev, "top_processes": self._last_proc_rows})

                if ev != "NORMAL" and not self._incident:
                    last = self._last_trig.get(ev, 0)
                    if time.time() - last > self.COOLDOWN:
                        self._incident = True
                        self._last_trig[ev] = time.time()
                        ctx = self.ctx_builder.build_context(m, ev, evs)
                        self._start_agent_async(ctx)
            except Exception as e:
                _write_runtime_log("ERR", f"Worker error: {e}\n{traceback.format_exc()}")
                self.sig.log_line.emit("ERR", f"Worker error: {e}")
            elapsed = time.time() - loop_started
            time.sleep(max(0.05, 1.0 - elapsed))

    def _run_agent(self, ctx: dict):
        ev = ctx["primary_event"]
        self.sig.log_line.emit("ERR",  f"INCIDENT – {ev}")
        self.sig.log_line.emit("WARN", f"CPU={ctx['cpu_usage']}% – RAM={ctx['memory_usage']}%")
        self.sig.agent_state.emit("detective")
        self.sig.thought.emit("", f"❄️ Trigger [{ev}] – handing off to local AI agent...")
        self.sig.thought.emit("", "🧬 Detective agent starting diagnostic chain...")

        try:
            #  REAL AI CALL 
            # This is where the local CrewAI crew decides which tools to call.
            # It reads check_processes output and CHOOSES what to do next.
            from detective_agent import run_diagnostic_crew

            self.sig.thought.emit("", "💡 Local AI is analyzing your system – this may take 15–30 seconds...")
            self.sig.log_line.emit("INFO", "AI agent running – analyzing live system data...")

            result = self._run_diagnostic_crew_with_timeout(
                run_diagnostic_crew,
                ctx,
                timeout_s=self.DIAGNOSTIC_TIMEOUT_S,
            )

            rca = result["rca"]
            pid = result.get("pid", "N/A")
            diag = result.get("diagnostic_result", "")
            tool_trace = result.get("tool_trace", [])
            self._last_incident_id = str(result.get("incident_id", "") or "")
            self._last_correlation_id = str(result.get("correlation_id", "") or "")
            risk_level = str(result.get("risk_level", ctx.get("risk_level", "caution")))

            if tool_trace:
                self.sig.thought.emit("", "AI tool execution trace:")
                for step in tool_trace:
                    self.sig.thought.emit("", step)
                    self.sig.log_line.emit("INFO", step)
            else:
                self.sig.log_line.emit("WARN", "No AI tool trace returned for this run.")

            prev_fix = result.get("previously_fixed_by", "")
            if prev_fix:
                self.sig.log_line.emit("INFO", prev_fix)
                self.sig.thought.emit("", f"↺ {prev_fix}")

            if not str(pid).isdigit():
                pid = self._fallback_pid()
                self.sig.log_line.emit("WARN", f"Model returned no usable PID; using live fallback PID {pid}.")

            # Stream key findings from the diagnostic into the thought panel
            self.sig.thought.emit("", "🔎 Detective agent finished investigation.")
            self.sig.agent_state.emit("reporter")
            self.sig.thought.emit("", "📋 Reporter agent writing Root Cause Analysis...")
            time.sleep(0.5)
            self.sig.thought.emit("", f"✅ RCA complete – culprit identified: PID {pid}")
            self.sig.agent_state.emit("done")

            self._cur_pid = pid
            self.sig.rca_ready.emit(rca, pid)
            self.sig.log_line.emit("OK",   f"AI diagnosis complete – PID {pid} identified")
            self.sig.log_line.emit("INFO", f"Risk level: {risk_level}")
            self.sig.log_line.emit("INFO", "Use the action buttons below to respond.")
            log_incident_event(
                "INFO",
                "gui_incident_ready",
                self._last_correlation_id,
                incident_id=self._last_incident_id,
                pid=pid,
                risk=risk_level,
                diagnosis_preview=str(diag)[:220],
            )
            self._incident = False

        except RuntimeError as e:
            # Keep operator-facing text calm and action-oriented.
            self.sig.agent_state.emit("reporter")
            self.sig.thought.emit("", "Analysis encountered an issue. Preparing a quick local report.")
            self.sig.log_line.emit("WARN", "Live analysis interrupted; generating quick local report.")
            _write_runtime_log("ERR", f"RuntimeError in _run_agent: {e}\n{traceback.format_exc()}")
            rca = self._build_rca(ctx)
            pid = self._cur_pid or self._fallback_pid()
            self._cur_pid = pid
            self.sig.rca_ready.emit(rca, pid)
            self.sig.agent_state.emit("done")
            self._incident = False

        except TimeoutError:
            # If analysis takes too long, return a quick local report and keep UI responsive.
            self.sig.agent_state.emit("reporter")
            self.sig.thought.emit("", "Analysis is taking longer than expected. Preparing a quick local report.")
            self.sig.log_line.emit("WARN", "Live analysis exceeded response budget; using quick local report.")
            rca = self._build_rca(ctx)
            pid = self._cur_pid or self._fallback_pid()
            self._cur_pid = pid
            self.sig.rca_ready.emit(rca, pid)
            self.sig.agent_state.emit("done")
            _write_runtime_log("WARN", "Analysis timeout in _run_agent; quick local RCA used.")
            self._incident = False

        except Exception as e:
            # Any other error  show it and fall back gracefully
            self.sig.agent_state.emit("idle")
            self.sig.thought.emit("", f"Agent error: {e}")
            self.sig.log_line.emit("ERR", f"Agent failed: {e}")
            self.sig.log_line.emit("INFO", "Falling back to rule-based diagnosis...")
            _write_runtime_log("ERR", f"Unhandled exception in _run_agent: {e}\n{traceback.format_exc()}")
            # Fall back to simple rule-based RCA
            rca = self._build_rca(ctx)
            pid = self._cur_pid or self._fallback_pid()
            self._cur_pid = pid
            self.sig.rca_ready.emit(rca, pid)
            self.sig.agent_state.emit("done")
            self._incident = False

    def _run_diagnostic_crew_with_timeout(self, func, ctx: dict, timeout_s: int = 60) -> dict:
        """Run the Ollama diagnostic in a helper thread and fail fast if it stalls."""
        result_box: dict = {}
        error_box: dict = {}

        def _target():
            try:
                result_box["value"] = func(ctx)
            except Exception as exc:
                error_box["error"] = exc

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout_s)

        if t.is_alive():
            raise TimeoutError(f"Analysis timed out after {timeout_s}s")

        if error_box.get("error") is not None:
            raise error_box["error"]

        return result_box.get("value", {})

    def _build_rca(self, ctx: dict) -> str:
        ev  = ctx["primary_event"]
        cpu = ctx["cpu_usage"]
        ram = ctx["memory_usage"]
        pid = self._cur_pid or "N/A"
        sym = {
            "CPU_SPIKE":          f"CPU spike ({cpu}%) exceeded the safe threshold.",
            "MEMORY_SPIKE":       f"Memory exhaustion ({ram}%)  system approaching OOM.",
            "DISK_SPIKE":         f"Disk usage exceeded the safe threshold.",
            "HIGH_PROCESS_COUNT": f"Process count saturated the Windows process table.",
            "LOG_ALERT":          f"Critical keyword found in the system event log.",
        }.get(ev, f"System anomaly detected: {ev}.")
        return (
            f"{sym} Diagnostic tools identified PID {pid} as the root cause  "
            f"abnormal resource consumption and excessive handle acquisition detected. "
            f"Recommended action: terminate PID {pid} and monitor system recovery."
        )

    def reset(self):
        self._incident = False
        self._cur_pid  = None


# 
#  TRAY ICON (Windows taskbar)
# 

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


# 
#  MAIN WINDOW
# 

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(" Autonomous SysAdmin")
        self.setMinimumSize(1280, 860)
        self.resize(1500, 960)
        self.setStyleSheet(GLOBAL_STYLE)

        self.watcher     = Watcher()
        self.ctx_builder = ContextBuilder()
        self.runner      = ToolRunner()
        self._cur_pid    = None
        self._uptime_s   = 0
        self._last_event = "NORMAL"
        self._hover_fx: dict[int, dict] = {}
        self._sounds_enabled = os.getenv("SYSADMIN_SILENT_UI", "0").strip().lower() not in {"1", "true", "yes", "on"}
        self._fx_enabled = False
        self._pending_rectify_active = False
        self._pending_rectify_pid = ""
        self._pending_rectify_rca = ""
        self._slack_current_pid = ""
        self._slack_initial_sent = False
        self._slack_followup_sent = False
        self._latest_metrics = {}
        self._latest_event = "NORMAL"

        metrics_server = start_metrics_server("gui")
        self._metrics_server = metrics_server

        self._build_ui()
        self._setup_tray()
        self._setup_worker()

        # uptime ticker
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._tick_uptime)
        self._uptime_timer.start(1000)

        # Badge pulse animation
        self._badge_phase = 0.0
        self._badge_timer = QTimer(self)
        self._badge_timer.timeout.connect(self._tick_badge_pulse)
        self._badge_timer.start(50)

        # Reminder timer for postponed rectify decision
        self._rectify_reminder_timer = QTimer(self)
        self._rectify_reminder_timer.setSingleShot(True)
        self._rectify_reminder_timer.timeout.connect(self._send_rectify_reminder)

        self.log.add("INFO", "Autonomous SysAdmin started  watching your system.")
        if metrics_server.get("started"):
            self.log.add("INFO", f"Prometheus exporter listening on :{metrics_server.get('port')}")
        elif metrics_server.get("enabled"):
            self.log.add("WARN", f"Prometheus exporter not started: {metrics_server.get('reason')}")
        self.log.add("OK",   f"Thresholds: CPU>{self.watcher.cpu_threshold}%  "
                             f"RAM>{self.watcher.memory_threshold}%  "
                             f"Disk>{self.watcher.disk_threshold}%")
        # Check disk immediately and warn if high but below threshold
        try:
            import sys
            dp = "C:\\" if sys.platform == "win32" else "/"
            disk_pct = psutil.disk_usage(dp).percent
            if disk_pct >= 90:
                self.log.add("WARN", f"Disk C: is at {disk_pct:.1f}%  consider freeing space soon.")
        except Exception:
            pass

    def _stop_rectify_timer(self):
        timer = getattr(self, "_rectify_reminder_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

    def _start_rectify_timer(self, delay_ms: int):
        timer = getattr(self, "_rectify_reminder_timer", None)
        if timer is None:
            return
        if timer.isActive():
            timer.stop()
        timer.start(delay_ms)

    #  UI BUILD 
    def _build_ui(self):
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        splash = SplashIntro()
        splash.clicked.connect(self._show_dashboard)
        self.stack.addWidget(splash)

        # Container widget for dashboard with incident card fixed at top
        dashboard_container = QWidget()
        dashboard_layout = QVBoxLayout(dashboard_container)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.setSpacing(0)

        #  incident spotlight (fixed at top, hidden by default)
        self.incident_card = QFrame()
        self.incident_card.setObjectName("IncidentCard")
        self.incident_card.setStyleSheet(f"""
            #IncidentCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0d1829, stop:1 #0f1d31);
                border: 1px solid {BORDER_LIT};
                border-radius: 12px;
                margin: 24px 24px 0px 24px;
            }}
        """)
        self.incident_card.setVisible(False)
        icl = QVBoxLayout(self.incident_card)
        icl.setContentsMargins(18, 14, 18, 14)
        icl.setSpacing(6)

        ic_head = QHBoxLayout()
        self.incident_badge = QLabel(" Normal")
        self.incident_badge.setFont(QFont("Century Gothic", 9, QFont.Weight.Bold))
        self.incident_badge.setStyleSheet(f"color: {GREEN}; background: transparent;")
        self.incident_time = QLabel("last update: --:--:--")
        self.incident_time.setFont(QFont("Century Gothic", 8))
        self.incident_time.setStyleSheet(f"color: {MUTED}; background: transparent;")
        ic_head.addWidget(self.incident_badge)
        ic_head.addStretch()
        ic_head.addWidget(self.incident_time)
        icl.addLayout(ic_head)

        self.incident_title = QLabel("No active incident. System is being monitored.")
        self.incident_title.setFont(QFont("Century Gothic", 11, QFont.Weight.Bold))
        self.incident_title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        self.incident_title.setWordWrap(True)
        icl.addWidget(self.incident_title)

        self.incident_detail = QLabel("If something looks wrong, this area will show the suspected app, the PID, and the next best action.")
        self.incident_detail.setFont(QFont("Century Gothic", 9))
        self.incident_detail.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        self.incident_detail.setWordWrap(True)
        icl.addWidget(self.incident_detail)

        dashboard_layout.addWidget(self.incident_card)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        dashboard_layout.addWidget(scroll)

        self.stack.addWidget(dashboard_container)

        root = GradientPanel()
        root.setMinimumWidth(1220)
        scroll.setWidget(root)

        main = QVBoxLayout(root)
        main.setContentsMargins(24, 20, 24, 20)
        main.setSpacing(14)

        self.body = QWidget()
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.body.setGraphicsEffect(None)
        body_lay = QVBoxLayout(self.body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(14)

        #  header bar 
        hdr = QHBoxLayout()
        logo = QLabel("AUTONOMOUS SYSADMIN")
        logo.setFont(QFont("Century Gothic", 11, QFont.Weight.Bold))
        logo.setStyleSheet(f"color: {GREEN};")
        hdr.addWidget(logo)

        # tagline
        tagline = QLabel(" AI-powered health monitor for your Windows machine")
        tagline.setFont(QFont("Century Gothic", 8))
        tagline.setStyleSheet(f"color: {MUTED};")
        hdr.addWidget(tagline)
        hdr.addStretch()

        self.status_dot = QLabel("")
        self.status_dot.setFont(QFont("Century Gothic", 11))
        self.status_dot.setStyleSheet(f"color: {ACCENT_RED};")
        self.status_lbl = QLabel("Everything looks good")
        self.status_lbl.setFont(QFont("Century Gothic", 9, QFont.Weight.Bold))
        self.status_lbl.setStyleSheet(f"color: {GREEN};")
        self.uptime_lbl = QLabel("00:00:00")
        self.uptime_lbl.setFont(QFont("Century Gothic", 9))
        self.uptime_lbl.setStyleSheet(f"color: {MUTED2};")
        self.clock_lbl  = QLabel("")
        self.clock_lbl.setFont(QFont("Century Gothic", 9))
        self.clock_lbl.setStyleSheet(f"color: {MUTED2};")

        self.header_chip = QLabel("LIVE")
        self.header_chip.setFont(QFont("Century Gothic", 8, QFont.Weight.Bold))
        self.header_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_chip.setFixedHeight(22)
        self.header_chip.setFixedWidth(42)
        self.header_chip.setStyleSheet(f"""
            QLabel {{
                background: {ACCENT_RED}1A;
                color: {ACCENT_RED};
                border: 1px solid {ACCENT_RED};
                border-radius: 11px;
                letter-spacing: 1px;
            }}
        """)

        hdr.addWidget(self.header_chip)
        hdr.addWidget(self.status_dot)
        hdr.addWidget(self.status_lbl)
        hdr.addSpacing(20)
        hdr.addWidget(self._small("running for"))
        hdr.addWidget(self.uptime_lbl)
        hdr.addSpacing(16)
        hdr.addWidget(self._small("time"))
        hdr.addWidget(self.clock_lbl)
        body_lay.addLayout(hdr)

        #  health summary strip 
        self.health_strip = QFrame()
        self.health_strip.setObjectName("HealthStrip")
        self.health_strip.setStyleSheet(f"""
            #HealthStrip {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0d1520, stop:1 #101b2a);
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
        """)
        self.health_strip.setFixedHeight(36)
        hl = QHBoxLayout(self.health_strip)
        hl.setContentsMargins(14, 0, 14, 0)
        hl.setSpacing(0)
        self.health_lbl = QLabel("  Watching quietly. The app checks CPU, memory, disk, and running apps in the background.")
        self.health_lbl.setFont(QFont("Century Gothic", 9))
        self.health_lbl.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        hl.addWidget(self.health_lbl)
        hl.addStretch()
        self.ai_status_lbl = QLabel(" AI: Ready  (CrewAI)")
        self.ai_status_lbl.setFont(QFont("Century Gothic", 8))
        self.ai_status_lbl.setStyleSheet(f"color: {MUTED}; background: transparent;")
        hl.addWidget(self.ai_status_lbl)
        body_lay.addWidget(self.health_strip)

        self.workflow = WorkflowStrip()
        body_lay.addWidget(self.workflow)

        #  metric cards row 
        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)
        self.card_cpu  = MetricCard("CPU Usage",    GREEN)
        self.card_ram  = MetricCard("Memory (RAM)", CYAN)
        disk_label = f"Disk Space {self._disk_label()}"
        self.card_disk = MetricCard(disk_label, PURPLE)
        self.card_proc = MetricCard("Process Load",  YELLOW)

        self.card_cpu.setToolTip("How hard your processor is working.\nAbove 80% = high load.")
        self.card_ram.setToolTip("How much of your RAM is being used.\nAbove 85% = low memory.")
        self.card_disk.setToolTip("How full your main drive (C:) is.\nAbove 95% = nearly full.")
        self.card_proc.setToolTip("Process-load percentage vs threshold.\nSubtitle shows actual process count.")

        for c in [self.card_cpu, self.card_ram, self.card_disk, self.card_proc]:
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            c.setMinimumHeight(360)
            c.setMaximumHeight(410)
            cards_row.addWidget(c)

        # Seed sparklines with initial values so they display immediately
        try:
            mem = psutil.virtual_memory()
            cpu_pct = min(100, psutil.cpu_percent(interval=None))
            ram_pct = mem.percent
            try:
                disk = psutil.disk_usage(self.watcher.disk_path)
            except Exception:
                disk = psutil.disk_usage("/")
            disk_pct = disk.percent
            proc_count = len([p for p in psutil.process_iter()])
            proc_pct = min(100, proc_count / self.watcher.process_count_threshold * 100)

            # Push initial readings twice to meet sparkline minimum of 2 data points
            self.card_cpu.push(cpu_pct, f"{psutil.cpu_count()} cores")
            self.card_cpu.push(cpu_pct, f"{psutil.cpu_count()} cores")
            self.card_ram.push(ram_pct, f"{mem.used/1e9:.1f} / {mem.total/1e9:.1f} GB")
            self.card_ram.push(ram_pct, f"{mem.used/1e9:.1f} / {mem.total/1e9:.1f} GB")
            self.card_disk.push(disk_pct, f"{disk.used/(1024**3):.1f} / {disk.total/(1024**3):.1f} GiB")
            self.card_disk.push(disk_pct, f"{disk.used/(1024**3):.1f} / {disk.total/(1024**3):.1f} GiB")
            self.card_proc.push(proc_pct, f"{proc_count} processes")
            self.card_proc.push(proc_pct, f"{proc_count} processes")
        except Exception:
            # If initial metrics fail, sparklines will populate once worker thread runs
            pass

        body_lay.addLayout(cards_row)

        #  middle row: investigation feed + process/actions 
        mid = QHBoxLayout()
        mid.setSpacing(14)

        # thought panel
        thought_frame = QFrame()
        thought_frame.setObjectName("TF")
        thought_frame.setStyleSheet(f"""
            #TF {{ background: {BG_CARD2}; border: 1px solid {BORDER}; border-radius: 12px; }}
        """)
        tfl = QVBoxLayout(thought_frame)
        tfl.setContentsMargins(16, 14, 16, 14)
        tfl.setSpacing(8)

        th_hdr = QHBoxLayout()
        th_title = QLabel("  AI Agent Live Thinking")
        th_title.setFont(QFont("Century Gothic", 10, QFont.Weight.Bold))
        th_title.setStyleSheet(f"color: {TEXT}; background: transparent;")
        th_title.setToolTip("Watch the AI reason through the problem step by step.\nIt decides which checks to run based on what it finds.")
        self.agent_badge = QLabel(" Watching quietly")
        self.agent_badge.setFont(QFont("Century Gothic", 9))
        self.agent_badge.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        th_hdr.addWidget(th_title)
        th_hdr.addStretch()
        th_hdr.addWidget(self.agent_badge)
        tfl.addLayout(th_hdr)

        self.thoughts = ThoughtWidget()
        self.thoughts.setMinimumHeight(240)
        tfl.addWidget(self.thoughts)

        th_status = QHBoxLayout()
        self.thinking_indicator = ThinkingIndicator()
        self.thinking_indicator.set_active(False)
        th_status.addWidget(self.thinking_indicator)
        th_status.addStretch()
        tfl.addLayout(th_status)

        mid.addWidget(thought_frame, stretch=3)

        # middle column: activity log
        self.log = LogWidget()
        self.log.setMinimumWidth(420)
        self.log.setMinimumHeight(240)
        mid.addWidget(self.log, stretch=2)

        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        # process table
        self.proctable = ProcessTable()
        self.proctable.setMinimumWidth(440)
        right_col.addWidget(self.proctable)

        #  friendly control panel 
        ctrl = QFrame()
        ctrl.setObjectName("CtrlPanel")
        ctrl.setStyleSheet(f"""
            #CtrlPanel {{
                background: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 14px;
            }}
        """)
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(12)

        # section title
        ctrl_hdr = QHBoxLayout()
        ctrl_icon = QLabel("")
        ctrl_icon.setFont(QFont("Century Gothic", 11, QFont.Weight.Bold))
        ctrl_icon.setStyleSheet(f"color: {ACCENT_TEAL}; background: transparent;")
        ctrl_title = QLabel("Actions")
        ctrl_title.setFont(QFont("Century Gothic", 11, QFont.Weight.Bold))
        ctrl_title.setStyleSheet(f"color: {TEXT_BRIGHT}; background: transparent;")
        self.actions_incident_badge = QLabel("incident active")
        self.actions_incident_badge.setFont(QFont("Century Gothic", 8, QFont.Weight.Bold))
        self.actions_incident_badge.setStyleSheet(
            f"background: {ACCENT_RED}1A; color: {ACCENT_RED}; border: 1px solid {ACCENT_RED}; border-radius: 10px; padding: 2px 8px;"
        )
        self.actions_incident_badge.hide()
        ctrl_hdr.addWidget(ctrl_icon)
        ctrl_hdr.addWidget(ctrl_title)
        ctrl_hdr.addStretch()
        ctrl_hdr.addWidget(self.actions_incident_badge)
        cl.addLayout(ctrl_hdr)

        ctrl_sub = QLabel("Choose one action. The buttons below are the safest next steps.")
        ctrl_sub.setFont(QFont("Century Gothic", 8))
        ctrl_sub.setStyleSheet(f"color: {TEXT_MID}; background: transparent;")
        cl.addWidget(ctrl_sub)

        # big friendly buttons with descriptions
        self.demo_btn = self._friendly_btn(
            "Start a live check",
            "Look at your system now and find what is slowing it down",
            ACCENT_TEAL, self._simulate,
            "M13 3L4 14h7l-1 7 10-12h-7z"
        )
        cl.addWidget(self.demo_btn)

        self.kill_btn2 = self._friendly_btn(
            "Stop the culprit",
            "Close the app the AI thinks is causing the slowdown",
            ACCENT_RED, self._kill_pid,
            "M6 7h12v2H6zm2 4h8v9H8zm3-8h2v3h-2z"
        )
        cl.addWidget(self.kill_btn2)

        self.slack_btn2 = self._friendly_btn(
            "Send report to Slack",
            "Share the full report with your team in Slack",
            ACCENT_CYAN, self._send_slack,
            "M3 5h18v14H3zm2 2v10h14V7zm2 3h10v2H7z"
        )
        cl.addWidget(self.slack_btn2)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {BORDER_MID}; border: none;")
        cl.addWidget(divider)

        self.reset_btn2 = self._friendly_btn(
            "Reset view",
            "Clear the alert and return to normal monitoring",
            GREEN, self._reset,
            "M12 5V2L8 6l4 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z",
            neutral=False
        )
        cl.addWidget(self.reset_btn2)

        # status row
        self.pid_badge = QFrame()
        self.pid_badge.setObjectName("PidBadge")
        self.pid_badge.setStyleSheet(f"""
            #PidBadge {{
                background: {BG_ELEVATED};
                border: 1px solid {BORDER_DIM};
                border-radius: 8px;
            }}
        """)
        pbl = QHBoxLayout(self.pid_badge)
        pbl.setContentsMargins(10, 7, 10, 7)
        self._pid_icon = QLabel("")
        self._pid_icon.setFont(QFont("Century Gothic", 9, QFont.Weight.Bold))
        self._pid_icon.setStyleSheet("background: transparent;")
        self._pid_text = QLabel("No active incident  monitoring is running normally")
        self._pid_text.setFont(QFont("Century Gothic", 9))
        self._pid_text.setStyleSheet(f"color: {TEXT_MID}; background: transparent;")
        self._pid_text.setWordWrap(True)
        pbl.addWidget(self._pid_icon)
        pbl.addWidget(self._pid_text)
        pbl.addStretch()
        cl.addWidget(self.pid_badge)

        right_col.addWidget(ctrl)
        right_col.addStretch()

        mid.addLayout(right_col, stretch=2)
        body_lay.addLayout(mid)

        #  RCA card 
        self.rca = RCACard(
            on_kill=self._kill_pid,
            on_slack=self._send_slack,
            on_reset=self._reset,
            parent=self,
        )
        self.rca.hide()
        dashboard_layout.insertWidget(0, self.rca)

        #  AI explanation banner (always visible at bottom) 
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
        abl.setContentsMargins(16, 10, 16, 10)
        abl.setSpacing(12)

        brain_icon = QLabel("")
        brain_icon.setFont(QFont("Century Gothic", 16))
        brain_icon.setStyleSheet("background: transparent;")
        brain_icon.setFixedWidth(28)

        ai_text = QLabel(
            "<b style='color:#00c3ff'>How it works:</b>  "
            "The app watches your computer in the background. If something looks wrong, it checks the busiest apps, "
            "explains the likely cause in plain English, and suggests the next safest action. "
            "<span style='color:#64748b'>  Local CrewAI crew  no browser  no cloud required.</span>"
        )
        ai_text.setFont(QFont("Century Gothic", 8))
        ai_text.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        ai_text.setWordWrap(True)
        ai_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        abl.addWidget(brain_icon, alignment=Qt.AlignmentFlag.AlignTop)
        abl.addWidget(ai_text)
        body_lay.addWidget(ai_banner)

        main.addWidget(self.body)

        # Add depth and polish to main cards/panels.
        self._apply_shadow(self.health_strip, CYAN)
        self._apply_shadow(self.incident_card, CYAN)
        self._apply_shadow(thought_frame, CYAN)
        self._apply_shadow(self.proctable, PURPLE)
        self._apply_shadow(ctrl, GREEN)
        self._apply_shadow(self.rca, RED)
        self._apply_shadow(self.log, CYAN)
        self._apply_shadow(ai_banner, CYAN)
        self._apply_shadow(self.pid_badge, YELLOW)
        for c in [self.card_cpu, self.card_ram, self.card_disk, self.card_proc]:
            self._apply_shadow(c, CYAN)

        for widget, color in [
            (self.health_strip, CYAN),
            (self.workflow, CYAN),
            (self.incident_card, CYAN),
            (self.proctable, PURPLE),
            (ctrl, GREEN),
            (self.rca, RED),
            (self.log, CYAN),
            (ai_banner, CYAN),
            (self.pid_badge, YELLOW),
            (self.card_cpu, GREEN),
            (self.card_ram, CYAN),
            (self.card_disk, PURPLE),
            (self.card_proc, YELLOW),
        ]:
            self._enable_hover_glow(widget, color)

    def _apply_shadow(self, widget: QWidget, color: str):
        if not self._fx_enabled:
            return
        fx = QGraphicsDropShadowEffect(self)
        glow = QColor(color)
        glow.setAlpha(70)
        fx.setBlurRadius(22)
        fx.setOffset(0, 8)
        fx.setColor(glow)
        widget.setGraphicsEffect(fx)

    def _enable_hover_glow(self, widget: QWidget, color: str, base_blur: int = 22, hover_blur: int = 34):
        if not self._fx_enabled:
            return
        fx = widget.graphicsEffect()
        if not isinstance(fx, QGraphicsDropShadowEffect):
            fx = QGraphicsDropShadowEffect(self)
            widget.setGraphicsEffect(fx)

        base = QColor(color)
        base.setAlpha(70)
        hover = QColor(color)
        hover.setAlpha(150)
        fx.setColor(base)
        fx.setBlurRadius(base_blur)
        fx.setOffset(0, 8)

        self._hover_fx[id(widget)] = {
            "effect": fx,
            "base_color": base,
            "hover_color": hover,
            "base_blur": base_blur,
            "hover_blur": hover_blur,
        }
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if not self._fx_enabled:
            return super().eventFilter(obj, event)
        state = self._hover_fx.get(id(obj))
        if state is not None:
            fx = state["effect"]
            if event.type() == QEvent.Type.Enter:
                fx.setColor(state["hover_color"])
                self._animate_shadow(fx, state["base_blur"], state["hover_blur"])
            elif event.type() == QEvent.Type.Leave:
                fx.setColor(state["base_color"])
                self._animate_shadow(fx, state["hover_blur"], state["base_blur"])
        return super().eventFilter(obj, event)

    def _ui_click(self):
        if self._sounds_enabled:
            QApplication.beep()

    def _ui_alert(self):
        if self._sounds_enabled:
            QApplication.beep()

    def _animate_shadow(self, effect: QGraphicsDropShadowEffect, start: int, end: int):
        if not self._fx_enabled:
            return
        anim = QPropertyAnimation(effect, b"blurRadius", self)
        anim.setDuration(180)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._shadow_anim = anim

    def showEvent(self, event):
        super().showEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "rca") and self.stack.currentIndex() == 1:
            self.rca.updateGeometry()

    def _position_rca_popup(self):
        # The RCA card now lives in the normal layout flow, so no manual positioning is needed.
        return

    def _show_dashboard(self):
        self.stack.setCurrentIndex(1)
        self.showFullScreen()
        self._position_rca_popup()

    def _small(self, text: str) -> QLabel:
        l = QLabel(text)
        l.setFont(QFont("Century Gothic", 8))
        l.setStyleSheet(f"color: {MUTED};")
        return l

    def _disk_label(self) -> str:
        path = (getattr(self.watcher, "disk_path", "") or "").strip()
        if not path:
            return ""
        if len(path) >= 2 and path[1] == ":":
            return path[:2]
        return path

    def _has_real_pid(self) -> bool:
        return str(self._cur_pid).isdigit()

    def _resolve_action_pid(self):
        """Find a numeric PID for kill action from UI/state/fallback sources."""
        current_pid = os.getpid()

        if self._has_real_pid():
            pid = int(self._cur_pid)
            if pid not in (0, 4, current_pid):
                return pid

        import re

        # 1) RCA badge text: "Alert PID: 12345"
        try:
            txt = self.rca.pid_lbl.text() or ""
            m = re.search(r"\bAlert\s*PID\s*[:=#-]?\s*(\d{2,7})\b", txt, re.IGNORECASE)
            if m:
                pid = int(m.group(1))
                if pid not in (0, 4, current_pid):
                    return pid
        except Exception:
            pass

        # 2) Last RCA body text may include "PID 12345"
        try:
            txt = self._last_rca or ""
            m = re.search(r"\bPID\s*[:=#-]?\s*(\d{2,7})\b", txt, re.IGNORECASE)
            if m:
                pid = int(m.group(1))
                if pid not in (0, 4, current_pid):
                    return pid
        except Exception:
            pass

        # 3) Live fallback from diagnostics tools
        try:
            pid = self.worker._fallback_pid()
            if str(pid).isdigit():
                pid = int(pid)
                if pid not in (0, 4, current_pid):
                    return pid
        except Exception:
            pass

        return None

    def _fallback_live_pid(self, blocked: set[int]):
        """Pick a live PID from top-process output, excluding blocked PIDs."""
        import re

        try:
            out = self.runner.check_processes()
        except Exception:
            return None

        for match in re.finditer(r"PID[=:\s]+(\d+)", out, re.IGNORECASE):
            try:
                pid = int(match.group(1))
            except Exception:
                continue
            if pid not in blocked:
                return pid
        return None

    def _describe_pid(self, pid: int) -> dict:
        """Return best-effort live details for a PID."""
        details = {"name": "unknown", "exe": "(unavailable)", "cmd": "(unavailable)"}
        try:
            proc = psutil.Process(pid)
            details["name"] = proc.name() or "unknown"
            try:
                details["exe"] = proc.exe() or "(unavailable)"
            except Exception:
                pass
            try:
                cmdline = proc.cmdline()
                details["cmd"] = " ".join(cmdline) if cmdline else "(unavailable)"
            except Exception:
                pass
        except Exception:
            pass
        return details

    def _set_incident_spotlight(self, badge: str, badge_color: str, title: str, detail: str):
        self.incident_badge.setText(badge)
        self.incident_badge.setStyleSheet(f"color: {badge_color}; background: transparent;")
        self.incident_title.setText(title)
        self.incident_detail.setText(detail)
        self.incident_time.setText(f"last update: {datetime.now().strftime('%H:%M:%S')}")

    def _vdivider(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setStyleSheet(f"color: {BORDER_LIT};")
        f.setFixedWidth(1)
        return f

    def _action_btn(self, label: str, color: str, slot) -> QPushButton:
        b = QPushButton(label)
        b.setFont(QFont("Century Gothic", 9))
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

    def _friendly_btn(self, title: str, desc: str, color: str, slot, icon_path: str, neutral: bool = False) -> QWidget:
        """Action tile with icon block, text stack, and chevron."""
        btn = QPushButton()
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(68)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Keep all action tiles visually uniform with a subtle translucent base.
        bg_fill = "rgba(17, 29, 53, 0.58)"
        border_col = color if not neutral else f"{TEXT_DIM}73"
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg_fill};
                border: 1px solid {border_col};
                border-radius: 10px;
                text-align: left;
                padding: 0px 10px;
            }}
            QPushButton:hover {{
                background: rgba(22, 32, 64, 0.72);
                border: 1px solid {BORDER_STRONG};
                color: {TEXT};
            }}
            QPushButton:pressed {{ background: rgba(17, 29, 53, 0.82); }}
        """)

        # Use a layout inside the button via a child widget trick
        inner = QWidget(btn)
        inner.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        il = QHBoxLayout(inner)
        il.setContentsMargins(12, 10, 12, 10)
        il.setSpacing(10)

        icon_box = QFrame()
        icon_box.setFixedSize(32, 32)
        icon_box.setStyleSheet(
            f"background: {color}26; border: 1px solid {color}; border-radius: 8px;"
        )
        icon_l = QVBoxLayout(icon_box)
        icon_l.setContentsMargins(0, 0, 0, 0)
        icon_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(svg_icon_pixmap(icon_path, size=16, color="#ffffff"))
        icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        icon_l.addWidget(icon_lbl)

        text_col = color if not neutral else TEXT_MID
        txt_wrap = QVBoxLayout()
        txt_wrap.setContentsMargins(0, 0, 0, 0)
        txt_wrap.setSpacing(2)
        t = QLabel(title)
        t.setFont(QFont("Century Gothic", 10, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {text_col}; background: transparent;")
        t.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        d = QLabel(desc)
        d.setFont(QFont("Century Gothic", 8))
        d.setStyleSheet(f"color: {TEXT_MID}; background: transparent;")
        d.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        d.setWordWrap(False)
        txt_wrap.addWidget(t)
        txt_wrap.addWidget(d)

        chev = QLabel("")
        chev.setFont(QFont("Century Gothic", 14, QFont.Weight.Bold))
        chev.setStyleSheet(f"color: {TEXT_DIM}; background: transparent;")
        chev.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        il.addWidget(icon_box)
        il.addLayout(txt_wrap, stretch=1)
        il.addWidget(chev, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        btn._glow_filter = ButtonGlowFilter(btn, accent=NEON_BLUE, glow=NEON_BLUE)
        btn.clicked.connect(lambda: (self._ui_click(), slot()))

        # Resize inner widget when button resizes
        def _resize(event, b=btn, w=inner):
            w.setGeometry(0, 0, b.width(), b.height())
            QPushButton.resizeEvent(b, event)
        btn.resizeEvent = _resize
        inner.setGeometry(0, 0, 340, 64)

        return btn

    #  TRAY 
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(_make_tray_icon("green"))
        self.tray.setToolTip("SysAdmin AI  Watching")

        menu = QMenu()
        menu.addAction(" Open Dashboard",    self.show_window)
        menu.addAction(" Show Last RCA",     self._show_rca_toast)
        menu.addSeparator()
        menu.addAction(" Run Live AI Check",  self._simulate)
        menu.addSeparator()
        menu.addAction(" Quit",               self._quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_window()
                                    if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()
        self._last_rca = ""

    def show_window(self):
        self._show_dashboard()
        self.activateWindow()
        self.raise_()

    def _show_rca_toast(self):
        if self._last_rca:
            self.tray.showMessage("Last RCA", self._last_rca[:200],
                                  QSystemTrayIcon.MessageIcon.Warning, 5000)

    #  WORKER WIRING 
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
        self._latest_metrics = dict(m or {})
        self._latest_event = ev
        update_system_metrics(m, ev, str(m.get("risk_level", "safe")))
        if hasattr(self, "thinking_indicator"):
            self.thinking_indicator.set_active(ev != "NORMAL", CYAN if ev == "NORMAL" else YELLOW)

        if ev != self._last_event and ev != "NORMAL" and self._last_event == "NORMAL":
            self._ui_alert()
        self._last_event = ev

        # update cards
        self.card_cpu.push(m["cpu_usage"],
                           f"{psutil.cpu_count()} cores")
        self.card_ram.push(m["memory_usage"],
                           f"{m.get('memory_used_gb', 0.0):.1f} / {m.get('memory_total_gb', 0.0):.1f} GB")
        self.card_disk.push(m["disk_usage"],
                            f"{m.get('disk_used_gb', 0.0):.1f} / {m.get('disk_total_gb', 0.0):.1f} GiB")
        pct = min(100, m["process_count"] / self.watcher.process_count_threshold * 100)
        self.card_proc.push(pct, f"{m['process_count']} processes (threshold {self.watcher.process_count_threshold})")

        # Process table rows are prepared in the worker thread to keep UI responsive.
        rows = m.get("top_processes", []) if isinstance(m, dict) else []
        self.proctable.update_procs(rows)

        # update tray + status
        status_msgs = {
            "NORMAL":           ("Everything looks good",    GREEN),
            "CPU_SPIKE":        (" CPU is overloaded",      RED),
            "MEMORY_SPIKE":     (" Running low on memory",  RED),
            "DISK_SPIKE":       (" Disk is almost full",    RED),
            "HIGH_PROCESS_COUNT": (" Too many apps running", YELLOW),
            "LOG_ALERT":        (" System error detected",  RED),
        }
        msg, col = status_msgs.get(ev, (ev, RED))
        if ev != "NORMAL":
            self.tray.setIcon(_make_tray_icon("red"))
            self.tray.setToolTip(f"SysAdmin AI  {msg}")
            self.status_dot.setStyleSheet(f"color: {ACCENT_RED};")
            self.status_lbl.setStyleSheet(f"color: {col};")
            self.status_lbl.setText(msg)
            self._set_incident_spotlight(
                " Active Incident",
                RED,
                f"{msg}",
                "The AI agent is collecting evidence and will generate a clear root-cause report."
            )
        else:
            col = YELLOW if m["cpu_usage"] > 60 or m["memory_usage"] > 70 else GREEN
            self.tray.setIcon(_make_tray_icon("yellow" if col == YELLOW else "green"))
            self.tray.setToolTip("SysAdmin AI  Everything looks good")
            self.status_dot.setStyleSheet(f"color: {ACCENT_RED};")
            self.status_lbl.setStyleSheet(f"color: {col};")
            self.status_lbl.setText("Everything looks good")
            self._set_incident_spotlight(
                " Normal",
                GREEN,
                "No active incident. System is being monitored.",
                "Live checks are running for CPU, memory, disk, process count, and logs."
            )

        # clock
        self.clock_lbl.setText(datetime.now().strftime("%H:%M:%S"))

    def _on_thought(self, icon: str, text: str):
        self.thoughts.add(icon, text)
        if hasattr(self, "thinking_indicator") and any(token in text.lower() for token in ("investigating", "analyzing", "waking", "thinking", "report")):
            self.thinking_indicator.set_active(True, CYAN)

    def _on_rca(self, rca: str, pid: str):
        pid_key = str(pid)
        if pid_key != self._slack_current_pid:
            self._slack_current_pid = pid_key
            self._slack_initial_sent = False
            self._slack_followup_sent = False
            self._stop_rectify_timer()

        self._cur_pid = pid
        self._last_rca = rca
        self._pending_rectify_active = False
        self._pending_rectify_pid = str(pid)
        self._pending_rectify_rca = rca or ""
        self._pid_icon.setText("")
        self._pid_icon.setStyleSheet(f"color: {ACCENT_RED}; background: transparent;")
        if str(pid).isdigit():
            info = self._describe_pid(int(pid))
            self._pid_text.setText(f"CULPRIT PID: {pid}  |  {info['name']}")
            self._pid_text.setToolTip(f"EXE: {info['exe']}\nCMD: {info['cmd']}")
            self.log.add("ERR", f"CULPRIT PID CONFIRMED: {pid} ({info['name']})")
        else:
            self._pid_text.setText(f"CULPRIT PID: {pid}")
            self.log.add("WARN", f"Culprit PID reported: {pid}")
        self._pid_text.setStyleSheet(f"color: {ACCENT_RED}; background: transparent;")
        if hasattr(self, "actions_incident_badge"):
            self.actions_incident_badge.show()
        self._set_incident_spotlight(
            " Action Needed",
            YELLOW,
            f"Issue isolated to CULPRIT PID {pid}.",
            "Review the report below, then choose Stop the Problem, Notify via Slack, or Dismiss & Reset."
        )
        self.rca.show_rca(rca, pid)
        self._position_rca_popup()
        urgent, reason = self._is_slack_urgent()
        if not self._slack_initial_sent:
            if urgent:
                self.log.add("WARN", f"Urgent incident detected ({reason}). Sending Slack alert.")
            else:
                self.log.add("INFO", f"Incident below urgent threshold ({reason}). Sending a single Slack alert only.")
            QTimer.singleShot(250, lambda: self._auto_send_slack_from_alert(rca, pid, urgent))
        else:
            self.log.add("INFO", f"Slack alert already sent for PID {pid}; suppressing repeats.")
            if not urgent and not self._slack_followup_sent:
                self._start_rectify_timer(60 * 60 * 1000 * 2)
        self.tray.showMessage(
            " Incident Detected",
            f"PID {pid} identified. Click to open dashboard.",
            QSystemTrayIcon.MessageIcon.Warning, 6000
        )

    def _is_slack_urgent(self):
        """Return (is_urgent, reason) based on strict urgency rules."""
        m = self._latest_metrics or {}
        ev = self._latest_event or "NORMAL"
        cpu = float(m.get("cpu_usage", 0.0) or 0.0)
        mem = float(m.get("memory_usage", 0.0) or 0.0)
        proc_count = int(m.get("process_count", 0) or 0)

        # Exact urgency tuning:
        # 1) process takes too much memory
        # 2) compute speed is decreasing (process overload)
        # 3) CPU is overworked
        # 4) explicit CPU spikes
        mem_urgent_threshold = max(float(getattr(self.watcher, "memory_threshold", 85)), 90.0)
        cpu_urgent_threshold = max(float(getattr(self.watcher, "cpu_threshold", 80)), 88.0)
        process_urgent_threshold = int(max(1, getattr(self.watcher, "process_count_threshold", 300)) * 1.15)

        memory_pressure = mem >= mem_urgent_threshold
        cpu_overwork = cpu >= cpu_urgent_threshold
        cpu_spike = ev == "CPU_SPIKE" and cpu >= 85.0

        # "Decreasing speed of compute" approximation: very high process count with sustained high CPU.
        compute_slowdown = (
            ev == "HIGH_PROCESS_COUNT"
            and proc_count >= process_urgent_threshold
            and cpu >= 80.0
        )

        if cpu_spike:
            return True, "cpu spike"
        if memory_pressure:
            return True, "high memory pressure"
        if cpu_overwork:
            return True, "cpu overwork"
        if compute_slowdown:
            return True, "compute slowdown from process overload"
        return False, "below urgent thresholds"

    def _auto_send_slack_from_alert(self, rca_text: str, pid: str, urgent: bool):
        if self._slack_initial_sent:
            return
        self._slack_initial_sent = True
        self.log.add("INFO", "Sending Slack alert...")
        self.rca.set_slack_status("Slack: sending incident report", ACCENT_CYAN)
        severity = "URGENT" if urgent else "NOTICE"
        decision_text = (
            f"[{severity}] Main cause identified: PID {pid}.\n"
            f"{rca_text}\n\n"
            "Action required: rectify now?"
        )
        ok, code, message = self._send_slack_payload(decision_text)
        if ok:
            self.log.add("OK", "Sent the alert!")
            if urgent:
                self.rca.set_slack_status("Slack: urgent alert sent", GREEN)
            else:
                self.rca.set_slack_status("Slack: notice sent; follow-up in 2 hours", GREEN)
                self._start_rectify_timer(60 * 60 * 1000 * 2)
            self._ask_rectify_decision(pid)
        elif code == "missing-webhook":
            self.log.add("WARN", "Slack webhook not configured. Auto-send skipped.")
            self.rca.set_slack_status("Slack: webhook not configured", ACCENT_AMBER)
            self._slack_initial_sent = False
        else:
            self.log.add("ERR", f"Slack auto-send failed: {message}")
            self.rca.set_slack_status("Slack: send failed", ACCENT_RED)
            self._slack_initial_sent = False

    def _ask_rectify_decision(self, pid: str):
        from PyQt6.QtWidgets import QMessageBox

        prompt = QMessageBox(self)
        prompt.setWindowTitle("Rectify Incident")
        prompt.setIcon(QMessageBox.Icon.Warning)
        prompt.setText(f"Root cause identified (PID {pid}).")
        prompt.setInformativeText("Do you want to rectify it now?")
        prompt.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        prompt.setDefaultButton(QMessageBox.StandardButton.Yes)
        result = prompt.exec()

        if result == QMessageBox.StandardButton.Yes:
            self.log.add("INFO", "User chose to rectify now.")
            self._pending_rectify_active = False
            self._stop_rectify_timer()
            if str(pid).isdigit():
                self._cur_pid = str(pid)
            self._kill_pid()
            self._send_slack_payload(f"User selected RECTIFY. Remediation initiated for PID {pid}.")
        else:
            self.log.add("WARN", "User postponed rectify. Reminder scheduled in 2 hours.")
            self._pending_rectify_active = True
            self._start_rectify_timer(60 * 60 * 1000 * 2)

    def _send_rectify_reminder(self):
        if not self._pending_rectify_active:
            return
        pid = self._pending_rectify_pid or "N/A"
        self._slack_followup_sent = True
        self.log.add("INFO", "Sending 2-hour follow-up reminder...")
        self.tray.showMessage(
            "Reminder",
            f"Incident for PID {pid} is still pending. Please choose Rectify or Reset.",
            QSystemTrayIcon.MessageIcon.Warning,
            6000,
        )
        ok, _, _ = self._send_slack_payload(
            f"Reminder: incident for PID {pid} is still pending. Please decide whether to rectify now."
        )
        if ok:
            self.log.add("OK", "Sent 1-hour reminder to Slack.")
        else:
            self.log.add("WARN", "Unable to send 1-hour Slack reminder.")

    def _on_agent_state(self, state: str):
        labels = {
            "idle":      (" Watching quietly",          MUTED2),
            "triggered": (" Problem detected!",         YELLOW),
            "detective": (" AI is investigating...",    CYAN),
            "reporter":  (" AI is writing the report...", PURPLE),
            "done":      (" Investigation complete",     GREEN),
        }
        text, color = labels.get(state, (" Watching quietly", MUTED2))
        self.agent_badge.setText(text)
        self.agent_badge.setStyleSheet(f"color: {color}; background: transparent;")
        if hasattr(self, "actions_incident_badge"):
            if state in {"triggered", "detective", "reporter", "done"}:
                self.actions_incident_badge.show()
            else:
                self.actions_incident_badge.hide()
        if hasattr(self, "thinking_indicator"):
            self.thinking_indicator.set_active(state in {"triggered", "detective", "reporter"}, color)
        if hasattr(self, "workflow"):
            self.workflow.set_state(state)

        # also update health strip
        strip_msgs = {
            "idle":      ("  Watching quietly. The app checks CPU, memory, disk, and running apps in the background.", MUTED2),
            "triggered": ("  Something unusual was detected. The AI is starting a guided check.", YELLOW),
            "detective": ("  The AI is actively diagnosing the issue right now.", RED),
            "reporter":  ("  Investigation finished. The AI is writing a plain-English report.", RED),
            "done":      ("  A problem was found. Review the report and pick the safest next step.", YELLOW),
        }
        msg, col = strip_msgs.get(state, strip_msgs["idle"])
        self.health_lbl.setText(msg)
        self.health_lbl.setStyleSheet(f"color: {col}; background: transparent;")
        ai_states = {
            "idle":      " AI: Ready  (CrewAI)",
            "triggered": " AI: Waking up...",
            "detective": " AI: Investigating ",
            "reporter":  " AI: Writing report ",
            "done":      " AI: Done ",
        }
        self.ai_status_lbl.setText(ai_states.get(state, " AI: Ready"))

        if state == "detective":
            self._set_incident_spotlight(
                " Investigating",
                CYAN,
                "AI is checking what changed.",
                "Please wait while the agent checks process, memory, disk, and network evidence."
            )
        elif state == "reporter":
            self._set_incident_spotlight(
                " Writing Report",
                PURPLE,
                "The investigation is done. Now the app is writing your summary.",
                "The final report will explain what happened and what to do next in simple language."
            )
        elif state == "done" and not self._has_real_pid():
            self._set_incident_spotlight(
                " Investigation Complete",
                GREEN,
                "The analysis finished, but no single PID was confirmed yet.",
                "Check the RCA section for details and recommended next steps."
            )

    #  UPTIME 
    def _tick_uptime(self):
        self._uptime_s += 1
        h = self._uptime_s // 3600
        m = (self._uptime_s % 3600) // 60
        s = self._uptime_s % 60
        self.uptime_lbl.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _tick_badge_pulse(self):
        """Animate badge pulse when incident is active."""
        import math
        self._badge_phase = (self._badge_phase + 0.05) % (2 * math.pi)
        
        # Only pulse if incident card is visible
        if self.incident_card.isVisible():
            pulse_opacity = 0.6 + 0.4 * math.sin(self._badge_phase)
            badge_text = self.incident_badge.text()
            badge_color = self.incident_badge.styleSheet()
            
            # Extract color from stylesheet and apply pulse opacity
            if "red" in badge_color.lower() or "ff3f6d" in badge_color or "ff5c5c" in badge_color:
                color = "ff3f6d"
            else:
                color = "22d3ee"
            
            self.incident_badge.setStyleSheet(
                f"color: {color}; background: transparent; opacity: {pulse_opacity};"
            )

    #  ACTIONS 
    def _simulate(self):
        self._ui_click()
        audit_action("manual_live_check", "attempted", getattr(self.worker, "_last_correlation_id", ""))
        observe_action("manual_live_check", "attempted")
        if getattr(self.worker, "_incident", False):
            self.log.add("INFO", "AI is already investigating an incident. Please wait for it to finish.")
            return

        self.log.add("INFO", " Running manual live AI check from current system metrics")
        self.thoughts.clear_thoughts()
        self._on_agent_state("triggered")

        metrics = self.watcher.get_metrics()
        primary_event, detected = self.watcher.detect_events(metrics)
        if primary_event == "NORMAL":
            primary_event = "MANUAL_HEALTH_CHECK"
            detected = ["MANUAL_HEALTH_CHECK"]

        live_ctx = self.ctx_builder.build_context(metrics, primary_event, detected)
        self.worker._incident = True

        # trigger worker inline
        t = threading.Thread(target=self.worker._run_agent, args=(live_ctx,), daemon=True)
        t.start()

    def _kill_pid(self):
        self._ui_click()
        corr_id = getattr(self.worker, "_last_correlation_id", "")
        incident_id = getattr(self.worker, "_last_incident_id", "")
        audit_action("kill_pid", "attempted", corr_id, selected_pid=self._cur_pid or "")
        observe_action("kill_pid", "attempted")
        pid = self._resolve_action_pid()
        if pid is None:
            self.log.add("INFO", "No numeric PID is available yet. Wait for analysis to finish, then try again.")
            return

        if pid in (0, 4):
            alt = self._fallback_live_pid({0, 4, os.getpid()})
            if alt is None:
                self.log.add("ERR", f"Refusing to terminate protected system PID {pid}.")
                return
            self.log.add("WARN", f"Selected PID {pid} is protected. Switching to live culprit PID {alt}.")
            pid = alt

        if pid == os.getpid():
            alt = self._fallback_live_pid({0, 4, os.getpid()})
            if alt is None:
                self.log.add("ERR", "Refusing to terminate the SysAdmin app process itself.")
                return
            self.log.add("WARN", f"PID {pid} is the SysAdmin app. Switching to live culprit PID {alt}.")
            pid = alt

        root_pid = resolve_termination_target(pid, blocked_pids={0, 4, os.getpid()})
        if root_pid != pid:
            self.log.add("WARN", f"PID {pid} is part of a worker tree. Switching termination target to root PID {root_pid}.")
            pid = root_pid

        info = self._describe_pid(pid)
        self._cur_pid = str(pid)
        self.log.add("OK", f"Target process: {info['name']} (PID {pid})")
        self.log.add("INFO", f"EXE: {info['exe']}")
        self.log.add("INFO", f"CMD: {info['cmd'][:180]}")
        self.log.add("OK", f"Sending terminate signal to {info['name']} (PID {pid})")
        result = terminate_process_tree(pid)
        terminated = bool(result.get("terminated"))

        if terminated:
            self.log.add("OK", f"{info['name']} (PID {pid}) successfully terminated.")
            audit_action("kill_pid", "executed", corr_id, pid=pid, name=info["name"])
            observe_action("kill_pid", "executed")
            get_incident_memory().update_incident_outcome(
                incident_id,
                action_taken="terminate_process",
                outcome="resolved",
                outcome_notes=f"{info['name']} ({pid}) terminated",
            )
        else:
            self.log.add("ERR", f"Process {info['name']} (PID {pid}) is still running. {result.get('error', '')}")
            audit_action("kill_pid", "failed", corr_id, pid=pid, error=result.get("error", ""))
            observe_action("kill_pid", "failed")
            get_incident_memory().update_incident_outcome(
                incident_id,
                action_taken="terminate_process",
                outcome="failed",
                outcome_notes=str(result.get("error", "still running")),
            )

        self._cur_pid = None
        self._pending_rectify_active = False
        self._stop_rectify_timer()
        self._pid_icon.setText("")
        if terminated:
            self._pid_text.setText("Process terminated successfully")
            self._pid_text.setStyleSheet(f"color: {GREEN}; background: transparent;")
            self.worker.reset()
        else:
            self._pid_text.setText("Unable to terminate process. Check permissions and try again.")
            self._pid_text.setStyleSheet(f"color: {RED}; background: transparent;")

    def _send_slack(self):
        self._ui_click()
        corr_id = getattr(self.worker, "_last_correlation_id", "")
        audit_action("send_slack", "attempted", corr_id)
        observe_action("send_slack", "attempted")
        ok, code, message = self._send_slack_payload(self._last_rca or "No RCA available.")
        if ok:
            self.log.add("OK", " RCA posted to Slack #sysadmin-alerts!")
            audit_action("send_slack", "executed", corr_id)
            observe_action("send_slack", "executed")
            self.tray.showMessage("Slack", "RCA sent to your channel.",
                                  QSystemTrayIcon.MessageIcon.Information, 3000)
        elif code == "missing-webhook":
            self._show_slack_setup_dialog()
            audit_action("send_slack", "failed", corr_id, error="missing-webhook")
            observe_action("send_slack", "failed")
        else:
            self.log.add("ERR", f"Slack send failed: {message}")
            audit_action("send_slack", "failed", corr_id, error=message)
            observe_action("send_slack", "failed")

    def _send_slack_payload(self, rca_text: str):
        webhook = self._resolve_slack_webhook()
        if not webhook:
            return False, "missing-webhook", "Slack webhook is missing"
        if "hooks.slack.com/services/" not in webhook:
            return False, "missing-webhook", "Invalid Slack webhook URL. Use an Incoming Webhook URL from Slack App settings."

        try:
            from notifier import SlackNotifier
            n = SlackNotifier(webhook_url=webhook)
            metrics = self.watcher.get_metrics()
            ctx = {
                "primary_event":  "INCIDENT",
                "cpu_usage":      metrics["cpu_usage"],
                "memory_usage":   metrics["memory_usage"],
                "disk_usage":     metrics["disk_usage"],
            }
            n.send_rca(rca_text or "No RCA available.", ctx)
            return True, "ok", "sent"
        except Exception as e:
            return False, "error", str(e)

    def _resolve_slack_webhook(self) -> str:
        webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        if webhook:
            return webhook

        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if not os.path.exists(env_path):
            return ""
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    if key.strip() == "SLACK_WEBHOOK_URL":
                        webhook = val.strip().strip('"').strip("'")
                        if webhook:
                            os.environ["SLACK_WEBHOOK_URL"] = webhook
                            return webhook
        except Exception:
            return ""
        return ""

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

        title = QLabel("  Connect Slack Alerts")
        title.setFont(QFont("Century Gothic", 13, QFont.Weight.Bold))
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
        steps.setFont(QFont("Century Gothic", 9))
        steps.setStyleSheet(f"color: {MUTED2}; background: transparent;")
        lay.addWidget(steps)

        url_input = QLineEdit()
        url_input.setPlaceholderText("https://hooks.slack.com/services/T.../B.../...")
        url_input.setFixedHeight(36)
        lay.addWidget(url_input)

        note = QLabel("This will be saved to your .env file so you only need to do this once.")
        note.setFont(QFont("Century Gothic", 8))
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
        self._ui_click()
        audit_action(
            "reset_incident",
            "executed",
            getattr(self.worker, "_last_correlation_id", ""),
            incident_id=getattr(self.worker, "_last_incident_id", ""),
        )
        observe_action("reset_incident", "executed")
        self._cur_pid = None
        self._slack_initial_sent = False
        self._slack_followup_sent = False
        self._slack_current_pid = ""
        self._pending_rectify_active = False
        self._pending_rectify_pid = ""
        self._pending_rectify_rca = ""
        self._stop_rectify_timer()
        self._pid_icon.setText("")
        self._pid_icon.setStyleSheet(f"color: {TEXT_DIM}; background: transparent;")
        self._pid_text.setText("No active incident  monitoring is running normally")
        self._pid_text.setStyleSheet(f"color: {TEXT_MID}; background: transparent;")
        if hasattr(self, "actions_incident_badge"):
            self.actions_incident_badge.hide()
        self._set_incident_spotlight(
            " Normal",
            GREEN,
            "Incident dismissed. Monitoring resumed.",
            "The AI agent is back to background watch mode."
        )
        self.thoughts.clear_thoughts()
        self._on_agent_state("idle")
        self.rca.hide_rca()
        self.tray.setIcon(_make_tray_icon("green"))
        self.worker.reset()
        self.worker._last_incident_id = ""
        self.worker._last_correlation_id = ""
        self.log.add("OK", " System reset  Watcher is monitoring again.")

    def _quit(self):
        self.worker.stop()
        self.worker.wait(2000)
        QApplication.quit()

    #  minimise to tray instead of close 
    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "SysAdmin AI", "Still watching in the background. Click tray icon to restore.",
            QSystemTrayIcon.MessageIcon.Information, 3000
        )


# 
#  ENTRY POINT
# 
if __name__ == "__main__":
    def _uncaught_excepthook(exc_type, exc_value, exc_tb):
        _write_runtime_log(
            "CRASH",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).strip()
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _uncaught_excepthook

    env_report = validate_runtime_environment(os.path.dirname(__file__))
    for warn in env_report.get("warnings", []):
        _write_runtime_log("WARN", f"Startup validation: {warn}")
    for err in env_report.get("errors", []):
        _write_runtime_log("ERR", f"Startup validation: {err}")

    app = QApplication(sys.argv)
    app.setApplicationName("" \
    " SysAdmin")
    app.setQuitOnLastWindowClosed(False)   # keep running in tray
    win = MainWindow()
    win.showFullScreen()
    rc = 0
    try:
        rc = app.exec()
    except KeyboardInterrupt:
        # Ctrl+C from terminal should exit cleanly, not look like an app crash.
        try:
            win._quit()
        except Exception:
            pass
        rc = 0
    sys.exit(rc)


