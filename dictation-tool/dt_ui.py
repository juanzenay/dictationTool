"""dictation-tool UI: Dictation Bar, the Hub window and its pages, Scratchpad, and the dictation flow."""
from __future__ import annotations

import datetime as dt
import os
import queue
import threading
import time
import tkinter as tk
import urllib.parse
import webbrowser
import winsound
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import pyperclip

import dt_core as c
from dt_bar import FlowBar
from dt_core import APP, log

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    pystray = None

# Look and feel. Swap these to restyle the whole app.
THEME = {
    "accent": "#2F6FED", "accent_hover": "#2559C4", "danger": "#E5484D", "warn": "#F5A524",
    "bar": "#16181D", "bar_border": "#5B606B", "font": "Segoe UI",
}
F = THEME["font"]
MUTED = ("gray40", "gray65")
CARD = ("gray92", "gray17")
SCRIPT = str(Path(__file__).with_name("dictation_tool.pyw"))


def round_rect(cv, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0,
           y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


# -------------------------------------------------------------- scratchpad
class Scratchpad(ctk.CTkToplevel):
    MAX_TABS = 5

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Scratchpad")
        try:
            self.iconbitmap(app.icon)
        except Exception:
            pass
        self.geometry("760x520")
        self.tabs: list[dict] = []
        self.active = None
        self.sidebar_on = True
        self._job = None
        self._loading = False
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=(8, 0))
        app.btn(bar, "☰", self.toggle_sidebar, 34, primary=False).pack(side="left")
        self.tabbar = ctk.CTkFrame(bar, fg_color="transparent")
        self.tabbar.pack(side="left", padx=6)
        app.btn(bar, "+", self.new_tab, 34).pack(side="left")
        app.btn(bar, "Delete", self.delete_note, 64, primary=False).pack(side="right")
        self.pin_btn = app.btn(bar, "Pin", self.toggle_pin, 64, primary=False)
        self.pin_btn.pack(side="right", padx=4)
        app.btn(bar, "History", self.show_history, 70, primary=False).pack(side="right")
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=8, pady=8)
        self.side = ctk.CTkFrame(body, width=210)
        self.side.pack(side="left", fill="y", padx=(0, 8))
        self.side.pack_propagate(False)
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *a: self.render_list())
        ctk.CTkEntry(self.side, textvariable=self.search, placeholder_text="Search").pack(fill="x", padx=6, pady=6)
        self.listbox = ctk.CTkScrollableFrame(self.side, fg_color="transparent")
        self.listbox.pack(fill="both", expand=True)
        edit = ctk.CTkFrame(body, fg_color="transparent")
        edit.pack(side="left", fill="both", expand=True)
        self.title_var = tk.StringVar()
        self.title_var.trace_add("write", self._title_changed)
        ctk.CTkEntry(edit, textvariable=self.title_var, font=(F, 18, "bold"), border_width=0,
                     fg_color="transparent").pack(fill="x")
        self.text = ctk.CTkTextbox(edit, wrap="word", font=(F, 14))
        self.text.pack(fill="both", expand=True, pady=(4, 0))
        self.text.bind("<KeyRelease>", lambda e: self._schedule())
        for seq, fn in (("<Control-t>", self.new_tab), ("<Control-w>", self.close_tab),
                        ("<Control-e>", self.toggle_sidebar), ("<Control-W>", self.close_window)):
            self.bind(seq, lambda e, fn=fn: (fn(), "break")[1])
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.withdraw()

    # -- notes
    def _new_note(self) -> dict:
        now = time.time()
        return {"id": f"{int(now * 1000)}", "title": "", "auto": True, "text": "", "pinned": False,
                "created": now, "modified": now, "versions": []}

    def new_tab(self):
        if len(self.tabs) >= self.MAX_TABS:
            return
        self.save_now()
        note = self._new_note()
        self.tabs.append(note)
        self.activate(note)

    def open_note(self, note):
        self.save_now()
        if note not in self.tabs:
            if len(self.tabs) >= self.MAX_TABS:
                self.tabs.pop(0)
            self.tabs.append(note)
        self.activate(note)

    def activate(self, note):
        self.active = note
        self._loading = True
        self.title_var.set(note["title"])
        self.text.delete("1.0", "end")
        self.text.insert("1.0", note["text"])
        self._loading = False
        self.pin_btn.configure(text="Unpin" if note["pinned"] else "Pin")
        self.render_tabs()
        self.render_list()
        self.text.focus_set()

    def close_tab(self, note=None):
        note = note or self.active
        if not note:
            return
        self.save_now()
        if note in self.tabs:
            self.tabs.remove(note)
        if self.tabs:
            self.activate(self.tabs[-1])
        else:
            self.active = None
            self.new_tab()

    def close_window(self):
        self.save_now()
        self.tabs, self.active = [], None
        self.withdraw()

    def hide(self):
        self.save_now()
        self.withdraw()

    def _title_changed(self, *a):
        if self._loading or not self.active:
            return
        self.active["title"] = self.title_var.get()[:100]
        self.active["auto"] = False
        self._schedule()

    def _schedule(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(600, self.save_now)

    def save_now(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None
        n = self.active
        if not n:
            return
        text = self.text.get("1.0", "end-1c")
        changed = text != n["text"]
        n["text"] = text
        if n["auto"]:
            words = text.split()
            if len(text.strip()) >= 10 or len(words) >= 3:
                n["title"] = " ".join(words[:3])[:100]
                self._loading = True
                self.title_var.set(n["title"])
                self._loading = False
                n["auto"] = True
        if not text.strip() and not n["title"].strip():
            if n in self.app.notes:
                self.app.notes.remove(n)
                self.app.save_notes()
            return
        if n not in self.app.notes:
            self.app.notes.append(n)
        if changed:
            now = time.time()
            n["modified"] = now
            v = n["versions"]
            if v and now - v[-1]["t"] < 600:
                v[-1] = {"t": now, "text": text}
            else:
                v.append({"t": now, "text": text})
            del v[:-50]
        self.app.save_notes()
        self.render_tabs()
        self.render_list()
        if self.app.page == "scratchpad" and changed:
            self.app.refresh_soon()

    def toggle_pin(self):
        if self.active:
            self.active["pinned"] = not self.active["pinned"]
            self.pin_btn.configure(text="Unpin" if self.active["pinned"] else "Pin")
            self.save_now()
            self.app.save_notes()
            self.render_list()

    def delete_note(self):
        n = self.active
        if not n:
            return
        if not messagebox.askyesno("Delete note", "Delete this note and its history? This can't be undone.", parent=self):
            return
        if n in self.app.notes:
            self.app.notes.remove(n)
            self.app.save_notes()
        n["text"], n["title"] = "", ""
        self._loading = True
        self.text.delete("1.0", "end")
        self._loading = False
        self.close_tab(n)
        self.app.refresh_soon()

    def show_history(self):
        n = self.active
        if not n or not n["versions"]:
            return
        m = tk.Menu(self, tearoff=0)
        for v in reversed(n["versions"]):
            label = time.strftime("%b %d, %I:%M %p", time.localtime(v["t"])) + "  " + v["text"][:40].replace("\n", " ")
            m.add_command(label=label, command=lambda v=v: self._restore(v))
        m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _restore(self, v):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", v["text"])
        self.save_now()

    def toggle_sidebar(self):
        self.sidebar_on = not self.sidebar_on
        if self.sidebar_on:
            self.side.pack(side="left", fill="y", padx=(0, 8), before=self.side.master.winfo_children()[-1])
        else:
            self.side.pack_forget()

    # -- rendering
    def render_tabs(self):
        for w in self.tabbar.winfo_children():
            w.destroy()
        for n in self.tabs:
            on = n is self.active
            fr = ctk.CTkFrame(self.tabbar, fg_color=THEME["accent"] if on else CARD, corner_radius=6)
            fr.pack(side="left", padx=2)
            lab = ctk.CTkLabel(fr, text=(n["title"] or "Untitled")[:16], text_color="white" if on else MUTED, cursor="hand2")
            lab.pack(side="left", padx=(8, 2), pady=2)
            lab.bind("<Button-1>", lambda e, n=n: self.open_note(n))
            x = ctk.CTkLabel(fr, text="×", text_color="white" if on else MUTED, cursor="hand2", width=14)
            x.pack(side="left", padx=(0, 6))
            x.bind("<Button-1>", lambda e, n=n: self.close_tab(n))

    def render_list(self):
        for w in self.listbox.winfo_children():
            w.destroy()
        q = self.search.get().lower()
        for n in sorted_notes(self.app.notes):
            if q and q not in n["title"].lower() and q not in n["text"].lower():
                continue
            t = ("📌 " if n["pinned"] else "") + (n["title"] or "Untitled")
            b = ctk.CTkButton(self.listbox, text=t[:26], anchor="w", height=28, command=lambda n=n: self.open_note(n),
                              fg_color=CARD if n is self.active else "transparent", text_color=("gray10", "gray90"),
                              hover_color=("gray85", "gray25"))
            b.pack(fill="x", pady=1)

    # -- shortcut behaviour
    def toggle(self, force_show=False):
        visible = self.winfo_viewable()
        if visible and self.focus_displayof() is not None and not force_show:
            self.hide()
            return
        mode = self.app.cfg["scratchpad_open"]
        if mode == "Open last active pinned note":
            pinned = [n for n in sorted_notes(self.app.notes) if n["pinned"]]
            if pinned:
                self.open_note(pinned[0])
        elif mode == "Open in new tab" and self.active and self.active["text"].strip():
            self.new_tab()
        if not self.tabs:
            self.new_tab()
        self.deiconify()
        self.lift()
        self.focus_force()
        self.text.focus_set()


def sorted_notes(notes):
    return sorted(notes, key=lambda n: (not n["pinned"], -n["modified"]))


# -------------------------------------------------------------------- app
class App(ctk.CTk):
    NAV = [("home", "Home"), ("meetings", "Meetings"), ("dictionary", "Dictionary"), ("snippets", "Snippets"), ("style", "Style"),
           ("transforms", "Transforms"), ("scratchpad", "Scratchpad"), ("insights", "Insights")]

    def __init__(self, selftest=False):
        super().__init__()
        self.selftest = selftest
        self.selftest_events: list = []
        self.cfg = c.load_settings()
        ctk.set_appearance_mode(self.cfg["theme"])
        self.dg_key = c.get_key(self.cfg, "deepgram")
        self.ai_key = c.get_key(self.cfg, "groq")
        self.history = c.purge(c.load_json(c.HISTORY_FILE, []), self.cfg["history_storage"])
        stats = c.load_json(c.STATS_FILE, None)
        self.stats = stats if isinstance(stats, dict) and "days" in stats else c.new_stats()
        self.notes = c.load_json(c.NOTES_FILE, [])
        self.meetings = c.load_json(c.MEETINGS_FILE, [])
        c.backfill_spend(self.stats, self.history, self.meetings, self.cfg)
        self.meeting = None  # the meeting being transcribed right now
        self.meeting_rec = None
        self.meeting_win = None
        self.detect = {"quiet": 0, "handled": ""}
        self.events: queue.Queue = queue.Queue()
        self.phase = "idle"  # idle | rec | proc
        self.rec: dict | None = None
        self.session = None
        self.sid = 0
        self.last_entry = self.history[0] if self.history else None
        self.last_tf = None
        self.tf_busy = False
        self.retrying = False
        self.tf_token = 0
        self.capture_cb = None
        self.paused = False
        self.page = "home"
        self.search_text = ""
        self._save_job = None
        self._refresh_job = None
        self._limit_jobs: list = []
        self._mic_close_job = None
        self._home_box = None
        self.hist_limit = 20
        self.heat_offset = 0
        self.tray = None
        self.changes_win = None

        self.title(APP)
        self.icon = c.resource("dictation_tool.ico")
        try:
            self.iconbitmap(self.icon)
        except Exception:
            log.exception("window icon")
        if self.cfg["autostart"] and not selftest:  # keep "launch at login" pointing at whatever is running now
            try:
                c.set_autostart(True, SCRIPT)
            except Exception:
                log.exception("autostart")
        self.geometry("1000x700")
        self.minsize(860, 560)
        self._build_shell()
        self.bar = FlowBar(self)
        self.pad = Scratchpad(self)
        self.recorder = c.Recorder(self.events.put)
        self.hook = c.InputHook(self.events.put, accept_injected=selftest)
        self.hook.start()
        self.hook.ready.wait(3)
        self.apply_bindings()
        self._start_tray()
        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.after(10, self._poll)
        if not selftest:
            self.after(4000, self._detect_tick)
            self.after(500, self._caps_guard)
        self.show("home")
        if self.cfg["keep_mic_open"] and not selftest:
            self.after(500, self._warm_mic)
        if self.cfg["start_minimized"] and self.tray and not selftest:
            self.withdraw()

    # ---- small helpers
    def btn(self, master, text, command, width=90, primary=True, **kw):
        if primary:
            kw.setdefault("fg_color", THEME["accent"])
            kw.setdefault("hover_color", THEME["accent_hover"])
            return ctk.CTkButton(master, text=text, command=command, width=width, **kw)
        return ctk.CTkButton(master, text=text, command=command, width=width, fg_color="transparent",
                             border_width=1, text_color=("gray10", "gray90"), hover_color=("gray85", "gray25"), **kw)

    def h1(self, master, text, sub=""):
        ctk.CTkLabel(master, text=text, font=(F, 24, "bold"), anchor="w").pack(fill="x", padx=24, pady=(20, 0))
        if sub:
            ctk.CTkLabel(master, text=sub, text_color=MUTED, anchor="w", justify="left", wraplength=720
                         ).pack(fill="x", padx=24, pady=(2, 0))

    def ptt_label(self) -> str:
        b = self.cfg["shortcuts"].get("ptt") or [[]]
        return c.combo_label(b[0])

    def sc_label(self, action) -> str:
        b = self.cfg["shortcuts"].get(action) or []
        return c.combo_label(b[0]) if b else "not set"

    def notify(self, title, body="", actions=(), timeout=5000):
        self.bar.notify(title, body, actions, timeout)

    def set_cfg(self, key, value):
        self.cfg[key] = value
        self.save_cfg()

    def save_cfg(self):
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(400, self._save_now)

    def _save_now(self):
        self._save_job = None
        if self.selftest:
            return
        try:
            c.save_json(c.SETTINGS_FILE, self.cfg)
        except Exception:
            log.exception("save settings")

    def save_history(self):
        if not self.selftest:
            try:
                c.save_json(c.HISTORY_FILE, self.history)
            except Exception:
                log.exception("save history")

    def save_notes(self):
        if not self.selftest:
            try:
                c.save_json(c.NOTES_FILE, self.notes)
            except Exception:
                log.exception("save notes")

    def apply_bindings(self):
        self.hook.set_bindings(self.cfg["shortcuts"])

    def refresh_soon(self):
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(200, self._refresh)

    def _refresh(self):
        self._refresh_job = None
        if self.winfo_viewable():
            self.show(self.page)

    def _beep(self, freq):
        if self.cfg["sounds"] and not self.selftest:
            threading.Thread(target=lambda: winsound.Beep(freq, 70), daemon=True).start()

    def _warm_mic(self):
        if self.recorder.stream is None:
            try:
                self.recorder.open(self.cfg["mic"])
            except Exception as e:
                log.warning("mic warm-up failed: %s", e)

    # ---- shell
    def _build_shell(self):
        side = ctk.CTkFrame(self, width=190, corner_radius=0)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        ctk.CTkLabel(side, text=APP, font=(F, 19, "bold"), anchor="w").pack(fill="x", padx=18, pady=(18, 14))
        self.nav_btns = {}
        for key, label in self.NAV:
            self.nav_btns[key] = self._nav(side, key, label)
            self.nav_btns[key].pack(fill="x", padx=10, pady=1)
        ctk.CTkFrame(side, fg_color="transparent").pack(fill="both", expand=True)
        for key, label in (("settings", "Settings"), ("help", "Help")):
            self.nav_btns[key] = self._nav(side, key, label)
            self.nav_btns[key].pack(fill="x", padx=10, pady=1)
        ctk.CTkFrame(side, fg_color="transparent", height=10).pack()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(side="left", fill="both", expand=True)

    def _nav(self, master, key, label):
        return ctk.CTkButton(master, text=label, anchor="w", height=34, command=lambda: self.show(key),
                             fg_color="transparent", text_color=("gray10", "gray90"),
                             hover_color=("gray85", "gray25"), font=(F, 14))

    def show(self, page):
        self.page = page
        for key, b in self.nav_btns.items():
            b.configure(fg_color=("gray82", "gray28") if key == page else "transparent")
        for w in self.content.winfo_children():
            w.destroy()
        try:
            getattr(self, "page_" + page)(self.content)
        except Exception:
            log.exception("page %s", page)
            ctk.CTkLabel(self.content, text="Something went wrong loading this page. See the log.").pack(pady=40)

    def open_page(self, page):
        self.show_window()
        self.show(page)

    # ================================================================ pages
    def page_home(self, p):
        name = self.cfg["name"].strip()
        self.h1(p, f"Welcome back, {name}" if name else "Welcome back")
        cur, _ = c.streaks(self.stats)
        chips = ctk.CTkFrame(p, fg_color="transparent")
        chips.pack(fill="x", padx=24, pady=(10, 0))
        for text in (f"🔥 {cur} day streak" if cur > 1 else "🔥 Start a streak",
                     f"🚀 {self.stats['total_words']:,} words", f"🏆 {c.wpm(self.stats)} WPM",
                     f"💸 {c.money(self.stats['spend']['usd'])} spent"):
            ctk.CTkLabel(chips, text=text, fg_color=CARD, corner_radius=14, padx=12, height=28).pack(side="left", padx=(0, 8))
        hero = ctk.CTkFrame(p, fg_color=CARD)
        hero.pack(fill="x", padx=24, pady=14)
        ctk.CTkLabel(hero, text=f"Hold  {self.ptt_label()}  and speak. Release to insert the text where your cursor is.",
                     font=(F, 15, "bold"), anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(hero, anchor="w", text_color=MUTED, justify="left",
                     text=f"Hands-free: {self.sc_label('handsfree')}, or double-tap push to talk, or click the Dictation Bar.   "
                          f"Paste last transcript: {self.sc_label('paste_last')}.   Esc cancels."
                     ).pack(fill="x", padx=16, pady=(0, 14))
        if not self.dg_key:
            warn = ctk.CTkFrame(p, fg_color="transparent")
            warn.pack(fill="x", padx=24)
            ctk.CTkLabel(warn, text="Add your Deepgram API key to start dictating.", text_color=THEME["danger"]).pack(side="left")
            self.btn(warn, "Open Settings", lambda: self.show("settings"), 110).pack(side="left", padx=10)
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=(6, 0))
        ctk.CTkLabel(row, text="Recent activity", font=(F, 15, "bold")).pack(side="left")
        sv = tk.StringVar(value=self.search_text)
        ent = ctk.CTkEntry(row, textvariable=sv, placeholder_text="Search history", width=220)
        ent.pack(side="right")
        box = ctk.CTkScrollableFrame(p, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=16, pady=8)
        self._home_box = box

        def render(*a):
            self.search_text = sv.get()
            for w in box.winfo_children():
                w.destroy()
            self._history_rows(box, sv.get().lower())

        sv.trace_add("write", render)
        render()

    def entry_text(self, e) -> str:
        return e.get("raw", "") if e.get("show_raw") else e.get("text", "")

    def _history_rows(self, box, q):
        if self.cfg["history_storage"] == "Never store":
            ctk.CTkLabel(box, text="History disabled. Change it in Settings → Data & Privacy.", text_color=MUTED).pack(anchor="w", padx=8, pady=8)
            return
        matches = [e for e in self.history if not q or q in self.entry_text(e).lower()]
        items = matches[:self.hist_limit]  # a short list keeps the page quick to draw
        if not items:
            ctk.CTkLabel(box, text="Nothing yet. Your dictations will show up here.", text_color=MUTED).pack(anchor="w", padx=8, pady=8)
            return
        last_day = None
        for e in items:
            day = dt.date.fromtimestamp(e["ts"])
            if day != last_day:
                last_day = day
                self._day_label(box, day)
            self._history_row(box, e)
        if len(matches) > len(items):
            def more():
                self.hist_limit += 40
                self.show("home")
            self.btn(box, f"Show more ({len(matches) - len(items)} older)", more, 200, primary=False).pack(pady=10)

    def _day_label(self, box, day, **pack):
        today = dt.date.today()
        label = "Today" if day == today else "Yesterday" if (today - day).days == 1 else day.strftime("%B %d, %Y")
        w = ctk.CTkLabel(box, text=label.upper(), text_color=MUTED, font=(F, 11, "bold"), anchor="w")
        w.pack(fill="x", padx=8, pady=(10, 2), **pack)
        w.day = day
        return w

    def _history_row(self, box, e, **pack):
        row = ctk.CTkFrame(box, fg_color=CARD)
        row.pack(fill="x", padx=4, pady=2, **pack)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=time.strftime("%I:%M %p", time.localtime(e["ts"])), text_color=MUTED, width=70,
                     font=(F, 11)).grid(row=0, column=0, sticky="nw", padx=(10, 4), pady=8)
        status = e.get("status", "done")
        if status == "done":
            text = ("✨ " if e.get("mode") == "command" else "") + self.entry_text(e)
            color = ("gray10", "gray92")
        else:
            text = "Transcription failed" if status == "failed" else "Cancelled"
            color = THEME["danger"] if status == "failed" else MUTED
        ctk.CTkLabel(row, text=text, wraplength=520, justify="left", anchor="w", text_color=color
                     ).grid(row=0, column=1, sticky="w", pady=8)
        if status == "done":
            self.btn(row, "Copy", lambda e=e: pyperclip.copy(self.entry_text(e)), 56, primary=False).grid(row=0, column=2, padx=4)
        elif os.path.exists(c.AUDIO_DIR / f"{e['id']}.wav"):
            self.btn(row, "Retry" if status == "failed" else "Recover", lambda e=e: self.retry_entry(e), 70).grid(row=0, column=2, padx=4)
        self.btn(row, "⋯", lambda e=e: self._entry_menu(e), 32, primary=False).grid(row=0, column=3, padx=(0, 8))
        return row

    def _home_add(self, entry):
        """Show a new dictation on Home by adding one row, instead of redrawing the whole list."""
        box = self._home_box
        if self.page != "home" or box is None or not box.winfo_exists() or not self.winfo_viewable() or self.search_text:
            return
        kids = box.winfo_children()
        first = kids[0] if kids else None
        if first is None or getattr(first, "day", None) != dt.date.today():
            self.refresh_soon()  # first one today (or an empty list): simplest to redraw once
            return
        after = first
        self._history_row(box, entry, after=after)

    def _entry_menu(self, e):
        m = tk.Menu(self, tearoff=0)
        if e.get("status") == "done" and e.get("raw") and e.get("raw") != e.get("text") and e.get("mode") != "command":
            m.add_command(label="Redo AI edit" if e.get("show_raw") else "Undo AI edit",
                          command=lambda: (e.__setitem__("show_raw", not e.get("show_raw")), self.save_history(), self.refresh_soon()))
        if os.path.exists(c.AUDIO_DIR / f"{e['id']}.wav"):
            m.add_command(label="Retry transcript", command=lambda: self.retry_entry(e))
        m.add_command(label="Delete", command=lambda: self._delete_entry(e))
        m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _delete_entry(self, e):
        if e in self.history:
            self.history.remove(e)
        try:
            (c.AUDIO_DIR / f"{e['id']}.wav").unlink(missing_ok=True)
        except Exception:
            pass
        if self.last_entry is e:
            self.last_entry = self.history[0] if self.history else None
        self.save_history()
        self.refresh_soon()

    # ---- dictionary
    def page_dictionary(self, p):
        self.h1(p, "Dictionary", "Teach Dictation Tool your words. Each word is sent to the speech model as a hint; "
                                 "a correction replaces a misspelling it keeps producing.")
        self._list_page(p, "dictionary")

    def page_snippets(self, p):
        self.h1(p, "Snippets", "Save text under a spoken trigger. Say the trigger alone or inside a sentence and the "
                               "expansion replaces it.")
        self._list_page(p, "snippets")

    def _list_page(self, p, kind):
        is_dict = kind == "dictionary"
        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=12)
        sv = tk.StringVar()
        ctk.CTkEntry(bar, textvariable=sv, placeholder_text="Search", width=240).pack(side="left")
        self.btn(bar, "Add new", (lambda: self.dlg_word()) if is_dict else (lambda: self.dlg_snippet()), 90).pack(side="right")
        self.btn(bar, "Import", (self.import_dictionary if is_dict else self.import_snippets), 80, primary=False).pack(side="right", padx=8)
        box = ctk.CTkScrollableFrame(p, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        def render(*a):
            for w in box.winfo_children():
                w.destroy()
            q = sv.get().lower()
            items = self.cfg[kind]
            shown = 0
            for e in sorted(items, key=lambda e: (e.get("word") or e.get("trigger") or "").lower()):
                if is_dict:
                    main = f"{e['misspelling']}  →  {e['word']}" if e.get("misspelling") else e["word"]
                    sub = ""
                    hay = main.lower()
                else:
                    main = e["trigger"]
                    sub = e.get("text", "").replace("\n", " ⏎ ")
                    sub = sub[:110] + ("…" if len(sub) > 110 else "")
                    hay = (main + " " + sub).lower()
                if q and q not in hay:
                    continue
                shown += 1
                row = ctk.CTkFrame(box, fg_color=CARD)
                row.pack(fill="x", padx=4, pady=2)
                ctk.CTkLabel(row, text=main, font=(F, 13, "bold"), anchor="w").pack(side="left", padx=(12, 8), pady=8)
                if sub:
                    ctk.CTkLabel(row, text=sub, text_color=MUTED, anchor="w").pack(side="left")
                self.btn(row, "🗑", lambda e=e: self._delete_item(kind, e), 32, primary=False).pack(side="right", padx=(2, 8))
                self.btn(row, "✎", (lambda e=e: self.dlg_word(e)) if is_dict else (lambda e=e: self.dlg_snippet(e)),
                         32, primary=False).pack(side="right")
                if e.get("source") == "imported":
                    ctk.CTkLabel(row, text="Imported from file", text_color=MUTED, font=(F, 11)).pack(side="right", padx=8)
            if not shown:
                ctk.CTkLabel(box, text="No matches." if q else "Nothing here yet. Click Add new.", text_color=MUTED).pack(anchor="w", padx=8, pady=8)

        sv.trace_add("write", render)
        render()

    def _delete_item(self, kind, e):
        if not messagebox.askyesno("Delete", "Delete this entry? This is permanent.", parent=self):
            return
        if e in self.cfg[kind]:
            self.cfg[kind].remove(e)
        self.save_cfg()
        self.refresh_soon()

    def _dialog(self, title, w, h):
        d = ctk.CTkToplevel(self)
        d.title(title)
        d.geometry(f"{w}x{h}+{self.winfo_rootx() + 120}+{self.winfo_rooty() + 80}")
        d.transient(self)
        try:
            d.iconbitmap(self.icon)
        except Exception:
            pass
        d.after(200, lambda: (d.lift(), d.focus_force()))
        return d

    def dlg_word(self, entry=None):
        d = self._dialog("Edit word" if entry else "Add to dictionary", 460, 300)
        wv = tk.StringVar(value=entry["word"] if entry else "")
        mv = tk.StringVar(value=entry.get("misspelling", "") if entry else "")
        on = tk.BooleanVar(value=bool(entry and entry.get("misspelling")))
        for v in (wv, mv):
            v.trace_add("write", lambda *a, v=v: v.set(v.get()[:60]) if len(v.get()) > 60 else None)
        ctk.CTkLabel(d, text="Correct spelling" if on.get() else "Word or phrase", anchor="w").pack(fill="x", padx=20, pady=(18, 2))
        e1 = ctk.CTkEntry(d, textvariable=wv)
        e1.pack(fill="x", padx=20)
        mis = ctk.CTkFrame(d, fg_color="transparent")
        ctk.CTkLabel(mis, text="Misspelling that keeps showing up", anchor="w").pack(fill="x", pady=(8, 2))
        ctk.CTkEntry(mis, textvariable=mv).pack(fill="x")
        msg = ctk.CTkLabel(d, text="", text_color=THEME["danger"], anchor="w")

        def toggle():
            if on.get():
                mis.pack(fill="x", padx=20, before=msg)
            else:
                mis.pack_forget()

        ctk.CTkSwitch(d, text="Correct a misspelling", variable=on, command=toggle, progress_color=THEME["accent"]).pack(anchor="w", padx=20, pady=10)
        msg.pack(fill="x", padx=20)
        toggle()

        def save():
            word, m = wv.get().strip(), (mv.get().strip() if on.get() else "")
            if not word or not any(ch.isalnum() for ch in word):
                msg.configure(text="Word cannot be empty")
                return
            if on.get() and not any(ch.isalnum() for ch in m):
                msg.configure(text="Enter the misspelling to correct")
                return
            for o in self.cfg["dictionary"]:
                if o is not entry and o["word"].lower() == word.lower() and o.get("misspelling", "").lower() == m.lower():
                    msg.configure(text=f'"{word}" already exists in your personal dictionary')
                    return
            if entry:
                if entry["word"] == word and entry.get("misspelling", "") == m:
                    msg.configure(text="No changes made")
                    return
                entry["word"], entry["misspelling"] = word, m
            else:
                self.cfg["dictionary"].append({"word": word, "misspelling": m, "source": "manual", "uses": 0})
            self.save_cfg()
            d.destroy()
            self.refresh_soon()

        self.btn(d, "Save changes" if entry else "Add word", save, 120).pack(anchor="e", padx=20, pady=12)
        d.bind("<Control-Return>", lambda e: save())
        e1.focus_set()

    def dlg_snippet(self, entry=None):
        d = self._dialog("Edit snippet" if entry else "Add snippet", 540, 420)
        tv = tk.StringVar(value=entry["trigger"] if entry else "")
        tv.trace_add("write", lambda *a: tv.set(tv.get()[:60]) if len(tv.get()) > 60 else None)
        ctk.CTkLabel(d, text="Snippet (what you say)", anchor="w").pack(fill="x", padx=20, pady=(18, 2))
        e1 = ctk.CTkEntry(d, textvariable=tv)
        e1.pack(fill="x", padx=20)
        ctk.CTkLabel(d, text="Expansion (what gets inserted)", anchor="w").pack(fill="x", padx=20, pady=(10, 2))
        box = ctk.CTkTextbox(d, height=180, wrap="word")
        box.pack(fill="both", expand=True, padx=20)
        if entry:
            box.insert("1.0", entry.get("text", ""))
        count = ctk.CTkLabel(d, text="", text_color=MUTED, anchor="e")
        count.pack(fill="x", padx=20)
        box.bind("<KeyRelease>", lambda e: count.configure(text=f"{len(box.get('1.0', 'end-1c'))}/4000"))
        msg = ctk.CTkLabel(d, text="", text_color=THEME["danger"], anchor="w")
        msg.pack(fill="x", padx=20)

        def save():
            trig, text = tv.get().strip(), box.get("1.0", "end-1c")
            if not trig or not text.strip():
                msg.configure(text="Please enter a snippet and its expansion")
                return
            if not any(ch.isalnum() for ch in trig):
                msg.configure(text="The snippet needs letters or numbers")
                return
            if len(text) > 4000:
                msg.configure(text="This expansion is too long. Please shorten it and try again.")
                return
            for o in self.cfg["snippets"]:
                if o is not entry and c._norm(o["trigger"]) == c._norm(trig):
                    msg.configure(text=f'"{trig}" already exists in your personal dictionary')
                    return
            if entry:
                if entry["trigger"] == trig and entry.get("text", "") == text:
                    msg.configure(text="No changes made")
                    return
                entry["trigger"], entry["text"] = trig, text
            else:
                self.cfg["snippets"].append({"trigger": trig, "text": text, "source": "manual", "uses": 0})
            self.save_cfg()
            d.destroy()
            self.refresh_soon()

        self.btn(d, "Save changes" if entry else "Add snippet", save, 120).pack(anchor="e", padx=20, pady=12)
        d.bind("<Control-Return>", lambda e: save())
        e1.focus_set()

    def _import(self, kind, ext, parser, key_of):
        path = filedialog.askopenfilename(parent=self, title="Import", filetypes=[(ext.upper(), "*." + ext)])
        if not path:
            return
        try:
            if not path.lower().endswith("." + ext):
                raise ValueError(f"Incorrect file type. Use .{ext}")
            if os.path.getsize(path) > 3 * 1024 * 1024:
                raise ValueError("File is too large")
            items, skipped = parser(Path(path).read_text(encoding="utf-8-sig"))
            if len(items) > 1000:
                raise ValueError("Cannot import more than 1,000 entries")
            if not items:
                raise ValueError("No items found")
        except ValueError as e:
            messagebox.showerror("Import", str(e), parent=self)
            return
        except Exception:
            messagebox.showerror("Import", "Failed to parse or read file", parent=self)
            return
        have = {key_of(e) for e in self.cfg[kind]}
        new = []
        for it in items:
            k = key_of(it)
            if k in have:
                skipped += 1
            else:
                have.add(k)
                new.append(it)
        if not new:
            messagebox.showinfo("Import", f"All {len(items)} items already exist", parent=self)
            return
        if messagebox.askokcancel("Import", f"Import {len(new)} items?" + (f"\n{skipped} skipped (duplicates or invalid)." if skipped else ""), parent=self):
            self.cfg[kind].extend(new)
            self.save_cfg()
            self.refresh_soon()

    def import_dictionary(self):
        self._import("dictionary", "csv", c.parse_dictionary_csv,
                     lambda e: (e["word"].lower(), e.get("misspelling", "").lower()))

    def import_snippets(self):
        self._import("snippets", "json", c.parse_snippets_json, lambda e: c._norm(e["trigger"]))

    # ---- style
    def page_style(self, p):
        self.h1(p, "Style", "How your dictation is written in each kind of app, and how much it is cleaned up.")
        tabs = ["Personal messages", "Work messages", "Email", "Other", "Auto cleanup"]
        keys = ["personal", "work", "email", "other", "cleanup"]
        cur = getattr(self, "_style_tab", "Personal messages")
        body = ctk.CTkFrame(p, fg_color="transparent")

        def render(tab):
            self._style_tab = tab
            for w in body.winfo_children():
                w.destroy()
            key = keys[tabs.index(tab)]
            if key == "cleanup":
                opts = [("None", "Exactly what you said", "Um so I think we should, uh, meet tuesday no wednesday"),
                        ("Light", "Removes filler words and fixes grammar", "So I think we should meet Wednesday."),
                        ("Medium", "Also edits for clarity and conciseness", "Let's meet Wednesday.")]
                chosen = self.cfg["cleanup"]
                where = {"personal": "", "work": "", "email": "", "other": ""}
            else:
                opts = [(s,) + c.STYLE_BLURB[s] for s in c.STYLE_OPTIONS[key]]
                chosen = self.cfg["styles"][key]
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=10)
            for name, blurb, sample in opts:
                on = name == chosen
                card = ctk.CTkButton(
                    row, width=220, height=150, corner_radius=12, font=(F, 13),
                    text=f"{name}\n{blurb}\n\n“{sample}”", fg_color=CARD, text_color=("gray10", "gray92"),
                    hover_color=("gray85", "gray25"), border_width=3 if on else 1,
                    border_color=THEME["accent"] if on else ("gray75", "gray35"),
                    command=lambda n=name: pick(key, n))
                if getattr(card, "_text_label", None) is not None:
                    card._text_label.configure(wraplength=190, justify="left")
                card.pack(side="left", padx=(0, 12))
            if key == "cleanup":
                note = ("Light and Medium use your AI key for full cleanup, self-corrections and lists."
                        if self.ai_key else
                        "No AI key yet: Light and Medium use basic rules (filler words, \"scratch that\"). "
                        "Add an AI key in Settings for full cleanup, self-corrections, lists and Medium rewording.")
                ctk.CTkLabel(body, text=note, text_color=MUTED, anchor="w", justify="left", wraplength=700).pack(fill="x")
            else:
                apps = {"personal": "WhatsApp, Telegram, Signal, Messenger, Discord",
                        "work": "Slack, Teams, Zoom chat", "email": "Outlook, Gmail, Thunderbird",
                        "other": "Everything else. AI assistants and terminals get the raw transcript."}[key]
                ctk.CTkLabel(body, text=f"Applies in: {apps}", text_color=MUTED, anchor="w").pack(fill="x")

        def pick(key, name):
            if key == "cleanup":
                self.cfg["cleanup"] = name
            else:
                self.cfg["styles"][key] = name
            self.save_cfg()
            render(self._style_tab)

        seg = ctk.CTkSegmentedButton(p, values=tabs, command=render, selected_color=THEME["accent"],
                                     selected_hover_color=THEME["accent_hover"])
        seg.set(cur)
        seg.pack(anchor="w", padx=24, pady=(14, 0))
        body.pack(fill="both", expand=True, padx=24)
        render(cur)

    # ---- transforms
    def page_transforms(self, p):
        self.h1(p, "Transforms", "Rewrite selected text in any app. Select text, press the shortcut, and the rewrite replaces it.")
        if not self.ai_key:
            ctk.CTkLabel(p, text="Transforms need an AI key. Add one in Settings → Account.", text_color=THEME["danger"], anchor="w").pack(fill="x", padx=24, pady=(8, 0))
        top = ctk.CTkFrame(p, fg_color="transparent")
        top.pack(fill="x", padx=24, pady=10)
        on = tk.BooleanVar(value=self.cfg["transforms_on"])
        ctk.CTkSwitch(top, text="Transforms on", variable=on, progress_color=THEME["accent"],
                      command=lambda: (self.set_cfg("transforms_on", on.get()))).pack(side="left")
        self.btn(top, "Reset to defaults", self._reset_transforms, 130, primary=False).pack(side="right")
        box = ctk.CTkScrollableFrame(p, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=16)

        def card(tf_id, name, desc, editable):
            row = ctk.CTkFrame(box, fg_color=CARD)
            row.pack(fill="x", padx=4, pady=3)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=14, pady=10)
            ctk.CTkLabel(left, text=name, font=(F, 14, "bold"), anchor="w").pack(fill="x")
            ctk.CTkLabel(left, text=desc, text_color=MUTED, anchor="w", justify="left", wraplength=470).pack(fill="x")
            self.btn(row, "Configure", lambda: self.dlg_transform(tf_id), 90, primary=False).pack(side="right", padx=(4, 12))
            pill = self.btn(row, self.sc_label(tf_id), None, 150, primary=False)
            pill.configure(command=lambda: self.record_shortcut(tf_id, 0, pill, single=True))
            pill.pack(side="right")

        card("polish", "Polish", "Makes text more concise, clearer and better ordered while keeping your tone.", False)
        card("prompt_engineer", "Prompt Engineer", "Turns unstructured thoughts into a structured AI prompt.", False)
        for t in self.cfg["custom_transforms"]:
            card("tf:" + t["id"], t.get("name") or "Untitled transform", (t.get("prompt") or "No prompt yet")[:140], True)
        if len(self.cfg["custom_transforms"]) < 8:
            self.btn(box, "+ Create new", self._new_transform, 130).pack(anchor="w", padx=4, pady=10)
        auto = ctk.CTkFrame(p, fg_color="transparent")
        auto.pack(fill="x", padx=24, pady=12)
        ctk.CTkLabel(auto, text="Auto apply after dictation").pack(side="left")
        names = {"Off": "", "Polish": "polish", "Prompt Engineer": "prompt_engineer"}
        for t in self.cfg["custom_transforms"]:
            if t.get("name"):
                names[t["name"]] = "tf:" + t["id"]
        cur = next((k for k, v in names.items() if v == self.cfg["auto_apply"]), "Off")
        om = ctk.CTkOptionMenu(auto, values=list(names), command=lambda v: self.set_cfg("auto_apply", names[v]),
                               fg_color=THEME["accent"], button_color=THEME["accent_hover"], width=180)
        om.set(cur)
        om.pack(side="left", padx=10)
        ctk.CTkLabel(auto, text=f"See changes: {self.sc_label('view_changes')}", text_color=MUTED).pack(side="right")

    def _new_transform(self):
        t = {"id": f"{int(time.time() * 1000):x}", "name": "", "prompt": "", "samples": []}
        self.cfg["custom_transforms"].append(t)
        self.save_cfg()
        self.show("transforms")
        self.dlg_transform("tf:" + t["id"])

    def _reset_transforms(self):
        if not messagebox.askyesno("Reset to defaults", "Remove all custom transforms and restore Polish and Prompt Engineer?", parent=self):
            return
        for t in self.cfg["custom_transforms"]:
            self.cfg["shortcuts"].pop("tf:" + t["id"], None)
        self.cfg["custom_transforms"] = []
        self.cfg["polish"] = c.json.loads(c.json.dumps(c.DEFAULTS["polish"]))
        self.cfg["prompt_engineer"] = {"prompt": c.PE_PROMPT, "samples": []}
        self.cfg["shortcuts"]["polish"] = c.json.loads(c.json.dumps(c.DEFAULT_SHORTCUTS["polish"]))
        self.cfg["shortcuts"]["prompt_engineer"] = c.json.loads(c.json.dumps(c.DEFAULT_SHORTCUTS["prompt_engineer"]))
        self.apply_bindings()
        self.save_cfg()
        self.notify("Settings reset")
        self.show("transforms")

    def dlg_transform(self, tf_id):
        custom = next((t for t in self.cfg["custom_transforms"] if "tf:" + t["id"] == tf_id), None)
        store = custom if custom else self.cfg["polish" if tf_id == "polish" else "prompt_engineer"]
        d = self._dialog("Configure " + (custom.get("name") or "transform" if custom else c.transform_spec(self.cfg, tf_id)[0]), 600, 620)
        f = ctk.CTkScrollableFrame(d, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=10, pady=10)

        def lab(t):
            ctk.CTkLabel(f, text=t, font=(F, 13, "bold"), anchor="w").pack(fill="x", padx=8, pady=(10, 2))

        if custom:
            lab("Name")
            nv = tk.StringVar(value=custom["name"])
            nv.trace_add("write", lambda *a: (custom.__setitem__("name", nv.get().strip()), self.save_cfg()))
            ctk.CTkEntry(f, textvariable=nv).pack(fill="x", padx=8)
            lab("Shortcut")
            pill = self.btn(f, self.sc_label(tf_id) if self.cfg["shortcuts"].get(tf_id) else "Click to add (suggested: Win + Alt + 3)",
                            None, 260, primary=False)
            pill.configure(command=lambda: self.record_shortcut(tf_id, 0, pill, single=True))
            pill.pack(anchor="w", padx=8)
        if tf_id == "polish":
            lab("Rules")
            for key, label in c.POLISH_RULES:
                v = tk.BooleanVar(value=store.get(key, True))
                ctk.CTkSwitch(f, text=label, variable=v, progress_color=THEME["accent"],
                              command=lambda key=key, v=v: (store.__setitem__(key, v.get()), self.save_cfg())).pack(anchor="w", padx=8, pady=3)
            lab("Custom rules")
            rules = ctk.CTkFrame(f, fg_color="transparent")
            rules.pack(fill="x", padx=8)
            err = ctk.CTkLabel(f, text="", text_color=THEME["danger"], anchor="w")

            def render_rules():
                for w in rules.winfo_children():
                    w.destroy()
                for r in store["rules"]:
                    row = ctk.CTkFrame(rules, fg_color=CARD)
                    row.pack(fill="x", pady=2)
                    v = tk.BooleanVar(value=r.get("on", True))
                    ctk.CTkSwitch(row, text=r["text"][:60], variable=v, progress_color=THEME["accent"],
                                  command=lambda r=r, v=v: (r.__setitem__("on", v.get()), self.save_cfg())).pack(side="left", padx=8, pady=4)
                    self.btn(row, "🗑", lambda r=r: (store["rules"].remove(r), self.save_cfg(), render_rules()), 30, primary=False).pack(side="right", padx=6)

            add = ctk.CTkFrame(f, fg_color="transparent")
            add.pack(fill="x", padx=8, pady=4)
            rv = tk.StringVar()
            ent = ctk.CTkEntry(add, textvariable=rv, placeholder_text="For example: never use semicolons")
            ent.pack(side="left", fill="x", expand=True)

            def add_rule(*a):
                t = rv.get().strip()
                if not t:
                    return
                if any(r["text"].lower() == t.lower() for r in store["rules"]):
                    err.configure(text="A custom rule with this text already exists.")
                    return
                err.configure(text="")
                store["rules"].append({"text": t, "on": True})
                rv.set("")
                self.save_cfg()
                render_rules()

            self.btn(add, "Add", add_rule, 60).pack(side="left", padx=6)
            ent.bind("<Return>", add_rule)
            err.pack(fill="x", padx=8)
            render_rules()
        else:
            lab("Prompt")
            pb = ctk.CTkTextbox(f, height=150, wrap="word")
            pb.pack(fill="x", padx=8)
            pb.insert("1.0", store.get("prompt", ""))
            pb.bind("<KeyRelease>", lambda e: (store.__setitem__("prompt", pb.get("1.0", "end-1c").strip()), self.save_cfg()))
        lab("Writing samples (optional)")
        ctk.CTkLabel(f, text="Up to five samples of your own writing, 50 to 500 words each. Put a line with only --- between samples.",
                     text_color=MUTED, anchor="w", justify="left", wraplength=520).pack(fill="x", padx=8)
        sb = ctk.CTkTextbox(f, height=140, wrap="word")
        sb.pack(fill="x", padx=8, pady=4)
        sb.insert("1.0", "\n---\n".join(store.get("samples", [])))

        def save_samples(e=None):
            parts = [s.strip() for s in sb.get("1.0", "end-1c").split("\n---\n")]
            store["samples"] = [s for s in parts if s][:5]
            self.save_cfg()

        sb.bind("<KeyRelease>", save_samples)
        foot = ctk.CTkFrame(d, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=(0, 12))

        def close():
            d.destroy()
            if self.page == "transforms":
                self.show("transforms")

        if custom:
            def delete():
                self.cfg["custom_transforms"].remove(custom)
                self.cfg["shortcuts"].pop(tf_id, None)
                if self.cfg["auto_apply"] == tf_id:
                    self.cfg["auto_apply"] = ""
                self.apply_bindings()
                self.save_cfg()
                close()
            self.btn(foot, "Delete", delete, 80, primary=False).pack(side="left")
        self.btn(foot, "Done", close, 80).pack(side="right")
        d.protocol("WM_DELETE_WINDOW", close)

    # ---- scratchpad page
    def page_scratchpad(self, p):
        self.h1(p, "Scratchpad", "A place for typed or dictated drafts and reference notes.")
        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=12)
        self.btn(bar, "+ New note", lambda: (self.toggle_scratchpad(force_show=True), self.pad.new_tab()), 110).pack(side="left")
        ctk.CTkLabel(bar, text=f"Open from anywhere: {self.sc_label('scratchpad')}", text_color=MUTED).pack(side="left", padx=14)
        sv = tk.StringVar()
        ctk.CTkEntry(bar, textvariable=sv, placeholder_text="Search notes", width=220).pack(side="right")
        box = ctk.CTkScrollableFrame(p, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=16)

        def render(*a):
            for w in box.winfo_children():
                w.destroy()
            q = sv.get().lower()
            notes = [n for n in sorted_notes(self.notes) if not q or q in n["text"].lower() or q in n["title"].lower()]
            if not notes:
                ctk.CTkLabel(box, text="No notes yet.", text_color=MUTED).pack(anchor="w", padx=8, pady=8)
            for n in notes:
                row = ctk.CTkFrame(box, fg_color=CARD)
                row.pack(fill="x", padx=4, pady=2)
                left = ctk.CTkFrame(row, fg_color="transparent")
                left.pack(side="left", fill="x", expand=True, padx=12, pady=8)
                ctk.CTkLabel(left, text=("📌 " if n["pinned"] else "") + (n["title"] or "Untitled"), font=(F, 13, "bold"), anchor="w").pack(fill="x")
                ctk.CTkLabel(left, text=n["text"].replace("\n", " ")[:120], text_color=MUTED, anchor="w").pack(fill="x")
                self.btn(row, "Edit", lambda n=n: (self.toggle_scratchpad(force_show=True), self.pad.open_note(n)), 56).pack(side="right", padx=(2, 10))
                self.btn(row, "Unpin" if n["pinned"] else "Pin", lambda n=n: (n.__setitem__("pinned", not n["pinned"]), self.save_notes(), render()), 56, primary=False).pack(side="right", padx=2)
                ctk.CTkLabel(row, text=time.strftime("%b %d", time.localtime(n["modified"])), text_color=MUTED).pack(side="right", padx=8)

        sv.trace_add("write", render)
        render()

    def toggle_scratchpad(self, force_show=False):
        self.pad.toggle(force_show)

    # ---- notetaker
    def save_meetings(self):
        if not self.selftest:
            try:
                c.save_json(c.MEETINGS_FILE, self.meetings)
            except Exception:
                log.exception("save meetings")

    def page_meetings(self, p):
        free = self.cfg["meeting_engine"] != c.CLOUD_ENGINE
        self.h1(p, "Meetings", "Plain transcripts of your calls: your microphone as you, the computer's audio as the others. "
                               + ("Transcribed on this computer, so it costs nothing and nothing is sent anywhere. " if free
                                  else "Transcribed by Deepgram, billed per minute of speech. ")
                               + "Text appears a few seconds after someone stops talking. Copy it to Claude for the notes.")
        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.pack(fill="x", padx=24, pady=12)
        if self.meeting:
            self.btn(bar, "■  Stop transcribing", self.stop_meeting, 170, fg_color=THEME["danger"], hover_color="#B93A3E").pack(side="left")
            self.btn(bar, "Open live transcript", lambda: self.open_meeting(self.meeting), 160, primary=False).pack(side="left", padx=8)
        else:
            self.btn(bar, "●  Start transcribing", self.start_meeting, 170).pack(side="left")
        ctk.CTkLabel(bar, text=f"Calls in Teams, Zoom and Webex: {self.cfg['meeting_detect'].lower()}", text_color=MUTED).pack(side="left", padx=14)
        box = ctk.CTkScrollableFrame(p, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=16)
        if not self.meetings:
            ctk.CTkLabel(box, text="No meetings yet.", text_color=MUTED).pack(anchor="w", padx=8, pady=8)
        for m in self.meetings[:100]:
            live = m is self.meeting
            row = ctk.CTkFrame(box, fg_color=CARD)
            row.pack(fill="x", padx=4, pady=2)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            ctk.CTkLabel(left, text=("● " if live else "") + m["title"], font=(F, 13, "bold"), anchor="w",
                         text_color=THEME["danger"] if live else ("gray10", "gray92")).pack(fill="x")
            mins = max(1, round(((time.time() if live else m.get("end", m["start"])) - m["start"]) / 60))
            words = sum(c.count_words(s["text"]) for s in m["segments"])
            sub = f"{time.strftime('%b %d, %I:%M %p', time.localtime(m['start']))}   {mins} min   {words:,} words"
            if m.get("engine") == "local":
                sub += "   free (this computer)"
            elif m.get("sent_secs") is not None and not live:
                sub += f"   {max(1, round(m['sent_secs'] / 60))} min billed by Deepgram"
            ctk.CTkLabel(left, text=sub + ("   summary ✓" if m.get("summary") else ""), text_color=MUTED, anchor="w").pack(fill="x")
            self.btn(row, "Open", lambda m=m: self.open_meeting(m), 64).pack(side="right", padx=10)

    def toggle_meeting(self):
        if self.meeting:
            self.stop_meeting()
        else:
            self.start_meeting()

    def start_meeting(self, auto_app=""):
        if self.meeting:
            return
        local = self.cfg["meeting_engine"] != c.CLOUD_ENGINE
        if not local and not self.dg_key:
            self.notify("Add your Deepgram API key", "The Notetaker needs it to transcribe.",
                        actions=[("Open settings", lambda: self.open_page("settings"))])
            return
        now = time.time()
        m = {"id": f"{int(now * 1000)}", "start": now, "end": now, "segments": [], "summary": "", "auto": bool(auto_app),
             "engine": "local" if local else "deepgram",
             "title": f"{auto_app + ' meeting' if auto_app else 'Meeting'}, {time.strftime('%b %d %I:%M %p')}"}
        rec = c.MeetingRecorder(self.dg_key, self.cfg, self.events.put, m["id"], me=self.cfg["name"].strip() or "Me")
        try:
            rec.start()
        except Exception:
            log.exception("meeting start")
            rec.stop()
            self.notify("Microphone unavailable", "The meeting couldn't be transcribed. Check the microphone in Settings.")
            return
        self.meetings.insert(0, m)
        self.meeting, self.meeting_rec = m, rec
        self.save_meetings()
        body = ("Transcribed on this computer: free, nothing is sent anywhere. " if local
                else "Using Deepgram, billed per minute of speech. ") + "Tell the others they are being transcribed."
        if not rec.system_audio:
            body += " Only your microphone is captured: the computer's audio isn't available."
        self.notify("Transcribing this meeting", body, timeout=9000,
                    actions=[("Open", lambda: self.open_meeting(m)), ("Stop", self.stop_meeting)])
        self.bar.draw()
        if self.page == "meetings":
            self.refresh_soon()

    def stop_meeting(self, quiet=False):
        m, rec = self.meeting, self.meeting_rec
        if not m:
            return
        self.meeting, self.meeting_rec = None, None
        self.detect["handled"] = self.detect["handled"] or "manual"  # don't restart on the same call
        try:
            rec.stop()
            m["sent_secs"] = round(rec.sent_seconds())  # audio actually sent, which is what gets billed
            if m.get("engine") != "local":
                self._spent("meeting", m["sent_secs"], c.meeting_rate(self.cfg))
        except Exception:
            log.exception("meeting stop")
        m["end"] = time.time()
        self.save_meetings()
        self.bar.draw()
        if quiet:
            return

        def settle():  # the last words arrive a few seconds after stopping
            if not m["segments"] and m in self.meetings:
                self.meetings.remove(m)
                self.notify("Nothing was transcribed", "No speech was heard, so the meeting wasn't saved.")
            else:
                self.notify("Meeting saved", m["title"], actions=[("Open", lambda: self.open_meeting(m))])
            self.save_meetings()
            if self.page == "meetings":
                self.refresh_soon()

        self.after(7000, settle)
        if self.page == "meetings":
            self.refresh_soon()

    def _on_mt(self, mid, who, text, t0=None):
        m = self.meeting if self.meeting and self.meeting["id"] == mid else next(
            (x for x in self.meetings[:5] if x["id"] == mid), None)
        if not m:
            return
        seg = {"t": round(max(0.0, (t0 or time.time()) - m["start"]), 1), "who": who, "text": text}
        in_order = not m["segments"] or m["segments"][-1]["t"] <= seg["t"]
        m["segments"].append(seg)
        if not in_order:  # the two sides finish at different times: keep the transcript in spoken order
            m["segments"].sort(key=lambda x: x["t"])
        if len(m["segments"]) % 10 == 0:
            self.save_meetings()
        d = self.meeting_win
        if d and d.winfo_exists() and d.meeting is m:
            if in_order:
                self._append_seg(d, seg)
            else:
                self._fill_transcript(d)

    def _fill_transcript(self, d):
        """Put the whole transcript in the window in one go (one insert, however long the meeting)."""
        m = d.meeting
        names = m.get("names", {})
        tb = d.box
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("1.0", c.meeting_text(m))
        tb.see("end" if m is self.meeting else "1.0")
        tb.configure(state="disabled")
        last = m["segments"][-1]["who"] if m["segments"] else None
        d.last_who = (names.get(last) or last) if last else None

    def _append_seg(self, d, seg):
        tb = d.box
        tb.configure(state="normal")
        who = d.meeting.get("names", {}).get(seg["who"]) or seg["who"]
        if who != d.last_who:
            mm, ss = divmod(int(seg["t"]), 60)
            tb.insert("end", ("\n\n" if d.last_who else "") + f"[{mm:02d}:{ss:02d}] {who}\n{seg['text']}")
            d.last_who = who
        else:
            tb.insert("end", " " + seg["text"])
        tb.see("end")
        tb.configure(state="disabled")

    def open_meeting(self, m):
        if self.meeting_win and self.meeting_win.winfo_exists():
            self.meeting_win.destroy()
        d = self._dialog("Meeting", 780, 660)
        d.transient("")  # its own window, so it stays up when the Hub is hidden
        self.meeting_win = d
        d.meeting, d.last_who = m, None
        tv = tk.StringVar(value=m["title"])
        tv.trace_add("write", lambda *a: (m.__setitem__("title", tv.get().strip() or "Meeting"), self.save_meetings()))
        ctk.CTkEntry(d, textvariable=tv, font=(F, 18, "bold"), border_width=0, fg_color="transparent").pack(fill="x", padx=16, pady=(12, 0))
        status = ctk.CTkLabel(d, text="", text_color=MUTED, anchor="w")
        status.pack(fill="x", padx=22)
        row = ctk.CTkFrame(d, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=8)
        stop = self.btn(row, "■  Stop", self.stop_meeting, 80, fg_color=THEME["danger"], hover_color="#B93A3E")
        if m is self.meeting:
            stop.pack(side="left", padx=(0, 6))
        self.btn(row, "Copy transcript", lambda: (pyperclip.copy(c.meeting_markdown(m)), self.notify("Transcript copied", "Paste it to Claude and ask for the notes.", timeout=3000)), 130).pack(side="left", padx=(0, 6))
        self.btn(row, "Export", lambda: self.export_meeting(m), 70, primary=False).pack(side="left")
        self.btn(row, "Delete", lambda: self.delete_meeting(m), 70, primary=False).pack(side="right")
        d.summary = ctk.CTkTextbox(d, height=170, wrap="word")
        if m.get("summary"):
            d.summary.pack(fill="x", padx=16, pady=(0, 8))
            d.summary.insert("1.0", m["summary"])
        d.box = ctk.CTkTextbox(d, wrap="word", font=(F, 13))
        d.box.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self._fill_transcript(d)

        def tick():
            if not d.winfo_exists():
                return
            live = m is self.meeting
            secs = int((time.time() if live else m.get("end", m["start"])) - m["start"])
            when = time.strftime("%b %d, %I:%M %p", time.localtime(m["start"]))
            status.configure(text=(f"● Transcribing   {secs // 60:02d}:{secs % 60:02d}" if live else f"{when}   {max(1, round(secs / 60))} min"),
                             text_color=THEME["danger"] if live else MUTED)
            if not live and stop.winfo_ismapped():
                stop.pack_forget()
            d.after(1000, tick)

        tick()

    def export_meeting(self, m):
        safe = "".join(ch for ch in m["title"] if ch.isalnum() or ch in " -_").strip() or "Meeting"
        path = filedialog.asksaveasfilename(parent=self.meeting_win or self, defaultextension=".md", initialfile=safe + ".md",
                                            filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
        if path:
            Path(path).write_text(c.meeting_markdown(m), encoding="utf-8")
            self.notify("Meeting exported", timeout=2500)

    def delete_meeting(self, m):
        if not messagebox.askyesno("Delete meeting", "Delete this meeting and its transcript? This can't be undone.",
                                   parent=self.meeting_win or self):
            return
        if m is self.meeting:
            self.stop_meeting(quiet=True)
        if m in self.meetings:
            self.meetings.remove(m)
        self.save_meetings()
        if self.meeting_win and self.meeting_win.winfo_exists():
            self.meeting_win.destroy()
        if self.page == "meetings":
            self.refresh_soon()

    def _detect_tick(self):
        """Every few seconds: is a meeting app using the microphone?"""
        try:
            mode = self.cfg["meeting_detect"]
            auto_live = bool(self.meeting and self.meeting.get("auto"))
            app = c.meeting_app_on_mic() if (mode != "Do nothing" or auto_live) else ""
            d = self.detect
            if app:
                d["quiet"] = 0
                if not self.meeting and not d["handled"]:
                    d["handled"] = app
                    if mode == "Start automatically":
                        self.start_meeting(app)
                    elif mode == "Ask me":
                        self.notify(f"{app} call detected", "Transcribe it?", timeout=15000,
                                    actions=[("Start transcribing", lambda: self.start_meeting(app))])
            else:
                d["quiet"] += 1
                if d["quiet"] >= 4:  # about 12 seconds with no meeting app on the mic
                    d["handled"] = ""
                    if auto_live:
                        self.stop_meeting()
        except Exception:
            log.exception("detect")
        self.after(3000, self._detect_tick)

    # ---- insights
    def page_insights(self, p):
        self.h1(p, "Insights", "Your usage on this computer.")
        st = self.stats
        grid = ctk.CTkFrame(p, fg_color="transparent")
        grid.pack(fill="x", padx=18, pady=12)
        grid.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="t")

        def tile(col, title, big, lines=()):
            t = ctk.CTkFrame(grid, fg_color=CARD)
            t.grid(row=0, column=col, sticky="nsew", padx=6)
            ctk.CTkLabel(t, text=title, text_color=MUTED, anchor="w").pack(fill="x", padx=14, pady=(12, 0))
            ctk.CTkLabel(t, text=big, font=(F, 30, "bold"), anchor="w").pack(fill="x", padx=14)
            for ln in lines:
                ctk.CTkLabel(t, text=ln, text_color=MUTED, anchor="w").pack(fill="x", padx=14)
            ctk.CTkFrame(t, fg_color="transparent", height=10).pack()

        fx = st["fixes"]
        lines = [f"{fx['words']:,} words corrected"] * bool(fx["words"]) + [f"{fx['dictionary']:,} dictionary fixes"] * bool(fx["dictionary"])
        tile(0, "Words per minute", str(c.wpm(st)))
        tile(1, "Fixes made", f"{fx['words'] + fx['dictionary']:,}", lines)
        tile(2, "Total words dictated", f"{st['total_words']:,}")
        sp = st["spend"]
        today = sp["days"].get(dt.date.today().isoformat(), 0.0)
        spend_lines = [f"Today: {c.money(today)}", f"Dictation: {sp['dictation_secs'] / 60:.1f} min"]
        if sp.get("meeting_secs"):
            spend_lines.append(f"Meetings: {sp['meeting_secs'] / 60:.1f} min")
        if self.cfg["meeting_engine"] != c.CLOUD_ENGINE:
            spend_lines.append("Meetings are free now")
        tile(3, "Deepgram spend (estimate)", c.money(sp["usd"]), spend_lines)
        low = ctk.CTkFrame(p, fg_color="transparent")
        low.pack(fill="both", expand=True, padx=18)
        low.grid_columnconfigure(0, weight=2, uniform="l")
        low.grid_columnconfigure(1, weight=3, uniform="l")
        cats = ctk.CTkFrame(low, fg_color=CARD)
        cats.grid(row=0, column=0, sticky="nsew", padx=6, pady=4)
        ctk.CTkLabel(cats, text="Desktop usage", font=(F, 14, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(cats, text=f"{len(st['apps'])} apps", text_color=MUTED, anchor="w").pack(fill="x", padx=14)
        total = sum(v["words"] for v in st["cats"].values()) or 1
        rows = sorted(c.INSIGHT_CATS, key=lambda kv: -st["cats"].get(kv[0], {}).get("words", 0))
        for key, label in rows if st["total_words"] else []:
            w = st["cats"].get(key, {}).get("words", 0)
            level = "🚀 Power" if w >= 10000 else "👍 Moderate" if w >= 1000 else "🤏 Light"
            ctk.CTkLabel(cats, text=f"{label}   {w:,} words   {level}", anchor="w").pack(fill="x", padx=14, pady=(8, 0))
            bar = ctk.CTkProgressBar(cats, height=8, progress_color=THEME["accent"])
            bar.set(w / total)
            bar.pack(fill="x", padx=14)
        if not st["total_words"]:
            ctk.CTkLabel(cats, text="Dictate something to see where you use it.", text_color=MUTED, anchor="w").pack(fill="x", padx=14, pady=10)
        heat = ctk.CTkFrame(low, fg_color=CARD)
        heat.grid(row=0, column=1, sticky="nsew", padx=6, pady=4)
        cur, longest = c.streaks(st)
        head = ctk.CTkFrame(heat, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(head, text="Activity", font=(F, 14, "bold")).pack(side="left")
        txt = "   ".join(x for x in (f"Current streak: {cur} days" if cur > 1 else "", f"Longest streak: {longest} days" if longest else "") if x)
        ctk.CTkLabel(head, text=txt, text_color=MUTED).pack(side="left", padx=12)
        self.btn(head, "›", lambda: self._heat_move(-4), 30, primary=False).pack(side="right")
        self.btn(head, "‹", lambda: self._heat_move(4), 30, primary=False).pack(side="right", padx=4)
        bg = self._apply_appearance_mode(CARD)
        cv = tk.Canvas(heat, height=150, bg=bg, highlightthickness=0)
        cv.pack(fill="x", padx=14, pady=(0, 6))
        tip = ctk.CTkLabel(heat, text="Hover a day for details", text_color=MUTED, anchor="w")
        tip.pack(fill="x", padx=14, pady=(0, 10))
        dark = ctk.get_appearance_mode() == "Dark"
        shades = ["#2B2F38", "#1F3F7A", "#2856A8", "#2F6FED", "#7FA8FF"] if dark else ["#E3E6EB", "#C5D6FB", "#94B4F7", "#5E8DF2", "#2F6FED"]
        weeks = 22
        today = dt.date.today()
        end = today - dt.timedelta(weeks=self.heat_offset)
        start = end - dt.timedelta(days=(end.weekday() + 1) % 7 + 7 * (weeks - 1))
        last_month = None
        for i in range(weeks * 7):
            day = start + dt.timedelta(days=i)
            col, r = divmod(i, 7)
            x, y = 4 + col * 19, 20 + r * 18
            if r == 0 and day.month != last_month:
                last_month = day.month
                cv.create_text(x, 8, text=day.strftime("%b"), anchor="w", fill="#8A909B", font=(F, 9))
            if day > today:
                continue
            info = st["days"].get(day.isoformat(), {"words": 0, "apps": {}})
            w = info["words"]
            band = 0 if w == 0 else 1 if w < 250 else 2 if w < 500 else 3 if w < 750 else 4
            item = cv.create_rectangle(x, y, x + 14, y + 14, fill=shades[band], outline="")
            apps = info.get("apps", {})
            top = max(apps, key=apps.get) if apps else "—"
            msg = f"{day.strftime('%b %d, %Y')}: {w:,} words, {len(apps)} apps, top app {top}"
            cv.tag_bind(item, "<Enter>", lambda e, m=msg: tip.configure(text=m))

    def _heat_move(self, d):
        self.heat_offset = max(0, self.heat_offset + d)
        self.show("insights")

    # ---- settings
    def page_settings(self, p):
        self.h1(p, "Settings")
        f = ctk.CTkScrollableFrame(p, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=14, pady=8)
        cfg = self.cfg

        def section(text):
            ctk.CTkLabel(f, text=text, font=(F, 16, "bold"), anchor="w").pack(fill="x", padx=10, pady=(18, 6))

        def line(label, value=""):
            r = ctk.CTkFrame(f, fg_color=CARD)
            r.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(r, text=label, width=210, anchor="w").pack(side="left", padx=12, pady=8)
            if value:
                ctk.CTkLabel(r, text=value, text_color=MUTED, anchor="w").pack(side="left")
            return r

        def switch(label, get, put):
            r = line(label)
            v = tk.BooleanVar(value=get())
            ctk.CTkSwitch(r, text="", variable=v, width=40, progress_color=THEME["accent"],
                          command=lambda: (put(v.get()), self.save_cfg())).pack(side="right", padx=10)

        def option(label, values, get, put, width=220):
            r = line(label)
            om = ctk.CTkOptionMenu(r, values=values, width=width, fg_color=THEME["accent"],
                                   button_color=THEME["accent_hover"], command=lambda v: (put(v), self.save_cfg()))
            om.set(get())
            om.pack(side="right", padx=10)

        def key_row(label, name, test, link):
            r = line(label)
            v = tk.StringVar(value=self.dg_key if name == "deepgram" else self.ai_key)
            status = ctk.CTkLabel(f, text="", text_color=MUTED, anchor="w")

            def save():
                val = v.get().strip()
                c.set_key(cfg, name, val)
                if name == "deepgram":
                    self.dg_key = val
                else:
                    self.ai_key = val
                self.save_cfg()
                status.configure(text="Saved to Windows Credential Manager." if val else "Key removed.")

            def run_test():
                val = v.get().strip()
                if not val:
                    status.configure(text="Paste a key first.")
                    return
                status.configure(text="Testing…")
                threading.Thread(target=lambda: self.events.put(("ui", lambda m=test(val): status.winfo_exists() and status.configure(text=m))), daemon=True).start()

            self.btn(r, "Test", run_test, 56, primary=False).pack(side="right", padx=(4, 10))
            self.btn(r, "Save", save, 56).pack(side="right")
            ctk.CTkEntry(r, textvariable=v, show="•", width=250).pack(side="right", padx=8)
            lk = ctk.CTkLabel(f, text=link, text_color=THEME["accent"], anchor="w", cursor="hand2")
            lk.pack(fill="x", padx=22)
            lk.bind("<Button-1>", lambda e: webbrowser.open(link.split(": ", 1)[1]))
            status.pack(fill="x", padx=22)

        section("General")
        r = line("Shortcuts", f"Push to talk: {self.ptt_label()}")
        self.btn(r, "Change", self.dlg_shortcuts, 80).pack(side="right", padx=10)
        option("Microphone", ["Auto-detect"] + c.list_mics(), lambda: cfg["mic"], lambda v: cfg.__setitem__("mic", v), 300)
        langs = "Auto-detect" if cfg["auto_detect"] else ", ".join(dict(c.LANGUAGES).get(x, x) for x in cfg["languages"])
        r = line("Languages", langs)
        self.btn(r, "Change", self.dlg_languages, 80).pack(side="right", padx=10)

        def theme(v):
            cfg["theme"] = v
            ctk.set_appearance_mode(v)
            self.after(50, lambda: self.show("settings"))

        option("Theme", ["Light", "Dark", "System"], lambda: cfg["theme"], theme, 120)

        section("System")

        def autostart(v):
            cfg["autostart"] = v
            if not self.selftest:
                try:
                    c.set_autostart(v, SCRIPT)
                except Exception:
                    log.exception("autostart")

        switch("Launch at login", lambda: cfg["autostart"], autostart)
        switch("Start hidden in the tray", lambda: cfg["start_minimized"], lambda v: cfg.__setitem__("start_minimized", v))
        switch("Show Dictation Bar at all times", lambda: cfg["flowbar_always"],
               lambda v: (cfg.__setitem__("flowbar_always", v), cfg.__setitem__("flowbar_hidden_until", 0), self.bar.draw()))
        switch("Meme mode (the bar spins, bounces and flashes while you dictate)", lambda: cfg["meme_mode"],
               lambda v: (cfg.__setitem__("meme_mode", v), self.bar.reconfigure()))
        switch("Dictation sounds", lambda: cfg["sounds"], lambda v: cfg.__setitem__("sounds", v))
        for k, label in (("suggestions", "Notifications: suggestions"), ("announcements", "Notifications: announcements"),
                         ("milestones", "Notifications: milestones")):
            switch(label, lambda k=k: cfg["notif"][k], lambda v, k=k: cfg["notif"].__setitem__(k, v))
        option("Scratchpad opens with", ["Resume last note", "Open in new tab", "Open last active pinned note"],
               lambda: cfg["scratchpad_open"], lambda v: cfg.__setitem__("scratchpad_open", v), 240)

        section("Notetaker")
        option("When Teams, Zoom or Webex starts a call", ["Start automatically", "Ask me", "Do nothing"],
               lambda: cfg["meeting_detect"], lambda v: cfg.__setitem__("meeting_detect", v), 180)
        option("Transcribe meetings with", [c.LOCAL_ENGINE, c.CLOUD_ENGINE], lambda: cfg["meeting_engine"],
               lambda v: cfg.__setitem__("meeting_engine", v), 230)
        option("Speech model on this computer", ["small", "base"], lambda: cfg["local_model"],
               lambda v: cfg.__setitem__("local_model", v), 120)
        ctk.CTkLabel(f, text="\"small\" is more accurate; \"base\" is about twice as fast and lighter on the processor.",
                     text_color=MUTED, anchor="w").pack(fill="x", padx=22)
        switch("Skip silence", lambda: cfg["meeting_pause_silence"],
               lambda v: cfg.__setitem__("meeting_pause_silence", v))
        ctk.CTkLabel(f, text="Transcribes your microphone and the computer's audio. No audio is saved, only text. "
                             "Tell the other people they are being transcribed.",
                     text_color=MUTED, anchor="w", justify="left", wraplength=700).pack(fill="x", padx=22)

        section("Account")
        r = line("First name")
        nv = tk.StringVar(value=cfg["name"])
        nv.trace_add("write", lambda *a: (cfg.__setitem__("name", nv.get().strip()), self.save_cfg()))
        ctk.CTkEntry(r, textvariable=nv, width=220).pack(side="right", padx=10)
        key_row("Deepgram API key (speech)", "deepgram", c.test_deepgram_key, "Get a key: https://console.deepgram.com")
        key_row("Groq API key (AI features)", "groq", lambda k: c.test_ai_key(k, cfg["llm_model"]),
                "Get a key: https://console.groq.com/keys")
        ctk.CTkLabel(f, text="The Groq key powers full Auto Cleanup, Command Mode, Transforms and meeting summaries. Dictation works without it.",
                     text_color=MUTED, anchor="w").pack(fill="x", padx=22)

        section("Data & Privacy")
        switch("Context awareness (app name sent with cleanup)", lambda: cfg["context_awareness"],
               lambda v: cfg.__setitem__("context_awareness", v))

        def storage(v):
            cfg["history_storage"] = v
            self.history = c.purge(self.history, v)
            self.save_history()

        option("History and audio", ["Keep forever", "Keep 24 hours", "Never store"], lambda: cfg["history_storage"], storage, 160)
        r = line("Delete history", "Removes all transcripts and saved audio")
        self.btn(r, "Delete", self._delete_history, 80, primary=False).pack(side="right", padx=10)
        r = line("Export my data", "Settings, history, notes and stats as one file")
        self.btn(r, "Export", self._export, 80, primary=False).pack(side="right", padx=10)
        r = line("Data folder", str(c.DATA_DIR))
        self.btn(r, "Open", lambda: os.startfile(c.DATA_DIR), 80, primary=False).pack(side="right", padx=10)

        section("Experimental")
        switch("Command Mode (hold " + self.sc_label("command") + ")", lambda: cfg["command_mode"],
               lambda v: cfg.__setitem__("command_mode", v))
        switch("Press Enter command (say \"press enter\" at the end)", lambda: cfg["press_enter"],
               lambda v: cfg.__setitem__("press_enter", v))

        section("Advanced")
        for label, key, vals in (("Speech model", "dg_model", ["nova-3", "nova-3-medical", "nova-2"]),
                                 ("AI model", "llm_model", ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"])):
            r = line(label)
            v = tk.StringVar(value=cfg[key])
            v.trace_add("write", lambda *a, key=key, v=v: (cfg.__setitem__(key, v.get().strip()), self.save_cfg()))
            ctk.CTkComboBox(r, values=vals, variable=v, width=220).pack(side="right", padx=10)
        option("Insert text by", ["Paste", "Type"], lambda: cfg["output"], lambda v: cfg.__setitem__("output", v), 120)
        switch("Add a space after each dictation", lambda: cfg["trailing_space"], lambda v: cfg.__setitem__("trailing_space", v))

        def keep(v):
            cfg["keep_mic_open"] = v
            if v:
                self._warm_mic()
            elif self.phase == "idle":
                self.recorder.close()

        switch("Keep the microphone open (never misses the first word)", lambda: cfg["keep_mic_open"], keep)

    def _delete_history(self):
        if not messagebox.askyesno("Delete history", "Delete all transcripts and saved audio? This can't be undone.", parent=self):
            return
        self.history, self.last_entry = [], None
        for fpath in c.AUDIO_DIR.glob("*.wav"):
            try:
                fpath.unlink()
            except Exception:
                pass
        self.save_history()
        self.notify("History deleted")

    def _export(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialfile="dictation-tool-export.json")
        if path:
            data = {"settings": {k: v for k, v in self.cfg.items() if k != "key_fallback"}, "history": self.history,
                    "notes": self.notes, "stats": self.stats}
            c.save_json(Path(path), data)
            self.notify("Export saved")

    def dlg_languages(self):
        d = self._dialog("My languages", 420, 560)
        auto = tk.BooleanVar(value=self.cfg["auto_detect"])
        picks = {code: tk.BooleanVar(value=code in self.cfg["languages"]) for code, _ in c.LANGUAGES}
        ctk.CTkSwitch(d, text="Auto-detect", variable=auto, progress_color=THEME["accent"]).pack(anchor="w", padx=20, pady=(16, 4))
        ctk.CTkLabel(d, text="One language is detected per dictation. Auto-detect works best for mixed English and Spanish.",
                     text_color=MUTED, wraplength=370, justify="left").pack(anchor="w", padx=20)
        box = ctk.CTkScrollableFrame(d)
        box.pack(fill="both", expand=True, padx=16, pady=10)
        for code, name in c.LANGUAGES:
            ctk.CTkCheckBox(box, text=name, variable=picks[code], fg_color=THEME["accent"]).pack(anchor="w", pady=3)
        msg = ctk.CTkLabel(d, text="", text_color=THEME["danger"])
        msg.pack()

        def save():
            chosen = [code for code, v in picks.items() if v.get()]
            if not auto.get() and not chosen:
                msg.configure(text="Please select at least one language or turn on auto-detect.")
                return
            self.cfg["auto_detect"] = auto.get()
            self.cfg["languages"] = chosen or ["en"]
            self.save_cfg()
            d.destroy()
            if self.page == "settings":
                self.show("settings")

        self.btn(d, "Save and close", save, 130).pack(pady=(0, 14))

    # ---- shortcuts
    def record_shortcut(self, action, index, pill, single=False, on_done=None):
        """Capture the next key combination and store it as a binding for `action`."""
        old = pill.cget("text")
        pill.configure(text="Press keys…  (Esc cancels)")

        def got(keys):
            self.capture_cb = None
            if not pill.winfo_exists():
                return
            if not keys:
                pill.configure(text=old)
                return
            sc = self.cfg["shortcuts"]
            err = c.validate_binding(action, keys, sc, skip=(action, index))
            if err:
                pill.configure(text=old)
                self.notify(err)
                if on_done:
                    on_done(err)
                return
            lists = sc.setdefault(action, [])
            if single:
                sc[action] = [keys]
            elif index < len(lists):
                lists[index] = keys
            else:
                lists.append(keys)
            self.apply_bindings()
            self.save_cfg()
            pill.configure(text=c.combo_label(keys))
            hf, ptt = sc.get("handsfree", []), sc.get("ptt", [])
            if action in ("handsfree", "ptt") and any(set(a) <= set(b) for a in hf for b in ptt):
                self.notify("Shortcut saved", "Hands-free overlaps with push to talk, so both may trigger.")
            else:
                self.notify("Shortcut saved", timeout=1800)
            if on_done:
                on_done(None)

        self.capture_cb = got
        self.hook.start_capture()

    def dlg_shortcuts(self):
        d = self._dialog("Shortcuts", 640, 560)
        body = ctk.CTkScrollableFrame(d, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10, pady=10)
        sc = self.cfg["shortcuts"]

        def render(*a):
            for w in body.winfo_children():
                w.destroy()
            for act, label, lo, hi in c.ACTIONS:
                row = ctk.CTkFrame(body, fg_color=CARD)
                row.pack(fill="x", padx=4, pady=3)
                ctk.CTkLabel(row, text=label, width=170, anchor="w", font=(F, 13, "bold")).pack(side="left", padx=12, pady=10)
                lists = sc.setdefault(act, [])
                cell = ctk.CTkFrame(row, fg_color="transparent")
                cell.pack(side="left", fill="x", expand=True)
                for i, keys in enumerate(lists):
                    one = ctk.CTkFrame(cell, fg_color="transparent")
                    one.pack(anchor="w", pady=2)
                    pill = self.btn(one, c.combo_label(keys), None, 200, primary=False)
                    pill.configure(command=lambda act=act, i=i, pill=pill: self.record_shortcut(act, i, pill, on_done=lambda e: None))
                    pill.pack(side="left")
                    if len(lists) > lo:
                        self.btn(one, "🗑", lambda act=act, i=i: (sc[act].pop(i), self.apply_bindings(), self.save_cfg(), render()),
                                 30, primary=False).pack(side="left", padx=4)
                if len(lists) < hi:
                    text = "Click to add a shortcut" if not lists else "+"
                    add = self.btn(cell, text, None, 200 if not lists else 34, primary=False)
                    add.configure(command=lambda act=act, add=add: self.record_shortcut(act, len(sc[act]), add, on_done=lambda e: self.after(300, render)))
                    add.pack(anchor="w", pady=2)

        render()
        ctk.CTkLabel(d, text="Hold the modifier, press the second key, release the modifier last. Up to three keys.",
                     text_color=MUTED).pack(padx=16, anchor="w")
        foot = ctk.CTkFrame(d, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=12)

        def reset():
            for act, lists in c.DEFAULT_SHORTCUTS.items():
                sc[act] = c.json.loads(c.json.dumps(lists))
            for k in [k for k in sc if k.startswith("tf:")]:
                sc.pop(k)
            self.apply_bindings()
            self.save_cfg()
            render()

        def close():
            self.hook.cancel_capture()
            self.capture_cb = None
            d.destroy()
            if self.winfo_viewable():
                self.show(self.page)

        self.btn(foot, "Reset to default", reset, 130, primary=False).pack(side="left")
        self.btn(foot, "Done", close, 80).pack(side="right")
        d.protocol("WM_DELETE_WINDOW", close)

    # ---- help
    def page_help(self, p):
        self.h1(p, "Help")
        f = ctk.CTkScrollableFrame(p, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=14, pady=8)
        rows = [(label, self.sc_label(act)) for act, label, lo, hi in c.ACTIONS]
        rows += [("Polish selected text", self.sc_label("polish")), ("Prompt Engineer", self.sc_label("prompt_engineer"))]
        ctk.CTkLabel(f, text="Your shortcuts", font=(F, 15, "bold"), anchor="w").pack(fill="x", padx=10, pady=(6, 4))
        for a, b in rows:
            r = ctk.CTkFrame(f, fg_color=CARD)
            r.pack(fill="x", padx=10, pady=1)
            ctk.CTkLabel(r, text=a, width=240, anchor="w").pack(side="left", padx=12, pady=6)
            ctk.CTkLabel(r, text=b, font=(F, 13, "bold")).pack(side="left")
        tips = ("Push to talk: hold, speak, release. A tap under half a second doesn't paste.\n"
                "Hands-free: press the shortcut, double-tap push to talk, or click the Dictation Bar. Press again to stop. "
                "A press within half a second of starting cancels.\n"
                "Say \"new line\" or \"new paragraph\" for line breaks. Say \"scratch that\" to drop what you just said.\n"
                "A recording stops at 20 minutes, with a warning at 19.\n"
                "If text didn't land, use Paste last transcript, or find it under Home.\n"
                "Drag the Dictation Bar to the bottom, left or right edge. Right-click it to hide it for an hour or pick a microphone.")
        ctk.CTkLabel(f, text="How it works", font=(F, 15, "bold"), anchor="w").pack(fill="x", padx=10, pady=(16, 4))
        ctk.CTkLabel(f, text=tips, anchor="w", justify="left", wraplength=700).pack(fill="x", padx=10)
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=16)
        self.btn(row, "Open log", lambda: os.startfile(c.LOG_FILE), 100, primary=False).pack(side="left")
        self.btn(row, "Open data folder", lambda: os.startfile(c.DATA_DIR), 140, primary=False).pack(side="left", padx=8)

    # ========================================================= tray, window
    def _start_tray(self):
        if not pystray or self.selftest:
            return
        try:
            img = Image.open(c.resource("dictation_tool.png")).convert("RGBA").resize((64, 64), Image.LANCZOS)
            put = lambda what: (lambda: self.events.put(("tray", what)))
            menu = pystray.Menu(
                pystray.MenuItem("Open Dictation Tool", put("open"), default=True),
                pystray.MenuItem("Shortcuts", put("shortcuts")),
                pystray.MenuItem("Paste last transcript", put("paste")),
                pystray.MenuItem("Scratchpad", put("pad")),
                pystray.MenuItem("Start / stop meeting transcription", put("meeting"), checked=lambda item: bool(self.meeting)),
                pystray.MenuItem("Pause dictation", put("pause"), checked=lambda item: self.paused),
                pystray.MenuItem("Help", put("help")),
                pystray.MenuItem("Quit", put("quit")))
            self.tray = pystray.Icon(APP, img, APP, menu)
            self.tray.run_detached()
        except Exception:
            log.exception("tray")
            self.tray = None

    def hide_window(self):
        if self.tray:
            self.withdraw()
        else:
            self.iconify()

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def quit_app(self):
        if self._save_job:
            self.after_cancel(self._save_job)
            self._save_now()
        try:
            if self.meeting:
                self.stop_meeting(quiet=True)
            self.pad.save_now()
            if self.session:
                self.session.cancel()
            self.recorder.close()
            self.hook.stop()
            if self.tray:
                self.tray.stop()
            if not self.selftest:
                c.save_json(c.STATS_FILE, self.stats)
        except Exception:
            log.exception("quit")
        self.destroy()

    # ============================================================ dictation
    def _press(self, mode):
        """Push-to-talk style press for dictation or Command Mode."""
        now = time.time()
        if self.phase == "proc" or self.tf_busy:
            self.notify("Transcript currently processing", timeout=2500)
            return
        if self.phase == "idle":
            self.start_recording(mode, locked=False)
            return
        r = self.rec
        if r["tap_job"]:  # second press right after a tap: lock hands-free
            self.after_cancel(r["tap_job"])
            r["tap_job"] = None
            r["locked"], r["lock_t"] = True, now
            self.bar.set_state("rec", True)
        elif r["locked"]:
            if now - r["lock_t"] < 0.5:
                self.cancel_dictation(quiet=True)
            else:
                self.stop_recording()
        elif r["mode"] != mode and now - r["t0"] < 1.0:
            r["mode"] = mode if self._mode_ok(mode) else r["mode"]

    def _release(self, mode):
        r = self.rec
        if self.phase != "rec" or r["locked"] or r["tap_job"] or r["mode"] != mode:
            return
        if time.time() - r["t0"] < 0.5:
            r["tap_job"] = self.after(500, self._tap_timeout)
        else:
            self.after(70, self.stop_recording)  # keep the tail of the last word

    def _tap_timeout(self):
        if self.phase == "rec" and self.rec["tap_job"]:
            self.rec["tap_job"] = None
            self.cancel_dictation(quiet=True)
            if self.cfg["notif"]["suggestions"]:
                self.notify("Hold, don't tap", f"Hold {self.ptt_label()} while you speak, or double-tap it for hands-free.", timeout=3500)

    def _handsfree(self):
        now = time.time()
        if self.phase == "proc" or self.tf_busy:
            self.notify("Transcript currently processing", timeout=2500)
        elif self.phase == "idle":
            self.start_recording("dictate", locked=True)
        else:
            r = self.rec
            if not r["locked"]:
                if r["tap_job"]:
                    self.after_cancel(r["tap_job"])
                    r["tap_job"] = None
                r["locked"], r["lock_t"] = True, now
                self.bar.set_state("rec", True)
            elif now - r["lock_t"] < 0.5:
                self.cancel_dictation(quiet=True)
            else:
                self.stop_recording()

    def flowbar_click(self):
        self._handsfree()

    def _mode_ok(self, mode) -> bool:
        if mode != "command":
            return True
        if not self.cfg["command_mode"]:
            self.notify("Command mode is toggled off", actions=[("Go to settings", lambda: self.open_page("settings"))])
            return False
        if not self.ai_key:
            self.notify("Command Mode needs an AI key", "Add one in Settings → Account.",
                        actions=[("Go to settings", lambda: self.open_page("settings"))])
            return False
        return True

    def start_recording(self, mode, locked):
        if self.paused:
            return
        if not self.dg_key:
            self.notify("Add your Deepgram API key", "Dictation needs it to transcribe.",
                        actions=[("Open settings", lambda: self.open_page("settings"))])
            return
        if not self._mode_ok(mode):
            return
        app = c.foreground_app()
        self.sid += 1
        sid = self.sid
        session = c.Session(sid, self.dg_key, self.cfg, self.events.put)
        if self._mic_close_job:
            self.after_cancel(self._mic_close_job)
            self._mic_close_job = None
        now = time.time()
        self.session = session
        self.phase = "rec"
        self.rec = {"mode": mode, "locked": locked, "t0": now, "lock_t": now if locked else 0, "tap_job": None, "app": app}
        self.hook.cancel_active = True
        self.bar.dismiss()
        self.bar.set_state("rec", locked)  # the bar reacts on the key press, before the mic is even open
        session.start()  # starts connecting to Deepgram straight away

        def open_mic():
            try:
                self.recorder.start(session.feed, self.cfg["mic"])
                if self.sid != sid or self.phase != "rec":  # already released or cancelled
                    self.recorder.stop(True)
                self.events.put(("mic_ok", sid))
            except Exception as e:
                log.exception("mic")
                self.recorder.close()
                self.events.put(("mic_fail", sid, str(e)))

        threading.Thread(target=open_mic, daemon=True).start()
        self._limit_jobs = [
            self.after(c.WARN_SESSION * 1000, lambda: self.notify("One minute left", "Dictation Tool supports up to 20 minutes per session.")),
            self.after(c.MAX_SESSION * 1000, self._limit_stop)]
        if mode == "command":
            self.notify("Using Command Mode" + ("" if self.cfg["command_seen"] else " (beta)"),
                        "Speak an instruction, then release.", timeout=2500)
            self.cfg["command_seen"] = True
            self.save_cfg()

    def _spent(self, kind, secs, rate):
        """Count audio sent to Deepgram toward the spend estimate."""
        c.add_spend(self.stats, kind, max(0.0, secs), rate)
        if not self.selftest:
            try:
                c.save_json(c.STATS_FILE, self.stats)
            except Exception:
                log.exception("stats")

    def _caps_off(self):
        """Caps Lock is the dictation key, so it must never be left switched on."""
        try:
            if c.caps_is_shortcut(self.cfg["shortcuts"]) and c.caps_lock_on():
                c.tap(c.VK_CAPS)  # a synthetic tap: the hook lets it through, and it flips Caps Lock back off
                log.info("Caps Lock was on; switched it off")
        except Exception:
            log.exception("caps guard")

    def _caps_guard(self):
        if self.phase != "rec":  # not while the key is being held
            self._caps_off()
        self.after(1000, self._caps_guard)

    def _mic_failed(self, sid, msg):
        if sid != self.sid or self.phase != "rec":
            return
        self._clear_rec()
        self.session.cancel()
        self._idle()
        self.bar.flash("err")
        msg = msg.lower()
        if "invalid device" in msg or "no default" in msg or "-1" in msg:
            self.notify("No microphone detected", "Connect a microphone, then try again.",
                        actions=[("Open Sound Settings", lambda: os.startfile("ms-settings:sound"))])
        else:
            self.notify("Microphone unavailable", "Close other apps using the mic, or pick another one in Settings.",
                        actions=[("Choose Microphone", lambda: self.open_page("settings"))])

    def _release_mic(self):
        """Stop listening but keep the mic warm for 30 seconds, so the next dictation starts instantly."""
        self.recorder.stop(True)
        if not self.cfg["keep_mic_open"]:
            if self._mic_close_job:
                self.after_cancel(self._mic_close_job)
            self._mic_close_job = self.after(30000, self._close_mic)

    def _close_mic(self):
        self._mic_close_job = None
        if self.phase != "rec" and not self.cfg["keep_mic_open"]:
            self.recorder.close()

    def _limit_stop(self):
        if self.phase == "rec":
            self.stop_recording()
            self.notify("Dictation Tool supports up to 20 minutes per session.", "Your text is being transcribed.")

    def _clear_rec(self):
        for j in self._limit_jobs:
            self.after_cancel(j)
        self._limit_jobs = []
        if self.rec and self.rec.get("tap_job"):
            self.after_cancel(self.rec["tap_job"])
            self.rec["tap_job"] = None

    def _idle(self):
        self.phase = "idle"
        self.hook.cancel_active = self.tf_busy
        self.bar.set_state("rest")

    def stop_recording(self):
        if self.phase != "rec":
            return
        self._clear_rec()
        r = self.rec
        self._release_mic()
        r["secs"] = time.time() - r["t0"]; self._spent("dictation", r["secs"], c.dictation_rate(self.cfg))
        if self.recorder.peak < 0.006:
            self.session.cancel()
            self._idle()
            self.bar.flash("err")
            self.notify("Is your microphone muted?", "No audio was detected, so nothing was sent.",
                        actions=[("Choose Microphone", lambda: self.open_page("settings"))])
            return
        self.session.finish()
        r["t_stop"] = time.time()
        self.phase = "proc"
        self._beep(660)
        self.bar.set_state("proc")

    def cancel_dictation(self, quiet=False):
        """Esc, the X on the Dictation Bar, or a quick repeat press."""
        if self.tf_busy:
            self.tf_token += 1
            self.tf_busy = False
            self._idle()
            self.notify("Polish cancelled", timeout=2000)
            return
        if self.phase == "idle":
            return
        self._clear_rec()
        r = self.rec
        if self.retrying or r is None:  # cancelling a retry: just drop its result
            self.retrying = False
            self.sid += 1
            self._idle()
            return
        if self.phase == "rec":
            self._release_mic()
            r["secs"] = time.time() - r["t0"]; self._spent("dictation", r["secs"], c.dictation_rate(self.cfg))
        self.session.cancel()
        self.sid += 1
        pcm = bytes(self.session.pcm)
        self._idle()
        if quiet or r.get("secs", 0) < 0.5:
            return
        entry = self._new_entry(r, status="cancelled")
        self._store(entry, pcm)
        self.notify("Dictation cancelled", actions=[("Undo", lambda: self.retry_entry(entry, paste=True, pcm=pcm))])

    def _new_entry(self, r, raw="", text="", status="done") -> dict:
        app = r["app"]
        return {"id": f"{int(time.time() * 1000)}", "ts": time.time(), "raw": raw, "text": text, "status": status,
                "mode": r["mode"], "app": app["name"], "exe": app["exe"], "title": app["title"] if self.cfg["context_awareness"] else "",
                "cat": app["insight"], "words": c.count_words(text), "secs": round(r.get("secs", 0), 1)}

    def _store(self, entry, pcm=None):
        self.last_entry = entry
        if self.cfg["history_storage"] == "Never store":
            return
        is_new = entry not in self.history
        if is_new:
            self.history.insert(0, entry)
            del self.history[2000:]
        if pcm and not self.selftest:  # disk work stays off the UI thread
            threading.Thread(target=c.save_audio, args=(entry["id"], pcm), daemon=True).start()
        self.save_history()
        if is_new:
            self.after(600, lambda: self._home_add(entry))  # after the text has landed, and only one row
        elif self.page == "home":
            self.refresh_soon()

    def _on_done(self, sid, raw, err):
        if sid != self.sid or self.phase != "proc":
            return
        r = self.rec
        r["t_text"] = time.time()
        pcm = bytes(self.session.pcm)
        if err:
            entry = self._new_entry(r, status="failed")
            self._store(entry, pcm)
            self._idle()
            self.bar.flash("err")
            self.notify("Transcription failed", err, timeout=9000,
                        actions=[("Retry", lambda: self.retry_entry(entry, paste=True, pcm=pcm)),
                                 ("Open History", lambda: self.open_page("home"))])
            return
        if not raw.strip():
            self._idle()
            self.notify("Didn't catch that", timeout=1800)
            return
        threading.Thread(target=self._process, args=(sid, raw, r, pcm), daemon=True).start()

    def _process(self, sid, raw, r, pcm):
        """Worker thread: cleanup or command, then hand the result back to the UI thread."""
        try:
            if r["mode"] == "command":
                sel = c.get_selection()
                kind, out = c.run_command(self.ai_key, self.cfg["llm_model"], raw, sel)
                res = {"text": out, "kind": kind, "enter": False, "dict_fixes": 0, "words_fixed": 0, "ai": True}
            else:
                res = c.process_dictation(raw, self.cfg, self.ai_key, r["app"])
                res["kind"] = "paste"
            self.events.put(("final", sid, raw, res, r, pcm, None))
        except Exception as e:
            log.exception("process")
            self.events.put(("final", sid, raw, None, r, pcm, c.friendly_error(e)))

    def _on_final(self, sid, raw, res, r, pcm, err):
        if sid != self.sid:
            return
        self._idle()
        if err or res is None:
            title = "Command failed" if r["mode"] == "command" else "Formatting failed"
            self.notify(title, err or "", timeout=7000)
            if r["mode"] != "command":
                res = {"text": raw, "kind": "paste", "enter": False, "dict_fixes": 0, "words_fixed": 0}
            else:
                return
        text = res["text"]
        if res["kind"] == "search":
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(text))
        elif text:
            self.bar.flash("ok")
            self._insert(text, r["app"]["exe"], res.get("enter", False), space=r["mode"] != "command")
        elif r["mode"] == "command":
            self.notify("Command returned no text")
        entry = self._new_entry(r, raw=raw, text=text)
        if "t_stop" in r and "t_text" in r:
            now = time.time()
            entry["ms"] = int((now - r["t_stop"]) * 1000)
            log.info("dictation: %.1fs of audio, transcript %dms, cleanup %dms, release-to-text %dms%s",
                     r.get("secs", 0), (r["t_text"] - r["t_stop"]) * 1000, (now - r["t_text"]) * 1000, entry["ms"],
                     " (AI cleanup)" if res.get("ai") else "")
        self._store(entry, pcm)
        if r["mode"] != "command" and text:
            self._count(entry, res)

    def _insert(self, text, exe="", enter=False, space=True):
        if space and self.cfg["trailing_space"] and not enter and not text.endswith(("\n", " ")):
            text += " "
        threading.Thread(target=self._insert_thread, args=(text, exe, enter), daemon=True).start()

    def _insert_thread(self, text, exe, enter):
        try:
            c.insert_text(text, self.cfg["output"], exe, enter)
        except Exception:
            log.exception("insert")
            self.events.put(("ui", lambda: self.notify("Dictation Tool can't paste right now. Text saved to clipboard",
                                                       f"Press {self.sc_label('paste_last')} or Ctrl + V in a text field.")))

    def _count(self, entry, res):
        before = self.stats["total_words"]
        c.record_stats(self.stats, entry["words"], entry["secs"], entry["app"], entry["cat"],
                       res.get("words_fixed", 0), res.get("dict_fixes", 0))
        if not self.selftest:
            try:
                c.save_json(c.STATS_FILE, self.stats)
            except Exception:
                log.exception("stats")
        if res.get("dict_fixes"):
            self.save_cfg()
        if self.cfg["notif"]["milestones"]:
            for m in (1000, 5000, 10000, 25000, 50000, 100000, 250000):
                if before < m <= self.stats["total_words"] and m not in self.cfg["milestones_seen"]:
                    self.cfg["milestones_seen"].append(m)
                    self.save_cfg()
                    self.after(1500, lambda m=m: self.notify(f"🎉 {m:,} words dictated", "Nice work. See Insights for your stats."))

    # ---- retry, paste last, copy last
    def retry_entry(self, entry, paste=False, pcm=None):
        if self.phase != "idle":
            self.notify("Cannot retry while dictating")
            return
        pcm = pcm or c.load_audio(entry["id"])
        if not pcm:
            self.notify("Retry failed. Please try again.", "The audio for this dictation is no longer saved.")
            return
        self.bar.set_state("proc")
        self.phase = "proc"
        self.retrying = True
        self.sid += 1
        sid = self.sid
        self.hook.cancel_active = True
        app = c.classify_app(entry.get("exe", ""), entry.get("title", ""))

        def work():
            try:
                raw = c.transcribe_pcm(self.dg_key, self.cfg, pcm)
                res = c.process_dictation(raw, self.cfg, self.ai_key, app) if raw.strip() else None
                self.events.put(("retried", sid, entry, raw, res, paste, None))
            except Exception as e:
                log.exception("retry")
                self.events.put(("retried", sid, entry, "", None, paste, c.friendly_error(e)))

        threading.Thread(target=work, daemon=True).start()

    def _on_retried(self, sid, entry, raw, res, paste, err):
        if sid != self.sid:
            return
        self.retrying = False
        self._idle()
        if err or not res or not res["text"]:
            self.notify("Retry failed. Please try again.", err or "No speech was found in the recording.")
            return
        first = entry.get("status") != "done"
        entry.update(raw=raw, text=res["text"], status="done", words=c.count_words(res["text"]), show_raw=False)
        self._store(entry)
        if first:
            self._count(entry, res)
        if paste:
            self._insert(res["text"], entry.get("exe", ""))
        else:
            self.notify("Transcript updated", "Copy it from Home.", timeout=2500)

    def paste_last(self):
        if self.phase == "proc":
            self.sid += 1
            self.session and self.session.cancel()
            self._idle()
        e = self.last_entry
        if not e:
            self.notify("No transcript yet", "Dictate something first.")
        elif e.get("status") != "done":
            self.retry_entry(e, paste=True)
        else:
            self._insert(self.entry_text(e), space=False)

    def copy_last(self):
        e = self.last_entry
        if e and e.get("status") == "done":
            pyperclip.copy(self.entry_text(e))
            self.notify("Copied last transcript", timeout=1800)
        else:
            self.notify("No transcript yet")

    # ---- transforms
    def run_transform(self, tf_id, text=None):
        if not self.cfg["transforms_on"] or self.tf_busy:
            return
        if self.phase != "idle":
            self.notify("Finish dictating first")
            return
        spec = c.transform_spec(self.cfg, tf_id)
        if not spec:
            self.notify("This transform isn't finished", "It needs a name and a prompt.",
                        actions=[("Configure", lambda: self.open_page("transforms"))])
            return
        if not self.ai_key:
            self.notify("Transforms need an AI key", "Add one in Settings → Account.",
                        actions=[("Go to settings", lambda: self.open_page("settings"))])
            return
        self.tf_busy = True
        self.tf_token += 1
        token = self.tf_token
        self.hook.cancel_active = True
        self.bar.set_state("proc")
        exe = c.foreground_app()["exe"]

        def work():
            try:
                src = text if text is not None else c.get_selection()
                if not src.strip():
                    return self.events.put(("tf", token, "empty", None))
                if c.count_words(src) > 3000:
                    return self.events.put(("tf", token, "long", None))
                out = c.transform_text(self.ai_key, self.cfg["llm_model"], spec[1], src, spec[2])
                self.events.put(("tf", token, "ok", {"orig": src, "new": out, "name": spec[0], "id": tf_id, "exe": exe,
                                                    "paste": text is None}))
            except Exception as e:
                log.exception("transform")
                self.events.put(("tf", token, "error", c.friendly_error(e)))

        threading.Thread(target=work, daemon=True).start()

    def _on_tf(self, token, status, data):
        if token != self.tf_token or not self.tf_busy:
            return
        self.tf_busy = False
        self._idle()
        if status == "empty":
            self.notify("Hey, select text first!", "Select text to apply a transform.")
        elif status == "long":
            self.notify("Oops, too long to polish", "Try again with under 3000 words")
        elif status == "error":
            self.notify("Transform failed", data, timeout=7000)
        elif not data["new"].strip():
            self.notify("Transform returned no text")
        elif data["new"].strip() == data["orig"].strip():
            self.notify("Your text looks good!", timeout=2500)
        else:
            self.last_tf = data
            if data["paste"]:
                self._insert(data["new"], data["exe"], space=False)
                self.notify("Done.", actions=[("See changes", self.view_changes), ("Undo", self.undo_transform)], timeout=8000)
            else:
                pyperclip.copy(data["new"])
            if self.changes_win and self.changes_win.winfo_exists():
                self.view_changes()

    def undo_transform(self):
        threading.Thread(target=lambda: (c.wait_mods_released(), c.chord(c.VK_CTRL, 0x5A)), daemon=True).start()

    def view_changes(self):
        t = self.last_tf
        if not t:
            self.notify("No transform to show yet")
            return
        if self.changes_win and self.changes_win.winfo_exists():
            self.changes_win.destroy()
        d = self._dialog(f"{t['name']}: changes", 860, 520)
        self.changes_win = d
        cols = ctk.CTkFrame(d, fg_color="transparent")
        cols.pack(fill="both", expand=True, padx=12, pady=12)
        cols.grid_columnconfigure((0, 1), weight=1, uniform="c")
        cols.grid_rowconfigure(1, weight=1)
        for i, (label, body) in enumerate((("Original", t["orig"]), ("Rewritten", t["new"]))):
            ctk.CTkLabel(cols, text=label, font=(F, 13, "bold"), anchor="w").grid(row=0, column=i, sticky="w", padx=6)
            tb = ctk.CTkTextbox(cols, wrap="word")
            tb.grid(row=1, column=i, sticky="nsew", padx=6, pady=4)
            tb.insert("1.0", body)
            tb.configure(state="disabled")
        foot = ctk.CTkFrame(d, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=(0, 12))
        self.btn(foot, "Copy", lambda: pyperclip.copy(t["new"]), 70).pack(side="left")
        self.btn(foot, "Retry", lambda: self.run_transform(t["id"], text=t["orig"]), 70, primary=False).pack(side="left", padx=6)
        ctk.CTkLabel(foot, text="Re-transform:", text_color=MUTED).pack(side="left", padx=(16, 4))
        for name in ["Prompt Engineer"] + list(c.RETRANSFORMS):
            tid = "prompt_engineer" if name == "Prompt Engineer" else name
            self.btn(foot, name, lambda tid=tid: self.run_transform(tid, text=t["new"]), 30, primary=False).pack(side="left", padx=2)
        ctk.CTkLabel(d, text="Retry and re-transform results are copied to your clipboard.", text_color=MUTED).pack(pady=(0, 8))

    # ============================================================== events
    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                try:
                    self._dispatch(ev)
                except Exception:
                    log.exception("event %s", ev[0])
        except queue.Empty:
            pass
        self.after(10, self._poll)

    def _dispatch(self, ev):
        kind = ev[0]
        if kind == "level":
            if self.phase == "rec":
                self.bar.level(ev[1])
            return
        if self.selftest and kind in ("sc_down", "sc_up", "cancel"):
            self.selftest_events.append(ev[:2])
            return
        if kind == "sc_down":
            act = ev[1]
            if ev[2]:
                c.tap(c.VK_MASK)  # stops Win/Alt from opening menus on release
            if act == "ptt":
                self._press("dictate")
            elif act == "command":
                self._press("command")
            elif act == "handsfree":
                self._handsfree()
            elif act == "paste_last":
                self.paste_last()
            elif act == "copy_last":
                self.copy_last()
            elif act == "scratchpad":
                self.toggle_scratchpad()
            elif act == "view_changes":
                self.view_changes()
            elif act == "meeting":
                self.toggle_meeting()
            else:
                self.run_transform(act)
        elif kind == "sc_up":
            if ev[1] == "ptt":
                self._release("dictate")
            elif ev[1] == "command":
                self._release("command")
            self.after(60, self._caps_off)  # right after every release, make sure Caps Lock is off
        elif kind == "cancel":
            self.cancel_dictation()
        elif kind == "done":
            self._on_done(ev[1], ev[2], ev[3])
        elif kind == "final":
            self._on_final(*ev[1:])
        elif kind == "retried":
            self._on_retried(*ev[1:])
        elif kind == "tf":
            self._on_tf(*ev[1:])
        elif kind == "captured":
            if self.capture_cb:
                self.capture_cb(ev[1])
        elif kind == "mic_ok":
            if ev[1] == self.sid:
                self._beep(880)
        elif kind == "mic_fail":
            self._mic_failed(ev[1], ev[2])
        elif kind == "mt":
            self._on_mt(ev[1], ev[2], ev[3], ev[4] if len(ev) > 4 else None)
        elif kind == "mt_error":
            if self.meeting and self.meeting["id"] == ev[1]:
                self.notify("Meeting transcription problem", f"{ev[2]}: {ev[3]}", timeout=9000)
        elif kind == "ui":
            ev[1]()
        elif kind == "tray":
            what = ev[1]
            if what == "open":
                self.show_window()
            elif what == "shortcuts":
                self.show_window()
                self.dlg_shortcuts()
            elif what == "paste":
                self.paste_last()
            elif what == "pad":
                self.toggle_scratchpad(force_show=True)
            elif what == "meeting":
                self.toggle_meeting()
            elif what == "help":
                self.open_page("help")
            elif what == "pause":
                self.paused = not self.paused
                self.hook.enabled = not self.paused
                if self.paused:
                    self.cancel_dictation(quiet=True)
            elif what == "quit":
                self.quit_app()
