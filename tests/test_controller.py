"""Tests for per-machine scan verification and the MO number parser."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import CompareConfig, MachineConfig, ScannerConfig
from moreader.controller import MachineMonitor, MachineState
from moreader.plc import SimulatedMachine
from moreader.scanner import extract_mo_number


def make_monitor(model=1001):
    link = SimulatedMachine(MachineConfig(name="M1"), model=model)
    monitor = MachineMonitor(link)
    monitor.connect()
    return link, monitor


def test_match_enables_run():
    link, monitor = make_monitor(model=1001)
    result = monitor.verify(1001)
    assert result.matched is True
    assert monitor.state is MachineState.RUNNING
    assert link.run_permit is True
    assert link.alarm is False


def test_mismatch_raises_alarm():
    link, monitor = make_monitor(model=1001)
    result = monitor.verify(2002)
    assert result.matched is False
    assert monitor.state is MachineState.ALARM
    assert link.run_permit is False
    assert link.alarm is True


def test_invalid_scan_number_blocks():
    link, monitor = make_monitor(model=1001)
    result = monitor.verify(None)
    assert result.matched is False
    assert monitor.state is MachineState.ALARM
    assert link.run_permit is False


def test_lockout_clears_permit():
    link, monitor = make_monitor(model=1001)
    monitor.verify(1001)
    monitor.lock_out()
    assert monitor.state is MachineState.LOCKED
    assert link.run_permit is False


def test_last_scan_echoed_to_plc():
    link, monitor = make_monitor(model=1001)
    monitor.verify(1001)
    assert link.last_scan == 1001


# --- MO number extraction ----------------------------------------------------

SCAN = ScannerConfig(type="stdin")
CMP = CompareConfig(mo_last_digits=4, digits_only=True)


def test_last_four_digits_used():
    assert extract_mo_number("MO-2024-981001", SCAN, CMP) == 1001


def test_digits_only_strips_separators():
    assert extract_mo_number("12-34-56-78", SCAN, CMP) == 5678


def test_whole_number_when_zero_digits():
    cfg = CompareConfig(mo_last_digits=0, digits_only=True)
    assert extract_mo_number("000123456", SCAN, cfg) == 123456


def test_non_numeric_returns_none():
    assert extract_mo_number("NO-DIGITS-HERE", SCAN, CMP) is None


def test_scan_pattern_extracts_then_last_digits():
    scan = ScannerConfig(type="stdin", scan_pattern=r"MODEL=(?P<model>\d+)")
    assert extract_mo_number("JOB|MODEL=778801001|QTY=5", scan, CMP) == 1001
