# moreader

Manufacturing-order scan verification for three Allen Bradley PLCs, with a
PySide6 industrial-HMI front end.

Each shift the incoming operator presses **VERIFY MO** and scans the
manufacturing order. `moreader` takes the **last 4 digits** of the barcode and
compares them to the model-number **DINT** each PLC is currently set to run
(default tag `recipe[0].Name`). A machine whose model matches gets its **run
permit**; a mismatch raises that machine's **alarm** and keeps it locked out.

```
                              ┌─ PLC 1 (recipe[0].Name = DINT) ─ match? ─ permit/alarm
 scan MO ─▶ last 4 digits ─▶ ─┼─ PLC 2 (recipe[0].Name = DINT) ─ match? ─ permit/alarm
                              └─ PLC 3 (recipe[0].Name = DINT) ─ match? ─ permit/alarm
 shift change (clock or PLC bit) clears all run permits and forces a re-scan
```

## The HMI

`python -m moreader` opens a full-screen-friendly industrial HMI:

* **Operator screen** — three machine tiles (one per PLC), each with a status
  lamp, the model DINT it is set to run, and the last scanned value. Tiles are
  colour-coded: green = RUN ENABLED, red = ALARM, amber = LOCKED, grey =
  OFFLINE. There is **no text box** — the operator presses the large **VERIFY
  MO** button, which opens a modal dialog the USB scanner sends the barcode
  into. A **NEW SHIFT** button re-locks all machines on demand.
* **Configuration screen** — **password protected** (default `2134chAP!@`,
  stored in the YAML). Tabs:
  * **Machines** — per PLC: name, IP, slot, and every tag with its **name and
    description**.
  * **Scanner** — type and an optional model-extraction regex.
  * **Compare** — how many trailing digits to match, digit-stripping.
  * **Shift** — shift start times, PLC-request watch, lock-on-startup, poll rate.
  * **Security** — change the configuration password.

  **Save & Apply** writes the YAML and reconnects the PLCs.

All PLC I/O runs on a background thread, so a slow or offline PLC never freezes
the interface; the GUI polls the worker's event queue with a timer.

## How a scan is matched

1. The raw barcode is read in the scan dialog.
2. An optional regex (`scanner.scan_pattern`) can pull the model out of a richer
   MO string.
3. Non-digits are stripped (configurable) and the **last N digits** are taken
   (`compare.mo_last_digits`, default 4) and parsed as an integer.
4. That integer is compared to each PLC's model DINT. Match → `run_permit` set,
   alarm cleared; mismatch → `run_permit` cleared, `alarm` set.

Your PLC ladder gates the machine by interlocking on its `run_permit` bit;
`moreader` only sets/clears bits and reads the model DINT.

## Install

```bash
pip install -r requirements.txt
# Linux also needs the Qt runtime libraries, e.g. on Debian/Ubuntu:
#   sudo apt-get install libegl1 libgl1 libxkbcommon0 libfontconfig1
```

* `PySide6` — the GUI.
* `pylogix` — EtherNet/IP comms to CompactLogix / ControlLogix.
* `pyyaml` — config file.

## Run

```bash
# GUI against real hardware (creates/uses ./config.yaml)
python -m moreader --config config.yaml

# GUI with NO hardware — three simulated PLCs (models 1001/1002/1003)
python -m moreader --simulate

# Headless console mode (no display)
python -m moreader --config config.yaml --headless
```

In simulated mode, press **VERIFY MO** and scan/type `...1002` to enable only
Machine 2; `...1001` enables Machine 1, etc. Open **Settings** with `2134chAP!@`
to edit the PLC tags.

## Configure

Copy `config.example.yaml` to `config.yaml` (or let the GUI create it on the
first **Save**). Per-PLC tags:

| Purpose                          | Tag key            | Type   |
| -------------------------------- | ------------------ | ------ |
| Model the PLC is set to run      | `model_tag`        | DINT   |
| Run permit (gate motion on this) | `run_permit_tag`   | BOOL   |
| Scan-mismatch alarm              | `alarm_tag`        | BOOL   |
| Shift-change request (optional)  | `shift_request_tag`| BOOL   |
| Last-scan echo (optional)        | `last_scan_tag`    | DINT   |

## Project layout

| File                       | Responsibility                                   |
| -------------------------- | ------------------------------------------------ |
| `moreader/config.py`       | Load/validate/save YAML (machines, tags, etc.).  |
| `moreader/plc.py`          | pylogix per-machine link + a simulated machine.  |
| `moreader/scanner.py`      | Scan input + last-N-digit MO number parser.      |
| `moreader/controller.py`   | Per-machine LOCKED/RUNNING/ALARM state machine.  |
| `moreader/shift.py`        | Shift-change detection (clock + PLC bit edge).   |
| `moreader/worker.py`       | Background thread driving all three PLCs.        |
| `moreader/gui_qt.py`       | PySide6 industrial HMI.                          |
| `moreader/cli.py`          | Entry point (GUI default, `--headless` option).  |
| `scripts/gui_smoketest.py` | Offscreen GUI smoke test / screenshot driver.    |

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

The 29 tests run entirely against simulated PLCs — no hardware required.

## Safety note

`moreader` is an interlock aid, not a safety-rated system. Keep all required
machine safety functions (E-stops, guarding, safety relays) independent of this
program. The configuration password is stored in plaintext in the YAML to keep
casual operators out of settings — it is not a security boundary against someone
with file access.
