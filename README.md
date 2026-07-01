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

When a scan is rejected, a red **error screen** pops up naming the exact
reason(s) — wrong scan length, an encapsulator whose recipe doesn't match, a
battery label that doesn't match the Assembled Battery MO, an unreadable
barcode, or an offline PLC — so the operator knows what to fix before scanning
again.

All PLC I/O runs on a background thread, so a slow or offline PLC never freezes
the interface.

## What moreader reads/writes

| PLC                 | Tag                | Dir   | Type | Purpose                                   |
| ------------------- | ------------------ | ----- | ---- | ----------------------------------------- |
| Encapsulator 1/2/3  | `recipe[0].Name`   | read  | DINT | recipe number the encapsulator is set to  |
| COS (master)        | `MO_Verified`      | write | BOOL | true only when all recipes match the scan |
| COS (master)        | `MO_Bypassed`      | write | BOOL | true when an operator bypasses (passworded; cleared at shift/lockout) |
| COS (master)        | `System.Mode.CycleStopReq` | write | BOOL | true on lockout/shift change for a graceful cycle stop |
| COS (master)        | `Heartbeat`        | write | BOOL/DINT | watchdog pulsed every `heartbeat_interval` s |

The heartbeat has two modes (Settings → PLCs → Heartbeat mode): **toggle**
pulses a **BOOL** ON/OFF (the classic watchdog — use this for a BOOL tag), or
**increment** counts up a **DINT**. Watchdog it in the PLC so the run permit
drops if the pulse ever stops. To verify it, watch the `Heartbeat` tag in Studio
5000 (it should flip ON/OFF each interval) or the pulsing ♥ on the master panel.

The heartbeat runs on its **own dedicated thread** with a drift-free schedule,
so its timing stays steady regardless of encapsulator reads, scans, or PLC
reconnect attempts happening on the main worker thread.

On a shift change or Manual Lockout, `CycleStopReq` is set true so the line
finishes its current cycle and stops gracefully; a successful verify (or bypass)
clears it.

### MO barcode formats

Different products use different MO barcode layouts, so the digits to compare are
chosen by a list of **formats** (Settings → MO Formats). The first rule whose
prefix matches the start of the scan wins; the blank-prefix rule is the default.
Each rule reads either the **last N digits** or **N characters at a position**:

| Starts with | Length | Read | Result (example) |
| ----------- | ------ | ---- | ---------------- |
| `F`  | 10  | last 4 | `F2220-1301` → `1301` |
| `GL` | any | 4 chars at pos 6 | `GL0007564-0000` → `7564` |
| (blank) | 9 | last 4 | `2220-1321` → `1321` |

The same list is used for both the Stuffed Element and Assembled Battery scans.

### Shared recipes (a list to search)

Each encapsulator's recipe tag can hold a **single number** or a
**dash-separated list** when several products share a recipe, e.g.
`1321-1333-8634-9121`. The scan matches that encapsulator if the scanned digits
equal **any** value in the list.

### Optional Assembled-Battery cross-check

Enable **Battery Scan** in the configuration to require two different MOs. VERIFY
MO then becomes a 3-step scan:

1. **Stuffed Element MO** — its last 4 digits are matched to the encapsulator
   recipes (the primary check).
2. **Assembled Battery MO** — a *different* MO; its last 4 digits are taken.
3. **Battery Label** — its first 4 digits must equal the Assembled Battery MO's
   last 4.

`MO_Verified` is set only when the encapsulators match step 1 **and** the battery
label matches step 2. Scan-length checks (each MO exactly 9 characters, the
battery label at least 10) prevent the same barcode being scanned twice.

## Scanners

Two scanner types are supported (Settings → Scanner):

* **keyboard** — HID keyboard-wedge (the scanner "types" the barcode). Its
  keystrokes are captured directly by the scan popup.
* **serial** — a virtual COM port (e.g. a **Zebra** scanner in USB-CDC mode).
  Set the port (`COM4`, `/dev/ttyACM0`, …) and baud rate; barcodes are read over
  pyserial and delivered to the scan popup automatically.

## Audit trail

Every scan and gate change is appended to a daily CSV in `audit.directory`
(`logs/moreader-YYYY-MM-DD.csv`): timestamp, event (VERIFY / BYPASS / LOCKOUT /
SHIFT_LOCKOUT / CHANGEOVER / PLC_OFFLINE…), result (PASS/FAIL), the MO(s), the
battery digits, and the failure reason. Disable it with `audit.enabled: false`.

## Kiosk / station deployment

Run with `--kiosk` for a factory station: full-screen, frameless, and the window
can only be closed via the password-gated **Exit App** button in Settings. A
single-instance lock prevents two copies fighting over the PLC writes.

```bash
python -m moreader --config config.yaml --kiosk
```

On Windows, set that command as a **Task Scheduler** task "at log on" with
"restart on failure" so the station relaunches after a crash or reboot. The
`Heartbeat` DINT must be watchdogged in the PLC so the run permit drops if this
application ever stops (see the safety note).

## Install

```bash
pip install -r requirements.txt
# Linux also needs the Qt runtime libraries, e.g. on Debian/Ubuntu:
#   sudo apt-get install libegl1 libgl1 libxkbcommon0 libfontconfig1
```

* `PySide6` — the GUI.  `pylogix` — EtherNet/IP comms.  `pyyaml` — config.
* `pyserial` — only for a serial (virtual COM port) scanner.

You can package it as a single `.exe` with PyInstaller so the station needs no
Python install.

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
| `moreader/audit.py`        | Daily CSV audit trail.                            |
| `moreader/cli.py`          | Entry point (GUI default, `--headless`, `--kiosk`).|
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
