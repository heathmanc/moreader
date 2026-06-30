"""PLC communication layer (Allen Bradley Logix via pylogix).

Two kinds of link:

* :class:`EncapsulatorLink` -- read-only; returns the recipe DINT.
* :class:`MasterLink` -- writes the COS ``MO_Verified`` BOOL and the heartbeat DINT.

Simulated implementations let the controller, worker, and GUI run end-to-end
with no hardware.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .config import EncapsulatorConfig, MasterConfig

log = logging.getLogger(__name__)


class PLCError(Exception):
    """Raised when a PLC read/write fails."""


class _PylogixConn:
    """Mixin that owns a pylogix connection and basic read/write helpers."""

    ip_address: str
    slot: int

    def __init__(self) -> None:
        self._comm = None

    def _open(self) -> None:
        try:
            from pylogix import PLC as PylogixDriver
        except ImportError as exc:  # pragma: no cover
            raise PLCError("pylogix is required: pip install pylogix") from exc
        self._comm = PylogixDriver()
        self._comm.IPAddress = self.ip_address
        self._comm.ProcessorSlot = self.slot

    def _close(self) -> None:
        if self._comm is not None:
            try:
                self._comm.Close()
            finally:
                self._comm = None

    def _require(self):
        if self._comm is None:
            raise PLCError("not connected")
        return self._comm

    def _read(self, tag: str):
        response = self._require().Read(tag)
        if response.Status != "Success":
            raise PLCError(f"failed reading {tag!r}: {response.Status}")
        return response.Value

    def _write(self, tag: str, value) -> None:
        response = self._require().Write(tag, value)
        if response.Status != "Success":
            raise PLCError(f"failed writing {tag!r}: {response.Status}")


# --- encapsulators (read-only) ----------------------------------------------

class EncapsulatorLink(ABC):
    def __init__(self, cfg: EncapsulatorConfig) -> None:
        self.cfg = cfg

    @property
    def name(self) -> str:
        return self.cfg.name

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def read_recipe(self) -> int:
        """Return the recipe-number DINT this encapsulator is set to run."""


class PylogixEncapsulator(EncapsulatorLink, _PylogixConn):
    def __init__(self, cfg: EncapsulatorConfig) -> None:
        EncapsulatorLink.__init__(self, cfg)
        _PylogixConn.__init__(self)
        self.ip_address = cfg.ip_address
        self.slot = cfg.slot

    def connect(self) -> None:
        self._open()
        self._read(self.cfg.recipe_tag.name)  # verify connectivity
        log.info("Connected to %s at %s", self.cfg.name, self.cfg.ip_address)

    def close(self) -> None:
        self._close()

    def read_recipe(self) -> int:
        value = self._read(self.cfg.recipe_tag.name)
        return int(value) if value is not None else 0


class SimulatedEncapsulator(EncapsulatorLink):
    def __init__(self, cfg: EncapsulatorConfig | None = None, recipe: int = 1001) -> None:
        super().__init__(cfg or EncapsulatorConfig())
        self._recipe = recipe
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        log.info("Connected to SIMULATED %s (recipe=%s)", self.cfg.name, self._recipe)

    def close(self) -> None:
        self.connected = False

    def set_recipe(self, recipe: int) -> None:
        self._recipe = recipe

    def read_recipe(self) -> int:
        return self._recipe


# --- master (COS) ------------------------------------------------------------

class MasterLink(ABC):
    def __init__(self, cfg: MasterConfig) -> None:
        self.cfg = cfg

    @property
    def name(self) -> str:
        return self.cfg.name

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def set_mo_verified(self, verified: bool) -> None:
        """Allow (True) or block (False) the master from running the product."""

    @abstractmethod
    def write_heartbeat(self, value: int) -> None:
        """Write the heartbeat DINT so the master knows the app is alive."""


class PylogixMaster(MasterLink, _PylogixConn):
    def __init__(self, cfg: MasterConfig) -> None:
        MasterLink.__init__(self, cfg)
        _PylogixConn.__init__(self)
        self.ip_address = cfg.ip_address
        self.slot = cfg.slot

    def connect(self) -> None:
        self._open()
        self._read(self.cfg.mo_verified_tag.name)  # verify connectivity
        log.info("Connected to master %s at %s", self.cfg.name, self.cfg.ip_address)

    def close(self) -> None:
        self._close()

    def set_mo_verified(self, verified: bool) -> None:
        self._write(self.cfg.mo_verified_tag.name, bool(verified))

    def write_heartbeat(self, value: int) -> None:
        if self.cfg.heartbeat_tag.name:
            self._write(self.cfg.heartbeat_tag.name, int(value))


class SimulatedMaster(MasterLink):
    def __init__(self, cfg: MasterConfig | None = None) -> None:
        super().__init__(cfg or MasterConfig())
        self.mo_verified = False
        self.heartbeat = 0
        self.connected = False

    def connect(self) -> None:
        self.connected = True
        log.info("Connected to SIMULATED master %s", self.cfg.name)

    def close(self) -> None:
        self.connected = False

    def set_mo_verified(self, verified: bool) -> None:
        self.mo_verified = bool(verified)

    def write_heartbeat(self, value: int) -> None:
        self.heartbeat = int(value)


# --- factories ---------------------------------------------------------------

def build_encapsulator(cfg: EncapsulatorConfig, driver: str, sim_recipe: int = 1001) -> EncapsulatorLink:
    if driver == "simulated":
        return SimulatedEncapsulator(cfg, sim_recipe)
    if driver == "logix":
        return PylogixEncapsulator(cfg)
    raise PLCError(f"Unknown PLC driver: {driver!r}")


def build_master(cfg: MasterConfig, driver: str) -> MasterLink:
    if driver == "simulated":
        return SimulatedMaster(cfg)
    if driver == "logix":
        return PylogixMaster(cfg)
    raise PLCError(f"Unknown PLC driver: {driver!r}")
