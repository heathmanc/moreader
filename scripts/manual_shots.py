"""Render the operator screenshots used in the operator manual.

Run:  QT_QPA_PLATFORM=offscreen python3 scripts/manual_shots.py /tmp/manual
"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QInputDialog, QLineEdit, QWidget

from moreader.config import Config
from moreader.gui_qt import EncapsulatorTile, MainWindow, ScanDialog, ScanErrorDialog, STYLESHEET
from moreader.worker import CMD_BYPASS, CMD_VERIFY, PLCWorker

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/manual")
OUT.mkdir(parents=True, exist_ok=True)

# Sample barcodes used throughout the manual.
STUFFED_MO = "2220-1321"          # last 4 = 1321
ASSEMBLED_MO = "2220-1964"        # last 4 = 1964
BATTERY_LABEL = "1964W1261820308"  # first 4 = 1964
RECIPE = 1321                      # machines set to run 1321 (matches stuffed last 4)

STEPS = [
    ("mo", "SCAN STUFFED ELEMENT MO", "Scan the Stuffed Element MO barcode."),
    ("assembled_mo", "SCAN ASSEMBLED BATTERY MO", "Scan the Assembled Battery MO barcode."),
    ("battery", "SCAN BATTERY LABEL", "Scan the battery label barcode."),
]


def pump(app, win, n=16):
    for _ in range(n):
        win._drain_events()
        app.processEvents()
        time.sleep(0.05)


def save(widget, name):
    widget.repaint()
    QApplication.processEvents()
    widget.grab().save(str(OUT / name))
    print("wrote", name)


def composite(main_pix: QPixmap, dlg, name: str) -> None:
    """Draw the scan pop-up centered over a dimmed copy of the main screen."""

    dlg.adjustSize()
    QApplication.processEvents()
    dlg_pix = dlg.grab()
    result = QPixmap(main_pix)
    painter = QPainter(result)
    painter.fillRect(result.rect(), QColor(0, 0, 0, 130))     # dim the background
    x = (result.width() - dlg_pix.width()) // 2
    y = (result.height() - dlg_pix.height()) // 2
    painter.drawPixmap(x, y, dlg_pix)
    painter.end()
    result.save(str(OUT / name))
    print("wrote", name)


def main():
    app = QApplication.instance() or QApplication([])

    win = MainWindow(Config(), Path(tempfile.mkdtemp()) / "config.yaml", simulate=True)
    win.resize(1200, 800)
    # Set the simulated machines to recipe 1321 so the screen matches the samples.
    win.worker.shutdown()
    win.worker = PLCWorker(win.config, simulate=True, sim_recipes=[RECIPE, RECIPE, RECIPE], events=win.events)
    win.worker.start()
    win.show()
    pump(app, win)

    # 1) Main screen — waiting for a scan.
    save(win, "01_main_locked.png")
    main_pix = win.grab()          # snapshot of the locked screen for the composites

    # 2/3/4) Full-window shots of each scan step with the sample barcodes.
    for idx, (value, fname) in enumerate([
        (STUFFED_MO, "08_scan_step1.png"),
        (ASSEMBLED_MO, "09_scan_step2.png"),
        (BATTERY_LABEL, "10_scan_step3.png"),
    ]):
        dlg = ScanDialog(STEPS, win)
        dlg.index = idx
        dlg._show_step()
        dlg._buffer = value
        dlg.display.setText(value)
        dlg.show()
        QApplication.processEvents()
        composite(main_pix, dlg, fname)
        dlg.close()

    # 5) A good scan (all machines match 1321).
    win.worker.submit(CMD_VERIFY, STUFFED_MO)
    pump(app, win)
    save(win, "02_main_verified.png")

    # 6) The three tile states (legend).
    legend = QWidget()
    legend.setStyleSheet(STYLESHEET)
    legend.resize(1200, 430)
    lay = QHBoxLayout(legend)
    lay.setContentsMargins(22, 22, 22, 22)
    lay.setSpacing(16)
    for d in [
        {"name": "Good", "state": "MATCH", "recipe": 1321, "scanned": "1321", "connected": True},
        {"name": "Bad", "state": "MISMATCH", "recipe": 1999, "scanned": "1321", "connected": True},
        {"name": "Waiting", "state": "LOCKED", "recipe": 1321, "scanned": None, "connected": True},
    ]:
        t = EncapsulatorTile(d["name"])
        t.update_from(d)
        lay.addWidget(t, 1)
    legend.show()
    QApplication.processEvents()
    save(legend, "03_tile_states.png")

    # 7) The error screen (bad scan).
    err = ScanErrorDialog(
        "SCAN VERIFICATION FAILED",
        ["Encapsulator 3: set to recipe 1999, but the MO ends in 1321.",
         "Fix the machine setup or scan the correct order."],
        win,
    )
    err.show()
    QApplication.processEvents()
    save(err, "05_error_screen.png")
    err.close()

    # 8) The bypass password box.
    box = QInputDialog(win)
    box.setStyleSheet(STYLESHEET)
    box.setWindowTitle("Bypass — password required")
    box.setLabelText("Enter password to enable MO bypass:")
    box.setTextEchoMode(QLineEdit.Password)
    box.setTextValue("••••••••")
    box.resize(460, 160)
    box.show()
    QApplication.processEvents()
    save(box, "06_bypass_password.png")
    box.close()

    # 9) Bypass active (master turns purple).
    win.worker.submit(CMD_BYPASS, "on")
    pump(app, win)
    save(win, "07_bypass_active.png")

    win._allow_close = True
    win.worker.shutdown()
    print("done")


if __name__ == "__main__":
    main()
