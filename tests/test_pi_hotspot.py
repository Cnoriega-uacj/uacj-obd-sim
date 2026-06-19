"""
v0.5.1 — Tests for the Pi-as-WiFi-Access-Point config generators.

The actual `scripts/setup_pi_hotspot.sh` is a bash script that runs on
the Pi as root and starts hostapd/dnsmasq services — not testable in
CI. But the contents it writes ARE testable: every config string can
be generated from the pure Python helpers in `uacj_obd.pi_hotspot`,
and tests verify the resulting files satisfy the wifi/DHCP invariants
the operator cares about (WPA2 length, channel range, IP shape,
revert idempotency).
"""

from __future__ import annotations

import pytest

from uacj_obd.pi_hotspot import (
    HotspotSettings,
    WPA2_MAX_PASSPHRASE_LEN,
    WPA2_MIN_PASSPHRASE_LEN,
    dashboard_url_for_clients,
    render_dhcpcd_block,
    render_dnsmasq_conf,
    render_hostapd_conf,
)


# ---------------------------------------------------------------------------
# HotspotSettings validation
# ---------------------------------------------------------------------------

def test_defaults_match_script_defaults() -> None:
    s = HotspotSettings()
    assert s.ssid == "UACJ-SIM"
    assert s.passphrase == "uacj1234"
    assert s.country == "MX"
    assert s.channel == 6
    assert s.pi_ip == "192.168.50.1"
    assert s.wlan_iface == "wlan0"


def test_short_passphrase_rejected() -> None:
    with pytest.raises(ValueError, match="passphrase"):
        HotspotSettings(passphrase="short")


def test_long_passphrase_rejected() -> None:
    with pytest.raises(ValueError, match="passphrase"):
        HotspotSettings(passphrase="x" * (WPA2_MAX_PASSPHRASE_LEN + 1))


def test_boundary_passphrase_lengths_accepted() -> None:
    HotspotSettings(passphrase="x" * WPA2_MIN_PASSPHRASE_LEN)
    HotspotSettings(passphrase="x" * WPA2_MAX_PASSPHRASE_LEN)


def test_invalid_channel_rejected() -> None:
    with pytest.raises(ValueError, match="channel"):
        HotspotSettings(channel=0)
    with pytest.raises(ValueError, match="channel"):
        HotspotSettings(channel=15)


def test_invalid_country_code_rejected() -> None:
    with pytest.raises(ValueError, match="country"):
        HotspotSettings(country="MEX")  # 3 letters
    with pytest.raises(ValueError, match="country"):
        HotspotSettings(country="1A")   # not alpha


def test_empty_ssid_rejected() -> None:
    with pytest.raises(ValueError, match="SSID"):
        HotspotSettings(ssid="")


def test_oversize_ssid_rejected() -> None:
    with pytest.raises(ValueError, match="SSID"):
        HotspotSettings(ssid="x" * 33)


# ---------------------------------------------------------------------------
# hostapd.conf
# ---------------------------------------------------------------------------

def test_hostapd_conf_includes_required_directives() -> None:
    conf = render_hostapd_conf(HotspotSettings())
    for required in (
        "interface=wlan0",
        "driver=nl80211",
        "ssid=UACJ-SIM",
        "channel=6",
        "country_code=MX",
        "wpa=2",          # WPA2, never WPA1
        "wpa_passphrase=uacj1234",
        "auth_algs=1",    # open-system auth only
    ):
        assert required in conf, f"missing {required!r}"


def test_hostapd_conf_does_not_contain_wep_or_wpa1() -> None:
    """WPA1 is broken; WEP is broken; neither must show up."""
    conf = render_hostapd_conf(HotspotSettings())
    assert "wpa=1" not in conf
    assert "wep_" not in conf
    assert "auth_algs=2" not in conf  # shared-key WEP-style auth


def test_hostapd_conf_reflects_custom_settings() -> None:
    conf = render_hostapd_conf(HotspotSettings(
        ssid="Custom",
        passphrase="custompw123",
        channel=11,
        country="US",
    ))
    assert "ssid=Custom" in conf
    assert "channel=11" in conf
    assert "country_code=US" in conf
    assert "wpa_passphrase=custompw123" in conf


# ---------------------------------------------------------------------------
# dnsmasq.conf
# ---------------------------------------------------------------------------

def test_dnsmasq_dhcp_range_matches_settings() -> None:
    conf = render_dnsmasq_conf(HotspotSettings())
    assert "dhcp-range=192.168.50.10,192.168.50.50,255.255.255.0,12h" in conf


def test_dnsmasq_binds_to_wlan_interface() -> None:
    conf = render_dnsmasq_conf(HotspotSettings())
    assert "interface=wlan0" in conf
    assert "bind-interfaces" in conf


def test_dnsmasq_advertises_pi_as_gateway_and_dns() -> None:
    """Without this, clients without internet take 30+ seconds to
    realize DNS isn't reachable and spam retries."""
    conf = render_dnsmasq_conf(HotspotSettings())
    assert "dhcp-option=3,192.168.50.1" in conf  # gateway
    assert "dhcp-option=6,192.168.50.1" in conf  # DNS


# ---------------------------------------------------------------------------
# dhcpcd block
# ---------------------------------------------------------------------------

def test_dhcpcd_block_has_revert_markers() -> None:
    """The block must be wrapped between markers so the revert script
    can strip it without rewriting the rest of dhcpcd.conf."""
    block = render_dhcpcd_block(HotspotSettings())
    assert "# >>> UACJ-AP block" in block
    assert "# <<< UACJ-AP block" in block


def test_dhcpcd_block_disables_wpa_supplicant_hook() -> None:
    """If wpa_supplicant runs while we're in AP mode it fights hostapd
    for the radio — `nohook wpa_supplicant` is mandatory."""
    block = render_dhcpcd_block(HotspotSettings())
    assert "nohook wpa_supplicant" in block


def test_dhcpcd_block_sets_static_ip_with_netmask() -> None:
    block = render_dhcpcd_block(HotspotSettings())
    assert "static ip_address=192.168.50.1/24" in block


# ---------------------------------------------------------------------------
# Client-facing helpers
# ---------------------------------------------------------------------------

def test_dashboard_url_for_default_port() -> None:
    url = dashboard_url_for_clients(HotspotSettings())
    assert url == "http://192.168.50.1:8765"


def test_dashboard_url_for_custom_port() -> None:
    url = dashboard_url_for_clients(HotspotSettings(), port=80)
    assert url == "http://192.168.50.1:80"


def test_dashboard_url_uses_pi_ip() -> None:
    url = dashboard_url_for_clients(HotspotSettings(pi_ip="10.42.0.1"))
    assert url == "http://10.42.0.1:8765"
