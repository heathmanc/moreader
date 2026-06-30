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
from moreader.gui_qt import MainWindow, ScanDialog
from moreader.worker import CMD_VERIFY

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mo"


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
    win = MainWindow(Config(), Path(tempfile.mkdtemp()) / "config.yaml", simulate=True)
    win.resize(1180, 780)
    win.show()
    pump(app, win)
    shot(win, f"{OUT}_op_locked.png")

    # All sim recipes default to 1001; scanning ...1001 should verify all -> master VERIFIED.
    win.worker.submit(CMD_VERIFY, "MO-2024-1001")
    pump(app, win)
    shot(win, f"{OUT}_op_verified.png")

    # A mismatching scan clears verification.
    win.worker.submit(CMD_VERIFY, "MO-2024-9999")
    pump(app, win)
    shot(win, f"{OUT}_op_mismatch.png")

    dlg = ScanDialog(win)
    dlg.field.setText("MO-2024-1001")
    dlg.show()
    QApplication.processEvents()
    dlg.grab().save(f"{OUT}_scan_dialog.png")
    print("wrote", f"{OUT}_scan_dialog.png")
    dlg.close()

    win.config_page = win._build_config_page()
    win.config_index = win.stack.addWidget(win.config_page)
    win._load_config_into_widgets()
    win.stack.setCurrentIndex(win.config_index)
    QApplication.processEvents()
    shot(win, f"{OUT}_config_plcs.png")

    for e in win.worker.encapsulators:
        print(f"{e.name}: state={e.state.value} recipe={e.recipe}")
    print(f"master: verified={win.worker.master.mo_verified} hb={win.worker.master.heartbeat}")
    win.worker.shutdown()


if __name__ == "__main__":
    main()
