import os
import tempfile
import unittest

from meshchat.app import parse_phone
from meshchat.protocol import BROADCAST
from meshchat.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.path = handle.name
        handle.close()
        self.store = Store(self.path)

    def tearDown(self) -> None:
        self.store.close()
        os.remove(self.path)

    def test_everyone_is_saved(self) -> None:
        contacts = self.store.contacts()
        self.assertEqual(contacts[0].phone, BROADCAST)
        self.assertEqual(contacts[0].name, "Everyone")

    def test_numbers_and_messages_stick(self) -> None:
        self.store.set_phone(100001)
        self.store.save_contact(100002, "Ada")
        self.store.add_message(100002, 100001, 100002, "hi", "sending")
        self.store.mark_tx(100002, 50, True)
        self.store.mark_delivered(100002)
        messages = self.store.messages(100002)
        self.assertEqual(messages[0].body, "hi")
        self.assertEqual(messages[0].status, "delivered")
        self.assertEqual(messages[0].msg_id, 50)
        self.assertEqual(self.store.phone(), 100001)

    def test_unknown_sender_is_kept(self) -> None:
        self.store.ensure_contact(100003)
        self.assertEqual(self.store.contact_name(100003), "100003")

    def test_phone_parser(self) -> None:
        self.assertEqual(parse_phone("100001"), 100001)
        with self.assertRaises(ValueError):
            parse_phone("12ab")
        with self.assertRaises(ValueError):
            parse_phone("4294967295")


if __name__ == "__main__":
    unittest.main()
