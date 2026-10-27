"""Saved numbers and chat history."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass

from meshchat.protocol import BROADCAST

EVERYONE = "Everyone"


@dataclass
class Contact:
    phone: int
    name: str
    pinned: bool


@dataclass
class Message:
    id: int
    peer: int
    sender: int
    target: int
    body: str
    msg_id: int | None
    status: str
    ts: float

    @property
    def outgoing(self) -> bool:
        return self.status != "received"


def data_dir() -> str:
    override = os.environ.get("MESHCHAT_DATA")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    path = os.path.join(base, "meshchat")
    os.makedirs(path, exist_ok=True)
    return path


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        try:
            self.db = sqlite3.connect(path, timeout=15)
        except sqlite3.Error as exc:
            raise RuntimeError(
                "Could not open the Mesh Chat database. "
                "Another Mesh Chat may still be running — close it or reboot the Pi."
            ) from exc
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self._create()

    def close(self) -> None:
        self.db.close()

    def _create(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                phone INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                peer INTEGER NOT NULL,
                sender INTEGER NOT NULL,
                target INTEGER NOT NULL,
                body TEXT NOT NULL,
                msg_id INTEGER,
                status TEXT NOT NULL,
                ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.db.execute(
            "INSERT OR IGNORE INTO contacts (phone, name, pinned) VALUES (?, ?, 1)",
            (BROADCAST, EVERYONE),
        )
        self.db.commit()

    def phone(self) -> int | None:
        row = self.db.execute("SELECT value FROM settings WHERE key = 'phone'").fetchone()
        if row is None or not str(row["value"]).isdigit():
            return None
        return int(row["value"])

    def set_phone(self, phone: int) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value) VALUES ('phone', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(phone),),
        )
        self.db.commit()

    def contacts(self) -> list[Contact]:
        rows = self.db.execute(
            "SELECT phone, name, pinned FROM contacts ORDER BY pinned DESC, name COLLATE NOCASE"
        ).fetchall()
        return [Contact(int(r["phone"]), r["name"], bool(r["pinned"])) for r in rows]

    def contact_name(self, phone: int) -> str:
        if phone == BROADCAST:
            return EVERYONE
        row = self.db.execute("SELECT name FROM contacts WHERE phone = ?", (phone,)).fetchone()
        if row is None:
            return str(phone)
        return row["name"]

    def save_contact(self, phone: int, name: str) -> None:
        clean = " ".join(name.split()) or str(phone)
        self.db.execute(
            "INSERT INTO contacts (phone, name, pinned) VALUES (?, ?, 0) "
            "ON CONFLICT(phone) DO UPDATE SET name = excluded.name",
            (phone, clean),
        )
        self.db.commit()

    def ensure_contact(self, phone: int) -> None:
        if phone in (0, BROADCAST):
            return
        self.db.execute(
            "INSERT OR IGNORE INTO contacts (phone, name, pinned) VALUES (?, ?, 0)",
            (phone, str(phone)),
        )
        self.db.commit()

    def rename(self, phone: int, name: str) -> None:
        if phone == BROADCAST:
            return
        self.save_contact(phone, name)

    def delete_contact(self, phone: int) -> None:
        if phone == BROADCAST:
            return
        self.db.execute("DELETE FROM contacts WHERE phone = ? AND pinned = 0", (phone,))
        self.db.execute("DELETE FROM messages WHERE peer = ?", (phone,))
        self.db.commit()

    def add_message(
        self,
        peer: int,
        sender: int,
        target: int,
        body: str,
        status: str,
        msg_id: int | None = None,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO messages (peer, sender, target, body, msg_id, status, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (peer, sender, target, body, msg_id, status, time.time()),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def messages(self, peer: int) -> list[Message]:
        rows = self.db.execute(
            "SELECT id, peer, sender, target, body, msg_id, status, ts "
            "FROM messages WHERE peer = ? ORDER BY id",
            (peer,),
        ).fetchall()
        return [
            Message(
                int(r["id"]),
                int(r["peer"]),
                int(r["sender"]),
                int(r["target"]),
                r["body"],
                None if r["msg_id"] is None else int(r["msg_id"]),
                r["status"],
                float(r["ts"]),
            )
            for r in rows
        ]

    def fail_sending(self, older_than: float | None = None) -> int:
        if older_than is None:
            cur = self.db.execute("UPDATE messages SET status = 'failed' WHERE status = 'sending'")
        else:
            cur = self.db.execute(
                "UPDATE messages SET status = 'failed' WHERE status = 'sending' AND ts < ?",
                (time.time() - older_than,),
            )
        self.db.commit()
        return int(cur.rowcount)

    def mark_tx(self, peer: int, msg_id: int, ok: bool) -> None:
        row = self.db.execute(
            "SELECT id FROM messages WHERE peer = ? AND status = 'sending' "
            "ORDER BY id LIMIT 1",
            (peer,),
        ).fetchone()
        if row is None and ok:
            row = self.db.execute(
                "SELECT id FROM messages WHERE peer = ? AND status = 'failed' "
                "ORDER BY id DESC LIMIT 1",
                (peer,),
            ).fetchone()
        if row is None:
            return
        self.db.execute(
            "UPDATE messages SET status = ?, msg_id = ? WHERE id = ?",
            ("sent" if ok else "failed", msg_id, row["id"]),
        )
        self.db.commit()

    def mark_handed_off(self, row_id: int) -> None:
        self.db.execute(
            "UPDATE messages SET status = 'sent' WHERE id = ? AND status = 'sending'",
            (row_id,),
        )
        self.db.commit()

    def mark_delivered(self, peer: int) -> None:
        row = self.db.execute(
            "SELECT id FROM messages WHERE peer = ? AND status = 'sent' "
            "ORDER BY id DESC LIMIT 1",
            (peer,),
        ).fetchone()
        if row is None:
            return
        self.db.execute("UPDATE messages SET status = 'delivered' WHERE id = ?", (row["id"],))
        self.db.commit()
