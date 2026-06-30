"""Tests for encapsulator/master monitors and the MO number parser."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import CompareConfig, EncapsulatorConfig, MasterConfig, ScannerConfig
from moreader.controller import EncapsulatorMonitor, MasterMonitor, State
from moreader.plc import SimulatedEncapsulator, SimulatedMaster
from moreader.scanner import extract_mo_number


def make_encap(recipe=1001):
    link = SimulatedEncapsulator(EncapsulatorConfig(name="E1"), recipe=recipe)
    mon = EncapsulatorMonitor(link)
    mon.connect()
    return link, mon


def make_master():
    link = SimulatedMaster(MasterConfig(name="COS"))
    mon = MasterMonitor(link)
    mon.connect()
    return link, mon


def test_encapsulator_match():
    link, mon = make_encap(1001)
    assert mon.evaluate(1001) is True
    assert mon.state is State.MATCH


def test_encapsulator_mismatch():
    link, mon = make_encap(1001)
    assert mon.evaluate(2002) is False
    assert mon.state is State.MISMATCH


def test_encapsulator_invalid_scan():
    link, mon = make_encap(1001)
    assert mon.evaluate(None) is False
    assert mon.state is State.MISMATCH


def test_encapsulator_is_read_only():
    # SimulatedEncapsulator exposes no permit/alarm — only the recipe is read.
    link, mon = make_encap(1234)
    assert mon.link.read_recipe() == 1234
    assert not hasattr(link, "run_permit")


def test_master_verified_sets_bit():
    link, mon = make_master()
    assert link.mo_verified is False        # starts safe on connect
    mon.set_verified(True)
    assert link.mo_verified is True
    assert mon.state is State.VERIFIED


def test_master_lockout_clears_bit():
    link, mon = make_master()
    mon.set_verified(True)
    mon.set_verified(False)
    assert link.mo_verified is False
    assert mon.state is State.LOCKED


def test_master_heartbeat_increments_and_writes():
    link, mon = make_master()
    mon.beat()
    mon.beat()
    assert mon.heartbeat == 2
    assert link.heartbeat == 2


# --- MO number extraction ----------------------------------------------------

SCAN = ScannerConfig(type="stdin")
CMP = CompareConfig(mo_last_digits=4, digits_only=True)


def test_last_four_digits_used():
    assert extract_mo_number("MO-2024-981001", SCAN, CMP) == 1001


def test_digits_only_strips_separators():
    assert extract_mo_number("12-34-56-78", SCAN, CMP) == 5678


def test_non_numeric_returns_none():
    assert extract_mo_number("NO-DIGITS-HERE", SCAN, CMP) is None
