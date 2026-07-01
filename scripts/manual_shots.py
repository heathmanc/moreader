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

from PySide6.QtWidgets import QApplication, QHBoxLayout, QInputDialog, QLineEdit, QWidget

from moreader.config import Config
from moreader.gui_qt import EncapsulatorTile, MainWindow, ScanDialog, ScanErrorDialog, STYLESHEET
from moreader.worker import CMD_BYPASS, CMD_VERIFY

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/manual")
OUT.mkdir(parents=True, exist_ok=True)
MO = "000001001"


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


def main():
    app = QApplication.instance() or QApplication([])

    # 1) Main operator screen — waiting for a scan (locked).
    win = MainWindow(Config(), Path(tempfile.mkdtemp()) / "config.yaml", simulate=True)
    win.resize(1200, 800)
    win.show()
    pump(app, win)
    save(win, "01_main_locked.png")

    # 2) Main operator screen — a good scan (all match, line enabled).
    win.worker.submit(CMD_VERIFY, MO)
    pump(app, win)
    save(win, "02_main_verified.png")

    # 3) The three tile states side by side (legend).
    legend = QWidget()
    legend.setStyleSheet(STYLESHEET)
    legend.resize(1200, 430)
    lay = QHBoxLayout(legend)
    lay.setContentsMargins(22, 22, 22, 22)
    lay.setSpacing(16)
    for d in [
        {"name": "Good", "state": "MATCH", "recipe": 1001, "scanned": "1001", "connected": True},
        {"name": "Bad", "state": "MISMATCH", "recipe": 2002, "scanned": "1001", "connected": True},
        {"name": "Waiting", "state": "LOCKED", "recipe": 1001, "scanned": None, "connected": True},
    ]:
        t = EncapsulatorTile(d["name"])
        t.update_from(d)
        lay.addWidget(t, 1)
    legend.show()
    QApplication.processEvents()
    save(legend, "03_tile_states.png")

    # 4) The scan pop-up (single MO).
    dlg = ScanDialog([("mo", "SCAN MANUFACTURING ORDER",
                       "Scan the MO barcode. The last 4 digits are matched to every encapsulator.")], win)
    dlg._buffer = "0572WD261351001"
    dlg.display.setText(dlg._buffer)
    dlg.show()
    QApplication.processEvents()
    save(dlg, "04_scan_popup.png")
    dlg.close()

    # 5) The error screen (bad scan).
    err = ScanErrorDialog(
        "SCAN VERIFICATION FAILED",
        ["Encapsulator 3: set to recipe 2002, but the MO ends in 1001.",
         "Fix the machine setup or scan the correct order."],
        win,
    )
    err.show()
    QApplication.processEvents()
    save(err, "05_error_screen.png")
    err.close()

    # 6) The bypass password box.
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

    # 7) Bypass active (master turns purple).
    win.worker.submit(CMD_BYPASS, "on")
    pump(app, win)
    save(win, "07_bypass_active.png")

    win._allow_close = True
    win.worker.shutdown()
    print("done")


if __name__ == "__main__":
    main()
