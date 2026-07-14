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

from .config import CompareConfig, MoFormat, ScannerConfig

log = logging.getLogger(__name__)


class ScannerError(Exception):
    """Raised when the scanner cannot be read."""


def match_mo_format(raw: str, formats: list[MoFormat]) -> MoFormat | None:
    """Pick the first format whose prefix matches the start of the scan."""

    text = raw.strip("\r\n").strip().upper()
    for fmt in formats:
        if fmt.prefix == "" or text.startswith(fmt.prefix.upper()):
            return fmt
    return None


def parse_mo(raw: str, formats: list[MoFormat]) -> tuple["MoFormat | None", str | None, str | None]:
    """Parse an MO barcode using the matching format.

    Returns ``(format, digits, error)``.  ``digits`` is the compare value (a
    string, leading zeros kept); ``error`` is a short reason when it fails.
    """

    text = raw.strip("\r\n").strip()
    fmt = match_mo_format(text, formats)
    if fmt is None:
        return None, None, "does not match any known barcode format"
    if fmt.length and len(text) != fmt.length:
        return fmt, None, f"must be {fmt.length} characters (you scanned {len(text)})"
    if fmt.take == "slice":
        value = text[fmt.start - 1: fmt.start - 1 + fmt.count]
    else:  # last
        digits = "".join(c for c in text if c.isdigit())
        value = digits[-fmt.count:] if fmt.count > 0 else digits
    if fmt.count > 0 and (len(value) != fmt.count or not value.isdigit()):
        return fmt, None, f"could not read {fmt.count} digits"
    return fmt, value, None


def _apply_pattern(value: str, scan_pattern: str | None) -> str:
    if not scan_pattern:
        return value
    compiled = re.compile(scan_pattern)
    match = compiled.search(value)
    if not match:
        log.warning("Scan %r did not match scan_pattern; using raw value", value)
        return value
    if "model" in (compiled.groupindex or {}):
        return match.group("model")
    if match.groups():
        return match.group(1)
    return match.group(0)


def _take_digits(value: str, n: int, *, take_first: bool, digits_only: bool) -> str | None:
    """Return the chosen digit substring as a STRING (leading zeros preserved)."""

    if digits_only:
        value = "".join(ch for ch in value if ch.isdigit())
    if n and n > 0:
        value = value[:n] if take_first else value[-n:]
    return value or None


def extract_mo_digits(value: str, scanner_cfg: ScannerConfig, compare_cfg: CompareConfig) -> str | None:
    """Return the last-N digits of a scanned MO barcode (leading zeros kept).

    Returns ``None`` when no digits can be parsed from the scan.
    """

    value = _apply_pattern(value.strip("\r\n").strip(), scanner_cfg.scan_pattern)
    return _take_digits(value, compare_cfg.mo_last_digits, take_first=False, digits_only=compare_cfg.digits_only)


def extract_last_digits(value: str, scanner_cfg: ScannerConfig, n: int, digits_only: bool = True) -> str | None:
    """Return the last-N digits of a scan (e.g. the Assembled Battery MO), zeros kept."""

    value = _apply_pattern(value.strip("\r\n").strip(), scanner_cfg.scan_pattern)
    return _take_digits(value, n, take_first=False, digits_only=digits_only)


def extract_battery_digits(value: str, scanner_cfg: ScannerConfig, n: int, digits_only: bool = True) -> str | None:
    """Return the first-N digits of a scanned battery label (leading zeros kept)."""

    value = _apply_pattern(value.strip("\r\n").strip(), scanner_cfg.scan_pattern)
    return _take_digits(value, n, take_first=True, digits_only=digits_only)


def recipe_to_digits(recipe: int, n: int) -> str:
    """Format a recipe DINT as the last-N digits, zero-padded, for string comparison.

    A DINT cannot itself store leading zeros, so it is padded to width ``n`` to
    line up with the (zero-preserving) scanned digits.
    """

    text = str(int(recipe))
    if n and n > 0:
        return text.zfill(n)[-n:]
    return text


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


class SerialScanner(BarcodeScanner):
    """Reads a scanner on a virtual COM port (e.g. Zebra USB-CDC), headless mode."""

    def __init__(self, cfg: ScannerConfig) -> None:
        super().__init__(cfg)
        try:
            import serial
        except ImportError as exc:  # pragma: no cover
            raise ScannerError("pyserial is required for a serial scanner: pip install pyserial") from exc
        try:
            self._serial = serial.Serial(cfg.port, cfg.baudrate, timeout=None)
        except Exception as exc:  # pragma: no cover - depends on hardware
            raise ScannerError(f"Could not open serial port {cfg.port!r}: {exc}") from exc

    def read_raw(self, prompt: str | None = None) -> str | None:
        line = self._serial.readline()
        if not line:
            return None
        return line.decode("ascii", errors="ignore")

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

    def read_raw(self, prompt: str | None = None) -> str | None:
        try:
            return next(self._it)
        except StopIteration:
            return None


def split_scans(buffer: str) -> tuple[list[str], str]:
    """Split a serial buffer into complete barcodes on CR/LF.

    Returns ``(complete_barcodes, remainder)`` where ``remainder`` is any partial
    barcode not yet terminated.
    """

    parts = re.split(r"[\r\n]+", buffer)
    remainder = parts.pop()
    return [p.strip() for p in parts if p.strip()], remainder


class SerialScanSource:
    """Background reader for a virtual-COM-port scanner, feeding the GUI.

    A daemon thread reads the port continuously and splits complete barcodes on
    CR/LF into a thread-safe queue.  The scan dialog flushes then polls it.

    The reader is **self-healing**: if the scanner is unplugged the read fails,
    the stale port handle is dropped, and the thread keeps trying to reopen the
    configured port (~1 s cadence).  Plugging the scanner back into the same
    port restores scanning with no operator action.  ``connected`` reflects the
    live link state so the GUI can show a status indicator.
    """

    RETRY_INTERVAL = 1.0     # seconds between reopen attempts while disconnected

    def __init__(self, port: str, baudrate: int) -> None:
        import queue
        import threading

        try:
            import serial
        except ImportError as exc:  # pragma: no cover
            raise ScannerError("pyserial is required for a serial scanner: pip install pyserial") from exc

        self._serial_mod = serial
        self.port = port
        self.baudrate = baudrate
        self._serial = None
        self._connected = False
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._stop = threading.Event()
        # Best-effort first open so a scanner that is already plugged in reports
        # "online" immediately instead of flickering through the offline state.
        self._open()
        self._thread = threading.Thread(target=self._loop, name="SerialScan", daemon=True)
        self._thread.start()

    @property
    def connected(self) -> bool:
        return self._connected

    def _open(self) -> bool:
        try:
            self._serial = self._serial_mod.Serial(self.port, self.baudrate, timeout=0.2)
            self._connected = True
            return True
        except Exception:  # pragma: no cover - depends on hardware
            self._serial = None
            self._connected = False
            return False

    def _close_serial(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:  # pragma: no cover
                pass
            self._serial = None

    def _loop(self) -> None:
        buffer = ""
        while not self._stop.is_set():
            if self._serial is None:
                if not self._open():
                    if self._stop.wait(self.RETRY_INTERVAL):   # still gone; retry
                        break
                    continue
                buffer = ""
            try:
                data = self._serial.read(128)
            except Exception:   # unplugged (or transient) — drop handle, reconnect
                self._connected = False
                self._close_serial()
                buffer = ""
                if self._stop.wait(0.5):
                    break
                continue
            if not data:
                continue
            buffer += data.decode("ascii", errors="ignore")
            scans, buffer = split_scans(buffer)   # keep any partial trailing barcode
            for scan in scans:
                self._queue.put(scan)

    def flush(self) -> None:
        import queue

        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def poll(self) -> str | None:
        import queue

        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._close_serial()


def build_scanner(cfg: ScannerConfig) -> BarcodeScanner:
    if cfg.type == "serial":
        return SerialScanner(cfg)
    if cfg.type in {"keyboard", "stdin"}:
        return KeyboardWedgeScanner(cfg)
    raise ScannerError(f"Unknown scanner type: {cfg.type!r}")
