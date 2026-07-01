"""Tests for serial-scan buffer splitting and digit extraction (zeros kept)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import CompareConfig, Config, ScannerConfig
from moreader.scanner import extract_battery_digits, extract_last_digits, parse_mo, split_scans

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


# --- MO barcode formats (F / GL / default) ----------------------------------

FORMATS = Config().mo_formats


def test_parse_f_format():
    fmt, val, err = parse_mo("F2220-1301", FORMATS)
    assert err is None and val == "1301" and fmt.prefix == "F"


def test_parse_gl_format():
    fmt, val, err = parse_mo("GL0007564-0000", FORMATS)
    assert err is None and val == "7564" and fmt.prefix == "GL"


def test_parse_default_format():
    fmt, val, err = parse_mo("2220-1321", FORMATS)
    assert err is None and val == "1321" and fmt.prefix == ""


def test_parse_f_wrong_length():
    fmt, val, err = parse_mo("F2220-13011", FORMATS)   # 11 chars, F needs 10
    assert val is None and "10 characters" in err


def test_parse_gl_keeps_leading_zeros():
    # GL slice at position 6, 4 chars — even if they are zeros.
    fmt, val, err = parse_mo("GL0010042-0000", FORMATS)
    assert val == "0042"
