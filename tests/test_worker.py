"""Tests for the multi-machine background worker, driven synchronously."""

import os
import queue
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import Config
from moreader.controller import MachineState
from moreader.worker import CMD_LOCKOUT, CMD_VERIFY, PLCWorker


def make_worker(models=(1001, 1002, 1003)):
    cfg = Config()
    cfg.shift.start_times = []            # disable time-based detection in tests
    worker = PLCWorker(cfg, simulate=True, sim_models=list(models), events=queue.Queue())
    worker._build_monitors()
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


def test_connect_locks_all_on_startup():
    worker = make_worker()
    assert len(worker.monitors) == 3
    assert all(m.connected for m in worker.monitors)
    assert all(m.state is MachineState.LOCKED for m in worker.monitors)
    assert all(m.link.run_permit is False for m in worker.monitors)


def test_scan_enables_only_matching_machine():
    worker = make_worker(models=(1001, 1002, 1003))
    # Scanned MO last-4 = 1002 -> only Machine 2 should run.
    worker._handle(CMD_VERIFY, "MO-99-771002")
    states = [m.state for m in worker.monitors]
    assert states == [MachineState.ALARM, MachineState.RUNNING, MachineState.ALARM]
    assert worker.monitors[1].link.run_permit is True
    assert worker.monitors[0].link.run_permit is False
    assert worker.monitors[2].link.run_permit is False
    assert worker.monitors[0].link.alarm is True


def test_scan_matching_all():
    worker = make_worker(models=(1001, 1001, 1001))
    worker._handle(CMD_VERIFY, "AB1001")
    assert all(m.state is MachineState.RUNNING for m in worker.monitors)
    assert all(m.link.run_permit for m in worker.monitors)


def test_invalid_scan_logs_alarm():
    worker = make_worker()
    worker._handle(CMD_VERIFY, "NODIGITS")
    texts = [e.get("text", "") for e in drain(worker) if e["type"] == "log"]
    assert any("Invalid scan" in t for t in texts)


def test_status_event_shape():
    worker = make_worker(models=(1001, 1002, 1003))
    worker._handle(CMD_VERIFY, "X1002")
    statuses = [e for e in drain(worker) if e["type"] == "status"]
    assert statuses
    machines = statuses[-1]["machines"]
    assert len(machines) == 3
    assert machines[1]["state"] == "RUNNING"
    assert machines[1]["model"] == 1002
    assert machines[1]["scanned"] == 1002


def test_lockout_command_locks_all():
    worker = make_worker(models=(1001, 1001, 1001))
    worker._handle(CMD_VERIFY, "AB1001")
    worker._handle(CMD_LOCKOUT, None)
    assert all(m.state is MachineState.LOCKED for m in worker.monitors)
    assert all(m.link.run_permit is False for m in worker.monitors)


def test_plc_shift_request_locks_out_while_running():
    worker = make_worker(models=(1001, 1001, 1001))
    worker._handle(CMD_VERIFY, "AB1001")           # all RUNNING
    worker.monitors[0].link.request_shift_change(True)
    worker._housekeeping()
    assert all(m.state is MachineState.LOCKED for m in worker.monitors)
