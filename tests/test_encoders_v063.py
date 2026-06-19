"""
v0.6.3 — Edge-case + manufacturer-PID coverage for `simulator/encoders.py`.

v0.6.2 left this module at 83%. The uncovered lines were:

  - Defensive `int()` / `str()` exception branches in `_enc_*` helpers
    (fuel_system_status, byte_passthrough, brake_switch, bool_byte).
  - Manufacturer-PID encoder functions (Ford, GM, Toyota, Honda, Nissan)
    — never exercised directly by any test before this.
  - `select_make()` bank switching + `active_make()` reporting.
  - `encode_mfg_pid()` error paths (unknown key, encoder raises, None
    value).

This module covers each of those. Standard SAE J1979 formulas were
already locked in by `test_encoder_expansion_v0_4_11.py`; this is the
manufacturer-bank counterpart.
"""

from __future__ import annotations

import pytest

from uacj_obd.simulator.encoders import (
    active_make,
    encodable_mfg_pids,
    encode_mfg_pid,
    encode_pid,
    select_make,
)


# ---------------------------------------------------------------------------
# Defensive branches in standard PID encoders
# ---------------------------------------------------------------------------

def test_fuel_system_status_string_value_falls_back_to_closed_loop() -> None:
    """0103 accepts an int but defaults to 0x02 (closed loop) when
    given a non-numeric string."""
    encoded = encode_pid("0103", "not a number")
    assert encoded == bytes([0x02, 0x00])


def test_fuel_system_status_none_value_returns_none() -> None:
    """Per `encode_pid`'s contract: value=None → None response."""
    assert encode_pid("0103", None) is None


def test_byte_passthrough_string_value_falls_back_to_zero() -> None:
    """0x12 / 0x13 / 0x1C / 0x1D / 0x51 accept enums; non-numeric
    string falls back to 0."""
    # PID 0x13 is one of these byte-passthrough encoders.
    encoded = encode_pid("0113", "not a number")
    assert encoded == bytes([0x00])


def test_o2_wide_range_zero_value_uses_stoichiometric_default() -> None:
    """If the scenario passes 0 for a wide-range O2 PID, the encoder
    defaults to a stoichiometric equivalence ratio (1.0 → raw 32768)."""
    encoded = encode_pid("0124", 0)
    assert encoded is not None
    assert len(encoded) == 4
    # First two bytes = 1.0 * 32768 = 0x8000
    ratio_raw = (encoded[0] << 8) | encoded[1]
    assert ratio_raw == 0x8000


# ---------------------------------------------------------------------------
# Manufacturer encoders — exercise each function via `encode_mfg_pid`
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_make_bank():
    """Each test starts with the default make bank so they don't leak
    state into each other."""
    select_make("default")
    yield
    select_make("default")


def test_ford_trans_oil_temp_round_trip() -> None:
    # Formula: (b[0]*256 + b[1])/10 - 40 → °C; 80°C → raw 1200 → 0x04B0
    encoded = encode_mfg_pid("22115C", 80.0)
    assert encoded == bytes([0x04, 0xB0])


def test_ford_key_on_runtime_round_trip() -> None:
    # PID 0x221101: seconds, u16 big-endian (in the default Ford bank)
    encoded = encode_mfg_pid("221101", 3600)
    assert encoded == bytes([0x0E, 0x10])


def test_ford_ac_compressor_truthy_int() -> None:
    """A/C compressor PID returns 0 or 1 based on truthiness."""
    assert encode_mfg_pid("221108", 1) == bytes([0x01])
    assert encode_mfg_pid("221108", 0) == bytes([0x00])


def test_ford_ac_compressor_string_yes_no() -> None:
    """The bool encoder accepts 'on' / 'true' / 'yes' as truthy strings."""
    assert encode_mfg_pid("221108", "on") == bytes([0x01])
    assert encode_mfg_pid("221108", "off") == bytes([0x00])
    assert encode_mfg_pid("221108", "yes") == bytes([0x01])


def test_ford_fuel_pump_duty_pct() -> None:
    """Duty as 0-100% mapped to 0-255."""
    encoded = encode_mfg_pid("221156", 50.0)
    assert encoded == bytes([round(50 * 255 / 100)])


def test_ford_gear_pass_through() -> None:
    """Gear is a single-byte passthrough."""
    assert encode_mfg_pid("221157", 4) == bytes([0x04])


def test_gm_oil_life_pct() -> None:
    """Oil life percent → byte."""
    encoded = encode_mfg_pid("220005", 75.0)
    assert encoded == bytes([round(75 * 255 / 100)])


def test_gm_trans_fluid_temp_offset40() -> None:
    """Trans fluid temp = A - 40."""
    encoded = encode_mfg_pid("22115A", 95)
    assert encoded == bytes([135])  # 95 + 40


def test_gm_fuel_tank_pressure_round_trip() -> None:
    """Formula: (b[0]*256 + b[1]) * 0.25 - 8192 → Pa."""
    # 0 Pa → raw = 8192 / 0.25 = 32768 = 0x8000
    encoded = encode_mfg_pid("22000C", 0.0)
    assert encoded == bytes([0x80, 0x00])


def test_gm_gear_pass_through() -> None:
    assert encode_mfg_pid("22115B", 6) == bytes([0x06])


def test_gm_baro_pass_through() -> None:
    assert encode_mfg_pid("22100C", 101) == bytes([0x65])


def test_toyota_engine_runtime_minutes() -> None:
    """Toyota PID 0x220101 = engine runtime in minutes, u16 BE."""
    encoded = encode_mfg_pid("220101", 60)
    assert encoded == bytes([0x00, 0x3C])


def test_toyota_hybrid_soc_pct() -> None:
    """Hybrid SOC percent."""
    encoded = encode_mfg_pid("220102", 80.0)
    assert encoded == bytes([round(80 * 255 / 100)])


def test_toyota_inverter_temp_offset40() -> None:
    encoded = encode_mfg_pid("220103", 65)
    assert encoded == bytes([105])


def test_honda_atf_temp_offset40() -> None:
    encoded = encode_mfg_pid("22015C", 90)
    assert encoded == bytes([130])


def test_honda_vtec_oil_press_round_trip() -> None:
    """Inverse of b[0:2] * 0.1 → kPa; 300 kPa → raw = 3000 → 0x0BB8"""
    encoded = encode_mfg_pid("220144", 300.0)
    assert encoded == bytes([0x0B, 0xB8])


def test_honda_brake_switch_truthy_int() -> None:
    assert encode_mfg_pid("220123", 1) == bytes([0x01])
    assert encode_mfg_pid("220123", 0) == bytes([0x00])


def test_honda_brake_switch_string_pressed() -> None:
    """Honda's brake-switch encoder accepts 'on' / 'true' / 'pressed'."""
    assert encode_mfg_pid("220123", "pressed") == bytes([0x01])
    assert encode_mfg_pid("220123", "released") == bytes([0x00])


def test_honda_target_idle_round_trip() -> None:
    """Inverse of b[0:2] → RPM; 800 RPM → 0x0320"""
    encoded = encode_mfg_pid("22012F", 800)
    assert encoded == bytes([0x03, 0x20])


def test_honda_knock_retard_round_trip() -> None:
    """Inverse of b[0] * 0.5 - 64 → degrees; 0° → 128"""
    encoded = encode_mfg_pid("220156", 0.0)
    assert encoded == bytes([128])


def test_honda_fuel_pressure_round_trip() -> None:
    """Inverse of b[0:2] → kPa."""
    encoded = encode_mfg_pid("22011A", 350)
    assert encoded == bytes([0x01, 0x5E])


def test_nissan_cvt_temp_offset40() -> None:
    """Nissan PID 0x221102 is in the default bank too."""
    encoded = encode_mfg_pid("221102", 85)
    assert encoded == bytes([125])  # 85 + 40


# ---------------------------------------------------------------------------
# Make-bank switching
# ---------------------------------------------------------------------------

def test_active_make_starts_at_default() -> None:
    select_make("default")
    assert active_make() == "default"


def test_select_make_nissan_swaps_bank() -> None:
    """After `select_make('nissan')`, PID 0x221101 should decode as the
    Nissan CVT ratio instead of the Ford key-on-runtime that the
    default bank uses."""
    select_make("nissan")
    assert active_make() == "nissan"
    # Nissan CVT ratio uses scale 1000: 1.5 → raw 1500 = 0x05DC
    encoded = encode_mfg_pid("221101", 1.5)
    assert encoded == bytes([0x05, 0xDC])


def test_select_make_toyota_swaps_bank() -> None:
    select_make("toyota")
    # Toyota bank ADDS 0x220156 for accelerator pedal. (Default bank has
    # Honda knock-retard there.) Toyota's encoder uses pct → byte.
    encoded = encode_mfg_pid("220156", 50.0)
    # Toyota accelerator pedal: 50% → raw = round(50 * 255/100) = 128
    assert encoded == bytes([128])


def test_select_make_case_insensitive() -> None:
    select_make("NISSAN")
    assert active_make() == "nissan"
    select_make("Toyota")
    assert active_make() == "toyota"


def test_select_make_unknown_falls_back_to_default() -> None:
    select_make("ferrari")
    assert active_make() == "default"


def test_select_make_none_or_empty_falls_back_to_default() -> None:
    select_make("")
    assert active_make() == "default"
    select_make(None)  # type: ignore[arg-type]
    assert active_make() == "default"


# ---------------------------------------------------------------------------
# encode_mfg_pid — error paths
# ---------------------------------------------------------------------------

def test_encode_mfg_pid_none_value_returns_none() -> None:
    assert encode_mfg_pid("22115C", None) is None


def test_encode_mfg_pid_unknown_key_returns_none() -> None:
    """A PID not in the active bank → None (not an exception)."""
    assert encode_mfg_pid("22FFFF", 100) is None


def test_encode_mfg_pid_encoder_raises_returns_none() -> None:
    """If the underlying encoder raises (e.g. unconvertible value),
    the wrapper returns None rather than propagating."""
    # Most encoders accept ints/floats. Feed a fundamentally-bad value
    # like a list to the Ford trans-oil-temp encoder; the `+ 40` step
    # raises TypeError, which the wrapper should swallow.
    result = encode_mfg_pid("22115C", ["not", "a", "number"])  # type: ignore[arg-type]
    assert result is None


def test_encodable_mfg_pids_lists_active_bank() -> None:
    """`encodable_mfg_pids` should reflect the currently-selected bank."""
    select_make("default")
    default_pids = encodable_mfg_pids()
    assert "22115C" in default_pids  # Ford trans oil temp
    assert "221102" in default_pids  # Nissan CVT temp (in default bank)

    select_make("nissan")
    nissan_pids = encodable_mfg_pids()
    # Nissan bank has 0x221101 → Nissan CVT ratio (overriding Ford key-on-runtime)
    assert "221101" in nissan_pids
    # And Ford PIDs are still present via inheritance from default
    assert "22115C" in nissan_pids
