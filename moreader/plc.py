"""PLC communication layer (Allen Bradley Logix via pylogix).

Each machine is one PLC.  A :class:`MachineLink` wraps a single PLC connection
and exposes just what the controller needs: read the model DINT, set the
run-permit / alarm BOOLs, optionally echo the scanned number, and read a
shift-change request bit.

:class:`SimulatedMachine` implements the same interface with no hardware, so the
controller, worker, and GUI can be exercised end-to-end without a PLC.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .config import MachineConfig

log = logging.getLogger(__name__)


class PLCError(Exception):
    """Raised when a PLC read/write fails."""


class MachineLink(ABC):
    """Connection to a single machine/PLC."""

    def __init__(self, cfg: MachineConfig) -> None:
        self.cfg = cfg

    @property
    def name(self) -> str:
        return self.cfg.name

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def read_model(self) -> int:
        """Return the model-number DINT the PLC is currently set to run."""

    @abstractmethod
    def read_shift_request(self) -> bool: ...

    @abstractmethod
    def set_run_permit(self, allowed: bool) -> None: ...

    @abstractmethod
    def set_alarm(self, active: bool) -> None: ...

    @abstractmethod
    def write_last_scan(self, value: int) -> None: ...


class PylogixMachine(MachineLink):
    """A CompactLogix / ControlLogix PLC via pylogix."""

    def __init__(self, cfg: MachineConfig) -> None:
        super().__init__(cfg)
        self._comm = None

    def connect(self) -> None:
        try:
            from pylogix import PLC as PylogixDriver
        except ImportError as exc:  # pragma: no cover
            raise PLCError("pylogix is required: pip install pylogix") from exc
        self._comm = PylogixDriver()
        self._comm.IPAddress = self.cfg.ip_address
        self._comm.ProcessorSlot = self.cfg.slot
        # pylogix connects lazily; verify by reading the model DINT.
        self._read(self.cfg.model_tag.name)
        log.info("Connected to %s at %s slot %s", self.cfg.name, self.cfg.ip_address, self.cfg.slot)

    def close(self) -> None:
        if self._comm is not None:
            try:
                self._comm.Close()
            finally:
                self._comm = None

    def _require(self):
        if self._comm is None:
            raise PLCError(f"{self.cfg.name}: not connected")
        return self._comm

    def _read(self, tag: str):
        response = self._require().Read(tag)
        if response.Status != "Success":
            raise PLCError(f"{self.cfg.name}: failed reading {tag!r}: {response.Status}")
        return response.Value

    def _write(self, tag: str, value) -> None:
        response = self._require().Write(tag, value)
        if response.Status != "Success":
            raise PLCError(f"{self.cfg.name}: failed writing {tag!r}: {response.Status}")

    def read_model(self) -> int:
        value = self._read(self.cfg.model_tag.name)
        return int(value) if value is not None else 0

    def read_shift_request(self) -> bool:
        tag = self.cfg.shift_request_tag.name
        if not tag:
            return False
        return bool(self._read(tag))

    def set_run_permit(self, allowed: bool) -> None:
        self._write(self.cfg.run_permit_tag.name, bool(allowed))

    def set_alarm(self, active: bool) -> None:
        if self.cfg.alarm_tag.name:
            self._write(self.cfg.alarm_tag.name, bool(active))

    def write_last_scan(self, value: int) -> None:
        tag = self.cfg.last_scan_tag.name
        if tag:
            try:
                self._write(tag, int(value))
            except PLCError as exc:  # non-critical echo
                log.warning("Could not write last-scan tag on %s: %s", self.cfg.name, exc)


class SimulatedMachine(MachineLink):
    """In-memory machine for dry runs, demos, and tests."""

    def __init__(self, cfg: MachineConfig | None = None, model: int = 1001) -> None:
        super().__init__(cfg or MachineConfig())
        self._model = model
        self._shift_request = False
        self.run_permit = False
        self.alarm = False
        self.last_scan: int | None = None
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        log.info("Connected to SIMULATED %s (model=%s)", self.cfg.name, self._model)

    def close(self) -> None:
        self.connected = False

    # Test/demo helpers -------------------------------------------------
    def set_model(self, model: int) -> None:
        self._model = model

    def request_shift_change(self, active: bool = True) -> None:
        self._shift_request = active

    # Interface ---------------------------------------------------------
    def read_model(self) -> int:
        return self._model

    def read_shift_request(self) -> bool:
        return self._shift_request

    def set_run_permit(self, allowed: bool) -> None:
        self.run_permit = bool(allowed)

    def set_alarm(self, active: bool) -> None:
        self.alarm = bool(active)

    def write_last_scan(self, value: int) -> None:
        self.last_scan = int(value)


def build_machine(cfg: MachineConfig, driver: str, sim_model: int = 1001) -> MachineLink:
    """Factory: build the link for one machine based on the driver."""

    if driver == "simulated":
        return SimulatedMachine(cfg, sim_model)
    if driver == "logix":
        return PylogixMachine(cfg)
    raise PLCError(f"Unknown PLC driver: {driver!r}")
