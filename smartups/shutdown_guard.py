"""Graceful shutdown guard for SmartUPS.

Triggers `shutdown -h now` when the UPS is on battery and the charge level
drops below a threshold for N consecutive readings. The consecutive-reading
requirement prevents false positives from transient voltage dips.
"""
from __future__ import annotations

import logging
import subprocess
from collections import deque
from typing import Callable

log = logging.getLogger(__name__)


class ShutdownGuard:
    """Watch battery readings and trigger shutdown when sustained low.

    A reading is considered "critical" when the UPS is on battery (not
    charging) AND the battery percentage is at or below ``threshold_pct``.
    Shutdown fires after ``consecutive_required`` critical readings in a row.
    """

    def __init__(
        self,
        threshold_pct: float = 20.0,
        consecutive_required: int = 3,
        enabled: bool = True,
        shutdown_cmd: list[str] | None = None,
        runner: Callable[[list[str]], int] | None = None,
    ) -> None:
        self.threshold_pct = float(threshold_pct)
        self.consecutive_required = max(1, int(consecutive_required))
        self.enabled = bool(enabled)
        self.shutdown_cmd = shutdown_cmd or ["sudo", "shutdown", "-h", "now"]
        self._runner = runner or self._default_runner
        self._critical_streak: deque[bool] = deque(maxlen=self.consecutive_required)
        self._has_fired = False

    @staticmethod
    def _default_runner(cmd: list[str]) -> int:
        return subprocess.run(cmd, check=False).returncode

    def observe(self, battery_pct: float, is_charging: bool) -> bool:
        """Record a reading. Returns True if shutdown was triggered."""
        if not self.enabled or self._has_fired:
            return False

        is_critical = (not is_charging) and (battery_pct <= self.threshold_pct)
        self._critical_streak.append(is_critical)

        if (
            len(self._critical_streak) == self.consecutive_required
            and all(self._critical_streak)
        ):
            log.critical(
                "Battery at %.1f%% on battery for %d consecutive readings — "
                "initiating graceful shutdown.",
                battery_pct,
                self.consecutive_required,
            )
            self._has_fired = True
            rc = self._runner(self.shutdown_cmd)
            if rc != 0:
                log.error(
                    "Shutdown command %s returned non-zero exit code %d. "
                    "Verify that the SmartUPS service has passwordless sudo for "
                    "/sbin/shutdown.",
                    self.shutdown_cmd,
                    rc,
                )
            return True
        return False

    def reset(self) -> None:
        """Reset the streak and fire state (useful after test runs)."""
        self._critical_streak.clear()
        self._has_fired = False
