"""On-air packet layout used by the T9 radios.

A message is 15 header bytes plus the text, all big-endian:

    sender u32 | target u32 | type u8 | message id u32 | length u16 | text

Type 0 is a message. Type 1 is a delivery receipt and stops after the type
byte (9 bytes total). The phone number on the About screen is the device id.
Broadcast is 4294967295.
"""

from __future__ import annotations

import struct

BROADCAST = 0xFFFFFFFF
TYPE_MESSAGE = 0
TYPE_DELIVERED = 1
HEADER = 15
MAX_TEXT = 200


def pack_message(sender: int, target: int, message_id: int, text: bytes) -> bytes:
    if len(text) > MAX_TEXT:
        raise ValueError("text is too long")
    return struct.pack(
        ">IIBIH",
        sender & BROADCAST,
        target & BROADCAST,
        TYPE_MESSAGE,
        message_id & BROADCAST,
        len(text),
    ) + text


def pack_delivered(sender: int, target: int) -> bytes:
    return struct.pack(">IIB", sender & BROADCAST, target & BROADCAST, TYPE_DELIVERED)


def unpack(packet: bytes) -> dict:
    if len(packet) < 9:
        raise ValueError("packet is too short")
    sender, target, kind = struct.unpack(">IIB", packet[:9])
    if kind == TYPE_DELIVERED:
        return {"type": TYPE_DELIVERED, "sender": sender, "target": target}
    if kind != TYPE_MESSAGE or len(packet) < HEADER:
        raise ValueError("not a message")
    message_id, length = struct.unpack(">IH", packet[9:15])
    if HEADER + length > len(packet):
        raise ValueError("length runs past the packet")
    return {
        "type": TYPE_MESSAGE,
        "sender": sender,
        "target": target,
        "id": message_id,
        "text": packet[HEADER : HEADER + length],
    }
