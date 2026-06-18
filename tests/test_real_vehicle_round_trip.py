"""
v0.4.11 — Real-vehicle round-trip integration test harness.

Closes the meta-gap that allowed 10 bugs to surface during on-site
install: every previous test exercised mock-against-mock. This module
adds fixtures shaped like *real* python-obd return values (bytearray
VINs, tuple-of-bytes DTC entries, multi-segment VINs) and a real-world
PID dump (the ~30 PIDs the client's 2012 Mazda3 reported through
OBDwiz), and walks them through the full pipeline:

    raw python-obd-style data
        ↓
    Elm327Adapter._decode_string_response / DTC decode / live PID
        ↓
    SessionStore-format payload
        ↓
    scenario_to_state
        ↓
    EcuEmulator dispatch
        ↓
    Mode 01 / 03 / 09 response bytes match SAE J1979

If any of these break, this test catches it before the client sees it.
"""

from __future__ import annotations

from uacj_obd.adapters.elm327 import _decode_string_response
from uacj_obd.simulator.can_runtime import scenario_to_state
from uacj_obd.simulator.ecu import EcuEmulator
from uacj_obd.simulator.encoders import encode_pid


# ---------------------------------------------------------------------------
# Fixtures: what python-obd actually returns on the client's hardware
# ---------------------------------------------------------------------------

MAZDA3_RAW_VIN = bytearray(b"JM1BL1L72C1627697")  # bytearray, not str
MAZDA3_RAW_VIN_MULTI = [bytearray(b"JM1BL1L7"), bytearray(b"2C1627697")]
MAZDA3_RAW_CAL_ID = bytearray(b"L31418881   ")  # with trailing spaces
MAZDA3_RAW_ECU_NAME = bytearray(b"ECM\x00")  # with trailing null

# Real PID set captured from a 2012 Mazda3 (subset of the 113 supported).
MAZDA3_LIVE_PIDS_AT_IDLE = {
    "0104": 32.5,    # engine load %
    "0105": 88,      # coolant temp °C
    "0106": 2.3,     # short fuel trim %
    "0107": 10.9,    # long fuel trim %
    "010B": 32,      # MAP kPa
    "010C": 773,     # RPM
    "010D": 0,       # speed km/h
    "010E": 8,       # timing advance deg
    "010F": 38,      # intake air temp °C
    "0110": 4.2,     # MAF g/s
    "0111": 15.7,    # throttle position %
    "0114": 0.45,    # O2 sensor B1S1 voltage
    "011F": 1280,    # runtime since start
    "0121": 35,      # distance with MIL on
    "012C": 12,      # commanded EGR %
    "012D": -3,      # EGR error %
    "012E": 8,       # commanded EVAP purge %
    "0130": 17,      # warm-ups since cleared
    "0131": 196,     # distance since codes cleared
    "0132": -100,    # EVAP vapor pressure Pa
    "0133": 87,      # barometric pressure kPa
    "013C": 647,     # catalyst temp B1S1 °C
    "0142": 14.2,    # control module voltage
    "0143": 18.4,    # absolute load %
    "0145": 5.88,    # relative throttle position %
    "0146": 25,      # ambient air temp °C
    "0147": 15.29,   # absolute throttle position B %
    "0149": 15.29,   # accelerator pedal D %
    "014A": 7.45,    # accelerator pedal E %
    "014C": 7.45,    # commanded throttle actuator %
    "0155": 0.0,     # short term secondary O2 trim B1B3 %
    "0156": 0.0,     # long term secondary O2 trim B1B3 %
    "015C": 95,      # engine oil temp °C
    "015E": 1.8,     # engine fuel rate L/h
}

# python-obd DTC return shape (tuple of code+description, code as bytes)
MAZDA3_RAW_DTCS_NO_CODES: list = []
DEMO_RAW_DTCS_P0420: list = [
    (b"P0420", b"Catalyst System Efficiency Below Threshold (Bank 1)"),
]


# ---------------------------------------------------------------------------
# Adapter-layer decode round-trip
# ---------------------------------------------------------------------------

def test_real_mazda3_vin_decodes_to_clean_string() -> None:
    assert _decode_string_response(MAZDA3_RAW_VIN) == "JM1BL1L72C1627697"


def test_real_mazda3_vin_multi_segment_concatenates() -> None:
    assert _decode_string_response(MAZDA3_RAW_VIN_MULTI) == "JM1BL1L72C1627697"


def test_real_mazda3_calibration_id_strips_trailing_spaces() -> None:
    assert _decode_string_response(MAZDA3_RAW_CAL_ID) == "L31418881"


def test_real_mazda3_ecu_name_strips_trailing_null() -> None:
    assert _decode_string_response(MAZDA3_RAW_ECU_NAME) == "ECM"


def test_real_dtc_code_from_bytes_decodes_cleanly() -> None:
    code, _ = DEMO_RAW_DTCS_P0420[0]
    assert _decode_string_response(code) == "P0420"


# ---------------------------------------------------------------------------
# Full pipeline: scenario payload → ScenarioState → ECU Mode response
# ---------------------------------------------------------------------------

def _mazda3_scenario_payload(with_dtc: bool = False) -> dict:
    """Build a scenario payload as if the dashboard sent it after a
    fresh Mazda3 capture (post-v0.4.11). VIN is a clean string,
    bytes/bytearray have already been decoded by the adapter."""
    payload = {
        "vehicle": {
            "vin": "JM1BL1L72C1627697",
            "make": "Mazda",
            "model": "Mazda3",
            "year": 2012,
            "calibration_id": "L31418881",
            "ecu_name": "ECM",
        },
        "live_overrides": dict(MAZDA3_LIVE_PIDS_AT_IDLE),
        "dtcs": [],
    }
    if with_dtc:
        payload["dtcs"] = [{"code": "P0420", "status": "stored"}]
        payload["freeze_frame"] = {
            "dtc": "P0420",
            "pids": {"010C": 1850, "0105": 91, "0111": 18},
        }
    return payload


def test_mazda3_full_pipeline_vin_round_trip() -> None:
    state = scenario_to_state(_mazda3_scenario_payload())
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x09, 0x02]))
    assert resp[:3] == bytes([0x49, 0x02, 0x01])
    assert resp[3:] == b"JM1BL1L72C1627697"


def test_mazda3_full_pipeline_rpm_round_trip() -> None:
    state = scenario_to_state(_mazda3_scenario_payload())
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x01, 0x0C]))
    assert resp[0:2] == bytes([0x41, 0x0C])
    raw = (resp[2] << 8) | resp[3]
    assert abs(raw / 4 - 773) < 1


def test_mazda3_full_pipeline_coolant_temp_round_trip() -> None:
    state = scenario_to_state(_mazda3_scenario_payload())
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x01, 0x05]))
    assert resp[0:2] == bytes([0x41, 0x05])
    assert resp[2] - 40 == 88


def test_mazda3_full_pipeline_catalyst_temp_now_supported() -> None:
    """The v0.4.11 encoder expansion adds PID 0x3C (catalyst temp).
    Previously NRC; now a real response."""
    state = scenario_to_state(_mazda3_scenario_payload())
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x01, 0x3C]))
    assert resp[0] != 0x7F  # not NRC
    assert resp[0:2] == bytes([0x41, 0x3C])
    raw = (resp[2] << 8) | resp[3]
    assert abs(raw / 10 - 40 - 647) <= 1


def test_mazda3_full_pipeline_accelerator_pedal_now_supported() -> None:
    """v0.4.11 added accelerator pedal PIDs (0x49 / 0x4A). Previously
    NRC; now reports the captured value."""
    state = scenario_to_state(_mazda3_scenario_payload())
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x01, 0x49]))
    assert resp[0] != 0x7F
    assert resp[0:2] == bytes([0x41, 0x49])
    # Expect close to 15.29% (within rounding error)
    assert abs(resp[2] * 100 / 255 - 15.29) < 1


def test_mazda3_full_pipeline_mode03_with_no_dtcs() -> None:
    state = scenario_to_state(_mazda3_scenario_payload())
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x03]))
    assert resp == bytes([0x43, 0x00])


def test_mazda3_full_pipeline_mode03_with_p0420() -> None:
    state = scenario_to_state(_mazda3_scenario_payload(with_dtc=True))
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x03]))
    assert resp[0] == 0x43
    assert resp[1] == 1
    assert resp[2:4] == bytes([0x04, 0x20])


def test_mazda3_full_pipeline_mode01_pid01_byte_a_consistent_with_mode03() -> None:
    """v0.4.2 fix locked in here: byte A of Mode 01 PID 01 (MIL state +
    DTC count) MUST agree with Mode 03's count. Otherwise scan tools
    silently fail."""
    state = scenario_to_state(_mazda3_scenario_payload(with_dtc=True))
    ecu = EcuEmulator(state)
    resp_01_01 = ecu.handle(bytes([0x01, 0x01]))
    resp_03 = ecu.handle(bytes([0x03]))
    assert resp_01_01[2] == 0x81  # MIL on + 1 DTC
    assert resp_03[1] == 1


def test_mazda3_full_pipeline_byte_d_derives_cat_not_complete() -> None:
    """v0.4.3 fix locked in here: with P0420 stored, byte D bit 0 (CAT
    not complete) MUST be set. Otherwise the Innova suppresses the
    monitor-badges row."""
    state = scenario_to_state(_mazda3_scenario_payload(with_dtc=True))
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[5] & 0x01 == 0x01  # CAT bit set


def test_legacy_capture_with_bytearray_vin_still_plays_back() -> None:
    """v0.4.11 simulator can replay sessions captured before v0.4.10
    (when VIN was stored as the bytearray repr string). The Mode 09
    response must still produce a clean 17-char ASCII VIN."""
    payload = _mazda3_scenario_payload()
    payload["vehicle"]["vin"] = "bytearray(b'JM1BL1L72C1627697')"
    state = scenario_to_state(payload)
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x09, 0x02]))
    assert resp[3:] == b"JM1BL1L72C1627697"


def test_full_mazda3_capture_every_pid_round_trips_or_nrcs_cleanly() -> None:
    """Final guard: every PID in the captured Mazda3 set either returns
    a valid Mode 01 response OR an NRC — never garbage, never an
    exception."""
    state = scenario_to_state(_mazda3_scenario_payload())
    ecu = EcuEmulator(state)
    for pid_key in MAZDA3_LIVE_PIDS_AT_IDLE:
        pid_byte = int(pid_key[2:], 16)
        resp = ecu.handle(bytes([0x01, pid_byte]))
        assert resp, f"no response for {pid_key}"
        # Either a positive response (0x41 + PID byte + data) or an NRC
        # (0x7F + 0x01 + NRC byte). Anything else means a bug.
        if resp[0] == 0x7F:
            assert resp[1] == 0x01, f"NRC for wrong service on {pid_key}"
        else:
            assert resp[0] == 0x41
            assert resp[1] == pid_byte


def test_v0_4_11_encoder_coverage_meets_mazda3_subset() -> None:
    """The encoder set must cover every PID in the real Mazda3 capture.
    If python-obd ever reports a PID that has no simulator encoder, we
    want a loud regression here, not a silent miss in the field."""
    missing = []
    for pid_key, value in MAZDA3_LIVE_PIDS_AT_IDLE.items():
        if encode_pid(pid_key, value) is None:
            missing.append(pid_key)
    assert not missing, (
        f"v0.4.11 should cover every PID in the Mazda3 capture; "
        f"missing encoders for: {missing}"
    )
