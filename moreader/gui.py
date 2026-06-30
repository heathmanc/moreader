"""Polished Tkinter/ttk GUI for moreader.

Two top-level tabs:

* **Operator** -- always available.  Big colour-coded status banner, the scan
  field (a USB keyboard-wedge scanner types straight into it), the model the PLC
  is set to run, and a live event log.
* **Configuration** -- password protected.  Sub-tabs for PLC (tag names +
  descriptions), Scanner, Shift, Compare, and Security.  Saving writes the YAML
  and reconnects the PLC.

All PLC I/O runs in :class:`~moreader.worker.PLCWorker`; the GUI only sends
commands and renders the events the worker returns, so it never freezes.
"""

from __future__ import annotations

import queue
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from .config import TAG_FIELDS, Config, ConfigError, from_dict, save_config, to_dict
from .scanner import extract_model
from .worker import CMD_LOCKOUT, CMD_RECONNECT, CMD_VERIFY, PLCWorker

# --- palette -----------------------------------------------------------------
COL_BG = "#f3f5f7"
COL_PANEL = "#ffffff"
COL_HEADER = "#1f2d3d"
COL_HEADER_FG = "#ffffff"
COL_TEXT = "#1f2d3d"
COL_MUTED = "#6b7785"
COL_ACCENT = "#2563eb"

STATE_STYLES = {
    "LOCKED": ("#b7791f", "#fffbeb", "LOCKED — SCAN MANUFACTURING ORDER"),
    "RUNNING": ("#15803d", "#ecfdf5", "RUN ENABLED"),
    "ALARM": ("#b91c1c", "#fef2f2", "ALARM — SCAN DOES NOT MATCH PLC"),
    "DISCONNECTED": ("#475569", "#f1f5f9", "PLC NOT CONNECTED"),
}

LOG_COLORS = {
    "ok": "#15803d",
    "alarm": "#b91c1c",
    "warn": "#b7791f",
    "info": COL_MUTED,
}


class MoreaderGUI:
    def __init__(
        self,
        config: Config,
        config_path: Path,
        simulate: bool = False,
        expected_model: str = "ABC-100",
    ) -> None:
        self.config = config
        self.config_path = Path(config_path)
        self.simulate = simulate
        self.expected_model = expected_model
        self.unlocked = False

        self.root = tk.Tk()
        self.root.title("moreader — Manufacturing Order Verification")
        self.root.geometry("960x680")
        self.root.minsize(820, 600)
        self.root.configure(bg=COL_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._init_styles()
        self._build_header()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._build_operator_tab()
        self._build_config_tab()

        # Worker + event pump.
        self.events: queue.Queue = queue.Queue()
        self.commands: queue.Queue = queue.Queue()
        self.worker: PLCWorker | None = None
        self._start_worker()
        self.root.after(100, self._pump_events)

    # -- styling --------------------------------------------------------
    def _init_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover
            pass
        style.configure(".", font=("Segoe UI", 10), background=COL_BG, foreground=COL_TEXT)
        style.configure("TFrame", background=COL_BG)
        style.configure("Panel.TFrame", background=COL_PANEL)
        style.configure("TLabel", background=COL_BG, foreground=COL_TEXT)
        style.configure("Panel.TLabel", background=COL_PANEL, foreground=COL_TEXT)
        style.configure("Muted.TLabel", background=COL_PANEL, foreground=COL_MUTED)
        style.configure("Heading.TLabel", font=("Segoe UI Semibold", 13), background=COL_PANEL)
        style.configure("Field.TLabel", font=("Segoe UI", 10), background=COL_PANEL)
        style.configure("Value.TLabel", font=("Segoe UI Semibold", 12), background=COL_PANEL)
        style.configure("TNotebook", background=COL_BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 8), font=("Segoe UI Semibold", 10))
        style.configure("TButton", padding=(14, 8), font=("Segoe UI Semibold", 10))
        style.configure("Accent.TButton", foreground="#ffffff", background=COL_ACCENT)
        style.map("Accent.TButton", background=[("active", "#1d4ed8")])
        style.configure("TEntry", padding=4)
        style.configure("TCheckbutton", background=COL_PANEL)
        style.configure("TCombobox", padding=4)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=COL_HEADER, height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="  moreader", bg=COL_HEADER, fg=COL_HEADER_FG,
            font=("Segoe UI Semibold", 16),
        ).pack(side="left", padx=8)
        tk.Label(
            header, text="Manufacturing Order Verification", bg=COL_HEADER,
            fg="#9fb3c8", font=("Segoe UI", 10),
        ).pack(side="left", padx=4, pady=(8, 0))

        self.conn_var = tk.StringVar(value="● PLC: connecting…")
        self.conn_label = tk.Label(
            header, textvariable=self.conn_var, bg=COL_HEADER, fg="#fbbf24",
            font=("Segoe UI Semibold", 10),
        )
        self.conn_label.pack(side="right", padx=14)

    # -- operator tab ---------------------------------------------------
    def _build_operator_tab(self) -> None:
        tab = ttk.Frame(self.notebook, style="TFrame", padding=14)
        self.notebook.add(tab, text="  Operator  ")

        # Status banner.
        self.banner = tk.Label(
            tab, text="", font=("Segoe UI Semibold", 26), height=2,
            bg="#f1f5f9", fg="#475569",
        )
        self.banner.pack(fill="x", pady=(0, 14))

        # Info panel.
        info = ttk.Frame(tab, style="Panel.TFrame", padding=16)
        info.pack(fill="x")
        info.columnconfigure(1, weight=1)
        self.expected_var = tk.StringVar(value="—")
        self.lastscan_var = tk.StringVar(value="—")
        self.shift_var = tk.StringVar(value="—")
        rows = [
            ("PLC is set to run:", self.expected_var),
            ("Last scan:", self.lastscan_var),
            ("Current shift:", self.shift_var),
        ]
        for r, (label, var) in enumerate(rows):
            ttk.Label(info, text=label, style="Field.TLabel").grid(row=r, column=0, sticky="w", pady=4, padx=(0, 12))
            ttk.Label(info, textvariable=var, style="Value.TLabel").grid(row=r, column=1, sticky="w", pady=4)

        # Scan entry.
        scan_panel = ttk.Frame(tab, style="Panel.TFrame", padding=16)
        scan_panel.pack(fill="x", pady=14)
        ttk.Label(scan_panel, text="Scan manufacturing order", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            scan_panel,
            text="Scan the barcode (or type the order and press Enter).",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        entry_row = ttk.Frame(scan_panel, style="Panel.TFrame")
        entry_row.pack(fill="x")
        self.scan_var = tk.StringVar()
        self.scan_entry = tk.Entry(
            entry_row, textvariable=self.scan_var, font=("Consolas", 18),
            relief="solid", bd=1,
        )
        self.scan_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.scan_entry.bind("<Return>", self._on_scan)
        ttk.Button(entry_row, text="Verify", style="Accent.TButton", command=self._on_scan).pack(side="left", padx=(10, 0))

        # Event log.
        log_panel = ttk.Frame(tab, style="Panel.TFrame", padding=10)
        log_panel.pack(fill="both", expand=True, pady=(0, 0))
        ttk.Label(log_panel, text="Event log", style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        log_wrap = ttk.Frame(log_panel, style="Panel.TFrame")
        log_wrap.pack(fill="both", expand=True)
        self.log = tk.Text(
            log_wrap, height=8, font=("Consolas", 9), wrap="word",
            relief="flat", bg="#0f172a", fg="#e2e8f0", state="disabled",
        )
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_wrap, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)
        for level, color in LOG_COLORS.items():
            self.log.tag_configure(level, foreground=color)

        self._set_state("DISCONNECTED")
        self.scan_entry.focus_set()

    # -- configuration tab ---------------------------------------------
    def _build_config_tab(self) -> None:
        self.config_tab = ttk.Frame(self.notebook, style="TFrame", padding=14)
        self.notebook.add(self.config_tab, text="  Configuration  ")
        self._build_lock_view()

    def _build_lock_view(self) -> None:
        self.lock_frame = ttk.Frame(self.config_tab, style="Panel.TFrame", padding=40)
        self.lock_frame.place(relx=0.5, rely=0.4, anchor="center")
        ttk.Label(self.lock_frame, text="🔒  Configuration is locked", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, pady=(0, 14)
        )
        ttk.Label(self.lock_frame, text="Password", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10))
        self.pw_var = tk.StringVar()
        pw_entry = ttk.Entry(self.lock_frame, textvariable=self.pw_var, show="•", width=26)
        pw_entry.grid(row=1, column=1, sticky="ew", pady=4)
        pw_entry.bind("<Return>", lambda e: self._unlock())
        self.lock_msg = ttk.Label(self.lock_frame, text="", style="Muted.TLabel", foreground=COL_MUTED)
        self.lock_msg.grid(row=2, column=0, columnspan=2, pady=(6, 6))
        ttk.Button(self.lock_frame, text="Unlock", style="Accent.TButton", command=self._unlock).grid(
            row=3, column=0, columnspan=2, pady=(6, 0)
        )
        pw_entry.focus_set()

    def _unlock(self) -> None:
        if self.pw_var.get() == self.config.security.password:
            self.unlocked = True
            self.pw_var.set("")
            self.lock_frame.destroy()
            self._build_settings_view()
        else:
            self.lock_msg.configure(text="Incorrect password.", foreground=LOG_COLORS["alarm"])

    def _build_settings_view(self) -> None:
        container = ttk.Frame(self.config_tab, style="TFrame")
        container.pack(fill="both", expand=True)

        self.vars: dict[str, tk.Variable] = {}
        sub = ttk.Notebook(container)
        sub.pack(fill="both", expand=True)
        self._build_plc_subtab(sub)
        self._build_scanner_subtab(sub)
        self._build_shift_subtab(sub)
        self._build_compare_subtab(sub)
        self._build_security_subtab(sub)

        # Action bar.
        bar = ttk.Frame(container, style="TFrame", padding=(0, 10))
        bar.pack(fill="x")
        self.save_msg = ttk.Label(bar, text="", style="TLabel")
        self.save_msg.pack(side="left")
        ttk.Button(bar, text="Lock", command=self._lock).pack(side="right", padx=4)
        ttk.Button(bar, text="Reload", command=self._reload).pack(side="right", padx=4)
        ttk.Button(bar, text="Save & Apply", style="Accent.TButton", command=self._save).pack(side="right", padx=4)

        self._load_vars_from_config()

    def _panel(self, parent: ttk.Notebook, title: str) -> ttk.Frame:
        outer = ttk.Frame(parent, style="TFrame", padding=12)
        parent.add(outer, text=f"  {title}  ")
        panel = ttk.Frame(outer, style="Panel.TFrame", padding=16)
        panel.pack(fill="both", expand=True)
        return panel

    def _field(self, panel, row, label, var, width=36, show=None, values=None, hint=None):
        ttk.Label(panel, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", pady=6, padx=(0, 12))
        if values is not None:
            widget = ttk.Combobox(panel, textvariable=var, values=values, state="readonly", width=width - 4)
        else:
            widget = ttk.Entry(panel, textvariable=var, width=width, show=show or "")
        widget.grid(row=row, column=1, sticky="ew", pady=6)
        if hint:
            ttk.Label(panel, text=hint, style="Muted.TLabel").grid(row=row, column=2, sticky="w", padx=10)
        panel.columnconfigure(1, weight=1)
        return widget

    def _build_plc_subtab(self, parent) -> None:
        panel = self._panel(parent, "PLC")
        ttk.Label(panel, text="Connection", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.vars["plc.driver"] = tk.StringVar()
        self.vars["plc.ip_address"] = tk.StringVar()
        self.vars["plc.slot"] = tk.StringVar()
        self._field(panel, 1, "Driver", self.vars["plc.driver"], values=["logix", "simulated"],
                    hint="logix = pylogix (CompactLogix/ControlLogix)")
        self._field(panel, 2, "IP address", self.vars["plc.ip_address"])
        self._field(panel, 3, "CPU slot", self.vars["plc.slot"], width=8, hint="0 for CompactLogix")

        ttk.Label(panel, text="Tags", style="Heading.TLabel").grid(row=4, column=0, columnspan=3, sticky="w", pady=(16, 4))
        ttk.Label(panel, text="Tag name", style="Muted.TLabel").grid(row=5, column=1, sticky="w")
        ttk.Label(panel, text="Description", style="Muted.TLabel").grid(row=5, column=2, sticky="w", padx=10)
        for i, (attr, label, _desc) in enumerate(TAG_FIELDS):
            row = 6 + i
            name_var = tk.StringVar()
            desc_var = tk.StringVar()
            self.vars[f"plc.tag.{attr}.name"] = name_var
            self.vars[f"plc.tag.{attr}.description"] = desc_var
            ttk.Label(panel, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
            ttk.Entry(panel, textvariable=name_var, width=34).grid(row=row, column=1, sticky="ew", pady=5)
            ttk.Entry(panel, textvariable=desc_var, width=44).grid(row=row, column=2, sticky="ew", pady=5, padx=10)
        panel.columnconfigure(2, weight=1)

    def _build_scanner_subtab(self, parent) -> None:
        panel = self._panel(parent, "Scanner")
        for key in ("scanner.type", "scanner.port", "scanner.baudrate", "scanner.scan_pattern"):
            self.vars[key] = tk.StringVar()
        ttk.Label(panel, text="Scanner", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._field(panel, 1, "Type", self.vars["scanner.type"], values=["keyboard", "serial", "stdin"],
                    hint="keyboard = HID wedge (types into the scan box)")
        self._field(panel, 2, "Serial port", self.vars["scanner.port"], hint="serial only, e.g. COM3 or /dev/ttyACM0")
        self._field(panel, 3, "Baud rate", self.vars["scanner.baudrate"], width=12, hint="serial only")
        self._field(panel, 4, "Scan pattern (regex)", self.vars["scanner.scan_pattern"],
                    hint="optional, e.g. MODEL=(?P<model>[^|]+)")

    def _build_shift_subtab(self, parent) -> None:
        panel = self._panel(parent, "Shift")
        self.vars["shift.start_times"] = tk.StringVar()
        self.vars["shift.poll_interval"] = tk.StringVar()
        self.vars["shift.watch_plc_request"] = tk.BooleanVar()
        self.vars["shift.lock_on_startup"] = tk.BooleanVar()
        ttk.Label(panel, text="Shift change", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._field(panel, 1, "Shift start times", self.vars["shift.start_times"],
                    hint="comma separated HH:MM, e.g. 06:00, 14:00, 22:00")
        self._field(panel, 2, "Poll interval (s)", self.vars["shift.poll_interval"], width=10)
        ttk.Checkbutton(panel, text="Watch PLC shift-change request bit",
                        variable=self.vars["shift.watch_plc_request"]).grid(row=3, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(panel, text="Require a scan before the first run (lock on startup)",
                        variable=self.vars["shift.lock_on_startup"]).grid(row=4, column=0, columnspan=3, sticky="w", pady=6)

    def _build_compare_subtab(self, parent) -> None:
        panel = self._panel(parent, "Compare")
        self.vars["compare.strip"] = tk.BooleanVar()
        self.vars["compare.ignore_case"] = tk.BooleanVar()
        self.vars["compare.collapse_internal_space"] = tk.BooleanVar()
        ttk.Label(panel, text="Matching rules", style="Heading.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Checkbutton(panel, text="Trim surrounding whitespace", variable=self.vars["compare.strip"]).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Checkbutton(panel, text="Ignore case", variable=self.vars["compare.ignore_case"]).grid(row=2, column=0, sticky="w", pady=6)
        ttk.Checkbutton(panel, text="Collapse internal whitespace", variable=self.vars["compare.collapse_internal_space"]).grid(row=3, column=0, sticky="w", pady=6)

    def _build_security_subtab(self, parent) -> None:
        panel = self._panel(parent, "Security")
        self.vars["security.password"] = tk.StringVar()
        ttk.Label(panel, text="Configuration password", style="Heading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(panel, text="Stored in the YAML config file. Required to open this screen.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.pw_show = tk.BooleanVar(value=False)
        self.pw_widget = self._field(panel, 2, "Password", self.vars["security.password"], show="•")
        ttk.Checkbutton(panel, text="Show password", variable=self.pw_show,
                        command=self._toggle_pw).grid(row=3, column=1, sticky="w")

    def _toggle_pw(self) -> None:
        self.pw_widget.configure(show="" if self.pw_show.get() else "•")

    # -- config <-> vars ------------------------------------------------
    def _load_vars_from_config(self) -> None:
        c = self.config
        self.vars["plc.driver"].set(c.plc.driver)
        self.vars["plc.ip_address"].set(c.plc.ip_address)
        self.vars["plc.slot"].set(str(c.plc.slot))
        for attr, _label, _desc in TAG_FIELDS:
            spec = c.plc.tag(attr)
            self.vars[f"plc.tag.{attr}.name"].set(spec.name)
            self.vars[f"plc.tag.{attr}.description"].set(spec.description)
        self.vars["scanner.type"].set(c.scanner.type)
        self.vars["scanner.port"].set(c.scanner.port)
        self.vars["scanner.baudrate"].set(str(c.scanner.baudrate))
        self.vars["scanner.scan_pattern"].set(c.scanner.scan_pattern or "")
        self.vars["shift.start_times"].set(", ".join(c.shift.start_times))
        self.vars["shift.poll_interval"].set(str(c.shift.poll_interval))
        self.vars["shift.watch_plc_request"].set(c.shift.watch_plc_request)
        self.vars["shift.lock_on_startup"].set(c.shift.lock_on_startup)
        self.vars["compare.strip"].set(c.compare.strip)
        self.vars["compare.ignore_case"].set(c.compare.ignore_case)
        self.vars["compare.collapse_internal_space"].set(c.compare.collapse_internal_space)
        self.vars["security.password"].set(c.security.password)

    def _build_config_from_vars(self) -> Config:
        g = lambda k: self.vars[k].get()
        times = [t.strip() for t in g("shift.start_times").split(",") if t.strip()]
        data = {
            "security": {"password": g("security.password")},
            "plc": {
                "driver": g("plc.driver"),
                "ip_address": g("plc.ip_address"),
                "slot": int(g("plc.slot") or 0),
                "tags": {
                    attr: {
                        "name": g(f"plc.tag.{attr}.name"),
                        "description": g(f"plc.tag.{attr}.description"),
                    }
                    for attr, _l, _d in TAG_FIELDS
                },
            },
            "scanner": {
                "type": g("scanner.type"),
                "port": g("scanner.port"),
                "baudrate": int(g("scanner.baudrate") or 9600),
                "scan_pattern": g("scanner.scan_pattern") or None,
            },
            "compare": {
                "strip": bool(g("compare.strip")),
                "ignore_case": bool(g("compare.ignore_case")),
                "collapse_internal_space": bool(g("compare.collapse_internal_space")),
            },
            "shift": {
                "start_times": times,
                "watch_plc_request": bool(g("shift.watch_plc_request")),
                "lock_on_startup": bool(g("shift.lock_on_startup")),
                "poll_interval": float(g("shift.poll_interval") or 2.0),
            },
        }
        return from_dict(data)

    def _save(self) -> None:
        try:
            new_config = self._build_config_from_vars()
        except (ConfigError, ValueError) as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return
        try:
            save_config(new_config, self.config_path)
        except ConfigError as exc:
            messagebox.showerror("Could not save", str(exc))
            return
        self.config = new_config
        self.simulate = self.simulate or new_config.plc.driver == "simulated"
        self.save_msg.configure(text=f"Saved to {self.config_path} and applied.", foreground=LOG_COLORS["ok"])
        self._restart_worker()

    def _reload(self) -> None:
        self._load_vars_from_config()
        self.save_msg.configure(text="Reloaded current values.", foreground=COL_MUTED)

    def _lock(self) -> None:
        self.unlocked = False
        for child in self.config_tab.winfo_children():
            child.destroy()
        self._build_lock_view()

    # -- worker plumbing ------------------------------------------------
    def _start_worker(self) -> None:
        self.events = queue.Queue()
        self.worker = PLCWorker(
            self.config,
            simulate=self.simulate,
            expected_model=self.expected_model,
            events=self.events,
        )
        self.commands = self.worker.commands
        self.worker.start()

    def _restart_worker(self) -> None:
        if self.worker is not None:
            self.worker.shutdown()
        self._start_worker()

    def _on_scan(self, event=None) -> None:
        raw = self.scan_var.get().strip()
        self.scan_var.set("")
        self.scan_entry.focus_set()
        if not raw or self.worker is None:
            return
        value = extract_model(raw, self.config.scanner.scan_pattern)
        self.commands.put((CMD_VERIFY, value))

    # -- event pump -----------------------------------------------------
    def _pump_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(100, self._pump_events)

    def _handle_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "log":
            self._append_log(event["level"], event["text"])
        elif etype == "connection":
            self._set_connection(event["connected"], event["message"])
        elif etype == "status":
            self._set_state(event.get("state", "DISCONNECTED"))
            self.expected_var.set(event.get("expected") or "—")
            self.lastscan_var.set(event.get("last_scan") or "—")
            self.shift_var.set(event.get("shift") or "—")

    def _set_connection(self, connected: bool, message: str) -> None:
        if connected:
            self.conn_var.set("● PLC: connected")
            self.conn_label.configure(fg="#34d399")
        else:
            self.conn_var.set("● PLC: disconnected")
            self.conn_label.configure(fg="#f87171")

    def _set_state(self, state: str) -> None:
        fg, bg, text = STATE_STYLES.get(state, STATE_STYLES["DISCONNECTED"])
        self.banner.configure(text=text, bg=bg, fg=fg)

    def _append_log(self, level: str, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{ts}  ", ("info",))
        self.log.insert("end", text + "\n", (level,))
        self.log.see("end")
        self.log.configure(state="disabled")

    # -- lifecycle ------------------------------------------------------
    def _on_close(self) -> None:
        if self.worker is not None:
            self.worker.shutdown()
            self.worker.join(timeout=2.0)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def launch(config: Config, config_path: Path, simulate: bool = False, expected_model: str = "ABC-100") -> None:
    MoreaderGUI(config, config_path, simulate=simulate, expected_model=expected_model).run()
