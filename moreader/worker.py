"""Background PLC worker thread.

All PLC I/O happens here, off the GUI thread, so a slow or offline PLC never
freezes the interface.  The worker talks to the GUI through two thread-safe
queues:

* **commands** (GUI -> worker): verify a scan, force a lockout, reconnect, shut down.
* **events** (worker -> GUI): status snapshots, log lines, connection state.

The worker reuses :class:`~moreader.controller.ShiftChangeController` for the
actual scan/PLC logic, so the behaviour is identical to the headless CLI and is
covered by the same kind of tests (see ``tests/test_worker.py``).
"""

from __future__ import annotations

import logging
import queue
import threading

from .config import Config
from .controller import Notifier, ShiftChangeController, State
from .plc import PLCError, SimulatedPLC, build_plc
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
        self._log("warn", f"Shift change ({label}) — machine locked out. Scan the manufacturing order.")

    def match(self, result) -> None:
        self._log("ok", f"OK: {result.scanned!r} matches PLC model {result.expected!r}. RUN ENABLED.")

    def mismatch(self, result) -> None:
        self._log(
            "alarm",
            f"ALARM: scan {result.scanned!r} does not match PLC model {result.expected!r}. RUN BLOCKED.",
        )

    def info(self, message: str) -> None:
        self._log("info", message)

    def prompt(self) -> str:  # unused in GUI mode
        return ""


class PLCWorker(threading.Thread):
    def __init__(
        self,
        config: Config,
        simulate: bool = False,
        expected_model: str = "ABC-100",
        commands: "queue.Queue | None" = None,
        events: "queue.Queue | None" = None,
    ) -> None:
        super().__init__(daemon=True, name="PLCWorker")
        self.config = config
        self.simulate = simulate or config.plc.driver == "simulated"
        self.expected_model_sim = expected_model
        self.commands: queue.Queue = commands or queue.Queue()
        self.events: queue.Queue = events or queue.Queue()

        self.notifier = QueueNotifier(self.events)
        self.detector = ShiftDetector(config.shift)
        self.plc = None
        self.controller: ShiftChangeController | None = None
        self.connected = False
        self._running = False

        # Cached values for status snapshots.
        self.expected = ""
        self.last_scan = ""
        self.last_matched: bool | None = None

    # -- public API used by the GUI -------------------------------------
    def submit(self, command: str, value: str | None = None) -> None:
        self.commands.put((command, value))

    def shutdown(self) -> None:
        self.commands.put((CMD_SHUTDOWN, None))

    # -- thread body ----------------------------------------------------
    def run(self) -> None:
        self._running = True
        self._connect()
        while self._running:
            try:
                command, value = self.commands.get(timeout=self.config.shift.poll_interval)
            except queue.Empty:
                command, value = None, None
            if command is not None:
                self._handle(command, value)
            self._housekeeping()
        self._close()

    # -- connection -----------------------------------------------------
    def _build_plc(self):
        if self.simulate:
            return SimulatedPLC(self.config.plc, self.expected_model_sim)
        return build_plc(self.config.plc)

    def _connect(self) -> None:
        try:
            self.plc = self._build_plc()
            self.plc.connect()
            self.controller = ShiftChangeController(
                plc=self.plc,
                scanner=None,  # the GUI feeds scans directly
                compare_cfg=self.config.compare,
                shift_cfg=self.config.shift,
                notifier=self.notifier,
                detector=self.detector,
            )
            self.connected = True
            self._emit_connection(True, f"Connected to PLC ({'simulated' if self.simulate else self.config.plc.ip_address})")
            # Start safe: require a scan before the first run.
            if self.config.shift.lock_on_startup:
                self.controller.lock_out()
            self._refresh_expected()
            self._emit_status()
        except PLCError as exc:
            self.connected = False
            self._emit_connection(False, f"PLC connection failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            self.connected = False
            self._emit_connection(False, f"PLC connection error: {exc}")

    def _close(self) -> None:
        if self.plc is not None:
            try:
                self.plc.close()
            except Exception:  # pragma: no cover
                pass

    # -- command handling ----------------------------------------------
    def _handle(self, command: str, value: str | None) -> None:
        if command == CMD_SHUTDOWN:
            self._running = False
            return
        if command == CMD_RECONNECT:
            self._close()
            self.connected = False
            self._connect()
            return
        if not self.connected or self.controller is None:
            self.notifier.info("Ignored: PLC not connected.")
            return
        try:
            if command == CMD_VERIFY and value is not None:
                result = self.controller.verify_scan(value)
                self.last_scan = result.scanned
                self.last_matched = result.matched
                self.expected = result.expected
            elif command == CMD_LOCKOUT:
                self.controller.lock_out()
                self.last_matched = None
        except PLCError as exc:
            self.connected = False
            self._emit_connection(False, f"PLC I/O error: {exc}")
        self._emit_status()

    def _housekeeping(self) -> None:
        if not self.connected:
            # Attempt a reconnect on the next idle pass.
            self._connect()
            return
        if self.controller is None:
            return
        try:
            if self.controller.state is State.RUNNING:
                request = False
                if self.config.shift.watch_plc_request:
                    request = self.plc.read_shift_request()
                if self.detector.check(plc_request_active=request):
                    self.controller.lock_out()
                    self.last_matched = None
                    self._emit_status()
            self._refresh_expected()
        except PLCError as exc:
            self.connected = False
            self._emit_connection(False, f"PLC read error: {exc}")

    def _refresh_expected(self) -> None:
        if self.plc is None:
            return
        try:
            new_expected = self.plc.read_expected_model()
        except PLCError:
            return
        if new_expected != self.expected:
            self.expected = new_expected
            self._emit_status()

    # -- event emission -------------------------------------------------
    def _emit_status(self) -> None:
        state = self.controller.state.value if self.controller else "DISCONNECTED"
        self.events.put(
            {
                "type": "status",
                "state": state,
                "connected": self.connected,
                "expected": self.expected,
                "last_scan": self.last_scan,
                "matched": self.last_matched,
                "shift": self.detector.current_shift_label(),
            }
        )

    def _emit_connection(self, connected: bool, message: str) -> None:
        self.events.put({"type": "connection", "connected": connected, "message": message})
        self.events.put({"type": "log", "level": "info" if connected else "alarm", "text": message})
        self._emit_status()
