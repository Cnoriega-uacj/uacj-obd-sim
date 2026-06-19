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
    _MODE09_IMPLEMENTED_PIDS,
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
