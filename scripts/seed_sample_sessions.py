#!/usr/bin/env python3
"""
Seed five realistic sample vehicle sessions so the dashboard has
content on first boot. Students can practice diagnosis from day one
without the school needing to capture a real vehicle first.

Run: python scripts/seed_sample_sessions.py [data_dir]

Idempotent — re-running overwrites the same session IDs.

The five sample vehicles cover the protocol matrix:
  - 2015 Honda Civic LX            CAN 11/500    healthy
  - 2008 Chevrolet Silverado 1500  CAN 11/500    P0420 catalyst
  - 2007 Toyota Corolla LE         CAN 11/500    healthy, full readiness
  - 2014 Ford F-150 XLT            CAN 11/500    P0171 lean
  - 2006 Nissan Sentra             KWP fast-init pre-CAN, P0301 misfire

Each session contains:
  - metadata.json (vehicle identity + session info)
  - dtcs.json
  - monitors.json
  - freeze_frame.json (where applicable)
  - live_data.jsonl (60 seconds of streaming live data, 1 Hz cadence)

The data is plausible but synthetic — not real vehicle captures. Marked
in metadata.notes as "synthetic sample" so it's never confused with a
real capture.
"""

from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from uacj_obd.models import (
    DTC,
    DTCStatus,
    FreezeFrame,
    LiveSample,
    Monitor,
    Protocol,
    SessionMetadata,
    VehicleInfo,
)
from uacj_obd.storage import Database, SessionStore


# Standard SAE J1979 monitor list. Set per scenario below.
MONITORS_ALL = [
    "Misfire",
    "Fuel System",
    "Components",
    "Catalyst",
    "Heated Catalyst",
    "Evaporative System",
    "Secondary Air System",
    "AC Refrigerant",
    "Oxygen Sensor",
    "Oxygen Sensor Heater",
    "EGR System",
]


def _live_pid(pid: str, name: str, value: float | int, unit: str, ts: datetime) -> dict:
    return LiveSample(ts=ts, pid=pid, name=name, value=value, unit=unit).model_dump(mode="json")


def _stream(start: datetime, duration_s: int, generator) -> list[dict]:
    """Generate a 1 Hz live-data stream for `duration_s` seconds."""
    out: list[dict] = []
    for i in range(duration_s):
        t = start + timedelta(seconds=i)
        out.extend(generator(i, t))
    return out


# ---------- per-vehicle generators ----------------------------------------


def healthy_civic(i: int, t: datetime) -> list[dict]:
    """2015 Civic LX, idling warm, slight idle wander."""
    rpm = 760 + 20 * math.sin(i / 4.0) + random.randint(-8, 8)
    speed = 0
    coolant = 88 + (1 if i > 30 else 0)
    throttle = 14 + random.randint(-1, 1)
    maf = 2.8 + 0.1 * math.sin(i / 3.0)
    o2 = 0.45 + 0.4 * math.sin(i / 2.0)
    return [
        _live_pid("010C", "RPM", round(rpm), "rpm", t),
        _live_pid("010D", "SPEED", speed, "km/h", t),
        _live_pid("0105", "COOLANT_TEMP", coolant, "°C", t),
        _live_pid("0111", "THROTTLE_POS", throttle, "%", t),
        _live_pid("0110", "MAF", round(maf, 2), "g/s", t),
        _live_pid("0114", "O2_B1S1_VOLTAGE", round(o2, 3), "V", t),
        _live_pid("0104", "ENGINE_LOAD", 22 + random.randint(-2, 2), "%", t),
        _live_pid("010F", "INTAKE_TEMP", 32, "°C", t),
        _live_pid("0142", "MODULE_VOLTAGE", round(13.9 + 0.05 * math.sin(i), 2), "V", t),
        _live_pid("22015C", "HONDA_ATF_TEMP", 78, "°C", t),
    ]


def misfiring_silverado(i: int, t: datetime) -> list[dict]:
    """2008 Silverado 1500 5.3 V8, P0420 catalyst, idle slightly rough."""
    rpm = 700 + 60 * math.sin(i / 1.5)  # rough idle wander
    return [
        _live_pid("010C", "RPM", round(rpm), "rpm", t),
        _live_pid("010D", "SPEED", 0, "km/h", t),
        _live_pid("0105", "COOLANT_TEMP", 89, "°C", t),
        _live_pid("0111", "THROTTLE_POS", 16, "%", t),
        _live_pid("0110", "MAF", round(4.2 + 0.3 * math.sin(i / 2), 2), "g/s", t),
        _live_pid("0114", "O2_B1S1_VOLTAGE", round(0.05 + 0.1 * (i % 3), 3), "V", t),
        _live_pid("0115", "O2_B1S2_VOLTAGE", round(0.7 + 0.05 * math.sin(i), 3), "V", t),
        _live_pid("0104", "ENGINE_LOAD", 28, "%", t),
        _live_pid("0106", "STFT_B1", round(8 + 2 * math.sin(i / 2), 1), "%", t),
        _live_pid("0107", "LTFT_B1", 6, "%", t),
        _live_pid("220005", "GM_ENGINE_OIL_LIFE", 47, "%", t),
        _live_pid("22115A", "GM_TRANSMISSION_FLUID_TEMP", 92, "°C", t),
    ]


def healthy_corolla(i: int, t: datetime) -> list[dict]:
    """2007 Corolla LE, healthy, all readiness monitors complete."""
    rpm = 720 + random.randint(-5, 5)
    return [
        _live_pid("010C", "RPM", round(rpm), "rpm", t),
        _live_pid("010D", "SPEED", 0, "km/h", t),
        _live_pid("0105", "COOLANT_TEMP", 86, "°C", t),
        _live_pid("0111", "THROTTLE_POS", 12, "%", t),
        _live_pid("0110", "MAF", 2.4, "g/s", t),
        _live_pid("0114", "O2_B1S1_VOLTAGE", round(0.45 + 0.4 * math.sin(i / 2), 3), "V", t),
        _live_pid("0104", "ENGINE_LOAD", 19, "%", t),
        _live_pid("011F", "RUNTIME", i + 600, "s", t),
        _live_pid("220101", "TOYOTA_ENGINE_RUN_TIME", round((i + 600) / 60), "min", t),
    ]


def lean_f150(i: int, t: datetime) -> list[dict]:
    """2014 F-150 XLT 3.5L EcoBoost, P0171 lean bank 1."""
    rpm = 740 + random.randint(-10, 10)
    # Sustained positive STFT/LTFT — classic lean signature
    stft = 18 + math.sin(i / 3) * 2
    ltft = 22
    return [
        _live_pid("010C", "RPM", round(rpm), "rpm", t),
        _live_pid("010D", "SPEED", 0, "km/h", t),
        _live_pid("0105", "COOLANT_TEMP", 91, "°C", t),
        _live_pid("0111", "THROTTLE_POS", 13, "%", t),
        _live_pid("010B", "MAP", 33, "kPa", t),
        _live_pid("0110", "MAF", round(3.0 + 0.1 * math.sin(i), 2), "g/s", t),
        _live_pid("0106", "STFT_B1", round(stft, 1), "%", t),
        _live_pid("0107", "LTFT_B1", ltft, "%", t),
        _live_pid("0114", "O2_B1S1_VOLTAGE", round(0.12 + 0.05 * math.sin(i), 3), "V", t),
        _live_pid("0104", "ENGINE_LOAD", 26, "%", t),
        _live_pid("22115C", "FORD_TRANS_OIL_TEMP", 75, "°C", t),
        _live_pid("221101", "FORD_KEY_ON_RUN_TIME", i + 240, "s", t),
    ]


def misfire_sentra(i: int, t: datetime) -> list[dict]:
    """2006 Sentra 1.8L, P0301 cylinder 1 misfire, KWP fast-init."""
    rpm = 680 + 80 * math.sin(i / 1.2)  # very rough idle
    return [
        _live_pid("010C", "RPM", round(rpm), "rpm", t),
        _live_pid("010D", "SPEED", 0, "km/h", t),
        _live_pid("0105", "COOLANT_TEMP", 87, "°C", t),
        _live_pid("0111", "THROTTLE_POS", 17, "%", t),
        _live_pid("0110", "MAF", round(2.1 + 0.5 * math.sin(i / 1.5), 2), "g/s", t),
        _live_pid("0114", "O2_B1S1_VOLTAGE", round(0.5 + 0.45 * math.sin(i), 3), "V", t),
        _live_pid("0104", "ENGINE_LOAD", round(24 + 6 * math.sin(i / 1.2)), "%", t),
        _live_pid("0142", "MODULE_VOLTAGE", 13.7, "V", t),
    ]


# ---------- session manifest ---------------------------------------------


SAMPLES = [
    {
        "session_id": "sample_civic_2015_healthy",
        "vehicle": VehicleInfo(
            vin="1HGFB2F59FH123456",
            make="Honda", model="Civic LX", year=2015,
            calibration_id="37805-RX0-A030", ecu_name="ECM-PGM-FI",
        ),
        "protocol": Protocol.ISO_15765_4_CAN_11_500,
        "adapter": "OBDLink SX (sample)",
        "dtcs": [],
        "freeze_frame": None,
        "monitors_supported": MONITORS_ALL,
        "monitors_ready": MONITORS_ALL,
        "generator": healthy_civic,
        "duration_s": 60,
        "notes": "synthetic sample — healthy 2015 Civic at idle, all monitors complete",
    },
    {
        "session_id": "sample_silverado_2008_p0420",
        "vehicle": VehicleInfo(
            vin="2GCEC13C081234567",
            make="Chevrolet", model="Silverado 1500 5.3L", year=2008,
            calibration_id="12640003-A001",
        ),
        "protocol": Protocol.ISO_15765_4_CAN_11_500,
        "adapter": "OBDLink SX (sample)",
        "dtcs": [
            DTC(code="P0420", status=DTCStatus.STORED,
                description="Catalyst System Efficiency Below Threshold (Bank 1)"),
            DTC(code="P0420", status=DTCStatus.PERMANENT,
                description="Catalyst System Efficiency Below Threshold (Bank 1)"),
        ],
        "freeze_frame": FreezeFrame(
            dtc="P0420",
            pids={"010C": 720, "010D": 0, "0105": 88, "0104": 26, "0114": 0.08},
        ),
        "monitors_supported": MONITORS_ALL,
        "monitors_ready": [m for m in MONITORS_ALL if m != "Catalyst"],
        "generator": misfiring_silverado,
        "duration_s": 60,
        "notes": "synthetic sample — 2008 Silverado with stored+permanent P0420 catalyst",
    },
    {
        "session_id": "sample_corolla_2007_healthy",
        "vehicle": VehicleInfo(
            vin="2T1BR32E47C123456",
            make="Toyota", model="Corolla LE", year=2007,
            calibration_id="89663-02L00",
        ),
        "protocol": Protocol.ISO_15765_4_CAN_11_500,
        "adapter": "OBDLink SX (sample)",
        "dtcs": [],
        "freeze_frame": None,
        "monitors_supported": MONITORS_ALL,
        "monitors_ready": MONITORS_ALL,
        "generator": healthy_corolla,
        "duration_s": 60,
        "notes": "synthetic sample — healthy 2007 Corolla, all readiness monitors complete",
    },
    {
        "session_id": "sample_f150_2014_p0171_lean",
        "vehicle": VehicleInfo(
            vin="1FTFW1ET5EFC12345",
            make="Ford", model="F-150 XLT 3.5L EcoBoost", year=2014,
            calibration_id="DL3A-12A650-AHA",
        ),
        "protocol": Protocol.ISO_15765_4_CAN_11_500,
        "adapter": "OBDLink SX (sample)",
        "dtcs": [
            DTC(code="P0171", status=DTCStatus.STORED,
                description="System Too Lean (Bank 1)"),
            DTC(code="P0171", status=DTCStatus.PENDING,
                description="System Too Lean (Bank 1)"),
        ],
        "freeze_frame": FreezeFrame(
            dtc="P0171",
            pids={"010C": 740, "010D": 0, "0105": 91, "0106": 18.0, "0107": 22},
        ),
        "monitors_supported": MONITORS_ALL,
        "monitors_ready": [m for m in MONITORS_ALL if m not in ("Fuel System", "Oxygen Sensor")],
        "generator": lean_f150,
        "duration_s": 60,
        "notes": "synthetic sample — 2014 F-150 EcoBoost with P0171 lean bank 1",
    },
    {
        "session_id": "sample_sentra_2006_p0301_misfire",
        "vehicle": VehicleInfo(
            vin="3N1AB61E16L123456",
            make="Nissan", model="Sentra 1.8L", year=2006,
        ),
        "protocol": Protocol.ISO_14230_4_KWP_FAST,
        "adapter": "OBDLink SX (sample)",
        "dtcs": [
            DTC(code="P0301", status=DTCStatus.STORED,
                description="Cylinder 1 Misfire Detected"),
            DTC(code="P0300", status=DTCStatus.PENDING,
                description="Random/Multiple Cylinder Misfire Detected"),
        ],
        "freeze_frame": FreezeFrame(
            dtc="P0301",
            pids={"010C": 660, "010D": 0, "0105": 87, "0104": 28},
        ),
        "monitors_supported": MONITORS_ALL,
        "monitors_ready": [m for m in MONITORS_ALL if m not in ("Misfire", "Catalyst")],
        "generator": misfire_sentra,
        "duration_s": 60,
        "notes": "synthetic sample — 2006 Sentra (KWP fast-init) with P0301/P0300 misfire",
    },
]


def write_sample(store: SessionStore, db: Database, sample: dict) -> Path:
    started = datetime(2026, 4, 28, 14, 0, 0, tzinfo=timezone.utc)
    ended = started + timedelta(seconds=sample["duration_s"])
    meta = SessionMetadata(
        session_id=sample["session_id"],
        started_at=started,
        ended_at=ended,
        protocol=sample["protocol"],
        adapter=sample["adapter"],
        vehicle=sample["vehicle"],
        sample_count=0,
        notes=sample["notes"],
    )
    writer = store.open_session(meta)
    rng_state = random.getstate()
    random.seed(hash(sample["session_id"]))
    try:
        live_records = _stream(started, sample["duration_s"], sample["generator"])
        for rec in live_records:
            writer._live.write(json.dumps(rec) + "\n")
            writer._sample_count += 1
        writer.write_dtcs(sample["dtcs"])
        monitors = [
            Monitor(
                name=name,
                supported=name in sample["monitors_supported"],
                ready=name in sample["monitors_ready"],
            )
            for name in MONITORS_ALL
        ]
        writer.write_monitors(monitors)
        if sample["freeze_frame"]:
            writer.write_freeze_frame(sample["freeze_frame"])
    finally:
        random.setstate(rng_state)
    writer.close()

    v = sample["vehicle"]
    if v.vin:
        db.upsert_vehicle(v.vin, v.make, v.model, v.year, started.isoformat())
    # Insert session row (skip if duplicate from prior seed)
    try:
        db.insert_session(
            session_id=meta.session_id,
            vin=v.vin,
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            protocol=meta.protocol.value if hasattr(meta.protocol, "value") else str(meta.protocol),
            adapter=meta.adapter,
            sample_count=writer._sample_count,
            folder=str(writer.dir),
            notes=meta.notes,
        )
    except Exception:
        # Already seeded — refresh the metadata fields that may have changed.
        db.update_session(
            meta.session_id,
            ended_at=ended.isoformat(),
            sample_count=writer._sample_count,
            notes=meta.notes,
        )
    return writer.dir


def main() -> int:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(data_dir / "uacj.db")
    store = SessionStore(data_dir / "sessions")
    written: list[Path] = []
    for sample in SAMPLES:
        path = write_sample(store, db, sample)
        written.append(path)
    print(f"Seeded {len(written)} sample sessions under {data_dir}/sessions/:")
    for p in written:
        print(f"  - {p.relative_to(data_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
