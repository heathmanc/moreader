"""Tests for the audit log and that the worker records scan outcomes."""

import csv
import os
import queue
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.audit import AuditLog
from moreader.config import Config
from moreader.worker import CMD_BYPASS, CMD_LOCKOUT, CMD_VERIFY, PLCWorker

MO_1001 = "000001001"


def read_rows(directory):
    rows = []
    for path in sorted(directory.glob("moreader-*.csv")):
        with path.open(newline="") as fh:
            rows.extend(list(csv.DictReader(fh)))
    return rows


def test_audit_writes_header_and_row(tmp_path):
    audit = AuditLog(tmp_path, enabled=True)
    audit.record("VERIFY", "PASS", stuffed_mo="1001", detail="ok")
    rows = read_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["event"] == "VERIFY"
    assert rows[0]["result"] == "PASS"
    assert rows[0]["stuffed_mo"] == "1001"


def test_audit_disabled_writes_nothing(tmp_path):
    audit = AuditLog(tmp_path, enabled=False)
    audit.record("VERIFY", "PASS")
    assert list(tmp_path.glob("*.csv")) == []


def make_worker(tmp_path, recipes=(1001, 1001, 1001)):
    cfg = Config()
    cfg.shift.start_times = []
    cfg.audit.directory = str(tmp_path)
    worker = PLCWorker(cfg, simulate=True, sim_recipes=list(recipes), events=queue.Queue())
    worker._build()
    worker._connect_all()
    return worker


def test_bypass_records_name_and_reason(tmp_path):
    worker = make_worker(tmp_path)
    worker._handle(CMD_BYPASS, {"on": True, "name": "Sam Lee", "reason": "jam clear"})
    rows = read_rows(tmp_path)
    bypass = [r for r in rows if r["event"] == "BYPASS" and r["result"] == "ON"][0]
    assert "Sam Lee" in bypass["detail"]
    assert "jam clear" in bypass["detail"]


def test_worker_audits_pass_and_fail(tmp_path):
    worker = make_worker(tmp_path)
    worker._handle(CMD_VERIFY, MO_1001)                 # PASS
    worker._handle(CMD_VERIFY, "000009999")             # FAIL (mismatch)
    worker._handle(CMD_BYPASS, "on")                    # BYPASS ON
    worker._handle(CMD_LOCKOUT, None)                   # LOCKOUT
    rows = read_rows(tmp_path)
    events = [(r["event"], r["result"]) for r in rows]
    assert ("VERIFY", "PASS") in events
    assert ("VERIFY", "FAIL") in events
    assert ("BYPASS", "ON") in events
    assert ("LOCKOUT", "") in events
    # The failing row should carry the mismatch reason.
    fail = [r for r in rows if r["event"] == "VERIFY" and r["result"] == "FAIL"][0]
    assert "9999" in fail["detail"]
