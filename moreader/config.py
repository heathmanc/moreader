"""Configuration loading, validation, and saving for moreader.

Configuration lives in a YAML file (see ``config.example.yaml``) and is edited
through the GUI's password-protected Configuration screen.  Every section maps
to a small dataclass so the rest of the program works with typed objects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:  # PyYAML is the only hard dependency for config loading.
    import yaml
except ImportError:  # pragma: no cover - exercised only without PyYAML.
    yaml = None


# Default configuration-screen password (stored in the YAML, per requirement).
DEFAULT_PASSWORD = "2134chAP!@"


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class TagSpec:
    """A PLC tag: its address/name plus a human description for the GUI."""

    name: str = ""
    description: str = ""


# Ordered list of the tags moreader uses: (attribute, GUI label, default desc).
TAG_FIELDS: list[tuple[str, str, str]] = [
    ("expected_model", "Expected Model", "Model/part number the PLC is currently set to run (STRING)"),
    ("run_permit", "Run Permit", "BOOL the program SETS to allow the PLC to run the product"),
    ("alarm", "Mismatch Alarm", "BOOL the program SETS when a scan does not match (drives HMI alarm)"),
    ("shift_request", "Shift Change Request", "Optional BOOL the PLC/HMI raises to force a re-scan"),
    ("last_scan", "Last Scan Echo", "Optional STRING the program writes the last scanned value to"),
]


@dataclass
class SecurityConfig:
    """Configuration-screen protection."""

    password: str = DEFAULT_PASSWORD


@dataclass
class PLCConfig:
    """How to reach the Allen Bradley Logix PLC (pylogix) and which tags to use."""

    driver: str = "logix"          # "logix" (pylogix) or "simulated"
    ip_address: str = "192.168.1.10"
    slot: int = 0                  # CPU slot (ControlLogix chassis; 0 for CompactLogix)

    expected_model: TagSpec = field(
        default_factory=lambda: TagSpec("Program:MainProgram.ModelNumber", TAG_FIELDS[0][2])
    )
    run_permit: TagSpec = field(
        default_factory=lambda: TagSpec("Program:MainProgram.ScanRunPermit", TAG_FIELDS[1][2])
    )
    alarm: TagSpec = field(
        default_factory=lambda: TagSpec("Program:MainProgram.ScanMismatchAlarm", TAG_FIELDS[2][2])
    )
    shift_request: TagSpec = field(
        default_factory=lambda: TagSpec("Program:MainProgram.ShiftChangeRequest", TAG_FIELDS[3][2])
    )
    last_scan: TagSpec = field(
        default_factory=lambda: TagSpec("Program:MainProgram.LastScan", TAG_FIELDS[4][2])
    )

    def __post_init__(self) -> None:
        self.driver = str(self.driver).lower()
        if self.driver not in {"logix", "simulated"}:
            raise ConfigError(
                f"plc.driver must be 'logix' or 'simulated', got {self.driver!r}"
            )
        try:
            self.slot = int(self.slot)
        except (TypeError, ValueError):
            raise ConfigError(f"plc.slot must be an integer, got {self.slot!r}")

    def tag(self, attr: str) -> TagSpec:
        return getattr(self, attr)


@dataclass
class ScannerConfig:
    """How the USB barcode scanner presents data."""

    type: str = "keyboard"         # "keyboard" (HID into the GUI), "serial", "stdin"
    port: str = "/dev/ttyACM0"     # serial only
    baudrate: int = 9600           # serial only
    # Optional regex with a named group ``model`` to pull the model out of a
    # richer manufacturing-order barcode, e.g. "MO123|MODEL=ABC-100".
    scan_pattern: str | None = None

    def __post_init__(self) -> None:
        self.type = str(self.type).lower()
        if self.type not in {"keyboard", "stdin", "serial"}:
            raise ConfigError(
                f"scanner.type must be 'keyboard', 'stdin', or 'serial', got {self.type!r}"
            )
        if self.scan_pattern in ("", "null", "None"):
            self.scan_pattern = None


@dataclass
class CompareConfig:
    """How scanned values are normalised before comparison."""

    strip: bool = True
    ignore_case: bool = True
    collapse_internal_space: bool = False


@dataclass
class ShiftConfig:
    """When a shift change forces a re-scan before the PLC can run."""

    start_times: list[str] = field(default_factory=lambda: ["06:00", "14:00", "22:00"])
    watch_plc_request: bool = True
    lock_on_startup: bool = True
    poll_interval: float = 2.0


@dataclass
class Config:
    security: SecurityConfig = field(default_factory=SecurityConfig)
    plc: PLCConfig = field(default_factory=PLCConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    compare: CompareConfig = field(default_factory=CompareConfig)
    shift: ShiftConfig = field(default_factory=ShiftConfig)


# --- parsing -----------------------------------------------------------------

def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Config section '{key}' must be a mapping, got {type(value).__name__}")
    return value


def _tag(data: dict[str, Any], key: str, default: TagSpec) -> TagSpec:
    raw = data.get(key)
    if raw is None:
        return TagSpec(default.name, default.description)
    if isinstance(raw, str):  # allow a bare string for the tag name
        return TagSpec(raw, default.description)
    if isinstance(raw, dict):
        return TagSpec(
            name=str(raw.get("name", default.name)),
            description=str(raw.get("description", default.description)),
        )
    raise ConfigError(f"PLC tag '{key}' must be a string or a mapping")


def _plc_from_dict(data: dict[str, Any]) -> PLCConfig:
    tags = data.get("tags", {})
    if tags is None:
        tags = {}
    if not isinstance(tags, dict):
        raise ConfigError("plc.tags must be a mapping")
    defaults = PLCConfig()
    return PLCConfig(
        driver=data.get("driver", defaults.driver),
        ip_address=data.get("ip_address", defaults.ip_address),
        slot=data.get("slot", defaults.slot),
        expected_model=_tag(tags, "expected_model", defaults.expected_model),
        run_permit=_tag(tags, "run_permit", defaults.run_permit),
        alarm=_tag(tags, "alarm", defaults.alarm),
        shift_request=_tag(tags, "shift_request", defaults.shift_request),
        last_scan=_tag(tags, "last_scan", defaults.last_scan),
    )


def from_dict(data: dict[str, Any]) -> Config:
    """Build a :class:`Config` from a plain dictionary."""

    if not isinstance(data, dict):
        raise ConfigError("Top-level configuration must be a mapping")
    return Config(
        security=SecurityConfig(**_section(data, "security")),
        plc=_plc_from_dict(_section(data, "plc")),
        scanner=ScannerConfig(**_section(data, "scanner")),
        compare=CompareConfig(**_section(data, "compare")),
        shift=ShiftConfig(**_section(data, "shift")),
    )


def to_dict(config: Config) -> dict[str, Any]:
    """Serialise a :class:`Config` back to a YAML-friendly dictionary."""

    return {
        "security": {"password": config.security.password},
        "plc": {
            "driver": config.plc.driver,
            "ip_address": config.plc.ip_address,
            "slot": config.plc.slot,
            "tags": {
                attr: {
                    "name": config.plc.tag(attr).name,
                    "description": config.plc.tag(attr).description,
                }
                for attr, _label, _desc in TAG_FIELDS
            },
        },
        "scanner": asdict(config.scanner),
        "compare": asdict(config.compare),
        "shift": asdict(config.shift),
    }


# --- file I/O ----------------------------------------------------------------

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


def save_config(config: Config, path: str | Path) -> None:
    """Write configuration back to a YAML file (used by the GUI's Save button)."""

    if yaml is None:  # pragma: no cover
        raise ConfigError("PyYAML is required to save a config file: pip install pyyaml")
    path = Path(path)
    text = yaml.safe_dump(to_dict(config), sort_keys=False, default_flow_style=False)
    path.write_text(text)


def load_or_default(path: str | Path | None) -> tuple[Config, Path]:
    """Load ``path`` if it exists, otherwise return defaults.

    Returns the config and the resolved path it should be saved to.
    """

    resolved = Path(path) if path else Path("config.yaml")
    if resolved.exists():
        return load_config(resolved), resolved
    return Config(), resolved
