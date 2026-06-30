"""Command-line entry point for moreader.

By default this launches the PySide6 GUI.  ``--headless`` runs a console loop.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Config, ConfigError, load_or_default
from .controller import EncapsulatorMonitor, MasterMonitor, Notifier
from .plc import PLCError, build_encapsulator, build_master
from .scanner import ScannerError, build_scanner, extract_mo_number


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moreader",
        description=(
            "Verify a scanned manufacturing-order barcode against the recipe "
            "DINT of three encapsulator PLCs, and set the COS master's "
            "MO_Verified bit when they all match."
        ),
    )
    parser.add_argument("-c", "--config", help="Path to YAML config file (defaults to ./config.yaml).")
    parser.add_argument("--headless", action="store_true", help="Run the console loop instead of the GUI.")
    parser.add_argument("--simulate", action="store_true", help="Use simulated PLCs (for demos/tests).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    return parser


def _run_headless(config: Config, simulate: bool) -> int:
    driver = "simulated" if (simulate or config.plc.driver == "simulated") else config.plc.driver
    sim_recipes = [1001, 1001, 1001]
    encs = [
        EncapsulatorMonitor(build_encapsulator(c, driver, sim_recipe=sim_recipes[i % len(sim_recipes)]))
        for i, c in enumerate(config.plc.encapsulators)
    ]
    master = MasterMonitor(build_master(config.plc.master, driver))
    notifier = Notifier()

    for enc in encs:
        try:
            enc.connect()
        except PLCError as exc:
            print(f"{enc.name}: connection failed — {exc}", file=sys.stderr)
    try:
        master.connect()
    except PLCError as exc:
        print(f"{master.name}: connection failed — {exc}", file=sys.stderr)

    if not master.connected:
        print("Master PLC not connected; exiting.", file=sys.stderr)
        return 1
    if config.shift.lock_on_startup:
        notifier.shift_change("startup")
        master.set_verified(False)

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
            connected = [e for e in encs if e.connected]
            all_matched = len(connected) == len(encs)
            for enc in connected:
                all_matched = enc.evaluate(number) and all_matched
            master.set_verified(all_matched)
            notifier.scan(number, [e.result() for e in encs], all_matched)
    except KeyboardInterrupt:
        print("\nInterrupted; clearing MO_Verified.")
        if master.connected:
            try:
                master.set_verified(False)
            except PLCError:
                pass
    finally:
        for enc in encs:
            enc.close()
        master.close()
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
