"""Process lifecycle guard for KWMusic automation scripts."""

from __future__ import annotations

import datetime as dt
from typing import Any


class ProcessGuard:
    """Track restart/close behavior and enforce stop conditions."""

    def __init__(self, max_restart_total: int = 3, max_consecutive_closes: int = 3) -> None:
        self.max_restart_total = max(1, int(max_restart_total))
        self.max_consecutive_closes = max(1, int(max_consecutive_closes))

        self.restart_attempts = 0
        self.consecutive_closes = 0
        self.total_closes = 0
        self.current_pid: int | None = None
        self.current_start_time: str | None = None
        self.stop_reason: str | None = None
        self.limit_reason: str | None = None
        self.events: list[dict[str, Any]] = []

    def _now(self) -> str:
        return dt.datetime.now().astimezone().isoformat()

    def _record(self, event: str, **kwargs: Any) -> None:
        item = {"time": self._now(), "event": event}
        item.update(kwargs)
        self.events.append(item)

    def observe_start(self, pid: int, start_time: Any = None) -> None:
        self.current_pid = int(pid)
        self.current_start_time = str(start_time) if start_time is not None else None
        self._record("observe_start", pid=self.current_pid, start_time=self.current_start_time)

    def observe_exit(self, pid: int | None, reason: str, by_script: bool) -> None:
        self.total_closes += 1
        if by_script:
            self.consecutive_closes += 1
        else:
            self.consecutive_closes = 0

        self._record(
            "observe_exit",
            pid=pid,
            reason=reason,
            by_script=bool(by_script),
            consecutive_closes=self.consecutive_closes,
            total_closes=self.total_closes,
        )

        if self.consecutive_closes >= self.max_consecutive_closes and self.stop_reason is None:
            self.limit_reason = "consecutive_close_limit_exceeded"
            self.stop_reason = "script_induced_crash_suspected"
            self._record("stop", stop_reason=self.stop_reason, limit_reason=self.limit_reason)

    def mark_stable(self, reason: str = "stable_observation") -> None:
        if self.consecutive_closes != 0:
            self._record("mark_stable_reset", reason=reason, prev_consecutive=self.consecutive_closes)
        else:
            self._record("mark_stable", reason=reason)
        self.consecutive_closes = 0

    def register_restart_attempt(self, trigger: str) -> None:
        self.restart_attempts += 1
        self._record("register_restart_attempt", trigger=trigger, restart_attempts=self.restart_attempts)
        # Allow up to max_restart_total attempts; stop only after exceeding the limit.
        if self.restart_attempts > self.max_restart_total and self.stop_reason is None:
            self.limit_reason = "restart_limit_exceeded"
            self.stop_reason = "restart_limit_exceeded"
            self._record("stop", stop_reason=self.stop_reason, limit_reason=self.limit_reason)

    def can_restart(self) -> bool:
        if self.stop_reason is not None:
            return False
        if self.restart_attempts >= self.max_restart_total:
            self.limit_reason = "restart_limit_exceeded"
            self.stop_reason = "restart_limit_exceeded"
            self._record("stop", stop_reason=self.stop_reason, limit_reason=self.limit_reason)
            return False
        return True

    def should_stop(self) -> bool:
        return self.stop_reason is not None

    def summary(self) -> dict[str, Any]:
        return {
            "max_restart_total": self.max_restart_total,
            "max_consecutive_closes": self.max_consecutive_closes,
            "restart_attempts": self.restart_attempts,
            "consecutive_closes": self.consecutive_closes,
            "total_closes": self.total_closes,
            "current_pid": self.current_pid,
            "current_start_time": self.current_start_time,
            "stop_reason": self.stop_reason,
            "limit_reason": self.limit_reason,
            "events": self.events[-80:],
        }
