import unittest

from meshchat.gps_share import maneuver_text, travel_profile


class DirectionTests(unittest.TestCase):
    def test_profiles_stay_on_known_routers(self) -> None:
        self.assertEqual(travel_profile("foot"), "foot")
        self.assertEqual(travel_profile("driving"), "driving")
        self.assertEqual(travel_profile("something-else"), "driving")

    def test_turn_instructions(self) -> None:
        self.assertEqual(
            maneuver_text({"maneuver": {"type": "turn", "modifier": "left"}, "name": "Main Street"}),
            "Turn left onto Main Street",
        )
        self.assertEqual(
            maneuver_text({"maneuver": {"type": "arrive"}, "name": ""}),
            "You have arrived",
        )
        self.assertEqual(
            maneuver_text({"maneuver": {"type": "depart", "modifier": "north"}, "name": "1st Ave"}),
            "Head north onto 1st Ave",
        )


if __name__ == "__main__":
    unittest.main()
