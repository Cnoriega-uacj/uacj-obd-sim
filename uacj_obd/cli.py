from __future__ import annotations

import logging
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from uacj_obd.acquisition import AcquisitionSession, SessionConfig
from uacj_obd.adapters import open_adapter
from uacj_obd.pids import load_default_registry
from uacj_obd.storage import Database, SessionStore

console = Console()


@click.group()
@click.option("--data", default="data", show_default=True,
              help="Data root for sessions database and folders.")
@click.option("-v", "--verbose", count=True)
@click.pass_context
def main(ctx: click.Context, data: str, verbose: int) -> None:
    logging.basicConfig(
        level=logging.WARNING - 10 * min(verbose, 2),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["data"] = Path(data)


@main.command()
@click.option("--adapter", default="auto",
              type=click.Choice(["auto", "mock", "elm327", "stn2120", "replay"]))
@click.option("--port", default=None, help="Serial port (e.g. /dev/ttyUSB0)")
@click.option("--duration", default=10.0, type=float,
              help="Capture duration in seconds; use 0 for indefinite.")
@click.option("--notes", default="", help="Free-form notes saved with the session.")
@click.pass_context
def capture(ctx: click.Context, adapter: str, port: str | None,
             duration: float, notes: str) -> None:
    """Run a one-shot capture session against an adapter."""
    data: Path = ctx.obj["data"]
    db = Database(data / "uacj.db")
    store = SessionStore(data / "sessions")
    pid_reg = load_default_registry()
    a = open_adapter(adapter, portstr=port) if port else open_adapter(adapter)
    sess = AcquisitionSession(a, store, db, pid_reg, SessionConfig(notes=notes))
    meta = sess.start()
    console.print(f"[green]✓[/] connected: {meta.adapter} • protocol={meta.protocol.value}")
    console.print(f"[green]✓[/] vehicle: VIN={meta.vehicle.vin or '—'} "
                  f"{meta.vehicle.make or ''} {meta.vehicle.model or ''} "
                  f"{meta.vehicle.year or ''}")
    console.print(f"[green]✓[/] session_id={meta.session_id}")
    try:
        n = sess.run(duration_s=duration if duration > 0 else None)
        console.print(f"[green]✓[/] captured {n} samples")
    finally:
        path = sess.close()
        console.print(f"[green]✓[/] saved to {path}")


@main.command()
@click.pass_context
def vehicles(ctx: click.Context) -> None:
    """List vehicles seen so far."""
    db = Database(ctx.obj["data"] / "uacj.db")
    rows = db.list_vehicles()
    table = Table(title="Vehicles")
    for col in ("vin", "make", "model", "year", "first_seen", "last_seen"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["vin"] or "", r["make"] or "", r["model"] or "",
                       str(r["year"] or ""), r["first_seen"], r["last_seen"])
    console.print(table)


@main.command()
@click.option("--vin", default=None)
@click.pass_context
def sessions(ctx: click.Context, vin: str | None) -> None:
    """List capture sessions."""
    db = Database(ctx.obj["data"] / "uacj.db")
    rows = db.list_sessions(vin=vin)
    table = Table(title="Sessions")
    for col in ("session_id", "vin", "started_at", "samples", "folder"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["session_id"], r["vin"] or "—", r["started_at"],
                       str(r["sample_count"]), r["folder"])
    console.print(table)


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.pass_context
def serve(ctx: click.Context, host: str, port: int) -> None:
    """Run the FastAPI dashboard."""
    import uvicorn

    from uacj_obd.api import create_app

    app = create_app(data_root=ctx.obj["data"])
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main(obj={})
