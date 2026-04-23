"""Tests for the testable bits of smartups.tray.

Skips automatically if Pillow/pystray are not available — the main target is
the Raspberry Pi which has them installed via the project requirements.
"""
from __future__ import annotations

import pytest

tray = pytest.importorskip("smartups.tray")


def test_charging_color_is_blue_regardless_of_percent():
    assert tray._color_for(10.0, charging=True) == (30, 140, 255)
    assert tray._color_for(90.0, charging=True) == (30, 140, 255)


def test_discharging_color_by_percent():
    assert tray._color_for(15.0, charging=False) == (220, 40, 40)   # critical
    assert tray._color_for(20.0, charging=False) == (220, 40, 40)   # boundary critical
    assert tray._color_for(45.0, charging=False) == (230, 180, 20)  # low
    assert tray._color_for(50.0, charging=False) == (230, 180, 20)  # boundary low
    assert tray._color_for(80.0, charging=False) == (40, 180, 60)   # healthy


def test_render_icon_returns_rgba_64x64():
    img = tray._render_icon(50.0, charging=False)
    if img is None:  # pragma: no cover
        pytest.skip("Pillow not available in this environment")
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_tray_available_flag_is_bool():
    assert isinstance(tray.TrayIcon.available(), bool)
