import unittest
from pathlib import Path
from types import SimpleNamespace

from meshchat.flash import firmware_needs_update, flash_argv, tracker_port_names


class FlashTests(unittest.TestCase):
    def test_only_the_tracker_port_is_eligible(self) -> None:
        ports = [
            SimpleNamespace(vid=0x303A, pid=0x0002, device="COM30"),
            SimpleNamespace(vid=0x10C4, pid=0xEA60, device="COM29"),
            SimpleNamespace(vid=0x303A, pid=0x1001, device="COM8"),
        ]
        self.assertEqual(tracker_port_names(ports), ["COM8"])

    def test_firmware_updates_when_the_tracker_version_differs(self) -> None:
        # Kept for the helper; Update itself always re-flashes when plugged in.
        self.assertTrue(firmware_needs_update("", "1", True))
        self.assertTrue(firmware_needs_update("0", "1", True))
        self.assertFalse(firmware_needs_update("1", "1", True))
        self.assertFalse(firmware_needs_update("", "1", False))

    def test_flash_command_uses_dio_and_the_tracker_layout(self) -> None:
        folder = Path("tests") / "_tmp_fw"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            for name in ("bootloader.bin", "partitions.bin", "boot_app0.bin", "firmware.bin"):
                (folder / name).write_bytes(b"x")
            command = flash_argv("COM8", folder)
            self.assertIn("--flash_mode", command)
            self.assertIn("dio", command)
            self.assertNotIn("qio", command)
            self.assertEqual(command[command.index("0x0") + 1], str(folder / "bootloader.bin"))
            self.assertEqual(command[command.index("0x10000") + 1], str(folder / "firmware.bin"))
            self.assertEqual(command[command.index("--port") + 1], "COM8")
        finally:
            for path in folder.iterdir():
                path.unlink()
            folder.rmdir()


if __name__ == "__main__":
    unittest.main()
