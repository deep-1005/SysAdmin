"""
tray.py — Windows System Tray Icon
────────────────────────────────────
Runs as a background thread alongside the Textual TUI.
Shows a colour-coded icon in the Windows taskbar tray:
  🟢 Green  = NORMAL
  🟡 Yellow = WARNING (threshold approaching)
  🔴 Red    = INCIDENT ACTIVE

Right-click menu:
  • Open Dashboard   → brings Textual TUI to foreground
  • Last RCA         → Windows toast notification with RCA text
  • Simulate Spike   → triggers a fake CPU spike for demo
  • Quit             → shuts down everything

Dependencies:
  pip install pystray pillow
"""

from __future__ import annotations
import threading
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as Item


# ── icon painter ─────────────────────────────────────────────
def _make_icon(color: str) -> Image.Image:
    """
    Draw a 64×64 icon:  outer ring + inner circle + small lightning bolt.
    color: "green" | "yellow" | "red"
    """
    SIZE = 64
    img  = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = {
        "green":  {"ring": (30, 200, 100),  "fill": (20, 140, 70),   "bolt": (200, 255, 220)},
        "yellow": {"ring": (240, 180, 30),   "fill": (180, 130, 10),  "bolt": (255, 240, 180)},
        "red":    {"ring": (230, 60,  60),   "fill": (170, 30,  30),  "bolt": (255, 200, 200)},
    }
    c = palette.get(color, palette["green"])

    # outer ring
    draw.ellipse([4, 4, 60, 60], outline=c["ring"], width=4)
    # filled circle
    draw.ellipse([10, 10, 54, 54], fill=c["fill"])
    # lightning bolt  ⚡
    bolt = [(32, 12), (22, 34), (30, 34), (24, 52), (42, 28), (33, 28), (40, 12)]
    draw.polygon(bolt, fill=c["bolt"])

    return img


# ── tray controller ──────────────────────────────────────────
class TrayController:
    """
    Manages the system tray icon and its menu.
    Designed to live in its own background thread.

    Usage:
        tray = TrayController()
        tray.start()                  # non-blocking, spawns thread
        tray.set_status("red")        # call from any thread
        tray.set_last_rca("PID 9912 caused CPU spike…")
        tray.stop()
    """

    def __init__(self, on_simulate=None, on_quit=None, on_open_dashboard=None):
        self._status        = "green"
        self._last_rca      = "No incidents yet."
        self._icon          = None
        self._thread        = None

        # Callbacks injected by app.py
        self._on_simulate        = on_simulate        or (lambda: None)
        self._on_quit            = on_quit            or (lambda: None)
        self._on_open_dashboard  = on_open_dashboard  or (lambda: None)

    # ── public API ───────────────────────────────────────

    def start(self):
        """Spawn the tray in its own daemon thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="tray")
        self._thread.start()

    def stop(self):
        if self._icon:
            self._icon.stop()

    def set_status(self, status: str):
        """status: 'green' | 'yellow' | 'red'"""
        self._status = status
        if self._icon:
            self._icon.icon  = _make_icon(status)
            self._icon.title = self._tooltip()
            self._icon.update_menu()

    def set_last_rca(self, rca_text: str):
        self._last_rca = rca_text[:256]   # Windows toast limit

    def notify(self, title: str, message: str):
        """Show a Windows toast notification from the tray icon."""
        if self._icon:
            self._icon.notify(message, title)

    # ── internal ─────────────────────────────────────────

    def _tooltip(self) -> str:
        labels = {"green": "● NORMAL", "yellow": "⚠ WARNING", "red": "🔴 INCIDENT"}
        return f"SysAdmin AI — {labels.get(self._status, 'UNKNOWN')}"

    def _menu(self):
        return pystray.Menu(
            Item("⚡ SysAdmin AI",       None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item("Open Dashboard",        self._do_open),
            Item("Show Last RCA",         self._do_show_rca),
            pystray.Menu.SEPARATOR,
            Item("🐒 Simulate CPU Spike", self._do_simulate),
            pystray.Menu.SEPARATOR,
            Item("Quit",                  self._do_quit),
        )

    def _run(self):
        self._icon = pystray.Icon(
            name    = "sysadmin_ai",
            icon    = _make_icon(self._status),
            title   = self._tooltip(),
            menu    = self._menu(),
        )
        self._icon.run()

    # ── menu callbacks (called on tray thread) ────────────

    def _do_open(self, icon, item):
        self._on_open_dashboard()

    def _do_show_rca(self, icon, item):
        self.notify("Last Root Cause Analysis", self._last_rca)

    def _do_simulate(self, icon, item):
        self._on_simulate()

    def _do_quit(self, icon, item):
        icon.stop()
        self._on_quit()
