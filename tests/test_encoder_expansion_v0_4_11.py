"""
Round-trip tests for the v0.4.11 simulator PID encoder expansion.

For each newly added Mode 01 PID, verify that encode(value) returns the
right SAE J1979 byte pattern. This locks in the encoder's correctness so
a future change can't silently regress one of the 40+ added formulas.

Pairs with `pids/data/standard_j1979.yaml` decode formulas — the encode
here is the inverse of those.
"""

from __future__ import annotations

from uacj_obd.simulator.encoders import encode_pid


def _u(value: float, scale: float, offset: float = 0.0) -> int:
    """Helper: round-trip a value through (raw*scale + offset)."""
    return int(round((value - offset) / scale))


def test_pid_010E_timing_advance_zero_deg() -> None:
    # Formula: A/2 - 64 = degrees; at 0° A=128.
    assert encode_pid("010E", 0.0) == bytes([128])


def test_pid_010E_timing_advance_negative_deg() -> None:
    # -10° → A = (-10 + 64) * 2 = 108
    assert encode_pid("010E", -10.0) == bytes([108])


def test_pid_010E_timing_advance_positive_deg() -> None:
    # 15° → A = (15 + 64) * 2 = 158
    assert encode_pid("010E", 15.0) == bytes([158])


def test_pid_0121_distance_with_mil_on() -> None:
    # Formula: A*256+B = km. 1000 km → 0x03E8
    assert encode_pid("0121", 1000) == bytes([0x03, 0xE8])


def test_pid_012C_commanded_egr() -> None:
    # Formula: A * 100 / 255. At 50% A ≈ 128
    assert encode_pid("012C", 50.0) == bytes([128])


def test_pid_012D_egr_error_zero() -> None:
    # Formula: (A * 100 / 128) - 100. At 0% A = 128
    assert encode_pid("012D", 0.0) == bytes([128])


def test_pid_012D_egr_error_positive() -> None:
    # +50% → A = (50 + 100) * 128 / 100 = 192
    assert encode_pid("012D", 50.0) == bytes([192])


def test_pid_012E_commanded_evap_purge() -> None:
    # Formula: A * 100 / 255. At 25% A ≈ 64
    assert encode_pid("012E", 25.0) == bytes([64])


def test_pid_0130_warmups_since_cleared() -> None:
    # Single-byte count.
    assert encode_pid("0130", 17) == bytes([17])


def test_pid_0131_distance_since_codes_cleared() -> None:
    # km, 2 bytes big-endian. 196 km → 0x00C4
    assert encode_pid("0131", 196) == bytes([0x00, 0xC4])


def test_pid_0132_evap_vapor_pressure_zero() -> None:
    # Formula: ((A*256+B) / 4) - 8192 = Pa. At 0 Pa, raw = 32768 (0x8000)
    assert encode_pid("0132", 0.0) == bytes([0x80, 0x00])


def test_pid_013C_catalyst_temp() -> None:
    # Formula: ((A*256+B) / 10) - 40 = °C. At 647°C raw = (687 * 10) = 6870 = 0x1AD6
    encoded = encode_pid("013C", 647.0)
    assert encoded is not None
    assert len(encoded) == 2
    raw = (encoded[0] << 8) | encoded[1]
    # Allow small rounding error from the float→int conversion
    assert abs(raw - 6870) <= 1


def test_pid_013D_catalyst_temp_bank2() -> None:
    encoded = encode_pid("013D", 500.0)
    assert encoded is not None
    raw = (encoded[0] << 8) | encoded[1]
    assert abs(raw - 5400) <= 1


def test_pid_0143_absolute_load() -> None:
    # Formula: (A*256+B) * 100/255 = %. At 18.43% raw ≈ 47
    encoded = encode_pid("0143", 18.43)
    assert encoded is not None
    raw = (encoded[0] << 8) | encoded[1]
    assert abs(raw - 47) <= 1


def test_pid_0144_commanded_afr_stoich() -> None:
    # Formula: (A*256+B) / 32768 = equivalence ratio. Stoich = 1.0 → 0x8000
    assert encode_pid("0144", 1.0) == bytes([0x80, 0x00])


def test_pid_0145_relative_throttle_position() -> None:
    # Formula: A * 100/255. At 5.88% A ≈ 15
    encoded = encode_pid("0145", 5.88)
    assert encoded is not None
    assert abs(encoded[0] - 15) <= 1


def test_pid_0146_ambient_air_temp() -> None:
    # Formula: A - 40 = °C. 123.8°F = 51°C → A = 91
    assert encode_pid("0146", 51) == bytes([91])


def test_pid_0147_absolute_throttle_position_B() -> None:
    encoded = encode_pid("0147", 15.29)
    assert encoded is not None
    assert abs(encoded[0] - 39) <= 1


def test_pid_0149_accelerator_pedal_position_D() -> None:
    encoded = encode_pid("0149", 15.29)
    assert encoded is not None
    assert abs(encoded[0] - 39) <= 1


def test_pid_014A_accelerator_pedal_position_E() -> None:
    encoded = encode_pid("014A", 7.45)
    assert encoded is not None
    assert abs(encoded[0] - 19) <= 1


def test_pid_014C_commanded_throttle_actuator() -> None:
    encoded = encode_pid("014C", 7.45)
    assert encoded is not None
    assert abs(encoded[0] - 19) <= 1


def test_pid_0155_secondary_o2_trim_zero() -> None:
    # Formula: (A * 100/128) - 100. At 0% A = 128
    assert encode_pid("0155", 0.0) == bytes([128])


def test_pid_0156_secondary_o2_trim_positive() -> None:
    # +10% → A = (10 + 100) * 128 / 100 = 140.8 → 141
    assert encode_pid("0156", 10.0) == bytes([141])


def test_pid_015C_engine_oil_temp() -> None:
    # Formula: A - 40 = °C. 100°C → A = 140
    assert encode_pid("015C", 100) == bytes([140])


def test_pid_015E_engine_fuel_rate() -> None:
    # Formula: (A*256+B) * 0.05 = L/h. 5.0 L/h → raw = 100 → 0x0064
    assert encode_pid("015E", 5.0) == bytes([0x00, 0x64])


def test_pid_0102_freeze_dtc_default_zero() -> None:
    # Hex-string acceptable for freeze frame DTC pointer.
    assert encode_pid("0102", "0x0420") == bytes([0x04, 0x20])


def test_unknown_pid_returns_none() -> None:
    # Sanity: an actually-unknown PID still returns None for encode.
    assert encode_pid("01FF", 0) is None


def test_pid_0108_short_fuel_trim_bank2() -> None:
    # Same formula as 0106 fuel trim. At 0% A = 128
    assert encode_pid("0108", 0.0) == bytes([128])


def test_pid_010A_fuel_pressure() -> None:
    # Single-byte kPa. 32 kPa → A = 32
    assert encode_pid("010A", 32) == bytes([32])


def test_pid_0116_oxygen_sensor_voltage_bank1_sensor3() -> None:
    # 2-byte O2 voltage. 0.45 V → A = 90, B = 0xFF
    assert encode_pid("0116", 0.45) == bytes([90, 0xFF])
