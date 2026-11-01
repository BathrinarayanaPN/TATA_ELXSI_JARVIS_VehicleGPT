"""Minimal, dependency-free DBC codec (Intel/little-endian signals, signed and unsigned).

The .dbc file is standard and also opens in cantools, SavvyCAN, Vector CANdb++ etc.
A built-in codec keeps the edge image small and avoids pulling extra packages onto the ECU.
"""
import re
from dataclasses import dataclass, field

_BO = re.compile(r"^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)")
_SG = re.compile(
    r"^\s*SG_\s+(\w+)\s*:\s*(\d+)\|(\d+)@([01])([+-])\s*\(([^,]+),([^)]+)\)\s*\[([^|]*)\|([^\]]*)\]\s*\"([^\"]*)\""
)


@dataclass
class Signal:
    name: str
    start: int
    length: int
    signed: bool
    factor: float
    offset: float
    minimum: float
    maximum: float
    unit: str

    def encode(self, value: float) -> int:
        value = min(max(value, self.minimum), self.maximum) if self.maximum > self.minimum else value
        raw = int(round((value - self.offset) / self.factor))
        lo, hi = (-(1 << (self.length - 1)), (1 << (self.length - 1)) - 1) if self.signed else (0, (1 << self.length) - 1)
        raw = min(max(raw, lo), hi)
        return raw & ((1 << self.length) - 1)

    def decode(self, raw: int) -> float:
        if self.signed and raw & (1 << (self.length - 1)):
            raw -= 1 << self.length
        return raw * self.factor + self.offset


@dataclass
class Message:
    frame_id: int
    name: str
    dlc: int
    sender: str
    signals: list = field(default_factory=list)

    def encode(self, values: dict) -> bytes:
        word = 0
        for s in self.signals:
            if s.name in values and values[s.name] is not None:
                word |= s.encode(float(values[s.name])) << s.start
        return word.to_bytes(self.dlc, "little")

    def decode(self, data: bytes) -> dict:
        word = int.from_bytes(bytes(data[: self.dlc]).ljust(self.dlc, b"\0"), "little")
        return {s.name: s.decode((word >> s.start) & ((1 << s.length) - 1)) for s in self.signals}


class Database:
    def __init__(self, path):
        self.messages, self.by_id, self.by_name = [], {}, {}
        current = None
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = _BO.match(line)
                if m:
                    current = Message(int(m[1]), m[2], int(m[3]), m[4])
                    self.messages.append(current)
                    self.by_id[current.frame_id] = current
                    self.by_name[current.name] = current
                    continue
                m = _SG.match(line)
                if m and current is not None:
                    if m[4] != "1":
                        raise ValueError(f"Only Intel byte order supported: {m[1]}")
                    current.signals.append(Signal(
                        m[1], int(m[2]), int(m[3]), m[5] == "-", float(m[6]), float(m[7]),
                        float(m[8] or 0), float(m[9] or 0), m[10]))

    def encode(self, name: str, values: dict):
        msg = self.by_name[name]
        return msg.frame_id, msg.encode(values)

    def decode(self, frame_id: int, data: bytes):
        msg = self.by_id.get(frame_id)
        return (msg.name, msg.decode(data)) if msg else (None, {})
