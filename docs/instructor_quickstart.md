# Instructor Quick-Start

**For UACJ automotive program — print this and keep it next to the simulator.**

A class can run end-to-end with three buttons. The full reference is in
`docs/instructor.md`; this is the page-1 cheat sheet.

---

## Before class (1 minute)

1. Power the Pi simulator board (12 V via OBD-II pin 16, or USB-C).
2. On the laptop, double-click **`start_uacj.bat`** (Windows) or
   **`./start_uacj.sh`** (Mac/Linux).
3. The dashboard opens at <http://localhost:8000>.

> Switch language any time with the **EN/ES** button in the top-right.

---

## Loading a teaching scenario (3 buttons)

1. Open **Scenarios** in the top nav.
2. In the **From preset** panel, pick:
    - a **preset** (e.g. *P0420 catalyst*, *P0171 lean*,
      *P0301 misfire*, *P0455 EVAP leak*, *Drive-cycle incomplete*,
      *U0100 lost-comm*)
    - a **source session** (e.g. *2008 Silverado* — provides the live
      data the scenario will ride on top of)
3. Click **Instantiate** → a new scenario appears in the left list.
4. Click that scenario, then **Push to simulator**.

The Pi is now answering scan tools as that vehicle. Students plug in.

---

## During class

| Tab | Purpose |
|---|---|
| **Acquisition** | Live gauges, DTCs, monitors, captured-vehicle list |
| **Scenarios** | Build and edit teaching scenarios |
| **Classroom** | Live request log — see what each student's tool is asking for |
| **Diff** | Side-by-side compare of two captured sessions |

The **Classroom** view auto-refreshes once per second. Watch it
during class — every scan-tool query shows up with a colored pill
(green positive, red NRC, yellow warning).

---

## Six built-in presets to start with

| Preset | Teaches |
|---|---|
| **P0420** | Catalyst efficiency below threshold — 3-way cat diagnosis |
| **P0171** | System too lean bank 1 — fuel trim + MAF/vacuum reasoning |
| **P0301 + P0300** | Cyl-1 misfire + random misfire — coil/injector/compression |
| **P0455** | Evaporative system large leak — gas cap, hose, purge solenoid |
| **Drive-cycle incomplete** | Readiness monitors not ready — emissions-readiness |
| **U0100** | Lost communication with ECM — network/wiring fault |

---

## Saving a real vehicle for later (so you can replay it next semester)

1. Plug the **OBDLink SX** into the laptop USB port and into a real car.
2. **Acquisition** → pick adapter `elm327`, set the port (`COM3` on
   Windows; `/dev/ttyUSB0` on Linux/Mac), click **Start**.
3. Run for 30–60 seconds (idle is fine). Click **Stop**.
4. The vehicle is saved under `data/sessions/{VIN}_{make}_{model}_{year}/`.
5. From now on it appears as a "Source session" option when building
   scenarios.

---

## Backup before semester end

Click **Backup all data** in the left rail. A ZIP downloads with the
full database + every session. Save it to a USB stick. To restore on
a new laptop, run the launcher once, click **Restore from backup**,
pick the ZIP.

---

## When something goes wrong

| Symptom | First thing to try |
|---|---|
| Dashboard says "no vehicle connected" | Check the OBDLink SX cable; turn the car key fully on |
| "Push to simulator" times out | In Classroom view, re-test the simulator URL; check the Pi has power |
| Scan tool shows "no communication" | Confirm 12 V on OBD pin 16 of the simulator with a multimeter |
| DTC the student is reading isn't the one in the scenario | Re-push the scenario (the Pi may have cleared codes after the student tried mode 04) |
| Pi unreachable on `uacj-sim.local` | Use the IP directly — find it from the router's admin page or run `hostname -I` on the Pi over SSH |

---

## Contacts

- **Software issues:** open a GitHub issue on the project repo
  (link delivered with the source).
- **Hardware swap (parts arrive damaged, etc.):** Mercado Libre
  return policy on each item; DigiKey México has 2-business-day
  returns.

---

*UACJ OBD-II Training Simulator v0.4.0 — quick-start. Print at A4 or
US Letter; the layout fits on one printed sheet.*
