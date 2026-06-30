"""moreader -- manufacturing-order scan verification against an Allen Bradley PLC.

Reads a manufacturing-order / model barcode from a USB scanner and compares it to
the model number a PLC is currently set to run.  A new shift must scan a matching
order before the program grants the PLC's run permit; a mismatch raises an alarm.
"""

from .config import Config, load_config
from .controller import ScanResult, ShiftChangeController, State
from .plc import PLCInterface, SimulatedPLC, build_plc
from .scanner import BarcodeScanner, build_scanner

__version__ = "0.1.0"

__all__ = [
    "Config",
    "load_config",
    "ShiftChangeController",
    "ScanResult",
    "State",
    "PLCInterface",
    "SimulatedPLC",
    "build_plc",
    "BarcodeScanner",
    "build_scanner",
]
