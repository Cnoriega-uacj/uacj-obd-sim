from __future__ import annotations

from .base import Adapter


def open_adapter(kind: str = "auto", **kwargs) -> Adapter:
    """
    Open an adapter by kind:
      - "auto"      → try ELM327/STN2120 on common ports, fall back to mock
      - "elm327"    → real serial/Bluetooth ELM327 via python-obd
      - "mock"      → in-memory simulator for offline development
      - "replay"    → replay from a saved session directory
    """
    kind = kind.lower()
    if kind == "mock":
        from .mock import MockAdapter

        return MockAdapter(**kwargs)
    if kind in ("elm327", "stn2120", "real"):
        from .elm327 import Elm327Adapter

        return Elm327Adapter(**kwargs)
    if kind == "replay":
        from .replay import ReplayAdapter

        return ReplayAdapter(**kwargs)
    if kind == "auto":
        try:
            from .elm327 import Elm327Adapter

            return Elm327Adapter(**kwargs)
        except Exception:
            from .mock import MockAdapter

            return MockAdapter(**kwargs)
    raise ValueError(f"unknown adapter kind: {kind}")
