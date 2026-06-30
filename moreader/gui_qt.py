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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import ENCAP_RECIPE, MASTER_TAGS, Config, ConfigError, from_dict, save_config
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
QLabel#MasterName, QLabel#MasterStatus, QLabel#Heart, QLabel#HeartVal,
QLabel#HeaderTitle, QLabel#HeaderSub, QLabel#Clock, QLabel#ConnSummary {{ background: transparent; }}
QLabel#TileName {{ font-size: 19px; font-weight: 700; }}
QLabel#TileStatus {{ font-size: 16px; font-weight: 700; }}
QLabel#Caption {{ font-size: 12px; color: {MUTED}; }}
QLabel#TileModel {{ font-size: 40px; font-weight: 800; }}
QLabel#TileScan {{ font-size: 16px; font-weight: 600; color: {MUTED}; }}
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
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)

        top = QHBoxLayout()
        self.name_lbl = QLabel(name)
        self.name_lbl.setObjectName("TileName")
        self.lamp = Lamp()
        top.addWidget(self.name_lbl)
        top.addStretch(1)
        top.addWidget(self.lamp)
        lay.addLayout(top)

        self.status_lbl = QLabel("OFFLINE")
        self.status_lbl.setObjectName("TileStatus")
        lay.addWidget(self.status_lbl)

        lay.addSpacing(6)
        cap = QLabel("RECIPE (DINT)")
        cap.setObjectName("Caption")
        lay.addWidget(cap)
        self.model_lbl = QLabel("—")
        self.model_lbl.setObjectName("TileModel")
        lay.addWidget(self.model_lbl)

        lay.addStretch(1)
        scap = QLabel("LAST SCAN")
        scap.setObjectName("Caption")
        lay.addWidget(scap)
        self.scan_lbl = QLabel("—")
        self.scan_lbl.setObjectName("TileScan")
        lay.addWidget(self.scan_lbl)
        self._apply("DISCONNECTED")

    def _apply(self, state: str) -> None:
        bg, accent, text = STATE.get(state, STATE["DISCONNECTED"])
        self.setStyleSheet(f"QFrame#Tile {{ background: {bg}; border: 2px solid {accent}; border-radius: 12px; }}")
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {accent};")
        self.lamp.set_color(accent)

    def update_from(self, data: dict) -> None:
        state = data.get("state", "DISCONNECTED")
        if not data.get("connected", False):
            state = "DISCONNECTED"
        self.name_lbl.setText(data.get("name", self.name_lbl.text()))
        recipe = data.get("recipe")
        self.model_lbl.setText("—" if recipe is None else str(recipe))
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

    def update_from(self, data: dict) -> None:
        self.name_lbl.setText(f"{data.get('name', 'COS')}  ·  MASTER")
        if not data.get("connected", False):
            self._apply("DISCONNECTED")
            self.heart.setStyleSheet(f"color: {MUTED};")
            self.heart_val.setText("offline")
            return
        if data.get("mo_bypassed"):
            self._apply("BYPASSED")
        elif data.get("mo_verified"):
            self._apply("VERIFIED")
        else:
            self._apply("LOCKED")
        hb = data.get("heartbeat", 0)
        if hb != self._last_hb:
            self._pulse = not self._pulse
            self._last_hb = hb
        self.heart.setStyleSheet(f"color: {'#ef4444' if self._pulse else '#7a2a2a'};")
        self.heart_val.setText(str(hb))


class ScanDialog(QDialog):
    """Modal dialog the USB scanner sends the MO barcode into.

    There is no editable text field: the dialog captures the scanner's
    keystrokes directly and submits on the terminating Enter, so the operator
    cannot type an order in by hand.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scan Manufacturing Order")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(STYLESHEET)
        self._buffer = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 30, 34, 30)
        lay.setSpacing(16)

        title = QLabel("SCAN MANUFACTURING ORDER")
        title.setObjectName("DialogTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        hint = QLabel("Scan the MO barcode now. The last 4 digits are matched to every encapsulator.")
        hint.setStyleSheet(f"color: {MUTED};")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

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
        ok = QPushButton("OK")
        ok.setObjectName("Primary")
        ok.setFocusPolicy(Qt.NoFocus)
        ok.clicked.connect(self._submit)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

        # The dialog itself captures the scanner's keystrokes.
        self.setFocusPolicy(Qt.StrongFocus)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.setFocus()

    def _submit(self) -> None:
        if self._buffer.strip():
            self.accept()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._buffer.strip():
                self.accept()
            return
        if key == Qt.Key_Escape:
            self.reject()
            return
        if key == Qt.Key_Backspace:
            self._buffer = self._buffer[:-1]
        else:
            text = event.text()
            if text and text.isprintable():
                self._buffer += text
        self.display.setText(self._buffer or "waiting for scan…")

    def value(self) -> str:
        return self._buffer.strip()


class MainWindow(QWidget):
    def __init__(self, config: Config, config_path: Path, simulate: bool = False) -> None:
        super().__init__()
        self.config = config
        self.config_path = Path(config_path)
        self.simulate = simulate
        self.setWindowTitle("moreader — Manufacturing Order Verification")
        self.resize(1180, 780)
        self.setStyleSheet(STYLESHEET)

        self.events: queue.Queue = queue.Queue()
        self.worker: PLCWorker | None = None
        self.tiles: list[EncapsulatorTile] = []
        self.master_panel: MasterPanel | None = None
        self.cfg_widgets: dict = {}
        self.config_index = None
        self._master_bypassed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_operator_page())

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
        dialog = ScanDialog(self)
        if dialog.exec() == QDialog.Accepted and dialog.value() and self.worker:
            self.worker.submit(CMD_VERIFY, dialog.value())

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
        self.worker.submit(CMD_BYPASS, "on" if turning_on else "off")

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
        bar.addStretch(1)
        self.save_msg = QLabel("")
        bar.addWidget(self.save_msg)
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
        tabs.addTab(self._build_compare_tab(), "Compare")
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
            self._tag_row(mgrid, 2 + i, label, attr, mw["tags"])
        mgrid.addWidget(QLabel("Heartbeat interval (s)"), 2 + len(MASTER_TAGS), 0)
        self.cfg_widgets["heartbeat_interval"] = QDoubleSpinBox()
        self.cfg_widgets["heartbeat_interval"].setRange(0.1, 60.0)
        self.cfg_widgets["heartbeat_interval"].setSingleStep(0.5)
        mgrid.addWidget(self.cfg_widgets["heartbeat_interval"], 2 + len(MASTER_TAGS), 1)
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
        self.cfg_widgets["scanner_type"].addItems(["keyboard", "stdin"])
        grid.addWidget(self.cfg_widgets["scanner_type"], 0, 1)
        grid.addWidget(QLabel("HID keyboard-wedge scanner types into the scan popup."), 0, 2)
        grid.addWidget(QLabel("Scan pattern (regex)"), 1, 0)
        self.cfg_widgets["scan_pattern"] = QLineEdit()
        grid.addWidget(self.cfg_widgets["scan_pattern"], 1, 1)
        grid.addWidget(QLabel("optional, e.g. MO(?P<model>\\d+)"), 1, 2)
        grid.setRowStretch(2, 1)
        return w

    def _build_compare_tab(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Compare last N digits"), 0, 0)
        self.cfg_widgets["mo_last_digits"] = QSpinBox()
        self.cfg_widgets["mo_last_digits"].setRange(0, 18)
        grid.addWidget(self.cfg_widgets["mo_last_digits"], 0, 1)
        grid.addWidget(QLabel("0 = use the whole number. Default 4."), 0, 2)
        self.cfg_widgets["digits_only"] = QCheckBox("Strip non-digit characters before matching")
        grid.addWidget(self.cfg_widgets["digits_only"], 1, 0, 1, 3)
        grid.setRowStretch(2, 1)
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
        self.cfg_widgets["heartbeat_interval"].setValue(c.plc.heartbeat_interval)
        self.cfg_widgets["scanner_type"].setCurrentText(c.scanner.type)
        self.cfg_widgets["scan_pattern"].setText(c.scanner.scan_pattern or "")
        self.cfg_widgets["mo_last_digits"].setValue(c.compare.mo_last_digits)
        self.cfg_widgets["digits_only"].setChecked(c.compare.digits_only)
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
                "encapsulators": encapsulators,
                "master": master,
            },
            "scanner": {
                "type": self.cfg_widgets["scanner_type"].currentText(),
                "scan_pattern": self.cfg_widgets["scan_pattern"].text() or None,
            },
            "compare": {
                "mo_last_digits": self.cfg_widgets["mo_last_digits"].value(),
                "digits_only": self.cfg_widgets["digits_only"].isChecked(),
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

    # -- worker plumbing ------------------------------------------------
    def _start_worker(self) -> None:
        self.events = queue.Queue()
        self.worker = PLCWorker(self.config, simulate=self.simulate, events=self.events)
        self.worker.start()

    def _restart_worker(self) -> None:
        if self.worker is not None:
            self.worker.shutdown()
        self._start_worker()

    # -- event pump -----------------------------------------------------
    def _drain_events(self) -> None:
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass

    def _handle_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "log":
            self._append_log(event.get("level", "info"), event.get("text", ""))
        elif etype == "status":
            encs = event.get("encapsulators", [])
            for tile, data in zip(self.tiles, encs):
                tile.update_from(data)
            master = event.get("master", {})
            self._master_bypassed = bool(master.get("mo_bypassed"))
            if self.master_panel:
                self.master_panel.update_from(master)
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
        if self.worker is not None:
            self.worker.shutdown()
            self.worker.join(timeout=2.0)
        super().closeEvent(event)


def launch(config: Config, config_path: Path, simulate: bool = False, **_ignored) -> None:
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(config, config_path, simulate=simulate)
    window.show()
    app.exec()
