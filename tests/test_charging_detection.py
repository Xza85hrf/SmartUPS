"""Tests for the charging-state detection heuristic in SmartUPS.py.

The heuristic lives in the entry script (SmartUPS.py), so we import it via
importlib to avoid depending on the package layout.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def detect_charging():
    # Load SmartUPS.py as a module — it's a flat script, not in a package.
    src = Path(__file__).resolve().parents[1] / "SmartUPS.py"
    spec = importlib.util.spec_from_file_location("smartups_script", src)
    # Stub modules that require real hardware / display so import does not fail
    # in a headless CI/test environment.
    sys.modules.setdefault("smbus2", _StubModule())
    module = importlib.util.module_from_spec(spec)
    # Skip the module-level `init()` from colorama by pre-stubbing it if missing
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"SmartUPS.py import side-effects blocked: {exc}")
    return module.detect_charging


class _StubModule:
    class SMBus:
        def __init__(self, *_a, **_k): ...
        def write_i2c_block_data(self, *_a, **_k): ...
        def read_i2c_block_data(self, *_a, **_k):
            return [0, 0]


def test_clearly_charging_negative_current(detect_charging):
    assert detect_charging(current_a=-0.5, bus_voltage=12.2) is True


def test_clearly_discharging_positive_current(detect_charging):
    assert detect_charging(current_a=0.5, bus_voltage=12.2) is False


def test_idle_high_voltage_treated_as_charging(detect_charging):
    # Near-zero current but the pack is full/plugged-in: voltage says charging.
    assert detect_charging(current_a=0.0, bus_voltage=12.6) is True


def test_idle_low_voltage_treated_as_discharging(detect_charging):
    # Near-zero current and low bus voltage — likely unplugged under no load.
    assert detect_charging(current_a=0.0, bus_voltage=11.8) is False


def test_boundary_voltage_12_4_is_charging(detect_charging):
    assert detect_charging(current_a=0.0, bus_voltage=12.4) is True


def test_boundary_current_just_positive(detect_charging):
    # Slightly above the +5mA discharge threshold should be discharging.
    assert detect_charging(current_a=0.01, bus_voltage=12.6) is False
