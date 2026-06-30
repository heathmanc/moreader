"""Per-machine scan-verification logic.

Each :class:`MachineMonitor` owns one PLC link and the small state machine for
that machine:

``LOCKED``  -- a shift change occurred; run-permit cleared, awaiting a scan.
``RUNNING`` -- the last scan matched this PLC's model DINT; run-permit set.
``ALARM``   -- the last scan did NOT match; run-permit cleared, alarm set.
``DISCONNECTED`` -- the PLC link is down.

Keeping this off the worker thread and free of any GUI/transport concern makes it
straightforward to unit-test against the simulated machine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from .plc import MachineLink, PLCError

log = logging.getLogger(__name__)


class MachineState(str, Enum):
    LOCKED = "LOCKED"
    RUNNING = "RUNNING"
    ALARM = "ALARM"
    DISCONNECTED = "DISCONNECTED"


@dataclass
class MachineResult:
    name: str
    state: MachineState
    model: int | None
    scanned: int | None
    matched: bool


class MachineMonitor:
    """Tracks one machine/PLC and applies scan results to it."""

    def __init__(self, link: MachineLink) -> None:
        self.link = link
        self.state = MachineState.DISCONNECTED
        self.model: int | None = None
        self.scanned: int | None = None
        self.matched: bool = False
        self.connected = False

    @property
    def name(self) -> str:
        return self.link.name

    # -- connection -----------------------------------------------------
    def connect(self) -> None:
        self.link.connect()
        self.connected = True
        self.state = MachineState.LOCKED
        self.refresh_model()

    def close(self) -> None:
        try:
            self.link.close()
        finally:
            self.connected = False
            self.state = MachineState.DISCONNECTED

    def mark_disconnected(self) -> None:
        self.connected = False
        self.state = MachineState.DISCONNECTED

    def refresh_model(self) -> None:
        self.model = self.link.read_model()

    def read_shift_request(self) -> bool:
        return self.link.read_shift_request()

    # -- operations -----------------------------------------------------
    def lock_out(self) -> None:
        """Clear the run permit so this PLC cannot run; require a re-scan."""

        self.link.set_run_permit(False)
        self.state = MachineState.LOCKED
        self.matched = False

    def verify(self, number: int | None) -> MachineResult:
        """Compare a scanned number to this PLC's model DINT and apply the result."""

        self.model = self.link.read_model()
        self.scanned = number
        matched = number is not None and number == self.model
        self.matched = matched
        if number is not None:
            self.link.write_last_scan(number)
        if matched:
            self.link.set_alarm(False)
            self.link.set_run_permit(True)
            self.state = MachineState.RUNNING
        else:
            self.link.set_run_permit(False)
            self.link.set_alarm(True)
            self.state = MachineState.ALARM
        return self.result()

    def result(self) -> MachineResult:
        return MachineResult(
            name=self.name,
            state=self.state,
            model=self.model,
            scanned=self.scanned,
            matched=self.matched,
        )


class Notifier:
    """Default console notifier (used by the headless loop)."""

    def shift_change(self, label: str) -> None:
        print(f"\n*** SHIFT CHANGE ({label}) -- all machines locked out; scan the MO ***")

    def scan(self, number: int | None, results: list[MachineResult]) -> None:
        if number is None:
            print("[INVALID] Could not read a number from the scan.")
        for r in results:
            if r.state is MachineState.RUNNING:
                print(f"[OK]    {r.name}: scan {number} matches model {r.model} -> RUN ENABLED")
            elif r.state is MachineState.ALARM:
                print(f"[ALARM] {r.name}: scan {number} != model {r.model} -> RUN BLOCKED")
            else:
                print(f"[----]  {r.name}: {r.state.value}")

    def info(self, message: str) -> None:
        print(message)
