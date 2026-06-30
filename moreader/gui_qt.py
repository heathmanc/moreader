"""Industrial-HMI GUI for moreader, built with PySide6.

Operator screen: three machine tiles (one per PLC) with status lamps and the
model each PLC is set to run, plus a large VERIFY MO button.  The operator never
types on this screen — pressing VERIFY MO opens a modal dialog into which the
USB scanner sends the manufacturing-order barcode.

Configuration screen: password protected, with tabs for Machines (tag names +
descriptions), Scanner, Compare, Shift, and Security.

All PLC I/O runs in :class:`~moreader.worker.PLCWorker`; the GUI polls its event
queue with a QTimer, so it never blocks on the network.
"""

from __future__ import annotations

import queue
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
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

from .config import TAG_FIELDS, Config, ConfigError, from_dict, save_config
from .worker import CMD_LOCKOUT, CMD_VERIFY, PLCWorker

# --- industrial palette ------------------------------------------------------
BG = "#0e1620"
PANEL = "#16212e"
PANEL_HI = "#1d2a3a"
EDGE = "#2a3a4d"
TEXT = "#e6edf3"
MUTED = "#8aa0b3"
ACCENT = "#2d8cf0"

STATE = {
    "RUNNING": ("#15321f", "#27c46b", "RUN ENABLED"),
    "ALARM": ("#3a1414", "#ef4444", "ALARM — MISMATCH"),
    "LOCKED": ("#332708", "#eab308", "LOCKED — SCAN MO"),
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
QFrame#Tile {{ background: {PANEL}; border: 2px solid {EDGE}; border-radius: 12px; }}
QLabel#TileName {{ font-size: 20px; font-weight: 700; }}
QLabel#TileStatus {{ font-size: 17px; font-weight: 700; }}
QLabel#TileModelCaption, QLabel#TileScanCaption {{ font-size: 12px; color: {MUTED}; }}
QLabel#TileModel {{ font-size: 40px; font-weight: 800; }}
QLabel#TileScan {{ font-size: 18px; font-weight: 600; color: {MUTED}; }}
QPushButton#Verify {{ background: {ACCENT}; color: white; font-size: 26px; font-weight: 800;
    border: none; border-radius: 12px; padding: 22px; }}
QPushButton#Verify:hover {{ background: #1f6fd0; }}
QPushButton#Secondary {{ background: {PANEL_HI}; color: {TEXT}; font-size: 16px; font-weight: 700;
    border: 1px solid {EDGE}; border-radius: 10px; padding: 18px; }}
QPushButton#Secondary:hover {{ background: {EDGE}; }}
QPushButton {{ background: {PANEL_HI}; color: {TEXT}; border: 1px solid {EDGE};
    border-radius: 8px; padding: 9px 16px; font-weight: 600; }}
QPushButton:hover {{ background: {EDGE}; }}
QPushButton#Primary {{ background: {ACCENT}; color: white; border: none; }}
QPushButton#Primary:hover {{ background: #1f6fd0; }}
QPlainTextEdit#Log {{ background: #0a1018; border: 1px solid {EDGE}; border-radius: 8px;
    font-family: 'Consolas', 'DejaVu Sans Mono', monospace; font-size: 12px; }}
QLineEdit, QComboBox, QSpinBox {{ background: {PANEL_HI}; border: 1px solid {EDGE};
    border-radius: 6px; padding: 7px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {ACCENT}; }}
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
    """A round status indicator."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(26, 26)
        self.set_color(STATE["DISCONNECTED"][1])

    def set_color(self, color: str) -> None:
        self.setStyleSheet(f"background: {color}; border-radius: 13px; border: 2px solid rgba(255,255,255,0.25);")


class MachineTile(QFrame):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.setObjectName("Tile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(8)

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
        cap = QLabel("PLC IS SET TO RUN")
        cap.setObjectName("TileModelCaption")
        lay.addWidget(cap)
        self.model_lbl = QLabel("—")
        self.model_lbl.setObjectName("TileModel")
        lay.addWidget(self.model_lbl)

        lay.addStretch(1)
        scap = QLabel("LAST SCAN")
        scap.setObjectName("TileScanCaption")
        lay.addWidget(scap)
        self.scan_lbl = QLabel("—")
        self.scan_lbl.setObjectName("TileScan")
        lay.addWidget(self.scan_lbl)

        self._apply("DISCONNECTED")

    def _apply(self, state: str) -> None:
        bg, accent, text = STATE.get(state, STATE["DISCONNECTED"])
        self.setStyleSheet(
            f"QFrame#Tile {{ background: {bg}; border: 2px solid {accent}; border-radius: 12px; }}"
        )
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {accent};")
        self.lamp.set_color(accent)

    def update_from(self, data: dict) -> None:
        state = data.get("state", "DISCONNECTED")
        if not data.get("connected", False):
            state = "DISCONNECTED"
        self.name_lbl.setText(data.get("name", self.name_lbl.text()))
        model = data.get("model")
        self.model_lbl.setText("—" if model is None else str(model))
        scanned = data.get("scanned")
        self.scan_lbl.setText("—" if scanned is None else str(scanned))
        self._apply(state)


class ScanDialog(QDialog):
    """Modal dialog the USB scanner sends the MO barcode into."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scan Manufacturing Order")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(STYLESHEET)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 30, 34, 30)
        lay.setSpacing(16)

        title = QLabel("SCAN MANUFACTURING ORDER")
        title.setObjectName("DialogTitle")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        hint = QLabel("Scan the MO barcode now. The last 4 digits are matched to each PLC.")
        hint.setStyleSheet(f"color: {MUTED};")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

        self.field = QLineEdit()
        self.field.setObjectName("ScanField")
        self.field.setAlignment(Qt.AlignCenter)
        self.field.setPlaceholderText("waiting for scan…")
        self.field.returnPressed.connect(self._accept_if_filled)
        lay.addWidget(self.field)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Verify")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._accept_if_filled)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

        self.field.setFocus()

    def _accept_if_filled(self) -> None:
        if self.field.text().strip():
            self.accept()

    def value(self) -> str:
        return self.field.text().strip()


class MainWindow(QWidget):
    def __init__(self, config: Config, config_path: Path, simulate: bool = False) -> None:
        super().__init__()
        self.config = config
        self.config_path = Path(config_path)
        self.simulate = simulate
        self.setWindowTitle("moreader — Manufacturing Order Verification")
        self.resize(1180, 760)
        self.setStyleSheet(STYLESHEET)

        self.events: queue.Queue = queue.Queue()
        self.worker: PLCWorker | None = None
        self.tiles: list[MachineTile] = []
        self.cfg_widgets: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_operator_page())   # index 0
        self.config_index = None

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
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        t = QLabel("moreader")
        t.setObjectName("HeaderTitle")
        s = QLabel("Manufacturing Order Verification")
        s.setObjectName("HeaderSub")
        title_box.addWidget(t)
        title_box.addWidget(s)
        lay.addLayout(title_box)
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
        lay.setContentsMargins(22, 22, 22, 18)
        lay.setSpacing(18)

        self.shift_lbl = QLabel("")
        self.shift_lbl.setStyleSheet(f"color: {MUTED}; font-size: 14px;")
        lay.addWidget(self.shift_lbl)

        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(16)
        self.tiles = []
        for machine in self.config.plc.machines:
            tile = MachineTile(machine.name)
            self.tiles.append(tile)
            tiles_row.addWidget(tile, 1)
        lay.addLayout(tiles_row, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(16)
        verify = QPushButton("VERIFY  MO")
        verify.setObjectName("Verify")
        verify.clicked.connect(self._verify_mo)
        button_row.addWidget(verify, 3)
        new_shift = QPushButton("NEW SHIFT\n(lock all)")
        new_shift.setObjectName("Secondary")
        new_shift.clicked.connect(self._new_shift)
        button_row.addWidget(new_shift, 1)
        lay.addLayout(button_row)

        self.log = QPlainTextEdit()
        self.log.setObjectName("Log")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(130)
        lay.addWidget(self.log)
        return page

    # -- operator actions ----------------------------------------------
    def _verify_mo(self) -> None:
        dialog = ScanDialog(self)
        if dialog.exec() == QDialog.Accepted and dialog.value() and self.worker:
            self.worker.submit(CMD_VERIFY, dialog.value())

    def _new_shift(self) -> None:
        if self.worker:
            self.worker.submit(CMD_LOCKOUT)

    # -- settings / configuration --------------------------------------
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
        tabs.addTab(self._build_machines_tab(), "Machines")
        tabs.addTab(self._build_scanner_tab(), "Scanner")
        tabs.addTab(self._build_compare_tab(), "Compare")
        tabs.addTab(self._build_shift_tab(), "Shift")
        tabs.addTab(self._build_security_tab(), "Security")
        lay.addWidget(tabs, 1)
        return page

    def _build_machines_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(14)
        self.cfg_widgets["machines"] = []
        for i, machine in enumerate(self.config.plc.machines):
            box = QGroupBox(machine.name)
            grid = QGridLayout(box)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(8)
            widgets: dict = {}
            grid.addWidget(QLabel("Machine name"), 0, 0)
            widgets["name"] = QLineEdit()
            grid.addWidget(widgets["name"], 0, 1)
            grid.addWidget(QLabel("IP address"), 0, 2)
            widgets["ip"] = QLineEdit()
            grid.addWidget(widgets["ip"], 0, 3)
            grid.addWidget(QLabel("Slot"), 0, 4)
            widgets["slot"] = QSpinBox()
            widgets["slot"].setRange(0, 17)
            grid.addWidget(widgets["slot"], 0, 5)

            header = QLabel("Tag name")
            header.setStyleSheet(f"color: {MUTED};")
            grid.addWidget(header, 1, 1)
            dheader = QLabel("Description")
            dheader.setStyleSheet(f"color: {MUTED};")
            grid.addWidget(dheader, 1, 2, 1, 4)
            widgets["tags"] = {}
            for r, (attr, label, _dn, _dd) in enumerate(TAG_FIELDS):
                row = 2 + r
                grid.addWidget(QLabel(label), row, 0)
                name_edit = QLineEdit()
                desc_edit = QLineEdit()
                grid.addWidget(name_edit, row, 1)
                grid.addWidget(desc_edit, row, 2, 1, 4)
                widgets["tags"][attr] = (name_edit, desc_edit)
            lay.addWidget(box)
            self.cfg_widgets["machines"].append(widgets)
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
        self.cfg_widgets["poll_interval"] = QLineEdit()
        grid.addWidget(self.cfg_widgets["poll_interval"], 1, 1)
        self.cfg_widgets["watch_plc_request"] = QCheckBox("Lock out on a PLC shift-change request bit")
        grid.addWidget(self.cfg_widgets["watch_plc_request"], 2, 0, 1, 3)
        self.cfg_widgets["lock_on_startup"] = QCheckBox("Require a scan before the first run (lock on startup)")
        grid.addWidget(self.cfg_widgets["lock_on_startup"], 3, 0, 1, 3)
        grid.setRowStretch(4, 1)
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
        for i, machine in enumerate(c.plc.machines):
            w = self.cfg_widgets["machines"][i]
            w["name"].setText(machine.name)
            w["ip"].setText(machine.ip_address)
            w["slot"].setValue(machine.slot)
            for attr, *_ in TAG_FIELDS:
                spec = machine.tag(attr)
                name_edit, desc_edit = w["tags"][attr]
                name_edit.setText(spec.name)
                desc_edit.setText(spec.description)
        self.cfg_widgets["scanner_type"].setCurrentText(c.scanner.type)
        self.cfg_widgets["scan_pattern"].setText(c.scanner.scan_pattern or "")
        self.cfg_widgets["mo_last_digits"].setValue(c.compare.mo_last_digits)
        self.cfg_widgets["digits_only"].setChecked(c.compare.digits_only)
        self.cfg_widgets["start_times"].setText(", ".join(c.shift.start_times))
        self.cfg_widgets["poll_interval"].setText(str(c.shift.poll_interval))
        self.cfg_widgets["watch_plc_request"].setChecked(c.shift.watch_plc_request)
        self.cfg_widgets["lock_on_startup"].setChecked(c.shift.lock_on_startup)
        self.cfg_widgets["password"].setText(c.security.password)

    def _gather_config(self) -> Config:
        machines = []
        for w in self.cfg_widgets["machines"]:
            tags = {
                attr: {"name": name_edit.text(), "description": desc_edit.text()}
                for attr, (name_edit, desc_edit) in w["tags"].items()
            }
            machines.append(
                {
                    "name": w["name"].text(),
                    "ip_address": w["ip"].text(),
                    "slot": w["slot"].value(),
                    "tags": tags,
                }
            )
        times = [t.strip() for t in self.cfg_widgets["start_times"].text().split(",") if t.strip()]
        data = {
            "security": {"password": self.cfg_widgets["password"].text()},
            "plc": {"driver": self.config.plc.driver, "machines": machines},
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
                "watch_plc_request": self.cfg_widgets["watch_plc_request"].isChecked(),
                "lock_on_startup": self.cfg_widgets["lock_on_startup"].isChecked(),
                "poll_interval": float(self.cfg_widgets["poll_interval"].text() or 2.0),
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
        self._rebuild_tiles()
        self._restart_worker()

    def _rebuild_tiles(self) -> None:
        # Machine count/names may have changed; rebuild the operator tiles.
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
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass

    def _handle_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "log":
            self._append_log(event.get("level", "info"), event.get("text", ""))
        elif etype == "status":
            machines = event.get("machines", [])
            for tile, data in zip(self.tiles, machines):
                tile.update_from(data)
            online = sum(1 for m in machines if m.get("connected"))
            total = len(machines)
            color = LOG_COLOR["ok"] if online == total and total else LOG_COLOR["alarm"]
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
