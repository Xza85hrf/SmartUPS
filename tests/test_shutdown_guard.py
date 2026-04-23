"""Tests for smartups.shutdown_guard.

Uses an in-memory runner fake instead of executing any real shutdown command.
"""
from __future__ import annotations

from smartups.shutdown_guard import ShutdownGuard


class FakeRunner:
    def __init__(self, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, cmd: list[str]) -> int:
        self.calls.append(cmd)
        return self.rc


def _make_guard(**overrides):
    runner = overrides.pop("runner", FakeRunner())
    guard = ShutdownGuard(
        threshold_pct=overrides.pop("threshold_pct", 20.0),
        consecutive_required=overrides.pop("consecutive_required", 3),
        enabled=overrides.pop("enabled", True),
        runner=runner,
    )
    return guard, runner


def test_does_not_fire_when_charging_even_at_low_battery():
    guard, runner = _make_guard()
    for _ in range(10):
        fired = guard.observe(battery_pct=5.0, is_charging=True)
        assert not fired
    assert runner.calls == []


def test_does_not_fire_above_threshold():
    guard, runner = _make_guard(threshold_pct=20.0)
    for _ in range(10):
        fired = guard.observe(battery_pct=25.0, is_charging=False)
        assert not fired
    assert runner.calls == []


def test_fires_after_consecutive_critical_readings():
    guard, runner = _make_guard(consecutive_required=3)
    assert not guard.observe(battery_pct=15.0, is_charging=False)
    assert not guard.observe(battery_pct=15.0, is_charging=False)
    assert guard.observe(battery_pct=15.0, is_charging=False)
    assert len(runner.calls) == 1
    assert "shutdown" in runner.calls[0]


def test_resets_streak_on_non_critical_reading():
    guard, runner = _make_guard(consecutive_required=3)
    assert not guard.observe(battery_pct=15.0, is_charging=False)
    assert not guard.observe(battery_pct=15.0, is_charging=False)
    # Charging resumes — streak breaks
    assert not guard.observe(battery_pct=15.0, is_charging=True)
    # Back on battery — must need another full streak
    assert not guard.observe(battery_pct=15.0, is_charging=False)
    assert not guard.observe(battery_pct=15.0, is_charging=False)
    assert guard.observe(battery_pct=15.0, is_charging=False)
    assert len(runner.calls) == 1


def test_does_not_fire_twice():
    guard, runner = _make_guard(consecutive_required=2)
    guard.observe(battery_pct=5.0, is_charging=False)
    guard.observe(battery_pct=5.0, is_charging=False)
    # Already fired once — keep observing, no more calls
    for _ in range(10):
        guard.observe(battery_pct=5.0, is_charging=False)
    assert len(runner.calls) == 1


def test_disabled_never_fires():
    guard, runner = _make_guard(enabled=False)
    for _ in range(10):
        guard.observe(battery_pct=1.0, is_charging=False)
    assert runner.calls == []


def test_boundary_at_threshold_counts_as_critical():
    # "<= threshold" — exactly at threshold should be critical
    guard, runner = _make_guard(threshold_pct=20.0, consecutive_required=1)
    assert guard.observe(battery_pct=20.0, is_charging=False)
    assert len(runner.calls) == 1


def test_logs_runner_failure_but_state_is_fired():
    """If shutdown command fails, we still mark as fired to prevent retries."""
    runner = FakeRunner(rc=1)
    guard = ShutdownGuard(
        threshold_pct=20.0, consecutive_required=1, runner=runner,
    )
    assert guard.observe(battery_pct=5.0, is_charging=False)
    # Second observation should not retry
    guard.observe(battery_pct=5.0, is_charging=False)
    assert len(runner.calls) == 1
