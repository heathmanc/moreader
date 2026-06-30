"""PLC communication layer.

A thin abstraction over `pycomm3 <https://github.com/ottowayi/pycomm3>`_ so the
rest of the program never imports the hardware driver directly.  This keeps the
controller testable: :class:`SimulatedPLC` implements the same interface with no
hardware or third-party dependency.

The expected model number lives in a PLC tag (``expected_model_tag``).  The
program reads it, compares it to the scanned barcode, and writes the result back
to the run-permit / alarm BOOL tags so the PLC logic can gate the machine.
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

    # Context-manager sugar so callers can use ``with build_plc(cfg) as plc:``.
    def __enter__(self) -> "PLCInterface":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class _PycommBase(PLCInterface):
    """Shared logic for the pycomm3-backed drivers."""

    def __init__(self, cfg: PLCConfig) -> None:
        self.cfg = cfg
        self._driver = None  # set in connect()

    def _path(self) -> str:
        raise NotImplementedError

    def _make_driver(self):
        raise NotImplementedError

    def connect(self) -> None:
        self._driver = self._make_driver()
        self._driver.open()
        log.info("Connected to PLC at %s", self._path())

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            finally:
                self._driver = None

    def _require(self):
        if self._driver is None:
            raise PLCError("PLC is not connected; call connect() first")
        return self._driver

    def _read(self, tag: str):
        result = self._require().read(tag)
        if result is None or not getattr(result, "error", None) in (None, ""):
            err = getattr(result, "error", "unknown error")
            raise PLCError(f"Failed reading tag {tag!r}: {err}")
        return result.value

    def _write(self, tag: str, value) -> None:
        result = self._require().write((tag, value))
        # pycomm3 returns a Tag (or list); a truthy .error means failure.
        items = result if isinstance(result, list) else [result]
        for item in items:
            if getattr(item, "error", None):
                raise PLCError(f"Failed writing tag {tag!r}: {item.error}")

    def read_expected_model(self) -> str:
        value = self._read(self.cfg.expected_model_tag)
        return "" if value is None else str(value)

    def read_shift_request(self) -> bool:
        if not self.cfg.shift_request_tag:
            return False
        return bool(self._read(self.cfg.shift_request_tag))

    def set_run_permit(self, allowed: bool) -> None:
        self._write(self.cfg.run_permit_tag, bool(allowed))

    def set_alarm(self, active: bool) -> None:
        if self.cfg.alarm_tag:
            self._write(self.cfg.alarm_tag, bool(active))

    def write_last_scan(self, value: str) -> None:
        if self.cfg.last_scan_tag:
            try:
                self._write(self.cfg.last_scan_tag, value)
            except PLCError as exc:  # non-critical: just an HMI echo
                log.warning("Could not write last-scan tag: %s", exc)


class LogixPLC(_PycommBase):
    """CompactLogix / ControlLogix / Micro800 via pycomm3 LogixDriver."""

    def _path(self) -> str:
        # ControlLogix needs the CPU slot; CompactLogix/Micro800 ignore it.
        if self.cfg.slot:
            return f"{self.cfg.ip_address}/{self.cfg.slot}"
        return self.cfg.ip_address

    def _make_driver(self):
        try:
            from pycomm3 import LogixDriver
        except ImportError as exc:  # pragma: no cover
            raise PLCError(
                "pycomm3 is required for the Logix driver: pip install pycomm3"
            ) from exc
        return LogixDriver(self._path())


class SLCPLC(_PycommBase):
    """MicroLogix / SLC 500 via pycomm3 SLCDriver (file-based addressing)."""

    def _path(self) -> str:
        return self.cfg.ip_address

    def _make_driver(self):
        try:
            from pycomm3 import SLCDriver
        except ImportError as exc:  # pragma: no cover
            raise PLCError(
                "pycomm3 is required for the SLC driver: pip install pycomm3"
            ) from exc
        return SLCDriver(self._path())


class SimulatedPLC(PLCInterface):
    """In-memory PLC for dry runs, demos, and tests (no hardware needed).

    The expected model defaults to ``ABC-100`` but can be set with
    :meth:`set_expected_model` to mimic an operator/engineer changing the job on
    the PLC.
    """

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
        return LogixPLC(cfg)
    if cfg.driver == "slc":
        return SLCPLC(cfg)
    if cfg.driver == "simulated":
        return SimulatedPLC(cfg)
    raise PLCError(f"Unknown PLC driver: {cfg.driver!r}")
