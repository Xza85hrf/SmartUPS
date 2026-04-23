"""Smoke tests for SmartUPS.py entry script.

Only things that are safe to exercise without real I2C hardware:
 - Argument parser contract (new flags are present, defaults are sane)
 - _get_cpu_temp fallback when no thermal sensor is exposed
 - _configure_logging produces a file handler

These cover the bits of SmartUPS.py we actually changed in the feature.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def script_module():
    src = Path(__file__).resolve().parents[1] / "SmartUPS.py"
    # Stub hardware modules so import succeeds in CI / dev environments.
    sys.modules.setdefault("smbus2", _StubModule())
    spec = importlib.util.spec_from_file_location("smartups_script", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class _StubModule:
    class SMBus:
        def __init__(self, *_a, **_k): ...
        def write_i2c_block_data(self, *_a, **_k): ...
        def read_i2c_block_data(self, *_a, **_k):
            return [0, 0]


def test_arg_parser_has_new_flags(script_module):
    p = script_module._build_arg_parser()
    # Parse a minimal valid arg line and verify defaults for the new flags.
    ns = p.parse_args([])
    assert ns.daemon is False
    assert ns.tray is False
    assert ns.no_shutdown is False
    assert ns.shutdown_threshold == 20.0
    assert ns.shutdown_consecutive == 3
    assert ns.show_plot is False


def test_arg_parser_accepts_custom_values(script_module):
    p = script_module._build_arg_parser()
    ns = p.parse_args([
        "--daemon", "--tray", "--no-shutdown",
        "--shutdown-threshold", "15",
        "--shutdown-consecutive", "5",
        "--log-interval", "10",
    ])
    assert ns.daemon is True
    assert ns.tray is True
    assert ns.no_shutdown is True
    assert ns.shutdown_threshold == 15.0
    assert ns.shutdown_consecutive == 5
    assert ns.log_interval == 10


def test_configure_logging_writes_to_file(tmp_path, script_module):
    log_file = tmp_path / "smartups.log"
    script_module._configure_logging(daemon=True, log_file=str(log_file))
    logging.getLogger("smartups").info("hello-world-marker")
    for h in logging.getLogger().handlers:
        h.flush()
    assert log_file.exists()
    assert "hello-world-marker" in log_file.read_text()


def test_get_cpu_temp_returns_none_when_no_sensor(script_module):
    with patch.object(script_module.psutil, "sensors_temperatures", return_value={}):
        assert script_module._get_cpu_temp() is None


def test_get_cpu_temp_returns_first_thermal_reading(script_module):
    class _Reading:
        current = 47.5

    with patch.object(
        script_module.psutil, "sensors_temperatures",
        return_value={"cpu_thermal": [_Reading()]},
    ):
        assert script_module._get_cpu_temp() == 47.5
