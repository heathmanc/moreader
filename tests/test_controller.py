"""Tests for the scan-verification controller using the simulated PLC."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import CompareConfig, ScannerConfig, ShiftConfig
from moreader.controller import ShiftChangeController, State, normalize
from moreader.plc import SimulatedPLC
from moreader.scanner import IterableScanner


def make_controller(scans, expected="ABC-100", **shift_kwargs):
    plc = SimulatedPLC(expected_model=expected)
    plc.connect()
    scanner = IterableScanner(scans)
    shift_cfg = ShiftConfig(start_times=[], watch_plc_request=False, **shift_kwargs)
    controller = ShiftChangeController(
        plc=plc,
        scanner=scanner,
        compare_cfg=CompareConfig(),
        shift_cfg=shift_cfg,
    )
    return plc, controller


def test_matching_scan_sets_run_permit():
    plc, controller = make_controller(["ABC-100"])
    controller.run_forever(max_iterations=1)
    assert plc.run_permit is True
    assert plc.alarm is False
    assert controller.state is State.RUNNING


def test_mismatch_raises_alarm_and_blocks():
    plc, controller = make_controller(["WRONG-999"])
    controller.run_forever(max_iterations=1)
    assert plc.run_permit is False
    assert plc.alarm is True
    assert controller.state is State.ALARM


def test_rescan_after_alarm_clears_it():
    plc, controller = make_controller(["WRONG-999", "ABC-100"])
    controller.run_forever(max_iterations=2)
    assert plc.run_permit is True
    assert plc.alarm is False
    assert controller.state is State.RUNNING


def test_case_insensitive_and_whitespace_match():
    plc, controller = make_controller(["  abc-100 "])
    controller.run_forever(max_iterations=1)
    assert plc.run_permit is True


def test_locked_on_startup_blocks_until_scan():
    plc, controller = make_controller([], lock_on_startup=True)
    # No scans available: the machine must remain blocked.
    controller.run_forever(max_iterations=1)
    assert plc.run_permit is False
    assert controller.state is State.LOCKED


def test_last_scan_written_to_plc():
    plc, controller = make_controller(["ABC-100"])
    controller.run_forever(max_iterations=1)
    assert plc.last_scan == "ABC-100"


def test_normalize_respects_config():
    cfg = CompareConfig(strip=True, ignore_case=True, collapse_internal_space=True)
    assert normalize("  Foo   Bar ", cfg) == "foo bar"
    cfg2 = CompareConfig(strip=False, ignore_case=False, collapse_internal_space=False)
    assert normalize(" Foo ", cfg2) == " Foo "


def test_scan_pattern_extracts_model():
    cfg = ScannerConfig(type="stdin", scan_pattern=r"MODEL=(?P<model>[^|]+)")
    scanner = IterableScanner(["MO12345|MODEL=ABC-100|QTY=500"], cfg)
    assert scanner.read() == "ABC-100"
