"""Persistent audit trail.

Every verification attempt and gate change is appended to a daily CSV file so the
line has a traceable record (MO scanned, result, reason, operator actions).  A
new file is started each day: ``<directory>/moreader-YYYY-MM-DD.csv``.

The worker thread is the only writer, so no locking is required.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

FIELDS = ["timestamp", "event", "result", "stuffed_mo", "assembled_mo", "battery", "detail"]


class AuditLog:
    def __init__(self, directory: str | Path, enabled: bool = True) -> None:
        self.enabled = enabled
        self.directory = Path(directory)
        self._day: str | None = None
        self._path: Path | None = None
        if self.enabled:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # pragma: no cover - depends on filesystem
                log.error("Could not create audit directory %s: %s", self.directory, exc)
                self.enabled = False

    def _file_for_today(self) -> Path:
        day = datetime.now().strftime("%Y-%m-%d")
        path = self.directory / f"moreader-{day}.csv"
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(FIELDS)
        self._day, self._path = day, path
        return path

    def record(
        self,
        event: str,
        result: str = "",
        stuffed_mo: str | None = None,
        assembled_mo: str | None = None,
        battery: str | None = None,
        detail: str = "",
    ) -> None:
        """Append one row.  Never raises — auditing must not break the line."""

        if not self.enabled:
            return
        try:
            path = self._file_for_today()
            with path.open("a", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow([
                    datetime.now().isoformat(timespec="seconds"),
                    event,
                    result,
                    stuffed_mo or "",
                    assembled_mo or "",
                    battery or "",
                    detail,
                ])
        except OSError as exc:  # pragma: no cover - depends on filesystem
            log.error("Could not write audit row: %s", exc)

    @property
    def current_path(self) -> Path | None:
        return self._path
