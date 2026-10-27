import unittest

from meshchat.link import open_link, parse_line


class LineTests(unittest.TestCase):
    def test_receive(self) -> None:
        event = parse_line("RX 100001 100002 99 hello there")
        self.assertEqual(event["op"], "rx")
        self.assertEqual(event["sender"], 100001)
        self.assertEqual(event["target"], 100002)
        self.assertEqual(event["id"], 99)
        self.assertEqual(event["text"], "hello there")

    def test_send_result_and_ack(self) -> None:
        sent = parse_line("TX 100002 44 ok")
        self.assertEqual(sent["phone"], 100002)
        self.assertTrue(sent["ok"])
        ack = parse_line("ACK 100002")
        self.assertEqual(ack["phone"], 100002)

    def test_gps_and_nmea(self) -> None:
        fix = parse_line("GPS 47.609700 -122.333100 12.4 8 1")
        self.assertEqual(fix["op"], "gps")
        self.assertEqual(fix["fix"], 1)
        self.assertAlmostEqual(fix["lat"], 47.6097)
        self.assertEqual(fix["sats"], 8)
        searching = parse_line("GPS none 4")
        self.assertEqual(searching["fix"], 0)
        self.assertEqual(searching["sats"], 4)
        sentence = "$GNRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
        nmea = parse_line("NMEA " + sentence)
        self.assertEqual(nmea["sentence"], sentence)

    def test_radio_and_id(self) -> None:
        self.assertTrue(parse_line("RADIO ok")["ok"])
        self.assertFalse(parse_line("RADIO fail -2")["ok"])
        self.assertEqual(parse_line("ID 100001")["phone"], 100001)
        self.assertEqual(parse_line("PONG")["op"], "pong")
        self.assertEqual(parse_line("FW 1")["version"], "1")
        self.assertEqual(parse_line("HEARD 15 bad-len")["op"], "heard")

    def test_open_does_not_reset_the_tracker(self) -> None:
        opened = {}

        class Port:
            def __init__(self) -> None:
                self.dtr = True
                self.rts = True
                self.port = ""
                self.baudrate = 0
                self.timeout = None

            def open(self) -> None:
                opened["dtr"] = self.dtr
                opened["rts"] = self.rts

        import meshchat.link as link

        original = link.serial.Serial
        link.serial.Serial = Port
        try:
            open_link("COM8")
        finally:
            link.serial.Serial = original
        self.assertFalse(opened["dtr"])
        self.assertFalse(opened["rts"])


if __name__ == "__main__":
    unittest.main()
