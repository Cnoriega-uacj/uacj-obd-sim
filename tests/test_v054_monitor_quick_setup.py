"""
v0.5.4 — Tests for the scenario-editor monitor quick-setup buttons.

The actual buttons are JS in `web/scenarios.html`. The contract that
matters for correctness is:

  1. Every name in the JS `STANDARD_MONITORS` array is recognised by
     the simulator's `_MONITOR_NAME_TO_POSITION` table. If someone
     changes one side without the other, the bit-packing silently
     drops monitors and the Innova hides badges — exactly the bug
     Cristopher chased for hours.

  2. The 11-monitor preset produces sensible MID-bitmap bytes when
     run through `_encode_monitors_per_j1979`. Specifically, the
     5 "all on" non-continuous monitors must land in different bit
     positions in byte C.

This module locks both invariants in.
"""

from __future__ import annotations

import re
from pathlib import Path

from uacj_obd.simulator.can_runtime import (
    _MONITOR_NAME_TO_POSITION,
    _encode_monitors_per_j1979,
)


# Single source of truth for what the dashboard's "Set up standard
# monitors" button populates. Kept here in sync with the JS const
# `STANDARD_MONITORS` in web/scenarios.html. The test below also
# verifies the JS file contains every entry — if you add or remove
# from this list, update the JS too.
EXPECTED_STANDARD_MONITORS = [
    ("Misfire",                  True,  True),
    ("Fuel System",              True,  True),
    ("Comprehensive Components", True,  True),
    ("Catalyst",                 True,  True),
    ("Heated Catalyst",          True,  True),
    ("Evaporative System",       True,  True),
    ("Secondary Air System",     False, False),
    ("A/C System Refrigerant",   False, False),
    ("Oxygen Sensor",            True,  True),
    ("Oxygen Sensor Heater",     True,  True),
    ("EGR System",               True,  True),
]


def test_every_standard_monitor_name_is_recognised_by_simulator() -> None:
    """If a name in the JS preset doesn't appear in the simulator's
    table, the bit gets silently dropped when the scenario is pushed.
    Lock in the symmetry."""
    for name, _, _ in EXPECTED_STANDARD_MONITORS:
        key = name.lower().strip()
        assert key in _MONITOR_NAME_TO_POSITION, (
            f"Standard monitor name {name!r} is not in the simulator's "
            f"_MONITOR_NAME_TO_POSITION — bit will be dropped. "
            f"Add an entry to can_runtime.py."
        )


def test_standard_monitors_pack_into_expected_j1979_bytes() -> None:
    """v0.5.4 preset: 6 monitors supported+ready in the continuous and
    non-continuous ranges. Catalyst / Heated Catalyst / EVAP / O2 sensor
    / O2 heater / EGR all supported and complete; secondary air and
    A/C marked unsupported."""
    monitors = [
        {"name": name, "supported": sup, "ready": rdy}
        for name, sup, rdy in EXPECTED_STANDARD_MONITORS
    ]
    byte_b, byte_c, byte_d = _encode_monitors_per_j1979(monitors)
    # byte B: 3 continuous monitors all supported (bits 0-2) and all
    # complete (bits 4-6 clear → upper nibble 0).
    assert byte_b & 0b00000111 == 0b00000111   # Misfire/Fuel/CCM supported
    assert byte_b & 0b01110000 == 0            # all complete
    # byte C: CAT/HCAT/EVAP/O2/HTR/EGR supported (bits 0,1,2,5,6,7);
    # AIR (bit 3) and A/C (bit 4) NOT supported.
    expected_c = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 5) | (1 << 6) | (1 << 7)
    assert byte_c == expected_c, f"got byte C = 0x{byte_c:02X}, expected 0x{expected_c:02X}"
    # byte D: nothing not-complete (everything supported is also ready).
    assert byte_d == 0, f"byte D should be 0 (all ready), got 0x{byte_d:02X}"


def test_js_file_contains_all_expected_monitor_names() -> None:
    """If you edit `EXPECTED_STANDARD_MONITORS` above without updating
    the JS, this test fails so the dashboard and the Python source of
    truth never drift."""
    js_path = Path(__file__).parent.parent / "web" / "scenarios.html"
    js = js_path.read_text(encoding="utf-8")
    # Find the STANDARD_MONITORS const block.
    start = js.find("STANDARD_MONITORS")
    assert start != -1, "STANDARD_MONITORS const missing from scenarios.html"
    block_end = js.find("];", start)
    block = js[start:block_end]
    for name, _, _ in EXPECTED_STANDARD_MONITORS:
        # JS string literals are double-quoted in the file.
        assert f'"{name}"' in block, (
            f"Monitor name {name!r} expected in JS STANDARD_MONITORS array "
            f"but not found. Update web/scenarios.html or this test."
        )


def test_all_ready_button_semantics_via_encoder() -> None:
    """Simulates the 'All ready' button by setting every monitor to
    supported+ready, then checks no `not_complete` bit is set anywhere."""
    monitors = [
        {"name": name, "supported": True, "ready": True}
        for name, _, _ in EXPECTED_STANDARD_MONITORS
    ]
    byte_b, byte_c, byte_d = _encode_monitors_per_j1979(monitors)
    # Upper nibble of byte B = continuous "not complete" bits — must be 0.
    assert byte_b & 0xF0 == 0
    # Byte D = non-continuous "not complete" — must be 0.
    assert byte_d == 0


def test_all_incomplete_button_semantics_via_encoder() -> None:
    """'All incomplete' button: every monitor supported but not ready.
    Byte B upper nibble + byte D should have the not-complete bits set
    matching byte C's supported bits."""
    monitors = [
        {"name": name, "supported": True, "ready": False}
        for name, _, _ in EXPECTED_STANDARD_MONITORS
    ]
    byte_b, byte_c, byte_d = _encode_monitors_per_j1979(monitors)
    # All 3 continuous incomplete: bits 4,5,6 of byte B
    assert byte_b & 0b01110000 == 0b01110000
    # byte D mirrors byte C exactly — every supported monitor is also
    # not-complete.
    assert byte_d == byte_c
