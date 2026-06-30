"""PLC communication layer (Allen Bradley Logix via pylogix).

A thin abstraction over `pylogix <https://github.com/dmroeder/pylogix>`_ so the
rest of the program never imports the driver directly.  This keeps the controller
testable: :class:`SimulatedPLC` implements the same interface with no hardware or
third-party dependency.

The expected model number lives in a PLC tag.  The program reads it, compares it
to the scanned barcode, and writes the result back to the run-permit / alarm BOOL
tags so the PLC logic can gate the machine.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .config import PLCConfig

log = logging.getLogger(__name__)


class PLCError(Exception):
    """Raised when a PLC read/write fails."""


class PLCInterface(ABC):
    """Common interface for every PLC backend."""

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def read_expected_model(self) -> str:
        """Return the model/part number the PLC is currently set to run."""

    @abstractmethod
    def read_shift_request(self) -> bool:
        """Return True while the PLC/HMI is requesting a shift-change lockout."""

    @abstractmethod
    def set_run_permit(self, allowed: bool) -> None:
        """Allow (True) or block (False) the PLC from running the product."""

    @abstractmethod
    def set_alarm(self, active: bool) -> None:
        """Drive the scan-mismatch alarm bit."""

    @abstractmethod
    def write_last_scan(self, value: str) -> None:
        """Echo the last scanned value back to the PLC for HMI display."""

    def __enter__(self) -> "PLCInterface":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class PylogixPLC(PLCInterface):
    """CompactLogix / ControlLogix via pylogix."""

    def __init__(self, cfg: PLCConfig) -> None:
        self.cfg = cfg
        self._comm = None

    def connect(self) -> None:
        try:
            from pylogix import PLC as PylogixDriver
        except ImportError as exc:  # pragma: no cover
            raise PLCError("pylogix is required: pip install pylogix") from exc
        self._comm = PylogixDriver()
        self._comm.IPAddress = self.cfg.ip_address
        self._comm.ProcessorSlot = self.cfg.slot
        # pylogix connects lazily on first request; verify by reading the model.
        self._read(self.cfg.expected_model.name)
        log.info("Connected to PLC at %s slot %s", self.cfg.ip_address, self.cfg.slot)

    def close(self) -> None:
        if self._comm is not None:
            try:
                self._comm.Close()
            finally:
                self._comm = None

    def _require(self):
        if self._comm is None:
            raise PLCError("PLC is not connected; call connect() first")
        return self._comm

    def _read(self, tag: str):
        response = self._require().Read(tag)
        if response.Status != "Success":
            raise PLCError(f"Failed reading tag {tag!r}: {response.Status}")
        return response.Value

    def _write(self, tag: str, value) -> None:
        response = self._require().Write(tag, value)
        if response.Status != "Success":
            raise PLCError(f"Failed writing tag {tag!r}: {response.Status}")

    def read_expected_model(self) -> str:
        value = self._read(self.cfg.expected_model.name)
        return "" if value is None else str(value)

    def read_shift_request(self) -> bool:
        tag = self.cfg.shift_request.name
        if not tag:
            return False
        return bool(self._read(tag))

    def set_run_permit(self, allowed: bool) -> None:
        self._write(self.cfg.run_permit.name, bool(allowed))

    def set_alarm(self, active: bool) -> None:
        if self.cfg.alarm.name:
            self._write(self.cfg.alarm.name, bool(active))

    def write_last_scan(self, value: str) -> None:
        tag = self.cfg.last_scan.name
        if tag:
            try:
                self._write(tag, value)
            except PLCError as exc:  # non-critical: just an HMI echo
                log.warning("Could not write last-scan tag: %s", exc)


class SimulatedPLC(PLCInterface):
    """In-memory PLC for dry runs, demos, and tests (no hardware needed)."""

    def __init__(self, cfg: PLCConfig | None = None, expected_model: str = "ABC-100") -> None:
        self.cfg = cfg
        self._expected = expected_model
        self._shift_request = False
        self.run_permit = False
        self.alarm = False
        self.last_scan = ""
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        log.info("Connected to SIMULATED PLC (expected model=%s)", self._expected)

    def close(self) -> None:
        self.connected = False

    # Test/demo helpers -------------------------------------------------
    def set_expected_model(self, model: str) -> None:
        self._expected = model

    def request_shift_change(self, active: bool = True) -> None:
        self._shift_request = active

    # Interface ---------------------------------------------------------
    def read_expected_model(self) -> str:
        return self._expected

    def read_shift_request(self) -> bool:
        return self._shift_request

    def set_run_permit(self, allowed: bool) -> None:
        self.run_permit = bool(allowed)

    def set_alarm(self, active: bool) -> None:
        self.alarm = bool(active)

    def write_last_scan(self, value: str) -> None:
        self.last_scan = value


def build_plc(cfg: PLCConfig) -> PLCInterface:
    """Factory: pick the PLC backend named in the config."""

    if cfg.driver == "logix":
        return PylogixPLC(cfg)
    if cfg.driver == "simulated":
        return SimulatedPLC(cfg)
    raise PLCError(f"Unknown PLC driver: {cfg.driver!r}")
