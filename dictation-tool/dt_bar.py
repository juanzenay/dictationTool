"""dictation-tool Dictation Bar: an animated, glowing pill drawn every frame and shown with per-pixel alpha.

It never takes keyboard focus, and its transparent pixels are click-through. It fades in on the key
press and fades out when the text has landed.
States: rest, hover, recording (hold or hands-free), processing, and short success / error flashes.
Meme mode (a setting): while recording, the pill spins, bounces, flashes and throws emoji.
"""
from __future__ import annotations

import ctypes
import math
import random
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import dt_core as c
from dt_core import log

user32 = c.user32
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.c_void_p, wintypes.DWORD]
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(wintypes.POINT),
                                       ctypes.POINTER(wintypes.SIZE), ctypes.c_void_p, ctypes.POINTER(wintypes.POINT),
                                       wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL

PALETTES = {
    "aurora": [(0, 229, 255), (124, 92, 255), (255, 79, 216), (255, 170, 60)],
    "gold": [(255, 196, 61), (255, 122, 40), (255, 232, 150), (255, 150, 60)],
    "ok": [(60, 255, 170), (0, 229, 255), (150, 255, 120)],
    "err": [(255, 80, 90), (255, 150, 80), (255, 60, 120)],
    "rainbow": [(255, 40, 60), (255, 200, 0), (60, 255, 90), (0, 200, 255), (160, 70, 255), (255, 60, 220)],
    "toxic": [(170, 255, 0), (0, 255, 200), (255, 255, 0), (0, 255, 90)],
    "hot": [(255, 0, 110), (255, 120, 0), (255, 240, 0), (255, 0, 200)],
}
MEME_PALETTES = ["rainbow", "toxic", "hot", "aurora", "gold"]
MEME_WORDS = ["LET HIM COOK", "YAPPING", "GO OFF", "LOCKED IN", "SPITTING", "NO THOUGHTS", "CERTIFIED YAP"]
MEME_EMOJI = ["😂", "🔥", "💯", "🚀", "💀", "✨", "🗣️", "🤯"]
SIZES = {"rest": (56, 14), "hover": (296, 34), "rec": (180, 36), "locked": (252, 38), "proc": (112, 32),
         "ok": (60, 32), "err": (60, 32)}
BODY = (12, 13, 19, 240)
CARD_BG, CARD_BORDER, ACCENT, ACCENT_HOVER = "#12131A", "#3A3F4B", "#2F6FED", "#2559C4"
FONT = "Segoe UI"


def _font(size: float, bold=False):
    for name in (("seguisb.ttf", "segoeuib.ttf") if bold else ("segoeui.ttf",)):
        try:
            return ImageFont.truetype(rf"C:\Windows\Fonts\{name}", max(8, int(size)))
        except OSError:
            continue
    return ImageFont.load_default()


class FlowBar:
    CW = 460  # canvas width in logical pixels; the pill lives inside, the rest is glow room

    def __init__(self, app):
        self.app = app
        self.state, self.locked, self.hover = "rest", False, False
        try:
            self.S = float(app._get_window_scaling())
        except Exception:
            self.S = 1.0
        self.R = self.S * 2  # drawn at double size, then halved, for smooth edges
        self.w, self.h = SIZES["rest"]
        self.vw = self.vh = 0.0
        self.lv = self.lv_target = 0.0
        self.hist = deque([0.0] * 24, maxlen=24)
        self.sparks: list = []
        self.emoji: list = []
        self.sprites: list = []
        self.spin = 0.0
        self.lift = self.vlift = 0.0
        self.alpha = 0.0
        self.flash_kind, self.flash_until = None, 0.0
        self.active_until = time.time() + 3
        self.last = time.time()
        self.drag = None
        self._geo = None
        self._job = None
        self._grads: dict = {}
        self._warned = False
        self.font = _font(10.5 * self.S)
        self.font_big = _font(15 * self.S, bold=True)
        self.hbmp = None

        self.win = tk.Toplevel(app)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry("10x10+-20000+-20000")
        self.win.update_idletasks()
        self.hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
        self._styles()
        self.hdc_screen = user32.GetDC(None)
        self.hdc_mem = gdi32.CreateCompatibleDC(self.hdc_screen)
        self.zero = wintypes.POINT(0, 0)
        self.blend = BLENDFUNCTION(0, 0, 255, 1)
        self.reconfigure()
        for seq, fn in (("<Enter>", lambda e: self._hover(True)), ("<Leave>", lambda e: self._hover(False)),
                        ("<ButtonPress-1>", self._press), ("<B1-Motion>", self._motion),
                        ("<ButtonRelease-1>", self._release), ("<Button-3>", self._menu)):
            self.win.bind(seq, fn)

        self.card = tk.Toplevel(app)
        self.card.overrideredirect(True)
        self.card.attributes("-topmost", True)
        self.card.configure(bg=CARD_BG, highlightthickness=1, highlightbackground=CARD_BORDER)
        self.card.geometry("+-20000+-20000")
        self.card.update_idletasks()
        c.no_activate(self.card)
        self._card_job = None
        self._tick()

    # ------------------------------------------------------------ surface
    def meme(self) -> bool:
        return bool(self.app.cfg.get("meme_mode"))

    def reconfigure(self):
        """(Re)build the drawing surface. Meme mode needs a taller canvas to spin and bounce in."""
        self.CH = 330 if self.meme() else 130
        self.CW = 460 if self.meme() else 350  # a smaller canvas is cheaper to draw
        self.W, self.H = int(self.CW * self.S), int(self.CH * self.S)
        self.RW, self.RH = self.W * 2, self.H * 2
        self._grads.clear()
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), self.W, -self.H, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bits = ctypes.c_void_p()
        old = self.hbmp
        self.hbmp = gdi32.CreateDIBSection(self.hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        gdi32.SelectObject(self.hdc_mem, self.hbmp)
        if old:
            gdi32.DeleteObject(old)
        self.size = wintypes.SIZE(self.W, self.H)
        self._geo = None
        self.alpha = 0.0
        if self.meme() and not self.sprites:
            self._make_sprites()
        self.kick()

    def _make_sprites(self):
        try:
            f = ImageFont.truetype(r"C:\Windows\Fonts\seguiemj.ttf", int(22 * self.S))
            for ch in MEME_EMOJI:
                im = Image.new("RGBA", (int(34 * self.S), int(34 * self.S)), (0, 0, 0, 0))
                ImageDraw.Draw(im).text((im.width // 2, im.height // 2), ch, font=f, embedded_color=True, anchor="mm")
                self.sprites.append(im)
        except Exception as e:
            log.warning("emoji sprites unavailable: %s", e)

    def _styles(self):
        ex = user32.GetWindowLongPtrW(self.hwnd, -20)
        user32.SetWindowLongPtrW(self.hwnd, -20, ex | 0x80000 | 0x08000000 | 0x80)  # layered, no-activate, tool

    def _present(self, img) -> bool:
        data = img.tobytes("raw", "BGRa")
        ctypes.memmove(self.bits, data, len(data))
        self.blend.SourceConstantAlpha = max(0, min(255, int(self.alpha * 255)))
        args = (self.hwnd, self.hdc_screen, None, ctypes.byref(self.size), self.hdc_mem, ctypes.byref(self.zero), 0,
                ctypes.byref(self.blend), 2)
        ok = user32.UpdateLayeredWindow(*args)
        if not ok:
            self._styles()
            ok = user32.UpdateLayeredWindow(*args)
            if not ok and not self._warned:
                self._warned = True
                log.error("flow bar could not be drawn: %s", ctypes.get_last_error())
        return bool(ok)

    # ------------------------------------------------------------- state
    def kind(self, now=None) -> str:
        if self.flash_kind and (now or time.time()) < self.flash_until:
            return self.flash_kind
        if self.state == "rec":
            return "locked" if self.locked else "rec"
        if self.state == "proc":
            return "proc"
        return "hover" if self.hover else "rest"

    def hidden(self) -> bool:
        cfg = self.app.cfg
        if self.app.meeting:  # always show the red dot while a meeting is being transcribed
            return False
        return self.kind() == "rest" and (not cfg["flowbar_always"] or time.time() < cfg["flowbar_hidden_until"])

    def kick(self):
        """Draw the next frame now instead of waiting for the idle timer."""
        self.active_until = max(self.active_until, time.time() + 1.5)
        if self._job is not None:
            try:
                self.app.after_cancel(self._job)
            except Exception:
                pass
            self._job = self.app.after(1, self._tick)

    def set_state(self, state, locked=False):
        self.state, self.locked = state, locked
        if state != "rec":
            self.lv_target = 0.0
            self.hist.extend([0.0] * 24)
        self.kick()

    def level(self, v):
        v = min(1.0, v * 5)
        self.lv_target = max(self.lv_target, v)
        self.hist.append(v)

    def flash(self, kind, seconds=0.55):
        self.flash_kind, self.flash_until = kind, time.time() + seconds
        self.kick()

    def draw(self):
        self.kick()

    def _center(self):
        edge = self.app.cfg["flowbar_edge"]
        if edge == "left":
            return 18 + self.w / 2 + self.lift, self.CH / 2
        if edge == "right":
            return self.CW - 18 - self.w / 2 - self.lift, self.CH / 2
        return self.CW / 2, self.CH - 46 - self.lift

    def _place(self):
        left, top, right, bottom = c.work_area()
        edge = self.app.cfg["flowbar_edge"]
        if edge == "left":
            geo = (left, (top + bottom - self.H) // 2)
        elif edge == "right":
            geo = (right - self.W, (top + bottom - self.H) // 2)
        else:
            geo = ((left + right - self.W) // 2, bottom - self.H + int(18 * self.S))
        if geo != self._geo:
            self._geo = geo
            self.win.geometry(f"{self.W}x{self.H}+{geo[0]}+{geo[1]}")

    # -------------------------------------------------------------- loop
    def _tick(self):
        now = time.time()
        dt = min(0.05, now - self.last)
        self.last = now
        busy = True
        try:
            gone = self.hidden() and not self.drag
            target = 0.0 if gone else 1.0
            self.alpha += (target - self.alpha) * min(1.0, dt * (13 if gone else 22))
            if gone and self.alpha < 0.03:
                self.alpha = 0.0
                if self._geo != "off":
                    self._geo = "off"
                    self.win.geometry(f"{self.W}x{self.H}+-20000+-20000")
                    self.w, self.h = SIZES["rest"]  # next time it pops open from small
                    self.vw = self.vh = 0.0
                    self.sparks, self.emoji = [], []
                busy = False
            else:
                k = self.kind(now)
                self._step(k, dt)
                if not (self.drag and self.drag.get("moving")):
                    self._place()
                self._present(self._render(now, k))
                busy = (k != "rest" or now < self.active_until or abs(SIZES[k][0] - self.w) > 0.4
                        or bool(self.sparks) or bool(self.emoji) or bool(self.drag) or abs(target - self.alpha) > 0.01)
        except Exception:
            log.exception("flow bar frame")
        self._job = self.app.after(self._delay(busy), self._tick)

    def _delay(self, busy) -> int:
        """Aim for 30 frames a second while moving, and never let drawing crowd out the rest of the app."""
        if not busy:
            return 150
        spent = (time.time() - self.last) * 1000
        frame = 50 if (self.meme() and self.state == "rec") else 33  # Meme mode frames cost more, so fewer of them
        return int(max(10, frame - spent))

    def _step(self, k, dt):
        tw, th = SIZES[k]
        wild = self.meme() and k in ("rec", "locked")
        springs = (("w", "vw", tw), ("h", "vh", th), ("lift", "vlift", 120.0 if wild else 0.0))
        for attr, vel, target in springs:  # quick, slightly springy size changes
            x, v = getattr(self, attr), getattr(self, vel)
            v += ((target - x) * 430 - v * 30) * dt
            setattr(self, vel, v)
            setattr(self, attr, x + v * dt)
        self.w, self.h = max(8.0, self.w), max(8.0, self.h)
        self.lv += (self.lv_target - self.lv) * min(1.0, dt * 18)
        self.lv_target *= max(0.0, 1 - dt * 5)
        cx, cy = self._center()
        if k in ("rec", "locked") and self.lv > 0.28 and random.random() < self.lv * 0.9 and len(self.sparks) < 40:
            self.sparks.append({"x": cx + random.uniform(-self.w * 0.32, self.w * 0.32), "y": cy - self.h / 2,
                                "vx": random.uniform(-22, 22), "vy": -random.uniform(40, 120) * (0.6 + self.lv),
                                "life": 1.0, "rate": random.uniform(1.1, 2.0), "r": random.uniform(0.9, 2.1)})
        if wild:
            self.spin += dt * (140 + 1100 * self.lv)  # the louder you talk, the faster it spins
            if self.sprites and random.random() < 0.10 + self.lv * 0.5 and len(self.emoji) < 26:
                self.emoji.append({"x": cx + random.uniform(-60, 60), "y": cy, "vx": random.uniform(-190, 190),
                                   "vy": -random.uniform(190, 430), "life": 1.0, "i": random.randrange(len(self.sprites))})
        else:
            self.spin = 0.0
        for s in self.sparks:
            s["x"] += s["vx"] * dt
            s["y"] += s["vy"] * dt
            s["vy"] += 55 * dt
            s["life"] -= s["rate"] * dt
        self.sparks = [s for s in self.sparks if s["life"] > 0 and s["y"] > 0]
        for e in self.emoji:
            e["x"] += e["vx"] * dt
            e["y"] += e["vy"] * dt
            e["vy"] += 620 * dt
            e["life"] -= dt * 0.75
        self.emoji = [e for e in self.emoji if e["life"] > 0 and -30 < e["x"] < self.CW + 30 and e["y"] < self.CH + 30]

    # ------------------------------------------------------------ render
    def _gradient(self, name, offset):
        strip = self._grads.get(name)
        if strip is None:
            stops = PALETTES[name]
            n, width = len(stops), self.W
            px = []
            for x in range(width):
                pos = x / width * n
                i = int(pos)
                f = pos - i
                f = f * f * (3 - 2 * f)
                a, b = stops[i % n], stops[(i + 1) % n]
                px.append(tuple(int(a[j] + (b[j] - a[j]) * f) for j in range(3)))
            strip = Image.new("RGB", (width * 2, 1))
            strip.putdata(px + px)
            self._grads[name] = strip
        off = int(offset * self.S) % self.W
        return strip.crop((off, 0, off + self.W, 1)).resize((self.W, self.H), Image.NEAREST)

    def _render(self, t, k):
        """One frame. Shapes are drawn as cheap one-channel masks at double size (for smooth edges),
        halved, then coloured at screen size. That keeps a frame to a few milliseconds."""
        S, W, H = self.S, self.W, self.H
        lv = self.lv
        breathe = 0.5 + 0.5 * math.sin(t * 2.2)
        recording = k in ("rec", "locked")
        wild = self.meme() and recording
        ss = 1 if wild else 2  # a spinning pill doesn't need the double-size smoothing
        R, MW, MH = S * ss, W * ss, H * ss
        down = (lambda im: im.reduce(2)) if ss == 2 else (lambda im: im)
        command = recording and self.app.rec and self.app.rec.get("mode") == "command"
        pal = "gold" if command else k if k in ("ok", "err") else "aurora"
        speed = {"rest": 16, "hover": 46, "proc": 320, "ok": 260, "err": 60}.get(k, 80 + 260 * lv)
        if wild:
            pal = MEME_PALETTES[int(t * 5) % len(MEME_PALETTES)]  # colour scheme flips five times a second
            speed = 900
        grad = self._gradient(pal, t * speed)
        cx, cy = self._center()
        w, h = self.w, self.h
        if k == "err":
            cx += math.sin(t * 40) * 5 * max(0.0, (self.flash_until - t) / 0.55)
        if wild:
            cx += math.sin(t * 3.3) * 46
            cy -= abs(math.sin(t * 6.8)) * (34 + 44 * lv) - 20
            pulse = 1 + 0.22 * lv + 0.06 * math.sin(t * 15)
            w, h = w * pulse, h * pulse
        x0, y0, x1, y1 = (cx - w / 2) * R, (cy - h / 2) * R, (cx + w / 2) * R, (cy + h / 2) * R
        rad = (y1 - y0) / 2
        mid = cy * R
        line = max(2, int(1.4 * R))

        # soft coloured glow behind the pill, stronger with your voice (drawn small, blurred, scaled up)
        glow = {"rest": 70 + 50 * breathe, "hover": 150, "proc": 170 + 60 * breathe, "ok": 255, "err": 255}.get(
            k, 120 + 135 * min(1.0, lv * 1.7))
        spread = {"rest": 3, "hover": 6, "proc": 8, "ok": 12, "err": 10}.get(k, 6 + 15 * lv)
        if wild:
            glow, spread = 255, 14 + 22 * lv + 6 * math.sin(t * 18)
        g = S / 2
        gm = Image.new("L", (W // 2, H // 2), 0)
        ImageDraw.Draw(gm).rounded_rectangle(
            [(cx - w / 2 - spread) * g, (cy - h / 2 - spread) * g, (cx + w / 2 + spread) * g, (cy + h / 2 + spread) * g],
            radius=(h / 2 + spread) * g, fill=int(min(255, glow)))
        gm = gm.filter(ImageFilter.GaussianBlur(max(1.2, spread * g))).resize((W, H), Image.BILINEAR)
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        img.paste(grad, (0, 0), gm)

        strobe = wild and lv > 0.45 and int(t * 4) % 2 == 0  # at most two white flashes a second
        bm = Image.new("L", (MW, MH), 0)  # the pill body
        ImageDraw.Draw(bm).rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=240)
        img.paste((245, 245, 255) if strobe else BODY[:3], (0, 0, W, H), down(bm))

        m = Image.new("L", (MW, MH), 0)  # everything painted with the moving gradient
        md = ImageDraw.Draw(m)
        red = white = None               # masks made only when a state needs them
        texts = []                       # (x, y, string, anchor, font), drawn straight at screen size
        md.rounded_rectangle([x0, y0, x1, y1], radius=rad, outline=255, width=line)
        settled = abs(SIZES[k][0] - self.w) < 26

        if k == "rest":
            fw = (13 + 6 * breathe) * R
            shift = 5 * R if self.app.meeting else 0
            md.rounded_rectangle([cx * R - fw / 2 + shift, mid - 1.2 * R, cx * R + fw / 2 + shift, mid + 1.2 * R],
                                 radius=1.2 * R, fill=int(150 + 90 * breathe))
            if self.app.meeting:
                red = Image.new("L", (MW, MH), 0)
                ImageDraw.Draw(red).ellipse([x0 + 5 * R, mid - 2.8 * R, x0 + 10.6 * R, mid + 2.8 * R],
                                            fill=int(140 + 115 * math.sin(t * 5) ** 2))
        elif k == "hover" and settled:
            gx = x0 + 18 * R
            md.rounded_rectangle([gx - 2.8 * R, mid - 7 * R, gx + 2.8 * R, mid + 1.6 * R], radius=2.8 * R, fill=255)
            md.arc([gx - 5.6 * R, mid - 4.4 * R, gx + 5.6 * R, mid + 5.2 * R], 0, 180, fill=255, width=line)
            md.line([gx, mid + 5.2 * R, gx, mid + 8 * R], fill=255, width=line)
            texts.append((x0 / ss + 32 * S, cy * S - 0.5 * S, f"Click or hold {self.app.ptt_label()} to start dictating",
                          "lm", self.font))
        elif recording:
            p = (t * 0.9) % 1.0  # ring that keeps radiating outward
            sp = p * 15 * R
            md.rounded_rectangle([x0 - sp, y0 - sp, x1 + sp, y1 + sp], radius=rad + sp,
                                 outline=int((1 - p) * 150 * (0.35 + min(1, lv * 1.5))), width=max(2, int(1.1 * R)))
            md.rounded_rectangle([x0, y0, x1, y1], radius=rad, outline=255, width=line)
            bx0 = x0 + (40 if k == "locked" else 25) * R
            bx1 = x1 - (70 if k == "locked" else 44) * R
            gap, bw = 4.4 * R, 2.4 * R
            n = max(3, int((bx1 - bx0) / gap))
            half = (n - 1) / 2
            start = bx0 + (bx1 - bx0 - (n - 1) * gap) / 2
            for i in range(n):
                v = self.hist[-1 - min(23, int(abs(i - half) * 23 / max(1, half)))]
                wob = 0.62 + 0.38 * math.sin(t * 9 + i * 1.3)
                hh = (1.8 + (v * 0.75 + lv * 0.25) * wob * (h / 2 - 7)) * R
                bx = start + i * gap
                md.rounded_rectangle([bx - bw / 2, mid - hh, bx + bw / 2, mid + hh], radius=bw / 2, fill=255)
            if self.app.rec and not strobe:
                secs = int(t - self.app.rec["t0"])
                texts.append((x1 / ss - (52 if k == "locked" else 23) * S, cy * S - 0.5 * S,
                              f"{secs // 60}:{secs % 60:02d}", "mm", self.font))
            red = Image.new("L", (MW, MH), 0)
            rd = ImageDraw.Draw(red)
            if k == "locked":
                bxc, sxc = x0 + 19 * R, x1 - 19 * R
                white = Image.new("L", (MW, MH), 0)
                wd = ImageDraw.Draw(white)
                wd.ellipse([bxc - 11 * R, mid - 11 * R, bxc + 11 * R, mid + 11 * R], fill=34)
                for a, b in (((-3.4, -3.4), (3.4, 3.4)), ((-3.4, 3.4), (3.4, -3.4))):
                    wd.line([bxc + a[0] * R, mid + a[1] * R, bxc + b[0] * R, mid + b[1] * R], fill=235, width=line)
                pr = (11 + 1.4 * math.sin(t * 6)) * R
                rd.ellipse([sxc - pr, mid - pr, sxc + pr, mid + pr], fill=255)
                wd.rounded_rectangle([sxc - 3.8 * R, mid - 3.8 * R, sxc + 3.8 * R, mid + 3.8 * R], radius=1.4 * R, fill=255)
            else:
                dr = (3 + 1.2 * math.sin(t * 6) ** 2) * R
                rd.ellipse([x0 + 14 * R - dr, mid - dr, x0 + 14 * R + dr, mid + dr], fill=255)
        elif k == "proc":
            a0, a1 = x0 + 16 * R, x1 - 16 * R
            for phase, amp, fill in ((2.1, 2.6, 95), (0.0, 4.6, 255)):
                pts = []
                x = a0
                while x <= a1:
                    env = math.sin(math.pi * (x - a0) / max(1, a1 - a0))
                    lx = x / R
                    pts.append((x, mid + (math.sin(lx * 0.19 - t * 13 + phase) * amp
                                          + math.sin(lx * 0.08 + t * 7 + phase) * amp * 0.4) * env * R))
                    x += 4 * R
                if len(pts) > 1:
                    md.line(pts, fill=fill, width=max(2, int(1.7 * R)), joint="curve")
        elif k == "ok":
            md.line([(cx - 6.5) * R, mid + 0.5 * R, (cx - 2) * R, mid + 5 * R, (cx + 7.5) * R, mid - 5.5 * R],
                    fill=255, width=int(2.2 * R), joint="curve")
        elif k == "err":
            for a, b in (((-5, -5), (5, 5)), ((-5, 5), (5, -5))):
                md.line([(cx + a[0]) * R, mid + a[1] * R, (cx + b[0]) * R, mid + b[1] * R], fill=255, width=int(2.2 * R))

        for s in self.sparks:  # sparks thrown off by loud moments
            r = s["r"] * R * (0.5 + 0.5 * s["life"])
            md.ellipse([s["x"] * R - r, s["y"] * R - r, s["x"] * R + r, s["y"] * R + r], fill=int(255 * min(1, s["life"] * 1.3)))

        if strobe:
            img.paste((20, 20, 30), (0, 0, W, H), down(m))
        else:
            img.paste(grad, (0, 0), down(m))
        if red is not None:
            img.paste((236, 66, 78), (0, 0, W, H), down(red))
        if white is not None:
            img.paste((255, 255, 255), (0, 0, W, H), down(white))
        if texts:
            d = ImageDraw.Draw(img)
            for tx, ty, s, anchor, font in texts:
                d.text((tx, ty), s, fill=(226, 228, 236, 255), font=font, anchor=anchor)
        if wild:  # spin the whole pill, then add things that should not spin with it
            img = img.rotate(self.spin % 360, resample=Image.BILINEAR, center=(cx * S, cy * S))
            for e in self.emoji:
                sp = self.sprites[e["i"]]
                img.alpha_composite(sp, (max(0, min(W - sp.width, int(e["x"] * S - sp.width / 2))),
                                         max(0, min(H - sp.height, int(e["y"] * S - sp.height / 2)))))
            word = MEME_WORDS[int(t / 1.3) % len(MEME_WORDS)]
            pos = (W / 2 + math.sin(t * 9) * 5 * S, (26 + abs(math.sin(t * 5)) * 8) * S)
            tm = Image.new("L", (W, H), 0)
            ImageDraw.Draw(tm).text(pos, word, fill=255, font=self.font_big, anchor="mm")
            ImageDraw.Draw(img).text((pos[0] + 1.5 * S, pos[1] + 1.5 * S), word, fill=(0, 0, 0, 255), font=self.font_big, anchor="mm")
            img.paste(grad, (0, 0), tm)
        return img.convert("RGBa")

    def preview(self, k, background=(40, 44, 52)) -> Image.Image:
        """A still of one state over a flat background, for checking the look."""
        self.w, self.h = SIZES[k]
        self.lift = 120.0 if (self.meme() and k in ("rec", "locked")) else 0.0
        out = self._render(time.time(), k).convert("RGBA")
        bg = Image.new("RGBA", out.size, background + (255,))
        bg.alpha_composite(out)
        return bg

    # -------------------------------------------------------------- mouse
    def _hover(self, on):
        if self.state == "rest" and not self.drag:
            self.hover = on
            self.kick()

    def _press(self, e):
        self.drag = {"x": e.x_root, "y": e.y_root, "moving": False}

    def _motion(self, e):
        d = self.drag
        if not d or self.state != "rest":
            return
        if not d["moving"] and abs(e.x_root - d["x"]) + abs(e.y_root - d["y"]) > 10:
            d["moving"] = True
            self.hover = False
        if d["moving"]:
            cx, cy = self._center()
            self._geo = None
            self.win.geometry(f"{self.W}x{self.H}+{int(e.x_root - cx * self.S)}+{int(e.y_root - cy * self.S)}")

    def _release(self, e):
        d, self.drag = self.drag, None
        self.kick()
        if d and d["moving"]:
            sw = self.win.winfo_screenwidth()
            self.app.cfg["flowbar_edge"] = "left" if e.x_root < sw / 4 else "right" if e.x_root > sw * 3 / 4 else "bottom"
            self.app.save_cfg()
            self._geo = None
            return
        if self.state == "rest":
            self.hover = False
            self.app.flowbar_click()
        elif self.state == "rec" and self.locked:
            cx, _ = self._center()
            if not self.meme() and e.x < (cx - self.w / 2 + 38) * self.S:
                self.app.cancel_dictation()
            else:  # in meme mode the pill is spinning, so any click stops; Esc cancels
                self.app.stop_recording()

    def _menu(self, e):
        app = self.app
        menu = tk.Menu(app, tearoff=0)
        if self.state == "rest":
            menu.add_command(label="Hide for 1 hour", command=self.hide_hour)
        mics = tk.Menu(menu, tearoff=0)
        for name in ["Auto-detect"] + c.list_mics():
            mics.add_command(label=("✓ " if name == app.cfg["mic"] else "    ") + name,
                             command=lambda n=name: app.set_cfg("mic", n))
        menu.add_cascade(label="Microphone", menu=mics)
        menu.add_command(label="Paste last transcript", command=app.paste_last)
        menu.add_command(label="Stop meeting transcription" if app.meeting else "Start meeting transcription",
                         command=app.toggle_meeting)
        menu.add_command(label="Open Scratchpad", command=lambda: app.toggle_scratchpad(force_show=True))
        menu.add_command(label="Settings", command=lambda: app.open_page("settings"))
        menu.tk_popup(e.x_root, e.y_root)

    def hide_hour(self):
        self.app.cfg["flowbar_hidden_until"] = time.time() + 3600
        self.app.save_cfg()
        self.hover = False
        self.kick()

    # -------------------------------------------------------------- cards
    def notify(self, title, body="", actions=(), timeout=5000):
        card = self.card
        if self._card_job:
            self.app.after_cancel(self._card_job)
            self._card_job = None
        for wdg in card.winfo_children():
            wdg.destroy()
        strip = tk.Canvas(card, height=3, bg=CARD_BG, highlightthickness=0)
        strip.pack(fill="x")
        for i, col in enumerate(["#00E5FF", "#7C5CFF", "#FF4FD8", "#FFAA3C"]):
            strip.create_rectangle(i * 120, 0, (i + 1) * 120, 3, fill=col, outline="")
        top = tk.Frame(card, bg=CARD_BG)
        top.pack(fill="x", padx=14, pady=(9, 0))
        tk.Label(top, text=title, fg="white", bg=CARD_BG, font=(FONT, 11, "bold"), wraplength=360, justify="left").pack(side="left")
        x = tk.Label(top, text="✕", fg="#9AA0AA", bg=CARD_BG, font=(FONT, 10), cursor="hand2")
        x.pack(side="right", padx=(16, 0))
        x.bind("<Button-1>", lambda e: self.dismiss())
        if body:
            tk.Label(card, text=body, fg="#C8CCD4", bg=CARD_BG, font=(FONT, 10), wraplength=390, justify="left"
                     ).pack(anchor="w", padx=14, pady=(2, 0))
        if actions:
            row = tk.Frame(card, bg=CARD_BG)
            row.pack(anchor="w", padx=12, pady=(8, 0))
            for i, (label, fn) in enumerate(actions):
                def run(fn=fn):
                    self.dismiss()
                    fn()
                tk.Button(row, text=label, command=run, relief="flat", bd=0, padx=12, pady=3, cursor="hand2",
                          font=(FONT, 10, "bold"), fg="white", activeforeground="white",
                          bg=ACCENT if i == 0 else "#3A3F4B", activebackground=ACCENT_HOVER).pack(side="left", padx=2)
        tk.Frame(card, bg=CARD_BG, height=10).pack()
        card.update_idletasks()
        w, h = max(card.winfo_reqwidth(), 260), card.winfo_reqheight()
        left, top_, right, bottom = c.work_area()
        card.geometry(f"{w}x{h}+{(left + right - w) // 2}+{bottom - h - int(74 * self.S)}")
        if timeout:
            self._card_job = self.app.after(timeout, self.dismiss)

    def dismiss(self):
        self._card_job = None
        self.card.geometry("+-20000+-20000")
