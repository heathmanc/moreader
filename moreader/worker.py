"""Background PLC worker thread (3 encapsulators + COS master).

All PLC I/O happens here, off the GUI thread.  The worker:

* reads each encapsulator's recipe DINT,
* on a scan, sets the master ``MO_Verified`` bit true only if every connected
  encapsulator's recipe matches the scanned number,
* clears ``MO_Verified`` on a shift change or a manual lockout,
* writes an incrementing heartbeat DINT to the master on a fixed interval.

It talks to the GUI through two thread-safe queues (commands in, events out).
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from .audit import AuditLog
from .config import Config
from .controller import EncapsulatorMonitor, MasterMonitor, Notifier, State
from .plc import PLCError, build_encapsulator, build_master
from .scanner import extract_battery_digits, extract_last_digits, extract_mo_digits
from .shift import ShiftDetector

log = logging.getLogger(__name__)

# Command types (GUI -> worker)
CMD_VERIFY = "verify"
CMD_LOCKOUT = "lockout"
CMD_BYPASS = "bypass"        # value "on"/"off"
CMD_RECONNECT = "reconnect"
CMD_SHUTDOWN = "shutdown"


class QueueNotifier(Notifier):
    """Notifier that pushes log lines onto the GUI event queue."""

    def __init__(self, events: "queue.Queue") -> None:
        self.events = events

    def _log(self, level: str, text: str) -> None:
        self.events.put({"type": "log", "level": level, "text": text})

    def shift_change(self, label: str) -> None:
        self._log("warn", f"Shift change ({label}) — MO_Verified cleared. Scan the MO.")

    def info(self, message: str) -> None:
        self._log("info", message)


class PLCWorker(threading.Thread):
    def __init__(
        self,
        config: Config,
        simulate: bool = False,
        sim_recipes: list[int] | None = None,
        commands: "queue.Queue | None" = None,
        events: "queue.Queue | None" = None,
    ) -> None:
        super().__init__(daemon=True, name="PLCWorker")
        self.config = config
        self.simulate = simulate or config.plc.driver == "simulated"
        self.sim_recipes = sim_recipes or [1001, 1001, 1001]
        self.commands: queue.Queue = commands or queue.Queue()
        self.events: queue.Queue = events or queue.Queue()

        self.notifier = QueueNotifier(self.events)
        self.detector = ShiftDetector(config.shift)
        self.audit = AuditLog(config.audit.directory, config.audit.enabled)
        self.encapsulators: list[EncapsulatorMonitor] = []
        self.master: MasterMonitor | None = None
        self._running = False
        self._last_beat = 0.0
        self._offline: set[str] = set()   # names currently logged as offline (throttle)
        self.battery_scanned: int | None = None
        self.battery_matched: bool | None = None

    # -- public API -----------------------------------------------------
    def submit(self, command: str, value: str | None = None) -> None:
        self.commands.put((command, value))

    def shutdown(self) -> None:
        self.commands.put((CMD_SHUTDOWN, None))

    # -- thread body ----------------------------------------------------
    def run(self) -> None:
        self._running = True
        self._build()
        self._connect_all()
        loop_timeout = max(0.1, min(self.config.shift.poll_interval, self.config.plc.heartbeat_interval))
        while self._running:
            try:
                command, value = self.commands.get(timeout=loop_timeout)
            except queue.Empty:
                command, value = None, None
            if command is not None:
                self._handle(command, value)
            self._housekeeping()
        for enc in self.encapsulators:
            enc.close()
        if self.master:
            self.master.close()

    # -- setup ----------------------------------------------------------
    def _build(self) -> None:
        driver = "simulated" if self.simulate else self.config.plc.driver
        self.encapsulators = []
        for i, enc_cfg in enumerate(self.config.plc.encapsulators):
            recipe = self.sim_recipes[i] if i < len(self.sim_recipes) else 1001
            link = build_encapsulator(enc_cfg, driver, sim_recipe=recipe)
            self.encapsulators.append(EncapsulatorMonitor(link))
        self.master = MasterMonitor(build_master(self.config.plc.master, driver))

    def _connect_all(self) -> None:
        for enc in self.encapsulators:
            self._connect_enc(enc)
        self._connect_master()
        if self.config.shift.lock_on_startup:
            self.notifier.shift_change(self.detector.current_shift_label())
            self._lockout(cycle_stop=False)   # nothing is running yet at startup
        self._emit_status()

    def _connect_enc(self, enc: EncapsulatorMonitor) -> None:
        try:
            enc.connect()
            self._report_online(enc.name)
        except PLCError as exc:
            enc.mark_disconnected()
            self._report_offline(enc.name, exc)

    def _connect_master(self) -> None:
        try:
            self.master.connect()
            self._report_online(self.master.name)
        except PLCError as exc:
            self.master.mark_disconnected()
            self._report_offline(self.master.name, exc)

    def _report_offline(self, name: str, exc: Exception) -> None:
        # Log/audit only on the transition to offline, not on every retry.
        if name not in self._offline:
            self._offline.add(name)
            self.notifier._log("alarm", f"{name}: connection failed — {exc}")
            self.audit.record("PLC_OFFLINE", name, detail=str(exc))

    def _report_online(self, name: str) -> None:
        if name in self._offline:
            self._offline.discard(name)
            self.notifier._log("ok", f"{name}: reconnected.")
            self.audit.record("PLC_ONLINE", name)
        else:
            self.notifier.info(f"Connected to {name}.")

    # -- command handling ----------------------------------------------
    def _handle(self, command: str, value: str | None) -> None:
        if command == CMD_SHUTDOWN:
            self._running = False
            return
        if command == CMD_RECONNECT:
            for enc in self.encapsulators:
                enc.close()
                self._connect_enc(enc)
            self.master.close()
            self._connect_master()
            self._emit_status()
            return
        if command == CMD_LOCKOUT:
            self.notifier._log("warn", "Manual lockout — MO_Verified/MO_Bypassed cleared, cycle stop requested.")
            self._lockout(cycle_stop=True)
            self.audit.record("LOCKOUT", detail="manual lockout button")
            self._emit_status()
            return
        if command == CMD_BYPASS:
            self._bypass(value == "on")
            self._emit_status()
            return
        if command == CMD_VERIFY:
            self._verify(value)

    def _reset_battery(self) -> None:
        self.battery_scanned = None
        self.battery_matched = None

    def _lockout(self, cycle_stop: bool = False) -> None:
        self._reset_battery()
        for enc in self.encapsulators:
            if enc.connected:
                enc.lock()
        if self.master and self.master.connected:
            try:
                self.master.set_verified(False)
                self.master.set_bypassed(False)        # bypass resets at shift/lockout
                if cycle_stop:
                    self.master.set_cycle_stop(True)   # graceful stop so the cycle finishes
            except PLCError as exc:
                self._fail_master(exc)

    def _bypass(self, on: bool) -> None:
        if not (self.master and self.master.connected):
            self.notifier._log("alarm", "Cannot bypass — master not connected.")
            return
        try:
            self.master.set_bypassed(on)
            if on:
                self.master.set_cycle_stop(False)      # bypass means the line may run
        except PLCError as exc:
            self._fail_master(exc)
            return
        if on:
            self.notifier._log("warn", f"MO BYPASS ENABLED by operator — {self.master.name} may run without a verified scan.")
        else:
            self.notifier._log("info", "MO bypass cleared.")
        self.audit.record("BYPASS", "ON" if on else "OFF", detail="operator bypass")

    def _reject(self, title: str, reasons: list[str], stuffed=None, assembled=None, battery=None) -> None:
        """Block the master, log the reasons, and pop an error screen in the GUI."""

        for r in reasons:
            self.notifier._log("alarm", r)
        self.events.put({"type": "error", "title": title, "reasons": reasons})
        self.audit.record("VERIFY", "FAIL", stuffed_mo=stuffed, assembled_mo=assembled,
                          battery=battery, detail=" | ".join(reasons))
        if self.master and self.master.connected and self.master.mo_verified:
            try:
                self.master.set_verified(False)
            except PLCError as exc:
                self._fail_master(exc)
        self._emit_status()

    def _verify(self, payload) -> None:
        # payload is either the raw stuffed-element MO string, or a dict with
        # {"mo": ..., "assembled_mo": ..., "battery": ...} when the battery scan
        # is enabled.
        if isinstance(payload, dict):
            raw_mo = payload.get("mo", "")
            raw_assembled = payload.get("assembled_mo")
            raw_battery = payload.get("battery")
        else:
            raw_mo = payload or ""
            raw_assembled = None
            raw_battery = None

        assembled_digits = None

        # --- Stuffed Element MO -> encapsulator recipe check ---
        mo_text = (raw_mo or "").strip("\r\n").strip()
        mo_len = self.config.compare.mo_length
        if mo_len and len(mo_text) != mo_len:
            self._reject("STUFFED ELEMENT MO SCAN FAILED",
                         [f"MO must be {mo_len} characters — you scanned {len(mo_text)}.",
                          "Make sure you scanned the Stuffed Element MO, not another label."],
                         stuffed=mo_text)
            return

        number = extract_mo_digits(raw_mo, self.config.scanner, self.config.compare)
        if number is None:
            self._reject("STUFFED ELEMENT MO SCAN FAILED",
                         [f"No number could be read from the scan {raw_mo!r}."],
                         stuffed=mo_text)
            return
        self.notifier._log("info", f"Stuffed Element MO {number} → comparing to each encapsulator recipe.")

        connected = [e for e in self.encapsulators if e.connected]
        all_present = len(connected) == len(self.encapsulators)
        last_n = self.config.compare.mo_last_digits
        reasons: list[str] = []
        if not all_present:
            offline = [e.name for e in self.encapsulators if not e.connected]
            reasons.append("Not all encapsulators are online: " + ", ".join(offline) + ".")
        for enc in connected:
            try:
                matched = enc.evaluate(number, last_n)
            except PLCError as exc:
                self._fail_enc(enc, exc)
                reasons.append(f"{enc.name}: PLC read error.")
                continue
            if matched:
                self.notifier._log("ok", f"{enc.name}: recipe {enc.recipe} matches {number}.")
            else:
                self.notifier._log("alarm", f"{enc.name}: recipe {enc.recipe} ≠ {number}.")
                reasons.append(f"{enc.name}: set to recipe {enc.recipe}, but the MO ends in {number}.")

        # --- Assembled Battery MO vs battery label (optional) ---
        if self.config.secondary.enabled:
            sec = self.config.secondary
            assembled_text = (raw_assembled or "").strip("\r\n").strip()
            battery_text = (raw_battery or "").strip("\r\n").strip()
            if sec.assembled_mo_length and len(assembled_text) != sec.assembled_mo_length:
                self._reset_battery()
                self._reject("ASSEMBLED BATTERY MO SCAN FAILED",
                             [f"Assembled Battery MO must be {sec.assembled_mo_length} characters — "
                              f"you scanned {len(assembled_text)}.",
                              "Make sure you scanned the Assembled Battery MO."],
                             stuffed=number)
                return
            if sec.battery_min_length and len(battery_text) < sec.battery_min_length:
                self._reset_battery()
                self._reject("BATTERY LABEL SCAN FAILED",
                             [f"Battery label must be at least {sec.battery_min_length} characters — "
                              f"you scanned {len(battery_text)}.",
                              "Make sure you scanned the battery label, not the MO."],
                             stuffed=number)
                return
            assembled_digits = extract_last_digits(assembled_text, self.config.scanner, sec.assembled_mo_last_digits)
            self.battery_scanned = extract_battery_digits(battery_text, self.config.scanner, sec.battery_first_digits)
            self.battery_matched = (
                assembled_digits is not None
                and self.battery_scanned is not None
                and assembled_digits == self.battery_scanned
            )
            if assembled_digits is None or self.battery_scanned is None:
                reasons.append("Could not read a number from the Assembled Battery MO or the battery label.")
            elif self.battery_matched:
                self.notifier._log("ok", f"Battery label {self.battery_scanned} matches Assembled MO {assembled_digits}.")
            else:
                reasons.append(f"Battery label starts with {self.battery_scanned}, "
                               f"but the Assembled Battery MO ends in {assembled_digits}.")

        if reasons:
            self._reject("SCAN VERIFICATION FAILED", reasons,
                         stuffed=number, assembled=assembled_digits, battery=self.battery_scanned)
            return

        # --- success ---
        if self.master and self.master.connected:
            try:
                self.master.set_verified(True)
                self.master.set_cycle_stop(False)   # release the graceful stop
            except PLCError as exc:
                self._fail_master(exc)
        self.audit.record("VERIFY", "PASS", stuffed_mo=number,
                          assembled_mo=assembled_digits, battery=self.battery_scanned)
        self.notifier._log("ok", f"MO {number} VERIFIED — {self.master.name} may run.")
        self._emit_status()

    def _housekeeping(self) -> None:
        # Reconnect anything that dropped.
        for enc in self.encapsulators:
            if not enc.connected:
                self._connect_enc(enc)
        if self.master and not self.master.connected:
            self._connect_master()

        # Heartbeat to the master on its own interval.
        now = time.monotonic()
        if self.master and self.master.connected and (now - self._last_beat) >= self.config.plc.heartbeat_interval:
            self._last_beat = now
            try:
                self.master.beat()
            except PLCError as exc:
                self._fail_master(exc)

        # Changeover: the PLC cleared MO_Verified on its own -> require a re-scan.
        if self.master and self.master.connected and self.master.mo_verified:
            try:
                if not self.master.read_verified():
                    self.notifier._log("warn", "Changeover detected (PLC cleared MO_Verified) — scan required.")
                    self._lockout(cycle_stop=False)   # the PLC is driving the changeover
                    self.audit.record("CHANGEOVER", detail="PLC cleared MO_Verified")
                    self._emit_status()
                    return
            except PLCError as exc:
                self._fail_master(exc)

        # Time-based shift change clears verification and bypass.
        running = self.master is not None and (self.master.mo_verified or self.master.mo_bypassed)
        if self.detector.check() and running:
            self.notifier.shift_change(self.detector.current_shift_label())
            self._lockout(cycle_stop=True)   # graceful stop so the cycle finishes
            self.audit.record("SHIFT_LOCKOUT", detail=self.detector.current_shift_label())
            self._emit_status()
            return

        # Refresh recipe setpoints for display.
        changed = False
        for enc in self.encapsulators:
            if not enc.connected:
                continue
            try:
                before = enc.recipe
                enc.refresh_recipe()
                changed = changed or (before != enc.recipe)
            except PLCError as exc:
                self._fail_enc(enc, exc)
                changed = True
        if changed:
            self._emit_status()

    def _fail_enc(self, enc: EncapsulatorMonitor, exc: Exception) -> None:
        enc.mark_disconnected()
        self._report_offline(enc.name, exc)

    def _fail_master(self, exc: Exception) -> None:
        self.master.mark_disconnected()
        self._report_offline(self.master.name, exc)

    # -- event emission -------------------------------------------------
    def _emit_status(self) -> None:
        encapsulators = [
            {
                "name": e.name,
                "state": e.state.value,
                "recipe": e.recipe,
                "scanned": e.scanned,
                "matched": e.matched,
                "connected": e.connected,
            }
            for e in self.encapsulators
        ]
        master = {
            "name": self.master.name if self.master else "COS",
            "state": self.master.state.value if self.master else "DISCONNECTED",
            "mo_verified": self.master.mo_verified if self.master else False,
            "mo_bypassed": self.master.mo_bypassed if self.master else False,
            "cycle_stop": self.master.cycle_stop if self.master else False,
            "heartbeat": self.master.heartbeat if self.master else 0,
            "connected": self.master.connected if self.master else False,
        }
        self.events.put(
            {
                "type": "status",
                "encapsulators": encapsulators,
                "master": master,
                "secondary": {
                    "enabled": self.config.secondary.enabled,
                    "scanned": self.battery_scanned,
                    "matched": self.battery_matched,
                },
                "shift": self.detector.current_shift_label(),
            }
        )
