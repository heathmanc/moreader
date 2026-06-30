"""Tests for the worker: 3 encapsulators + COS master, driven synchronously."""

import os
import queue
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import Config
from moreader.controller import State
from moreader.worker import CMD_BYPASS, CMD_LOCKOUT, CMD_VERIFY, PLCWorker


def make_worker(recipes=(1001, 1001, 1001)):
    cfg = Config()
    cfg.shift.start_times = []            # disable time-based detection in tests
    worker = PLCWorker(cfg, simulate=True, sim_recipes=list(recipes), events=queue.Queue())
    worker._build()
    worker._connect_all()
    return worker


def drain(worker):
    events = []
    try:
        while True:
            events.append(worker.events.get_nowait())
    except queue.Empty:
        pass
    return events


def test_connect_locks_master_on_startup():
    worker = make_worker()
    assert len(worker.encapsulators) == 3
    assert worker.master.connected
    assert worker.master.mo_verified is False
    assert all(e.state is State.LOCKED for e in worker.encapsulators)


# 9-character MO scans (the default required length) ending in the model number.
MO_1001 = "000001001"
MO_1002 = "000001002"


def test_all_match_verifies_master():
    worker = make_worker(recipes=(1001, 1001, 1001))
    worker._handle(CMD_VERIFY, MO_1001)
    assert all(e.state is State.MATCH for e in worker.encapsulators)
    assert worker.master.mo_verified is True
    assert worker.master.link.mo_verified is True


def test_one_mismatch_blocks_master():
    worker = make_worker(recipes=(1001, 1001, 2002))
    worker._handle(CMD_VERIFY, MO_1001)
    states = [e.state for e in worker.encapsulators]
    assert states == [State.MATCH, State.MATCH, State.MISMATCH]
    assert worker.master.mo_verified is False
    assert worker.master.link.mo_verified is False


def test_manual_lockout_clears_master_and_requests_cycle_stop():
    worker = make_worker()
    worker._handle(CMD_VERIFY, MO_1001)        # verified
    assert worker.master.mo_verified is True
    assert worker.master.cycle_stop is False
    worker._handle(CMD_LOCKOUT, None)
    assert worker.master.mo_verified is False
    assert worker.master.cycle_stop is True    # graceful cycle stop requested
    assert worker.master.link.cycle_stop is True
    assert all(e.state is State.LOCKED for e in worker.encapsulators)


def test_verify_clears_cycle_stop():
    worker = make_worker()
    worker._handle(CMD_LOCKOUT, None)
    assert worker.master.cycle_stop is True
    worker._handle(CMD_VERIFY, MO_1001)
    assert worker.master.mo_verified is True
    assert worker.master.cycle_stop is False


def test_mo_length_check_blocks_short_scan():
    worker = make_worker()
    worker._handle(CMD_VERIFY, "1001")          # too short (default length is 9)
    assert worker.master.mo_verified is False
    texts = [e.get("text", "") for e in drain(worker) if e["type"] == "log"]
    assert any("9 characters" in t for t in texts)


def test_bypass_sets_master_bit():
    worker = make_worker()
    worker._handle(CMD_BYPASS, "on")
    assert worker.master.mo_bypassed is True
    assert worker.master.link.mo_bypassed is True
    assert worker.master.state is State.BYPASSED


def test_bypass_cleared_by_lockout():
    worker = make_worker()
    worker._handle(CMD_BYPASS, "on")
    worker._handle(CMD_LOCKOUT, None)
    assert worker.master.mo_bypassed is False
    assert worker.master.link.mo_bypassed is False


# Assembled Battery MO: 9 chars ending in the model number.
ASSEMBLED_1001 = "770001001"


def test_secondary_battery_matches_assembled_mo():
    worker = make_worker(recipes=(1001, 1001, 1001))
    worker.config.secondary.enabled = True
    # Stuffed MO -> encapsulators; battery first-4 (1001) == Assembled MO last-4 (1001).
    worker._handle(CMD_VERIFY, {"mo": MO_1001, "assembled_mo": ASSEMBLED_1001, "battery": "1001ABCDEFGH"})
    assert worker.master.mo_verified is True
    assert worker.battery_matched is True


def test_secondary_battery_vs_assembled_mismatch_blocks():
    worker = make_worker(recipes=(1001, 1001, 1001))
    worker.config.secondary.enabled = True
    # Battery first-4 (2002) != Assembled MO last-4 (1001).
    worker._handle(CMD_VERIFY, {"mo": MO_1001, "assembled_mo": ASSEMBLED_1001, "battery": "2002ABCDEFGH"})
    assert worker.master.mo_verified is False


def test_secondary_uses_assembled_not_stuffed_mo():
    # Even when the battery would match the stuffed MO, only the Assembled MO counts.
    worker = make_worker(recipes=(1001, 1001, 1001))
    worker.config.secondary.enabled = True
    # Stuffed MO last-4 = 1001 (matches encapsulators); Assembled MO last-4 = 2002;
    # battery first-4 = 2002 -> matches the Assembled MO, so it verifies.
    worker._handle(CMD_VERIFY, {"mo": MO_1001, "assembled_mo": "770002002", "battery": "2002ABCDEFGH"})
    assert worker.master.mo_verified is True


def test_secondary_battery_too_short_blocks():
    worker = make_worker(recipes=(1001, 1001, 1001))
    worker.config.secondary.enabled = True
    worker._handle(CMD_VERIFY, {"mo": MO_1001, "assembled_mo": ASSEMBLED_1001, "battery": "1001"})  # < 10
    assert worker.master.mo_verified is False
    texts = [e.get("text", "") for e in drain(worker) if e["type"] == "log"]
    assert any("at least 10 characters" in t for t in texts)


def test_secondary_assembled_mo_wrong_length_blocks():
    worker = make_worker(recipes=(1001, 1001, 1001))
    worker.config.secondary.enabled = True
    worker._handle(CMD_VERIFY, {"mo": MO_1001, "assembled_mo": "1001", "battery": "1001ABCDEFGH"})  # 4 chars
    assert worker.master.mo_verified is False
    texts = [e.get("text", "") for e in drain(worker) if e["type"] == "log"]
    assert any("Assembled Battery MO must be 9 characters" in t for t in texts)


def test_bypass_cleared_by_shift_change():
    cfg = Config()
    worker = PLCWorker(cfg, simulate=True, events=queue.Queue())
    worker._build()
    worker._connect_all()
    worker._handle(CMD_BYPASS, "on")
    # Force a shift boundary by resetting the detector's baseline.
    worker.detector._current_shift = -99
    worker._housekeeping()
    assert worker.master.mo_bypassed is False


def test_invalid_scan_logs_alarm_and_no_verify():
    worker = make_worker()
    worker._handle(CMD_VERIFY, "NODIGITSX")     # 9 chars (passes length) but no number
    assert worker.master.mo_verified is False
    texts = [e.get("text", "") for e in drain(worker) if e["type"] == "log"]
    assert any("Invalid MO scan" in t for t in texts)


def test_heartbeat_written_during_housekeeping():
    worker = make_worker()
    worker._last_beat = -1000          # force a heartbeat this pass
    worker._housekeeping()
    assert worker.master.heartbeat >= 1
    assert worker.master.link.heartbeat == worker.master.heartbeat


def test_status_event_shape():
    worker = make_worker(recipes=(1001, 1001, 1001))
    worker._handle(CMD_VERIFY, MO_1001)
    statuses = [e for e in drain(worker) if e["type"] == "status"]
    assert statuses
    last = statuses[-1]
    assert len(last["encapsulators"]) == 3
    assert last["encapsulators"][0]["recipe"] == 1001
    assert last["master"]["mo_verified"] is True
    assert last["master"]["name"] == "COS"
    assert "cycle_stop" in last["master"]
    assert "secondary" in last
