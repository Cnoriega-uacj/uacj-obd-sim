from __future__ import annotations

import logging
import time
from typing import Iterable

from uacj_obd.models import (
    DTC,
    DTCStatus,
    FreezeFrame,
    LiveSample,
    Monitor,
    Protocol,
    VehicleInfo,
)

from .base import Adapter, AdapterError, AdapterStatus, ConnectionState

log = logging.getLogger(__name__)


def _extract_all_bitmap_candidates(resp) -> list[bytes]:
    """
    Collect every 4-byte supported-PID bitmap candidate from a raw
    python-obd response. Multiple candidates arise when more than one
    ECU answers Mode 01 PID 0x00 — most commonly the engine and
    transmission both responding under CAN. To avoid dropping PIDs
    that only one ECU supports, the caller ORs all candidates
    together.

    Handles response shapes python-obd has produced across versions:
      - `resp.value` is bytes / bytearray (some noop-decoder builds)
      - `resp.value` is a list of `Message` objects with `.data`
      - `resp.value` is a list of strings (older python-obd in
        debug-passthrough mode)
      - `resp.messages` is the same list as value, populated even
        when the decoder returned None
      - Echo prefix (0x41 + PID byte) may or may not be stripped

    Returns the candidates verbatim — caller decides how to combine.
    """
    candidates: list[bytes] = []

    def _consume(item) -> None:
        if isinstance(item, (bytes, bytearray)):
            candidates.append(bytes(item))
            return
        # python-obd Message objects expose `.data` (bytes), but some
        # debug builds expose `.raw()` returning a hex string.
        data = getattr(item, "data", None)
        if isinstance(data, (bytes, bytearray)):
            candidates.append(bytes(data))
            return
        if hasattr(item, "raw"):
            try:
                raw_str = item.raw() if callable(item.raw) else item.raw
            except Exception:
                raw_str = None
            if isinstance(raw_str, str):
                hexed = "".join(raw_str.split())
                try:
                    candidates.append(bytes.fromhex(hexed))
                except ValueError:
                    pass
            return
        if isinstance(item, str):
            hexed = "".join(item.split())
            try:
                candidates.append(bytes.fromhex(hexed))
            except ValueError:
                pass

    val = getattr(resp, "value", None)
    if isinstance(val, list):
        for m in val:
            _consume(m)
    else:
        _consume(val)
    for msg in getattr(resp, "messages", []) or []:
        _consume(msg)

    cleaned: list[bytes] = []
    for raw in candidates:
        if len(raw) >= 6 and raw[0] == 0x41:
            cleaned.append(raw[2:6])
        elif len(raw) >= 4:
            cleaned.append(raw[:4])
    return cleaned


def _extract_bitmap_bytes(resp) -> bytes:
    """
    Back-compat single-bitmap accessor. ORs all candidates so a
    multi-ECU response doesn't lose PIDs that only one ECU supports.
    Returns empty bytes if no candidates were found.
    """
    candidates = _extract_all_bitmap_candidates(resp)
    if not candidates:
        return b""
    merged = bytearray(4)
    for raw in candidates:
        for i in range(4):
            merged[i] |= raw[i]
    return bytes(merged)


def _decode_string_response(value) -> str:
    """Decode a python-obd response value to a clean ASCII string.

    python-obd returns VIN / calibration ID / ECU name as either `str`,
    `bytes`, or `bytearray` depending on the python-obd version and the
    chip. `str(bytearray(b'JM1...'))` formats as `"bytearray(b'JM1...')"`
    — the Python repr leaks through to the dashboard and to the on-disk
    session folder name. This helper normalises every shape to a clean
    string with nulls and whitespace stripped.
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        try:
            text = bytes(value).decode("ascii", errors="replace")
        except Exception:
            return bytes(value).hex().upper()
        return text.replace("\x00", "").strip()
    if isinstance(value, list):
        # python-obd sometimes splits VIN across multiple message segments
        parts = [_decode_string_response(v) for v in value]
        return "".join(p for p in parts if p)
    return str(value).strip()

# python-obd is loaded lazily so the rest of the system runs without it
# (e.g. on the Pi simulator side, or in CI).
try:
    import obd as pyobd  # type: ignore[import-not-found]
    _HAS_OBD = True
except Exception:  # pragma: no cover
    pyobd = None  # type: ignore[assignment]
    _HAS_OBD = False


_PROTOCOL_MAP = {
    "1": Protocol.SAE_J1850_PWM,
    "2": Protocol.SAE_J1850_VPW,
    "3": Protocol.ISO_9141_2,
    "4": Protocol.ISO_14230_4_KWP_5BAUD,
    "5": Protocol.ISO_14230_4_KWP_FAST,
    "6": Protocol.ISO_15765_4_CAN_11_500,
    "7": Protocol.ISO_15765_4_CAN_29_500,
    "8": Protocol.ISO_15765_4_CAN_11_250,
    "9": Protocol.ISO_15765_4_CAN_29_250,
}


class Elm327Adapter(Adapter):
    """
    Real ELM327 / STN1110 / STN2120 adapter via python-obd.

    Auto-detects protocol on the vehicle. Reconnects on transient failures.

    STN2120 (OBDLink SX) extensions: when `stn_mode=True` (or auto-detected
    via the ATI / STI banner), the adapter sends a small set of ST*
    commands after connect to enable the chip's faster init, larger RX
    buffer, and improved timing. These are silently ignored by a plain
    ELM327 clone, so the same code path works for both — but on a real
    STN2120 we get the speedup. Reference: OBDLink ST command reference,
    rev 4.20.
    """

    # STN2120 init sequence (sent post-connect, ignored by plain ELM327):
    #   STSBR 38400 — set serial baud (effective on next reset; we set
    #     the python-obd baudrate explicitly to match)
    #   STPRS — print current protocol search order
    #   STN — print firmware version (used to confirm STN chip vs clone)
    # We do NOT change non-volatile settings. Each session is stateless.
    _STN_PROBE_COMMANDS = ("STI", "STDI")  # banner / device-info
    # NOTE: post-connect runtime commands intentionally empty after on-site
    # testing on a 2012 Mazda3 with an OBDLink SX. The previous set re-sent
    # ATSP0 (re-triggers protocol auto-detection) and STCSEGR 1 (changes
    # how the chip frames multi-frame responses) AFTER python-obd had
    # already negotiated the protocol cleanly. Both rewrote the chip's
    # working state, leaving subsequent PID queries with no response.
    # python-obd's defaults handle the OBDLink SX correctly; STN-specific
    # tuning should be opt-in via constructor args, not on by default.
    _STN_RUNTIME_COMMANDS: tuple[str, ...] = ()

    def __init__(
        self,
        portstr: str | None = None,
        baudrate: int | None = None,
        fast: bool = False,
        # Real OBD-II queries against a car commonly take 200-1000 ms each,
        # and the initial protocol-detection query (0100) on a cold connect
        # can legitimately take 2-4 seconds on some OBDLink SX + vehicle
        # combinations as the chip walks through ISO 15765-4 variants. The
        # client's successful direct test against a 2012 Mazda3 used
        # timeout=5 and connected cleanly; timeout=2.0 from v0.4.6 left
        # the connection failing during protocol detection silently (python-obd
        # returns is_connected()=False when the connect 0100 times out, no
        # exception raised). 5.0 s matches the parameters known to work
        # against the client's hardware and is still well below any UI
        # latency the user would notice.
        timeout: float = 5.0,
        stn_mode: bool | None = None,
    ) -> None:
        if not _HAS_OBD:
            raise AdapterError(
                "python-obd is not installed; install with `pip install obd` "
                "or use the mock adapter for offline development"
            )
        self._portstr = portstr
        self._baudrate = baudrate
        self._fast = fast
        self._timeout = timeout
        # None = auto-detect via STI banner; True = force STN init; False = plain ELM
        self._stn_mode = stn_mode
        self._is_stn: bool | None = None  # set by connect()
        self._stn_banner: str | None = None
        self._conn: "pyobd.OBD | None" = None  # type: ignore[name-defined]
        self._last_error: str | None = None
        # v0.6.16: capture-side counters surfaced through read_metrics().
        # Track how many times the raw fallback fires vs how many of those
        # actually return bytes — Cristopher's session diagnostics needs
        # this to distinguish "adapter never tried raw" from "adapter
        # tried but bus was silent".
        self._raw_attempts: int = 0
        self._raw_successes: int = 0

    # --- lifecycle -----------------------------------------------------

    def connect(self) -> AdapterStatus:
        try:
            self._conn = pyobd.OBD(
                portstr=self._portstr,
                baudrate=self._baudrate,
                fast=self._fast,
                timeout=self._timeout,
            )
            if not self._conn.is_connected():
                raise AdapterError(f"adapter did not connect: {self._conn.status()}")
        except Exception as exc:
            self._last_error = str(exc)
            raise AdapterError(f"connect failed: {exc}") from exc
        # Probe for STN-class chip and apply tuned init if so. Failures
        # here are non-fatal — we want the adapter usable as a plain
        # ELM327 if probing or tuning misbehaves.
        try:
            self._apply_stn_init_if_present()
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("STN init skipped: %s", exc)
        return self.status()

    def _apply_stn_init_if_present(self) -> None:
        """Detect an STN-class chip (OBDLink SX/MX, STN1110/STN2120) and
        send its tuned post-connect command set if found.

        On a plain ELM327 clone the probe returns no STN signature and we
        leave python-obd's defaults in place. Single round-trip cost on
        the happy path: one ATI/STI exchange, ~50 ms.
        """
        is_stn = self._stn_mode
        if is_stn is None:
            banner = self._send_raw("STI") or self._send_raw("ATI")
            self._stn_banner = banner
            # STN1110/STN2120 banners include "STN" or "OBDLink"; ELM327
            # clones return "ELM327 v..." without those markers.
            is_stn = bool(banner) and ("STN" in banner.upper() or "OBDLINK" in banner.upper())
        self._is_stn = bool(is_stn)
        if not self._is_stn:
            return
        for cmd in self._STN_RUNTIME_COMMANDS:
            self._send_raw(cmd)

    def _send_raw(self, cmd: str) -> str | None:
        """Send an AT/ST command to the adapter and return the trimmed reply.
        Returns None if the underlying interface does not expose a raw
        send-and-parse hook (older python-obd builds).
        """
        if self._conn is None:
            return None
        interface = getattr(self._conn, "interface", None)
        sender = getattr(interface, "send_and_parse", None) if interface else None
        if sender is None:
            return None
        try:
            messages = sender(cmd.encode())
        except Exception:
            return None
        if not messages:
            return None
        # python-obd Message.raw() returns the AT/ST text reply
        raw = getattr(messages[0], "raw", lambda: None)()
        if raw is None:
            return None
        return raw.strip()

    @property
    def is_stn(self) -> bool | None:
        """True if the adapter has been identified as STN1110/STN2120/OBDLink."""
        return self._is_stn

    @property
    def stn_banner(self) -> str | None:
        """The raw STI/ATI banner returned at connect, for diagnostics."""
        return self._stn_banner

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def status(self) -> AdapterStatus:
        if self._conn is None or not self._conn.is_connected():
            return AdapterStatus(
                state=ConnectionState.DISCONNECTED,
                protocol=Protocol.UNKNOWN,
                adapter_name="ELM327",
                last_error=self._last_error,
            )
        proto_id = getattr(self._conn, "protocol_id", lambda: None)()
        protocol = _PROTOCOL_MAP.get(str(proto_id), Protocol.UNKNOWN)
        return AdapterStatus(
            state=ConnectionState.CONNECTED,
            protocol=protocol,
            adapter_name=f"ELM327 on {self._conn.port_name()}",
        )

    # --- data ----------------------------------------------------------

    def _ensure(self) -> "pyobd.OBD":  # type: ignore[name-defined]
        if self._conn is None or not self._conn.is_connected():
            raise AdapterError("adapter is not connected")
        return self._conn

    def supported_pids(self) -> set[str]:
        """
        Enumerate the mode-01 PIDs the connected vehicle supports.

        v0.6.12: prefer the raw bitmap probe (queries Mode 01 PID 0x00 /
        0x20 / 0x40 / 0x60 / 0x80 / 0xA0 / 0xC0 and parses the response
        bytes directly). This bypasses python-obd's command-table
        filter, which only includes commands python-obd has decoders
        for — so on a Mazda3 reporting 44 supported PIDs, the raw
        probe returns all 44 even when python-obd recognises only 25
        of them.

        Falls back to python-obd's `supported_commands` set if the raw
        probe returns nothing (e.g. older python-obd that doesn't
        accept synthesised OBDCommands, or adapter rejected the
        force=True query). Either path runs while connected; nothing
        about the wire protocol changes.
        """
        raw = self._raw_supported_pids()
        if raw:
            return raw
        log.debug("raw bitmap probe returned empty; falling back to python-obd supported_commands")
        c = self._ensure()
        out: set[str] = set()
        for cmd in c.supported_commands:
            mode = getattr(cmd, "mode", None)
            pid = getattr(cmd, "pid", None)
            if mode is not None and pid is not None:
                out.add(f"{int(mode):02X}{int(pid):02X}")
        return out

    def _raw_supported_pids(self) -> set[str]:
        """
        Query the mode-01 supported-PID bitmaps directly. Returns the
        full PID set the ECU reports — independent of python-obd's
        decoder coverage.

        Per SAE J1979: each bitmap response is 4 bytes (32 bits). Bit
        `7 - n` of byte `i` (MSB first) indicates whether PID
        `group_base + i*8 + n + 1` is supported. The high bit of the
        last bitmap byte indicates whether the next group should be
        queried. We probe all seven canonical groups but stop early
        when a group's continuation bit is clear.
        """
        if not _HAS_OBD:
            return set()
        try:
            c = self._ensure()
        except AdapterError:
            return set()
        try:
            from obd import OBDCommand, ECU
            from obd.decoders import noop
        except Exception as exc:  # pragma: no cover - lib import failure
            log.debug("raw bitmap probe: cannot import OBDCommand/noop: %s", exc)
            return set()

        out: set[str] = set()
        groups_with_data: list[int] = []
        for group_pid in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0):
            try:
                cmd = OBDCommand(
                    f"raw_supported_{group_pid:02X}",
                    f"raw supported-PID bitmap ({group_pid:02X})",
                    f"01{group_pid:02X}".encode("ascii"),
                    4,
                    noop,
                    ECU.ENGINE,
                )
                resp = c.query(cmd, force=True)
            except Exception as exc:
                log.debug("raw bitmap probe %02X failed: %s", group_pid, exc)
                continue
            if resp is None or resp.is_null():
                log.debug("raw bitmap probe %02X: null response", group_pid)
                continue
            data = _extract_bitmap_bytes(resp)
            if not data:
                log.debug("raw bitmap probe %02X: no usable bitmap bytes in response", group_pid)
                continue
            groups_with_data.append(group_pid)
            log.debug(
                "raw bitmap probe %02X: %s",
                group_pid, " ".join(f"{b:02X}" for b in data),
            )
            for byte_idx, byte_val in enumerate(data[:4]):
                for bit in range(8):
                    if byte_val & (1 << (7 - bit)):
                        pid_num = group_pid + (byte_idx * 8) + bit + 1
                        # PID 0x20, 0x40 etc are the continuation
                        # bitmap PIDs themselves — bit set means
                        # "next bitmap is available", not "PID 0x20 is
                        # a normal data PID". Skip those.
                        if pid_num in (0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0):
                            continue
                        out.add(f"01{pid_num:02X}")
            # Continuation bit is the LSB of the 4th byte. If clear,
            # higher groups are not supported and we can short-circuit.
            if len(data) >= 4 and not (data[3] & 0x01):
                break

        # Soft sanity check: if a real connection responded to at least
        # one group but the resulting set is implausibly small (1–2
        # PIDs total), warn so a diagnostics review notices. Mode 01
        # PID 0x01 (monitor status) is always supported by any OBD-II
        # vehicle, so a count below 3 means the bitmap parse is wrong
        # or the connection is degenerate.
        if groups_with_data and 0 < len(out) < 3:
            log.warning(
                "raw bitmap probe: implausibly low PID count (%d) from %d "
                "responding group(s) — bitmap parse may be malformed",
                len(out), len(groups_with_data),
            )
        return out

    def read_pid(self, pid: str) -> LiveSample | None:
        c = self._ensure()
        # pid is a 4-hex-char "MMPP" string; resolve via python-obd's command table
        mode = int(pid[:2], 16)
        pid_num = int(pid[2:], 16)
        cmd = None
        for candidate in c.supported_commands:
            if getattr(candidate, "mode", None) == mode and getattr(candidate, "pid", None) == pid_num:
                cmd = candidate
                break
        if cmd is None:
            # v0.6.13: PID is supported by the car (raw bitmap probe said
            # so) but python-obd has no decoder for it. Read raw bytes
            # so the simulator can pass them through later.
            return self._read_pid_raw(pid, mode, pid_num)
        try:
            response = c.query(cmd, force=True)
        except Exception as exc:
            self._last_error = str(exc)
            return self._read_pid_raw(pid, mode, pid_num)
        if response is None or response.is_null():
            return self._read_pid_raw(pid, mode, pid_num)
        value = response.value
        unit = None
        magnitude: float | int | str | None
        try:
            unit = str(value.units) if hasattr(value, "units") else None
            if hasattr(value, "magnitude"):
                magnitude = float(value.magnitude)
            elif isinstance(value, (int, float)):
                magnitude = value
            elif isinstance(value, (bytes, bytearray)):
                # Defensive: some PIDs (VIN-style, status enums) come back
                # as bytes through this code path on certain chips. Decode
                # to a clean string so live_data.jsonl stays JSON-serialisable.
                magnitude = _decode_string_response(value)
            else:
                magnitude = _decode_string_response(value)
        except Exception:
            magnitude = _decode_string_response(value)
        return LiveSample(pid=pid, name=cmd.name, value=magnitude, unit=unit)

    def _read_pid_raw(self, pid: str, mode: int, pid_num: int) -> LiveSample | None:
        """
        v0.6.16: fallback path for PIDs python-obd can't decode. Bypasses
        `c.query()` (which gates on python-obd's command-level validation
        and is_null logic) and talks directly to the ELM327 interface
        via `interface.send_and_parse(b"0114")`. This is the same low
        level python-obd uses internally but without the command/
        OBDResponse wrapper that was discarding our raw responses on
        the Mazda3 bench (v0.6.13 fell through here).

        Only fires for Mode 01 — Mode 09 vehicle-info has dedicated
        read paths above, and Mode 02/03/etc. are static services
        the simulator answers from scenario state directly.

        Bumps `_raw_attempts` regardless of outcome and `_raw_successes`
        only when bytes are actually returned. These counts surface in
        the session diagnostics endpoint so the instructor can see
        "fallback fired 22 times, captured 17 of them" — distinguishing
        adapter-side dropouts from bitmap-derivation gaps.
        """
        if mode != 0x01:
            return None
        if not _HAS_OBD:
            return None
        try:
            c = self._ensure()
        except AdapterError:
            return None

        self._raw_attempts += 1

        iface = getattr(c, "interface", None)
        if iface is None or not hasattr(iface, "send_and_parse"):
            log.debug("raw read %s: no interface available", pid)
            return None

        cmd_string = f"{mode:02X}{pid_num:02X}".encode("ascii")
        try:
            messages = iface.send_and_parse(cmd_string)
        except Exception as exc:
            log.debug("raw read %s: send_and_parse raised %s", pid, exc)
            return None

        if not messages:
            log.debug("raw read %s: empty messages from interface", pid)
            return None

        # Walk every message and pick the first that has actual data
        # bytes. On multi-ECU CAN responses, the engine ($7E8) is
        # typically first; on K-Line the only message is the single
        # ECU we addressed.
        for msg in messages:
            data = getattr(msg, "data", None)
            if not isinstance(data, (bytes, bytearray)) or not data:
                continue
            raw = bytes(data)
            # Strip the 0x41 + PID echo prefix if present.
            if len(raw) >= 2 and raw[0] == (0x40 | mode):
                raw = raw[2:]
            if not raw:
                continue
            hex_str = raw.hex().upper()
            self._raw_successes += 1
            log.debug("raw read %s: captured %s", pid, hex_str)
            return LiveSample(
                pid=pid,
                name=f"raw {pid}",
                value=f"raw:{hex_str}",
                unit=None,
            )

        log.debug("raw read %s: messages had no usable data bytes", pid)
        return None

    def stream_pids(self, pids: Iterable[str]) -> Iterable[LiveSample]:
        pids = list(pids)
        while True:
            for pid in pids:
                sample = self.read_pid(pid)
                if sample is not None:
                    yield sample
            if self._conn is None or not self._conn.is_connected():
                return
            time.sleep(0.05)

    def read_vehicle_info(self) -> VehicleInfo:
        c = self._ensure()
        info = VehicleInfo()
        for attr_name, cmd_name in (
            ("vin", "VIN"),
            ("calibration_id", "CALIBRATION_ID"),
            ("cvn", "CVN"),
            ("ecu_name", "ECU_NAME"),
        ):
            cmd = getattr(pyobd.commands, cmd_name, None)
            if cmd is None:
                continue
            try:
                resp = c.query(cmd, force=True)
                if resp and not resp.is_null():
                    setattr(info, attr_name, _decode_string_response(resp.value))
            except Exception as exc:  # pragma: no cover
                log.debug("vehicle info %s failed: %s", cmd_name, exc)
        return info

    def read_dtcs(self) -> list[DTC]:
        c = self._ensure()
        out: list[DTC] = []
        for cmd_name, status in (
            ("GET_DTC", DTCStatus.STORED),
            ("GET_CURRENT_DTC", DTCStatus.PENDING),
            ("GET_PERMANENT_DTC", DTCStatus.PERMANENT),
        ):
            cmd = getattr(pyobd.commands, cmd_name, None)
            if cmd is None:
                continue
            try:
                resp = c.query(cmd, force=True)
            except Exception as exc:  # pragma: no cover
                log.debug("dtc query %s failed: %s", cmd_name, exc)
                continue
            if not resp or resp.is_null():
                continue
            for entry in resp.value or []:
                # python-obd returns DTC tuples where the code can be str,
                # bytes, or bytearray depending on chip + library version.
                # Normalise both fields through `_decode_string_response`
                # so we never write `bytearray(b'P0420')` into a session.
                if isinstance(entry, (list, tuple)):
                    raw_code, raw_desc = entry[0], entry[1] if len(entry) > 1 else ""
                else:
                    raw_code, raw_desc = entry, ""
                code = _decode_string_response(raw_code)
                desc = _decode_string_response(raw_desc)
                if not code:
                    continue
                out.append(DTC(code=code, status=status, description=desc))
        return out

    def clear_dtcs(self) -> bool:
        c = self._ensure()
        cmd = getattr(pyobd.commands, "CLEAR_DTC", None)
        if cmd is None:
            return False
        try:
            c.query(cmd, force=True)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            return False

    def read_freeze_frame(self) -> FreezeFrame | None:
        c = self._ensure()
        ff = FreezeFrame()
        cmd = getattr(pyobd.commands, "FREEZE_DTC", None)
        if cmd is not None:
            try:
                resp = c.query(cmd, force=True)
                if resp and not resp.is_null():
                    # Same bytes/bytearray normalisation as the VIN read.
                    ff.dtc = _decode_string_response(resp.value)
            except Exception as exc:  # pragma: no cover
                log.debug("freeze dtc query failed: %s", exc)
        return ff if ff.dtc else None

    def read_monitors(self) -> list[Monitor]:
        c = self._ensure()
        cmd = getattr(pyobd.commands, "STATUS", None)
        out: list[Monitor] = []
        if cmd is None:
            return out
        try:
            resp = c.query(cmd, force=True)
        except Exception as exc:  # pragma: no cover
            log.debug("monitor status query failed: %s", exc)
            return out
        if not resp or resp.is_null():
            return out
        status = resp.value
        for name in (
            "MISFIRE_MONITORING", "FUEL_SYSTEM_MONITORING", "COMPONENT_MONITORING",
            "CATALYST_MONITORING", "HEATED_CATALYST_MONITORING",
            "EVAPORATIVE_SYSTEM_MONITORING", "SECONDARY_AIR_SYSTEM_MONITORING",
            "AC_SYSTEM_REFRIGERANT_MONITORING", "OXYGEN_SENSOR_MONITORING",
            "OXYGEN_SENSOR_HEATER_MONITORING", "EGR_VVT_SYSTEM_MONITORING",
        ):
            test = getattr(status, name, None)
            if test is None:
                continue
            out.append(Monitor(
                name=name.replace("_MONITORING", "").replace("_", " ").title(),
                supported=bool(getattr(test, "available", False)),
                ready=not bool(getattr(test, "incomplete", True)),
            ))
        return out

    def read_metrics(self) -> dict[str, int]:
        """
        v0.6.16: counts how many times the raw-passthrough fallback
        fired and how many of those produced data. Surfaced in the
        session diagnostics endpoint so a low capture count can be
        diagnosed as either "fallback never tried" (raw_attempts=0)
        or "fallback tried but bus silent" (raw_attempts > 0,
        raw_successes < attempts).
        """
        return {
            "raw_attempts": self._raw_attempts,
            "raw_successes": self._raw_successes,
        }

    def read_raw(self, mode: int, pid: int | None = None) -> bytes | None:
        c = self._ensure()
        # Compose raw OBD message and send through ELM327 directly.
        if pid is None:
            cmd_str = f"{mode:02X}"
        else:
            cmd_str = f"{mode:02X}{pid:02X}"
        try:
            messages = c.interface.send_and_parse(cmd_str.encode())
            if not messages:
                return None
            data = messages[0].data
            return bytes(data) if data else None
        except Exception as exc:
            self._last_error = str(exc)
            return None
