import unittest

from meshchat.protocol import pack_delivered, pack_message, unpack


class PacketTests(unittest.TestCase):
    def test_message_matches_the_t9_layout(self) -> None:
        packet = pack_message(0x01020304, 0x05060708, 0x0A0B0C0D, b"Hi")
        self.assertEqual(
            packet,
            bytes.fromhex("01020304 05060708 00 0A0B0C0D 0002 4869"),
        )
        parsed = unpack(packet)
        self.assertEqual(parsed["sender"], 0x01020304)
        self.assertEqual(parsed["target"], 0x05060708)
        self.assertEqual(parsed["id"], 0x0A0B0C0D)
        self.assertEqual(parsed["text"], b"Hi")

    def test_delivered_is_nine_bytes(self) -> None:
        packet = pack_delivered(0x01020304, 0x05060708)
        self.assertEqual(packet, bytes.fromhex("01020304 05060708 01"))
        parsed = unpack(packet)
        self.assertEqual(parsed["type"], 1)
        self.assertEqual(parsed["sender"], 0x01020304)

    def test_broadcast_number(self) -> None:
        packet = pack_message(100001, 0xFFFFFFFF, 7, b"all")
        parsed = unpack(packet)
        self.assertEqual(parsed["target"], 4294967295)
        self.assertEqual(parsed["text"], b"all")


if __name__ == "__main__":
    unittest.main()
