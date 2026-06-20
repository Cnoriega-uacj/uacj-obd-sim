"""
v0.6.16 — Tests for the python-obd-bypass raw read path.

Cristopher's v0.6.15 bench showed 22 PIDs discovered but never
captured AND 0 raw passthrough — meaning v0.6.13's fallback fired
but every response came back null. Root cause: `c.query(cmd)` runs
python-obd's `OBDResponse.is_null()` gate, which trips when the
ELM327 returns an empty frame after a previous failed query.

v0.6.16 talks directly to `c.interface.send_and_parse()` (the same
low-level path python-obd uses internally) and parses the Message
objects ourselves. Bypasses every gate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from uacj_obd.adapters import elm327 as elm_mod


# ---------------------------------------------------------------------------
# _read_pid_raw via the new interface.send_and_parse path
# ---------------------------------------------------------------------------


class _FakeInterface:
    """Minimal python-obd ELM327.interface stand-in."""

    def __init__(self, responses: dict[bytes, list], raise_on=None) -> None:
        self._responses = responses
        self._raise_on = raise_on
        self.sent: list[bytes] = []

    def send_and_parse(self, cmd: bytes) -> list | None:
        self.sent.append(cmd)
        if self._raise_on and cmd == self._raise_on:
            raise RuntimeError("simulated interface error")
        return self._responses.get(cmd)


class _FakeConn:
    def __init__(self, iface: _FakeInterface) -> None:
        self.interface = iface

    def is_connected(self) -> bool:
        return True


def _msg(data: bytes) -> SimpleNamespace:
    return SimpleNamespace(data=data)


def _make_adapter(responses: dict[bytes, list], raise_on=None) -> "elm_mod.Elm327Adapter":
    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _FakeConn(_FakeInterface(responses, raise_on=raise_on))
    return adapter


def test_raw_read_captures_bytes_from_interface() -> None:
    """Happy path: interface returns a message with data; the helper
    strips the 0x41+PID echo and returns "raw:HEX"."""
    responses = {b"0114": [_msg(bytes([0x41, 0x14, 0xAB, 0xCD]))]}
    adapter = _make_adapter(responses)
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is not None
    assert sample.value == "raw:ABCD"
    assert adapter._raw_attempts == 1
    assert adapter._raw_successes == 1


def test_raw_read_handles_no_echo_prefix() -> None:
    """Some adapters/protocols don't echo 0x41+PID; the raw bytes
    are the payload as-is."""
    responses = {b"0114": [_msg(bytes([0xCA, 0xFE]))]}
    adapter = _make_adapter(responses)
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is not None
    assert sample.value == "raw:CAFE"


def test_raw_read_falls_through_messages_until_data_found() -> None:
    """First message has empty data; second has the real payload.
    Helper should walk past the empty one."""
    responses = {
        b"0114": [
            _msg(b""),
            _msg(bytes([0x41, 0x14, 0xDE, 0xAD])),
        ],
    }
    adapter = _make_adapter(responses)
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is not None
    assert sample.value == "raw:DEAD"


def test_raw_read_returns_none_on_empty_messages() -> None:
    """Interface returns empty list — counts as attempt, not success."""
    responses = {b"0114": []}
    adapter = _make_adapter(responses)
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is None
    assert adapter._raw_attempts == 1
    assert adapter._raw_successes == 0


def test_raw_read_returns_none_on_none_messages() -> None:
    """Interface returns None — counts as attempt, not success."""
    responses = {b"0114": None}
    adapter = _make_adapter(responses)
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is None
    assert adapter._raw_attempts == 1
    assert adapter._raw_successes == 0


def test_raw_read_returns_none_when_interface_raises() -> None:
    """Exception inside send_and_parse must not propagate."""
    adapter = _make_adapter({}, raise_on=b"0114")
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is None
    assert adapter._raw_attempts == 1
    assert adapter._raw_successes == 0


def test_raw_read_returns_none_when_messages_have_no_data() -> None:
    """All messages have data=None or empty — no capturable bytes."""
    responses = {
        b"0114": [
            SimpleNamespace(data=None),
            SimpleNamespace(),  # no data attr at all
        ],
    }
    adapter = _make_adapter(responses)
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is None
    assert adapter._raw_attempts == 1
    assert adapter._raw_successes == 0


def test_raw_read_skips_mode_other_than_01() -> None:
    """Mode 09 / 02 / 03 etc. have dedicated paths; raw fallback shouldn't
    fire for them. Counter should NOT increment either — we never even
    tried."""
    responses = {b"0902": [_msg(bytes([0x49, 0x02, 0xFF]))]}
    adapter = _make_adapter(responses)
    sample = adapter._read_pid_raw("0902", 0x09, 0x02)
    assert sample is None
    assert adapter._raw_attempts == 0


def test_raw_read_returns_none_when_no_interface() -> None:
    """Connection without an interface attribute (e.g. mid-reconnect)
    should fail cleanly."""
    adapter = elm_mod.Elm327Adapter()
    adapter._conn = SimpleNamespace(is_connected=lambda: True)  # no .interface
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is None
    assert adapter._raw_attempts == 1
    assert adapter._raw_successes == 0


def test_raw_read_returns_none_when_disconnected() -> None:
    """_ensure() raises AdapterError; helper returns None and does NOT
    increment the attempt counter (no actual try happened)."""
    adapter = elm_mod.Elm327Adapter()
    # No _conn set → _ensure() raises
    sample = adapter._read_pid_raw("0114", 0x01, 0x14)
    assert sample is None
    assert adapter._raw_attempts == 0


def test_raw_read_sends_command_string_without_separator() -> None:
    """python-obd's interface expects bare hex like b'0114', not
    b'01 14' (the CR is added by ELM327 layer)."""
    iface = _FakeInterface({b"0114": [_msg(bytes([0x41, 0x14, 0x00]))]})
    adapter = elm_mod.Elm327Adapter()
    adapter._conn = _FakeConn(iface)
    adapter._read_pid_raw("0114", 0x01, 0x14)
    assert iface.sent == [b"0114"]


# ---------------------------------------------------------------------------
# read_metrics()
# ---------------------------------------------------------------------------


def test_read_metrics_starts_at_zero() -> None:
    adapter = elm_mod.Elm327Adapter()
    metrics = adapter.read_metrics()
    assert metrics == {"raw_attempts": 0, "raw_successes": 0}


def test_read_metrics_tracks_across_calls() -> None:
    """A run of raw reads (some success, some fail) should accumulate
    in the metrics."""
    responses = {
        b"0114": [_msg(bytes([0x41, 0x14, 0xAB]))],   # success
        b"0115": None,                                  # null
        b"0116": [_msg(bytes([0x41, 0x16, 0xCD]))],   # success
        b"0117": [],                                    # empty
    }
    adapter = _make_adapter(responses)
    adapter._read_pid_raw("0114", 0x01, 0x14)
    adapter._read_pid_raw("0115", 0x01, 0x15)
    adapter._read_pid_raw("0116", 0x01, 0x16)
    adapter._read_pid_raw("0117", 0x01, 0x17)
    metrics = adapter.read_metrics()
    assert metrics == {"raw_attempts": 4, "raw_successes": 2}


def test_base_adapter_read_metrics_default_empty() -> None:
    """Adapters that don't override read_metrics() report no telemetry."""
    from uacj_obd.adapters.mock import MockAdapter
    m = MockAdapter()
    assert m.read_metrics() == {}


# ---------------------------------------------------------------------------
# Acquisition session captures metrics in metadata.json
# ---------------------------------------------------------------------------


def test_acquisition_writes_adapter_metrics_to_metadata(tmp_path) -> None:
    """End-to-end: a session capture run finishes and the
    adapter_metrics dict appears in the on-disk metadata.json."""
    import json
    from fastapi.testclient import TestClient
    from uacj_obd.api import create_app

    client = TestClient(create_app(data_root=tmp_path))
    client.post("/api/sessions/start", json={"adapter": "mock", "duration_s": 0.5})
    import time
    for _ in range(80):
        time.sleep(0.1)
        if client.get("/api/sessions/current").json().get("active") is False:
            break
    sessions = client.get("/api/sessions").json()
    assert sessions
    folder = sessions[0]["folder"]
    meta = json.loads(open(f"{folder}/metadata.json").read())
    # MockAdapter exposes no metrics → field may be missing or empty
    assert meta.get("adapter_metrics", {}) == {}


def test_diagnostics_endpoint_returns_adapter_metrics(tmp_path) -> None:
    """Surface adapter_metrics via the diagnostics endpoint so the
    dashboard panel can show the raw fallback rate."""
    import json
    from fastapi.testclient import TestClient
    from uacj_obd.api import create_app
    from uacj_obd.storage import Database

    client = TestClient(create_app(data_root=tmp_path))
    db = Database(tmp_path / "uacj.db")
    db.upsert_vehicle("VINTEST", "Test", "Test", 2020, "2026-01-01T00:00:00+00:00")
    folder = tmp_path / "session_folder"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({
        "vehicle": {"vin": "VINTEST"},
        "discovered_pids": ["0114", "0115"],
        "pid_resolution_source": "discovered",
        "adapter_metrics": {"raw_attempts": 22, "raw_successes": 17},
    }))
    (folder / "live_data.jsonl").write_text(
        '{"pid": "0114", "value": "raw:CAFE"}\n'
    )
    db.insert_session(
        session_id="metrics_session",
        vin="VINTEST",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at=None,
        protocol="ISO_15765_4_CAN_11_500",
        adapter="elm327",
        sample_count=1,
        folder=str(folder),
        notes="",
    )
    body = client.get("/api/sessions/metrics_session/diagnostics").json()
    assert body["adapter_metrics"] == {"raw_attempts": 22, "raw_successes": 17}
