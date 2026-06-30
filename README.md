# moreader

Manufacturing-order scan verification for an Allen Bradley Logix PLC, with a
polished password-protected GUI.

At every shift change the incoming operator must scan the manufacturing order
barcode. `moreader` reads the scan from a USB barcode scanner, compares it to the
**model number the PLC is currently set to run**, and only then grants the PLC's
*run permit*. If the scan does not match, it raises an **alarm** and keeps the
machine locked out until a correct order is scanned.

```
 USB scanner ──▶ moreader ──▶ compare ──▶  match  ──▶ set RunPermit = TRUE  (PLC may run)
                    ▲                      mismatch ─▶ set Alarm = TRUE      (PLC blocked)
                    │
            shift change (clock or PLC bit) clears RunPermit and forces a re-scan
```

## The GUI

Run `python -m moreader` and you get two tabs:

* **Operator** — a large colour-coded status banner (LOCKED / RUN ENABLED /
  ALARM), the model the PLC is set to run, the last scan, the current shift, a
  scan box (a USB keyboard-wedge scanner types straight into it), and a live
  event log.
* **Configuration** — **password protected** (default `2134chAP!@`, stored in
  the YAML). Inside are sub-tabs:
  * **PLC** — driver, IP, slot, and every tag with its **name and description**.
  * **Scanner** — type, serial port/baud, optional model-extraction regex.
  * **Shift** — shift start times, PLC-request watch, lock-on-startup, poll rate.
  * **Compare** — whitespace/case matching rules.
  * **Security** — change the configuration password.

  **Save & Apply** writes the YAML and reconnects the PLC.

The entire configuration is editable from the GUI; you never have to touch the
YAML by hand (though you can).

## How it works

1. On startup (and at every shift change) the run permit is cleared, so the PLC
   cannot run the product.
2. The operator scans the manufacturing order into the scan box.
3. `moreader` reads the PLC's expected-model tag, normalises both strings, and
   compares them.
   * **Match** → sets `run_permit` True, clears the alarm. The machine can run.
   * **Mismatch** → sets `alarm` True, leaves the run permit cleared, waits for
     another scan.
4. While running it watches for the next shift change — a configured clock
   boundary (e.g. 06:00 / 14:00 / 22:00) or a rising edge on the PLC/HMI
   `shift_request` bit — and locks out again when it occurs.

All PLC I/O runs on a background thread, so a slow or offline PLC never freezes
the interface. Your PLC ladder gates the machine by interlocking on the
`run_permit` bit; `moreader` only sets/clears bits and reads the model tag.

## Install

```bash
pip install -r requirements.txt
# On Linux, Tkinter is an OS package:
#   sudo apt-get install python3-tk
# On Windows/macOS, Tkinter ships with the standard Python installer.
```

* `pylogix` — EtherNet/IP comms to CompactLogix / ControlLogix.
* `pyyaml` — config file.
* `pyserial` — only for a serial (USB-CDC) scanner in headless mode.

## Run

```bash
# GUI against real hardware (creates/uses ./config.yaml)
python -m moreader --config config.yaml

# GUI with NO hardware — simulated PLC, type scans into the box
python -m moreader --simulate --expected-model ABC-100

# Headless console mode (kiosk/terminal, no display)
python -m moreader --config config.yaml --headless
```

Try simulated mode: type a wrong value to see the alarm, then the matching model
to enable the run. Open **Configuration**, enter `2134chAP!@`, and edit the tags.

## Configure

Copy `config.example.yaml` to `config.yaml` (or just let the GUI create it on
first **Save**). Key tags to create in the PLC:

| Purpose                          | Config key       | Type   |
| -------------------------------- | ---------------- | ------ |
| Model the PLC is set to run      | `expected_model` | STRING |
| Run permit (gate motion on this) | `run_permit`     | BOOL   |
| Scan-mismatch alarm              | `alarm`          | BOOL   |
| Shift-change request (optional)  | `shift_request`  | BOOL   |
| Last-scan echo for HMI (optional)| `last_scan`      | STRING |

## Project layout

| File                      | Responsibility                                   |
| ------------------------- | ------------------------------------------------ |
| `moreader/config.py`      | Load/validate/save YAML into typed objects.      |
| `moreader/plc.py`         | pylogix Logix driver + a simulated PLC.          |
| `moreader/scanner.py`     | Scanner inputs + model-extraction helper.        |
| `moreader/shift.py`       | Shift-change detection (clock + PLC bit edge).   |
| `moreader/controller.py`  | LOCKED/RUNNING/ALARM state machine.              |
| `moreader/worker.py`      | Background PLC thread (keeps the GUI responsive).|
| `moreader/gui.py`         | Tkinter/ttk GUI.                                 |
| `moreader/cli.py`         | Entry point (GUI default, `--headless` option).  |
| `scripts/gui_smoketest.py`| Headless GUI smoke test / screenshot driver.     |

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

The 25 tests run entirely against the simulated PLC — no hardware required.

## Adapting it

* **Light stack / different alarm output** — the controller only sets
  `run_permit`, `alarm`, and (optionally) `last_scan`; wire those into your
  ladder however you need.
* **Tag addressing** — pylogix uses named tags (`Program:MainProgram.ModelNumber`,
  `MyModel`, `array[3]`, UDT members, etc.); set them on the PLC config tab.

## Safety note

`moreader` is an interlock aid, not a safety-rated system. Keep all required
machine safety functions (E-stops, guarding, safety relays) independent of this
program. The configuration password is stored in plaintext in the YAML to keep
casual operators out of settings — it is not a security boundary against someone
with file access.
