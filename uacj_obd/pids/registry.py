from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


@dataclass(frozen=True)
class PidDefinition:
    """
    Definition for a single PID — standard SAE J1979 (mode 0x01) or
    manufacturer-specific (mode 0x22).

    formula:    Python expression operating on a tuple `b` of response
                bytes. Evaluated with a sandboxed `eval`.
                Examples:
                  RPM (mode 0x01 PID 0x0C):  "(b[0] * 256 + b[1]) / 4"
                  Speed (mode 0x01 PID 0x0D): "b[0]"
    """

    mode: int
    pid: int
    name: str
    unit: str = ""
    formula: str = "b[0]"
    bytes_expected: int = 1
    manufacturer: str | None = None
    description: str = ""

    @property
    def key(self) -> str:
        return f"{self.mode:02X}{self.pid:02X}"


class PidRegistry:
    def __init__(self, defs: Iterable[PidDefinition] = ()) -> None:
        self._by_key: dict[str, PidDefinition] = {}
        for d in defs:
            self.register(d)

    def register(self, d: PidDefinition) -> None:
        self._by_key[d.key] = d

    def get(self, key: str) -> PidDefinition | None:
        return self._by_key.get(key.upper())

    def all(self) -> list[PidDefinition]:
        return list(self._by_key.values())

    def by_manufacturer(self, manufacturer: str | None) -> list[PidDefinition]:
        return [d for d in self._by_key.values() if d.manufacturer == manufacturer]

    def load_yaml(self, path: str | Path) -> int:
        raw = yaml.safe_load(Path(path).read_text()) or []
        n = 0
        for entry in raw:
            self.register(PidDefinition(**entry))
            n += 1
        return n

    def decode(self, key: str, data: bytes) -> float | int | str | None:
        defn = self.get(key)
        if defn is None or not data:
            return None
        b = list(data[: defn.bytes_expected])
        if len(b) < defn.bytes_expected:
            return None
        try:
            return eval(defn.formula, {"__builtins__": {}}, {"b": b})  # noqa: S307
        except Exception:
            return None


def load_default_registry() -> PidRegistry:
    """Load the bundled YAML files in uacj_obd/pids/data/."""
    reg = PidRegistry()
    data_dir = Path(__file__).parent / "data"
    if data_dir.exists():
        for f in sorted(data_dir.glob("*.yaml")):
            reg.load_yaml(f)
    return reg
