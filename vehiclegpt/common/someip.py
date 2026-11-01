"""SOME/IP (AUTOSAR) message header codec + the ADAS state-change event used in this project.

Header layout (16 bytes, big-endian):
  Message ID  = Service ID (16) | Method/Event ID (16)
  Length      = bytes following this field (8 header bytes + payload)
  Request ID  = Client ID (16) | Session ID (16)
  Protocol Version (8) = 0x01, Interface Version (8), Message Type (8), Return Code (8)
"""
import struct

HDR = struct.Struct(">HHIHHBBBB")
PROTOCOL_VERSION = 0x01
MSG_NOTIFICATION = 0x02

ADAS_SERVICE_ID = 0x4ADA
ADAS_STATE_EVENT_ID = 0x8001
ADAS_PAYLOAD = struct.Struct(">BBHf")  # engaged, reason code, reserved, speed km/h

ADAS_REASONS = {
    0: "ENGAGED",
    1: "DRIVER_OVERRIDE_STEER",
    2: "LANE_MARKINGS_LOST",
    3: "CAMERA_BLOCKED_GLARE",
    4: "DRIVER_HANDS_OFF_TIMEOUT",
    5: "SHARP_CURVE_LIMIT",
    6: "EXIT_ODD",
    7: "DRIVER_BRAKE_OVERRIDE",
}
REASON_CODES = {v: k for k, v in ADAS_REASONS.items()}


def pack(service_id, event_id, payload: bytes, session_id=1, client_id=0, interface_version=1):
    return HDR.pack(service_id, event_id, 8 + len(payload), client_id, session_id & 0xFFFF,
                    PROTOCOL_VERSION, interface_version, MSG_NOTIFICATION, 0) + payload


def unpack(packet: bytes):
    sid, eid, length, client, session, proto, iface, mtype, rc = HDR.unpack_from(packet)
    if proto != PROTOCOL_VERSION:
        raise ValueError("Not a SOME/IP packet")
    payload = packet[HDR.size:HDR.size + length - 8]
    return dict(service_id=sid, event_id=eid, session_id=session, message_type=mtype,
                return_code=rc, payload=payload)


def pack_adas_event(engaged: bool, reason: str, speed_kmh: float, session_id=1):
    body = ADAS_PAYLOAD.pack(1 if engaged else 0, REASON_CODES.get(reason, 0), 0, float(speed_kmh))
    return pack(ADAS_SERVICE_ID, ADAS_STATE_EVENT_ID, body, session_id)


def unpack_adas_event(payload: bytes):
    engaged, code, _, speed = ADAS_PAYLOAD.unpack_from(payload)
    return dict(engaged=bool(engaged), reason=ADAS_REASONS.get(code, f"UNKNOWN_{code}"), speed_kmh=speed)
