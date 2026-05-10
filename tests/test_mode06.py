"""
Mode 0x06 — on-board monitoring test results.

Used by emissions-readiness scan tools (CARB compliance / IM240) to
read each completed monitor's measured value plus the pass/fail
brackets. Pre-2002 vehicles use a different mode 06 layout (UASID
addressing); we implement the post-2002 CAN form per SAE J1979.
"""

from __future__ import annotations

from uacj_obd.simulator.can_runtime import scenario_to_state
from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState


def test_mode06_specific_tid_returns_packed_test_data():
    state = ScenarioState(obd_test_results={
        # TID 0x01 — catalyst monitor bank 1, value 0x1234, min 0x0500, max 0x2000
        0x01: (0x21, 0x1234, 0x0500, 0x2000),
    })
    ecu = EcuEmulator(state)
    response = ecu.handle(bytes([0x06, 0x01]))
    assert response[0] == 0x46  # positive response
    assert response[1] == 0x01  # echo TID
    assert response[2] == 0x21  # CID
    assert response[3] == 0x00  # UASID
    assert (response[4] << 8) | response[5] == 0x1234  # value
    assert (response[6] << 8) | response[7] == 0x0500  # min
    assert (response[8] << 8) | response[9] == 0x2000  # max


def test_mode06_unknown_tid_returns_bare_service_byte():
    """Vehicles with mode 06 supported but a TID with no completed test
    typically respond with just 0x46 (no data), not an NRC."""
    ecu = EcuEmulator(ScenarioState(obd_test_results={0x01: (0x21, 0x10, 0, 0xFF)}))
    response = ecu.handle(bytes([0x06, 0xFE]))
    assert response == bytes([0x46])


def test_mode06_bare_request_enumerates_all_configured_tests():
    state = ScenarioState(obd_test_results={
        0x01: (0x21, 0x1100, 0x0100, 0x2000),
        0x05: (0x22, 0x0220, 0x0050, 0x0500),
    })
    ecu = EcuEmulator(state)
    response = ecu.handle(bytes([0x06]))
    assert response[0] == 0x46
    # Header (1) + 9 bytes per test * 2 tests = 19 bytes total
    assert len(response) == 1 + 9 * 2
    # First entry should be TID 0x01 (sorted by TID)
    assert response[1] == 0x01
    assert response[10] == 0x05  # second entry's TID


def test_mode06_with_empty_results_returns_no_data_marker():
    """If the scenario has no test results yet, mode 06 still answers
    positively but with just the service byte — mirrors a CARB tester
    seeing 'monitor not yet completed'."""
    ecu = EcuEmulator(ScenarioState())
    response = ecu.handle(bytes([0x06]))
    assert response == bytes([0x46])


def test_scenario_to_state_loads_obd_test_results_from_payload():
    payload = {
        "obd_test_results": {
            "01": [0x21, 0x1234, 0x0500, 0x2000],
            "05": {"cid": 0x22, "value": 100, "min": 50, "max": 200},
        }
    }
    state = scenario_to_state(payload)
    assert state.obd_test_results[0x01] == (0x21, 0x1234, 0x0500, 0x2000)
    assert state.obd_test_results[0x05] == (0x22, 100, 50, 200)
