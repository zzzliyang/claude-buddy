"""
Claude Buddy - a floating desktop mascot (Pip or the goat) that reacts to Claude Code.

  busy     -> the goat trots back and forth, chewing
  waiting  -> the goat hops and bleats (Claude needs your input / permission)
  idle     -> the goat lies down and sleeps

State comes from small JSON files written by goat_hook.py (one per Claude Code
session) in ~/.claude/goat/sessions/.

Controls: left-drag to move, right-click for the menu.
"""
import json
import math
import os
import random
import socket
import sys
import time
import threading
import queue
import tkinter as tk
import urllib.request

try:                            # optional: system-tray icon (pip install pystray pillow)
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None

HOME = os.path.expanduser("~")
GOAT_DIR = os.path.join(HOME, ".claude", "goat")
SESSIONS_DIR = os.path.join(GOAT_DIR, "sessions")
CONFIG_FILE = os.path.join(GOAT_DIR, "config.json")
CUSTOM_DIR = os.path.join(GOAT_DIR, "custom")
USAGE_FILE = os.path.join(GOAT_DIR, "usage.json")
CUSTOM_FRAME_MS = 100

FPS = 30
POLL_MS = 300
BUSY_STALE_SEC = 30 * 60        # busy with no hook activity for 30 min -> treat as idle
FILE_STALE_SEC = 24 * 3600      # forget session files older than a day
LOCK_PORT = 47917               # single-instance lock

KEY = "#ff00fe"                 # transparent colour key (never used in the drawing)
W, H = 240, 180                 # logical canvas size (scaled for DPI)
GROUND = 150

# palette
FUR = "#efe6d6"
FUR_DARK = "#d6c8b1"
LINE = "#4a3b2e"
HORN = "#b08d6a"
HOOF = "#8a7663"
MUZZLE = "#fbf3ea"
BLUSH = "#f2b8b0"
PILL = "#2b2b2b"
PIP = "#7fcdb9"
PIP_DARK = "#5fae9b"
PIP_BELLY = "#d9f3ea"
BULB_ON = "#ffd84d"
BULB_MID = "#ffe9a0"
BULB_HALO = "#fff4c8"
BULB_ALERT = "#ff7a6b"
BULB_OFF = "#c9cfd3"


# ---------------------------------------------------------------- helpers
def gif_frames_info(path):
    """Walk a GIF's blocks: per frame (delay_ms, disposal, left, top, w, h)."""
    try:
        with open(path, "rb") as f:
            d = f.read()
    except Exception:
        return []
    if d[:3] != b"GIF":
        return []
    i = 13
    if d[10] & 0x80:
        i += 3 * (2 ** ((d[10] & 7) + 1))
    frames, delay, disp = [], 100, 0
    try:
        while i < len(d):
            b = d[i]
            if b == 0x21:                                   # extension
                label = d[i + 1]
                i += 2
                if label == 0xF9 and d[i] == 4:             # graphic control
                    disp = (d[i + 1] >> 2) & 7
                    delay = int.from_bytes(d[i + 2:i + 4], "little") * 10
                    delay = delay if delay >= 20 else 100
                while d[i]:
                    i += d[i] + 1
                i += 1
            elif b == 0x2C:                                 # image descriptor
                l, t, w, h = (int.from_bytes(d[i + k:i + k + 2], "little") for k in (1, 3, 5, 7))
                packed = d[i + 9]
                i += 10
                if packed & 0x80:
                    i += 3 * (2 ** ((packed & 7) + 1))
                i += 1                                      # LZW min code size
                while d[i]:
                    i += d[i] + 1
                i += 1
                frames.append((delay, disp, l, t, w, h))
                delay, disp = 100, 0
            else:                                           # 0x3B trailer or junk
                break
    except IndexError:
        pass
    return frames


def single_instance():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(GOAT_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass


TRAY_DOT = {"idle": "#9aa3ab", "busy": "#4cc38a", "waiting": "#ff5a4a"}


def tray_image(state):
    """64x64 goat head with a coloured state dot, for the tray icon."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(16, 22), (8, 4), (24, 16)], fill=HORN, outline=LINE)      # horns
    d.polygon([(48, 22), (56, 4), (40, 16)], fill=HORN, outline=LINE)
    d.ellipse((2, 20, 18, 30), fill=FUR_DARK, outline=LINE)                # ears
    d.ellipse((46, 20, 62, 30), fill=FUR_DARK, outline=LINE)
    d.ellipse((12, 12, 52, 56), fill=FUR, outline=LINE, width=2)           # head
    d.ellipse((20, 34, 44, 54), fill=MUZZLE, outline=LINE)                 # muzzle
    d.ellipse((22, 26, 27, 31), fill=LINE)                                 # eyes
    d.ellipse((37, 26, 42, 31), fill=LINE)
    d.ellipse((42, 42, 62, 62), fill=TRAY_DOT.get(state, TRAY_DOT["idle"]),
              outline="white", width=2)                                    # state dot
    return img


def save_icon(path):
    """Write the tray goat as a .ico (used by the Start menu shortcut)."""
    tray_image("idle").save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])


def read_aggregate_state():
    """waiting > busy > idle across all live sessions."""
    now = time.time()
    states = []
    try:
        names = os.listdir(SESSIONS_DIR)
    except FileNotFoundError:
        return "idle"
    for name in names:
        if not name.endswith(".json"):
            continue
        p = os.path.join(SESSIONS_DIR, name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            st, ts = d.get("state", "idle"), float(d.get("ts", 0))
        except Exception:
            continue
        age = now - ts
        if age > FILE_STALE_SEC:
            try:
                os.remove(p)
            except Exception:
                pass
            continue
        if st == "busy" and age > BUSY_STALE_SEC:
            st = "idle"
        states.append(st)
    if "waiting" in states:
        return "waiting"
    if "busy" in states:
        return "busy"
    return "idle"


# ---------------------------------------------------------------- weather
WEATHER_REFRESH_SEC = 15 * 60
DEFAULT_PLACE = {"place": "Zurich", "lat": 47.3769, "lon": 8.5417}


def weather_kind(code):
    """Map an Open-Meteo / WMO weather code to a drawing kind."""
    if code is None:
        return None
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code >= 95:
        return "thunder"
    if 51 <= code <= 82:
        return "rain"
    return "cloudy"


WEATHER_TEXT = {"clear": "clear", "partly": "partly cloudy", "cloudy": "cloudy", "fog": "fog",
                "rain": "rain", "snow": "snow", "thunder": "thunderstorm"}


def fetch_weather(lat, lon):
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&current=temperature_2m,weather_code,is_day" % (lat, lon))
    with urllib.request.urlopen(url, timeout=10) as r:
        cur = json.load(r)["current"]
    return {"kind": weather_kind(int(cur["weather_code"])), "temp": cur.get("temperature_2m"),
            "is_day": bool(cur.get("is_day", 1)), "ts": time.time()}


# ---------------------------------------------------------------- widget
class Goat:
    def __init__(self, root):
        self.root = root
        self.S = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.cfg = load_config()

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=KEY)
        try:
            root.attributes("-transparentcolor", KEY)   # Windows
        except tk.TclError:
            pass

        self.cw, self.ch = int(W * self.S), int(H * self.S)
        self.c = tk.Canvas(root, width=self.cw, height=self.ch, bg=KEY,
                           highlightthickness=0, bd=0)
        self.c.pack()

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x = self.cfg.get("x", sw - self.cw - 30)
        y = self.cfg.get("y", sh - self.ch - 70)
        x = min(max(0, x), sw - 40)
        y = min(max(0, y), sh - 40)
        root.geometry(f"{self.cw}x{self.ch}+{x}+{y}")

        # animation state
        self.state = "idle"
        self.shown = "idle"
        self.override = None          # (state, until) for menu tests
        self.t = 0.0
        self.cx = W / 2
        self.dir = 1
        self.zzz = []
        self.blink = 0

        # input
        self.c.bind("<ButtonPress-1>", self.on_press)
        self.c.bind("<B1-Motion>", self.on_drag)
        self.c.bind("<ButtonRelease-1>", self.on_release)
        self.c.bind("<Button-3>", self.on_menu)
        self.c.bind("<Enter>", self.tip_show)
        self.c.bind("<Leave>", self.tip_hide)
        self.tip = None
        self._tip_job = None
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Test: working (5 s)", command=lambda: self.test("busy"))
        self.menu.add_command(label="Test: needs you (5 s)", command=lambda: self.test("waiting"))
        self.menu.add_command(label="Test: idle (5 s)", command=lambda: self.test("idle"))
        self.menu.add_separator()
        self.mascot_var = tk.StringVar(value=self.cfg.get("mascot", "goat"))
        self.menu.add_radiobutton(label="Mascot: Pip", variable=self.mascot_var, value="pip",
                                  command=self.set_mascot)
        self.menu.add_radiobutton(label="Mascot: Goat", variable=self.mascot_var, value="goat",
                                  command=self.set_mascot)
        self.menu.add_radiobutton(label="Mascot: My images", variable=self.mascot_var,
                                  value="custom", command=self.set_mascot)
        self.menu.add_command(label="Open my images folder", command=self.open_custom_dir)
        self.menu.add_command(label="Reload my images", command=self.load_custom)
        self.menu.add_separator()
        wmenu = tk.Menu(self.menu, tearoff=0)
        self.weather_var = tk.StringVar(value=self.cfg.get("weather", "auto"))
        for label, val in (("Live (auto)", "auto"), ("Off", "off"), (None, None),
                           ("Test: sunny", "clear"), ("Test: partly cloudy", "partly"),
                           ("Test: cloudy", "cloudy"), ("Test: rain", "rain"),
                           ("Test: snow", "snow"), ("Test: fog", "fog"),
                           ("Test: thunderstorm", "thunder"), ("Test: clear night", "night")):
            if label is None:
                wmenu.add_separator()
            else:
                wmenu.add_radiobutton(label=label, variable=self.weather_var, value=val,
                                      command=self.set_weather_mode)
        self.menu.add_cascade(label="Weather", menu=wmenu)
        self.menu.add_command(label="Reset position", command=self.reset_pos)
        if pystray:
            self.menu.add_command(label="Hide to tray", command=self.hide)
        self.menu.add_command(label="Quit goat", command=self.quit)

        self.hidden = False
        self.quitting = False
        self.tray = None
        self.tray_q = queue.Queue()     # tray thread -> Tk thread
        self.tray_state = None
        self.start_tray()
        if self.tray and self.cfg.get("hidden"):
            self.hide()

        self.load_custom()
        self.weather = None
        self.wx_kind = None
        self.wx_parts = []
        self._wx_thread_running = False
        self.weather_fetch_loop()
        self.poll()
        self.tick()

    # ---------- custom images
    def load_custom(self):
        """Load idle/busy/waiting images (.gif may be animated, .png static) from CUSTOM_DIR."""
        self.custom = {}
        for st in ("idle", "busy", "waiting"):
            for ext in ("gif", "png"):
                p = os.path.join(CUSTOM_DIR, f"{st}.{ext}")
                if not os.path.exists(p):
                    continue
                frames = []
                try:
                    if ext == "gif":
                        frames = self._load_gif(p)
                    else:
                        frames.append(tk.PhotoImage(file=p))
                except tk.TclError:
                    frames = []
                if frames:
                    delays = [f[0] for f in gif_frames_info(p)] if ext == "gif" else []
                    if len(delays) < len(frames):
                        delays += [CUSTOM_FRAME_MS] * (len(frames) - len(delays))
                    frames = [self._scale_img(fr) for fr in frames]
                    self.custom[st] = (frames, delays[:len(frames)])
                    break
        # missing states fall back to whichever image exists
        if self.custom:
            first = next(iter(self.custom.values()))
            for st in ("idle", "busy", "waiting"):
                self.custom.setdefault(st, self.custom.get("idle", first))

    def _load_gif(self, path):
        """Load a GIF and composite its frames (honours partial frames and disposal),
        so optimised GIFs don't flicker."""
        info = gif_frames_info(path)
        raws = []
        while len(raws) < 500:
            try:
                raws.append(tk.PhotoImage(file=path, format=f"gif -index {len(raws)}"))
            except tk.TclError:
                break
        if not raws:
            return []
        W0, H0 = raws[0].width(), raws[0].height()
        canvas = tk.PhotoImage(width=W0, height=H0)
        out = []
        for k, raw in enumerate(raws):
            _, disp, l, t, w, h = info[k] if k < len(info) else (0, 0, 0, 0, W0, H0)
            prev = None
            if disp == 3:
                prev = tk.PhotoImage(width=W0, height=H0)
                prev.tk.call(prev, "copy", canvas)
            canvas.tk.call(canvas, "copy", raw)              # overlay: keeps what's underneath
            snap = tk.PhotoImage(width=W0, height=H0)
            snap.tk.call(snap, "copy", canvas)
            out.append(snap)
            if disp == 2:                                    # restore to background
                blank = tk.PhotoImage(width=max(1, w), height=max(1, h))
                canvas.tk.call(canvas, "copy", blank, "-to", l, t, l + w, t + h,
                               "-compositingrule", "set")
            elif disp == 3 and prev is not None:
                canvas = prev
        return out

    def _scale_img(self, img):
        """Scale a PhotoImage by the display scaling (Tk only does integer zoom/subsample)."""
        from fractions import Fraction
        f = Fraction(self.S).limit_denominator(4)
        num, den = f.numerator, f.denominator
        # never wider/taller than the window
        while (img.width() * num // den > self.cw or img.height() * num // den > self.ch) and num > 1:
            num -= 1
        if num == den:
            return img
        out = img.zoom(num) if num > 1 else img
        return out.subsample(den) if den > 1 else out

    def open_custom_dir(self):
        os.makedirs(CUSTOM_DIR, exist_ok=True)
        try:
            os.startfile(CUSTOM_DIR)          # Windows Explorer
        except Exception:
            pass

    def draw_custom(self, st, bob):
        frames, delays = self.custom[st]
        total = sum(delays) or 1
        ms = (self.t * 1000) % total
        idx = 0
        for idx, d in enumerate(delays):
            if ms < d:
                break
            ms -= d
        img = frames[idx]
        x = W / 2 * self.S
        y = (GROUND + bob) * self.S
        self.c.create_image(x, y, image=img, anchor="s")
        return GROUND + bob - img.height() / self.S

    # ---------- weather
    def set_weather_mode(self):
        self.cfg["weather"] = self.weather_var.get()
        save_config(self.cfg)
        if self.weather_var.get() == "auto":
            self.weather_fetch_loop(force=True)

    def weather_fetch_loop(self, force=False):
        """Fetch live weather in a background thread every WEATHER_REFRESH_SEC."""
        due = force or self.weather is None or time.time() - self.weather.get("ts", 0) > WEATHER_REFRESH_SEC
        if due and not self._wx_thread_running and self.weather_var.get() == "auto":
            self._wx_thread_running = True
            place = {**DEFAULT_PLACE, **{k: self.cfg[k] for k in ("place", "lat", "lon") if k in self.cfg}}

            def work():
                try:
                    w = fetch_weather(float(place["lat"]), float(place["lon"]))
                    w["place"] = place["place"]
                    self.weather = w
                except Exception:
                    if self.weather is None:
                        self.weather = {"kind": None, "ts": time.time() - WEATHER_REFRESH_SEC + 120}
                finally:
                    self._wx_thread_running = False
            threading.Thread(target=work, daemon=True).start()
        if not force:
            self.root.after(30 * 1000, self.weather_fetch_loop)

    def current_weather(self):
        """(kind, is_day) to draw, or (None, True)."""
        mode = self.weather_var.get()
        if mode == "off":
            return None, True
        if mode == "night":
            return "clear", False
        if mode != "auto":
            return mode, True
        w = self.weather or {}
        return w.get("kind"), w.get("is_day", True)

    def _wx_reset(self, kind):
        self.wx_kind = kind
        rnd = random.Random()
        n = {"rain": 28, "thunder": 34, "snow": 24}.get(kind, 0)
        self.wx_parts = [[rnd.uniform(0, W), rnd.uniform(-20, GROUND), rnd.uniform(0.7, 1.3),
                          rnd.uniform(0, 6.28)] for _ in range(n)]
        self.wx_flash = 0.0

    def _sx(self, v):
        return v * self.S

    def _cloud(self, x, y, scale, fill, outline):
        for dx, dy, r in ((-14, 2, 9), (-4, -5, 12), (9, -2, 10), (17, 3, 7), (2, 4, 10)):
            cx, cy, rr = x + dx * scale, y + dy * scale, r * scale
            self.c.create_oval(self._sx(cx - rr), self._sx(cy - rr * 0.85), self._sx(cx + rr),
                               self._sx(cy + rr * 0.85), fill=fill, outline=outline,
                               width=self._sx(1.5))
        # fill the seams so the puffs read as one cloud
        for dx, dy, r in ((-14, 2, 8), (-4, -5, 11), (9, -2, 9), (17, 3, 6), (2, 4, 9)):
            cx, cy, rr = x + dx * scale, y + dy * scale, r * scale
            self.c.create_oval(self._sx(cx - rr), self._sx(cy - rr * 0.85), self._sx(cx + rr),
                               self._sx(cy + rr * 0.85), fill=fill, outline=fill)

    def draw_weather_back(self):
        kind, is_day = self.current_weather()
        if kind != self.wx_kind:
            self._wx_reset(kind)
        if kind is None:
            return
        t = self.t
        sky_x, sky_y = 30, 28
        clear_sky = kind in ("clear", "partly")
        # sun or moon
        if kind in ("clear", "partly", "cloudy", "fog"):
            if is_day:
                r = 10
                for k in range(8):
                    a = t * 0.6 + k * math.pi / 4
                    x0, y0 = sky_x + math.cos(a) * (r + 3), sky_y + math.sin(a) * (r + 3)
                    x1, y1 = sky_x + math.cos(a) * (r + 8), sky_y + math.sin(a) * (r + 8)
                    self.c.create_line(self._sx(x0), self._sx(y0), self._sx(x1), self._sx(y1),
                                       fill="#f5b83d", width=self._sx(2.5), capstyle="round")
                self.c.create_oval(self._sx(sky_x - r), self._sx(sky_y - r), self._sx(sky_x + r),
                                   self._sx(sky_y + r), fill="#ffd54f", outline="#f2b632",
                                   width=self._sx(2))
            else:
                r = 10
                self.c.create_oval(self._sx(sky_x - r), self._sx(sky_y - r), self._sx(sky_x + r),
                                   self._sx(sky_y + r), fill="#f4efcf", outline="#cfc79a",
                                   width=self._sx(1.5))
                # cut the crescent with the transparent key colour
                self.c.create_oval(self._sx(sky_x - r + 6), self._sx(sky_y - r - 3),
                                   self._sx(sky_x + r + 6), self._sx(sky_y + r - 3),
                                   fill=KEY, outline=KEY)
                if clear_sky:
                    for i, (sx, sy) in enumerate(((62, 14), (92, 30), (128, 12), (170, 24),
                                                  (205, 10), (52, 50))):
                        tw = (math.sin(t * 2.2 + i * 1.7) + 1) / 2
                        L = 1.5 + tw * 2.5
                        col = "#f6e7a6" if tw > 0.4 else "#c9b977"
                        self.c.create_line(self._sx(sx - L), self._sx(sy), self._sx(sx + L),
                                           self._sx(sy), fill=col, width=self._sx(1.4))
                        self.c.create_line(self._sx(sx), self._sx(sy - L), self._sx(sx),
                                           self._sx(sy + L), fill=col, width=self._sx(1.4))
        # clouds
        drift = (t * 6) % (W + 80)
        if kind == "partly":
            self._cloud((150 + drift) % (W + 80) - 40, 26, 1.0, "#ffffff", "#c9d3dc")
        elif kind in ("cloudy", "fog"):
            self._cloud((120 + drift) % (W + 80) - 40, 22, 1.1, "#eef1f4", "#aab6c2")
            self._cloud((40 + drift * 0.7) % (W + 80) - 40, 36, 0.9, "#e2e7ec", "#9aa7b4")
        elif kind in ("rain", "snow", "thunder"):
            dark = kind == "thunder"
            self._cloud((70 + drift * 0.5) % (W + 80) - 40, 20, 1.2,
                        "#8d98a5" if dark else "#d5dbe1", "#5f6b78" if dark else "#93a0ad")
            self._cloud((170 + drift * 0.5) % (W + 80) - 40, 26, 1.0,
                        "#7d8895" if dark else "#c8cfd6", "#5f6b78" if dark else "#8b98a5")

    def draw_weather_front(self):
        kind = self.wx_kind
        if kind is None:
            return
        dt = getattr(self, "dt", 1 / FPS)
        if kind in ("rain", "thunder"):
            for p in self.wx_parts:
                p[1] += 160 * p[2] * dt
                p[0] -= 30 * p[2] * dt
                if p[1] > GROUND + 4:
                    p[1], p[0] = random.uniform(18, 30), random.uniform(0, W + 30)
                x, y = p[0], p[1]
                self.c.create_line(self._sx(x), self._sx(y), self._sx(x - 2), self._sx(y + 7),
                                   fill="#6f9fd6", width=self._sx(1.6), capstyle="round")
            if kind == "thunder":
                if self.wx_flash <= 0 and random.random() < 0.006:
                    self.wx_flash = 0.25
                    self.wx_bolt_x = random.uniform(60, 190)
                if self.wx_flash > 0:
                    self.wx_flash -= dt
                    bx = self.wx_bolt_x
                    pts = [bx, 30, bx - 8, 52, bx - 1, 52, bx - 9, 78, bx + 9, 46, bx + 1, 46, bx + 7, 30]
                    self.c.create_polygon([self._sx(v) for v in pts], fill="#ffe14d",
                                          outline="#e0a800", width=self._sx(1.2))
        elif kind == "snow":
            for p in self.wx_parts:
                p[1] += 22 * p[2] * dt
                p[3] += dt * 1.5
                x = p[0] + math.sin(p[3]) * 6
                if p[1] > GROUND + 2:
                    p[1], p[0] = random.uniform(18, 30), random.uniform(0, W)
                r = 2.3 * p[2]
                self.c.create_oval(self._sx(x - r), self._sx(p[1] - r), self._sx(x + r),
                                   self._sx(p[1] + r), fill="#ffffff", outline="#9fb3c8",
                                   width=self._sx(0.8))
        elif kind == "fog":
            for i, y in enumerate((GROUND - 34, GROUND - 22, GROUND - 10)):
                off = math.sin(self.t * 0.5 + i) * 10
                pts = []
                for k in range(9):
                    x = 10 + k * 27 + off
                    pts += [self._sx(x), self._sx(y + math.sin(k * 1.3 + self.t + i) * 2)]
                self.c.create_line(pts, fill="#c3cad1", width=self._sx(2.2), smooth=True,
                                   capstyle="round", dash=(int(self._sx(14)), int(self._sx(6))))

    # ---------- usage tooltip
    @staticmethod
    def _bar(pct, n=12):
        pct = max(0.0, min(100.0, float(pct)))
        full = int(round(pct / 100 * n))
        return "\u2588" * full + "\u2591" * (n - full)

    @staticmethod
    def _when(ts):
        if not ts:
            return ""
        lt = time.localtime(ts)
        if ts - time.time() < 20 * 3600 and lt.tm_yday == time.localtime().tm_yday:
            return time.strftime("%H:%M", lt)
        return time.strftime("%a %H:%M", lt)

    @staticmethod
    def _ago(ts):
        if not ts:
            return "never"
        m = int((time.time() - ts) // 60)
        if m < 1:
            return "just now"
        if m < 60:
            return f"{m} min ago"
        return f"{m // 60} h {m % 60} min ago"

    def tip_text(self):
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                u = json.load(f)
        except Exception:
            return ("Claude usage\n\nNo data yet. It appears after\n"
                    "Claude Code's next reply.")
        lines = ["Claude usage", ""]
        rl = u.get("rate_limits") or {}
        now = time.time()
        rows = [("5-hour", rl.get("five_hour")), ("Weekly", rl.get("seven_day"))]
        sp = rl.get("spend_limit")
        if sp:
            rows.append(("Spend", sp))
        shown = False
        for name, w in rows:
            if not w or w.get("used_percentage") is None:
                continue
            shown = True
            if w.get("resets_at") and w["resets_at"] < now:
                lines.append(f"{name:<7}{'(window has reset)'}")
                continue
            pct = float(w["used_percentage"])
            lines.append(f"{name:<7}{self._bar(pct)} {pct:3.0f}%")
            extra = ""
            if w.get("used_usd") is not None and w.get("limit_usd") is not None:
                extra = f"${w['used_usd']:.0f} of ${w['limit_usd']:.0f}  "
            lines.append(f"{'':<7}{extra}resets {self._when(w.get('resets_at'))}")
        if not shown:
            lines.append("No limit data (shown for Pro/Max")
            lines.append("plans after the first reply).")
        if u.get("context_pct") is not None:
            lines.append("")
            lines.append(f"Context  {float(u['context_pct']):.0f}% used"
                         + (f"  ({u['model']})" if u.get("model") else ""))
        lines.append("")
        w = self.weather or {}
        if self.weather_var.get() == "auto" and w.get("kind"):
            temp = f"{w['temp']:.0f}°C, " if w.get("temp") is not None else ""
            night = "" if w.get("is_day", True) else " (night)"
            lines.append(f"Weather  {w.get('place', '')} {temp}{WEATHER_TEXT[w['kind']]}{night}")
            lines.append("")
        lines.append(f"updated {self._ago(u.get('rate_ts') or u.get('ts'))}")
        return "\n".join(lines)

    def tip_show(self, _e=None):
        if self.tip is None:
            self.tip = tk.Toplevel(self.root)
            self.tip.overrideredirect(True)
            self.tip.attributes("-topmost", True)
            self.tip_label = tk.Label(self.tip, justify="left", bg="#2b2b2b", fg="#f4f4f4",
                                      font=("Consolas", 9), padx=10, pady=8,
                                      relief="solid", bd=1)
            self.tip_label.pack()
        self.tip_label.configure(text=self.tip_text())
        self.tip.update_idletasks()
        tw, th = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        x = self.root.winfo_x() + (self.cw - tw) // 2
        y = self.root.winfo_y() - th - 4
        if y < 0:
            y = self.root.winfo_y() + self.ch + 4
        x = min(max(0, x), self.root.winfo_screenwidth() - tw)
        self.tip.geometry(f"+{x}+{y}")
        self.tip.deiconify()
        if self._tip_job is None:
            self._tip_job = self.root.after(1000, self._tip_refresh)

    def _tip_refresh(self):
        self._tip_job = None
        if self.tip is not None and self.tip.winfo_viewable():
            self.tip_label.configure(text=self.tip_text())
            self._tip_job = self.root.after(1000, self._tip_refresh)

    def tip_hide(self, _e=None):
        if self.tip is not None:
            self.tip.withdraw()

    # ---------- input
    def on_press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def on_drag(self, e):
        self.tip_hide()
        dx, dy = self._drag
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def on_release(self, _e):
        self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
        save_config(self.cfg)

    def on_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    @property
    def mascot(self):
        return self.mascot_var.get()

    def set_mascot(self):
        if self.mascot_var.get() == "custom":
            self.load_custom()
        self.cfg["mascot"] = self.mascot_var.get()
        save_config(self.cfg)

    def test(self, st):
        self.override = (st, time.time() + 5)

    def reset_pos(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = sw - self.cw - 30, sh - self.ch - 70
        self.root.geometry(f"+{x}+{y}")
        self.cfg["x"], self.cfg["y"] = x, y
        save_config(self.cfg)

    # ---------- tray icon
    def start_tray(self):
        if not pystray:
            return
        post = self.tray_q.put
        menu = pystray.Menu(
            pystray.MenuItem("Show / hide goat", lambda: post("toggle"), default=True),
            pystray.MenuItem("Reset position", lambda: post("reset")),
            pystray.MenuItem("Quit goat", lambda: post("quit")))
        try:
            self.tray = pystray.Icon("claude_goat", tray_image("idle"), "Claude Buddy", menu)
            self.tray_state = "idle"
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception:
            self.tray = None

    def handle_tray(self):
        while True:
            try:
                cmd = self.tray_q.get_nowait()
            except queue.Empty:
                break
            if cmd == "toggle":
                self.show() if self.hidden else self.hide()
            elif cmd == "reset":
                self.show()
                self.reset_pos()
            elif cmd == "quit":
                self.quit()
                return
        if self.tray and self.state != self.tray_state:
            self.tray_state = self.state
            labels = {"busy": "working...", "waiting": "needs your input", "idle": "idle"}
            try:
                self.tray.icon = tray_image(self.state)
                self.tray.title = f"Claude Buddy - {labels.get(self.state, self.state)}"
            except Exception:
                pass

    def hide(self):
        self.tip_hide()
        self.root.withdraw()
        self.hidden = True
        self.cfg["hidden"] = True
        save_config(self.cfg)

    def show(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.hidden = False
        self.cfg["hidden"] = False
        save_config(self.cfg)

    def quit(self):
        self.quitting = True
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.destroy()

    # ---------- state
    def poll(self):
        prev = self.state
        if self.override and time.time() < self.override[1]:
            self.state = self.override[0]
        else:
            self.override = None
            self.state = read_aggregate_state()
        # Claude needs you: come out of the tray (stays out until hidden again)
        if self.hidden and self.state == "waiting" and prev != "waiting":
            self.show()
        self.handle_tray()
        if self.quitting:
            return
        if not self.hidden:
            self.root.attributes("-topmost", True)
        self.root.after(POLL_MS, self.poll)

    # ---------- drawing primitives (goat-local coords: x toward the head, y up is negative)
    def P(self, x, y, ox=0.0, oy=0.0):
        return ((self.cx + self.dir * (x + ox)) * self.S, (GROUND + y + oy) * self.S)

    def poly(self, pts, fill, outline=LINE, width=2, smooth=False, ox=0.0, oy=0.0):
        flat = []
        for x, y in pts:
            flat.extend(self.P(x, y, ox, oy))
        self.c.create_polygon(flat, fill=fill, outline=outline, width=width * self.S,
                              smooth=smooth, joinstyle="round")

    def line(self, pts, fill, width, ox=0.0, oy=0.0, smooth=True):
        flat = []
        for x, y in pts:
            flat.extend(self.P(x, y, ox, oy))
        self.c.create_line(flat, fill=fill, width=width * self.S, smooth=smooth,
                           capstyle="round", joinstyle="round")

    def oval(self, x, y, rx, ry, fill, outline=LINE, width=2, ox=0.0, oy=0.0):
        cx, cy = self.P(x, y, ox, oy)
        self.c.create_oval(cx - rx * self.S, cy - ry * self.S, cx + rx * self.S, cy + ry * self.S,
                           fill=fill, outline=outline, width=width * self.S)

    def text(self, x, y, s, size, color, bold=True):
        self.c.create_text(x * self.S, y * self.S, text=s, fill=color,
                           font=("Segoe UI", int(size), "bold" if bold else "normal"))

    def bubble(self, x, y, s):
        """Speech bubble in screen-logical coords (not mirrored)."""
        w = 12 + 8 * len(s)
        x0, y0, x1, y1 = x - w / 2, y - 12, x + w / 2, y + 12
        r = 8
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x - 3, y1, x - 8 * self.dir, y1 + 9, x - 10 * self.dir + 2, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        self.c.create_polygon([v * self.S for v in pts], fill="white", outline=LINE,
                              width=2 * self.S, smooth=True)
        self.text(x, y, s, 11, LINE)

    def pill(self, s, force=False):
        if not force:
            return   # status text under the mascot is turned off
        w = 14 + 6.6 * len(s)
        x0, x1, y0, y1 = W / 2 - w / 2, W / 2 + w / 2, H - 22, H - 6
        self.c.create_polygon([v * self.S for v in
                               (x0 + 8, y0, x1 - 8, y0, x1, y0, x1, y1, x1 - 8, y1,
                                x0 + 8, y1, x0, y1, x0, y0)],
                              fill=PILL, outline=PILL, smooth=True)
        self.text(W / 2, (y0 + y1) / 2, s, 8, "white", bold=False)

    # ---------- goat parts (original round "plush" goat)
    def draw_head(self, ox, oy, eyes_open=True, chew=0.0, tilt=0.0):
        hx, hy = ox, oy + tilt
        # nub horns
        self.line([(22, -80), (19, -90), (13, -93)], HORN, 6, hx, hy)
        self.line([(32, -82), (32, -92), (27, -97)], HORN, 6, hx, hy)
        # far ear
        self.oval(11, -70, 9, 4.5, FUR_DARK, width=1.5, ox=hx, oy=hy)
        # head
        self.oval(28, -64, 21, 19, FUR, ox=hx, oy=hy)
        # fluffy forelock
        for i, (fx, fy) in enumerate([(18, -81), (25, -84), (32, -82)]):
            self.oval(fx, fy, 5, 4, FUR, outline=FUR, width=1, ox=hx, oy=hy)
        # muzzle
        self.oval(41, -56 + chew * 0.3, 10, 8, MUZZLE, width=1.5, ox=hx, oy=hy)
        self.oval(46, -58, 1.6, 1.3, LINE, outline=LINE, width=1, ox=hx, oy=hy)
        self.line([(41, -52 + chew * 0.4), (44, -50 + chew * 0.4), (47, -52 + chew * 0.4)],
                  LINE, 1.5, hx, hy)
        # near ear (floppy)
        self.oval(7, -64, 10, 4.5, FUR_DARK, width=1.5, ox=hx, oy=hy)
        # eye + blush
        if eyes_open:
            self.oval(34, -68, 2.6, 3.0, LINE, width=1, ox=hx, oy=hy)
            self.oval(34.8, -69.2, 0.9, 0.9, "white", outline="white", width=0.5, ox=hx, oy=hy)
        else:
            self.line([(31, -67), (34, -65.5), (37, -67)], LINE, 1.8, hx, hy)
        self.oval(36, -60, 3.5, 2.2, BLUSH, outline=BLUSH, width=1, ox=hx, oy=hy)

    def draw_standing(self, phase, bob, chew, legs_swing=True, tuck=0.0, eyes_open=True):
        oy = bob
        sw = 5 if legs_swing else 0
        legs = [(-18, 0.0, False), (12, math.pi, False), (-12, math.pi, True), (18, 0.0, True)]
        for near in (False, True):
            for hx, po, n in legs:
                if n != near:
                    continue
                fx = hx + math.sin(phase + po) * sw
                lift = max(0.0, math.cos(phase + po)) * 3 if legs_swing else 0
                fy = -lift - tuck
                self.line([(hx, -28 + oy), (fx, fy - 3)], FUR_DARK if not n else FUR, 10,
                          smooth=False)
                self.line([(fx - 0.5, fy - 2), (fx + 0.5, fy - 2)], HOOF, 9, smooth=False)
            if not near:
                wag = math.sin(self.t * 10) * 3
                self.oval(-31, -46 + wag * 0.5, 6, 5, FUR, width=1.5, oy=oy)
                self.oval(0, -38, 31, 20, FUR, oy=oy)
                # belly tuft
                self.oval(4, -27, 14, 5, FUR, outline=FUR, width=1, oy=oy)
        self.draw_head(0, oy - 8, eyes_open=eyes_open, chew=chew)

    def tilted_oval(self, cx, cy, rx, ry, deg, fill, outline=LINE, width=2, oy=0.0):
        """Ellipse rotated by deg (positive lifts the front/head end)."""
        th = math.radians(deg)
        pts = []
        for k in range(28):
            a = 2 * math.pi * k / 28
            u, v = rx * math.cos(a), ry * math.sin(a)
            pts.append((cx + u * math.cos(th) + v * math.sin(th),
                        cy - u * math.sin(th) + v * math.cos(th)))
        self.poly(pts, fill, outline=outline, width=width, oy=oy)

    def draw_typing(self):
        """Busy: the goat sits on its haunches and hammers a laptop."""
        t = self.t
        # frantic, slightly irregular tempo
        if not hasattr(self, "type_phase"):
            self.type_phase, self.type_rate = 0.0, 24.0
        self.type_rate += random.uniform(-3, 3)
        self.type_rate = min(32.0, max(17.0, self.type_rate))
        self.type_phase += self.type_rate * getattr(self, 'dt', 1 / FPS)
        phase = self.type_phase
        # one hoof up while the other is down; sharpened so they snap to the ends
        w = math.sin(phase)
        sharp = math.copysign(abs(w) ** 0.45, w)
        slam_n = (1 + sharp) / 2           # 1 = near hoof down on the keys
        slam_f = 1 - slam_n                # far hoof does the opposite
        cyc = int(phase // (2 * math.pi))
        jit_n = random.Random(cyc * 2).uniform(-8, 8)        # new spot each slam
        jit_f = random.Random(cyc * 2 + 1).uniform(-4, 4)
        hit = max(slam_n, slam_f) > 0.97
        shake = random.uniform(-1.5, 1.5) if hit and getattr(self, 'laptop_shake', True) else 0.0
        bob = -abs(sharp) * 1.6

        # ---- laptop on the ground in front
        lx = shake
        self.poly([(64, -5), (94, -5), (99, -46), (69, -46)], "#3a3f4b", outline="#23262e", ox=lx)
        self.poly([(67, -8), (91.5, -8), (95.6, -43), (71, -43)], "#1d2129", outline="#1d2129",
                  width=1, ox=lx)
        cols = ["#7fd18b", "#f2c46d", "#8ab4f8", "#e88f8f", "#c9ccd3"]
        step = int(t * 7)
        rnd = random.Random(step)
        for i in range(6):
            y = -38 + i * 5.4
            x0 = 72.5 + (5 - i) * 0.55 + rnd.choice([0, 0, 2, 4])
            self.line([(x0, y), (x0 + rnd.uniform(5, 15), y)], cols[(i + step) % len(cols)], 1.6,
                      ox=lx, smooth=False)
        # keyboard deck in perspective: back edge at the hinge, front edge toward the goat
        self.poly([(64, -9), (96, -9), (91, 0), (40, 0)], "#c3c8d0", outline="#5d6470", ox=lx)
        self.line([(40, 0), (91, 0), (96, -9)], "#8f96a2", 2, ox=lx, smooth=False)   # deck edge
        for row, (y, x0, x1) in enumerate(((-6.5, 58, 92), (-3, 50, 87))):
            n = 5
            for k in range(n):
                kx = x0 + (x1 - x0) * k / n
                self.line([(kx + 1, y), (kx + (x1 - x0) / n - 1.5, y)], "#8a919c", 1.5,
                          ox=lx, smooth=False)

        def foreleg(shoulder, up, down, s, col):
            hx = up[0] + (down[0] - up[0]) * s
            hy = up[1] + (down[1] - up[1]) * s
            sx, sy = shoulder
            self.line([(sx, sy + bob), (hx, hy)], col, 8, smooth=False)
            self.line([(hx - 0.5, hy + 1), (hx + 1.5, hy + 1)], HOOF, 8, smooth=False)
            return hx, hy

        # ---- far foreleg (behind the body)
        far = foreleg((12, -40), (50 + jit_f, -44), (54 + jit_f, -6), slam_f, FUR_DARK)
        # ---- sitting body: rump on the ground, chest raised
        wag = math.sin(t * 22) * 3
        self.oval(-29, -10 + wag * 0.4, 6, 5, FUR, width=1.5)                 # tail
        self.tilted_oval(-2, -26, 27, 18, 38, FUR, oy=bob)                     # body
        self.oval(-12, -13, 14, 11, FUR, oy=bob * 0.5)                         # haunch
        self.line([(-4, -4), (10, -3)], FUR, 8, smooth=False)                  # folded hind leg
        self.line([(11, -3), (13, -3)], HOOF, 8, smooth=False)
        # ---- near foreleg in front
        near = foreleg((18, -38), (60 + jit_n, -46), (64 + jit_n, -3), slam_n, FUR)
        # ---- head over the keyboard, chewing hard
        chew = math.sin(t * 20) * 1.8
        self.draw_head(4 + random.uniform(-0.6, 0.6), bob - 2, eyes_open=True, chew=chew)

        # ---- impact bursts + glyphs flying off the keys
        if not hasattr(self, "keys_fx"):
            self.keys_fx = []
        for (hx, hy), sl in ((near, slam_n), (far, slam_f)):
            if sl > 0.97 and random.random() < 0.5 and len(self.keys_fx) < 8:
                self.keys_fx.append([hx, hy - 4, random.uniform(-0.6, 0.6),
                                     random.choice("#{};<>/*!=$"), 0.0])
        alive = []
        for fx in self.keys_fx:
            fx[0] += fx[2]
            fx[1] -= 1.1
            fx[4] += getattr(self, 'dt', 1 / FPS)
            if fx[4] < 0.8:
                alive.append(fx)
                x, y = self.P(fx[0], fx[1])
                self.c.create_text(x, y, text=fx[3], fill="#6d7bb0",
                                   font=("Consolas", int(9 * self.S), "bold"))
        self.keys_fx = alive
        for (hx, hy), sl in ((near, slam_n), (far, slam_f)):
            if sl > 0.97:
                for a in (-2.4, -1.6, -0.8):
                    dx, dy = math.cos(a), math.sin(a)
                    self.line([(hx + dx * 6, hy + dy * 6), (hx + dx * 11, hy + dy * 11)],
                              "#f2a541", 2, smooth=False)

    def draw_grass(self, front):
        """A small grass patch the goat sits/stands on (back blades, then front blades)."""
        rnd = random.Random(7)
        if not front:
            self.oval(4, -1, 60, 7, "#9fd27c", outline="#6fa652", width=1.5)
            self.oval(4, -2, 48, 4, "#b4de94", outline="#b4de94", width=1)
        n = 16 if front else 22
        for i in range(n):
            x = rnd.uniform(-52, 60)
            h = rnd.uniform(5, 11) if not front else rnd.uniform(4, 8)
            sway = math.sin(self.t * 1.8 + x * 0.15) * 1.6
            base_y = -3 if not front else 3.5
            col = rnd.choice(["#6fa652", "#7dba5c", "#5d9445"])
            self.line([(x, base_y), (x + sway * 0.5, base_y - h * 0.6), (x + sway + 1.5, base_y - h)],
                      col, 1.8)
        if not front:
            # a couple of tiny flowers
            for fx, col in ((-40, "#ffffff"), (46, "#ffd84d"), (52, "#ffffff")):
                self.oval(fx, -8, 2, 2, col, outline="#d9c76a", width=0.8)

    def draw_lying(self, breath):
        b = breath
        self.oval(-31, -22, 6, 5, FUR, width=1.5)
        self.line([(16, -4), (24, -4)], FUR, 10, smooth=False)
        self.line([(26, -4), (27, -4)], HOOF, 9, smooth=False)
        self.oval(0, -17 - b / 2, 32, 16 + b / 2, FUR)
        self.draw_head(-2, 26, eyes_open=False, chew=0, tilt=b * 0.4)

    # ---------- Pip (original mascot)
    def draw_pip(self, st, phase=0.0, bob=0.0, squash=0.0):
        oy = bob
        # feet
        if st == "idle":
            feet = [(-12, -4, False), (12, -4, True)]
        else:
            sw = 6 if st == "busy" else 0
            feet = [(-9 + math.sin(phase) * sw, -5 - max(0, math.cos(phase)) * 3, False),
                    (9 + math.sin(phase + math.pi) * sw,
                     -5 - max(0, math.cos(phase + math.pi)) * 3, True)]
        for fx, fy, near in feet:
            if not near:
                self.oval(fx, fy + (0 if st == "idle" else (oy if st == "waiting" else oy * 0.3)), 8, 5,
                          PIP_DARK, width=1.5)
        # body
        rx, ry = 27 + squash, 30 - squash
        cy = -8 - ry + (0 if st == "idle" else oy)
        top = cy - ry
        # antenna
        sway = math.sin(self.t * (6 if st == "busy" else 2)) * (4 if st == "busy" else 1.5)
        self.line([(0, top + 2), (2 + sway * 0.5, top - 9), (5 + sway, top - 15)], LINE, 2.5)
        if st == "busy":
            glow = BULB_ON if int(self.t * 4) % 2 == 0 else BULB_MID
        elif st == "waiting":
            glow = BULB_ALERT
        else:
            glow = BULB_OFF
        if st == "busy":
            self.oval(5 + sway, top - 19, 8, 8, BULB_HALO, outline=BULB_HALO, width=1)
        self.oval(5 + sway, top - 19, 5, 5, glow, width=1.5)
        # body
        self.oval(0, cy, rx, ry, PIP, width=2)
        self.oval(4, cy + ry * 0.35, rx * 0.55, ry * 0.45, PIP_BELLY, outline=PIP_BELLY, width=1)
        # arm
        if st == "waiting":
            self.line([(-20, cy - 2), (-30, cy - 18)], PIP, 7)
            self.line([(18, cy - 2), (28, cy - 18)], PIP, 7)
        else:
            swing = math.sin(phase) * 4 if st == "busy" else 0
            self.line([(-22, cy + 2), (-26 - swing, cy + 12)], PIP_DARK, 6)
        # face (three-quarter view toward facing direction)
        ey = cy - ry * 0.25
        if st == "idle":
            for ex in (4, 16):
                self.line([(ex - 3, ey + 1), (ex, ey + 2.5), (ex + 3, ey + 1)], LINE, 1.8)
            self.oval(11, ey + 9, 2.5, 1.5, LINE, outline=LINE, width=1)
        else:
            look = math.sin(self.t * 0.7) * 0.8
            for ex in (4, 16):
                self.oval(ex + look, ey, 3.2, 4.0, LINE, width=1)
                self.oval(ex + look + 1, ey - 1.5, 1.1, 1.1, "white", outline="white", width=0.5)
            if st == "waiting":
                self.oval(10, ey + 10, 3.5, 3, LINE, outline=LINE, width=1)
            else:
                self.line([(7, ey + 8), (10, ey + 10), (13, ey + 8)], LINE, 1.8)
        self.oval(-2, ey + 6, 3.5, 2, BLUSH, outline=BLUSH, width=1)
        self.oval(22, ey + 6, 3, 2, BLUSH, outline=BLUSH, width=1)
        for fx, fy, near in feet:
            if near:
                self.oval(fx, fy + (0 if st == "idle" else (oy if st == "waiting" else oy * 0.3)), 8, 5, PIP_DARK, width=1.5)
        return top

    # ---------- frame
    def tick(self):
        # advance by real elapsed time, so animations (and GIFs) play at true speed
        # even when Windows delivers timer ticks late
        now = time.monotonic()
        last = getattr(self, "_last_tick", None)
        dt = 1.0 / FPS if last is None else min(0.1, max(0.0, now - last))
        self._last_tick = now
        self.dt = dt
        if self.hidden:                 # nothing to draw while tucked in the tray
            self.root.after(int(1000 / FPS), self.tick)
            return
        self.t += dt
        st = self.state
        if st != self.shown:
            self.shown = st
            self.zzz = []
        self.c.delete("all")
        self.draw_weather_back()

        if self.mascot == "custom" and self.custom:
            # your own images are shown exactly as they are: no extra bob or hop
            label = {"busy": "working", "waiting": "needs your input"}.get(st, "idle")
            top = self.draw_custom(st, 0)
            if st == "waiting":
                self.dir = 1
                self.bubble(W / 2 + 34, max(16, top - 6), "Hey!")
            self.pill(label)
            self.draw_weather_front()
            self.root.after(int(1000 / FPS), self.tick)
            return
        if self.mascot == "custom":
            self.pill("no images yet - right-click me", force=True)
            self.draw_weather_front()
            self.root.after(int(1000 / FPS), self.tick)
            return

        if st == "busy":
            speed = 38.0
            self.cx += self.dir * speed * dt
            lo, hi = 72, W - 72
            if self.cx > hi:
                self.cx, self.dir = hi, -1
            elif self.cx < lo:
                self.cx, self.dir = lo, 1
            phase = self.t * 11
            bob = -abs(math.sin(phase)) * 2.5
            chew = math.sin(self.t * 14) * 1.6
            if self.mascot == "pip":
                self.draw_pip("busy", phase, bob)
            else:
                self.cx, self.dir = 88, 1
                self.draw_typing()
            dots = "." * (1 + int(self.t * 2.5) % 3)
            self.pill("working" + dots)

        elif st == "waiting":
            hop = -abs(math.sin(self.t * 5.5)) * 16
            if self.mascot == "pip":
                land = max(0.0, 1 - abs(hop) / 4) * 3
                self.draw_pip("waiting", 0, hop, squash=land)
                hx, _ = self.P(20, 0)
                self.bubble(hx / self.S + self.dir * 22, 28 + hop * 0.3, "Hey!")
            else:
                self.draw_grass(front=False)
                self.draw_standing(0, hop, 0, legs_swing=False, tuck=abs(hop) * 0.15)
                self.draw_grass(front=True)
                hx, _ = self.P(40, -98 + hop)
                self.bubble(hx / self.S + self.dir * 18, 22 + hop * 0.3, "Mäh!")
            self.pill("needs your input")

        else:  # idle
            breath = (math.sin(self.t * 1.6) + 1) * 1.2
            if self.mascot == "pip":
                self.draw_pip("idle", squash=5 + breath)
                zx0, zy0 = self.cx + self.dir * 26, 104.0
            else:
                self.draw_grass(front=False)
                self.draw_lying(breath)
                self.draw_grass(front=True)
                zx0, zy0 = self.cx + self.dir * 40, 100.0
            # floating Zzz
            if random.random() < 0.025 and len(self.zzz) < 3:
                self.zzz.append([zx0, zy0, 0.0])
            for z in self.zzz:
                z[0] += self.dir * 0.35
                z[1] -= 0.5
                z[2] += dt
            self.zzz = [z for z in self.zzz if z[2] < 3.5]
            for zx, zy, age in self.zzz:
                self.text(zx, zy, "z", 9 + age * 3, "#7a8aa0")
            self.pill("idle")

        self.draw_weather_front()
        self.root.after(int(1000 / FPS), self.tick)


def main():
    lock = single_instance()
    if lock is None:
        return   # already running
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    root = tk.Tk()
    root.title("Claude Buddy")
    Goat(root)
    root.mainloop()


if __name__ == "__main__":
    main()
