"""ECU emulator dispatch tests."""

from __future__ import annotations

from uacj_obd.simulator.ecu import EcuEmulator, ScenarioState


def _ecu(**kwargs) -> EcuEmulator:
    return EcuEmulator(ScenarioState(**kwargs))


def test_mode01_rpm_round_trip() -> None:
    ecu = _ecu(live={"010C": 1850})
    resp = ecu.handle(bytes([0x01, 0x0C]))
    # 0x41, 0x0C, then RPM*4 = 7400 = 0x1CE8
    assert resp[0] == 0x41
    assert resp[1] == 0x0C
    raw = (resp[2] << 8) | resp[3]
    assert raw / 4 == 1850


def test_mode01_pid00_supported_bitmap() -> None:
    ecu = _ecu(live={"010C": 1000, "010D": 50, "0105": 90})
    resp = ecu.handle(bytes([0x01, 0x00]))
    assert resp[0] == 0x41
    assert resp[1] == 0x00
    bitmap = resp[2:]
    # PID 0x05 is at index 4 in group 0x00 — bit 7 of byte 0
    assert bitmap[0] & (1 << (7 - 4))
    # PID 0x0C is index 11 — bit 4 of byte 1
    assert bitmap[1] & (1 << (7 - (11 - 8)))
    # PID 0x0D index 12 — bit 3 of byte 1
    assert bitmap[1] & (1 << (7 - (12 - 8)))


def test_mode01_unsupported_pid_returns_nrc() -> None:
    ecu = _ecu(live={})
    resp = ecu.handle(bytes([0x01, 0xFF]))
    assert resp[0] == 0x7F
    assert resp[1] == 0x01


def test_mode01_pid01_byte_a_no_dtcs() -> None:
    ecu = _ecu()
    resp = ecu.handle(bytes([0x01, 0x01]))
    # bit 7 = MIL off, bits 0-6 = 0 DTCs
    assert resp[:2] == bytes([0x41, 0x01])
    assert resp[2] == 0x00


def test_mode01_pid01_byte_a_one_stored_dtc_turns_mil_on() -> None:
    ecu = _ecu(dtcs_stored=["P0420"])
    resp = ecu.handle(bytes([0x01, 0x01]))
    # bit 7 set (MIL on) | 1 DTC
    assert resp[2] == 0x81


def test_mode01_pid01_byte_a_dtc_count_saturates_at_127() -> None:
    ecu = _ecu(dtcs_stored=[f"P{n:04X}" for n in range(200)])
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[2] == 0xFF  # MIL on + 127


def test_mode01_pid01_byte_a_pending_only_does_not_turn_mil_on() -> None:
    # Per SAE J1979, MIL only illuminates for stored (confirmed) DTCs.
    ecu = _ecu(dtcs_pending=["P0171"])
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[2] == 0x00


def test_mode01_pid01_bytes_bcd_come_from_scenario_state() -> None:
    ecu = _ecu(
        dtcs_stored=["P0420"],
        monitor_b=0x07,
        monitor_c=0xE7,
        monitor_d=0x01,
    )
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[2] == 0x81  # byte A derived (MIL on, 1 DTC)
    assert resp[3] == 0x07
    assert resp[4] == 0xE7
    assert resp[5] == 0x01


def test_mode03_returns_packed_dtcs() -> None:
    ecu = _ecu(dtcs_stored=["P0420", "P0171"])
    resp = ecu.handle(bytes([0x03]))
    assert resp[0] == 0x43
    assert resp[1] == 2  # count
    # P0420 → high nibble letter P (00), digits 0x0420 → bytes 0x04 0x20
    assert resp[2:4] == bytes([0x04, 0x20])
    # P0171 → 0x01 0x71
    assert resp[4:6] == bytes([0x01, 0x71])


def test_mode04_clears_stored_and_pending_only() -> None:
    state = ScenarioState(
        dtcs_stored=["P0420"],
        dtcs_pending=["P0171"],
        dtcs_permanent=["P0301"],
        freeze_dtc="P0420",
    )
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x04]))
    assert resp == bytes([0x44])
    assert state.dtcs_stored == []
    assert state.dtcs_pending == []
    # Permanent DTCs and freeze frame for them survive a clear (per spec)
    assert state.dtcs_permanent == ["P0301"]
    assert state.freeze_dtc is None  # we wipe freeze on clear; clarified in code


def test_mode09_vin_round_trip() -> None:
    ecu = _ecu(vin="2HGFC2F59FH123456")
    resp = ecu.handle(bytes([0x09, 0x02]))
    assert resp[0:3] == bytes([0x49, 0x02, 0x01])
    assert resp[3:].decode("ascii").startswith("2HGFC2F59FH123456")


def test_mode09_vin_missing_returns_nrc() -> None:
    ecu = _ecu()
    resp = ecu.handle(bytes([0x09, 0x02]))
    assert resp[0] == 0x7F


def test_mode07_pending_vs_mode0a_permanent() -> None:
    ecu = _ecu(dtcs_pending=["P0171"], dtcs_permanent=["P0301"])
    p = ecu.handle(bytes([0x07]))
    assert p[0] == 0x47
    assert p[1] == 1
    a = ecu.handle(bytes([0x0A]))
    assert a[0] == 0x4A
    assert a[1] == 1


def test_unknown_service_returns_nrc() -> None:
    ecu = _ecu()
    resp = ecu.handle(bytes([0x99]))
    assert resp[0] == 0x7F
    assert resp[1] == 0x99
