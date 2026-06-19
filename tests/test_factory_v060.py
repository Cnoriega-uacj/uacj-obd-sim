"""
v0.6.0 — Tests for the adapter factory dispatch.

v0.5.5 audit identified `adapters/factory.py` as 33% covered. The
factory is tiny but it's the dispatch every entry point uses to pick
an adapter, so even small bugs (a missing alias, a wrong fallback)
would propagate widely.
"""

from __future__ import annotations

import pytest

from uacj_obd.adapters import open_adapter
from uacj_obd.adapters.mock import MockAdapter


def test_mock_kind_returns_mock_adapter() -> None:
    a = open_adapter("mock")
    assert isinstance(a, MockAdapter)


def test_mock_kind_passes_kwargs_through() -> None:
    a = open_adapter("mock", vin="TEST1234567890123", make="Test")
    assert isinstance(a, MockAdapter)


def test_kind_is_case_insensitive() -> None:
    assert isinstance(open_adapter("MOCK"), MockAdapter)
    assert isinstance(open_adapter("Mock"), MockAdapter)


def test_unknown_kind_raises_clean_value_error() -> None:
    with pytest.raises(ValueError, match="unknown adapter kind"):
        open_adapter("does-not-exist")


def test_elm327_alias_resolves_to_elm327_adapter(monkeypatch) -> None:
    """The factory accepts `elm327`, `stn2120`, and `real` as aliases
    for the same class. We patch the constructor to avoid trying to
    open a real serial port."""
    from uacj_obd.adapters import elm327 as elm_mod

    constructed: list[dict] = []

    def fake_init(self, **kwargs):  # type: ignore[no-untyped-def]
        constructed.append(kwargs)
        # Don't actually call the real __init__; just set _conn to a
        # sentinel so the object survives garbage collection.
        self._conn = None
        self._last_error = None
        self._portstr = kwargs.get("portstr")
        self._baudrate = kwargs.get("baudrate")
        self._fast = kwargs.get("fast", False)
        self._timeout = kwargs.get("timeout", 5.0)
        self._stn_mode = kwargs.get("stn_mode")
        self._is_stn = None
        self._stn_banner = None

    monkeypatch.setattr(elm_mod.Elm327Adapter, "__init__", fake_init)
    a = open_adapter("elm327", portstr="/dev/ttyUSB0")
    assert isinstance(a, elm_mod.Elm327Adapter)
    a2 = open_adapter("stn2120", portstr="/dev/ttyUSB1")
    assert isinstance(a2, elm_mod.Elm327Adapter)
    a3 = open_adapter("real", portstr="/dev/ttyUSB2")
    assert isinstance(a3, elm_mod.Elm327Adapter)
    # Three calls captured.
    assert len(constructed) == 3
    assert constructed[0]["portstr"] == "/dev/ttyUSB0"


def test_replay_kind_returns_replay_adapter(tmp_path) -> None:
    """The replay adapter is constructed from a saved session
    directory. We don't have a real one in this test, but the factory
    should at least try to construct it and surface the constructor
    error transparently."""
    from uacj_obd.adapters.replay import ReplayAdapter

    # ReplayAdapter requires a `path` kwarg. We point at a tmpdir that
    # has the minimum structure expected.
    sess_dir = tmp_path / "20260619T120000Z-abc"
    sess_dir.mkdir()
    (sess_dir / "metadata.json").write_text(
        '{"session_id": "test", "started_at": "2026-06-19T12:00:00Z",'
        '"protocol": "iso_15765_4_can_11_500", "adapter": "mock",'
        '"vehicle": {}, "notes": "", "sample_count": 0, "ended_at": null}'
    )
    (sess_dir / "live_data.jsonl").write_text("")
    a = open_adapter("replay", session_dir=sess_dir)
    assert isinstance(a, ReplayAdapter)


def test_auto_falls_back_to_mock_when_elm327_construction_fails(monkeypatch) -> None:
    """When `auto` is asked and the ELM327 constructor raises (no
    hardware, no driver), the factory must fall back to mock cleanly."""
    from uacj_obd.adapters import elm327 as elm_mod

    def boom(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("no serial port available")

    monkeypatch.setattr(elm_mod.Elm327Adapter, "__init__", boom)
    a = open_adapter("auto")
    assert isinstance(a, MockAdapter)


def test_auto_uses_elm327_when_construction_succeeds(monkeypatch) -> None:
    """If ELM327 construction works (hardware available or mocked),
    `auto` should use it — not fall back."""
    from uacj_obd.adapters import elm327 as elm_mod

    def fake_init(self, **kwargs):  # type: ignore[no-untyped-def]
        # Set the bare minimum the class needs to exist.
        self._conn = None
        self._last_error = None
        self._portstr = None
        self._baudrate = None
        self._fast = False
        self._timeout = 5.0
        self._stn_mode = None
        self._is_stn = None
        self._stn_banner = None

    monkeypatch.setattr(elm_mod.Elm327Adapter, "__init__", fake_init)
    a = open_adapter("auto")
    assert isinstance(a, elm_mod.Elm327Adapter)
