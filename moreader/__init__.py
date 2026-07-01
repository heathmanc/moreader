"""moreader -- manufacturing-order scan verification against Allen Bradley PLCs.

Reads the recipe DINT from three encapsulator PLCs, compares the last digits of a
scanned manufacturing order to each, and sets the COS master PLC's MO_Verified
bit when they all match.  A shift change or manual lockout clears it, and a
heartbeat DINT is written to the master to prove the app is alive.
"""

from .audit import AuditLog
from .config import Config, load_config, save_config
from .controller import EncapsulatorMonitor, MasterMonitor, State
from .plc import build_encapsulator, build_master
from .scanner import build_scanner, extract_mo_digits
from .worker import PLCWorker

__version__ = "0.5.0"

__all__ = [
    "Config",
    "load_config",
    "save_config",
    "EncapsulatorMonitor",
    "MasterMonitor",
    "State",
    "build_encapsulator",
    "build_master",
    "build_scanner",
    "extract_mo_digits",
    "PLCWorker",
    "AuditLog",
]
