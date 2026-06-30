"""Command-line entry point for moreader.

By default this launches the GUI.  ``--headless`` runs the original console loop
(useful for a kiosk/terminal deployment or for testing without a display).
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Config, ConfigError, load_or_default
from .controller import ShiftChangeController
from .plc import PLCError, SimulatedPLC, build_plc
from .scanner import ScannerError, build_scanner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moreader",
        description=(
            "Verify a scanned manufacturing-order barcode against the model "
            "number an Allen Bradley PLC is set to run, gating the machine on a "
            "shift-by-shift basis."
        ),
    )
    parser.add_argument(
        "-c", "--config", help="Path to YAML config file (defaults to ./config.yaml)."
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the console loop instead of the GUI.",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use a simulated PLC instead of real hardware (for demos/tests).",
    )
    parser.add_argument(
        "--expected-model",
        default="ABC-100",
        help="With --simulate, the model the simulated PLC is configured to run.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    return parser


def _run_headless(config: Config, simulate: bool, expected_model: str) -> int:
    if simulate or config.plc.driver == "simulated":
        plc = SimulatedPLC(config.plc, expected_model)
    else:
        plc = build_plc(config.plc)
    try:
        scanner = build_scanner(config.scanner)
    except ScannerError as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 1
    controller = ShiftChangeController(
        plc=plc, scanner=scanner, compare_cfg=config.compare, shift_cfg=config.shift
    )
    try:
        plc.connect()
    except PLCError as exc:
        print(f"Could not connect to PLC: {exc}", file=sys.stderr)
        return 1
    try:
        controller.run_forever()
    except KeyboardInterrupt:
        print("\nInterrupted; locking out machine and exiting.")
        try:
            plc.set_run_permit(False)
        except PLCError:
            pass
    finally:
        scanner.close()
        plc.close()
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
        return _run_headless(config, args.simulate, args.expected_model)

    try:
        from .gui import launch
    except Exception as exc:  # tkinter missing, no display, etc.
        print(f"Could not start the GUI ({exc}). Try --headless.", file=sys.stderr)
        return 1
    launch(config, config_path, simulate=args.simulate, expected_model=args.expected_model)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
