"""Headless smoke test: build the GUI, drive the simulated flow, screenshot it.

Run under xvfb:  xvfb-run -s '-screen 0 1100x760x24' python3 scripts/gui_smoketest.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moreader.config import Config
from moreader.gui import MoreaderGUI
from moreader.worker import CMD_VERIFY

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/moreader_gui.png"


def shot(name):
    subprocess.run(f"import -window root {name}", shell=True, check=True)


def main():
    cfgdir = tempfile.mkdtemp()
    gui = MoreaderGUI(Config(), Path(cfgdir) / "config.yaml", simulate=True, expected_model="ABC-100")

    steps = {"n": 0}

    def tick():
        gui.root.update_idletasks()
        gui.root.update()

    def drive():
        # Let the worker connect and emit the initial LOCKED status.
        for _ in range(20):
            tick()
            gui.root.after(20)
        gui._pump_events()
        tick()

        # 1) Wrong scan -> ALARM.
        gui.commands.put((CMD_VERIFY, "WRONG-999"))
        for _ in range(15):
            tick(); gui.root.after(20)
        gui._pump_events(); tick()
        shot(OUT.replace(".png", "_alarm.png"))

        # 2) Correct scan -> RUN ENABLED.
        gui.commands.put((CMD_VERIFY, "ABC-100"))
        for _ in range(15):
            tick(); gui.root.after(20)
        gui._pump_events(); tick()
        shot(OUT.replace(".png", "_running.png"))

        # 3) Open Configuration, unlock, show the PLC tab with tags.
        gui.notebook.select(1)
        tick()
        gui.pw_var.set("2134chAP!@")
        gui._unlock()
        tick()
        for _ in range(5):
            tick(); gui.root.after(20)
        shot(OUT.replace(".png", "_config_plc.png"))

        print("BANNER:", gui.banner.cget("text"))
        print("EXPECTED:", gui.expected_var.get())
        print("LASTSCAN:", gui.lastscan_var.get())
        print("UNLOCKED:", gui.unlocked)
        gui._on_close()

    gui.root.after(200, drive)
    try:
        gui.root.mainloop()
    except Exception as exc:  # pragma: no cover
        print("ERROR:", exc)
        raise


if __name__ == "__main__":
    main()
