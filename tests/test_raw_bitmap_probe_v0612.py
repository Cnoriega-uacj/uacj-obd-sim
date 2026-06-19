"""
v0.6.12 — Tests for the raw bitmap probe in Elm327Adapter.

Cristopher's bench: the real Mazda3 reports 44 live PIDs on the
Innova, but `supported_pids()` returned 10 because python-obd's
`supported_commands` only contains commands python-obd has decoders
for. The raw bitmap probe queries Mode 01 PID 0x00/0x20/0x40/0x60/...
directly and parses the response bytes — so PIDs python-obd doesn't
recognize still get reported.

These tests build a fake python-obd connection that returns a
known supported-PID bitmap, run `_raw_supported_pids`, and assert
the full set comes back. They also cover the byte-extraction helper
(`_extract_bitmap_bytes`) across the response shapes python-obd
emits in the wild.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from uacj_obd.adapters import elm327 as elm_mod
from uacj_obd.adapters.elm327 import _extract_bitmap_bytes


# ---------------------------------------------------------------------------
# _extract_bitmap_bytes
# ---------------------------------------------------------------------------

def test_extract_from_resp_value_bytes() -> None:
    """python-obd's noop decoder can return raw bytes in `.value`."""
    resp = SimpleNamespace(value=bytes([0xBE, 0x1F, 0xA8, 0x13]), messages=[])
    assert _extract_bitmap_bytes(resp) == bytes([0xBE, 0x1F, 0xA8, 0x13])


def test_extract_from_resp_value_message_list() -> None:
    """When the decoder returns a list of Message objects, pull
    `.data` from the first one."""
    msg = SimpleNamespace(data=bytes([0x80, 0x01, 0x00, 0x00]))
    resp = SimpleNamespace(value=[msg], messages=[msg])
    assert _extract_bitmap_bytes(resp) == bytes([0x80, 0x01, 0x00, 0x00])


def test_extract_falls_back_to_messages_when_value_empty() -> None:
    msg = SimpleNamespace(data=bytes([0x10, 0x20, 0x30, 0x40]))
    resp = SimpleNamespace(value=None, messages=[msg])
    assert _extract_bitmap_bytes(resp) == bytes([0x10, 0x20, 0x30, 0x40])


def test_extract_strips_echo_prefix_when_present() -> None:
    """Some adapters echo the 0x41 (Mode 01 reply) + PID + 4 bitmap
    bytes. The helper should strip the leading 0x41 + PID."""
    resp = SimpleNamespace(
        value=bytes([0x41, 0x00, 0xBE, 0x1F, 0xA8, 0x13]),
        messages=[],
    )
    assert _extract_bitmap_bytes(resp) == bytes([0xBE, 0x1F, 0xA8, 0x13])


def test_extract_returns_empty_on_garbage() -> None:
    assert _extract_bitmap_bytes(SimpleNamespace(value=None, messages=[])) == b""
    assert _extract_bitmap_bytes(SimpleNamespace(value="not bytes", messages=[])) == b""
    short = SimpleNamespace(value=bytes([0xFF]), messages=[])
    assert _extract_bitmap_bytes(short) == b""


def test_extract_ors_multi_ecu_responses() -> None:
    """When multiple ECUs respond (engine + trans on CAN), the helper
    must OR the bitmaps so PIDs from either ECU survive."""
    engine_msg = SimpleNamespace(data=bytes([0x80, 0x00, 0x00, 0x00]))  # PID 0x01
    trans_msg = SimpleNamespace(data=bytes([0x00, 0x00, 0x80, 0x00]))   # PID 0x11
    resp = SimpleNamespace(value=[engine_msg, trans_msg], messages=[])
    merged = _extract_bitmap_bytes(resp)
    assert merged == bytes([0x80, 0x00, 0x80, 0x00])


def test_extract_handles_string_message_raw_method() -> None:
    """Some python-obd versions expose `.raw()` returning a hex string
    instead of `.data` returning bytes — accept that too."""
    msg = SimpleNamespace(raw=lambda: "BE 1F A8 13")
    resp = SimpleNamespace(value=[msg], messages=[])
    assert _extract_bitmap_bytes(resp) == bytes([0xBE, 0x1F, 0xA8, 0x13])


def test_extract_handles_raw_property_not_method() -> None:
    """Older python-obd: `.raw` is a string attribute, not a callable."""
    msg = SimpleNamespace(raw="80 00 00 00")
    resp = SimpleNamespace(value=[msg], messages=[])
    assert _extract_bitmap_bytes(resp) == bytes([0x80, 0x00, 0x00, 0x00])


def test_extract_skips_unparseable_hex_string() -> None:
    """A non-hex `.raw()` string must not crash — just skip."""
    msg = SimpleNamespace(raw=lambda: "not hex zzz")
    resp = SimpleNamespace(value=[msg], messages=[])
    assert _extract_bitmap_bytes(resp) == b""


# ---------------------------------------------------------------------------
# _raw_supported_pids — via a fake python-obd connection
# ---------------------------------------------------------------------------

class _FakeConnection:
    """
    Stands in for the python-obd `OBD` connection. Configured at
    construction with one bitmap response per group PID. Returns a
    SimpleNamespace per query that matches the helper's expected
    response shape.
    """

    def __init__(self, bitmaps: dict[int, bytes]) -> None:
        self._bitmaps = bitmaps
        self.queries: list[bytes] = []

    def is_connected(self) -> bool:
        return True

    def query(self, cmd, force=False):
        self.queries.append(cmd.command)
        # Parse "01XX" out of cmd.command bytes
        group_pid = int(cmd.command.decode("ascii")[2:4], 16)
        if group_pid not in self._bitmaps:
            return SimpleNamespace(
                value=None, messages=[],
                is_null=lambda: True,
            )
        return SimpleNamespace(
            value=self._bitmaps[group_pid],
            messages=[],
            is_null=lambda: False,
        )


def _make_adapter(bitmaps: dict[int, bytes]) -> "elm_mod.Elm327Adapter":
    """Build an Elm327Adapter with a fake OBD connection wired in."""
    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _FakeConnection(bitmaps)
    return adapter


def test_raw_probe_decodes_first_group_bitmap() -> None:
    """0xBE 0x1F 0xA8 0x13 in group 0x00 encodes a known PID set."""
    # Byte 0 = 0xBE = 1011 1110 → PIDs 1, 3, 4, 5, 6, 7 supported
    # Byte 1 = 0x1F = 0001 1111 → PIDs 12, 13, 14, 15, 16 supported
    # Byte 2 = 0xA8 = 1010 1000 → PIDs 17, 19, 21 supported
    # Byte 3 = 0x13 = 0001 0011 → PIDs 28, 31, 32 (32 = 0x20)
    # Note: 0x20 is filtered out (it's the next-group continuation PID)
    bitmaps = {0x00: bytes([0xBE, 0x1F, 0xA8, 0x13])}
    adapter = _make_adapter(bitmaps)
    pids = adapter._raw_supported_pids()
    assert "0101" in pids
    assert "0103" in pids
    assert "0104" in pids
    assert "0107" in pids
    assert "010C" in pids
    assert "010D" in pids
    assert "0111" in pids
    assert "011F" in pids
    # 0x20 is filtered (continuation marker, not a real data PID)
    assert "0120" not in pids


def test_raw_probe_stops_when_continuation_bit_clear() -> None:
    """If byte 3's LSB is 0, no later bitmaps are queried."""
    # 0x13 has bit 0 = 1 → continuation supported, more groups expected
    # 0x12 has bit 0 = 0 → stop here
    bitmaps = {
        0x00: bytes([0xBE, 0x1F, 0xA8, 0x12]),
        0x20: bytes([0xFF, 0xFF, 0xFF, 0xFF]),  # would add PIDs if queried
    }
    adapter = _make_adapter(bitmaps)
    pids = adapter._raw_supported_pids()
    # PIDs 0x21-0x40 should NOT be in the set because we stopped early.
    assert "0121" not in pids
    assert "0140" not in pids


def test_raw_probe_continues_when_continuation_bit_set() -> None:
    """Continuation = 1 means probe the next group."""
    bitmaps = {
        0x00: bytes([0x80, 0x00, 0x00, 0x01]),  # PID 0x01 only + continue
        0x20: bytes([0x80, 0x00, 0x00, 0x00]),  # PID 0x21 only + stop
    }
    adapter = _make_adapter(bitmaps)
    pids = adapter._raw_supported_pids()
    assert "0101" in pids
    assert "0121" in pids


def test_raw_probe_returns_empty_when_disconnected() -> None:
    """A disconnected adapter should return empty, not crash."""
    adapter = elm_mod.Elm327Adapter()
    # No _conn set → _ensure() raises
    assert adapter._raw_supported_pids() == set()


def test_raw_probe_returns_empty_when_obd_lib_missing(monkeypatch) -> None:
    """If python-obd isn't importable, return empty cleanly."""
    bitmaps = {0x00: bytes([0xFF, 0xFF, 0xFF, 0xFF])}
    adapter = _make_adapter(bitmaps)  # construct while _HAS_OBD=True
    monkeypatch.setattr(elm_mod, "_HAS_OBD", False)  # then simulate missing
    assert adapter._raw_supported_pids() == set()


def test_raw_probe_swallows_query_errors() -> None:
    """A query that raises (timeout, no response) should skip the
    group, not propagate."""

    class _RaisingConnection(_FakeConnection):
        def query(self, cmd, force=False):
            raise RuntimeError("simulated timeout")

    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _RaisingConnection({})
    assert adapter._raw_supported_pids() == set()


def test_raw_probe_handles_null_response() -> None:
    """is_null() True → skip the group."""

    class _NullConnection(_FakeConnection):
        def query(self, cmd, force=False):
            return SimpleNamespace(value=None, messages=[], is_null=lambda: True)

    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _NullConnection({})
    assert adapter._raw_supported_pids() == set()


# ---------------------------------------------------------------------------
# supported_pids — integration: raw probe takes priority, falls back
# ---------------------------------------------------------------------------

def test_supported_pids_prefers_raw_probe() -> None:
    """When the raw probe returns PIDs, that's the answer — the
    python-obd supported_commands set is ignored."""
    bitmaps = {0x00: bytes([0x80, 0x00, 0x00, 0x00])}  # PID 0x01 only

    adapter = elm_mod.Elm327Adapter()

    class _Conn(_FakeConnection):
        @property
        def supported_commands(self):
            # The fallback path would return these — but we should not see them.
            return [SimpleNamespace(mode=1, pid=0x0C), SimpleNamespace(mode=1, pid=0x0D)]

    adapter._conn = _Conn(bitmaps)
    pids = adapter.supported_pids()
    assert pids == {"0101"}


def test_raw_probe_unions_multi_ecu_responses() -> None:
    """Engine + trans both answering Mode 01 0x00 should be merged.
    Build a fake connection where the response has both ECUs' bitmaps
    in `value` — verify PIDs from both ECUs end up in the set."""

    class _MultiEcuConn(_FakeConnection):
        def query(self, cmd, force=False):
            engine = SimpleNamespace(data=bytes([0x80, 0x00, 0x00, 0x00]))  # PID 0x01
            trans = SimpleNamespace(data=bytes([0x00, 0x00, 0x80, 0x00]))   # PID 0x11
            return SimpleNamespace(
                value=[engine, trans],
                messages=[engine, trans],
                is_null=lambda: False,
            )

    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _MultiEcuConn({})
    pids = adapter._raw_supported_pids()
    assert "0101" in pids
    assert "0111" in pids


def test_raw_probe_warns_on_implausibly_low_count(caplog) -> None:
    """A response that decodes to just 1 PID should trigger a WARNING
    so a real-bench diagnostic notices the bitmap parse is suspect."""
    # 0x80 0x00 0x00 0x00 → only PID 0x01 supported, no continuation
    bitmaps = {0x00: bytes([0x80, 0x00, 0x00, 0x00])}
    adapter = _make_adapter(bitmaps)
    import logging
    with caplog.at_level(logging.WARNING, logger="uacj_obd.adapters.elm327"):
        adapter._raw_supported_pids()
    assert any("implausibly low" in r.message for r in caplog.records)


def test_raw_probe_no_warning_on_plausible_count(caplog) -> None:
    """A normal response (≥3 PIDs) should NOT warn."""
    bitmaps = {0x00: bytes([0xBE, 0x1F, 0xA8, 0x12])}
    adapter = _make_adapter(bitmaps)
    import logging
    with caplog.at_level(logging.WARNING, logger="uacj_obd.adapters.elm327"):
        adapter._raw_supported_pids()
    assert not any("implausibly low" in r.message for r in caplog.records)


def test_raw_probe_includes_0xe0_group() -> None:
    """The probe should walk through group 0xE0 too, not stop at 0xC0,
    so vehicles that report PIDs > 0xE0 get full coverage."""
    bitmaps = {
        0x00: bytes([0x80, 0x00, 0x00, 0x01]),
        0x20: bytes([0x00, 0x00, 0x00, 0x01]),
        0x40: bytes([0x00, 0x00, 0x00, 0x01]),
        0x60: bytes([0x00, 0x00, 0x00, 0x01]),
        0x80: bytes([0x00, 0x00, 0x00, 0x01]),
        0xA0: bytes([0x00, 0x00, 0x00, 0x01]),
        0xC0: bytes([0x00, 0x00, 0x00, 0x01]),
        0xE0: bytes([0x80, 0x00, 0x00, 0x00]),  # PID 0xE1 supported
    }
    adapter = _make_adapter(bitmaps)
    pids = adapter._raw_supported_pids()
    assert "01E1" in pids


def test_supported_pids_falls_back_when_raw_probe_empty() -> None:
    """If the raw probe returns empty (all groups failed), use
    python-obd's supported_commands."""

    class _Conn(_FakeConnection):
        def query(self, cmd, force=False):
            return SimpleNamespace(value=None, messages=[], is_null=lambda: True)

        @property
        def supported_commands(self):
            return [
                SimpleNamespace(mode=1, pid=0x0C),
                SimpleNamespace(mode=1, pid=0x0D),
            ]

    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _Conn({})
    pids = adapter.supported_pids()
    assert pids == {"010C", "010D"}
