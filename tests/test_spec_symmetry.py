"""
v0.4.13 — Spec symmetry tests.

These tests address Pattern E ("advertised capability ≠ implemented
capability") found during the v0.4.12 audit. Static "supported PIDs"
bitmaps could silently drift away from what the dispatcher actually
handles. The fix is to derive bitmaps from a single source-of-truth
set, but a test layer is what GUARANTEES the symmetry holds going
forward.

For every Mode that advertises a supported-PIDs bitmap, this module
asserts:

  1. Every PID listed in the bitmap is answered by the dispatcher
     (not NRC).
  2. Every PID the dispatcher answers (within the 0x01-0x20 bitmap
     range) is listed in the bitmap.

If anyone adds a new Mode 09 PID without updating
`_MODE09_IMPLEMENTED_PIDS`, OR adds a PID to the set without writing
a dispatcher branch, these tests fail loudly.
"""

from __future__ import annotations

from uacj_obd.simulator.ecu import (
    _MODE06_BITMAP_TIDS,
    _MODE09_IMPLEMENTED_PIDS,
    _mode06_supported_bitmap,
    _mode09_supported_bitmap,
    EcuEmulator,
    ScenarioState,
)


def _ecu_with_everything() -> EcuEmulator:
    """ECU with every Mode 09 field populated so each PID returns a
    positive response rather than NRC for "missing data"."""
    state = ScenarioState(
        vin="JM1BL1L72C1627697",
        calibration_id="PE2GEM000PE06020",
        cvn="CDA08E85",
        ecu_name="ECM",
    )
    return EcuEmulator(state)


def _bitmap_to_pids(bitmap: bytes) -> set[int]:
    """Decode a 4-byte supported-PIDs bitmap into the set of advertised
    PIDs. Inverse of `_mode09_supported_bitmap`."""
    raw = int.from_bytes(bitmap, "big")
    pids = set()
    for pid in range(1, 0x21):
        bit = 32 - pid
        if raw & (1 << bit):
            pids.add(pid)
    return pids


def test_mode09_bitmap_advertises_exactly_what_is_implemented() -> None:
    """Every PID in `_MODE09_IMPLEMENTED_PIDS` (within bitmap range) must
    be advertised, and only those."""
    advertised = _bitmap_to_pids(_mode09_supported_bitmap())
    expected = {p for p in _MODE09_IMPLEMENTED_PIDS if 1 <= p <= 0x20}
    assert advertised == expected, (
        f"Mode 09 bitmap drift! advertised={sorted(advertised)} "
        f"expected={sorted(expected)}"
    )


def test_mode09_every_advertised_pid_returns_positive_response() -> None:
    """For every PID we advertise, the dispatcher MUST return a positive
    response (not NRC). Otherwise we are lying to scan tools about what
    we support."""
    ecu = _ecu_with_everything()
    bitmap = _mode09_supported_bitmap()
    advertised = _bitmap_to_pids(bitmap)
    nrc_pids = []
    for pid in advertised:
        resp = ecu.handle(bytes([0x09, pid]))
        if resp[0] == 0x7F:
            nrc_pids.append(pid)
    assert not nrc_pids, (
        f"Mode 09 advertises PIDs {nrc_pids} but the dispatcher NRCs "
        f"them. Bitmap ↔ implementation drift."
    )


def test_mode09_every_implemented_pid_is_in_advertised_set() -> None:
    """The constant `_MODE09_IMPLEMENTED_PIDS` IS the source of truth, but
    in case a future contributor adds a dispatcher branch without
    updating the set, this test catches it. We probe every Mode 09 PID
    in the bitmap range and check the answered ones are exactly the
    set we declare."""
    ecu = _ecu_with_everything()
    answered = set()
    for pid in range(0x01, 0x21):
        resp = ecu.handle(bytes([0x09, pid]))
        if resp[0] != 0x7F:
            answered.add(pid)
    expected = {p for p in _MODE09_IMPLEMENTED_PIDS if 1 <= p <= 0x20}
    assert answered == expected, (
        f"Mode 09 dispatcher answers PIDs {sorted(answered)} but "
        f"`_MODE09_IMPLEMENTED_PIDS` declares {sorted(expected)}"
    )


def test_mode09_advertised_pid_set_matches_real_scan_tools() -> None:
    """v0.4.12 fix locked in: client's Innova 5210 queries Mode 09 PIDs
    0x02 (VIN), 0x04 (Cal ID), 0x06 (CVN), and 0x0A (ECU name). All
    four must remain advertised."""
    advertised = _bitmap_to_pids(_mode09_supported_bitmap())
    for required in (0x02, 0x04, 0x06, 0x0A):
        assert required in advertised, (
            f"PID 0x{required:02X} must be advertised — real Innova queries it"
        )


def test_mode09_message_count_pids_return_one() -> None:
    """v0.4.13: PIDs 0x01 / 0x03 / 0x05 are the "how many messages will
    you send" pre-queries that strict scan tools issue before asking
    for the actual VIN/Cal ID/CVN. We always answer 1 because we
    emulate a single ECU."""
    ecu = _ecu_with_everything()
    for pid in (0x01, 0x03, 0x05):
        resp = ecu.handle(bytes([0x09, pid]))
        assert resp[0] == 0x49, f"PID 0x{pid:02X} returned NRC"
        assert resp[1] == pid
        assert resp[2] == 0x01, f"PID 0x{pid:02X} should report 1 message"


# ---------------------------------------------------------------------------
# Mode 06 symmetry (v0.4.13 second sweep)
# ---------------------------------------------------------------------------

def _ecu_with_mode06_results() -> EcuEmulator:
    """ECU configured with three Mode 06 test results across two
    different MID ranges so the bitmap exercise has real data to
    advertise."""
    state = ScenarioState(
        obd_test_results={
            0x01: (0x0B, 0x0064, 0x0000, 0x00C8),  # MID 0x01 — O2 sensor B1S1
            0x05: (0x0B, 0x0080, 0x0000, 0x00FF),  # MID 0x05 — O2 sensor B2S1
            0x21: (0x0C, 0x0200, 0x0100, 0x0300),  # MID 0x21 — catalyst monitor
        },
    )
    return EcuEmulator(state)


def test_mode06_bitmap_advertises_implemented_mids_in_range_0x00() -> None:
    """Bitmap TID 0x00 advertises MIDs 0x01-0x20. With test results
    configured for MIDs 0x01 and 0x05, the bitmap must have bits 31
    and 27 set (and nothing else in that range)."""
    ecu = _ecu_with_mode06_results()
    resp = ecu.handle(bytes([0x06, 0x00]))
    assert resp[0] == 0x46
    assert resp[1] == 0x00
    bitmap = int.from_bytes(resp[2:6], "big")
    # MID 0x01 → bit 31 (0x80000000); MID 0x05 → bit 27 (0x08000000)
    expected = 0x80000000 | 0x08000000
    assert bitmap == expected, (
        f"Mode 06 bitmap (TID 0x00) drift: got 0x{bitmap:08X}, "
        f"expected 0x{expected:08X}"
    )


def test_mode06_bitmap_advertises_implemented_mids_in_range_0x20() -> None:
    """Bitmap TID 0x20 advertises MIDs 0x21-0x40. MID 0x21 is configured,
    so bit 31 of that bitmap should be set."""
    ecu = _ecu_with_mode06_results()
    resp = ecu.handle(bytes([0x06, 0x20]))
    bitmap = int.from_bytes(resp[2:6], "big")
    assert bitmap == 0x80000000


def test_mode06_bitmap_empty_when_no_results_configured() -> None:
    ecu = EcuEmulator(ScenarioState())
    resp = ecu.handle(bytes([0x06, 0x00]))
    assert resp[2:6] == bytes([0, 0, 0, 0])


def test_mode06_bitmap_symmetry_for_every_advertised_range() -> None:
    """For every range bitmap TID, decoding the bitmap and looking up
    each advertised MID in the dispatcher must return a positive
    response. Catches any future Pattern E drift between the bitmap
    derivation and the dispatcher behaviour."""
    ecu = _ecu_with_mode06_results()
    for tid in sorted(_MODE06_BITMAP_TIDS):
        bitmap_resp = ecu.handle(bytes([0x06, tid]))
        if bitmap_resp[0] == 0x7F:
            continue  # range not relevant for this scenario, fine
        bitmap = int.from_bytes(bitmap_resp[2:6], "big")
        for offset in range(1, 0x21):
            bit = 32 - offset
            if not (bitmap & (1 << bit)):
                continue
            mid = tid + offset
            # Advertised MID must produce a non-NRC response.
            resp = ecu.handle(bytes([0x06, mid]))
            assert resp[0] == 0x46, (
                f"Mode 06 bitmap at TID 0x{tid:02X} advertises MID "
                f"0x{mid:02X} but dispatcher NRCs it"
            )


def test_mode06_dispatcher_matches_state_obd_test_results() -> None:
    """The reverse direction: every MID that the scenario configures
    must be advertised by the appropriate bitmap. No 'works but not
    advertised' silent gap."""
    ecu = _ecu_with_mode06_results()
    configured_mids = {0x01, 0x05, 0x21}
    advertised_mids: set[int] = set()
    for tid in sorted(_MODE06_BITMAP_TIDS):
        bitmap_resp = ecu.handle(bytes([0x06, tid]))
        bitmap = int.from_bytes(bitmap_resp[2:6], "big")
        for offset in range(1, 0x21):
            bit = 32 - offset
            if bitmap & (1 << bit):
                advertised_mids.add(tid + offset)
    assert configured_mids.issubset(advertised_mids), (
        f"Configured MIDs {configured_mids - advertised_mids} are not "
        f"advertised by any bitmap range — Pattern E drift."
    )
