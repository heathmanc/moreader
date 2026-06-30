"""Command-line entry point for moreader."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Config, ConfigError, load_config
from .controller import ShiftChangeController
from .plc import PLCError, SimulatedPLC, build_plc
from .scanner import IterableScanner, ScannerError, build_scanner


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
        "-c", "--config", help="Path to YAML config file (see config.example.yaml)."
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use a simulated PLC instead of real hardware (for demos/tests).",
    )
    parser.add_argument(
        "--expected-model",
        help="With --simulate, set the model the simulated PLC is configured to run.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    return parser


def build_app(config: Config, simulate: bool = False, expected_model: str | None = None):
    """Construct the controller from a config object."""

    if simulate or config.plc.driver == "simulated":
        plc = SimulatedPLC(config.plc, expected_model or "ABC-100")
    else:
        plc = build_plc(config.plc)
    scanner = build_scanner(config.scanner)
    controller = ShiftChangeController(
        plc=plc,
        scanner=scanner,
        compare_cfg=config.compare,
        shift_cfg=config.shift,
    )
    return plc, scanner, controller


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config) if args.config else Config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        plc, scanner, controller = build_app(config, args.simulate, args.expected_model)
    except (PLCError, ScannerError) as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 1

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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
