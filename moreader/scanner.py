"""Barcode scanner input.

Most USB barcode scanners ship in *keyboard-wedge* (HID) mode: they "type" the
barcode characters followed by Enter, exactly like a keyboard.  For a console
application that means each scan arrives as a line on standard input, so the
default reader simply blocks on :func:`input`.

A serial reader is also provided for scanners configured as a USB virtual COM
port (USB-CDC), which requires ``pyserial``.
"""

from __future__ import annotations

import logging
import re
import sys
from abc import ABC, abstractmethod

from .config import ScannerConfig

log = logging.getLogger(__name__)


class ScannerError(Exception):
    """Raised when the scanner cannot be read."""


def extract_model(value: str, pattern: str | None) -> str:
    """Apply an optional regex to pull the model out of a richer barcode.

    Prefers a named ``model`` group, then the first capture group, else the whole
    match.  If the pattern does not match, the trimmed raw value is returned.
    """

    value = value.strip("\r\n")
    if not pattern:
        return value
    compiled = re.compile(pattern)
    match = compiled.search(value)
    if not match:
        log.warning("Scan %r did not match scan_pattern; using raw value", value)
        return value
    if "model" in (compiled.groupindex or {}):
        return match.group("model")
    if match.groups():
        return match.group(1)
    return match.group(0)


class BarcodeScanner(ABC):
    """Reads one barcode per call."""

    def __init__(self, cfg: ScannerConfig) -> None:
        self.cfg = cfg

    @abstractmethod
    def _read_raw(self, prompt: str | None) -> str | None:
        """Return one raw line from the scanner, or None on end-of-input."""

    def read(self, prompt: str | None = None) -> str | None:
        """Read a scan and apply the optional extraction pattern.

        Returns the (possibly extracted) value, or ``None`` if the input stream
        has closed.
        """

        raw = self._read_raw(prompt)
        if raw is None:
            return None
        return extract_model(raw, self.cfg.scan_pattern)

    def close(self) -> None:  # overridden where needed
        pass

    def __enter__(self) -> "BarcodeScanner":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class KeyboardWedgeScanner(BarcodeScanner):
    """Reads HID keyboard-wedge scanners from standard input."""

    def _read_raw(self, prompt: str | None) -> str | None:
        try:
            if prompt and sys.stdin.isatty():
                return input(prompt)
            line = sys.stdin.readline()
            if line == "":  # EOF
                return None
            return line
        except EOFError:
            return None
        except KeyboardInterrupt:
            raise
        except OSError as exc:  # pragma: no cover
            raise ScannerError(f"Could not read scanner input: {exc}") from exc


class SerialScanner(BarcodeScanner):
    """Reads scanners exposed as a USB serial / virtual COM port."""

    def __init__(self, cfg: ScannerConfig) -> None:
        super().__init__(cfg)
        try:
            import serial  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ScannerError(
                "pyserial is required for the serial scanner: pip install pyserial"
            ) from exc
        import serial

        self._serial = serial.Serial(cfg.port, cfg.baudrate, timeout=None)

    def _read_raw(self, prompt: str | None) -> str | None:
        if prompt:
            print(prompt, end="", flush=True)
        line = self._serial.readline()
        if not line:
            return None
        return line.decode("ascii", errors="replace")

    def close(self) -> None:
        try:
            self._serial.close()
        except Exception:  # pragma: no cover
            pass


class IterableScanner(BarcodeScanner):
    """Feeds scans from any iterable of strings.  Useful for tests/demos."""

    def __init__(self, values, cfg: ScannerConfig | None = None) -> None:
        super().__init__(cfg or ScannerConfig(type="stdin"))
        self._it = iter(values)

    def _read_raw(self, prompt: str | None) -> str | None:
        if prompt:
            print(prompt, end="", flush=True)
        try:
            return next(self._it)
        except StopIteration:
            return None


def build_scanner(cfg: ScannerConfig) -> BarcodeScanner:
    """Factory: pick the scanner backend named in the config."""

    if cfg.type in {"keyboard", "stdin"}:
        return KeyboardWedgeScanner(cfg)
    if cfg.type == "serial":
        return SerialScanner(cfg)
    raise ScannerError(f"Unknown scanner type: {cfg.type!r}")
