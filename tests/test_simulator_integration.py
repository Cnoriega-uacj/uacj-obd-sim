"""End-to-end test: scan-tool-style request → CAN frame → ECU → response frames."""

from __future__ import annotations

from uacj_obd.simulator.can_runtime import (
    CanRuntime,
    _encode_monitors_per_j1979,
    scenario_to_state,
)
from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState
from uacj_obd.simulator.iso_tp import CanFrame, IsoTpFramer


class _NullBus:
    """Stub bus — never touched in this test."""


def _request(framer: IsoTpFramer, payload: bytes) -> CanFrame:
    framer = IsoTpFramer(tx_id=0x7DF)
    frames = framer.encode(payload)
    assert len(frames) == 1
    # rewrite arbitration_id to functional request 0x7DF
    return CanFrame(arbitration_id=0x7DF, data=frames[0].data)


def test_runtime_dispatches_mode_01_request() -> None:
    ecu = EcuEmulator(ScenarioState(live={"010C": 2200}))
    rt = CanRuntime(ecu, _NullBus())
    framer = IsoTpFramer()
    req = CanFrame(0x7DF, framer.encode(bytes([0x01, 0x0C]))[0].data)
    out = rt.handle_request_frame(req)
    assert len(out) == 1
    # Decode the response payload back through a fresh framer
    decoder = IsoTpFramer()
    payload = decoder.decode(out[0])
    assert payload[0] == 0x41
    assert payload[1] == 0x0C
    raw = (payload[2] << 8) | payload[3]
    assert raw / 4 == 2200


def test_runtime_vin_response_is_multi_frame() -> None:
    ecu = EcuEmulator(ScenarioState(vin="2HGFC2F59FH123456"))
    rt = CanRuntime(ecu, _NullBus())
    framer = IsoTpFramer()
    req = CanFrame(0x7DF, framer.encode(bytes([0x09, 0x02]))[0].data)
    out = rt.handle_request_frame(req)
    # 20-byte response → 3 frames (FF + 2 CFs)
    assert len(out) == 3
    decoder = IsoTpFramer()
    assert decoder.decode(out[0]) is None
    assert decoder.decode(out[1]) is None
    payload = decoder.decode(out[2])
    assert payload[:3] == bytes([0x49, 0x02, 0x01])
    assert b"2HGFC2F59FH123456" in payload


def test_scenario_to_state_round_trip() -> None:
    payload = {
        "vehicle": {"vin": "2HGFC2F59FH123456", "make": "Honda"},
        "dtcs": [
            {"code": "P0420", "status": "stored", "description": ""},
            {"code": "P0301", "status": "pending", "description": ""},
            {"code": "P0700", "status": "permanent", "description": ""},
        ],
        "monitors": [
            {"name": "Misfire", "supported": True, "ready": True},
            {"name": "Catalyst", "supported": True, "ready": False},
        ],
        "live_overrides": {"010C": 1500, "010D": 60},
        "freeze_frame": {"dtc": "P0420", "pids": {"010C": 1850}},
    }
    state = scenario_to_state(payload)
    assert state.vin == "2HGFC2F59FH123456"
    assert state.dtcs_stored == ["P0420"]
    assert state.dtcs_pending == ["P0301"]
    assert state.dtcs_permanent == ["P0700"]
    assert state.live["010C"] == 1500
    assert state.freeze_dtc == "P0420"
    # ECU answers correctly with this state
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x03]))
    assert resp[0] == 0x43 and resp[1] == 1


def test_encode_monitors_continuous_supported_and_complete() -> None:
    # All three continuous monitors supported and complete →
    # byte B lower nibble = 0x07, upper nibble = 0x00. Bytes C/D untouched.
    b, c, d = _encode_monitors_per_j1979([
        {"name": "Misfire", "supported": True, "ready": True},
        {"name": "Fuel System", "supported": True, "ready": True},
        {"name": "Comprehensive Components", "supported": True, "ready": True},
    ])
    assert b == 0x07
    assert c == 0x00
    assert d == 0x00


def test_encode_monitors_continuous_supported_not_ready_sets_upper_nibble() -> None:
    # Misfire supported but NOT ready → bit 0 (supported) + bit 4 (not complete).
    b, _, _ = _encode_monitors_per_j1979([
        {"name": "Misfire", "supported": True, "ready": False},
    ])
    assert b == 0x11  # 0b00010001


def test_encode_monitors_catalyst_supported_not_ready() -> None:
    # Catalyst supported, not ready (P0420-style scenario).
    # Non-continuous bit 0 → byte C bit 0 (supported) + byte D bit 0 (not complete).
    _, c, d = _encode_monitors_per_j1979([
        {"name": "Catalyst", "supported": True, "ready": False},
    ])
    assert c == 0x01
    assert d == 0x01


def test_encode_monitors_evap_not_ready() -> None:
    # EVAP is non-continuous bit 2.
    _, c, d = _encode_monitors_per_j1979([
        {"name": "Evaporative System", "supported": True, "ready": False},
    ])
    assert c == 0x04
    assert d == 0x04


def test_encode_monitors_unsupported_does_not_set_any_bit() -> None:
    # A monitor explicitly marked unsupported contributes nothing.
    b, c, d = _encode_monitors_per_j1979([
        {"name": "Secondary Air System", "supported": False, "ready": True},
    ])
    assert b == 0 and c == 0 and d == 0


def test_encode_monitors_accepts_id_and_abbreviation() -> None:
    # The encoder accepts the preset's display name or the scan-tool abbreviation.
    _, c_full, d_full = _encode_monitors_per_j1979([
        {"name": "Oxygen Sensor Heater", "supported": True, "ready": False},
    ])
    _, c_abbr, d_abbr = _encode_monitors_per_j1979([
        {"id": "HTR", "supported": True, "ready": False},
    ])
    assert c_full == c_abbr == 0x40  # bit 6
    assert d_full == d_abbr == 0x40


def test_encode_monitors_unknown_name_is_silently_ignored() -> None:
    # Unknown monitor name doesn't crash and doesn't set any bit.
    b, c, d = _encode_monitors_per_j1979([
        {"name": "Quantum Flux Capacitor", "supported": True, "ready": False},
    ])
    assert b == 0 and c == 0 and d == 0


def test_encode_monitors_full_typical_pre_2008_vehicle() -> None:
    # Typical post-2008 CAN-OBD vehicle with everything supported and most
    # monitors complete; CAT and EVAP not yet complete (drive-cycle pending).
    b, c, d = _encode_monitors_per_j1979([
        {"name": "Misfire", "supported": True, "ready": True},
        {"name": "Fuel System", "supported": True, "ready": True},
        {"name": "Comprehensive Components", "supported": True, "ready": True},
        {"name": "Catalyst", "supported": True, "ready": False},
        {"name": "Heated Catalyst", "supported": True, "ready": True},
        {"name": "Evaporative System", "supported": True, "ready": False},
        {"name": "Secondary Air System", "supported": False, "ready": True},
        {"name": "A/C System Refrigerant", "supported": False, "ready": True},
        {"name": "Oxygen Sensor", "supported": True, "ready": True},
        {"name": "Oxygen Sensor Heater", "supported": True, "ready": True},
        {"name": "EGR System", "supported": True, "ready": True},
    ])
    assert b == 0x07          # all 3 continuous supported + complete
    assert c == 0xE7          # all non-cont supported except AIR (bit 3) and A/C (bit 4)
    assert d == 0x05          # CAT (bit 0) + EVAP (bit 2) not complete


def test_scenario_to_state_propagates_encoded_monitor_bytes() -> None:
    # The high-level scenario_to_state passes the encoded bytes through to
    # ScenarioState — the ECU emulator then renders them on Mode 01 PID 01.
    payload = {
        "vehicle": {"vin": "1HGCM82633A123456"},
        "dtcs": [],
        "monitors": [
            {"name": "Misfire", "supported": True, "ready": True},
            {"name": "Catalyst", "supported": True, "ready": False},
        ],
    }
    state = scenario_to_state(payload)
    assert state.monitor_b == 0x01     # MIS supported, complete
    assert state.monitor_c == 0x01     # CAT supported
    assert state.monitor_d == 0x01     # CAT not complete


# ---------------------------------------------------------------------------
# v0.6.13/v0.6.14 — raw passthrough end-to-end through CAN runtime
# ---------------------------------------------------------------------------


def test_runtime_answers_raw_passthrough_pid_via_can() -> None:
    """A scenario carrying a raw-bytes PID should produce the correct
    Mode 01 response over CAN — verifies the whole stack
    (scenario_to_state → ScenarioState.live → ECU._mode01 →
    encode_pid raw branch → ISO-TP single frame)."""
    payload = {
        "vehicle": {"vin": "1HGCM82633A123456"},
        "live_baseline": {"01AB": "raw:CAFE"},
    }
    state = scenario_to_state(payload)
    assert state.live["01AB"] == "raw:CAFE"

    ecu = EcuEmulator(state)
    rt = CanRuntime(ecu, _NullBus())
    framer = IsoTpFramer()
    req = CanFrame(0x7DF, framer.encode(bytes([0x01, 0xAB]))[0].data)
    out = rt.handle_request_frame(req)
    assert len(out) == 1
    decoder = IsoTpFramer()
    response = decoder.decode(out[0])
    # Mode 01 response: 0x41 + PID + raw bytes
    assert response == bytes([0x41, 0xAB, 0xCA, 0xFE])


def test_runtime_raw_pid_advertised_in_bitmap() -> None:
    """Mode 01 PID 0xA0 group bitmap must include PID 0xAB when the
    scenario has a raw value for it — otherwise the scan tool won't
    even request it."""
    payload = {
        "vehicle": {"vin": "1HGCM82633A123456"},
        "live_baseline": {"010C": 800, "01AB": "raw:CAFE"},
    }
    state = scenario_to_state(payload)
    ecu = EcuEmulator(state)
    rt = CanRuntime(ecu, _NullBus())
    framer = IsoTpFramer()
    # Request mode 01 PID 0xA0 (which covers PIDs 0xA1..0xC0)
    req = CanFrame(0x7DF, framer.encode(bytes([0x01, 0xA0]))[0].data)
    out = rt.handle_request_frame(req)
    decoder = IsoTpFramer()
    response = decoder.decode(out[0])
    # 0x41 0xA0 + 4 bitmap bytes
    assert response[:2] == bytes([0x41, 0xA0])
    bitmap = response[2:6]
    # PID 0xAB is at position (0xAB - 0xA1) = 10 in this group.
    # Byte 1, bit 7 - (10 % 8) = bit 5.
    assert (bitmap[1] & (1 << 5)) != 0


def test_runtime_invalid_raw_marker_nrcs() -> None:
    """An invalid raw marker should NRC (not crash, not return garbage)
    because is_answerable returned False, so the bitmap won't claim
    support — but if a tool still asks, encode_pid returns None and
    _mode01 produces NRC 0x31."""
    payload = {
        "vehicle": {"vin": "1HGCM82633A123456"},
        "live_baseline": {"01AB": "raw:not_hex"},
    }
    state = scenario_to_state(payload)
    ecu = EcuEmulator(state)
    rt = CanRuntime(ecu, _NullBus())
    framer = IsoTpFramer()
    req = CanFrame(0x7DF, framer.encode(bytes([0x01, 0xAB]))[0].data)
    out = rt.handle_request_frame(req)
    decoder = IsoTpFramer()
    response = decoder.decode(out[0])
    # NRC layout: 0x7F + service + reason
    assert response[0] == 0x7F
    assert response[1] == 0x01


def test_runtime_long_raw_payload_uses_multi_frame() -> None:
    """A raw response longer than 7 bytes (the ISO-TP single-frame
    limit minus mode+pid bytes) needs FF + CFs. Verifies the
    multi-frame path works for arbitrary captured-bytes PIDs."""
    # Build a 16-byte raw payload — too long for a single CAN frame
    raw_hex = "".join(f"{i:02X}" for i in range(16))
    payload = {
        "vehicle": {"vin": "1HGCM82633A123456"},
        "live_baseline": {"01AB": f"raw:{raw_hex}"},
    }
    state = scenario_to_state(payload)
    ecu = EcuEmulator(state)
    rt = CanRuntime(ecu, _NullBus())
    framer = IsoTpFramer()
    req = CanFrame(0x7DF, framer.encode(bytes([0x01, 0xAB]))[0].data)
    out = rt.handle_request_frame(req)
    # Response total: 0x41 + 0xAB + 16 bytes = 18 bytes → needs FF + CFs
    assert len(out) >= 2
    decoder = IsoTpFramer()
    assembled = None
    for frame in out:
        assembled = decoder.decode(frame)
    assert assembled is not None
    assert assembled[:2] == bytes([0x41, 0xAB])
    assert assembled[2:] == bytes(range(16))
