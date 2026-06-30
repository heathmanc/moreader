"""Command-line entry point for moreader.

By default this launches the PySide6 GUI.  ``--headless`` runs a console loop
(useful for a terminal deployment or for testing without a display).
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Config, ConfigError, load_or_default
from .controller import MachineMonitor, Notifier
from .plc import PLCError, build_machine
from .scanner import ScannerError, build_scanner, extract_mo_number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moreader",
        description=(
            "Verify a scanned manufacturing-order barcode against the model "
            "number (DINT) each of three Allen Bradley PLCs is set to run, "
            "gating each machine on a shift-by-shift basis."
        ),
    )
    parser.add_argument("-c", "--config", help="Path to YAML config file (defaults to ./config.yaml).")
    parser.add_argument("--headless", action="store_true", help="Run the console loop instead of the GUI.")
    parser.add_argument("--simulate", action="store_true", help="Use simulated PLCs (for demos/tests).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    return parser


def _run_headless(config: Config, simulate: bool) -> int:
    driver = "simulated" if (simulate or config.plc.driver == "simulated") else config.plc.driver
    sim_models = [1001, 1002, 1003]
    monitors = []
    for i, machine_cfg in enumerate(config.plc.machines):
        link = build_machine(machine_cfg, driver, sim_model=sim_models[i % len(sim_models)])
        monitors.append(MachineMonitor(link))

    notifier = Notifier()
    for monitor in monitors:
        try:
            monitor.connect()
        except PLCError as exc:
            print(f"{monitor.name}: connection failed — {exc}", file=sys.stderr)

    connected = [m for m in monitors if m.connected]
    if not connected:
        print("No PLCs connected; exiting.", file=sys.stderr)
        return 1

    if config.shift.lock_on_startup:
        notifier.shift_change("startup")
        for monitor in connected:
            monitor.lock_out()

    try:
        scanner = build_scanner(config.scanner)
    except ScannerError as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 1

    try:
        while True:
            raw = scanner.read_raw("Scan MO > ")
            if raw is None:
                break
            if not raw.strip():
                continue
            number = extract_mo_number(raw, config.scanner, config.compare)
            results = [m.verify(number) for m in monitors if m.connected]
            notifier.scan(number, results)
    except KeyboardInterrupt:
        print("\nInterrupted; locking out all machines.")
        for monitor in monitors:
            if monitor.connected:
                try:
                    monitor.lock_out()
                except PLCError:
                    pass
    finally:
        for monitor in monitors:
            monitor.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config, config_path = load_or_default(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.headless:
        return _run_headless(config, args.simulate)

    try:
        from .gui_qt import launch
    except Exception as exc:  # PySide6 missing, no display, etc.
        print(f"Could not start the GUI ({exc}). Try --headless.", file=sys.stderr)
        return 1
    launch(config, config_path, simulate=args.simulate)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
