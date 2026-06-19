"""
v0.5.1 — Pi-as-WiFi-Access-Point config generators.

The actual setup is done by `scripts/setup_pi_hotspot.sh`, which the
operator runs as root on the Pi. This module exposes the configuration
generation as pure functions so the contents of hostapd / dnsmasq /
dhcpcd can be tested without touching the filesystem or starting any
services.

Useful for:
- Unit tests that lock in the WPA2 settings, IP layout, and DHCP pool.
- A future dashboard endpoint that lets an instructor edit AP settings
  and re-render configs without SSH-ing to the Pi.
- Sanity-checking the bash script against an authoritative spec.
"""

from __future__ import annotations

from dataclasses import dataclass


# Common pitfalls the bash script already enforces (e.g. WPA2 length),
# replicated here so the Python entry point catches them at validation
# time before any file is written.

WPA2_MIN_PASSPHRASE_LEN = 8
WPA2_MAX_PASSPHRASE_LEN = 63


@dataclass(frozen=True)
class HotspotSettings:
    """All knobs the hotspot config exposes. Values match the defaults
    in `scripts/setup_pi_hotspot.sh` exactly — change one, change both."""

    ssid: str = "UACJ-SIM"
    passphrase: str = "uacj1234"
    country: str = "MX"
    channel: int = 6
    pi_ip: str = "192.168.50.1"
    dhcp_start: str = "192.168.50.10"
    dhcp_end: str = "192.168.50.50"
    wlan_iface: str = "wlan0"

    def __post_init__(self) -> None:
        # dataclass-frozen still lets __post_init__ run; raise early so
        # callers get a clean error before trying to write configs.
        if not (1 <= len(self.ssid) <= 32):
            raise ValueError(
                f"SSID length must be 1-32 chars (got {len(self.ssid)})"
            )
        if not (WPA2_MIN_PASSPHRASE_LEN <= len(self.passphrase) <= WPA2_MAX_PASSPHRASE_LEN):
            raise ValueError(
                f"WPA2 passphrase length must be {WPA2_MIN_PASSPHRASE_LEN}-"
                f"{WPA2_MAX_PASSPHRASE_LEN} chars (got {len(self.passphrase)})"
            )
        if not (1 <= self.channel <= 14):
            raise ValueError(
                f"2.4 GHz WiFi channel must be 1-14 (got {self.channel})"
            )
        if len(self.country) != 2 or not self.country.isalpha():
            raise ValueError(
                f"country must be a 2-letter ISO 3166-1 alpha-2 code (got {self.country!r})"
            )


def render_hostapd_conf(s: HotspotSettings) -> str:
    """Generate the contents of /etc/hostapd/hostapd.conf for the given
    settings. Mirrors the bash script's heredoc — keep in sync."""
    return (
        "# UACJ OBD-II Simulator — WiFi access point\n"
        f"interface={s.wlan_iface}\n"
        "driver=nl80211\n"
        f"ssid={s.ssid}\n"
        "hw_mode=g\n"
        f"channel={s.channel}\n"
        f"country_code={s.country}\n"
        "ieee80211d=1\n"
        "ieee80211n=1\n"
        "wmm_enabled=1\n"
        "auth_algs=1\n"
        "wpa=2\n"
        "wpa_key_mgmt=WPA-PSK\n"
        "wpa_pairwise=TKIP\n"
        "rsn_pairwise=CCMP\n"
        f"wpa_passphrase={s.passphrase}\n"
    )


def render_dnsmasq_conf(s: HotspotSettings) -> str:
    """Generate the contents of /etc/dnsmasq.conf for the AP's DHCP pool."""
    return (
        "# UACJ OBD-II Simulator — DHCP for laptop / scan-tool clients\n"
        f"interface={s.wlan_iface}\n"
        "bind-interfaces\n"
        "domain-needed\n"
        "bogus-priv\n"
        f"dhcp-range={s.dhcp_start},{s.dhcp_end},255.255.255.0,12h\n"
        "# Pi itself is the gateway / DNS — even with no internet, this keeps\n"
        "# clients from spamming retries against unreachable upstream DNS.\n"
        f"dhcp-option=3,{s.pi_ip}\n"
        f"dhcp-option=6,{s.pi_ip}\n"
    )


def render_dhcpcd_block(s: HotspotSettings) -> str:
    """Generate the static-IP block that gets appended to
    /etc/dhcpcd.conf. The script wraps this between markers so it can
    be removed on revert without touching the rest of the file."""
    return (
        "# >>> UACJ-AP block\n"
        f"interface {s.wlan_iface}\n"
        f"    static ip_address={s.pi_ip}/24\n"
        "    nohook wpa_supplicant\n"
        "# <<< UACJ-AP block\n"
    )


def dashboard_url_for_clients(s: HotspotSettings, port: int = 8765) -> str:
    """Convenience: the URL a laptop joined to the AP uses to reach the
    simulator HTTP endpoint."""
    return f"http://{s.pi_ip}:{port}"
