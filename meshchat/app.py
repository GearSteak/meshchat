"""Desktop chat window."""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import messagebox, simpledialog

from meshchat.flash import bundled_version, firmware_needs_update, update_tracker
from meshchat.gps_share import GpsShare
from meshchat.link import Tracker, tracker_port
from meshchat.protocol import BROADCAST, MAX_TEXT
from meshchat.store import Store, data_dir
from meshchat.update import check_update, install_root, install_update, local_revision, relaunch

BG = "#e8e8ed"
PANEL = "#f7f7f8"
INK = "#111111"
MUTED = "#8e8e93"
MINE = "#0b84ff"
MINE_INK = "#ffffff"
THEIRS = "#e5e5ea"
THEIR_INK = "#111111"
FIELD = "#ffffff"
ACCENT = "#248a3d"
SELECT = "#d6e8ff"
LINE = "#d1d1d6"
CHAT = "#ffffff"


def running_revision() -> str:
    sha = local_revision(install_root())
    if not sha:
        return "local"
    return sha[:7]


def ui_font(size: int, bold: bool = False) -> tuple:
    family = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
    return (family, size, "bold" if bold else "normal")


def show_incoming(sender: int, target: int, me: int | None) -> bool:
    """Keep every radio packet. Hiding by number made phone traffic disappear when
    My number was set to the other radio's id."""
    return True


def format_phone(number: int) -> str:
    """8-digit hex id, the form the phone asks for when adding a contact."""
    return f"0x{number:08X}"


def shown_name(name: str, phone: int) -> str:
    if phone == BROADCAST:
        return name
    if name == str(phone):
        return format_phone(phone)
    return name


def parse_phone(raw: str) -> int:
    text = raw.strip().replace(" ", "")
    hex_body = text[2:] if text.lower().startswith("0x") else text
    if not hex_body or any(c not in "0123456789abcdefABCDEF" for c in hex_body):
        raise ValueError("Use the hex id, like 0x00028B90.")
    number = int(hex_body, 16) if text.lower().startswith("0x") or any(c in "abcdefABCDEF" for c in hex_body) else int(hex_body)
    if number == 0 or number > BROADCAST:
        raise ValueError("That number does not fit in the radio's id.")
    if number == BROADCAST:
        raise ValueError("That number is the everyone channel.")
    return number


class App:
    def __init__(self, store: Store, tracker: Tracker, gps: GpsShare) -> None:
        self.store = store
        self.tracker = tracker
        self.gps = gps
        self.phones: list[int] = []
        self.current: int | None = None
        self.root = tk.Tk()
        self.root.title(f"Mesh Chat {running_revision()}")
        self.root.geometry("980x700")
        self.root.minsize(760, 520)
        self._thread_width = 0
        self._jobs: queue.Queue = queue.Queue()
        self._contact_rows: dict[int, tk.Frame] = {}
        self._id_fix: int | None = None
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.root.report_callback_exception = self._tk_exception
        self.refresh_contacts(BROADCAST)
        self.show_thread()
        self.tracker.start()
        self.root.after(100, self.poll)

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=FIELD, padx=16, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="Mesh Chat", bg=FIELD, fg=INK, font=ui_font(20, True)).pack(side="left")
        tk.Label(header, text=running_revision(), bg=FIELD, fg=MUTED, font=ui_font(10)).pack(
            side="left", padx=(8, 0)
        )
        self.number_btn = tk.Button(
            header,
            text="Set my number",
            command=self.edit_my_number,
            bg=FIELD,
            fg=MINE,
            activebackground=SELECT,
            activeforeground=MINE,
            relief="flat",
            padx=8,
            pady=4,
            font=ui_font(11, True),
        )
        self.number_btn.pack(side="right")
        self.map_btn = tk.Button(
            header,
            text="Directions",
            command=self.open_map,
            bg=FIELD,
            fg=MINE,
            activebackground=SELECT,
            activeforeground=MINE,
            relief="flat",
            padx=8,
            pady=4,
            font=ui_font(11, True),
        )
        self.map_btn.pack(side="right")
        self.update_btn = tk.Button(
            header,
            text="Update",
            command=self.check_for_update,
            bg=FIELD,
            fg=MINE,
            activebackground=SELECT,
            activeforeground=MINE,
            relief="flat",
            padx=8,
            pady=4,
            font=ui_font(11, True),
        )
        self.update_btn.pack(side="right")
        self.gps_label = tk.Label(header, text="GPS searching", bg=FIELD, fg=MUTED, font=ui_font(10))
        self.gps_label.pack(side="right", padx=8)
        self._paint_number()
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x")

        body = tk.PanedWindow(self.root, orient="horizontal", bg=LINE, sashwidth=1, sashrelief="flat")
        body.pack(fill="both", expand=True)

        side = tk.Frame(body, bg=PANEL)
        body.add(side, minsize=260, width=300)
        tk.Label(side, text="Contacts", bg=PANEL, fg=MUTED, font=ui_font(11, True), anchor="w").pack(
            fill="x", padx=16, pady=(12, 4)
        )
        contact_wrap = tk.Frame(side, bg=PANEL)
        contact_wrap.pack(fill="both", expand=True)
        self.contact_canvas = tk.Canvas(contact_wrap, bg=PANEL, highlightthickness=0, bd=0)
        contact_scroll = tk.Scrollbar(contact_wrap, command=self.contact_canvas.yview)
        self.contact_canvas.configure(yscrollcommand=contact_scroll.set)
        contact_scroll.pack(side="right", fill="y")
        self.contact_canvas.pack(side="left", fill="both", expand=True)
        self.contact_inner = tk.Frame(self.contact_canvas, bg=PANEL)
        self.contact_canvas.create_window((0, 0), window=self.contact_inner, anchor="nw", tags="inner")
        self.contact_inner.bind("<Configure>", self._sync_contact_scroll)
        self.contact_canvas.bind("<Configure>", self._stretch_contacts)
        self._bind_wheel(self.contact_canvas, self.contact_canvas)

        actions = tk.Frame(side, bg=PANEL, padx=12, pady=10)
        actions.pack(fill="x")
        for label, command in (
            ("New chat", self.new_chat),
            ("Rename", self.rename_contact),
            ("Remove", self.remove_contact),
        ):
            tk.Button(
                actions,
                text=label,
                command=command,
                bg=FIELD,
                fg=MINE,
                activebackground=SELECT,
                activeforeground=MINE,
                relief="flat",
                padx=10,
                pady=6,
                font=ui_font(10, True),
            ).pack(side="left", padx=(0, 6))

        chat = tk.Frame(body, bg=CHAT)
        body.add(chat, minsize=460)
        head = tk.Frame(chat, bg=FIELD, padx=16, pady=10)
        head.pack(fill="x")
        self.avatar = tk.Canvas(head, width=42, height=42, bg=FIELD, highlightthickness=0, bd=0)
        self.avatar.pack(side="left")
        names = tk.Frame(head, bg=FIELD)
        names.pack(side="left", padx=10)
        self.title = tk.Label(names, text="Everyone", bg=FIELD, fg=INK, font=ui_font(15, True), anchor="w")
        self.title.pack(anchor="w")
        self.subtitle = tk.Label(names, text="", bg=FIELD, fg=MUTED, font=ui_font(10), anchor="w")
        self.subtitle.pack(anchor="w")
        tk.Frame(chat, bg=LINE, height=1).pack(fill="x")

        thread_wrap = tk.Frame(chat, bg=CHAT)
        self.thread = tk.Canvas(thread_wrap, bg=CHAT, highlightthickness=0, bd=0)
        thread_scroll = tk.Scrollbar(thread_wrap, command=self.thread.yview)
        self.thread.configure(yscrollcommand=thread_scroll.set)
        thread_scroll.pack(side="right", fill="y")
        self.thread.pack(side="left", fill="both", expand=True)
        self.thread.bind("<Configure>", self._thread_resized)
        self._bind_wheel(self.thread, self.thread)

        composer = tk.Frame(chat, bg=FIELD, padx=12, pady=12)
        composer.pack(fill="x", side="bottom")
        tk.Frame(chat, bg=LINE, height=1).pack(fill="x", side="bottom")
        self.entry = tk.Entry(
            composer,
            bg=FIELD,
            fg=MUTED,
            insertbackground=INK,
            relief="solid",
            bd=1,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=MINE,
            font=ui_font(13),
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=8, ipadx=8)
        self._placeholder = "Type a message"
        self._entry_empty = True
        self.entry.insert(0, self._placeholder)
        self.entry.bind("<FocusIn>", self._entry_focus_in)
        self.entry.bind("<FocusOut>", self._entry_focus_out)
        self.entry.bind("<Return>", lambda _e: self.send())
        self.root.bind("<Key>", self._route_typing)
        tk.Button(
            composer,
            text="Send",
            command=self.send,
            bg=MINE,
            fg=MINE_INK,
            activebackground="#0066d6",
            activeforeground=MINE_INK,
            relief="flat",
            padx=18,
            pady=8,
            font=ui_font(12, True),
        ).pack(side="right")
        thread_wrap.pack(fill="both", expand=True)

        self.status = tk.Label(
            self.root,
            text="Looking for the Heltec Tracker V2",
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            padx=12,
            pady=6,
            font=ui_font(9),
        )
        self.status.pack(fill="x")
        self.radio_log = tk.Label(
            self.root,
            text="Radio log: (waiting)",
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            padx=12,
            pady=2,
            font=ui_font(8),
        )
        self.radio_log.pack(fill="x")
        self._radio_lines: list[str] = []

    def _bind_wheel(self, widget: tk.Widget, target: tk.Canvas) -> None:
        def wheel(event: tk.Event) -> None:
            if event.delta:
                target.yview_scroll(int(-event.delta / 120), "units")
            elif event.num == 4:
                target.yview_scroll(-1, "units")
            elif event.num == 5:
                target.yview_scroll(1, "units")

        widget.bind("<MouseWheel>", wheel)
        widget.bind("<Button-4>", wheel)
        widget.bind("<Button-5>", wheel)

    def _sync_contact_scroll(self, _event: tk.Event) -> None:
        self.contact_canvas.configure(scrollregion=self.contact_canvas.bbox("all"))

    def _stretch_contacts(self, event: tk.Event) -> None:
        self.contact_canvas.itemconfigure("inner", width=event.width)

    def _thread_resized(self, event: tk.Event) -> None:
        if abs(event.width - self._thread_width) < 4:
            return
        self._thread_width = event.width
        self.show_thread()

    def _paint_number(self) -> None:
        phone = self.store.phone()
        if phone is None:
            self.number_btn.configure(text="Set my number")
        else:
            self.number_btn.configure(text=f"My number  {format_phone(phone)}")

    def refresh_contacts(self, keep: int | None = None) -> None:
        if keep is None:
            keep = self.current if self.current is not None else BROADCAST
        self.current = keep
        for child in self.contact_inner.winfo_children():
            child.destroy()
        self._contact_rows.clear()
        self.phones = []
        for contact in self.store.contacts():
            self.phones.append(contact.phone)
            self._contact_row(contact.phone, contact.name)
        self._paint_header()

    def _contact_row(self, phone: int, name: str) -> None:
        selected = phone == self.current
        bg = SELECT if selected else PANEL
        row = tk.Frame(self.contact_inner, bg=bg, cursor="hand2")
        row.pack(fill="x")
        self._contact_rows[phone] = row
        label = shown_name(name, phone)
        color = "#34c759" if phone == BROADCAST else avatar_color(label)
        letter = "All" if phone == BROADCAST else (label[-1] if label.startswith("0x") else label[:1].upper())
        bubble = tk.Canvas(row, width=52, height=52, bg=bg, highlightthickness=0, bd=0)
        bubble.pack(side="left", padx=(12, 4), pady=6)
        bubble.create_oval(8, 8, 44, 44, fill=color, outline="")
        bubble.create_text(26, 26, text=letter, fill="white", font=ui_font(12, True))
        words = tk.Frame(row, bg=bg)
        words.pack(side="left", fill="x", expand=True, pady=8)
        tk.Label(words, text=label, bg=bg, fg=INK, font=ui_font(12, True), anchor="w").pack(fill="x")
        detail = "Everyone" if phone == BROADCAST else format_phone(phone)
        tk.Label(words, text=detail, bg=bg, fg=MUTED, font=ui_font(10), anchor="w").pack(fill="x")
        tk.Frame(self.contact_inner, bg=LINE, height=1).pack(fill="x", padx=16)

        def choose(_event: tk.Event, number: int = phone) -> None:
            self.current = number
            self.refresh_contacts(number)
            self.show_thread()

        for widget in (row, bubble, words, *words.winfo_children()):
            widget.bind("<Button-1>", choose)
            self._bind_wheel(widget, self.contact_canvas)

    def selected_phone(self) -> int | None:
        return self.current

    def _paint_header(self) -> None:
        phone = self.current
        self.avatar.delete("all")
        if phone is None:
            self.title.configure(text="Messages")
            self.subtitle.configure(text="")
            return
        name = shown_name(self.store.contact_name(phone), phone)
        color = "#34c759" if phone == BROADCAST else avatar_color(name)
        letter = "All" if phone == BROADCAST else (name[-1] if name.startswith("0x") else name[:1].upper())
        self.avatar.create_oval(1, 1, 41, 41, fill=color, outline="")
        self.avatar.create_text(21, 21, text=letter, fill="white", font=ui_font(13, True))
        self.title.configure(text=name)
        if phone == BROADCAST:
            self.subtitle.configure(text="Heard by every radio")
        else:
            self.subtitle.configure(text=format_phone(phone))

    def show_thread(self) -> None:
        canvas = self.thread
        canvas.delete("all")
        self._paint_header()
        phone = self.current
        width = canvas.winfo_width()
        if width < 120:
            width = max(self._thread_width, 640)
        if phone is None:
            canvas.configure(scrollregion=(0, 0, width, 200))
            return
        messages = self.store.messages(phone)
        if not messages:
            if phone == BROADCAST:
                empty = "No messages yet.\nNew chat opens one radio. Typing here reaches every radio."
            else:
                empty = f"No messages with {shown_name(self.store.contact_name(phone), phone)} yet.\nType below and press Send."
            canvas.create_text(
                width / 2,
                90,
                text=empty,
                fill=MUTED,
                font=ui_font(12),
                justify="center",
            )
            canvas.configure(scrollregion=(0, 0, width, 200))
            return
        body_font = tkfont.Font(family=ui_font(12)[0], size=12)
        meta_font = tkfont.Font(family=ui_font(9)[0], size=9)
        name_font = tkfont.Font(family=ui_font(9, True)[0], size=9, weight="bold")
        me = self.store.phone()
        max_text = max(80, min(420, int(width * 0.62)))
        y = 18
        for message in messages:
            mine = me is not None and message.sender == me
            who = ""
            if phone == BROADCAST and not mine:
                who = shown_name(self.store.contact_name(message.sender), message.sender)
            lines = wrap_lines(message.body, body_font, max_text)
            line_h = body_font.metrics("linespace")
            text_w = max(body_font.measure(line) for line in lines)
            text_h = line_h * len(lines)
            bubble_w = text_w + 28
            bubble_h = text_h + 18
            if mine:
                x2 = width - 16
                x1 = x2 - bubble_w
                fill = MINE
                ink = MINE_INK
            else:
                x1 = 16
                x2 = x1 + bubble_w
                fill = THEIRS
                ink = THEIR_INK
            if who:
                canvas.create_text(x1 + 12, y, anchor="nw", text=who, fill=MUTED, font=name_font)
                y += 16
            round_rect(canvas, x1, y, x2, y + bubble_h, 16, fill=fill)
            text_y = y + 9
            for line in lines:
                canvas.create_text(x1 + 14, text_y, anchor="nw", text=line, fill=ink, font=body_font)
                text_y += line_h
            y += bubble_h + 3
            stamp = time_label(message.ts)
            meta = stamp if message.status == "received" else f"{stamp}  {status_label(message.status)}"
            meta_fill = "#c0392b" if message.status == "failed" else MUTED
            if mine:
                canvas.create_text(x2 - 6, y, anchor="ne", text=meta, fill=meta_fill, font=meta_font)
            else:
                canvas.create_text(x1 + 6, y, anchor="nw", text=meta, fill=meta_fill, font=meta_font)
            y += 20
        canvas.configure(scrollregion=(0, 0, width, y + 8))
        canvas.yview_moveto(1.0)

    def edit_my_number(self) -> None:
        current = self.store.phone()
        raw = simpledialog.askstring(
            "My number",
            "Hex id for THIS Pi only — not the phone's id.\n"
            "Type the phone's id into New chat, and type this Pi id into the phone.",
            initialvalue="" if current is None else format_phone(current),
            parent=self.root,
        )
        if raw is None:
            return
        try:
            phone = parse_phone(raw)
        except ValueError as exc:
            messagebox.showerror("My number", str(exc), parent=self.root)
            return
        self.store.set_phone(phone)
        self._id_fix = None
        self.tracker.set_phone(phone)
        self._paint_number()
        self.show_thread()

    def new_chat(self) -> None:
        raw = simpledialog.askstring(
            "New chat",
            "Hex id from the other radio, like 0x00028B90.",
            parent=self.root,
        )
        if raw is None:
            return
        try:
            phone = parse_phone(raw)
        except ValueError as exc:
            messagebox.showerror("New chat", str(exc), parent=self.root)
            return
        if phone == self.store.phone():
            messagebox.showerror("New chat", "That is this Pi's own hex id.", parent=self.root)
            return
        self.open_chat(phone)

    def open_chat(self, phone: int) -> None:
        self.store.ensure_contact(phone)
        self.refresh_contacts(phone)
        self.show_thread()
        self.entry.focus_set()
        self.entry.icursor("end")

    def rename_contact(self) -> None:
        phone = self.selected_phone()
        if phone is None or phone == BROADCAST:
            return
        name = simpledialog.askstring(
            "Rename",
            "Name",
            initialvalue=self.store.contact_name(phone),
            parent=self.root,
        )
        if name is None:
            return
        self.store.rename(phone, name)
        self.refresh_contacts(phone)
        self.show_thread()

    def remove_contact(self) -> None:
        phone = self.selected_phone()
        if phone is None or phone == BROADCAST:
            return
        if not messagebox.askyesno(
            "Remove number",
            f"Remove {self.store.contact_name(phone)} and the chat with them?",
            parent=self.root,
        ):
            return
        self.store.delete_contact(phone)
        self.refresh_contacts(BROADCAST)
        self.show_thread()

    def _entry_focus_in(self, _event: tk.Event) -> None:
        if self._entry_empty:
            self.entry.delete(0, "end")
            self.entry.configure(fg=INK)
            self._entry_empty = False

    def _entry_focus_out(self, _event: tk.Event) -> None:
        if self.entry.get().strip():
            return
        self._entry_empty = True
        self.entry.delete(0, "end")
        self.entry.insert(0, self._placeholder)
        self.entry.configure(fg=MUTED)

    def _route_typing(self, event: tk.Event) -> None:
        if isinstance(event.widget, (tk.Entry, tk.Button, tk.Text)):
            return None
        if not event.char or not event.char.isprintable():
            return None
        self._entry_focus_in(event)
        self.entry.focus_set()
        self.entry.insert("end", event.char)
        return "break"

    def message_text(self) -> str:
        if self._entry_empty:
            return ""
        return self.entry.get().strip()

    def _tk_exception(self, exc, value, traceback_obj) -> None:
        import traceback
        from pathlib import Path

        detail = "".join(traceback.format_exception(exc, value, traceback_obj))
        try:
            Path(data_dir(), "crash.log").write_text(detail, encoding="utf-8")
        except OSError:
            pass
        try:
            self.set_status("Mesh Chat hit an error. The window stayed open.")
        except Exception:
            pass

    def send(self) -> None:
        try:
            text = self.message_text()
            if not text:
                self.entry.focus_set()
                return
            if len(text.encode("utf-8")) > MAX_TEXT:
                messagebox.showerror("Message", "That message is too long for the radio.", parent=self.root)
                return
            me = self.store.phone()
            if me is None:
                self.edit_my_number()
                me = self.store.phone()
                if me is None:
                    return
            target = self.selected_phone()
            if target is None:
                return
            row_id = self.store.add_message(target, me, target, text, "sending")
            self.entry.delete(0, "end")
            self._entry_empty = False
            self.entry.configure(fg=INK)
            self.entry.focus_set()
            if not self.tracker.send(target, text):
                self.store.mark_tx(target, 0, False)
                self.set_status("The Tracker is not connected. The message stayed here as not sent.")
            else:
                # The other radio already has these. Waiting on the Tracker to
                # agree left the bubble on "sending" forever.
                self.store.mark_handed_off(row_id)
            self.show_thread()
        except Exception as exc:
            try:
                self.set_status(f"Send failed: {exc}")
            except Exception:
                pass

    def poll(self) -> None:
        while True:
            try:
                event = self.tracker.events.get_nowait()
            except queue.Empty:
                break
            try:
                self.on_event(event)
            except Exception:
                self.set_status("Mesh Chat skipped a bad radio update.")
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            try:
                job()
            except Exception:
                self.set_status("Mesh Chat skipped a bad radio update.")
        if self.root.winfo_exists():
            self.root.after(100, self.poll)

    def on_event(self, event: dict) -> None:
        op = event.get("op")
        if op == "up":
            self.set_status(f"Tracker connected on {event.get('port', '')}")
        elif op == "down":
            self.set_status(str(event.get("detail") or "Tracker disconnected"))
            if self.store.fail_sending():
                self.show_thread()
        elif op == "pong":
            self.set_status(f"Tracker connected on {self.tracker.port_name}")
        elif op == "fw":
            self.tracker.firmware = str(event.get("version") or "")
            self.note_radio(f"FW {self.tracker.firmware}")
            if self.status.cget("text").startswith("Radio is listening"):
                self.set_status(self._listening_status())
        elif op == "radio":
            self.note_radio(f"RADIO {event.get('detail')}")
            if event.get("ok"):
                self.set_status(self._listening_status())
            else:
                self.set_status(f"Radio failed to start ({event.get('detail')})")
        elif op == "id":
            phone = int(event["phone"])
            wanted = self.tracker.wanted_phone
            if wanted and phone != wanted:
                if self._id_fix != wanted:
                    self._id_fix = wanted
                    self.tracker.set_phone(wanted)
                    self.set_status(f"Telling the Tracker this Pi's id is {format_phone(wanted)}")
                return
            self._id_fix = None
            if phone and self.store.phone() != phone:
                self.store.set_phone(phone)
                self.tracker.wanted_phone = phone
                self._paint_number()
        elif op == "rx":
            self.note_radio(
                f"RX {event.get('sender')} -> {event.get('target')}: {event.get('text')}"
            )
            self.on_rx(event)
        elif op == "heard":
            detail = str(event.get("detail") or "packet")
            self.note_radio(f"HEARD {detail}")
            self.set_status(f"Tracker heard a radio packet ({detail})")
        elif op == "irq":
            detail = str(event.get("detail") or "")
            self.note_radio(f"IRQ {detail}")
            self.set_status(f"Radio status {detail}")
        elif op == "log":
            detail = str(event.get("detail") or "")
            if detail.startswith(("RX", "HEARD", "IRQ", "RADIO", "FW")):
                self.note_radio(detail)
                self.set_status(f"Radio: {detail[:100]}")
        elif op == "tx":
            self.store.mark_tx(int(event["phone"]), int(event["id"]), bool(event.get("ok")))
            if self.current == int(event["phone"]):
                self.show_thread()
        elif op == "ack":
            peer = int(event["phone"])
            self.store.mark_delivered(peer)
            if self.current == peer:
                self.show_thread()
        elif op == "gps":
            self.on_gps(event)
        elif op == "nmea":
            self.gps.send_nmea(str(event.get("sentence") or ""))
        elif op == "err":
            detail = str(event.get("detail") or "Radio error")
            if detail in {"nophone", "send", "empty", "long", "unknown"} and self.store.fail_sending():
                self.show_thread()
            if detail == "nophone":
                detail = "Set your number, then send again."
            elif detail == "send":
                detail = "The Tracker could not send that message."
            self.set_status(detail)

    def on_rx(self, event: dict) -> None:
        sender = int(event["sender"])
        target = int(event["target"])
        text = str(event.get("text") or "")
        peer = BROADCAST if target == BROADCAST else sender
        self.store.ensure_contact(sender)
        if peer != BROADCAST:
            self.store.ensure_contact(peer)
        self.store.add_message(peer, sender, target, text, "received", int(event["id"]))
        # Always jump to the thread that owns this packet so a direct message is
        # not sitting unread while Everyone is still on screen.
        self.refresh_contacts(peer)
        self.show_thread()
        where = "Everyone" if peer == BROADCAST else format_phone(sender)
        self.set_status(f"Message from {format_phone(sender)} → {where}")

    def on_gps(self, event: dict) -> None:
        fix = int(event.get("fix") or 0)
        sats = int(event.get("sats") or 0)
        if fix:
            lat = float(event["lat"])
            lon = float(event["lon"])
            alt = float(event["alt"])
            self.gps.update_fix(fix, sats, lat, lon, alt)
            self.gps_label.configure(text=f"{lat:.5f}, {lon:.5f}", fg=ACCENT)
        else:
            self.gps.update_fix(0, sats)
            looking = f"GPS searching ({sats})" if sats else "GPS searching"
            self.gps_label.configure(text=looking, fg=MUTED)

    def open_map(self) -> None:
        if not self.gps.map_port:
            messagebox.showerror("Directions", "The map did not start.", parent=self.root)
            return
        webbrowser.open(f"http://127.0.0.1:{self.gps.map_port}/")

    def check_for_update(self) -> None:
        self.update_btn.configure(state="disabled", text="Checking")
        threading.Thread(target=self._look_for_update, daemon=True).start()

    def _look_for_update(self) -> None:
        try:
            status = check_update(install_root())
        except Exception as exc:
            self._jobs.put(lambda detail=str(exc): self._update_failed(detail))
            return
        self._jobs.put(lambda: self._offer_update(status))

    def _restore_update_button(self) -> None:
        self.update_btn.configure(state="normal", text="Update")

    def _update_failed(self, detail: str) -> None:
        self._restore_update_button()
        messagebox.showerror("Update", detail or "Could not reach GitHub.", parent=self.root)

    def _offer_update(self, status) -> None:
        plugged = tracker_port() is not None
        firmware = firmware_needs_update(self.tracker.firmware, bundled_version(install_root()), plugged)
        refresh_app = status.available
        flash = firmware or (refresh_app and plugged)
        if not refresh_app and not flash:
            self._restore_update_button()
            if plugged:
                text = "Mesh Chat and the Tracker are up to date."
            else:
                text = "Mesh Chat is up to date. Plug the Tracker in and press Update to refresh its firmware."
            messagebox.showinfo("Update", text, parent=self.root)
            return
        if refresh_app and flash:
            question = "Install the latest Mesh Chat and update the Tracker?"
        elif flash:
            question = (
                "Mesh Chat is already the latest copy. "
                "Update the Tracker firmware? The Tracker will restart."
            )
        else:
            question = (
                "A newer Mesh Chat is on GitHub. Install it and restart? "
                "The Tracker is not plugged in, so its firmware will not be updated."
            )
        if not messagebox.askyesno("Update", question, parent=self.root):
            self._restore_update_button()
            return
        self.update_btn.configure(text="Downloading")
        threading.Thread(
            target=self._install_update,
            args=(status.remote if refresh_app else "", flash),
            daemon=True,
        ).start()

    def _install_status(self, text: str) -> None:
        self._jobs.put(lambda: self.update_btn.configure(text=text))

    def _install_update(self, sha: str, flash: bool) -> None:
        self.tracker.stop()
        try:
            if sha:
                install_update(install_root(), sha, on_status=self._install_status)
            note = ""
            if flash:
                self._install_status("Updating Tracker")
                note = update_tracker(install_root())
        except Exception as exc:
            self._jobs.put(lambda detail=str(exc): self._update_failed(detail))
            return
        self._jobs.put(lambda note=note, updated=bool(sha): self._finish_update(note, updated))

    def _finish_update(self, note: str, app_updated: bool) -> None:
        if app_updated:
            lead = "Mesh Chat is updated and will restart."
        else:
            lead = "Mesh Chat was already the latest copy."
        messagebox.showinfo("Update", " ".join(part for part in (lead, note) if part), parent=self.root)
        self._restart_for_update()

    def _restart_for_update(self) -> None:
        self.tracker.stop()
        self.gps.stop()
        self.store.close()
        relaunch()

    def _listening_status(self) -> str:
        text = f"Radio is listening  ·  {self.tracker.port_name}"
        if self.tracker.firmware:
            text += f"  ·  firmware {self.tracker.firmware}"
        return text

    def note_radio(self, line: str) -> None:
        text = " ".join(str(line).split())
        if not text:
            return
        self._radio_lines.append(text[:120])
        self._radio_lines = self._radio_lines[-3:]
        try:
            self.radio_log.configure(text="Radio log: " + " | ".join(self._radio_lines), fg=INK)
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        good = text.startswith("Tracker connected") or text.startswith("Radio is listening")
        self.status.configure(text=text, fg=ACCENT if good else MUTED)

    def close(self) -> None:
        self.tracker.stop()
        self.gps.stop()
        self.store.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def avatar_color(name: str) -> str:
    palette = ("#0b84ff", "#ff9f0a", "#ff375f", "#5e5ce6", "#30b0c7", "#ac8e68", "#32ade6")
    return palette[sum(ord(char) for char in name) % len(palette)]


def wrap_lines(text: str, font: tkfont.Font, width: int) -> list[str]:
    width = max(width, 40)
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            while font.measure(word) > width and len(word) > 1:
                cut = 1
                while cut < len(word) and font.measure(word[: cut + 1]) <= width:
                    cut += 1
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:cut])
                word = word[cut:]
            trial = word if not current else f"{current} {word}"
            if not current or font.measure(trial) <= width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines or [""]


def round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, radius: float, fill: str) -> None:
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    canvas.create_polygon(
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
        smooth=True,
        fill=fill,
        outline=fill,
    )


def time_label(ts: float) -> str:
    import time

    return time.strftime("%H:%M", time.localtime(ts))


def status_label(status: str) -> str:
    if status == "sending":
        return "sending"
    if status == "sent":
        return "sent"
    if status == "delivered":
        return "delivered"
    if status == "failed":
        return "not sent"
    return status


def _startup_log(text: str) -> str:
    path = os.path.join(data_dir(), "startup.log")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        path = os.path.join(os.path.expanduser("~"), "meshchat-startup.log")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            return ""
    return path


def main() -> None:
    import traceback

    try:
        path = os.path.join(data_dir(), "meshchat.sqlite")
        store = Store(path)
        port = os.environ.get("MESHCHAT_PORT") or None
        tracker = Tracker(phone=store.phone() or 0, port=port)
        gps = GpsShare()
        gps.start()
        app = App(store, tracker, gps)
        if os.environ.get("MESHCHAT_SMOKE") == "1":
            app.root.update()
            app.close()
            return
        app.run()
    except Exception as exc:
        detail = traceback.format_exc()
        log_path = _startup_log(detail)
        try:
            root = tk.Tk()
            root.withdraw()
            where = f"\n\nDetails saved to:\n{log_path}" if log_path else ""
            messagebox.showerror("Mesh Chat", f"Could not start.\n\n{exc}{where}")
            root.destroy()
        except Exception:
            if log_path:
                sys.stderr.write(f"Mesh Chat failed to start. See {log_path}\n")
            else:
                sys.stderr.write(detail)
        raise SystemExit(1) from exc
