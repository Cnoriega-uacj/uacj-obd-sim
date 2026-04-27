"""
Encoders: value → response data bytes per standard PID.

Mirror images of the decode formulas in pids/data/standard_j1979.yaml.
We hand-roll encoders for the common live PIDs because YAML formulas
are decode-only — generating an inverse from an arbitrary expression
is messy and not worth automating for the ~20 PIDs students use.

Each encoder returns the *PID data bytes only* (no service+pid header).
The dispatcher prepends the response header.
"""

from __future__ import annotations

from typing import Callable


def _u8(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _u16(v: float) -> int:
    return max(0, min(65535, int(round(v))))


def _enc_engine_load(pct: float) -> bytes:
    return bytes([_u8(pct * 255 / 100)])


def _enc_coolant_temp(c: float) -> bytes:
    return bytes([_u8(c + 40)])


def _enc_fuel_trim(pct: float) -> bytes:
    return bytes([_u8(128 + pct * 128 / 100)])


def _enc_intake_pressure(kpa: float) -> bytes:
    return bytes([_u8(kpa)])


def _enc_rpm(rpm: float) -> bytes:
    raw = _u16(rpm * 4)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_speed(kmh: float) -> bytes:
    return bytes([_u8(kmh)])


def _enc_intake_temp(c: float) -> bytes:
    return bytes([_u8(c + 40)])


def _enc_maf(g_per_s: float) -> bytes:
    raw = _u16(g_per_s * 100)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_throttle(pct: float) -> bytes:
    return bytes([_u8(pct * 255 / 100)])


def _enc_o2_voltage(v: float) -> bytes:
    return bytes([_u8(v * 200), 0xFF])


def _enc_fuel_level(pct: float) -> bytes:
    return bytes([_u8(pct * 255 / 100)])


def _enc_runtime_seconds(s: float) -> bytes:
    raw = _u16(s)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_baro(kpa: float) -> bytes:
    return bytes([_u8(kpa)])


def _enc_module_voltage(v: float) -> bytes:
    raw = _u16(v * 1000)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_fuel_system_status(value: float | int | str) -> bytes:
    # Accept a single-byte status; default to "closed loop" (0x02) if missing
    try:
        b = int(value)
    except (TypeError, ValueError):
        b = 0x02
    return bytes([b & 0xFF, 0x00])


# Map full PID key (mode + pid) → encoder
_ENCODERS: dict[str, Callable[..., bytes]] = {
    "0103": _enc_fuel_system_status,
    "0104": _enc_engine_load,
    "0105": _enc_coolant_temp,
    "0106": _enc_fuel_trim,
    "0107": _enc_fuel_trim,
    "010B": _enc_intake_pressure,
    "010C": _enc_rpm,
    "010D": _enc_speed,
    "010F": _enc_intake_temp,
    "0110": _enc_maf,
    "0111": _enc_throttle,
    "0114": _enc_o2_voltage,
    "0115": _enc_o2_voltage,
    "011F": _enc_runtime_seconds,
    "012F": _enc_fuel_level,
    "0133": _enc_baro,
    "0142": _enc_module_voltage,
}


def encode_pid(pid_key: str, value: float | int | str | None) -> bytes | None:
    """
    Encode a value to the response bytes for the given PID.
    Returns None when the PID is not encodable by this module.
    """
    if value is None:
        return None
    enc = _ENCODERS.get(pid_key.upper())
    if enc is None:
        return None
    try:
        return enc(value)
    except Exception:
        return None


def supported_pid_bitmap(pids: set[str], group: int) -> bytes:
    """
    Build the 4-byte mode-01 supported-PIDs bitmap response.

    group=0x00 → returns whether PIDs 0x01..0x20 are supported,
    group=0x20 → 0x21..0x40, group=0x40 → 0x41..0x60, etc.

    The MSB of the first byte represents PID (group + 1).
    """
    out = [0, 0, 0, 0]
    for i in range(32):
        pid_num = group + 1 + i
        key = f"01{pid_num:02X}"
        if key in pids:
            byte_idx = i // 8
            bit = 7 - (i % 8)
            out[byte_idx] |= 1 << bit
    return bytes(out)


def encodable_pids() -> set[str]:
    return set(_ENCODERS.keys())
