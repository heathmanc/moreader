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

from .config import Config
from .controller import EncapsulatorMonitor, MasterMonitor, Notifier, State
from .plc import PLCError, build_encapsulator, build_master
from .scanner import extract_mo_number
from .shift import ShiftDetector

log = logging.getLogger(__name__)

# Command types (GUI -> worker)
CMD_VERIFY = "verify"
CMD_LOCKOUT = "lockout"
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
        self.encapsulators: list[EncapsulatorMonitor] = []
        self.master: MasterMonitor | None = None
        self._running = False
        self._last_beat = 0.0

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
            self._lockout(silent=True)
        self._emit_status()

    def _connect_enc(self, enc: EncapsulatorMonitor) -> None:
        try:
            enc.connect()
            self.notifier.info(f"Connected to {enc.name}.")
        except PLCError as exc:
            enc.mark_disconnected()
            self.notifier._log("alarm", f"{enc.name}: connection failed — {exc}")

    def _connect_master(self) -> None:
        try:
            self.master.connect()
            self.notifier.info(f"Connected to master {self.master.name}.")
        except PLCError as exc:
            self.master.mark_disconnected()
            self.notifier._log("alarm", f"{self.master.name}: connection failed — {exc}")

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
            self.notifier._log("warn", "Manual lockout — MO_Verified cleared.")
            self._lockout()
            self._emit_status()
            return
        if command == CMD_VERIFY:
            self._verify(value or "")

    def _lockout(self, silent: bool = False) -> None:
        for enc in self.encapsulators:
            if enc.connected:
                enc.lock()
        if self.master and self.master.connected:
            try:
                self.master.set_verified(False)
            except PLCError as exc:
                self._fail_master(exc)

    def _verify(self, raw: str) -> None:
        number = extract_mo_number(raw, self.config.scanner, self.config.compare)
        if number is None:
            self.notifier._log("alarm", f"Invalid scan {raw!r}: no number could be read.")
            self._emit_status()
            return
        self.notifier._log("info", f"Scanned MO → comparing {number} to each encapsulator recipe.")

        connected = [e for e in self.encapsulators if e.connected]
        all_present = len(connected) == len(self.encapsulators)
        all_matched = all_present
        for enc in connected:
            try:
                matched = enc.evaluate(number)
            except PLCError as exc:
                self._fail_enc(enc, exc)
                all_matched = False
                continue
            if matched:
                self.notifier._log("ok", f"{enc.name}: recipe {enc.recipe} matches {number}.")
            else:
                self.notifier._log("alarm", f"{enc.name}: recipe {enc.recipe} ≠ {number}.")
            all_matched = all_matched and matched

        if not all_present:
            self.notifier._log("alarm", "Not all encapsulators are connected — cannot verify.")

        if self.master and self.master.connected:
            try:
                self.master.set_verified(all_matched)
            except PLCError as exc:
                self._fail_master(exc)
        if all_matched:
            self.notifier._log("ok", f"MO {number} VERIFIED — {self.master.name} may run.")
        else:
            self.notifier._log("alarm", f"MO {number} NOT verified — {self.master.name} blocked.")
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

        # Time-based shift change clears verification.
        running = self.master is not None and self.master.mo_verified
        if self.detector.check() and running:
            self.notifier.shift_change(self.detector.current_shift_label())
            self._lockout()
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
        self.notifier._log("alarm", f"{enc.name}: PLC I/O error — {exc}")

    def _fail_master(self, exc: Exception) -> None:
        self.master.mark_disconnected()
        self.notifier._log("alarm", f"{self.master.name}: PLC I/O error — {exc}")

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
            "heartbeat": self.master.heartbeat if self.master else 0,
            "connected": self.master.connected if self.master else False,
        }
        self.events.put(
            {
                "type": "status",
                "encapsulators": encapsulators,
                "master": master,
                "shift": self.detector.current_shift_label(),
            }
        )
