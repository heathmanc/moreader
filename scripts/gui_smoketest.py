"""Headless smoke test for the PySide6 GUI.

Renders the operator screen and config screen to PNGs using the offscreen Qt
platform, and drives the simulated flow.  Run with:

    QT_QPA_PLATFORM=offscreen python3 scripts/gui_smoketest.py /tmp/moreader

"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from moreader.config import Config
from moreader.gui_qt import MainWindow, ScanDialog
from moreader.worker import CMD_VERIFY

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/moreader"


def pump(app, win, n=12):
    # Let the worker connect/emit and the GUI drain events a few times.
    import time
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
    cfgdir = tempfile.mkdtemp()
    win = MainWindow(Config(), Path(cfgdir) / "config.yaml", simulate=True)
    win.resize(1180, 760)
    win.show()
    pump(app, win)
    shot(win, f"{OUT}_operator_locked.png")

    # Scan that matches Machine 2 only (sim models 1001/1002/1003).
    win.worker.submit(CMD_VERIFY, "MO-2024-1002")
    pump(app, win)
    shot(win, f"{OUT}_operator_scanned.png")

    # Render the scan dialog.
    dlg = ScanDialog(win)
    dlg.field.setText("MO-2024-1002")
    dlg.show()
    QApplication.processEvents()
    dlg.grab().save(f"{OUT}_scan_dialog.png")
    print("wrote", f"{OUT}_scan_dialog.png")
    dlg.close()

    # Open configuration (bypass the password prompt for the screenshot).
    win.config_page = win._build_config_page()
    win.config_index = win.stack.addWidget(win.config_page)
    win._load_config_into_widgets()
    win.stack.setCurrentIndex(win.config_index)
    QApplication.processEvents()
    shot(win, f"{OUT}_config_machines.png")

    # Report final machine states.
    for m in win.worker.monitors:
        print(f"{m.name}: state={m.state.value} model={m.model} permit={m.link.run_permit}")
    win.worker.shutdown()


if __name__ == "__main__":
    main()
