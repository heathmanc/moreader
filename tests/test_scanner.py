"""Tests for serial-scan buffer splitting and digit extraction (zeros kept)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import CompareConfig, ScannerConfig
from moreader.scanner import extract_battery_digits, extract_last_digits, split_scans

SCAN = ScannerConfig(type="serial", port="COM4")


def test_split_scans_one_complete_one_partial():
    scans, remainder = split_scans("MO0001001\r\nMO0002")
    assert scans == ["MO0001001"]
    assert remainder == "MO0002"


def test_split_scans_multiple_terminators():
    scans, remainder = split_scans("A123\r\nB456\nC789\r")
    assert scans == ["A123", "B456", "C789"]
    assert remainder == ""


def test_split_scans_no_terminator_keeps_buffer():
    scans, remainder = split_scans("PARTIAL")
    assert scans == []
    assert remainder == "PARTIAL"


def test_serial_battery_and_assembled_extraction_keep_zeros():
    assert extract_battery_digits("0042XYZLOT99", SCAN, 4) == "0042"
    assert extract_last_digits("MO-77-980042", SCAN, 4) == "0042"
