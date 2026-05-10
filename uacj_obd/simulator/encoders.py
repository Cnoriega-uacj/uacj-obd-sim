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


# Mode 0x22 manufacturer PID encoders. Inverse of the formulas in
# pids/data/manufacturer_starter.yaml. Adding a new mfg PID is a 5-line
# addition here mirroring the YAML decode entry.

def _enc_ford_trans_oil_temp(c: float) -> bytes:
    raw = max(0, min(65535, int(round((c + 40) * 10))))
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_ford_key_on_runtime(s: float) -> bytes:
    raw = max(0, min(65535, int(round(s))))
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_gm_oil_life(pct: float) -> bytes:
    return bytes([_u8(pct * 255 / 100)])


def _enc_gm_trans_fluid_temp(c: float) -> bytes:
    return bytes([_u8(c + 40)])


def _enc_toyota_engine_runtime(minutes: float) -> bytes:
    raw = max(0, min(65535, int(round(minutes))))
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_honda_atf_temp(c: float) -> bytes:
    return bytes([_u8(c + 40)])


def _enc_honda_vtec_oil_press(kpa: float) -> bytes:
    raw = max(0, min(65535, int(round(kpa * 10))))
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_honda_brake_switch(value: float | int | str) -> bytes:
    try:
        b = 1 if int(value) else 0
    except (TypeError, ValueError):
        b = 1 if str(value).lower() in ("on", "true", "pressed") else 0
    return bytes([b])


def _enc_honda_target_idle(rpm: float) -> bytes:
    raw = _u16(rpm)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_honda_knock_retard(deg: float) -> bytes:
    # Inverse of formula b[0] * 0.5 - 64 → b[0] = (deg + 64) * 2
    return bytes([_u8((deg + 64) * 2)])


def _enc_honda_fuel_pressure(kpa: float) -> bytes:
    raw = _u16(kpa)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_pct_byte(pct: float) -> bytes:
    return bytes([_u8(pct * 255 / 100)])


def _enc_bool_byte(value: float | int | str) -> bytes:
    try:
        b = 1 if int(value) else 0
    except (TypeError, ValueError):
        b = 1 if str(value).lower() in ("on", "true", "yes") else 0
    return bytes([b])


def _enc_temp_offset40(c: float) -> bytes:
    return bytes([_u8(c + 40)])


def _enc_byte_passthrough(v: float) -> bytes:
    return bytes([_u8(v)])


def _enc_u16_be(v: float, scale: float = 1.0) -> bytes:
    raw = max(0, min(65535, int(round(v * scale))))
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


# Ford
def _enc_ford_ac_compressor(value): return _enc_bool_byte(value)
def _enc_ford_fuel_pump_duty(pct): return _enc_pct_byte(pct)
def _enc_ford_gear(g): return _enc_byte_passthrough(g)


# GM
def _enc_gm_fuel_tank_pressure(pa: float) -> bytes:
    # Inverse of (b[0]*256 + b[1]) * 0.25 - 8192 → raw = (pa + 8192) / 0.25
    raw = max(0, min(65535, int(round((pa + 8192) / 0.25))))
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_gm_gear(g): return _enc_byte_passthrough(g)
def _enc_gm_baro(kpa): return _enc_byte_passthrough(kpa)


# Toyota
def _enc_toyota_hybrid_soc(pct): return _enc_pct_byte(pct)
def _enc_toyota_inverter_temp(c): return _enc_temp_offset40(c)
def _enc_toyota_accel_pedal(pct): return _enc_pct_byte(pct)


# Nissan
def _enc_nissan_cvt_ratio(ratio): return _enc_u16_be(ratio, 1000.0)
def _enc_nissan_cvt_temp(c): return _enc_temp_offset40(c)
def _enc_nissan_target_afr(lam): return _enc_u16_be(lam, 10000.0)


# Encoders for the merged default registry. Where two makes share a key,
# the encoder corresponds to the YAML entry that loads last (alphabetical
# file order). Per-make Nissan PIDs at colliding keys are unreachable in
# the default registry — load `manufacturer_nissan.yaml` into a fresh
# PidRegistry() to swap them in.
_MFG_ENCODERS: dict[str, Callable[..., bytes]] = {
    # Ford
    "22115C": _enc_ford_trans_oil_temp,
    "221101": _enc_ford_key_on_runtime,        # collides with Nissan CVT ratio
    "221108": _enc_ford_ac_compressor,
    "221156": _enc_ford_fuel_pump_duty,        # collides with Nissan target AFR
    "221157": _enc_ford_gear,
    # GM
    "220005": _enc_gm_oil_life,
    "22115A": _enc_gm_trans_fluid_temp,
    "22000C": _enc_gm_fuel_tank_pressure,
    "22115B": _enc_gm_gear,
    "22100C": _enc_gm_baro,
    # Toyota
    "220101": _enc_toyota_engine_runtime,
    "220102": _enc_toyota_hybrid_soc,
    "220103": _enc_toyota_inverter_temp,
    # Honda — primary UACJ fleet
    "22015C": _enc_honda_atf_temp,
    "220144": _enc_honda_vtec_oil_press,
    "220123": _enc_honda_brake_switch,
    "22012F": _enc_honda_target_idle,
    "220156": _enc_honda_knock_retard,
    "22011A": _enc_honda_fuel_pressure,
    # Nissan — only the non-colliding key in the default merged registry
    "221102": _enc_nissan_cvt_temp,
}


# Per-make encoder banks for opt-in classroom profiles. Use:
#   from uacj_obd.simulator.encoders import select_make
#   select_make("nissan")
# to switch the simulator to Nissan-only PID handling for a session.
_NISSAN_ENCODERS: dict[str, Callable[..., bytes]] = {
    "221101": _enc_nissan_cvt_ratio,
    "221102": _enc_nissan_cvt_temp,
    "221156": _enc_nissan_target_afr,
}


_TOYOTA_ENCODERS: dict[str, Callable[..., bytes]] = {
    "220101": _enc_toyota_engine_runtime,
    "220102": _enc_toyota_hybrid_soc,
    "220103": _enc_toyota_inverter_temp,
    "220156": _enc_toyota_accel_pedal,  # collides with Honda knock retard
}


_MAKE_BANKS = {
    "default": _MFG_ENCODERS,
    "nissan": {**_MFG_ENCODERS, **_NISSAN_ENCODERS},
    "toyota": {**_MFG_ENCODERS, **_TOYOTA_ENCODERS},
}


_active_make = "default"


def select_make(make: str) -> None:
    """Swap the active mfg-PID encoder bank. `make` is matched
    case-insensitively against keys of `_MAKE_BANKS`. Falls back to
    'default' for unknown makes."""
    global _active_make
    key = (make or "default").lower()
    _active_make = key if key in _MAKE_BANKS else "default"


def active_make() -> str:
    return _active_make


def encode_mfg_pid(pid_key: str, value: float | int | str | None) -> bytes | None:
    """Encode a mode 0x22 manufacturer PID's value to response bytes,
    using the currently-selected make bank (default = mixed Ford/GM/
    Toyota/Honda)."""
    if value is None:
        return None
    bank = _MAKE_BANKS[_active_make]
    enc = bank.get(pid_key.upper())
    if enc is None:
        return None
    try:
        return enc(value)
    except Exception:
        return None


def encodable_mfg_pids() -> set[str]:
    return set(_MAKE_BANKS[_active_make].keys())
