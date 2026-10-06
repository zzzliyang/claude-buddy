"""
Claude Code status line -> saves usage-limit data for the goat's hover tooltip.

Claude Code pipes session JSON to this script on stdin (including
rate_limits for Pro/Max plans). We save the useful bits to
~/.claude/goat/usage.json, then print a status line: either the output of
your previous status line command (if you had one) or a compact default.
"""
import json
import os
import shutil
import subprocess
import sys
import time

GOAT_DIR = os.path.join(os.path.expanduser("~"), ".claude", "goat")
USAGE_FILE = os.path.join(GOAT_DIR, "usage.json")
ORIG_FILE = os.path.join(GOAT_DIR, "statusline_orig.json")


def save(data):
    try:
        old = {}
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            pass
        ctx = data.get("context_window") or {}
        new = {
            "ts": time.time(),
            "model": (data.get("model") or {}).get("display_name", ""),
            "context_pct": ctx.get("used_percentage", old.get("context_pct")),
            "session": data.get("session_name") or "",
            # keep the last known limits if this payload doesn't carry them
            "rate_limits": data.get("rate_limits") or old.get("rate_limits"),
            "rate_ts": time.time() if data.get("rate_limits") else old.get("rate_ts"),
        }
        os.makedirs(GOAT_DIR, exist_ok=True)
        tmp = USAGE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new, f)
        for _ in range(5):
            try:
                os.replace(tmp, USAGE_FILE)
                break
            except PermissionError:
                time.sleep(0.02)
    except Exception:
        pass


def run_original(raw):
    try:
        with open(ORIG_FILE, "r", encoding="utf-8") as f:
            cmd = (json.load(f) or {}).get("command")
    except Exception:
        return None
    if not cmd:
        return None
    bash = shutil.which("bash")
    argv = [bash, "-c", cmd] if bash else ["powershell", "-NoProfile", "-Command", cmd]
    try:
        r = subprocess.run(argv, input=raw, capture_output=True, text=True,
                           encoding="utf-8", timeout=10)
        return r.stdout.rstrip("\n")
    except Exception:
        return None


def default_line(data):
    parts = []
    model = (data.get("model") or {}).get("display_name")
    if model:
        parts.append(f"[{model}]")
    ctx = (data.get("context_window") or {}).get("used_percentage")
    if ctx is not None:
        parts.append(f"ctx {ctx:.0f}%")
    rl = data.get("rate_limits") or {}
    lim = []
    if (rl.get("five_hour") or {}).get("used_percentage") is not None:
        lim.append(f"5h {rl['five_hour']['used_percentage']:.0f}%")
    if (rl.get("seven_day") or {}).get("used_percentage") is not None:
        lim.append(f"7d {rl['seven_day']['used_percentage']:.0f}%")
    if lim:
        parts.append(" · ".join(lim))
    return " | ".join(parts)


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception:
        data = {}
    save(data)
    out = run_original(raw)
    print(out if out is not None else default_line(data))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
