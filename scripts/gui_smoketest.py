"""Headless smoke test for the PySide6 GUI (offscreen)."""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from moreader.config import Config
from moreader.gui_qt import MainWindow, ScanDialog, ScanErrorDialog
from moreader.worker import CMD_BYPASS, CMD_VERIFY

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mo"

MO_1001 = "000001001"      # 9-char MO ending in 1001


def pump(app, win, n=14):
    for _ in range(n):
        win._drain_events()
        app.processEvents()
        time.sleep(0.05)


def shot(win, name):
    win.repaint()
    QApplication.processEvents()
    win.grab().save(name)
    print("wrote", name)


def main():
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.secondary.enabled = True          # exercise the battery cross-check
    win = MainWindow(cfg, Path(tempfile.mkdtemp()) / "config.yaml", simulate=True)
    win.resize(1180, 800)
    win.show()
    pump(app, win)
    shot(win, f"{OUT}_op_locked.png")

    # Verified: stuffed MO + assembled MO + matching battery label.
    win.worker.submit(CMD_VERIFY, {"mo": MO_1001, "assembled_mo": "770001001", "battery": "1001ABCDEFGH"})
    pump(app, win)
    shot(win, f"{OUT}_op_verified.png")

    # Manual lockout -> cycle stop requested.
    win.worker.submit("lockout")
    pump(app, win)
    shot(win, f"{OUT}_op_lockout.png")

    # Three-step scan dialog (stuffed MO, assembled MO, battery label).
    steps = [
        ("mo", "SCAN STUFFED ELEMENT MO", "Scan the Stuffed Element MO. Its last 4 digits are matched to every encapsulator."),
        ("assembled_mo", "SCAN ASSEMBLED BATTERY MO", "Scan the Assembled Battery MO. Its last 4 digits are matched to the battery label."),
        ("battery", "SCAN BATTERY LABEL", "Scan the battery label. Its first 4 digits must match the Assembled Battery MO."),
    ]
    dlg = ScanDialog(steps, win)
    dlg.index = 2
    dlg._show_step()
    dlg._buffer = "1001ABCDEFGH"
    dlg.display.setText(dlg._buffer)
    dlg.show()
    QApplication.processEvents()
    dlg.grab().save(f"{OUT}_scan_dialog.png")
    print("wrote", f"{OUT}_scan_dialog.png")
    dlg.close()

    # Error screen (rendered directly).
    err = ScanErrorDialog(
        "SCAN VERIFICATION FAILED",
        [
            "Encapsulator 3: set to recipe 2002, but the MO ends in 1001.",
            "Battery label starts with 9999, but the Assembled Battery MO ends in 1001.",
        ],
        win,
    )
    err.show()
    QApplication.processEvents()
    err.grab().save(f"{OUT}_error.png")
    print("wrote", f"{OUT}_error.png")
    err.close()

    win.config_page = win._build_config_page()
    win.config_index = win.stack.addWidget(win.config_page)
    win._load_config_into_widgets()
    win.stack.setCurrentIndex(win.config_index)
    QApplication.processEvents()
    shot(win, f"{OUT}_config_plcs.png")

    print(f"master: verified={win.worker.master.mo_verified} bypass={win.worker.master.mo_bypassed} "
          f"cycle_stop={win.worker.master.cycle_stop} battery={win.worker.battery_matched}")
    win.worker.shutdown()


if __name__ == "__main__":
    main()
