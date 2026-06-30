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


def test_all_match_verifies_master():
    worker = make_worker(recipes=(1001, 1001, 1001))
    worker._handle(CMD_VERIFY, "MO-99-771001")
    assert all(e.state is State.MATCH for e in worker.encapsulators)
    assert worker.master.mo_verified is True
    assert worker.master.link.mo_verified is True


def test_one_mismatch_blocks_master():
    worker = make_worker(recipes=(1001, 1001, 2002))
    worker._handle(CMD_VERIFY, "AB1001")
    states = [e.state for e in worker.encapsulators]
    assert states == [State.MATCH, State.MATCH, State.MISMATCH]
    assert worker.master.mo_verified is False
    assert worker.master.link.mo_verified is False


def test_manual_lockout_clears_master():
    worker = make_worker()
    worker._handle(CMD_VERIFY, "AB1001")        # verified
    assert worker.master.mo_verified is True
    worker._handle(CMD_LOCKOUT, None)
    assert worker.master.mo_verified is False
    assert all(e.state is State.LOCKED for e in worker.encapsulators)


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
    worker._handle(CMD_VERIFY, "NODIGITS")
    assert worker.master.mo_verified is False
    texts = [e.get("text", "") for e in drain(worker) if e["type"] == "log"]
    assert any("Invalid scan" in t for t in texts)


def test_heartbeat_written_during_housekeeping():
    worker = make_worker()
    worker._last_beat = -1000          # force a heartbeat this pass
    worker._housekeeping()
    assert worker.master.heartbeat >= 1
    assert worker.master.link.heartbeat == worker.master.heartbeat


def test_status_event_shape():
    worker = make_worker(recipes=(1001, 1001, 1001))
    worker._handle(CMD_VERIFY, "X1001")
    statuses = [e for e in drain(worker) if e["type"] == "status"]
    assert statuses
    last = statuses[-1]
    assert len(last["encapsulators"]) == 3
    assert last["encapsulators"][0]["recipe"] == 1001
    assert last["master"]["mo_verified"] is True
    assert last["master"]["name"] == "COS"
