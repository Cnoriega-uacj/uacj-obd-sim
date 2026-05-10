"""
Honda mode 0x22 PID coverage.

Cristopher noted Honda is the most-frequently-diagnosed make at UACJ,
so we ship a broader Honda starter map than the other manufacturers.
Each new Honda PID needs an encoder (so the simulator can answer it
when a scenario sets a value) and a decoder formula in the YAML
registry (so the laptop can decode captures from real Hondas).

These tests prove every new Honda PID round-trips: encoded value →
decoded value comes back within the resolution of the formula.
"""

from __future__ import annotations

import math

import pytest

from uacj_obd.pids import load_default_registry
from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.encoders import encode_mfg_pid


HONDA_PIDS = [
    ("22015C", 95, 1.0),       # ATF temp 95 °C, 1 °C resolution
    ("220144", 350.0, 0.1),    # VTEC oil pressure 350.0 kPa, 0.1 resolution
    ("220123", 1, 0),          # brake pedal pressed (binary)
    ("22012F", 750, 1),        # target idle 750 RPM (1 RPM resolution)
    ("220156", -3.5, 0.5),     # knock retard -3.5° (0.5° resolution)
    ("22011A", 4500, 1),       # fuel pressure 4500 kPa
]


@pytest.fixture(scope="module")
def registry():
    return load_default_registry()


@pytest.mark.parametrize("pid_key,value,tolerance", HONDA_PIDS)
def test_honda_pid_round_trip(registry, pid_key, value, tolerance):
    encoded = encode_mfg_pid(pid_key, value)
    assert encoded is not None, f"no encoder for {pid_key}"
    decoded = registry.decode(pid_key, encoded)
    assert decoded is not None, f"registry can't decode {pid_key}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        assert math.isclose(float(decoded), float(value), abs_tol=tolerance + 1e-6), (
            f"{pid_key}: encoded {value} → bytes {encoded.hex()} → decoded {decoded}"
        )
    else:
        assert decoded == value, f"{pid_key}: expected {value!r}, got {decoded!r}"


def test_ecu_answers_new_honda_pid_via_mode_22():
    state = ScenarioState(live={"220144": 287.4})  # VTEC oil pressure
    ecu = EcuEmulator(state)
    response = ecu.handle(bytes([0x22, 0x01, 0x44]))
    assert response[:3] == bytes([0x62, 0x01, 0x44])  # positive response header
    raw = (response[3] << 8) | response[4]
    assert math.isclose(raw * 0.1, 287.4, abs_tol=0.1)


def test_ecu_returns_nrc_for_unmapped_honda_pid():
    state = ScenarioState(live={})
    ecu = EcuEmulator(state)
    # 0x22 0x01 0x44 with no live value → request out of range
    response = ecu.handle(bytes([0x22, 0x01, 0x44]))
    assert response[0] == 0x7F
    assert response[1] == 0x22
