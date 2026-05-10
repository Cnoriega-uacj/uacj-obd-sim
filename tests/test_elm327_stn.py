"""
Tests for the STN2120 (OBDLink SX) tuning path in Elm327Adapter.

We don't have a real adapter on hand, so the tests inject a fake
python-obd `OBD` object that records the AT/ST commands the adapter
sends. This proves:
  - the STN command sequence is sent only when the banner says STN/OBDLink
  - a plain ELM327 clone gets python-obd's default behavior (no extras)
  - explicit stn_mode=True / False overrides the banner detection
"""

from __future__ import annotations

import pytest

from uacj_obd.adapters import elm327 as elm_mod


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self._text = text

    def raw(self) -> str:
        return self._text


class _FakeInterface:
    def __init__(self, banner: str) -> None:
        self.banner = banner
        self.commands: list[bytes] = []

    def send_and_parse(self, cmd: bytes) -> list[_FakeMessage]:
        self.commands.append(cmd)
        upper = cmd.upper()
        if upper in (b"STI", b"ATI"):
            return [_FakeMessage(self.banner)]
        return [_FakeMessage("OK")]


class _FakeObd:
    def __init__(self, banner: str = "ELM327 v1.5", **_kwargs) -> None:
        self.interface = _FakeInterface(banner)
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    def status(self) -> str:
        return "OBDStatus.CAR_CONNECTED"

    def protocol_id(self) -> str:
        return "6"

    def port_name(self) -> str:
        return "/dev/ttyUSB-fake"

    def close(self) -> None:
        self._connected = False


@pytest.fixture
def fake_obd_module(monkeypatch):
    """Replace pyobd with a fake OBD class for the duration of a test.
    The fake's banner is set per-test by passing `banner=...` to .OBD().
    """
    captured = {"banner": "ELM327 v1.5"}

    def factory(**kwargs):
        return _FakeObd(banner=captured["banner"])

    fake_module = type(elm_mod.pyobd)("obd")
    fake_module.OBD = factory  # type: ignore[attr-defined]
    fake_module.commands = type("Cmds", (), {})()
    monkeypatch.setattr(elm_mod, "pyobd", fake_module)
    monkeypatch.setattr(elm_mod, "_HAS_OBD", True)
    return captured


def _connect_with_banner(fake_obd_module, banner: str, **kwargs):
    fake_obd_module["banner"] = banner
    adapter = elm_mod.Elm327Adapter(**kwargs)
    adapter.connect()
    return adapter


def test_stn_banner_triggers_st_init_sequence(fake_obd_module):
    adapter = _connect_with_banner(fake_obd_module, "STN1170 v4.2.1")
    sent = [c.decode() for c in adapter._conn.interface.commands]
    assert adapter.is_stn is True
    # The probe ran first...
    assert sent[0].upper() in ("STI", "ATI")
    # ...followed by the STN-only tuning commands.
    for required in ("STCSEGR 1", "STCFCPA"):
        assert any(required in s for s in sent), f"missing {required!r} in {sent}"


def test_obdlink_sx_banner_is_recognized(fake_obd_module):
    adapter = _connect_with_banner(fake_obd_module, "OBDLink SX r4.2")
    assert adapter.is_stn is True
    assert "OBDLINK" in (adapter.stn_banner or "").upper()


def test_plain_elm327_clone_skips_stn_init(fake_obd_module):
    adapter = _connect_with_banner(fake_obd_module, "ELM327 v1.5")
    sent = [c.decode() for c in adapter._conn.interface.commands]
    assert adapter.is_stn is False
    # No STN-only commands sent
    for forbidden in ("STCSEGR", "STCFCPA"):
        assert not any(forbidden in s for s in sent), f"unexpected {forbidden!r} sent to clone"


def test_explicit_stn_mode_true_forces_st_init_even_on_clone(fake_obd_module):
    adapter = _connect_with_banner(fake_obd_module, "ELM327 v1.5", stn_mode=True)
    sent = [c.decode() for c in adapter._conn.interface.commands]
    assert adapter.is_stn is True
    assert any("STCSEGR" in s for s in sent)


def test_explicit_stn_mode_false_skips_st_init_even_on_real_chip(fake_obd_module):
    adapter = _connect_with_banner(fake_obd_module, "STN2120 v4.x", stn_mode=False)
    sent = [c.decode() for c in adapter._conn.interface.commands]
    assert adapter.is_stn is False
    for forbidden in ("STCSEGR", "STCFCPA"):
        assert not any(forbidden in s for s in sent)
