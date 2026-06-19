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


# --- v0.4.11: expanded SAE J1979 Mode 01 coverage ----------------------
# Added after on-site testing showed the client's Mazda3 capture had
# many PIDs the simulator could not re-emit. Innova displayed only the
# subset for which we had encoders. Each new encoder mirrors the
# canonical J1979 formula; the decode formulas live in
# `pids/data/standard_j1979.yaml`.


def _enc_timing_advance(deg: float) -> bytes:
    # PID 0x0E: (A / 2) - 64 → degrees; range -64 to +63.5
    return bytes([_u8((deg + 64) * 2)])


def _enc_byte_passthrough(value: float | int | str) -> bytes:
    # PIDs 0x12 (commanded secondary air), 0x13 (O2 sensors present),
    # 0x1C (OBD requirements), 0x1D (O2 sensors present 4-bank), 0x51
    # (fuel type). All single-byte enums/bitmaps.
    try:
        b = int(value)
    except (TypeError, ValueError):
        b = 0
    return bytes([b & 0xFF])


def _enc_u16_km(km: float) -> bytes:
    # PID 0x21 (distance with MIL on), 0x31 (distance since codes cleared)
    raw = _u16(km)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_fuel_rail_pressure_relative(kpa: float) -> bytes:
    # PID 0x22: ((A*256 + B) * 0.079) → kPa, range 0-5177.265
    raw = _u16(kpa / 0.079)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_fuel_rail_pressure_high(kpa: float) -> bytes:
    # PID 0x23: ((A*256 + B) * 10) → kPa, range 0-655350
    raw = _u16(kpa / 10)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_o2_wide_range(value: float) -> bytes:
    # PIDs 0x24-0x2B: wide-range O2 sensors — equivalence ratio + voltage.
    # 4-byte response: A B = equivalence ratio * 32768, C D = voltage * 8192.
    # Accept the equivalence ratio as the scenario value; voltage defaults
    # to a stoichiometric 0.5 V.
    ratio_raw = _u16(value * 32768) if value else 32768  # default 1.0
    voltage_raw = _u16(0.5 * 8192)
    return bytes([
        (ratio_raw >> 8) & 0xFF, ratio_raw & 0xFF,
        (voltage_raw >> 8) & 0xFF, voltage_raw & 0xFF,
    ])


def _enc_commanded_egr(pct: float) -> bytes:
    # PID 0x2C: A * 100 / 255
    return bytes([_u8(pct * 255 / 100)])


def _enc_egr_error(pct: float) -> bytes:
    # PID 0x2D: (A * 100 / 128) - 100, range -100 to +99.22
    return bytes([_u8((pct + 100) * 128 / 100)])


def _enc_commanded_evap_purge(pct: float) -> bytes:
    # PID 0x2E: A * 100 / 255
    return bytes([_u8(pct * 255 / 100)])


def _enc_warmups_since_cleared(count: float) -> bytes:
    # PID 0x30: single byte count, 0-255
    return bytes([_u8(count)])


def _enc_evap_vapor_pressure(pa: float) -> bytes:
    # PID 0x32: ((A*256 + B) / 4) - 8192 → Pa, range -8192 to +8191.75.
    # SAE encodes as signed; we represent the raw 16-bit big-endian value.
    raw = max(0, min(65535, int(round((pa + 8192) * 4))))
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_catalyst_temp(c: float) -> bytes:
    # PIDs 0x3C-0x3F: ((A*256 + B) / 10) - 40 → degrees C, range -40 to +6513.5
    raw = _u16((c + 40) * 10)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_absolute_load(pct: float) -> bytes:
    # PID 0x43: ((A*256 + B) * 100 / 255) → percent, range 0-25700
    raw = _u16(pct * 255 / 100)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_commanded_afr(ratio: float) -> bytes:
    # PID 0x44: ((A*256 + B) / 32768) → equivalence ratio. Stoich = 1.0
    raw = _u16(ratio * 32768)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_pct_u8(pct: float) -> bytes:
    # Generic 1-byte percent encoder for many SAE J1979 PIDs:
    # 0x45 relative throttle, 0x47 abs throttle B, 0x48 abs throttle C,
    # 0x49 accel pedal D, 0x4A accel pedal E, 0x4B accel pedal F,
    # 0x4C commanded throttle actuator, 0x52 ethanol %, 0x5A relative
    # accel pedal, 0x5B hybrid battery remaining.
    return bytes([_u8(pct * 255 / 100)])


def _enc_temp_minus_40(c: float) -> bytes:
    # PID 0x46 ambient air temp, 0x5C engine oil temp: A - 40 → degrees C
    return bytes([_u8(c + 40)])


def _enc_secondary_o2_trim(pct: float) -> bytes:
    # PIDs 0x55-0x58 (B1S2/B2S4/B3S6/B4S8 secondary O2 trims):
    # (A * 100 / 128) - 100, range -100 to +99.22
    return bytes([_u8((pct + 100) * 128 / 100)])


def _enc_fuel_rate(l_per_h: float) -> bytes:
    # PID 0x5E: ((A*256 + B) * 0.05) → L/h, range 0-3276.75
    raw = _u16(l_per_h / 0.05)
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def _enc_two_byte_freeze_dtc(value: float | int | str) -> bytes:
    # PID 0x02: 2-byte DTC reference for the freeze frame. Default to zero
    # if the scenario doesn't carry a specific freeze DTC pointer.
    try:
        raw = int(value) if not isinstance(value, str) else int(value, 16)
    except (TypeError, ValueError):
        raw = 0
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


# Map full PID key (mode + pid) → encoder
_ENCODERS: dict[str, Callable[..., bytes]] = {
    # --- v0.4.0 baseline (the original 17) ---
    "0102": _enc_two_byte_freeze_dtc,
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
    # --- v0.4.11 expanded coverage ---
    "0108": _enc_fuel_trim,   # short fuel trim bank 2
    "0109": _enc_fuel_trim,   # long fuel trim bank 2
    "010A": _enc_intake_pressure,  # fuel pressure (gauge)
    "010E": _enc_timing_advance,
    "0112": _enc_byte_passthrough,  # commanded secondary air status
    "0113": _enc_byte_passthrough,  # O2 sensors present (2-bank)
    "0116": _enc_o2_voltage,
    "0117": _enc_o2_voltage,
    "0118": _enc_o2_voltage,
    "0119": _enc_o2_voltage,
    "011A": _enc_o2_voltage,
    "011B": _enc_o2_voltage,
    "011C": _enc_byte_passthrough,  # OBD requirements
    "011D": _enc_byte_passthrough,  # O2 sensors present (4-bank)
    "011E": _enc_byte_passthrough,  # auxiliary input status
    "0121": _enc_u16_km,             # distance with MIL on
    "0122": _enc_fuel_rail_pressure_relative,
    "0123": _enc_fuel_rail_pressure_high,
    "0124": _enc_o2_wide_range,
    "0125": _enc_o2_wide_range,
    "0126": _enc_o2_wide_range,
    "0127": _enc_o2_wide_range,
    "0128": _enc_o2_wide_range,
    "0129": _enc_o2_wide_range,
    "012A": _enc_o2_wide_range,
    "012B": _enc_o2_wide_range,
    "012C": _enc_commanded_egr,
    "012D": _enc_egr_error,
    "012E": _enc_commanded_evap_purge,
    "0130": _enc_warmups_since_cleared,
    "0131": _enc_u16_km,             # distance since codes cleared
    "0132": _enc_evap_vapor_pressure,
    "013C": _enc_catalyst_temp,
    "013D": _enc_catalyst_temp,
    "013E": _enc_catalyst_temp,
    "013F": _enc_catalyst_temp,
    "0143": _enc_absolute_load,
    "0144": _enc_commanded_afr,
    "0145": _enc_pct_u8,             # relative throttle position
    "0146": _enc_temp_minus_40,      # ambient air temperature
    "0147": _enc_pct_u8,             # absolute throttle position B
    "0148": _enc_pct_u8,             # absolute throttle position C
    "0149": _enc_pct_u8,             # accelerator pedal position D
    "014A": _enc_pct_u8,             # accelerator pedal position E
    "014B": _enc_pct_u8,             # accelerator pedal position F
    "014C": _enc_pct_u8,             # commanded throttle actuator control
    "0151": _enc_byte_passthrough,   # fuel type
    "0152": _enc_pct_u8,             # ethanol fuel %
    "0155": _enc_secondary_o2_trim,
    "0156": _enc_secondary_o2_trim,
    "0157": _enc_secondary_o2_trim,
    "0158": _enc_secondary_o2_trim,
    "015A": _enc_pct_u8,             # relative accelerator pedal position
    "015B": _enc_pct_u8,             # hybrid battery pack remaining life
    "015C": _enc_temp_minus_40,      # engine oil temperature
    "015E": _enc_fuel_rate,
}


_RAW_PREFIX = "raw:"


def _try_raw_passthrough(value) -> bytes | None:
    """
    v0.6.13: if `value` is a raw-bytes marker captured from a PID
    python-obd couldn't decode, return the raw response bytes for
    direct pass-through. The marker is `"raw:HEXHEX..."` — a string
    we control end-to-end (capture writes it, simulator parses it),
    so no risk of a numeric value being mis-classified as raw.
    """
    if not isinstance(value, str) or not value.startswith(_RAW_PREFIX):
        return None
    hex_part = value[len(_RAW_PREFIX):].strip()
    if not hex_part:
        return None
    try:
        return bytes.fromhex(hex_part)
    except ValueError:
        return None


def encode_pid(pid_key: str, value: float | int | str | None) -> bytes | None:
    """
    Encode a value to the response bytes for the given PID.
    Returns None when the PID is not encodable by this module.

    v0.6.13: a value shaped like `"raw:HEX"` bypasses the formula
    encoders and returns the bytes directly — used for PIDs the
    capture pipeline read as raw bytes because python-obd had no
    decoder. This lets the simulator answer ANY PID the real car
    answered, even ones the codebase doesn't have a formula for.
    """
    if value is None:
        return None
    raw = _try_raw_passthrough(value)
    if raw is not None:
        return raw
    enc = _ENCODERS.get(pid_key.upper())
    if enc is None:
        return None
    try:
        return enc(value)
    except Exception:
        return None


def is_answerable(pid_key: str, value) -> bool:
    """
    True when the simulator can produce a response for this PID with
    the given stored value — either via a registered formula encoder
    or via raw-bytes pass-through (v0.6.13).
    """
    if value is None:
        return False
    if _try_raw_passthrough(value) is not None:
        return True
    return pid_key.upper() in _ENCODERS


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
