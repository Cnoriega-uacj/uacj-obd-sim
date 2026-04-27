"""End-to-end test: scan-tool-style request → CAN frame → ECU → response frames."""

from __future__ import annotations

from uacj_obd.simulator.can_runtime import CanRuntime, scenario_to_state
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
