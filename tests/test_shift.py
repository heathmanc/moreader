"""Tests for shift-change detection."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import ShiftConfig
from moreader.shift import ShiftDetector


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def test_time_shift_change_fires_once_per_boundary():
    clock = Clock(datetime(2026, 6, 30, 5, 59))
    cfg = ShiftConfig(start_times=["06:00", "14:00", "22:00"], watch_plc_request=False)
    det = ShiftDetector(cfg, now=clock)

    assert det.check() is False               # still before 06:00
    clock.dt = datetime(2026, 6, 30, 6, 0)
    assert det.check() is True                # crossed into the 06:00 shift
    assert det.check() is False               # same shift, no repeat
    clock.dt = datetime(2026, 6, 30, 14, 1)
    assert det.check() is True                # crossed into the 14:00 shift


def test_plc_request_rising_edge():
    cfg = ShiftConfig(start_times=[], watch_plc_request=True)
    det = ShiftDetector(cfg)
    assert det.check(plc_request_active=False) is False
    assert det.check(plc_request_active=True) is True    # rising edge
    assert det.check(plc_request_active=True) is False   # held high, no repeat
    assert det.check(plc_request_active=False) is False
    assert det.check(plc_request_active=True) is True     # edge again


def test_plc_request_ignored_when_disabled():
    cfg = ShiftConfig(start_times=[], watch_plc_request=False)
    det = ShiftDetector(cfg)
    assert det.check(plc_request_active=True) is False


def test_shift_label():
    clock = Clock(datetime(2026, 6, 30, 15, 0))
    cfg = ShiftConfig(start_times=["06:00", "14:00", "22:00"])
    det = ShiftDetector(cfg, now=clock)
    assert det.current_shift_label() == "shift starting 14:00"
