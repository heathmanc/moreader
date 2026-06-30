"""moreader -- manufacturing-order scan verification against Allen Bradley PLCs.

Reads a manufacturing-order barcode from a USB scanner and compares the last few
digits to the model-number DINT each of three PLCs is currently set to run.  A
new shift must scan a matching order before a PLC's run permit is granted; a
mismatch raises that PLC's alarm.
"""

from .config import Config, load_config, save_config
from .controller import MachineMonitor, MachineResult, MachineState
from .plc import MachineLink, SimulatedMachine, build_machine
from .scanner import build_scanner, extract_mo_number
from .worker import PLCWorker

__version__ = "0.3.0"

__all__ = [
    "Config",
    "load_config",
    "save_config",
    "MachineMonitor",
    "MachineResult",
    "MachineState",
    "MachineLink",
    "SimulatedMachine",
    "build_machine",
    "build_scanner",
    "extract_mo_number",
    "PLCWorker",
]
