"""Configuration loading, validation, and saving for moreader.

The system watches three Allen Bradley PLCs.  Each PLC exposes the model number
it is currently set to run as a DINT tag (default ``recipe[0].Name``).  When the
operator scans a manufacturing order, the last few digits of the barcode are
compared against each PLC's DINT; a match grants that PLC's run permit.

Configuration lives in a YAML file (see ``config.example.yaml``) and is edited
through the GUI's password-protected Configuration screen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:  # PyYAML is the only hard dependency for config loading.
    import yaml
except ImportError:  # pragma: no cover
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


# Per-machine tags: (attribute, GUI label, default name, default description).
TAG_FIELDS: list[tuple[str, str, str, str]] = [
    ("model_tag", "Model (DINT)", "recipe[0].Name",
     "DINT the PLC is currently set to run (compared to the scanned MO)"),
    ("run_permit_tag", "Run Permit", "ScanRunPermit",
     "BOOL the program SETS to allow this PLC to run the product"),
    ("alarm_tag", "Mismatch Alarm", "ScanMismatchAlarm",
     "BOOL the program SETS when the scan does not match this PLC"),
    ("shift_request_tag", "Shift Change Request", "ShiftChangeRequest",
     "Optional BOOL the PLC/HMI raises to force a re-scan"),
    ("last_scan_tag", "Last Scan Echo", "LastScanValue",
     "Optional DINT the program writes the last scanned model number to"),
]

TAG_ATTRS = [f[0] for f in TAG_FIELDS]


@dataclass
class SecurityConfig:
    """Configuration-screen protection."""

    password: str = DEFAULT_PASSWORD


@dataclass
class MachineConfig:
    """One PLC / machine to watch."""

    name: str = "Machine"
    ip_address: str = "192.168.1.10"
    slot: int = 0
    model_tag: TagSpec = field(default_factory=lambda: TagSpec(TAG_FIELDS[0][2], TAG_FIELDS[0][3]))
    run_permit_tag: TagSpec = field(default_factory=lambda: TagSpec(TAG_FIELDS[1][2], TAG_FIELDS[1][3]))
    alarm_tag: TagSpec = field(default_factory=lambda: TagSpec(TAG_FIELDS[2][2], TAG_FIELDS[2][3]))
    shift_request_tag: TagSpec = field(default_factory=lambda: TagSpec(TAG_FIELDS[3][2], TAG_FIELDS[3][3]))
    last_scan_tag: TagSpec = field(default_factory=lambda: TagSpec(TAG_FIELDS[4][2], TAG_FIELDS[4][3]))

    def __post_init__(self) -> None:
        try:
            self.slot = int(self.slot)
        except (TypeError, ValueError):
            raise ConfigError(f"machine '{self.name}': slot must be an integer, got {self.slot!r}")

    def tag(self, attr: str) -> TagSpec:
        return getattr(self, attr)


def _default_machines() -> list[MachineConfig]:
    return [
        MachineConfig(name="Machine 1", ip_address="192.168.1.11"),
        MachineConfig(name="Machine 2", ip_address="192.168.1.12"),
        MachineConfig(name="Machine 3", ip_address="192.168.1.13"),
    ]


@dataclass
class PLCConfig:
    """Driver selection plus the list of machines to watch."""

    driver: str = "logix"          # "logix" (pylogix) or "simulated"
    machines: list[MachineConfig] = field(default_factory=_default_machines)

    def __post_init__(self) -> None:
        self.driver = str(self.driver).lower()
        if self.driver not in {"logix", "simulated"}:
            raise ConfigError(f"plc.driver must be 'logix' or 'simulated', got {self.driver!r}")
        if not self.machines:
            raise ConfigError("plc.machines must contain at least one machine")


@dataclass
class ScannerConfig:
    """How the USB barcode scanner presents data."""

    type: str = "keyboard"         # "keyboard" (HID into the scan popup) or "stdin"
    # Optional regex with a named group ``model`` to pull the number out of a
    # richer MO barcode before the last-N-digits rule is applied.
    scan_pattern: str | None = None

    def __post_init__(self) -> None:
        self.type = str(self.type).lower()
        if self.type not in {"keyboard", "stdin", "serial"}:
            raise ConfigError(f"scanner.type must be 'keyboard', 'stdin', or 'serial', got {self.type!r}")
        if self.scan_pattern in ("", "null", "None"):
            self.scan_pattern = None


@dataclass
class CompareConfig:
    """How a scanned MO barcode is reduced to a number for comparison."""

    # Compare only the last N digits of the scanned barcode (0 = whole number).
    mo_last_digits: int = 4
    # Strip every non-digit character before taking the last N digits.
    digits_only: bool = True

    def __post_init__(self) -> None:
        try:
            self.mo_last_digits = int(self.mo_last_digits)
        except (TypeError, ValueError):
            raise ConfigError(f"compare.mo_last_digits must be an integer, got {self.mo_last_digits!r}")


@dataclass
class ShiftConfig:
    """When a shift change forces a re-scan before the PLCs can run."""

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
    if isinstance(raw, str):
        return TagSpec(raw, default.description)
    if isinstance(raw, dict):
        return TagSpec(
            name=str(raw.get("name", default.name)),
            description=str(raw.get("description", default.description)),
        )
    raise ConfigError(f"tag '{key}' must be a string or a mapping")


def _machine_from_dict(data: dict[str, Any], index: int) -> MachineConfig:
    defaults = MachineConfig()
    tags = data.get("tags", {})
    if tags is None:
        tags = {}
    if not isinstance(tags, dict):
        raise ConfigError("machine.tags must be a mapping")
    return MachineConfig(
        name=data.get("name", f"Machine {index + 1}"),
        ip_address=data.get("ip_address", defaults.ip_address),
        slot=data.get("slot", defaults.slot),
        model_tag=_tag(tags, "model_tag", defaults.model_tag),
        run_permit_tag=_tag(tags, "run_permit_tag", defaults.run_permit_tag),
        alarm_tag=_tag(tags, "alarm_tag", defaults.alarm_tag),
        shift_request_tag=_tag(tags, "shift_request_tag", defaults.shift_request_tag),
        last_scan_tag=_tag(tags, "last_scan_tag", defaults.last_scan_tag),
    )


def _plc_from_dict(data: dict[str, Any]) -> PLCConfig:
    raw_machines = data.get("machines")
    if raw_machines is None:
        machines = _default_machines()
    else:
        if not isinstance(raw_machines, list):
            raise ConfigError("plc.machines must be a list")
        machines = [_machine_from_dict(m or {}, i) for i, m in enumerate(raw_machines)]
    return PLCConfig(driver=data.get("driver", "logix"), machines=machines)


def from_dict(data: dict[str, Any]) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("Top-level configuration must be a mapping")
    return Config(
        security=SecurityConfig(**_section(data, "security")),
        plc=_plc_from_dict(_section(data, "plc")),
        scanner=ScannerConfig(**_section(data, "scanner")),
        compare=CompareConfig(**_section(data, "compare")),
        shift=ShiftConfig(**_section(data, "shift")),
    )


def _machine_to_dict(machine: MachineConfig) -> dict[str, Any]:
    return {
        "name": machine.name,
        "ip_address": machine.ip_address,
        "slot": machine.slot,
        "tags": {
            attr: {"name": machine.tag(attr).name, "description": machine.tag(attr).description}
            for attr in TAG_ATTRS
        },
    }


def to_dict(config: Config) -> dict[str, Any]:
    return {
        "security": {"password": config.security.password},
        "plc": {
            "driver": config.plc.driver,
            "machines": [_machine_to_dict(m) for m in config.plc.machines],
        },
        "scanner": asdict(config.scanner),
        "compare": asdict(config.compare),
        "shift": asdict(config.shift),
    }


# --- file I/O ----------------------------------------------------------------

def load_config(path: str | Path) -> Config:
    if yaml is None:  # pragma: no cover
        raise ConfigError("PyYAML is required to load a config file: pip install pyyaml")
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:  # pragma: no cover
        raise ConfigError(f"Could not parse {path}: {exc}") from exc
    return from_dict(data)


def save_config(config: Config, path: str | Path) -> None:
    if yaml is None:  # pragma: no cover
        raise ConfigError("PyYAML is required to save a config file: pip install pyyaml")
    path = Path(path)
    text = yaml.safe_dump(to_dict(config), sort_keys=False, default_flow_style=False)
    path.write_text(text)


def load_or_default(path: str | Path | None) -> tuple[Config, Path]:
    resolved = Path(path) if path else Path("config.yaml")
    if resolved.exists():
        return load_config(resolved), resolved
    return Config(), resolved
