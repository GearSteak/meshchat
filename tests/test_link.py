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
        broadcast = parse_line("RX 166800 4294967295 3 hi everyone")
        self.assertEqual(broadcast["op"], "rx")
        self.assertEqual(broadcast["target"], 0xFFFFFFFF)
        self.assertEqual(broadcast["text"], "hi everyone")
        empty = parse_line("RX 166800 100001 4")
        self.assertEqual(empty["op"], "rx")
        self.assertEqual(empty["text"], "")
        broken = parse_line("RX not-a-number 1 2 x")
        self.assertEqual(broken["op"], "log")

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
        off = parse_line("GPS off")
        self.assertEqual(off["fix"], 0)
        self.assertEqual(off["sats"], 0)
        usb = parse_line("RX 1 4294967295 1 meshchat-usb-ok")
        self.assertEqual(usb["op"], "rx")
        self.assertEqual(usb["text"], "meshchat-usb-ok")
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
        scan = parse_line("SCAN irq=0 dio=0 pa=0")
        self.assertEqual(scan["op"], "scan")
        self.assertEqual(scan["raw"], "SCAN irq=0 dio=0 pa=0")
        rx = parse_line("RX 166800 4294967295 3 hi")
        self.assertEqual(rx["raw"], "RX 166800 4294967295 3 hi")

    def test_open_does_not_reset_the_tracker(self) -> None:
        opened = {}

        class Port:
            def __init__(self) -> None:
                self.dtr = True
                self.rts = True
                self.port = ""
                self.baudrate = 0
                self.timeout = None
                self.write_timeout = None
                self.dsrdtr = True
                self.rtscts = True
                self.xonxoff = True

            def open(self) -> None:
                opened["dtr"] = self.dtr
                opened["rts"] = self.rts
                opened["write_timeout"] = self.write_timeout

            def reset_input_buffer(self) -> None:
                return None

            def reset_output_buffer(self) -> None:
                return None

        import meshchat.link as link

        original = link.serial.Serial
        link.serial.Serial = Port
        try:
            open_link("COM8")
        finally:
            link.serial.Serial = original
        self.assertFalse(opened["dtr"])
        self.assertFalse(opened["rts"])
        self.assertEqual(opened["write_timeout"], 2)

    def test_write_survives_a_usb_io_error(self) -> None:
        import meshchat.link as link

        class Port:
            is_open = True

            def write(self, _data):
                raise OSError(5, "Input/output error")

            def flush(self):
                return None

            def close(self):
                self.is_open = False

        tracker = link.Tracker()
        tracker._ser = Port()
        self.assertFalse(tracker.write("PING"))
        self.assertIsNone(tracker._ser)


if __name__ == "__main__":
    unittest.main()
