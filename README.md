# moreader

Manufacturing-order scan verification for an Allen Bradley PLC.

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

## How it works

1. On startup (and at every shift change) the run permit is cleared, so the PLC
   cannot run the product.
2. The operator scans the manufacturing order. Most USB scanners are
   *keyboard-wedge* (HID) devices that "type" the barcode + Enter, so the scan
   arrives on standard input.
3. `moreader` reads the PLC's expected model tag, normalises both strings, and
   compares them.
   * **Match** → sets `run_permit_tag = True`, clears the alarm. The machine can run.
   * **Mismatch** → sets `alarm_tag = True`, leaves the run permit cleared, and
     waits for another scan.
4. While running, `moreader` watches for the next shift change — either a
   configured clock boundary (e.g. 06:00 / 14:00 / 22:00) or a rising edge on a
   PLC/HMI `ShiftChangeRequest` bit — and locks out again when it occurs.

Your PLC ladder gates the machine on the `run_permit_tag` bit. `moreader` only
sets/clears bits and reads the model tag; it does not command motion directly.

## Install

```bash
pip install -r requirements.txt
```

* `pycomm3` — EtherNet/IP comms to Logix and SLC/MicroLogix PLCs.
* `pyyaml` — config file.
* `pyserial` — only needed for a serial (USB-CDC) scanner.

## Configure

Copy `config.example.yaml` to `config.yaml` and edit it for your line — PLC IP,
driver, tag names, scanner type, shift times. The example file documents every
field, including SLC/MicroLogix file-style addressing (`ST10:0`, `B3:0/0`).

Key tags to create in the PLC:

| Purpose                | Config key            | Type   |
| ---------------------- | --------------------- | ------ |
| Model the PLC is set to run | `expected_model_tag`  | STRING |
| Run permit (gate motion on this) | `run_permit_tag` | BOOL  |
| Scan-mismatch alarm    | `alarm_tag`           | BOOL   |
| Shift-change request (optional) | `shift_request_tag` | BOOL |
| Last-scan echo for HMI (optional) | `last_scan_tag` | STRING |

## Run

```bash
# Against real hardware
python -m moreader --config config.yaml

# No hardware — simulated PLC, type scans at the prompt
python -m moreader --simulate --expected-model ABC-100
```

Try the simulated mode: scan (type) a wrong value to see the alarm, then the
matching model to enable the run.

### Extracting the model from a richer barcode

If the MO barcode encodes more than the model — e.g.
`MO12345|MODEL=ABC-100|QTY=500` — set a regex with a named `model` group in the
config:

```yaml
scanner:
  scan_pattern: 'MODEL=(?P<model>[^|]+)'
```

## Project layout

| File                    | Responsibility                                  |
| ----------------------- | ----------------------------------------------- |
| `moreader/config.py`    | Load & validate YAML config into typed objects. |
| `moreader/plc.py`       | PLC drivers: Logix, SLC, and a simulated one.   |
| `moreader/scanner.py`   | Scanner inputs: keyboard-wedge, serial, iterable. |
| `moreader/shift.py`     | Shift-change detection (clock + PLC bit edge).  |
| `moreader/controller.py`| State machine tying scan ↔ PLC together.        |
| `moreader/cli.py`       | Command-line entry point / wiring.              |

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

Tests run entirely against the simulated PLC and a fake scanner — no hardware
required.

## Adapting it

* **Light stack / HMI popup instead of console** — subclass
  `moreader.controller.Notifier` and pass it to `ShiftChangeController`.
* **Different gating logic** — the controller only sets `run_permit_tag`,
  `alarm_tag`, and (optionally) `last_scan_tag`; wire those into your ladder
  however you need.

## Safety note

`moreader` is an interlock aid, not a safety-rated system. Keep all required
machine safety functions (E-stops, guarding, safety relays) independent of this
program.
