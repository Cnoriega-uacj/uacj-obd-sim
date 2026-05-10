"""
Extended mfg PID library: Ford / GM / Toyota / Nissan additions.
"""

from __future__ import annotations

import math

import pytest

from uacj_obd.pids import load_default_registry
from uacj_obd.simulator.encoders import (
    active_make,
    encodable_mfg_pids,
    encode_mfg_pid,
    select_make,
)


@pytest.fixture(scope="module")
def registry():
    return load_default_registry()


@pytest.fixture(autouse=True)
def reset_make():
    select_make("default")
    yield
    select_make("default")


# Each tuple: (pid_key, value, tolerance) — tolerance = 0 means exact match
NEW_PIDS_DEFAULT = [
    # Ford
    ("221108", 1, 0),                        # A/C compressor on
    ("221156", 80, 1.0),                     # fuel pump duty 80%
    ("221157", 4, 0),                        # 4th gear
    # GM
    ("22000C", 250.0, 0.25),                 # tank pressure 250 Pa
    ("22115B", 3, 0),                        # 3rd gear
    ("22100C", 96, 0),                       # baro 96 kPa
    # Toyota
    ("220102", 60, 1.0),                     # hybrid SOC 60%
    ("220103", 75, 1.0),                     # inverter temp 75 °C
    # Honda (already covered, but spot-check one to ensure we didn't regress)
    ("220144", 287.4, 0.1),                  # VTEC oil pressure
    # Nissan (only non-colliding key in default bank)
    ("221102", 92, 1.0),                     # CVT fluid temp 92 °C
]


@pytest.mark.parametrize("pid_key,value,tolerance", NEW_PIDS_DEFAULT)
def test_default_bank_round_trip(registry, pid_key, value, tolerance):
    encoded = encode_mfg_pid(pid_key, value)
    assert encoded is not None, f"no encoder for {pid_key} in default bank"
    decoded = registry.decode(pid_key, encoded)
    assert decoded is not None, f"registry can't decode {pid_key}"
    if tolerance == 0:
        assert int(decoded) == int(value)
    else:
        assert math.isclose(float(decoded), float(value), abs_tol=tolerance + 1e-6), (
            f"{pid_key}: encoded {value} → bytes {encoded.hex()} → decoded {decoded}"
        )


def test_select_make_switches_encoder_bank():
    select_make("nissan")
    assert active_make() == "nissan"
    # In Nissan bank, 0x221101 should encode CVT ratio (×1000), not Ford runtime.
    encoded = encode_mfg_pid("221101", 0.523)
    assert encoded is not None
    raw = (encoded[0] << 8) | encoded[1]
    assert abs(raw - 523) <= 1, f"expected ~523 (0.523 × 1000), got {raw}"


def test_select_make_unknown_falls_back_to_default():
    select_make("kia")
    assert active_make() == "default"


def test_default_bank_contains_at_least_15_pids():
    pids = encodable_mfg_pids()
    assert len(pids) >= 15, f"expected ≥15 mfg PIDs in default bank, got {len(pids)}: {sorted(pids)}"


def test_toyota_bank_replaces_honda_knock_retard_with_accel_pedal():
    select_make("toyota")
    # 0x220156 in Toyota bank = accel pedal (% scaled), not knock retard.
    encoded = encode_mfg_pid("220156", 50)  # 50%
    assert encoded is not None
    # Toyota formula: b[0] * 100/255 → 50% is ~127
    assert abs(encoded[0] - 127) <= 2
