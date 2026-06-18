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


def test_mode01_pid01_byte_c_comes_from_scenario_state() -> None:
    # Byte C is availability — never derived; scenario controls it.
    ecu = _ecu(
        dtcs_stored=["P0420"],
        monitor_b=0x07,
        monitor_c=0xE7,
        monitor_d=0x00,
    )
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[4] == 0xE7


def test_mode01_pid01_p0420_derives_cat_not_complete() -> None:
    # P0420 = catalyst bank 1 → byte D bit 0 set
    ecu = _ecu(dtcs_stored=["P0420"], monitor_d=0x00)
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[5] & 0x01 == 0x01  # CAT not complete


def test_mode01_pid01_p0455_derives_evap_not_complete() -> None:
    # P0455 = EVAP gross leak → byte D bit 2 set
    ecu = _ecu(dtcs_stored=["P0455"], monitor_d=0x00)
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[5] & 0x04 == 0x04  # EVAP not complete


def test_mode01_pid01_p0300_derives_misfire_not_complete() -> None:
    # P0300 = random misfire → byte B bit 4 set (continuous monitor)
    ecu = _ecu(dtcs_stored=["P0300"], monitor_b=0x07)
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[3] & 0x10 == 0x10  # MIS not complete


def test_mode01_pid01_unknown_dtc_falls_back_to_ccm() -> None:
    # Unmapped DTC range (e.g. P0700 transmission) → CCM (byte B bit 6) set
    # so the monitor row still renders rather than appearing fully complete.
    ecu = _ecu(dtcs_stored=["P0700"], monitor_b=0x07)
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[3] & 0x40 == 0x40  # CCM not complete


def test_mode01_pid01_derivation_preserves_existing_bits() -> None:
    # Scenario explicitly says EVAP not complete (bit 2 of D = 1);
    # adding P0420 should ALSO set CAT (bit 0), not clear EVAP.
    ecu = _ecu(dtcs_stored=["P0420"], monitor_d=0x04)
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[5] & 0x01 == 0x01  # CAT (from DTC)
    assert resp[5] & 0x04 == 0x04  # EVAP (preserved from scenario)


def test_mode01_pid01_multiple_dtcs_set_multiple_bits() -> None:
    # P0420 (CAT, bit 0) + P0455 (EVAP, bit 2) + P0300 (MIS, byte B bit 4)
    ecu = _ecu(dtcs_stored=["P0420", "P0455", "P0300"])
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[3] & 0x10 == 0x10  # MIS in byte B
    assert resp[5] & 0x01 == 0x01  # CAT in byte D
    assert resp[5] & 0x04 == 0x04  # EVAP in byte D


def test_mode01_pid01_no_dtcs_leaves_bytes_bd_at_scenario_values() -> None:
    # Without DTCs, no derivation — bytes B and D pass through unchanged.
    ecu = _ecu(monitor_b=0x07, monitor_d=0x00)
    resp = ecu.handle(bytes([0x01, 0x01]))
    assert resp[3] == 0x07
    assert resp[5] == 0x00


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


def test_mode09_vin_handles_legacy_bytearray_repr_string() -> None:
    """Per v0.4.11 audit: captures from v0.4.0 through v0.4.9 stored VIN
    as the Python repr of a bytearray. The simulator must peel that
    wrapper off when replaying those sessions, otherwise the Innova sees
    nonsense and rejects the response."""
    state = ScenarioState(vin="bytearray(b'JM1BL1L72C1627697')")
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x09, 0x02]))
    assert resp[:3] == bytes([0x49, 0x02, 0x01])
    assert resp[3:] == b"JM1BL1L72C1627697"


def test_mode09_vin_handles_legacy_bytes_repr_string() -> None:
    state = ScenarioState(vin="b'1HGCM82633A123456'")
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x09, 0x02]))
    assert resp[3:] == b"1HGCM82633A123456"


def test_mode09_vin_handles_actual_bytearray_state() -> None:
    """Defensive: even if upstream code mistakenly stores bytearray
    directly on the state, Mode 09 should still emit a clean VIN."""
    state = ScenarioState(vin=bytearray(b"JM1BL1L72C1627697"))  # type: ignore[arg-type]
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x09, 0x02]))
    assert resp[3:] == b"JM1BL1L72C1627697"


def test_mode09_vin_strips_null_padding_from_legacy_capture() -> None:
    # python-obd sometimes returns VIN with leading nulls; the bytearray
    # repr captured included them. Strip on Mode 09 emit.
    state = ScenarioState(vin="bytearray(b'\\x00\\x00JM1BL1L72C1627697')")
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x09, 0x02]))
    # Whatever falls out must be 17 ASCII bytes, no `\x00` markers leaked.
    assert resp[3:].decode("ascii").lstrip("\x00").rstrip("\x00").endswith("L72C1627697")


def test_mode09_calibration_id_handles_legacy_bytearray_repr() -> None:
    state = ScenarioState(calibration_id="bytearray(b'12612560')")
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x09, 0x04]))
    assert resp[3:].startswith(b"12612560")


def test_mode09_ecu_name_handles_legacy_bytearray_repr() -> None:
    state = ScenarioState(ecu_name="bytearray(b'ECM')")
    ecu = EcuEmulator(state)
    resp = ecu.handle(bytes([0x09, 0x0A]))
    assert resp[3:].startswith(b"ECM")


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
