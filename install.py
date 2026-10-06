"""
Install / uninstall the Claude Goat for Claude Code on Windows.

    python install.py              install hooks + start goat + auto-start at login
    python install.py --no-startup install without auto-start at login
    python install.py --uninstall  remove hooks and auto-start (keeps a settings backup)

Edits the user-level Claude Code settings (~/.claude/settings.json); a backup
is written next to it before every change.
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
GOAT_DIR = os.path.join(CLAUDE_DIR, "goat")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                           "Start Menu", "Programs", "Startup")
STARTUP_FILE = os.path.join(STARTUP_DIR, "claude_goat.vbs")
START_MENU_LNK = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                              "Start Menu", "Programs", "Claude Buddy.lnk")
ICON_FILE = os.path.join(GOAT_DIR, "goat.ico")
CONFIG_FILE = os.path.join(GOAT_DIR, "config.json")
MARK = "goat_hook.py"
STATUS_MARK = "goat_status.py"
ORIG_FILE = os.path.join(GOAT_DIR, "statusline_orig.json")
MOD_DIR = os.path.join(GOAT_DIR, "usage-mod")

# event -> (matcher or None, state arg)
EVENTS = {
    "UserPromptSubmit": (None, "busy"),
    "PreToolUse": ("*", "busy"),
    "PostToolUse": ("*", "busy"),
    "Notification": (None, "waiting"),
    "PermissionRequest": ("*", "waiting"),
    "Elicitation": (None, "waiting"),
    "Stop": (None, "idle"),
    "SessionStart": (None, "idle"),
    "SessionEnd": (None, "end"),
}


def fwd(p):
    return p.replace("\\", "/")


def pythons():
    exe = sys.executable
    d = os.path.dirname(exe)
    py = os.path.join(d, "python.exe")
    pyw = os.path.join(d, "pythonw.exe")
    if not os.path.exists(py):
        py = exe
    if not os.path.exists(pyw):
        pyw = py
    return py, pyw


def load_settings():
    if not os.path.exists(SETTINGS):
        return {}
    with open(SETTINGS, "r", encoding="utf-8") as f:
        txt = f.read().strip()
    if not txt:
        return {}
    try:
        return json.loads(txt)
    except json.JSONDecodeError as e:
        sys.exit(f"Could not parse {SETTINGS} ({e}). Fix it or move it aside, then rerun.")


def save_settings(s):
    os.makedirs(CLAUDE_DIR, exist_ok=True)
    if os.path.exists(SETTINGS):
        bak = SETTINGS + time.strftime(".goat-backup-%Y%m%d-%H%M%S")
        shutil.copy2(SETTINGS, bak)
        print(f"  backup: {bak}")
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def strip_goat(settings):
    hooks = settings.get("hooks", {})
    for ev in list(hooks.keys()):
        groups = []
        for g in hooks[ev]:
            hs = [h for h in g.get("hooks", []) if MARK not in str(h.get("command", ""))]
            if hs:
                g = dict(g, hooks=hs)
                groups.append(g)
        if groups:
            hooks[ev] = groups
        else:
            del hooks[ev]
    if not hooks:
        settings.pop("hooks", None)
    return settings


def make_shortcut(py, pyw, widget):
    """Start menu entry so the goat can be relaunched by typing "Claude Buddy"."""
    icon = ""
    r = subprocess.run([py, "-c", "import runpy, sys; runpy.run_path(sys.argv[1])['save_icon'](sys.argv[2])",
                        widget, ICON_FILE], capture_output=True)
    if r.returncode == 0:
        icon = ICON_FILE
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:GOAT_LNK); "
          "$s.TargetPath = $env:GOAT_PYW; $s.Arguments = '\"' + $env:GOAT_WIDGET + '\"'; "
          "$s.WorkingDirectory = $env:GOAT_DIR; $s.Description = 'Claude Buddy desktop mascot'; "
          "if ($env:GOAT_ICON) { $s.IconLocation = $env:GOAT_ICON }; $s.Save()")
    env = dict(os.environ, GOAT_LNK=START_MENU_LNK, GOAT_PYW=pyw, GOAT_WIDGET=widget,
               GOAT_DIR=GOAT_DIR, GOAT_ICON=icon)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True)
    if r.returncode == 0 and os.path.exists(START_MENU_LNK):
        print(f"  Start menu shortcut: {START_MENU_LNK}")
    else:
        print("  could not create the Start menu shortcut")


def install(startup=True):
    print("Installing Claude Goat...")
    os.makedirs(GOAT_DIR, exist_ok=True)
    for name in ("goat_widget.pyw", "goat_hook.py", "goat_status.py"):
        shutil.copy2(os.path.join(HERE, name), os.path.join(GOAT_DIR, name))
    # your own mascot images live in the repo's custom/ folder; point the goat at it
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {"mascot": "custom"}              # fresh machine: show your images
    cfg["custom_dir"] = os.path.join(HERE, "custom")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    print(f"  your images: {cfg['custom_dir']}")
    # usage mod: gives the tooltip usage limits in the desktop app (no status line there)
    if os.path.isdir(MOD_DIR):
        shutil.rmtree(MOD_DIR)
    shutil.copytree(os.path.join(HERE, "usage-mod"), MOD_DIR,
                    ignore=shutil.ignore_patterns("*.test.ts", "types"))
    hook = fwd(os.path.join(GOAT_DIR, "goat_hook.py"))
    widget = os.path.join(GOAT_DIR, "goat_widget.pyw")
    py, pyw = pythons()

    # optional tray icon support; the goat still works without it
    r = subprocess.run([py, "-m", "pip", "install", "--user", "--quiet", "--disable-pip-version-check",
                        "pystray", "pillow"], capture_output=True, text=True)
    if r.returncode == 0:
        print("  tray icon support installed (pystray, pillow)")
    else:
        print("  could not pip install pystray/pillow - goat runs without a tray icon")

    s = strip_goat(load_settings())
    hooks = s.setdefault("hooks", {})
    for ev, (matcher, state) in EVENTS.items():
        group = {"hooks": [{"type": "command",
                            "command": f'"{fwd(py)}" "{hook}" {state}',
                            "timeout": 5}]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        hooks.setdefault(ev, []).append(group)
    # status line: feeds usage limits to the tooltip, chains any existing one
    cur = s.get("statusLine")
    if cur and STATUS_MARK not in str(cur.get("command", "")):
        with open(ORIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f)
        print(f"  your existing status line is kept (saved to {ORIG_FILE})")
    keep = {k: v for k, v in (cur or {}).items() if k in ("padding", "refreshInterval")}
    s["statusLine"] = {"type": "command",
                       "command": f'"{fwd(py)}" "{fwd(os.path.join(GOAT_DIR, "goat_status.py"))}"',
                       **keep}
    env = s.setdefault("env", {})
    dirs = [d for d in str(env.get("CLAUDE_CODE_PLUGIN_DIRS", "")).split(os.pathsep) if d]
    mod = fwd(MOD_DIR)
    if mod not in dirs:
        dirs.append(mod)
    env["CLAUDE_CODE_PLUGIN_DIRS"] = os.pathsep.join(dirs)
    save_settings(s)
    print(f"  hooks, status line + usage mod added to {SETTINGS}")

    if startup and os.path.isdir(STARTUP_DIR):
        with open(STARTUP_FILE, "w", encoding="utf-8") as f:
            f.write('CreateObject("WScript.Shell").Run """{}"" ""{}""", 0, False\n'
                    .format(pyw, widget))
        print(f"  auto-start: {STARTUP_FILE}")

    make_shortcut(py, pyw, widget)

    flags = 0x00000008 if sys.platform == "win32" else 0  # DETACHED_PROCESS
    subprocess.Popen([pyw, widget], creationflags=flags, close_fds=True)
    print("  goat started (bottom-right of your screen).")
    print("\nDone. Fully quit and reopen the Claude desktop app (and any terminal sessions)\n"
          "so they load the new hooks and usage mod.")


def uninstall():
    print("Uninstalling Claude Goat...")
    s = strip_goat(load_settings())
    env = s.get("env") or {}
    if "CLAUDE_CODE_PLUGIN_DIRS" in env:
        dirs = [d for d in env["CLAUDE_CODE_PLUGIN_DIRS"].split(os.pathsep)
                if d and "goat/usage-mod" not in d.replace("\\", "/")]
        if dirs:
            env["CLAUDE_CODE_PLUGIN_DIRS"] = os.pathsep.join(dirs)
        else:
            env.pop("CLAUDE_CODE_PLUGIN_DIRS")
        if not env:
            s.pop("env", None)
    if STATUS_MARK in str((s.get("statusLine") or {}).get("command", "")):
        orig = None
        try:
            with open(ORIG_FILE, "r", encoding="utf-8") as f:
                orig = json.load(f)
        except Exception:
            pass
        if orig:
            s["statusLine"] = orig
            os.remove(ORIG_FILE)
            print("  previous status line restored")
        else:
            s.pop("statusLine", None)
    save_settings(s)
    if os.path.exists(STARTUP_FILE):
        os.remove(STARTUP_FILE)
    if os.path.exists(START_MENU_LNK):
        os.remove(START_MENU_LNK)
    print("  hooks, auto-start and Start menu shortcut removed. Right-click the goat > Quit goat to close it.")
    print(f"  (files remain in {GOAT_DIR}; delete that folder if you like)")


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install(startup="--no-startup" not in sys.argv)
