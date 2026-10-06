"""
Claude Code hook -> writes this session's state for the goat widget.

Usage (from settings.json hooks):  python goat_hook.py busy|idle|waiting|end
Claude Code sends the hook payload as JSON on stdin; we only use session_id
(and the notification message). Prints nothing and always exits 0, so it can
never block or alter Claude Code.
"""
import json
import os
import sys
import time

SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "goat", "sessions")


def main():
    state = sys.argv[1] if len(sys.argv) > 1 else "idle"
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    sid = str(data.get("session_id") or "default")
    sid = "".join(ch for ch in sid if ch.isalnum() or ch in "-_")[:80] or "default"
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = os.path.join(SESSIONS_DIR, sid + ".json")

    if state == "end":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return

    event = str(data.get("hook_event_name", ""))
    tool = str(data.get("tool_name", ""))

    # Claude asking you a multiple-choice question is a tool call, but it's you it waits for
    if event == "PreToolUse" and tool == "AskUserQuestion":
        state = "waiting"

    if state == "waiting":
        ntype = str(data.get("notification_type", ""))
        msg = str(data.get("message", "")).lower()
        definite = (
            event in ("PermissionRequest", "Elicitation", "PreToolUse")
            or ntype in ("permission_prompt", "elicitation_dialog", "elicitation_url_dialog",
                         "agent_needs_input")
            or "permission" in msg
        )
        if not definite:
            # e.g. the "waiting for your input" reminder after a finished reply:
            # only bleat if Claude was mid-task
            current = "idle"
            try:
                with open(path, "r", encoding="utf-8") as f:
                    current = json.load(f).get("state", "idle")
            except Exception:
                pass
            if current != "busy":
                return

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"state": state, "ts": time.time(),
                   "cwd": data.get("cwd", "")}, f)
    for _ in range(5):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:      # widget reading the file on Windows
            time.sleep(0.02)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
