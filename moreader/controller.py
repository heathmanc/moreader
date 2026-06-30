"""Per-PLC monitors and the line verdict.

* :class:`EncapsulatorMonitor` -- read-only; tracks the recipe DINT and whether
  it matched the last scan.
* :class:`MasterMonitor` -- owns the COS ``MO_Verified`` bit and the heartbeat.

A scan is *verified* only when every connected encapsulator's recipe equals the
scanned number; the master's MO_Verified bit follows that verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from .plc import EncapsulatorLink, MasterLink

log = logging.getLogger(__name__)

# Heartbeat counter wraps here so the value stays small and easy to read.
HEARTBEAT_WRAP = 1_000_000


class State(str, Enum):
    LOCKED = "LOCKED"        # awaiting a scan
    MATCH = "MATCH"          # encapsulator recipe matched the scan
    MISMATCH = "MISMATCH"    # encapsulator recipe did not match
    VERIFIED = "VERIFIED"    # master: MO_Verified true
    BYPASSED = "BYPASSED"    # master: operator bypass active
    DISCONNECTED = "DISCONNECTED"


@dataclass
class EncapsulatorResult:
    name: str
    state: State
    recipe: int | None
    scanned: int | None
    matched: bool | None


class EncapsulatorMonitor:
    """Tracks one read-only encapsulator PLC."""

    def __init__(self, link: EncapsulatorLink) -> None:
        self.link = link
        self.state = State.DISCONNECTED
        self.recipe: int | None = None
        self.scanned: int | None = None
        self.matched: bool | None = None
        self.connected = False

    @property
    def name(self) -> str:
        return self.link.name

    def connect(self) -> None:
        self.link.connect()
        self.connected = True
        self.state = State.LOCKED
        self.refresh_recipe()

    def close(self) -> None:
        try:
            self.link.close()
        finally:
            self.connected = False
            self.state = State.DISCONNECTED

    def mark_disconnected(self) -> None:
        self.connected = False
        self.state = State.DISCONNECTED

    def refresh_recipe(self) -> None:
        self.recipe = self.link.read_recipe()

    def lock(self) -> None:
        self.state = State.LOCKED
        self.scanned = None
        self.matched = None

    def evaluate(self, number: int | None) -> bool:
        """Compare a scanned number to this encapsulator's recipe DINT."""

        self.recipe = self.link.read_recipe()
        self.scanned = number
        matched = number is not None and number == self.recipe
        self.matched = matched
        self.state = State.MATCH if matched else State.MISMATCH
        return matched

    def result(self) -> EncapsulatorResult:
        return EncapsulatorResult(self.name, self.state, self.recipe, self.scanned, self.matched)


class MasterMonitor:
    """Owns the COS master: the MO_Verified bit and the heartbeat counter."""

    def __init__(self, link: MasterLink) -> None:
        self.link = link
        self.state = State.DISCONNECTED
        self.mo_verified = False
        self.mo_bypassed = False
        self.cycle_stop = False
        self.heartbeat = 0
        self.connected = False

    @property
    def name(self) -> str:
        return self.link.name

    def connect(self) -> None:
        self.link.connect()
        self.connected = True
        # Start safe: nothing verified, bypassed, or requesting a cycle stop.
        self.link.set_mo_verified(False)
        self.link.set_mo_bypassed(False)
        self.link.set_cycle_stop(False)
        self.mo_verified = False
        self.mo_bypassed = False
        self.cycle_stop = False
        self._recompute()

    def close(self) -> None:
        try:
            self.link.close()
        finally:
            self.connected = False
            self.state = State.DISCONNECTED

    def mark_disconnected(self) -> None:
        self.connected = False
        self.state = State.DISCONNECTED

    def _recompute(self) -> None:
        if self.mo_bypassed:
            self.state = State.BYPASSED
        elif self.mo_verified:
            self.state = State.VERIFIED
        else:
            self.state = State.LOCKED

    def set_verified(self, verified: bool) -> None:
        self.link.set_mo_verified(verified)
        self.mo_verified = verified
        self._recompute()

    def set_bypassed(self, bypassed: bool) -> None:
        self.link.set_mo_bypassed(bypassed)
        self.mo_bypassed = bypassed
        self._recompute()

    def set_cycle_stop(self, requested: bool) -> None:
        self.link.set_cycle_stop(requested)
        self.cycle_stop = requested

    def beat(self) -> None:
        self.heartbeat = (self.heartbeat + 1) % HEARTBEAT_WRAP
        self.link.write_heartbeat(self.heartbeat)


class Notifier:
    """Default console notifier (used by the headless loop)."""

    def shift_change(self, label: str) -> None:
        print(f"\n*** SHIFT CHANGE ({label}) -- MO_Verified cleared; scan the MO ***")

    def scan(self, number: int | None, results, verified: bool) -> None:
        if number is None:
            print("[INVALID] Could not read a number from the scan.")
        for r in results:
            tag = "OK   " if r.matched else "FAIL "
            print(f"[{tag}] {r.name}: recipe {r.recipe} vs scan {number}")
        print("==> MO VERIFIED — COS may run." if verified else "==> NOT VERIFIED — COS blocked.")

    def info(self, message: str) -> None:
        print(message)
