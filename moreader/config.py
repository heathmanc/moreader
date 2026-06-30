"""Configuration loading and validation for moreader.

Configuration is read from a YAML file (see ``config.example.yaml``).  Every
section maps to a small dataclass so the rest of the program works with typed
objects instead of raw dictionaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # PyYAML is the only hard dependency for config loading.
    import yaml
except ImportError:  # pragma: no cover - exercised only without PyYAML.
    yaml = None


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class PLCConfig:
    """How to reach the Allen Bradley PLC and which tags to use."""

    driver: str = "logix"          # "logix", "slc", or "simulated"
    ip_address: str = "192.168.1.10"
    slot: int = 0                  # CPU slot, ControlLogix chassis only

    # Tag holding the model / part number the PLC is currently set to run.
    expected_model_tag: str = "ModelNumber"
    # BOOL the program sets True when the scan matches -> PLC may run.
    run_permit_tag: str = "RunPermit"
    # BOOL the program sets True on a mismatch (drives an HMI alarm).
    alarm_tag: str = "ScanMismatchAlarm"
    # Optional BOOL the PLC/HMI raises to request a shift-change lockout.
    shift_request_tag: str = "ShiftChangeRequest"
    # Optional STRING the program writes the last scanned value to (HMI echo).
    last_scan_tag: str = "LastScan"

    def __post_init__(self) -> None:
        self.driver = self.driver.lower()
        if self.driver not in {"logix", "slc", "simulated"}:
            raise ConfigError(
                f"plc.driver must be 'logix', 'slc', or 'simulated', got {self.driver!r}"
            )


@dataclass
class ScannerConfig:
    """How the USB barcode scanner presents data."""

    type: str = "keyboard"         # "keyboard", "serial", or "stdin"
    # Serial-only settings.
    port: str = "/dev/ttyACM0"
    baudrate: int = 9600
    # Optional regex with a named group ``model`` to pull the model number out
    # of a richer manufacturing-order barcode, e.g. "MO123|MODEL=ABC-100".
    scan_pattern: str | None = None

    def __post_init__(self) -> None:
        self.type = self.type.lower()
        if self.type not in {"keyboard", "stdin", "serial"}:
            raise ConfigError(
                f"scanner.type must be 'keyboard', 'stdin', or 'serial', got {self.type!r}"
            )


@dataclass
class CompareConfig:
    """How scanned values are normalised before comparison."""

    strip: bool = True             # trim surrounding whitespace
    ignore_case: bool = True       # case-insensitive match
    collapse_internal_space: bool = False


@dataclass
class ShiftConfig:
    """When a shift change forces a re-scan before the PLC can run."""

    # Local clock times (HH:MM) at which a new shift starts.  An empty list
    # disables time-based shift detection.
    start_times: list[str] = field(default_factory=lambda: ["06:00", "14:00", "22:00"])
    # Also watch the PLC's shift_request_tag for a rising edge.
    watch_plc_request: bool = True
    # Require a scan before the very first run after the program starts.
    lock_on_startup: bool = True
    # How often (seconds) to poll the PLC for a shift-change request while
    # running.
    poll_interval: float = 2.0


@dataclass
class Config:
    plc: PLCConfig = field(default_factory=PLCConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    compare: CompareConfig = field(default_factory=CompareConfig)
    shift: ShiftConfig = field(default_factory=ShiftConfig)


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Config section '{key}' must be a mapping, got {type(value).__name__}")
    return value


def from_dict(data: dict[str, Any]) -> Config:
    """Build a :class:`Config` from a plain dictionary."""

    if not isinstance(data, dict):
        raise ConfigError("Top-level configuration must be a mapping")
    return Config(
        plc=PLCConfig(**_section(data, "plc")),
        scanner=ScannerConfig(**_section(data, "scanner")),
        compare=CompareConfig(**_section(data, "compare")),
        shift=ShiftConfig(**_section(data, "shift")),
    )


def load_config(path: str | Path) -> Config:
    """Load and validate configuration from a YAML file."""

    if yaml is None:  # pragma: no cover
        raise ConfigError("PyYAML is required to load a config file: pip install pyyaml")
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - depends on file contents
        raise ConfigError(f"Could not parse {path}: {exc}") from exc
    return from_dict(data)
