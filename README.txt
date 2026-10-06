CLAUDE BUDDY - a floating mascot for Claude Code (Windows)
Mascots: the goat (default), Pip, or your own images - switch via right-click menu.

Needs: Python 3 for Windows (python.org installer includes Tkinter).

INSTALL
  1. Unzip anywhere, open a terminal in the folder.
  2. python install.py
  3. Fully quit the Claude desktop app (tray icon > Quit) and reopen it.

The mascot appears bottom-right, always on top, and starts at login.
  working...        Claude is busy (Pip walks, bulb glows; goat trots)
  needs your input  permission prompt / question (hops and calls out)
  idle              finished (sleeps)

Hover over the mascot to see your usage limits (5-hour and weekly %,
reset times, context used). Limits come from a small Claude Code mod
(usage-mod, works in the desktop app) and the status line (terminal),
for Pro/Max plans, after the first reply in a session.

Left-drag to move. Right-click: test states, switch mascot, reset position, quit.

TRAY ICON
  A goat icon sits in the system tray (by the clock; it may be under the ^
  overflow arrow - drag it onto the taskbar to keep it visible). Its dot shows
  the state: grey idle, green working, red needs you.
  Right-click the goat > "Hide to tray" to tuck it away; click the tray icon
  to bring it back. While hidden it pops back up by itself when Claude needs
  your input. Needs pystray + pillow (the installer pip-installs them).

STARTING IT
  install.py is a one-time setup (rerun it only after editing these files).
  The goat auto-starts at login. If you quit it, press Windows, type
  "Claude Buddy" and hit Enter (Start menu shortcut), or run:
    pythonw "%USERPROFILE%\.claude\goat\goat_widget.pyw"
  Only one goat runs at a time, so starting it twice is harmless.
Several Claude Code sessions at once are fine: any busy -> mascot works.

Options:  python install.py --no-startup   (no auto-start at login)
          python install.py --uninstall

USING YOUR OWN IMAGES
  Right-click > "Open my images folder" (it is ~/.claude/goat/custom), add:
    idle.png  or idle.gif
    busy.png  or busy.gif      (animated GIFs play at 10 frames/sec)
    waiting.png or waiting.gif
  To carry your images to another PC, put them in this repo's custom/
  folder: install.py copies them into ~/.claude/goat/custom (and picks
  "My images" on a fresh install).
  Any missing state reuses another image. Then right-click >
  "Mascot: My images" (or "Reload my images" after changing files).
  Tips: about 80-120 px tall looks right. Hard-edged transparency
  (pixel art, or GIF) looks cleanest; soft PNG edges can show a faint
  pink fringe. Images are shown as-is, centred, with a bob/hop.

STATUS LINE
  The installer sets a Claude Code status line (it feeds the tooltip).
  If you already had one, it is kept: its output is still shown, and
  --uninstall restores it exactly.
