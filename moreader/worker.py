"""Background PLC worker thread (handles all three PLCs).

All PLC I/O happens here, off the GUI thread, so a slow or offline PLC never
freezes the interface.  The worker talks to the GUI through two thread-safe
queues:

* **commands** (GUI -> worker): verify a scan, force a lockout, reconnect, shut down.
* **events** (worker -> GUI): per-machine status snapshots and log lines.
"""

from __future__ import annotations

import logging
import queue
import threading

from .config import Config
from .controller import MachineMonitor, MachineState, Notifier
from .plc import PLCError, build_machine
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
        self._log("warn", f"Shift change ({label}) — all machines locked out. Scan the MO.")

    def info(self, message: str) -> None:
        self._log("info", message)


class PLCWorker(threading.Thread):
    def __init__(
        self,
        config: Config,
        simulate: bool = False,
        sim_models: list[int] | None = None,
        commands: "queue.Queue | None" = None,
        events: "queue.Queue | None" = None,
    ) -> None:
        super().__init__(daemon=True, name="PLCWorker")
        self.config = config
        self.simulate = simulate or config.plc.driver == "simulated"
        self.sim_models = sim_models or [1001, 1002, 1003]
        self.commands: queue.Queue = commands or queue.Queue()
        self.events: queue.Queue = events or queue.Queue()

        self.notifier = QueueNotifier(self.events)
        self.detector = ShiftDetector(config.shift)
        self.monitors: list[MachineMonitor] = []
        self._running = False

    # -- public API -----------------------------------------------------
    def submit(self, command: str, value: str | None = None) -> None:
        self.commands.put((command, value))

    def shutdown(self) -> None:
        self.commands.put((CMD_SHUTDOWN, None))

    # -- thread body ----------------------------------------------------
    def run(self) -> None:
        self._running = True
        self._build_monitors()
        self._connect_all()
        while self._running:
            try:
                command, value = self.commands.get(timeout=self.config.shift.poll_interval)
            except queue.Empty:
                command, value = None, None
            if command is not None:
                self._handle(command, value)
            self._housekeeping()
        for monitor in self.monitors:
            monitor.close()

    # -- setup ----------------------------------------------------------
    def _build_monitors(self) -> None:
        driver = "simulated" if self.simulate else self.config.plc.driver
        self.monitors = []
        for i, machine_cfg in enumerate(self.config.plc.machines):
            model = self.sim_models[i] if i < len(self.sim_models) else 1000
            link = build_machine(machine_cfg, driver, sim_model=model)
            self.monitors.append(MachineMonitor(link))

    def _connect_all(self) -> None:
        for monitor in self.monitors:
            self._connect_one(monitor)
        # Start safe: require a scan before the first run.
        if self.config.shift.lock_on_startup:
            self.notifier.shift_change(self.detector.current_shift_label())
            for monitor in self.monitors:
                if monitor.connected:
                    try:
                        monitor.lock_out()
                    except PLCError as exc:
                        self._fail(monitor, exc)
        self._emit_status()

    def _connect_one(self, monitor: MachineMonitor) -> None:
        try:
            monitor.connect()
            self.notifier.info(f"Connected to {monitor.name}.")
        except PLCError as exc:
            monitor.mark_disconnected()
            self.notifier._log("alarm", f"{monitor.name}: connection failed — {exc}")

    def _fail(self, monitor: MachineMonitor, exc: Exception) -> None:
        monitor.mark_disconnected()
        self.notifier._log("alarm", f"{monitor.name}: PLC I/O error — {exc}")

    # -- command handling ----------------------------------------------
    def _handle(self, command: str, value: str | None) -> None:
        if command == CMD_SHUTDOWN:
            self._running = False
            return
        if command == CMD_RECONNECT:
            for monitor in self.monitors:
                monitor.close()
                self._connect_one(monitor)
            self._emit_status()
            return
        if command == CMD_LOCKOUT:
            for monitor in self.monitors:
                if monitor.connected:
                    try:
                        monitor.lock_out()
                    except PLCError as exc:
                        self._fail(monitor, exc)
            self._emit_status()
            return
        if command == CMD_VERIFY:
            self._verify(value or "")

    def _verify(self, raw: str) -> None:
        number = extract_mo_number(raw, self.config.scanner, self.config.compare)
        if number is None:
            self.notifier._log("alarm", f"Invalid scan {raw!r}: no number could be read.")
            self._emit_status()
            return
        self.notifier._log("info", f"Scanned MO → comparing {number} to each PLC model.")
        for monitor in self.monitors:
            if not monitor.connected:
                continue
            try:
                result = monitor.verify(number)
            except PLCError as exc:
                self._fail(monitor, exc)
                continue
            if result.matched:
                self.notifier._log("ok", f"{monitor.name}: {number} matches model {result.model} → RUN ENABLED.")
            else:
                self.notifier._log("alarm", f"{monitor.name}: {number} ≠ model {result.model} → RUN BLOCKED.")
        self._emit_status()

    def _housekeeping(self) -> None:
        # Reconnect anything that dropped.
        for monitor in self.monitors:
            if not monitor.connected:
                self._connect_one(monitor)

        connected = [m for m in self.monitors if m.connected]
        if not connected:
            return

        # Shift-change detection (global): any machine's request bit or the clock.
        request = False
        if self.config.shift.watch_plc_request:
            for monitor in connected:
                try:
                    if monitor.read_shift_request():
                        request = True
                        break
                except PLCError as exc:
                    self._fail(monitor, exc)
        running = any(m.state is MachineState.RUNNING for m in connected)
        if self.detector.check(plc_request_active=request) and running:
            self.notifier.shift_change(self.detector.current_shift_label())
            for monitor in connected:
                try:
                    monitor.lock_out()
                except PLCError as exc:
                    self._fail(monitor, exc)
            self._emit_status()
            return

        # Refresh model setpoints for display.
        changed = False
        for monitor in connected:
            try:
                before = monitor.model
                monitor.refresh_model()
                changed = changed or (before != monitor.model)
            except PLCError as exc:
                self._fail(monitor, exc)
        if changed:
            self._emit_status()

    # -- event emission -------------------------------------------------
    def _emit_status(self) -> None:
        machines = [
            {
                "name": m.name,
                "state": m.state.value,
                "model": m.model,
                "scanned": m.scanned,
                "matched": m.matched,
                "connected": m.connected,
            }
            for m in self.monitors
        ]
        self.events.put(
            {
                "type": "status",
                "machines": machines,
                "shift": self.detector.current_shift_label(),
            }
        )
