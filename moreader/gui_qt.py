"""Industrial-HMI GUI for moreader, built with PySide6.

Operator screen: three read-only encapsulator tiles (recipe DINT + match state)
and a master (COS) panel showing the MO_Verified state and a live heartbeat.
The operator never types here — pressing VERIFY MO opens a modal dialog the USB
scanner sends the manufacturing-order barcode into.  MANUAL LOCKOUT clears the
verification.

Configuration screen: password protected, with tabs for PLCs (encapsulators +
master, tag names + descriptions), Scanner, Compare, Shift, and Security.
"""

from __future__ import annotations

import queue
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import ENCAP_RECIPE, MASTER_TAGS, Config, ConfigError, MoFormat, from_dict, save_config
from .plc import PLCError, build_encapsulator, build_master
from .scanner import ScannerError, SerialScanSource
from .worker import CMD_BYPASS, CMD_LOCKOUT, CMD_VERIFY, PLCWorker

# --- industrial palette ------------------------------------------------------
BG = "#0e1620"
PANEL = "#16212e"
PANEL_HI = "#1d2a3a"
EDGE = "#2a3a4d"
TEXT = "#e6edf3"
MUTED = "#8aa0b3"
ACCENT = "#2d8cf0"

STATE = {
    "MATCH": ("#15321f", "#27c46b", "MATCH"),
    "VERIFIED": ("#15321f", "#27c46b", "MO VERIFIED"),
    "MISMATCH": ("#3a1414", "#ef4444", "MISMATCH"),
    "LOCKED": ("#332708", "#eab308", "LOCKED — SCAN MO"),
    "BYPASSED": ("#2a1a3a", "#a855f7", "MO BYPASSED"),
    "DISCONNECTED": ("#1a2533", "#5b6b7b", "OFFLINE"),
}
# Big status glyph shown on each encapsulator tile.
STATE_GLYPH = {
    "MATCH": "✓",
    "MISMATCH": "✗",
    "LOCKED": "…",
    "DISCONNECTED": "–",
}
LOG_COLOR = {"ok": "#27c46b", "alarm": "#ef4444", "warn": "#eab308", "info": MUTED}

STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI', 'DejaVu Sans', sans-serif; font-size: 14px; }}
#Header {{ background: {PANEL_HI}; border-bottom: 2px solid {ACCENT}; }}
#HeaderTitle {{ font-size: 24px; font-weight: 700; color: {TEXT}; }}
#HeaderSub {{ font-size: 13px; color: {MUTED}; }}
QLabel#Clock {{ font-size: 18px; color: {TEXT}; font-weight: 600; }}
QLabel#ConnSummary {{ font-size: 14px; font-weight: 600; }}
QFrame#Tile, QFrame#Master {{ background: {PANEL}; border: 2px solid {EDGE}; border-radius: 12px; }}
QLabel#TileName, QLabel#TileStatus, QLabel#Caption, QLabel#TileModel, QLabel#TileScan,
QLabel#TileGlyph, QLabel#MasterName, QLabel#MasterStatus, QLabel#Heart, QLabel#HeartVal,
QLabel#HeaderTitle, QLabel#HeaderSub, QLabel#Clock, QLabel#ConnSummary {{ background: transparent; }}
QLabel#TileName {{ font-size: 24px; font-weight: 800; }}
QLabel#TileStatus {{ font-size: 26px; font-weight: 800; }}
QLabel#TileGlyph {{ font-size: 96px; font-weight: 900; }}
QLabel#Caption {{ font-size: 14px; color: {MUTED}; font-weight: 600; }}
QLabel#TileModel {{ font-size: 68px; font-weight: 900; }}
QLabel#TileScan {{ font-size: 30px; font-weight: 700; color: {TEXT}; }}
QLabel#MasterName {{ font-size: 22px; font-weight: 800; }}
QLabel#MasterStatus {{ font-size: 26px; font-weight: 800; }}
QLabel#Heart {{ font-size: 22px; font-weight: 800; }}
QLabel#HeartVal {{ font-size: 16px; color: {MUTED}; font-family: 'Consolas', monospace; }}
QPushButton#Verify {{ background: {ACCENT}; color: white; font-size: 26px; font-weight: 800;
    border: none; border-radius: 12px; padding: 22px; }}
QPushButton#Verify:hover {{ background: #1f6fd0; }}
QPushButton#Lockout {{ background: #5a1d1d; color: #ffd7d7; font-size: 16px; font-weight: 800;
    border: 1px solid #7a2a2a; border-radius: 10px; padding: 18px; }}
QPushButton#Lockout:hover {{ background: #6e2525; }}
QPushButton#Bypass {{ background: #2a1a3a; color: #e3ccff; font-size: 16px; font-weight: 800;
    border: 1px solid #5b3a7a; border-radius: 10px; padding: 18px; }}
QPushButton#Bypass:hover {{ background: #38214d; }}
QPushButton {{ background: {PANEL_HI}; color: {TEXT}; border: 1px solid {EDGE};
    border-radius: 8px; padding: 9px 16px; font-weight: 600; }}
QPushButton:hover {{ background: {EDGE}; }}
QPushButton#Primary {{ background: {ACCENT}; color: white; border: none; }}
QPushButton#Primary:hover {{ background: #1f6fd0; }}
QPlainTextEdit#Log {{ background: #0a1018; border: 1px solid {EDGE}; border-radius: 8px;
    font-family: 'Consolas', 'DejaVu Sans Mono', monospace; font-size: 12px; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {PANEL_HI}; border: 1px solid {EDGE};
    border-radius: 6px; padding: 7px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QGroupBox {{ border: 1px solid {EDGE}; border-radius: 10px; margin-top: 14px; padding: 12px; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {ACCENT}; }}
QTabWidget::pane {{ border: 1px solid {EDGE}; border-radius: 8px; }}
QTabBar::tab {{ background: {PANEL}; padding: 10px 18px; margin-right: 2px;
    border-top-left-radius: 8px; border-top-right-radius: 8px; font-weight: 600; }}
QTabBar::tab:selected {{ background: {PANEL_HI}; color: {ACCENT}; }}
QScrollArea {{ border: none; }}
QTableWidget {{ background: {PANEL}; gridline-color: {EDGE}; border: 1px solid {EDGE}; }}
QHeaderView::section {{ background: {PANEL_HI}; color: {MUTED}; padding: 6px; border: none; font-weight: 600; }}
QLabel#DialogTitle {{ font-size: 26px; font-weight: 800; }}
QLineEdit#ScanField {{ font-size: 28px; padding: 16px; font-family: 'Consolas', monospace; }}
"""


class Lamp(QLabel):
    def __init__(self, size: int = 26) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self._r = size // 2
        self.set_color(STATE["DISCONNECTED"][1])

    def set_color(self, color: str) -> None:
        self.setStyleSheet(
            f"background: {color}; border-radius: {self._r}px; border: 2px solid rgba(255,255,255,0.25);"
        )


class EncapsulatorTile(QFrame):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.setObjectName("Tile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(4)

        self.name_lbl = QLabel(name)
        self.name_lbl.setObjectName("TileName")
        self.name_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.name_lbl)

        # Big pass/fail glyph.
        self.glyph_lbl = QLabel("–")
        self.glyph_lbl.setObjectName("TileGlyph")
        self.glyph_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.glyph_lbl)

        self.status_lbl = QLabel("OFFLINE")
        self.status_lbl.setObjectName("TileStatus")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.status_lbl)

        lay.addSpacing(10)
        cap = QLabel("RECIPE")
        cap.setObjectName("Caption")
        cap.setAlignment(Qt.AlignCenter)
        lay.addWidget(cap)
        self.model_lbl = QLabel("—")
        self.model_lbl.setObjectName("TileModel")
        self.model_lbl.setAlignment(Qt.AlignCenter)
        self.model_lbl.setWordWrap(True)
        lay.addWidget(self.model_lbl)

        lay.addStretch(1)
        scap = QLabel("LAST SCAN")
        scap.setObjectName("Caption")
        scap.setAlignment(Qt.AlignCenter)
        lay.addWidget(scap)
        self.scan_lbl = QLabel("—")
        self.scan_lbl.setObjectName("TileScan")
        self.scan_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.scan_lbl)
        self._apply("DISCONNECTED")

    def _apply(self, state: str) -> None:
        bg, accent, text = STATE.get(state, STATE["DISCONNECTED"])
        self.setStyleSheet(f"QFrame#Tile {{ background: {bg}; border: 3px solid {accent}; border-radius: 14px; }}")
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {accent};")
        self.glyph_lbl.setText(STATE_GLYPH.get(state, "–"))
        self.glyph_lbl.setStyleSheet(f"color: {accent};")

    def update_from(self, data: dict) -> None:
        state = data.get("state", "DISCONNECTED")
        if not data.get("connected", False):
            state = "DISCONNECTED"
        self.name_lbl.setText(data.get("name", self.name_lbl.text()))
        recipe = data.get("recipe")
        # Show each recipe number on its own line (stacked, no dashes).
        tokens = [t for t in re.split(r"\D+", str(recipe))
                  if t] if recipe not in (None, "") else []
        if not tokens:
            self.model_lbl.setText("—")
            size = 68
        else:
            self.model_lbl.setText("\n".join(tokens))
            size = {1: 68, 2: 48, 3: 38}.get(len(tokens), 30)   # 4+ -> 30
        self.model_lbl.setStyleSheet(f"font-size: {size}px; font-weight: 900; background: transparent;")
        scanned = data.get("scanned")
        self.scan_lbl.setText("—" if scanned is None else str(scanned))
        self._apply(state)


class MasterPanel(QFrame):
    def __init__(self, name: str = "COS") -> None:
        super().__init__()
        self.setObjectName("Master")
        self._last_hb = None
        self._pulse = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(18)

        left = QVBoxLayout()
        left.setSpacing(2)
        self.name_lbl = QLabel(f"{name}  ·  MASTER")
        self.name_lbl.setObjectName("MasterName")
        left.addWidget(self.name_lbl)
        cap = QLabel("MASTER RUN GATE (MO_Verified)")
        cap.setObjectName("Caption")
        left.addWidget(cap)
        self.flags_lbl = QLabel("")
        self.flags_lbl.setObjectName("Caption")
        left.addWidget(self.flags_lbl)
        lay.addLayout(left)
        lay.addStretch(1)

        self.lamp = Lamp(30)
        lay.addWidget(self.lamp)
        self.status_lbl = QLabel("OFFLINE")
        self.status_lbl.setObjectName("MasterStatus")
        lay.addWidget(self.status_lbl)
        lay.addSpacing(20)

        hb = QVBoxLayout()
        hb.setSpacing(0)
        hb.setAlignment(Qt.AlignCenter)
        self.heart = QLabel("♥")
        self.heart.setObjectName("Heart")
        self.heart.setAlignment(Qt.AlignCenter)
        self.heart_val = QLabel("—")
        self.heart_val.setObjectName("HeartVal")
        self.heart_val.setAlignment(Qt.AlignCenter)
        hb.addWidget(self.heart)
        hb.addWidget(self.heart_val)
        lay.addLayout(hb)
        self._apply("DISCONNECTED")

    def _apply(self, state: str) -> None:
        bg, accent, text = STATE.get(state, STATE["DISCONNECTED"])
        self.setStyleSheet(f"QFrame#Master {{ background: {bg}; border: 2px solid {accent}; border-radius: 12px; }}")
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {accent};")
        self.lamp.set_color(accent)

    def pulse(self, value, mode: str = "toggle") -> None:
        """Update just the heartbeat indicator (called live from its own thread)."""

        if value != self._last_hb:
            self._pulse = not self._pulse
            self._last_hb = value
        self.heart.setStyleSheet(f"color: {'#ef4444' if self._pulse else '#7a2a2a'};")
        self.heart_val.setText(("ON" if value else "OFF") if mode == "toggle" else str(value))

    def update_from(self, data: dict, secondary: dict | None = None) -> None:
        self.name_lbl.setText(f"{data.get('name', 'COS')}  ·  MASTER")
        if not data.get("connected", False):
            self._apply("DISCONNECTED")
            self.heart.setStyleSheet(f"color: {MUTED};")
            self.heart_val.setText("offline")
            self.flags_lbl.setText("")
            return
        if data.get("mo_bypassed"):
            self._apply("BYPASSED")
        elif data.get("mo_verified"):
            self._apply("VERIFIED")
        else:
            self._apply("LOCKED")
        self.pulse(data.get("heartbeat", 0), data.get("heartbeat_mode", "toggle"))

        flags = []
        if data.get("cycle_stop"):
            flags.append("⏹ CYCLE STOP REQUESTED")
        if secondary and secondary.get("enabled"):
            matched = secondary.get("matched")
            scanned = secondary.get("scanned")
            if matched is None:
                flags.append("Battery: —")
            elif matched:
                flags.append(f"Battery ✓ {scanned}")
            else:
                flags.append("Battery ✗")
        self.flags_lbl.setText("    ".join(flags))


class ScanDialog(QDialog):
    """Modal dialog the USB scanner sends one or more barcodes into.

    There is no editable text field: the dialog captures the scanner's
    keystrokes directly, so the operator cannot type an order in by hand.  When
    given several steps (e.g. MO then battery label) it walks through them and
    returns all captured values.
    """

    def __init__(self, steps, parent=None, scan_source=None) -> None:
        super().__init__(parent)
        self.steps = steps                 # list of (key, title, hint)
        self.index = 0
        self.values: dict[str, str] = {}
        self._buffer = ""
        self._source = scan_source         # SerialScanSource, or None for keyboard
        self.setWindowTitle("Scan")
        self.setModal(True)
        self.setMinimumWidth(580)
        self.setStyleSheet(STYLESHEET)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 28, 34, 28)
        lay.setSpacing(14)

        self.step_lbl = QLabel("")
        self.step_lbl.setStyleSheet(f"color: {ACCENT}; font-weight: 700;")
        self.step_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.step_lbl)

        self.title = QLabel("")
        self.title.setObjectName("DialogTitle")
        self.title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.title)

        self.hint = QLabel("")
        self.hint.setStyleSheet(f"color: {MUTED};")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        # Read-only display of what the scanner has sent — not an input box.
        self.display = QLabel("waiting for scan…")
        self.display.setObjectName("ScanDisplay")
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setStyleSheet(
            f"font-size: 30px; padding: 18px; font-family: 'Consolas', monospace;"
            f"color: {TEXT}; background: {PANEL_HI}; border: 2px dashed {EDGE}; border-radius: 8px;"
        )
        lay.addWidget(self.display)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setFocusPolicy(Qt.NoFocus)   # keep keystrokes flowing to the dialog
        cancel.clicked.connect(self.reject)
        self.ok_btn = QPushButton("OK")
        self.ok_btn.setObjectName("Primary")
        self.ok_btn.setFocusPolicy(Qt.NoFocus)
        self.ok_btn.clicked.connect(self._advance)
        row.addWidget(cancel)
        row.addWidget(self.ok_btn)
        lay.addLayout(row)

        self.setFocusPolicy(Qt.StrongFocus)

        # Serial scanners deliver barcodes asynchronously; poll the source.
        self._serial_timer = None
        if self._source is not None:
            self._serial_timer = QTimer(self)
            self._serial_timer.timeout.connect(self._poll_serial)
        self._show_step()

    def _show_step(self) -> None:
        key, title, hint = self.steps[self.index]
        self.title.setText(title)
        self.hint.setText(hint)
        self.step_lbl.setText(
            f"Step {self.index + 1} of {len(self.steps)}" if len(self.steps) > 1 else ""
        )
        self.ok_btn.setText("OK" if self.index == len(self.steps) - 1 else "Next ›")
        self._buffer = ""
        self.display.setText("waiting for scan…")
        if self._source is not None:
            self._source.flush()          # each step waits for a fresh scan

    def _poll_serial(self) -> None:
        code = self._source.poll()
        if code:
            self._buffer = code
            self.display.setText(code)
            self._advance()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setFocus()
        if self._source is not None:
            self._source.flush()          # drop any stale barcode
            self._serial_timer.start(50)

    def _advance(self) -> None:
        if not self._buffer.strip():
            return
        key = self.steps[self.index][0]
        self.values[key] = self._buffer.strip()
        if self.index < len(self.steps) - 1:
            self.index += 1
            self._show_step()
        else:
            self.accept()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.reject()
            return
        if self._source is not None:
            return          # serial mode: ignore the keyboard entirely
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._advance()
            return
        if key == Qt.Key_Backspace:
            self._buffer = self._buffer[:-1]
        else:
            text = event.text()
            if text and text.isprintable():
                self._buffer += text
        self.display.setText(self._buffer or "waiting for scan…")

    def done(self, result: int) -> None:
        if self._serial_timer is not None:
            self._serial_timer.stop()
        super().done(result)

    def result_values(self) -> dict[str, str]:
        return dict(self.values)


class ScanErrorDialog(QDialog):
    """Full-screen-style red error screen shown when a scan fails."""

    def __init__(self, title: str, reasons: list[str], parent=None) -> None:
        super().__init__(parent)
        red = STATE["MISMATCH"][1]
        self.setWindowTitle("Scan Failed")
        self.setModal(True)
        self.setMinimumWidth(640)
        self.setStyleSheet(STYLESHEET + f"QDialog {{ border: 3px solid {red}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 30)
        lay.setSpacing(16)

        banner = QLabel(f"✕  {title}")
        banner.setAlignment(Qt.AlignCenter)
        banner.setStyleSheet(f"color: {red}; font-size: 28px; font-weight: 800;")
        lay.addWidget(banner)

        sub = QLabel("The scan was rejected. The line was NOT enabled.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(sub)

        box = QFrame()
        box.setStyleSheet(f"background: #3a1414; border: 1px solid {red}; border-radius: 8px;")
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(18, 14, 18, 14)
        box_lay.setSpacing(8)
        for reason in reasons or ["Verification failed."]:
            row = QLabel(f"•  {reason}")
            row.setWordWrap(True)
            row.setStyleSheet(f"color: {TEXT}; font-size: 16px; background: transparent;")
            box_lay.addWidget(row)
        lay.addWidget(box)

        hint = QLabel("Press OK, correct the problem, and scan again.")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("OK")
        ok.setObjectName("Primary")
        ok.setMinimumWidth(140)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        row.addStretch(1)
        lay.addLayout(row)


class MainWindow(QWidget):
    def __init__(self, config: Config, config_path: Path, simulate: bool = False, kiosk: bool = False) -> None:
        super().__init__()
        self.config = config
        self.config_path = Path(config_path)
        self.simulate = simulate
        self.kiosk = kiosk
        self.setWindowTitle("moreader — Manufacturing Order Verification")
        self.resize(1180, 780)
        self.setStyleSheet(STYLESHEET)
        if kiosk:
            self.setWindowFlag(Qt.FramelessWindowHint, True)

        self.events: queue.Queue = queue.Queue()
        self.worker: PLCWorker | None = None
        self.scan_source = None
        self.scan_source_error = ""
        self.tiles: list[EncapsulatorTile] = []
        self.master_panel: MasterPanel | None = None
        self.cfg_widgets: dict = {}
        self.config_index = None
        self._master_bypassed = False
        self._error_open = False
        self._allow_close = not kiosk

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_operator_page())

        self._open_scan_source()
        self._start_worker()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain_events)
        self._timer.start(100)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(1000)
        self._tick_clock()

    # -- header ---------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(74)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(22, 10, 22, 10)
        box = QVBoxLayout()
        box.setSpacing(0)
        t = QLabel("moreader")
        t.setObjectName("HeaderTitle")
        s = QLabel("Manufacturing Order Verification")
        s.setObjectName("HeaderSub")
        box.addWidget(t)
        box.addWidget(s)
        lay.addLayout(box)
        lay.addStretch(1)
        self.conn_summary = QLabel("PLCs: …")
        self.conn_summary.setObjectName("ConnSummary")
        lay.addWidget(self.conn_summary)
        lay.addSpacing(22)
        self.clock_lbl = QLabel("")
        self.clock_lbl.setObjectName("Clock")
        lay.addWidget(self.clock_lbl)
        lay.addSpacing(22)
        self.settings_btn = QPushButton("⚙  Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        lay.addWidget(self.settings_btn)
        return header

    # -- operator page --------------------------------------------------
    def _build_operator_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(14)

        self.shift_lbl = QLabel("")
        self.shift_lbl.setStyleSheet(f"color: {MUTED}; font-size: 14px;")
        lay.addWidget(self.shift_lbl)

        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(16)
        self.tiles = []
        for enc in self.config.plc.encapsulators:
            tile = EncapsulatorTile(enc.name)
            self.tiles.append(tile)
            tiles_row.addWidget(tile, 1)
        lay.addLayout(tiles_row, 1)

        self.master_panel = MasterPanel(self.config.plc.master.name)
        lay.addWidget(self.master_panel)

        button_row = QHBoxLayout()
        button_row.setSpacing(16)
        verify = QPushButton("VERIFY  MO")
        verify.setObjectName("Verify")
        verify.clicked.connect(self._verify_mo)
        button_row.addWidget(verify, 3)
        bypass = QPushButton("BYPASS\n(passworded)")
        bypass.setObjectName("Bypass")
        bypass.clicked.connect(self._toggle_bypass)
        button_row.addWidget(bypass, 1)
        lockout = QPushButton("MANUAL\nLOCKOUT")
        lockout.setObjectName("Lockout")
        lockout.clicked.connect(self._manual_lockout)
        button_row.addWidget(lockout, 1)
        lay.addLayout(button_row)

        self.log = QPlainTextEdit()
        self.log.setObjectName("Log")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(120)
        lay.addWidget(self.log)
        return page

    def _verify_mo(self) -> None:
        sec = self.config.secondary
        if sec.enabled:
            steps = [
                ("mo", f"SCAN {sec.stuffed_element_label.upper()}",
                 "Scan the Stuffed Element MO. Its digits are matched to every encapsulator."),
                ("assembled_mo", f"SCAN {sec.assembled_mo_label.upper()}",
                 "Scan the Assembled Battery MO. Its digits are matched to the battery label."),
                ("battery", f"SCAN {sec.battery_label.upper()}",
                 f"Scan the battery label. Its first {sec.battery_first_digits} digits must match the Assembled Battery MO."),
            ]
        else:
            steps = [(
                "mo", "SCAN MANUFACTURING ORDER",
                "Scan the MO barcode. Its digits are matched to every encapsulator.",
            )]
        # Serial scanner configured but the port could not be opened.
        if self.config.scanner.type == "serial" and self.scan_source is None:
            self._show_error("SCANNER NOT AVAILABLE",
                             [self.scan_source_error or "The serial scanner could not be opened.",
                              "Check the COM port and cable in Settings → Scanner."])
            return
        dialog = ScanDialog(steps, self, scan_source=self.scan_source)
        if dialog.exec() != QDialog.Accepted or not self.worker:
            return
        vals = dialog.result_values()
        if sec.enabled:
            self.worker.submit(CMD_VERIFY, {
                "mo": vals.get("mo", ""),
                "assembled_mo": vals.get("assembled_mo", ""),
                "battery": vals.get("battery", ""),
            })
        else:
            self.worker.submit(CMD_VERIFY, vals.get("mo", ""))

    def _manual_lockout(self) -> None:
        if self.worker:
            self.worker.submit(CMD_LOCKOUT)

    def _toggle_bypass(self) -> None:
        if not self.worker:
            return
        turning_on = not self._master_bypassed
        verb = "enable" if turning_on else "clear"
        password, ok = QInputDialog.getText(
            self, "Bypass — password required",
            f"Enter password to {verb} MO bypass:", QLineEdit.Password,
        )
        if not ok:
            return
        if password != self.config.security.password:
            QMessageBox.warning(self, "Access denied", "Incorrect password.")
            return
        if not turning_on:
            self.worker.submit(CMD_BYPASS, "off")
            return
        # Turning bypass ON: capture who and why for the audit log.
        name, ok = QInputDialog.getText(self, "Bypass — who", "Your name:")
        if not ok or not name.strip():
            QMessageBox.warning(self, "Bypass cancelled", "A name is required to bypass.")
            return
        reason, ok = QInputDialog.getText(self, "Bypass — why", "Reason for bypass:")
        if not ok or not reason.strip():
            QMessageBox.warning(self, "Bypass cancelled", "A reason is required to bypass.")
            return
        self.worker.submit(CMD_BYPASS, {"on": True, "name": name.strip(), "reason": reason.strip()})

    # -- settings -------------------------------------------------------
    def _open_settings(self) -> None:
        password, ok = QInputDialog.getText(
            self, "Configuration locked", "Enter configuration password:", QLineEdit.Password
        )
        if not ok:
            return
        if password != self.config.security.password:
            QMessageBox.warning(self, "Access denied", "Incorrect password.")
            return
        if self.config_index is None:
            self.config_page = self._build_config_page()
            self.config_index = self.stack.addWidget(self.config_page)
        self._load_config_into_widgets()
        self.stack.setCurrentIndex(self.config_index)

    def _build_config_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)
        bar = QHBoxLayout()
        back = QPushButton("‹ Back to Operator")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        bar.addWidget(back)
        exit_btn = QPushButton("Exit App")
        exit_btn.clicked.connect(self._exit_app)
        bar.addWidget(exit_btn)
        bar.addStretch(1)
        self.save_msg = QLabel("")
        bar.addWidget(self.save_msg)
        self.test_btn = QPushButton("Test Connections")
        self.test_btn.clicked.connect(self._test_connections)
        bar.addWidget(self.test_btn)
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self._load_config_into_widgets)
        save_btn = QPushButton("Save & Apply")
        save_btn.setObjectName("Primary")
        save_btn.clicked.connect(self._save_config)
        bar.addWidget(reload_btn)
        bar.addWidget(save_btn)
        lay.addLayout(bar)

        tabs = QTabWidget()
        tabs.addTab(self._build_plcs_tab(), "PLCs")
        tabs.addTab(self._build_scanner_tab(), "Scanner")
        tabs.addTab(self._build_formats_tab(), "MO Formats")
        tabs.addTab(self._build_secondary_tab(), "Battery Scan")
        tabs.addTab(self._build_shift_tab(), "Shift")
        tabs.addTab(self._build_security_tab(), "Security")
        lay.addWidget(tabs, 1)
        return page

    def _tag_row(self, grid, row, label, attr, store):
        grid.addWidget(QLabel(label), row, 0)
        name_edit = QLineEdit()
        desc_edit = QLineEdit()
        grid.addWidget(name_edit, row, 1)
        grid.addWidget(desc_edit, row, 2, 1, 3)
        store[attr] = (name_edit, desc_edit)

    def _build_plcs_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(14)

        self.cfg_widgets["encapsulators"] = []
        for enc in self.config.plc.encapsulators:
            box = QGroupBox(enc.name)
            grid = QGridLayout(box)
            grid.setHorizontalSpacing(12)
            grid.addWidget(QLabel("Name"), 0, 0)
            w = {"name": QLineEdit(), "ip": QLineEdit(), "slot": QSpinBox(), "tags": {}}
            grid.addWidget(w["name"], 0, 1)
            grid.addWidget(QLabel("IP address"), 0, 2)
            grid.addWidget(w["ip"], 0, 3)
            grid.addWidget(QLabel("Slot"), 0, 4)
            w["slot"].setRange(0, 17)
            grid.addWidget(w["slot"], 0, 5)
            hn = QLabel("Tag name"); hn.setObjectName("Caption"); grid.addWidget(hn, 1, 1)
            hd = QLabel("Description"); hd.setObjectName("Caption"); grid.addWidget(hd, 1, 2)
            self._tag_row(grid, 2, ENCAP_RECIPE[1], "recipe_tag", w["tags"])
            self.cfg_widgets["encapsulators"].append(w)
            lay.addWidget(box)

        mbox = QGroupBox(f"Master — {self.config.plc.master.name}")
        mgrid = QGridLayout(mbox)
        mgrid.setHorizontalSpacing(12)
        mw = {"name": QLineEdit(), "ip": QLineEdit(), "slot": QSpinBox(), "tags": {}}
        mgrid.addWidget(QLabel("Name"), 0, 0)
        mgrid.addWidget(mw["name"], 0, 1)
        mgrid.addWidget(QLabel("IP address"), 0, 2)
        mgrid.addWidget(mw["ip"], 0, 3)
        mgrid.addWidget(QLabel("Slot"), 0, 4)
        mw["slot"].setRange(0, 17)
        mgrid.addWidget(mw["slot"], 0, 5)
        hn = QLabel("Tag name"); hn.setObjectName("Caption"); mgrid.addWidget(hn, 1, 1)
        hd = QLabel("Description"); hd.setObjectName("Caption"); mgrid.addWidget(hd, 1, 2)
        for i, (attr, label, _n, _d) in enumerate(MASTER_TAGS):
            row = 2 + i
            if attr == "cycle_stop_tag":
                mgrid.addWidget(QLabel(label), row, 0)
                name_edit, desc_edit = QLineEdit(), QLineEdit()
                mgrid.addWidget(name_edit, row, 1)
                self.cfg_widgets["cycle_stop_enabled"] = QCheckBox("moreader writes this")
                self.cfg_widgets["cycle_stop_enabled"].setToolTip(
                    "Uncheck to let the PLC handle the cycle-stop request; moreader will not write this tag."
                )
                mgrid.addWidget(self.cfg_widgets["cycle_stop_enabled"], row, 2)
                mgrid.addWidget(desc_edit, row, 3, 1, 2)
                mw["tags"][attr] = (name_edit, desc_edit)
            else:
                self._tag_row(mgrid, row, label, attr, mw["tags"])
        hb_row = 2 + len(MASTER_TAGS)
        mgrid.addWidget(QLabel("Heartbeat mode"), hb_row, 0)
        self.cfg_widgets["heartbeat_mode"] = QComboBox()
        self.cfg_widgets["heartbeat_mode"].addItems(["toggle", "increment"])
        mgrid.addWidget(self.cfg_widgets["heartbeat_mode"], hb_row, 1)
        mgrid.addWidget(QLabel("toggle = pulse a BOOL ON/OFF · increment = count up a DINT"), hb_row, 2, 1, 3)
        mgrid.addWidget(QLabel("Heartbeat interval (s)"), hb_row + 1, 0)
        self.cfg_widgets["heartbeat_interval"] = QDoubleSpinBox()
        self.cfg_widgets["heartbeat_interval"].setRange(0.1, 60.0)
        self.cfg_widgets["heartbeat_interval"].setSingleStep(0.5)
        mgrid.addWidget(self.cfg_widgets["heartbeat_interval"], hb_row + 1, 1)
        self.cfg_widgets["master"] = mw
        lay.addWidget(mbox)

        lay.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _build_scanner_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Scanner type"), 0, 0)
        self.cfg_widgets["scanner_type"] = QComboBox()
        self.cfg_widgets["scanner_type"].addItems(["keyboard", "serial", "stdin"])
        grid.addWidget(self.cfg_widgets["scanner_type"], 0, 1)
        grid.addWidget(QLabel("keyboard = HID wedge · serial = virtual COM port (e.g. Zebra)"), 0, 2)
        grid.addWidget(QLabel("Serial port"), 1, 0)
        self.cfg_widgets["scanner_port"] = QLineEdit()
        grid.addWidget(self.cfg_widgets["scanner_port"], 1, 1)
        grid.addWidget(QLabel("serial only, e.g. COM4 (Windows) or /dev/ttyACM0"), 1, 2)
        grid.addWidget(QLabel("Baud rate"), 2, 0)
        self.cfg_widgets["scanner_baud"] = QSpinBox()
        self.cfg_widgets["scanner_baud"].setRange(300, 921600)
        grid.addWidget(self.cfg_widgets["scanner_baud"], 2, 1)
        grid.addWidget(QLabel("serial only, typically 9600 or 115200"), 2, 2)
        grid.addWidget(QLabel("Scan pattern (regex)"), 3, 0)
        self.cfg_widgets["scan_pattern"] = QLineEdit()
        grid.addWidget(self.cfg_widgets["scan_pattern"], 3, 1)
        grid.addWidget(QLabel("optional, e.g. MO(?P<model>\\d+)"), 3, 2)
        grid.setRowStretch(4, 1)
        return w

    def _build_formats_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        intro = QLabel(
            "These rules read the digits from the Stuffed Element and Assembled Battery scans. "
            "The first row whose “Starts with” matches the scan is used. Leave “Starts with” "
            "blank for the default rule, and keep that row last.\n\n"
            "Read mode: “last” = last N digits (ignores letters/dashes). "
            "“slice” = N characters counted from the Start position (1 = first character)."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {MUTED};")
        v.addWidget(intro)

        self.fmt_table = QTableWidget(0, 5)
        self.fmt_table.setHorizontalHeaderLabels(
            ["Starts with", "Exact length (0=any)", "Read mode", "Start (slice)", "Digits"]
        )
        self.fmt_table.verticalHeader().setDefaultSectionSize(38)
        header = self.fmt_table.horizontalHeader()
        header.setStretchLastSection(True)
        self.fmt_table.setColumnWidth(0, 150)
        self.fmt_table.setColumnWidth(1, 170)
        self.fmt_table.setColumnWidth(2, 130)
        self.fmt_table.setColumnWidth(3, 120)
        v.addWidget(self.fmt_table, 1)

        row = QHBoxLayout()
        add = QPushButton("+ Add rule")
        add.clicked.connect(lambda: self._add_format_row())
        rem = QPushButton("Remove selected rule")
        rem.clicked.connect(self._remove_format_row)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        v.addLayout(row)
        return w

    def _add_format_row(self, fmt: MoFormat | None = None) -> None:
        r = self.fmt_table.rowCount()
        self.fmt_table.insertRow(r)
        prefix = QLineEdit(fmt.prefix if fmt else "")
        length = QSpinBox(); length.setRange(0, 64); length.setValue(fmt.length if fmt else 0)
        mode = QComboBox(); mode.addItems(["last", "slice"]); mode.setCurrentText(fmt.take if fmt else "last")
        start = QSpinBox(); start.setRange(1, 64); start.setValue(fmt.start if fmt else 1)
        count = QSpinBox(); count.setRange(1, 18); count.setValue(fmt.count if fmt else 4)
        for c, widget in enumerate([prefix, length, mode, start, count]):
            self.fmt_table.setCellWidget(r, c, widget)

    def _remove_format_row(self) -> None:
        r = self.fmt_table.currentRow()
        if r < 0:
            r = self.fmt_table.rowCount() - 1
        if r >= 0:
            self.fmt_table.removeRow(r)

    def _gather_formats(self) -> list:
        formats = []
        for r in range(self.fmt_table.rowCount()):
            formats.append({
                "prefix": self.fmt_table.cellWidget(r, 0).text(),
                "length": self.fmt_table.cellWidget(r, 1).value(),
                "take": self.fmt_table.cellWidget(r, 2).currentText(),
                "start": self.fmt_table.cellWidget(r, 3).value(),
                "count": self.fmt_table.cellWidget(r, 4).value(),
            })
        return formats

    def _build_secondary_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setColumnStretch(1, 1)
        self.cfg_widgets["secondary_enabled"] = QCheckBox(
            "Require the Assembled Battery MO + battery-label cross-check (3-step scan)"
        )
        grid.addWidget(self.cfg_widgets["secondary_enabled"], 0, 0, 1, 3)

        note = QLabel("The Stuffed Element and Assembled Battery scans are read using the MO Formats "
                      "tab. The battery label's first digits are matched to the Assembled Battery MO.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED};")
        grid.addWidget(note, 1, 0, 1, 3)

        grid.addWidget(QLabel("Stuffed Element MO label"), 2, 0)
        self.cfg_widgets["stuffed_element_label"] = QLineEdit()
        grid.addWidget(self.cfg_widgets["stuffed_element_label"], 2, 1, 1, 2)
        grid.addWidget(QLabel("Assembled Battery MO label"), 3, 0)
        self.cfg_widgets["assembled_mo_label"] = QLineEdit()
        grid.addWidget(self.cfg_widgets["assembled_mo_label"], 3, 1, 1, 2)
        grid.addWidget(QLabel("Battery label name"), 4, 0)
        self.cfg_widgets["battery_label"] = QLineEdit()
        grid.addWidget(self.cfg_widgets["battery_label"], 4, 1, 1, 2)

        grid.addWidget(QLabel("Battery first N digits"), 5, 0)
        self.cfg_widgets["battery_first_digits"] = QSpinBox()
        self.cfg_widgets["battery_first_digits"].setRange(1, 18)
        grid.addWidget(self.cfg_widgets["battery_first_digits"], 5, 1)
        grid.addWidget(QLabel("first digits of the battery label, matched to the Assembled MO. Default 4."), 5, 2)
        grid.addWidget(QLabel("Battery min scan length"), 6, 0)
        self.cfg_widgets["battery_min_length"] = QSpinBox()
        self.cfg_widgets["battery_min_length"].setRange(0, 64)
        grid.addWidget(self.cfg_widgets["battery_min_length"], 6, 1)
        grid.addWidget(QLabel("min characters (0 = no check). Default 10."), 6, 2)
        grid.setRowStretch(7, 1)
        return w

    def _build_shift_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Shift start times"), 0, 0)
        self.cfg_widgets["start_times"] = QLineEdit()
        grid.addWidget(self.cfg_widgets["start_times"], 0, 1)
        grid.addWidget(QLabel("comma separated HH:MM, e.g. 06:00, 14:00, 22:00"), 0, 2)
        grid.addWidget(QLabel("Poll interval (s)"), 1, 0)
        self.cfg_widgets["poll_interval"] = QDoubleSpinBox()
        self.cfg_widgets["poll_interval"].setRange(0.2, 30.0)
        grid.addWidget(self.cfg_widgets["poll_interval"], 1, 1)
        self.cfg_widgets["lock_on_startup"] = QCheckBox("Require a scan before the first run (lock on startup)")
        grid.addWidget(self.cfg_widgets["lock_on_startup"], 2, 0, 1, 3)
        grid.setRowStretch(3, 1)
        return w

    def _build_security_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Configuration password"), 0, 0)
        self.cfg_widgets["password"] = QLineEdit()
        self.cfg_widgets["password"].setEchoMode(QLineEdit.Password)
        grid.addWidget(self.cfg_widgets["password"], 0, 1)
        show = QCheckBox("Show")
        show.toggled.connect(
            lambda on: self.cfg_widgets["password"].setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        grid.addWidget(show, 0, 2)
        note = QLabel("Stored in the YAML config file. Required to open this screen.")
        note.setStyleSheet(f"color: {MUTED};")
        grid.addWidget(note, 1, 0, 1, 3)
        grid.setRowStretch(2, 1)
        return w

    # -- config <-> widgets --------------------------------------------
    def _load_config_into_widgets(self) -> None:
        c = self.config
        for i, enc in enumerate(c.plc.encapsulators):
            w = self.cfg_widgets["encapsulators"][i]
            w["name"].setText(enc.name)
            w["ip"].setText(enc.ip_address)
            w["slot"].setValue(enc.slot)
            name_edit, desc_edit = w["tags"]["recipe_tag"]
            name_edit.setText(enc.recipe_tag.name)
            desc_edit.setText(enc.recipe_tag.description)
        m = c.plc.master
        mw = self.cfg_widgets["master"]
        mw["name"].setText(m.name)
        mw["ip"].setText(m.ip_address)
        mw["slot"].setValue(m.slot)
        for attr, *_ in MASTER_TAGS:
            spec = m.tag(attr)
            name_edit, desc_edit = mw["tags"][attr]
            name_edit.setText(spec.name)
            desc_edit.setText(spec.description)
        self.cfg_widgets["cycle_stop_enabled"].setChecked(m.cycle_stop_enabled)
        self.cfg_widgets["heartbeat_mode"].setCurrentText(c.plc.heartbeat_mode)
        self.cfg_widgets["heartbeat_interval"].setValue(c.plc.heartbeat_interval)
        self.cfg_widgets["scanner_type"].setCurrentText(c.scanner.type)
        self.cfg_widgets["scanner_port"].setText(c.scanner.port)
        self.cfg_widgets["scanner_baud"].setValue(c.scanner.baudrate)
        self.cfg_widgets["scan_pattern"].setText(c.scanner.scan_pattern or "")
        self.fmt_table.setRowCount(0)
        for fmt in c.mo_formats:
            self._add_format_row(fmt)
        self.cfg_widgets["secondary_enabled"].setChecked(c.secondary.enabled)
        self.cfg_widgets["stuffed_element_label"].setText(c.secondary.stuffed_element_label)
        self.cfg_widgets["assembled_mo_label"].setText(c.secondary.assembled_mo_label)
        self.cfg_widgets["battery_label"].setText(c.secondary.battery_label)
        self.cfg_widgets["battery_first_digits"].setValue(c.secondary.battery_first_digits)
        self.cfg_widgets["battery_min_length"].setValue(c.secondary.battery_min_length)
        self.cfg_widgets["start_times"].setText(", ".join(c.shift.start_times))
        self.cfg_widgets["poll_interval"].setValue(c.shift.poll_interval)
        self.cfg_widgets["lock_on_startup"].setChecked(c.shift.lock_on_startup)
        self.cfg_widgets["password"].setText(c.security.password)

    def _gather_config(self) -> Config:
        encapsulators = []
        for w in self.cfg_widgets["encapsulators"]:
            name_edit, desc_edit = w["tags"]["recipe_tag"]
            encapsulators.append(
                {
                    "name": w["name"].text(),
                    "ip_address": w["ip"].text(),
                    "slot": w["slot"].value(),
                    "tags": {"recipe_tag": {"name": name_edit.text(), "description": desc_edit.text()}},
                }
            )
        mw = self.cfg_widgets["master"]
        master = {
            "name": mw["name"].text(),
            "ip_address": mw["ip"].text(),
            "slot": mw["slot"].value(),
            "cycle_stop_enabled": self.cfg_widgets["cycle_stop_enabled"].isChecked(),
            "tags": {
                attr: {"name": ne.text(), "description": de.text()}
                for attr, (ne, de) in mw["tags"].items()
            },
        }
        times = [t.strip() for t in self.cfg_widgets["start_times"].text().split(",") if t.strip()]
        data = {
            "security": {"password": self.cfg_widgets["password"].text()},
            "plc": {
                "driver": self.config.plc.driver,
                "heartbeat_interval": self.cfg_widgets["heartbeat_interval"].value(),
                "heartbeat_mode": self.cfg_widgets["heartbeat_mode"].currentText(),
                "encapsulators": encapsulators,
                "master": master,
            },
            "scanner": {
                "type": self.cfg_widgets["scanner_type"].currentText(),
                "port": self.cfg_widgets["scanner_port"].text() or "COM3",
                "baudrate": self.cfg_widgets["scanner_baud"].value(),
                "scan_pattern": self.cfg_widgets["scan_pattern"].text() or None,
            },
            "mo_formats": self._gather_formats(),
            "secondary": {
                "enabled": self.cfg_widgets["secondary_enabled"].isChecked(),
                "stuffed_element_label": self.cfg_widgets["stuffed_element_label"].text() or "Stuffed Element MO",
                "assembled_mo_label": self.cfg_widgets["assembled_mo_label"].text() or "Assembled Battery MO",
                "battery_label": self.cfg_widgets["battery_label"].text() or "Battery Label",
                "battery_first_digits": self.cfg_widgets["battery_first_digits"].value(),
                "battery_min_length": self.cfg_widgets["battery_min_length"].value(),
            },
            "shift": {
                "start_times": times,
                "lock_on_startup": self.cfg_widgets["lock_on_startup"].isChecked(),
                "poll_interval": self.cfg_widgets["poll_interval"].value(),
            },
        }
        return from_dict(data)

    def _save_config(self) -> None:
        try:
            new_config = self._gather_config()
            save_config(new_config, self.config_path)
        except (ConfigError, ValueError) as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))
            return
        self.config = new_config
        self.simulate = self.simulate or new_config.plc.driver == "simulated"
        self.save_msg.setText(f"Saved to {self.config_path}")
        self.save_msg.setStyleSheet(f"color: {LOG_COLOR['ok']};")
        self._rebuild_operator()
        self._restart_worker()

    def _rebuild_operator(self) -> None:
        operator = self.stack.widget(0)
        operator.deleteLater()
        new_op = self._build_operator_page()
        self.stack.insertWidget(0, new_op)
        self.stack.removeWidget(operator)

    def _exit_app(self) -> None:
        if QMessageBox.question(self, "Exit application", "Close moreader? The line will no longer be gated.") \
                != QMessageBox.Yes:
            return
        self._allow_close = True
        self.close()

    def _test_connections(self) -> None:
        try:
            cfg = self._gather_config()
        except (ConfigError, ValueError) as exc:
            QMessageBox.critical(self, "Invalid configuration", str(exc))
            return
        driver = "simulated" if self.simulate else cfg.plc.driver
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing…")
        self._test_queue = queue.Queue()

        def work():
            results = []
            for enc_cfg in cfg.plc.encapsulators:
                link = build_encapsulator(enc_cfg, driver)
                try:
                    link.connect()
                    link.read_recipe()
                    results.append((enc_cfg.name, True, "recipe read OK"))
                except Exception as exc:  # noqa: BLE001 - report any failure
                    results.append((enc_cfg.name, False, str(exc)))
                finally:
                    try:
                        link.close()
                    except Exception:
                        pass
            m = build_master(cfg.plc.master, driver)
            try:
                m.connect()
                m.read_mo_verified()
                results.append((cfg.plc.master.name, True, "MO_Verified read OK"))
            except Exception as exc:  # noqa: BLE001
                results.append((cfg.plc.master.name, False, str(exc)))
            finally:
                try:
                    m.close()
                except Exception:
                    pass
            self._test_queue.put(results)

        import threading
        threading.Thread(target=work, daemon=True).start()
        self._poll_test()

    def _poll_test(self) -> None:
        try:
            results = self._test_queue.get_nowait()
        except queue.Empty:
            QTimer.singleShot(150, self._poll_test)
            return
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Test Connections")
        lines = []
        for name, ok, detail in results:
            lines.append(f"{'✓' if ok else '✗'}  {name}: {detail}")
        box = QMessageBox(self)
        box.setWindowTitle("Connection test")
        box.setText("\n".join(lines))
        box.setIcon(QMessageBox.Information if all(ok for _, ok, _ in results) else QMessageBox.Warning)
        box.setStyleSheet(STYLESHEET)
        box.exec()

    # -- scan source ----------------------------------------------------
    def _open_scan_source(self) -> None:
        self._close_scan_source()
        self.scan_source = None
        self.scan_source_error = ""
        if self.config.scanner.type != "serial":
            return
        try:
            self.scan_source = SerialScanSource(self.config.scanner.port, self.config.scanner.baudrate)
        except ScannerError as exc:
            self.scan_source_error = str(exc)

    def _close_scan_source(self) -> None:
        if self.scan_source is not None:
            self.scan_source.close()
            self.scan_source = None

    # -- worker plumbing ------------------------------------------------
    def _start_worker(self) -> None:
        self.events = queue.Queue()
        self.worker = PLCWorker(self.config, simulate=self.simulate, events=self.events)
        self.worker.start()

    def _restart_worker(self) -> None:
        if self.worker is not None:
            self.worker.shutdown()
        self._open_scan_source()
        self._start_worker()

    # -- event pump -----------------------------------------------------
    def _drain_events(self) -> None:
        if self._error_open:      # don't process more while an error screen is up
            return
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass

    def _show_error(self, title: str, reasons: list) -> None:
        self._error_open = True
        try:
            ScanErrorDialog(title, reasons, self).exec()
        finally:
            self._error_open = False

    def _handle_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "log":
            self._append_log(event.get("level", "info"), event.get("text", ""))
        elif etype == "error":
            self._show_error(event.get("title", "SCAN FAILED"), event.get("reasons", []))
        elif etype == "heartbeat":
            if self.master_panel:
                self.master_panel.pulse(event.get("value", 0), event.get("mode", "toggle"))
        elif etype == "status":
            encs = event.get("encapsulators", [])
            for tile, data in zip(self.tiles, encs):
                tile.update_from(data)
            master = event.get("master", {})
            self._master_bypassed = bool(master.get("mo_bypassed"))
            if self.master_panel:
                self.master_panel.update_from(master, event.get("secondary"))
            online = sum(1 for m in encs if m.get("connected")) + (1 if master.get("connected") else 0)
            total = len(encs) + 1
            color = LOG_COLOR["ok"] if online == total else LOG_COLOR["alarm"]
            self.conn_summary.setText(f"PLCs online: {online}/{total}")
            self.conn_summary.setStyleSheet(f"color: {color};")
            self.shift_lbl.setText(f"Current shift: {event.get('shift', '—')}")

    def _append_log(self, level: str, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        color = LOG_COLOR.get(level, MUTED)
        self.log.appendHtml(f'<span style="color:{MUTED}">{ts}</span> <span style="color:{color}">{text}</span>')

    def _tick_clock(self) -> None:
        self.clock_lbl.setText(datetime.now().strftime("%a %H:%M:%S"))

    # -- lifecycle ------------------------------------------------------
    def closeEvent(self, event) -> None:
        if not self._allow_close:
            event.ignore()      # kiosk mode: only the password-gated Exit can close
            return
        if self.worker is not None:
            self.worker.shutdown()
            self.worker.join(timeout=2.0)
        self._close_scan_source()
        super().closeEvent(event)


def launch(config: Config, config_path: Path, simulate: bool = False, kiosk: bool = False, **_ignored) -> None:
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(config, config_path, simulate=simulate, kiosk=kiosk)
    if kiosk:
        window.showFullScreen()
    else:
        window.show()
    app.exec()
