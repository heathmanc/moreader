"""Per-PLC monitors and the line verdict.

* :class:`EncapsulatorMonitor` -- read-only; tracks the recipe DINT and whether
  it matched the last scan.
* :class:`MasterMonitor` -- owns the COS ``MO_Verified`` bit and the heartbeat.

A scan is *verified* only when every connected encapsulator's recipe equals the
scanned number; the master's MO_Verified bit follows that verdict.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from enum import Enum

from .plc import EncapsulatorLink, MasterLink
from .scanner import recipe_to_digits

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
    recipe: str | None
    scanned: str | None
    matched: bool | None


class EncapsulatorMonitor:
    """Tracks one read-only encapsulator PLC."""

    def __init__(self, link: EncapsulatorLink) -> None:
        self.link = link
        self.state = State.DISCONNECTED
        self.recipe: str | None = None
        self.scanned: str | None = None
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

    def evaluate(self, scanned: str | None, last_n: int) -> bool:
        """Compare the scanned MO digits to this encapsulator's recipe.

        The recipe may be a single number or a dash-separated list to search
        (e.g. ``1321-1333-8634-9121``).  The scan matches if it equals *any* of
        the values.  Each value is zero-padded to the scan width so a scan of
        ``0042`` matches a recipe of ``42`` without discarding leading zeros.
        """

        self.recipe = self.link.read_recipe()
        self.scanned = scanned
        if scanned is None:
            matched = False
        else:
            width = last_n if (last_n and last_n > 0) else len(scanned)
            tokens = [t for t in re.split(r"\D+", str(self.recipe)) if t]
            matched = any(scanned == recipe_to_digits(int(t), width) for t in tokens)
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
        self.cycle_stop_enabled = True   # when False, moreader never writes CycleStopReq
        self.heartbeat = 0               # last written value (0/1 for toggle, count for increment)
        self.heartbeat_mode = "toggle"   # "toggle" (BOOL on/off) or "increment" (DINT)
        self._hb_on = False
        self.connected = False
        # The heartbeat runs on its own thread, so every link access is guarded.
        self._lock = threading.RLock()

    @property
    def name(self) -> str:
        return self.link.name

    def connect(self) -> None:
        with self._lock:
            self.link.connect()
            self.connected = True
            # Start safe: nothing verified, bypassed, or requesting a cycle stop.
            self.link.set_mo_verified(False)
            self.link.set_mo_bypassed(False)
            if self.cycle_stop_enabled:
                self.link.set_cycle_stop(False)
            self.mo_verified = False
            self.mo_bypassed = False
            self.cycle_stop = False
            self._recompute()

    def close(self) -> None:
        with self._lock:
            try:
                self.link.close()
            finally:
                self.connected = False
                self.state = State.DISCONNECTED

    def mark_disconnected(self) -> None:
        with self._lock:
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
        with self._lock:
            self.link.set_mo_verified(verified)
            self.mo_verified = verified
            self._recompute()

    def read_verified(self) -> bool:
        """Read the live MO_Verified bit (the PLC may have cleared it)."""
        with self._lock:
            return self.link.read_mo_verified()

    def set_bypassed(self, bypassed: bool) -> None:
        with self._lock:
            self.link.set_mo_bypassed(bypassed)
            self.mo_bypassed = bypassed
            self._recompute()

    def reassert_bypass(self) -> None:
        """Re-write MO_Bypassed to moreader's authoritative value.

        Called every poll so a bypass set directly in the PLC (to skip
        verification) is overwritten — only the passworded button turns it on.
        """
        with self._lock:
            self.link.set_mo_bypassed(self.mo_bypassed)

    def set_cycle_stop(self, requested: bool) -> None:
        if not self.cycle_stop_enabled:
            return   # the PLC owns CycleStopReq; moreader stays out of it
        with self._lock:
            self.link.set_cycle_stop(requested)
            self.cycle_stop = requested

    def beat(self) -> None:
        """Pulse the watchdog: toggle a BOOL ON/OFF, or count up a DINT."""

        with self._lock:
            self._beat_locked()

    def _beat_locked(self) -> None:
        if self.heartbeat_mode == "increment":
            self.heartbeat = (self.heartbeat + 1) % HEARTBEAT_WRAP
            self.link.write_heartbeat(self.heartbeat)
        else:  # toggle
            self._hb_on = not self._hb_on
            self.heartbeat = 1 if self._hb_on else 0
            self.link.write_heartbeat(self._hb_on)   # writes a BOOL True/False


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
