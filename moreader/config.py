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
    ("mo_bypassed_tag", "MO Bypassed", "MO_Bypassed",
     "BOOL the program SETS true when an operator bypasses verification (passworded; cleared at shift/lockout)"),
    ("cycle_stop_tag", "Cycle Stop Request", "System.Mode.CycleStopReq",
     "BOOL the program SETS true on a lockout/shift change to request a graceful cycle stop"),
    ("heartbeat_tag", "Heartbeat", "Heartbeat",
     "Watchdog the program pulses so the master PLC knows the app is alive (BOOL toggle or DINT count)"),
]
MASTER_TAG_ATTRS = [t[0] for t in MASTER_TAGS]
MASTER_TAG_DEFAULTS = {attr: (name, desc) for attr, _label, name, desc in MASTER_TAGS}


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
    mo_verified_tag: TagSpec = field(default_factory=lambda: TagSpec(*MASTER_TAG_DEFAULTS["mo_verified_tag"]))
    mo_bypassed_tag: TagSpec = field(default_factory=lambda: TagSpec(*MASTER_TAG_DEFAULTS["mo_bypassed_tag"]))
    cycle_stop_tag: TagSpec = field(default_factory=lambda: TagSpec(*MASTER_TAG_DEFAULTS["cycle_stop_tag"]))
    heartbeat_tag: TagSpec = field(default_factory=lambda: TagSpec(*MASTER_TAG_DEFAULTS["heartbeat_tag"]))
    # When False, moreader never writes CycleStopReq (the PLC handles it).
    cycle_stop_enabled: bool = True

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
    # "toggle"    -> pulse the tag ON/OFF (BOOL watchdog), or
    # "increment" -> count up a DINT.  Toggle is the classic on/off watchdog.
    heartbeat_mode: str = "toggle"

    def __post_init__(self) -> None:
        self.driver = str(self.driver).lower()
        if self.driver not in {"logix", "simulated"}:
            raise ConfigError(f"plc.driver must be 'logix' or 'simulated', got {self.driver!r}")
        if not self.encapsulators:
            raise ConfigError("plc.encapsulators must contain at least one encapsulator")
        self.heartbeat_mode = str(self.heartbeat_mode).lower()
        if self.heartbeat_mode not in {"toggle", "increment"}:
            raise ConfigError(f"plc.heartbeat_mode must be 'toggle' or 'increment', got {self.heartbeat_mode!r}")
        try:
            self.heartbeat_interval = float(self.heartbeat_interval)
        except (TypeError, ValueError):
            raise ConfigError(f"plc.heartbeat_interval must be a number, got {self.heartbeat_interval!r}")


@dataclass
class ScannerConfig:
    """How the USB barcode scanner presents data.

    ``keyboard`` — HID keyboard-wedge (types into the scan popup).
    ``serial``   — a virtual COM port (e.g. Zebra in USB-CDC mode); read via pyserial.
    ``stdin``    — piped input, for testing/headless.
    """

    type: str = "keyboard"
    scan_pattern: str | None = None
    # Serial-only settings.
    port: str = "COM3"
    baudrate: int = 9600

    def __post_init__(self) -> None:
        self.type = str(self.type).lower()
        if self.type not in {"keyboard", "stdin", "serial"}:
            raise ConfigError(f"scanner.type must be 'keyboard', 'serial', or 'stdin', got {self.type!r}")
        if self.scan_pattern in ("", "null", "None"):
            self.scan_pattern = None
        try:
            self.baudrate = int(self.baudrate)
        except (TypeError, ValueError):
            raise ConfigError(f"scanner.baudrate must be an integer, got {self.baudrate!r}")


@dataclass
class CompareConfig:
    """How a scanned MO barcode is reduced to a number for comparison."""

    mo_last_digits: int = 4
    digits_only: bool = True
    # Required exact length of the raw MO scan (0 = no length check).  Guards
    # against scanning the wrong barcode (e.g. the battery label) for the MO.
    mo_length: int = 9

    def __post_init__(self) -> None:
        try:
            self.mo_last_digits = int(self.mo_last_digits)
            self.mo_length = int(self.mo_length)
        except (TypeError, ValueError):
            raise ConfigError("compare.mo_last_digits and compare.mo_length must be integers")


@dataclass
class MoFormat:
    """One MO barcode format, chosen by how the scan starts.

    ``take = "last"``  -> strip non-digits and take the last ``count`` digits.
    ``take = "slice"`` -> take ``count`` characters starting at ``start`` (1-based).

    Examples:
      F2220-1301  -> prefix "F",  length 10, take "last",  count 4  -> 1301
      GL0007564-0000 -> prefix "GL", take "slice", start 6, count 4 -> 7564
      2220-1321   -> prefix "",   length 9,  take "last",  count 4  -> 1321
    """

    prefix: str = ""      # matched at the start of the scan (case-insensitive); "" = default
    length: int = 0       # required exact length of the raw scan (0 = no check)
    take: str = "last"    # "last" or "slice"
    start: int = 1        # 1-based start position (slice mode)
    count: int = 4        # how many digits to compare

    def __post_init__(self) -> None:
        self.prefix = str(self.prefix)
        self.take = str(self.take).lower()
        if self.take not in {"last", "slice"}:
            raise ConfigError(f"mo_formats.take must be 'last' or 'slice', got {self.take!r}")
        try:
            self.length = int(self.length)
            self.start = int(self.start)
            self.count = int(self.count)
        except (TypeError, ValueError):
            raise ConfigError("mo_formats length/start/count must be integers")


def _default_mo_formats() -> list[MoFormat]:
    return [
        MoFormat(prefix="F", length=10, take="last", count=4),
        MoFormat(prefix="GL", length=0, take="slice", start=6, count=4),
        MoFormat(prefix="", length=9, take="last", count=4),   # default / fallback (keep last)
    ]


@dataclass
class ShiftConfig:
    """When a shift change forces a re-scan (clears MO_Verified)."""

    start_times: list[str] = field(default_factory=lambda: ["06:00", "14:00", "22:00"])
    lock_on_startup: bool = True
    poll_interval: float = 2.0


@dataclass
class SecondaryConfig:
    """Optional Assembled-Battery cross-check.

    When enabled, after the Stuffed Element MO (the primary scan), the operator
    scans a *different* MO — the Assembled Battery MO — and the battery label.
    The battery label's first N digits must equal the Assembled Battery MO's
    last N digits.
    """

    enabled: bool = False
    # Step labels shown on the scan prompts.
    stuffed_element_label: str = "Stuffed Element MO"
    assembled_mo_label: str = "Assembled Battery MO"
    battery_label: str = "Battery Label"
    # Last N digits taken from the Assembled Battery MO scan.
    assembled_mo_last_digits: int = 4
    # Required exact length of the Assembled Battery MO scan (0 = no check).
    assembled_mo_length: int = 9
    # First N digits taken from the battery label.
    battery_first_digits: int = 4
    # Minimum length of the battery-label scan (0 = no check).
    battery_min_length: int = 10

    def __post_init__(self) -> None:
        try:
            self.assembled_mo_last_digits = int(self.assembled_mo_last_digits)
            self.assembled_mo_length = int(self.assembled_mo_length)
            self.battery_first_digits = int(self.battery_first_digits)
            self.battery_min_length = int(self.battery_min_length)
        except (TypeError, ValueError):
            raise ConfigError("secondary digit/length settings must be integers")


@dataclass
class AuditConfig:
    """Persistent audit trail of every scan and gate change."""

    enabled: bool = True
    directory: str = "logs"     # daily CSV files are written here


@dataclass
class Config:
    security: SecurityConfig = field(default_factory=SecurityConfig)
    plc: PLCConfig = field(default_factory=PLCConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    compare: CompareConfig = field(default_factory=CompareConfig)
    shift: ShiftConfig = field(default_factory=ShiftConfig)
    secondary: SecondaryConfig = field(default_factory=SecondaryConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    # MO barcode formats (Stuffed Element + Assembled Battery scans).
    mo_formats: list[MoFormat] = field(default_factory=_default_mo_formats)


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
    tag_kwargs = {attr: _tag(source, attr, defaults.tag(attr)) for attr in MASTER_TAG_ATTRS}
    return MasterConfig(
        name=data.get("name", defaults.name),
        ip_address=data.get("ip_address", defaults.ip_address),
        slot=data.get("slot", defaults.slot),
        cycle_stop_enabled=bool(data.get("cycle_stop_enabled", defaults.cycle_stop_enabled)),
        **tag_kwargs,
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
        heartbeat_mode=data.get("heartbeat_mode", "toggle"),
    )


def _mo_formats_from_dict(raw) -> list[MoFormat]:
    if raw is None:
        return _default_mo_formats()
    if not isinstance(raw, list):
        raise ConfigError("mo_formats must be a list")
    formats = [_build(MoFormat, f or {}) for f in raw]
    return formats or _default_mo_formats()


def from_dict(data: dict[str, Any]) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("Top-level configuration must be a mapping")
    return Config(
        security=_build(SecurityConfig, _section(data, "security")),
        plc=_plc_from_dict(_section(data, "plc")),
        scanner=_build(ScannerConfig, _section(data, "scanner")),
        compare=_build(CompareConfig, _section(data, "compare")),
        shift=_build(ShiftConfig, _section(data, "shift")),
        secondary=_build(SecondaryConfig, _section(data, "secondary")),
        audit=_build(AuditConfig, _section(data, "audit")),
        mo_formats=_mo_formats_from_dict(data.get("mo_formats")),
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
        "cycle_stop_enabled": m.cycle_stop_enabled,
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
            "heartbeat_mode": config.plc.heartbeat_mode,
            "encapsulators": [_encapsulator_to_dict(e) for e in config.plc.encapsulators],
            "master": _master_to_dict(config.plc.master),
        },
        "scanner": asdict(config.scanner),
        "compare": asdict(config.compare),
        "shift": asdict(config.shift),
        "secondary": asdict(config.secondary),
        "audit": asdict(config.audit),
        "mo_formats": [asdict(f) for f in config.mo_formats],
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
