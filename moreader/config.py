"""Configuration loading, validation, and saving for moreader.

The line has three read-only *encapsulator* PLCs and one *master* PLC (COS):

* Each encapsulator exposes the recipe number it is set to run as a DINT
  (default tag ``recipe[0].Name``).  moreader only reads it.
* The master (COS) has a ``MO_Verified`` BOOL.  moreader SETS it true only when a
  scanned manufacturing order matches every encapsulator's recipe, and clears it
  at a shift change or when the operator presses Manual Lockout.  moreader also
  writes an incrementing *heartbeat* DINT so the master knows the app is alive.

Configuration lives in a YAML file (see ``config.example.yaml``) and is edited
through the GUI's password-protected Configuration screen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
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


# (attribute, GUI label, default name, default description)
ENCAP_RECIPE = ("recipe_tag", "Recipe (DINT)", "recipe[0].Name",
                "DINT recipe number this encapsulator is set to run")
MASTER_TAGS: list[tuple[str, str, str, str]] = [
    ("mo_verified_tag", "MO Verified", "MO_Verified",
     "BOOL the program SETS true when the scan is verified (allows COS to run)"),
    ("heartbeat_tag", "Heartbeat", "Heartbeat",
     "DINT the program increments so the master PLC knows the app is alive"),
]
MASTER_TAG_ATTRS = [t[0] for t in MASTER_TAGS]


@dataclass
class SecurityConfig:
    """Configuration-screen protection."""

    password: str = DEFAULT_PASSWORD


@dataclass
class EncapsulatorConfig:
    """One read-only encapsulator PLC."""

    name: str = "Encapsulator"
    ip_address: str = "192.168.1.10"
    slot: int = 0
    recipe_tag: TagSpec = field(default_factory=lambda: TagSpec(ENCAP_RECIPE[2], ENCAP_RECIPE[3]))

    def __post_init__(self) -> None:
        try:
            self.slot = int(self.slot)
        except (TypeError, ValueError):
            raise ConfigError(f"encapsulator '{self.name}': slot must be an integer, got {self.slot!r}")


@dataclass
class MasterConfig:
    """The master PLC (COS) that runs the product once the MO is verified."""

    name: str = "COS"
    ip_address: str = "192.168.1.10"
    slot: int = 0
    mo_verified_tag: TagSpec = field(default_factory=lambda: TagSpec(MASTER_TAGS[0][2], MASTER_TAGS[0][3]))
    heartbeat_tag: TagSpec = field(default_factory=lambda: TagSpec(MASTER_TAGS[1][2], MASTER_TAGS[1][3]))

    def __post_init__(self) -> None:
        try:
            self.slot = int(self.slot)
        except (TypeError, ValueError):
            raise ConfigError(f"master '{self.name}': slot must be an integer, got {self.slot!r}")

    def tag(self, attr: str) -> TagSpec:
        return getattr(self, attr)


def _default_encapsulators() -> list[EncapsulatorConfig]:
    return [
        EncapsulatorConfig(name="Encapsulator 1", ip_address="192.168.1.11"),
        EncapsulatorConfig(name="Encapsulator 2", ip_address="192.168.1.12"),
        EncapsulatorConfig(name="Encapsulator 3", ip_address="192.168.1.13"),
    ]


@dataclass
class PLCConfig:
    """Driver selection, the encapsulator list, and the master PLC."""

    driver: str = "logix"          # "logix" (pylogix) or "simulated"
    encapsulators: list[EncapsulatorConfig] = field(default_factory=_default_encapsulators)
    master: MasterConfig = field(default_factory=MasterConfig)
    heartbeat_interval: float = 1.0  # seconds between heartbeat writes to the master

    def __post_init__(self) -> None:
        self.driver = str(self.driver).lower()
        if self.driver not in {"logix", "simulated"}:
            raise ConfigError(f"plc.driver must be 'logix' or 'simulated', got {self.driver!r}")
        if not self.encapsulators:
            raise ConfigError("plc.encapsulators must contain at least one encapsulator")
        try:
            self.heartbeat_interval = float(self.heartbeat_interval)
        except (TypeError, ValueError):
            raise ConfigError(f"plc.heartbeat_interval must be a number, got {self.heartbeat_interval!r}")


@dataclass
class ScannerConfig:
    """How the USB barcode scanner presents data."""

    type: str = "keyboard"
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

    mo_last_digits: int = 4
    digits_only: bool = True

    def __post_init__(self) -> None:
        try:
            self.mo_last_digits = int(self.mo_last_digits)
        except (TypeError, ValueError):
            raise ConfigError(f"compare.mo_last_digits must be an integer, got {self.mo_last_digits!r}")


@dataclass
class ShiftConfig:
    """When a shift change forces a re-scan (clears MO_Verified)."""

    start_times: list[str] = field(default_factory=lambda: ["06:00", "14:00", "22:00"])
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


def _build(cls, data: dict[str, Any]):
    """Construct a dataclass from a dict, ignoring keys it doesn't define.

    This keeps moreader forgiving of stale/extra keys in a config file written by
    an older version, instead of crashing on an unexpected keyword argument.
    """

    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


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


def _encapsulator_from_dict(data: dict[str, Any], index: int) -> EncapsulatorConfig:
    defaults = EncapsulatorConfig()
    tags = data.get("tags", {}) or {}
    if not isinstance(tags, dict):
        raise ConfigError("encapsulator.tags must be a mapping")
    # Accept the recipe tag either nested under "tags" or as a top-level key.
    source = tags if "recipe_tag" in tags else data
    return EncapsulatorConfig(
        name=data.get("name", f"Encapsulator {index + 1}"),
        ip_address=data.get("ip_address", defaults.ip_address),
        slot=data.get("slot", defaults.slot),
        recipe_tag=_tag(source, "recipe_tag", defaults.recipe_tag),
    )


def _master_from_dict(data: dict[str, Any]) -> MasterConfig:
    defaults = MasterConfig()
    tags = data.get("tags", {}) or {}
    if not isinstance(tags, dict):
        raise ConfigError("master.tags must be a mapping")
    source = tags if any(a in tags for a in MASTER_TAG_ATTRS) else data
    return MasterConfig(
        name=data.get("name", defaults.name),
        ip_address=data.get("ip_address", defaults.ip_address),
        slot=data.get("slot", defaults.slot),
        mo_verified_tag=_tag(source, "mo_verified_tag", defaults.mo_verified_tag),
        heartbeat_tag=_tag(source, "heartbeat_tag", defaults.heartbeat_tag),
    )


def _plc_from_dict(data: dict[str, Any]) -> PLCConfig:
    raw = data.get("encapsulators")
    if raw is None:
        encapsulators = _default_encapsulators()
    else:
        if not isinstance(raw, list):
            raise ConfigError("plc.encapsulators must be a list")
        encapsulators = [_encapsulator_from_dict(e or {}, i) for i, e in enumerate(raw)]
    master = _master_from_dict(data.get("master", {}) or {})
    return PLCConfig(
        driver=data.get("driver", "logix"),
        encapsulators=encapsulators,
        master=master,
        heartbeat_interval=data.get("heartbeat_interval", 1.0),
    )


def from_dict(data: dict[str, Any]) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("Top-level configuration must be a mapping")
    return Config(
        security=_build(SecurityConfig, _section(data, "security")),
        plc=_plc_from_dict(_section(data, "plc")),
        scanner=_build(ScannerConfig, _section(data, "scanner")),
        compare=_build(CompareConfig, _section(data, "compare")),
        shift=_build(ShiftConfig, _section(data, "shift")),
    )


def _encapsulator_to_dict(e: EncapsulatorConfig) -> dict[str, Any]:
    return {
        "name": e.name,
        "ip_address": e.ip_address,
        "slot": e.slot,
        "tags": {"recipe_tag": {"name": e.recipe_tag.name, "description": e.recipe_tag.description}},
    }


def _master_to_dict(m: MasterConfig) -> dict[str, Any]:
    return {
        "name": m.name,
        "ip_address": m.ip_address,
        "slot": m.slot,
        "tags": {
            attr: {"name": m.tag(attr).name, "description": m.tag(attr).description}
            for attr in MASTER_TAG_ATTRS
        },
    }


def to_dict(config: Config) -> dict[str, Any]:
    return {
        "security": {"password": config.security.password},
        "plc": {
            "driver": config.plc.driver,
            "heartbeat_interval": config.plc.heartbeat_interval,
            "encapsulators": [_encapsulator_to_dict(e) for e in config.plc.encapsulators],
            "master": _master_to_dict(config.plc.master),
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
