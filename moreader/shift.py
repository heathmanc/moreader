"""Shift-change detection.

A shift change forces the operator to re-scan the manufacturing order before the
PLC is allowed to run again.  Two independent triggers are supported and may be
combined:

* **Time schedule** -- a list of clock times (``06:00``, ``14:00`` ...).  When
  the wall clock crosses one of these boundaries a new shift has begun.
* **PLC request** -- a rising edge on the PLC/HMI ``ShiftChangeRequest`` bit.

The :class:`ShiftDetector` is intentionally side-effect free: it just answers
"has a new shift started since I last checked?".  The controller decides what to
do about it.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Callable

from .config import ShiftConfig


def _parse_times(values: list[str]) -> list[time]:
    parsed: list[time] = []
    for raw in values:
        try:
            hh, mm = raw.strip().split(":")
            parsed.append(time(int(hh), int(mm)))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid shift start time {raw!r} (expected HH:MM)") from exc
    return sorted(parsed)


class ShiftDetector:
    """Tracks shift boundaries from the clock and/or a PLC request bit."""

    def __init__(self, cfg: ShiftConfig, now: Callable[[], datetime] = datetime.now) -> None:
        self.cfg = cfg
        self._now = now
        self._start_times = _parse_times(cfg.start_times)
        # Identify the shift currently in effect so we only fire on a *change*.
        self._current_shift = self._shift_index(self._now()) if self._start_times else None
        self._prev_request = False

    def _shift_index(self, dt: datetime) -> int | None:
        """Return the index of the most recent shift start at/before ``dt``.

        Returns ``None`` when ``dt`` is before the first start time of the day
        (i.e. still in the final shift carried over from the previous day).
        """

        if not self._start_times:
            return None
        now_t = dt.time()
        idx = None
        for i, start in enumerate(self._start_times):
            if now_t >= start:
                idx = i
        # Before the first boundary we belong to the last shift of the day.
        return idx if idx is not None else len(self._start_times) - 1

    def time_shift_changed(self) -> bool:
        """True if the clock has crossed into a new shift since last checked."""

        if not self._start_times:
            return False
        idx = self._shift_index(self._now())
        if idx != self._current_shift:
            self._current_shift = idx
            return True
        return False

    def plc_request_edge(self, request_active: bool) -> bool:
        """True on a rising edge of the PLC shift-change request bit."""

        if not self.cfg.watch_plc_request:
            return False
        edge = request_active and not self._prev_request
        self._prev_request = request_active
        return edge

    def check(self, plc_request_active: bool = False) -> bool:
        """Combined check: True if either trigger indicates a new shift."""

        time_changed = self.time_shift_changed()
        plc_edge = self.plc_request_edge(plc_request_active)
        return time_changed or plc_edge

    def current_shift_label(self) -> str:
        """Human-readable label for the active shift, for display/logging."""

        if not self._start_times or self._current_shift is None:
            return "shift"
        start = self._start_times[self._current_shift]
        return f"shift starting {start.strftime('%H:%M')}"
