# moreader

Manufacturing-order scan verification for a line of three encapsulator PLCs and
a COS master PLC, with a PySide6 industrial-HMI front end.

Each shift the operator presses **VERIFY MO** and scans the manufacturing order.
`moreader` takes the **last 4 digits** of the barcode and compares them to the
recipe **DINT** each encapsulator is set to run (default tag `recipe[0].Name`).
When all three match, it sets the master's `MO_Verified` bit so COS can run. A
shift change or **Manual Lockout** clears `MO_Verified`, and a **heartbeat** DINT
is written to the master so it knows the application is alive.

```
 scan MO ─▶ last 4 digits ─┬─ Encapsulator 1 recipe DINT ─┐
                           ├─ Encapsulator 2 recipe DINT ─┤ all match?
                           └─ Encapsulator 3 recipe DINT ─┘     │
                                                                ▼
                                   COS master:  MO_Verified = TRUE  (COS may run)
   shift change / Manual Lockout ▶ MO_Verified = FALSE   ·   heartbeat DINT ++ every 1 s
```

## The HMI

`python -m moreader` opens the operator screen:

* **Three encapsulator tiles** (read-only) — each shows its recipe DINT and,
  after a scan, MATCH (green) or MISMATCH (red); grey when offline.
* **Master (COS) panel** — shows **MO VERIFIED** (green) or **LOCKED** (amber),
  plus a pulsing heartbeat indicator and its counter.
* **VERIFY MO** — there is no text box anywhere; this button opens a modal dialog
  that captures the USB scanner's keystrokes directly (the operator cannot type
  an order in by hand).
* **BYPASS** — passworded; sets `MO_Bypassed` so COS may run without a verified
  scan. The master panel turns violet (MO BYPASSED).
* **MANUAL LOCKOUT** — clears `MO_Verified` and `MO_Bypassed` on demand.

`MO_Bypassed` is also cleared automatically at every shift change.

**Settings** (password `2134chAP!@`, stored in the YAML) opens the configuration
screen: tabs for **PLCs** (the three encapsulators and the master, each tag with
its name and description, plus the heartbeat interval), **Scanner**, **Compare**
(trailing-digit rule), **Shift**, and **Security**. **Save & Apply** writes the
YAML and reconnects.

All PLC I/O runs on a background thread, so a slow or offline PLC never freezes
the interface.

## What moreader reads/writes

| PLC                 | Tag                | Dir   | Type | Purpose                                   |
| ------------------- | ------------------ | ----- | ---- | ----------------------------------------- |
| Encapsulator 1/2/3  | `recipe[0].Name`   | read  | DINT | recipe number the encapsulator is set to  |
| COS (master)        | `MO_Verified`      | write | BOOL | true only when all recipes match the scan |
| COS (master)        | `MO_Bypassed`      | write | BOOL | true when an operator bypasses (passworded; cleared at shift/lockout) |
| COS (master)        | `Heartbeat`        | write | DINT | incremented every `heartbeat_interval` s  |

## Install

```bash
pip install -r requirements.txt
# Linux also needs the Qt runtime libraries, e.g. on Debian/Ubuntu:
#   sudo apt-get install libegl1 libgl1 libxkbcommon0 libfontconfig1
```

* `PySide6` — the GUI.  `pylogix` — EtherNet/IP comms.  `pyyaml` — config.

## Run

```bash
# GUI against real hardware (creates/uses ./config.yaml)
python -m moreader --config config.yaml

# GUI with NO hardware — simulated PLCs (all recipes 1001)
python -m moreader --simulate

# Headless console mode (no display)
python -m moreader --config config.yaml --headless
```

In simulated mode, press **VERIFY MO** and scan/type a value ending in `1001` to
verify all three encapsulators and set the master. Open **Settings** with
`2134chAP!@` to edit the PLC IPs and tags.

## Project layout

| File                       | Responsibility                                   |
| -------------------------- | ------------------------------------------------ |
| `moreader/config.py`       | Load/validate/save YAML (encapsulators, master). |
| `moreader/plc.py`          | pylogix encapsulator (read) + master (write).    |
| `moreader/scanner.py`      | Scan input + last-N-digit MO number parser.      |
| `moreader/controller.py`   | Encapsulator/master monitors + state.            |
| `moreader/shift.py`        | Shift-change detection.                          |
| `moreader/worker.py`       | Background thread; verification + heartbeat.     |
| `moreader/gui_qt.py`       | PySide6 industrial HMI.                           |
| `moreader/cli.py`          | Entry point (GUI default, `--headless`).         |
| `scripts/gui_smoketest.py` | Offscreen GUI smoke test / screenshot driver.    |

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

The 28 tests run entirely against simulated PLCs — no hardware required.

## Safety note

`moreader` is an interlock aid, not a safety-rated system. Keep all required
machine safety functions (E-stops, guarding, safety relays) independent of this
program. The configuration password is stored in plaintext in the YAML to keep
casual operators out of settings — it is not a security boundary against someone
with file access.
