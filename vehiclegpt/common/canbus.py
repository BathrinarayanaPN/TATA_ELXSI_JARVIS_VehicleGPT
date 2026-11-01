"""CAN transport abstraction.

* builtin-udp : raw CAN frames carried over localhost UDP. Zero drivers, works on Windows.
* anything else: handed to python-can (socketcan, udp_multicast, pcan, kvaser, vector, ...).

The collector only ever receives a ReadOnlyBus, which has no send() method at all,
so the AI side physically cannot put a frame on the vehicle network.
"""
import socket
import struct
import time
from dataclasses import dataclass

import config

_HDR = struct.Struct("<IBd")  # arbitration id, dlc, timestamp


@dataclass
class Frame:
    arbitration_id: int
    data: bytes
    timestamp: float


class _BuiltinUdpTx:
    def __init__(self, host, port):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, arb_id, data):
        self.sock.sendto(_HDR.pack(arb_id, len(data), time.time()) + bytes(data), self.addr)

    def close(self):
        self.sock.close()


class _BuiltinUdpRx:
    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))

    def recv(self, timeout=1.0):
        self.sock.settimeout(timeout)
        try:
            pkt, _ = self.sock.recvfrom(128)
        except (socket.timeout, OSError):
            return None
        arb, dlc, ts = _HDR.unpack_from(pkt)
        return Frame(arb, pkt[_HDR.size:_HDR.size + dlc], ts)

    def close(self):
        self.sock.close()


class _PythonCan:
    def __init__(self, backend, channel):
        import can  # optional dependency
        kwargs = {"interface": backend, "channel": channel}
        if backend == "udp_multicast":
            kwargs["receive_own_messages"] = False
        self.can = can
        self.bus = can.Bus(**kwargs)

    def send(self, arb_id, data):
        self.bus.send(self.can.Message(arbitration_id=arb_id, data=bytes(data), is_extended_id=False))

    def recv(self, timeout=1.0):
        m = self.bus.recv(timeout)
        return Frame(m.arbitration_id, bytes(m.data), m.timestamp) if m else None

    def close(self):
        self.bus.shutdown()


class ReadOnlyBus:
    """Receive-only wrapper handed to the AI collector. Deliberately has no send()."""

    def __init__(self, impl):
        self._impl = impl

    def recv(self, timeout=1.0):
        return self._impl.recv(timeout)

    def close(self):
        self._impl.close()


def open_tx():
    if config.CAN_BACKEND == "builtin-udp":
        return _BuiltinUdpTx(config.COLLECTOR_HOST, config.CAN_UDP_PORT)
    return _PythonCan(config.CAN_BACKEND, config.CAN_CHANNEL)


def open_rx_readonly():
    if config.CAN_BACKEND == "builtin-udp":
        return ReadOnlyBus(_BuiltinUdpRx(config.CAN_UDP_PORT))
    return ReadOnlyBus(_PythonCan(config.CAN_BACKEND, config.CAN_CHANNEL))
