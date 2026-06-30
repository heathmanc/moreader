"""Shift-change / scan-verification controller.

State machine
-------------
``LOCKED``  -- a shift change has occurred; run-permit is cleared in the PLC and
              the operator must scan the manufacturing order.
``RUNNING`` -- the last scan matched the PLC's model number; run-permit is set
              and we watch for the next shift change.
``ALARM``   -- the last scan did NOT match; run-permit stays cleared, the alarm
              bit is set, and we wait for another scan.

The controller never lets the PLC run unless a scan has matched the model number
the PLC is currently configured for.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .config import CompareConfig, ShiftConfig
from .plc import PLCInterface
from .scanner import BarcodeScanner
from .shift import ShiftDetector

log = logging.getLogger(__name__)


class State(str, Enum):
    LOCKED = "LOCKED"
    RUNNING = "RUNNING"
    ALARM = "ALARM"


def normalize(value: str, cfg: CompareConfig) -> str:
    """Apply the configured normalisation to a value before comparison."""

    if cfg.strip:
        value = value.strip()
    if cfg.collapse_internal_space:
        value = " ".join(value.split())
    if cfg.ignore_case:
        value = value.casefold()
    return value


@dataclass
class ScanResult:
    matched: bool
    scanned: str
    expected: str


class Notifier:
    """Default console notifier.  Swap out for HMI/light-stack integration."""

    def shift_change(self, label: str) -> None:
        print(f"\n*** SHIFT CHANGE ({label}) -- machine locked out ***")
        print("    Scan the manufacturing order to enable running.")

    def prompt(self) -> str:
        return "Scan manufacturing order > "

    def match(self, result: ScanResult) -> None:
        print(f"[OK] Scan {result.scanned!r} matches PLC model {result.expected!r}. RUN ENABLED.")

    def mismatch(self, result: ScanResult) -> None:
        print("\n" + "!" * 60)
        print("!! ALARM: SCAN DOES NOT MATCH PLC MODEL -- RUN BLOCKED")
        print(f"!!   scanned : {result.scanned!r}")
        print(f"!!   PLC set : {result.expected!r}")
        print("!" * 60)
        print("    Re-scan the correct manufacturing order to clear the alarm.")

    def info(self, message: str) -> None:
        print(message)


class ShiftChangeController:
    def __init__(
        self,
        plc: PLCInterface,
        scanner: BarcodeScanner,
        compare_cfg: CompareConfig,
        shift_cfg: ShiftConfig,
        notifier: Notifier | None = None,
        detector: ShiftDetector | None = None,
    ) -> None:
        self.plc = plc
        self.scanner = scanner
        self.compare_cfg = compare_cfg
        self.shift_cfg = shift_cfg
        self.notifier = notifier or Notifier()
        self.detector = detector or ShiftDetector(shift_cfg)
        self.state = State.LOCKED if shift_cfg.lock_on_startup else State.RUNNING
        self._running = False

    # -- core operations -------------------------------------------------
    def lock_out(self, label: str | None = None) -> None:
        """Clear the run permit so the PLC cannot run, and require a scan."""

        self.plc.set_run_permit(False)
        self.state = State.LOCKED
        self.notifier.shift_change(label or self.detector.current_shift_label())

    def verify_scan(self, scanned: str) -> ScanResult:
        """Compare a scanned value against the PLC's expected model number.

        Updates the PLC permit/alarm bits and the controller state.
        """

        expected = self.plc.read_expected_model()
        self.plc.write_last_scan(scanned)
        matched = normalize(scanned, self.compare_cfg) == normalize(expected, self.compare_cfg)
        result = ScanResult(matched=matched, scanned=scanned, expected=expected)

        if matched:
            self.plc.set_alarm(False)
            self.plc.set_run_permit(True)
            self.state = State.RUNNING
            self.notifier.match(result)
        else:
            self.plc.set_run_permit(False)
            self.plc.set_alarm(True)
            self.state = State.ALARM
            self.notifier.mismatch(result)
        return result

    # -- main loop -------------------------------------------------------
    def run_forever(self, max_iterations: int | None = None) -> None:
        """Run the verification loop until input ends or stop() is called.

        ``max_iterations`` bounds the loop for tests/demos.
        """

        self._running = True
        # Make sure the machine starts safe.
        if self.state == State.LOCKED:
            self.plc.set_run_permit(False)
            self.notifier.shift_change(self.detector.current_shift_label())

        iterations = 0
        while self._running:
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1

            if self.state in (State.LOCKED, State.ALARM):
                scanned = self.scanner.read(self.notifier.prompt())
                if scanned is None:  # input stream closed
                    self.notifier.info("Input closed; exiting.")
                    break
                if not scanned.strip():
                    continue
                self.verify_scan(scanned)
            else:  # RUNNING -- watch for the next shift change
                if self._poll_shift_change():
                    self.lock_out()
                else:
                    self._sleep(self.shift_cfg.poll_interval)

        self._running = False

    def _poll_shift_change(self) -> bool:
        request = False
        if self.shift_cfg.watch_plc_request:
            try:
                request = self.plc.read_shift_request()
            except Exception as exc:  # don't crash the loop on a transient read
                log.warning("Could not read shift-request tag: %s", exc)
        return self.detector.check(plc_request_active=request)

    def _sleep(self, seconds: float) -> None:
        _time.sleep(seconds)

    def stop(self) -> None:
        self._running = False
