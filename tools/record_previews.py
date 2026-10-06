"""
Record preview GIFs of the mascot in each state and weather -> preview/*.gif

    python tools/record_previews.py

Opens the widget (without tray icon or config saving, so a running goat is
untouched) in a corner of the screen, forces each state/weather, and grabs
the window. Keep the top-left of the screen clear while it runs (~1 min).
"""
import importlib.machinery
import importlib.util
import os
import sys
import tkinter as tk

from PIL import Image, ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "preview")
SECONDS = 3.0
FRAME_MS = 66              # ~15 fps
WARMUP_MS = 1200
POS = (40, 40)

loader = importlib.machinery.SourceFileLoader("goat_widget", os.path.join(ROOT, "goat_widget.pyw"))
spec = importlib.util.spec_from_loader("goat_widget", loader)
gw = importlib.util.module_from_spec(spec)
loader.exec_module(gw)
gw.pystray = None                                   # no tray icon
gw.save_config = lambda cfg: None                   # never touch the real config
gw.load_config = lambda: {"mascot": "goat", "weather": "off",
                          "custom_dir": os.path.join(ROOT, "custom")}

STATES = ("idle", "busy", "waiting")
WEATHERS = ("clear", "partly", "cloudy", "rain", "snow", "fog", "thunder", "night")
CLIPS = ([(f"{m}_{st}", m, st, "off") for m in ("goat", "pip", "custom") for st in STATES]
         + [(f"weather_{w}", "goat", "busy", w) for w in WEATHERS])


def backdrop(size):
    """Soft desktop-like gradient behind the (transparent) widget."""
    w, h = size
    top, bot = (122, 160, 196), (64, 98, 140)
    bg = Image.new("RGB", size)
    px = bg.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(a + (b - a) * t) for a, b in zip(top, bot))
        for x in range(w):
            px[x, y] = c
    return bg


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    os.makedirs(OUT, exist_ok=True)
    root = tk.Tk()
    root.tk.call("tk", "scaling", 96 / 72)          # record at 1x, whatever the DPI
    g = gw.Goat(root)
    root.attributes("-transparentcolor", "")        # grab the colour key, not the desktop
    root.geometry(f"+{POS[0]}+{POS[1]}")
    bg = backdrop((g.cw, g.ch))
    key = tuple(int(gw.KEY[i:i + 2], 16) for i in (1, 3, 5))
    clips = list(CLIPS)

    def start_clip():
        if not clips:
            root.destroy()
            return
        name, mascot, state, weather = clips.pop(0)
        g.mascot_var.set(mascot)
        g.weather_var.set(weather)
        g.override = (state, float("inf"))
        frames = []

        def grab():
            x, y = root.winfo_rootx(), root.winfo_rooty()
            img = ImageGrab.grab(bbox=(x, y, x + g.cw, y + g.ch), all_screens=True).convert("RGB")
            mask = Image.eval(img.split()[0], lambda v: 0)
            px, mp = img.load(), mask.load()
            for yy in range(img.height):
                for xx in range(img.width):
                    if px[xx, yy] == key:
                        mp[xx, yy] = 255
            frames.append(Image.composite(bg, img, mask))
            if len(frames) < SECONDS * 1000 / FRAME_MS:
                root.after(FRAME_MS, grab)
            else:
                pal = [f.quantize(colors=255, method=Image.Quantize.MEDIANCUT) for f in frames]
                path = os.path.join(OUT, name + ".gif")
                pal[0].save(path, save_all=True, append_images=pal[1:], duration=FRAME_MS,
                            loop=0, optimize=True)
                print(f"  {name}.gif  {os.path.getsize(path) // 1024} KB")
                start_clip()

        root.after(WARMUP_MS, grab)

    root.after(500, start_clip)
    root.mainloop()


if __name__ == "__main__":
    main()
