"""Tests for the background PLC worker, driven synchronously (no thread).

The worker's command handlers and housekeeping are called directly so the tests
are deterministic; the thread loop is just a wrapper around these.
"""

import os
import queue
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import Config
from moreader.controller import State
from moreader.worker import CMD_LOCKOUT, CMD_VERIFY, PLCWorker


def make_worker(expected="ABC-100"):
    cfg = Config()
    cfg.shift.start_times = []           # disable time-based detection in tests
    worker = PLCWorker(cfg, simulate=True, expected_model=expected, events=queue.Queue())
    worker._connect()                     # build + connect the simulated PLC
    return worker


def drain(worker):
    events = []
    try:
        while True:
            events.append(worker.events.get_nowait())
    except queue.Empty:
        pass
    return events


def test_connect_locks_out_on_startup():
    worker = make_worker()
    assert worker.connected is True
    assert worker.controller.state is State.LOCKED
    assert worker.plc.run_permit is False


def test_verify_match_enables_run():
    worker = make_worker()
    worker._handle(CMD_VERIFY, "ABC-100")
    assert worker.plc.run_permit is True
    assert worker.plc.alarm is False
    assert worker.controller.state is State.RUNNING


def test_verify_mismatch_raises_alarm():
    worker = make_worker()
    worker._handle(CMD_VERIFY, "WRONG-1")
    assert worker.plc.run_permit is False
    assert worker.plc.alarm is True
    assert worker.controller.state is State.ALARM
    # A log event describing the alarm should have been emitted.
    texts = [e.get("text", "") for e in drain(worker) if e["type"] == "log"]
    assert any("ALARM" in t for t in texts)


def test_status_events_emitted():
    worker = make_worker()
    worker._handle(CMD_VERIFY, "ABC-100")
    statuses = [e for e in drain(worker) if e["type"] == "status"]
    assert statuses
    last = statuses[-1]
    assert last["state"] == "RUNNING"
    assert last["expected"] == "ABC-100"
    assert last["last_scan"] == "ABC-100"


def test_plc_shift_request_locks_out_while_running():
    worker = make_worker()
    worker._handle(CMD_VERIFY, "ABC-100")            # -> RUNNING
    assert worker.controller.state is State.RUNNING
    worker.plc.request_shift_change(True)            # PLC asks for a shift change
    worker._housekeeping()
    assert worker.controller.state is State.LOCKED
    assert worker.plc.run_permit is False


def test_lockout_command():
    worker = make_worker()
    worker._handle(CMD_VERIFY, "ABC-100")
    worker._handle(CMD_LOCKOUT, None)
    assert worker.controller.state is State.LOCKED
    assert worker.plc.run_permit is False
