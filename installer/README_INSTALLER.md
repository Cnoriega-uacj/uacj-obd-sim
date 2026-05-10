# UACJ OBD-II Training Simulator — Laptop Installer

This folder contains everything you need to run the simulator's
laptop-side dashboard. Both Windows and macOS/Linux launchers are
included.

## Quickstart

### Windows (10/11)

1. Make sure Python 3.11 or newer is installed from <https://python.org>.
   Check **"Add Python to PATH"** during install.
2. Double-click **`start_uacj.bat`**.
3. The first run takes ~2 minutes (creates the virtualenv, installs
   dependencies, seeds the sample vehicles). Subsequent runs are instant.
4. The dashboard opens automatically at <http://localhost:8000>.

### macOS / Linux

```bash
./start_uacj.sh
```

Same flow as the Windows launcher.

## What's pre-installed on first run

- All Python dependencies (FastAPI, python-obd, python-can, pyserial,
  pydantic, etc.)
- Five pre-loaded sample vehicles so the dashboard isn't empty:
    - 2015 Honda Civic LX (healthy)
    - 2008 Chevrolet Silverado 1500 (P0420 catalyst)
    - 2007 Toyota Corolla LE (healthy)
    - 2014 Ford F-150 EcoBoost (P0171 lean)
    - 2006 Nissan Sentra (P0301 cyl-1 misfire)
- Six teaching presets ready to instantiate.

## Connecting to the simulator board

In the dashboard's Classroom view, set the simulator URL to the Pi's
hostname or IP, e.g. `http://uacj-sim.local:8765`. Click "Test
connection" — green check means the board is reachable.

Then go to **Scenarios → New from preset**, pick a preset and a source
session, and click **Push to simulator**. The Pi answers as that
vehicle on the OBD-II port.

## Backing up and moving to a new laptop

In the dashboard's left rail, click **Backup all data**. A ZIP
downloads with the full SQLite database and every session folder.
On the new laptop, run the launcher once, then **Restore from backup**
and pick the ZIP. State is identical to the original machine.

## If something goes wrong on first run

- **"Python is not installed"** — Download from python.org, restart
  the prompt/terminal, and rerun. On Windows, check the "Add Python
  to PATH" checkbox.
- **"Failed to install dependencies"** — Check internet access. The
  installer downloads ~120 MB of Python packages.
- **Port 8000 already in use** — Edit the launcher and change
  `--port 8000` to e.g. `--port 8001`, then open the matching URL.
- Any other issue — contact me directly with the last 30 lines of
  output from the launcher window.
