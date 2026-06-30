"""Barcode scan parsing.

Most USB barcode scanners ship in *keyboard-wedge* (HID) mode: they "type" the
barcode characters followed by Enter.  In the GUI those keystrokes land in the
modal scan dialog; in headless mode they arrive on standard input.

A scanned manufacturing order is reduced to an integer for comparison against
each PLC's model DINT: an optional regex extracts the model from a richer
barcode, then the configured last-N digits are taken and parsed as an int.
"""

from __future__ import annotations

import logging
import re
import sys
from abc import ABC, abstractmethod

from .config import CompareConfig, ScannerConfig

log = logging.getLogger(__name__)


class ScannerError(Exception):
    """Raised when the scanner cannot be read."""


def extract_mo_number(value: str, scanner_cfg: ScannerConfig, compare_cfg: CompareConfig) -> int | None:
    """Reduce a raw scanned barcode to the integer used for comparison.

    Returns ``None`` when no digits can be parsed from the scan.
    """

    value = value.strip("\r\n").strip()
    if scanner_cfg.scan_pattern:
        compiled = re.compile(scanner_cfg.scan_pattern)
        match = compiled.search(value)
        if match:
            if "model" in (compiled.groupindex or {}):
                value = match.group("model")
            elif match.groups():
                value = match.group(1)
            else:
                value = match.group(0)
        else:
            log.warning("Scan %r did not match scan_pattern; using raw value", value)

    if compare_cfg.digits_only:
        value = "".join(ch for ch in value if ch.isdigit())

    n = compare_cfg.mo_last_digits
    if n and n > 0:
        value = value[-n:]

    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        log.warning("Could not parse %r as an integer", value)
        return None


class BarcodeScanner(ABC):
    """Reads one raw barcode line per call (used by headless mode)."""

    def __init__(self, cfg: ScannerConfig) -> None:
        self.cfg = cfg

    @abstractmethod
    def read_raw(self, prompt: str | None = None) -> str | None:
        """Return one raw line from the scanner, or None on end-of-input."""

    def close(self) -> None:
        pass


class KeyboardWedgeScanner(BarcodeScanner):
    """Reads HID keyboard-wedge scanners from standard input (headless mode)."""

    def read_raw(self, prompt: str | None = None) -> str | None:
        try:
            if prompt and sys.stdin.isatty():
                return input(prompt)
            line = sys.stdin.readline()
            return None if line == "" else line
        except EOFError:
            return None
        except OSError as exc:  # pragma: no cover
            raise ScannerError(f"Could not read scanner input: {exc}") from exc


class IterableScanner(BarcodeScanner):
    """Feeds scans from any iterable of strings.  Useful for tests/demos."""

    def __init__(self, values, cfg: ScannerConfig | None = None) -> None:
        super().__init__(cfg or ScannerConfig(type="stdin"))
        self._it = iter(values)

    def read_raw(self, prompt: str | None = None) -> str | None:
        try:
            return next(self._it)
        except StopIteration:
            return None


def build_scanner(cfg: ScannerConfig) -> BarcodeScanner:
    if cfg.type in {"keyboard", "stdin", "serial"}:
        return KeyboardWedgeScanner(cfg)
    raise ScannerError(f"Unknown scanner type: {cfg.type!r}")
