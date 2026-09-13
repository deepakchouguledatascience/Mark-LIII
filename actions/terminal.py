#terminal.py — Linux Mint (incl. LMDE) terminal control for JARVIS.
#
# Two modes, because the user explicitly asked for both behaviours:
#   1. run  — execute a shell command in the background, return its output.
#             Best for: ls, df, apt list --installed, systemctl status, etc.
#   2. type — open a terminal window and TYPE the command there (with Enter),
#             so the user sees it happen. Best for: interactive installs,
#             sudo prompts, htop, watch, anything the user wants to watch.
#
# Safety: generic shell access is powerful. Destructive patterns are blocked
# outright; sudo + destructive combos are parked behind the on-screen
# CONFIRM gate (core/confirm.py) — the model can never self-confirm.
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

# ── Mint / distro detection ──────────────────────────────────────────────────
def _distro_info() -> dict:
    info = {"id": "", "name": "", "version": "", "mint_edition": ""}
    try:
        txt = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
        for line in txt.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.strip().lower()] = v.strip().strip('"')
        info["id"] = info.get("id", "")
        info["name"] = info.get("pretty_name", info.get("name", ""))
        info["version"] = info.get("version_id", "")
    except Exception:
        pass
    try:
        mint = Path("/etc/linuxmint/info")
        if mint.exists():
            info["mint_edition"] = mint.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0][:120]
    except Exception:
        pass
    return info


def _is_mint() -> bool:
    d = _distro_info()
    blob = f"{d.get('id','')} {d.get('name','')} {d.get('mint_edition','')}".lower()
    return "mint" in blob or "lmde" in blob


# ── Terminal emulator handling (Mint defaults first) ─────────────────────────
_TERMINALS = (
    "gnome-terminal",      # Mint Cinnamon default
    "cinnamon-terminal",
    "mate-terminal",
    "xfce4-terminal",
    "konsole",
    "x-terminal-emulator",
    "xterm",
)


def _find_terminal() -> str | None:
    for t in _TERMINALS:
        if shutil.which(t):
            return t
    return None


def open_terminal() -> str:
    term = _find_terminal()
    if not term:
        return "No terminal emulator found (looked for gnome-terminal, mate-terminal, xfce4-terminal, konsole, xterm)."
    try:
        subprocess.Popen([term], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        time.sleep(0.8)
        return f"Terminal opened: {term}."
    except Exception as e:
        return f"Could not open terminal ({term}): {e}"


def _focus_terminal() -> None:
    """Best-effort focus of the terminal window. Never raises."""
    try:
        from actions import computer_control as _cc
        for title in ("Terminal", "gnome-terminal", "xterm", "konsole"):
            try:
                _cc._focus_window(title)
                time.sleep(0.3)
                return
            except Exception:
                continue
    except Exception:
        pass


def type_command(command: str, press_enter: bool = True) -> str:
    """Open/focus a terminal and TYPE the command there visibly."""
    cmd = (command or "").strip()
    if not cmd:
        return "No command given to type."
    blocked = _safety_check(cmd)
    if blocked == "block":
        return (f"Refused to type '{cmd[:80]}' — that command can destroy the system. "
                "I did not type anything.")
    if blocked == "confirm":
        from core import confirm as _confirm
        if _confirm.pending_title():
            return ("There is already a confirmation waiting on screen. "
                    "Ask the user to answer that one first.")
        return _confirm.request(
            key="terminal-type", title=f"Type in terminal: {cmd[:60]}",
            detail=f"JARVIS will type this into your terminal:\n{cmd[:300]}",
            run=lambda c=cmd: (type_command.__wrapped__(c) if hasattr(type_command, "__wrapped__") else _type_now(c)),
        )
    return _type_now(cmd, press_enter)


def _type_now(cmd: str, press_enter: bool = True) -> str:
    # Make sure a terminal exists, then type into it.
    open_terminal()
    _focus_terminal()
    try:
        from actions import computer_control as _cc
        time.sleep(0.4)
        _cc._smart_type(cmd, clear_first=False)
        if press_enter:
            time.sleep(0.2)
            _cc._press_backend("enter")
        return f"Typed in terminal: {cmd[:120]}"
    except Exception as e:
        return f"Typing in terminal failed: {e}"


# ── Safety gate ──────────────────────────────────────────────────────────────
# block  → refuse outright. confirm → on-screen CONFIRM gate. None → allowed.
_BLOCK_PATTERNS = (
    r"\brm\s+-rf\s+/\s*(--no-preserve-root)?\s*$",
    r"\brm\s+-rf\s+~/?\s*$",
    r"\brm\s+-rf\s+/\s+",
    r"\bmkfs(\s|\.)",
    r"\bdd\s+.*of=/dev/",
    r":\(\)\s*\{\s*:\|\:&\s*\}\s*;",   # fork bomb
    r"\bchmod\s+-R\s+777\s+/\b",
    r"\bchown\s+-R\s+.*\s+/\b",
    r">\s*/dev/sd[a-z]",
    r"\bwipefs\b.*--all",
)

_CONFIRM_PATTERNS = (
    r"^\s*sudo\s+rm\s+-rf?\b",
    r"^\s*sudo\s+mkfs\b",
    r"^\s*sudo\s+dd\b",
    r"\bdd\s+.*of=",
    r"^\s*sudo\s+apt\s+(purge|remove)\b.*(kernel|systemd|cinnamon|mintsystem|grub)",
    r"\bshutdown\b|\bpoweroff\b|\breboot\b|\bhalt\b|\binit\s+[06]\b",
    r"\b:\s*>\s*/etc/(passwd|shadow|fstab|sudoers)",
)


def _safety_check(cmd: str) -> str | None:
    c = (cmd or "").strip()
    low = c.lower()
    for pat in _BLOCK_PATTERNS:
        try:
            if re.search(pat, c) or re.search(pat, low):
                return "block"
        except Exception:
            continue
    for pat in _CONFIRM_PATTERNS:
        try:
            if re.search(pat, c) or re.search(pat, low):
                return "confirm"
        except Exception:
            continue
    # shutdown-type words only count when they are the actual command
    return None


# ── Background execution ─────────────────────────────────────────────────────
_MAX_OUTPUT = 4000
_DEFAULT_TIMEOUT = 30


def run_command(command: str, timeout: float = _DEFAULT_TIMEOUT, cwd: str = "") -> str:
    cmd = (command or "").strip()
    if not cmd:
        return "No command given."
    verdict = _safety_check(cmd)
    if verdict == "block":
        return (f"Refused '{cmd[:80]}' — that command can destroy the system. Nothing was run.")
    if verdict == "confirm":
        from core import confirm as _confirm
        if _confirm.pending_title():
            return ("There is already a confirmation waiting on screen. "
                    "Ask the user to answer that one first.")
        return _confirm.request(
            key="terminal-run", title=f"Run: {cmd[:60]}",
            detail=f"JARVIS will run this shell command:\n{cmd[:300]}",
            run=lambda c=cmd, t=timeout, w=cwd: _run_now(c, t, w),
        )
    return _run_now(cmd, timeout, cwd)


def _run_now(cmd: str, timeout: float = _DEFAULT_TIMEOUT, cwd: str = "") -> str:
    try:
        timeout = max(1.0, min(float(timeout or _DEFAULT_TIMEOUT), 120.0))
    except Exception:
        timeout = _DEFAULT_TIMEOUT
    workdir = None
    if cwd:
        p = Path(cwd).expanduser()
        if not p.is_dir():
            return f"Directory not found: {cwd}"
        workdir = str(p)
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout, cwd=workdir)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        body = out
        if err and err not in out:
            body = (body + "\n" + err).strip() if body else err
        if not body:
            body = "(no output)"
        if len(body) > _MAX_OUTPUT:
            body = body[:_MAX_OUTPUT] + f"\n… (truncated, exit={r.returncode})"
            return f"$ {cmd}\n(exit {r.returncode})\n{body}"
        status = "ok" if r.returncode == 0 else f"exit {r.returncode}"
        return f"$ {cmd}\n({status})\n{body}"
    except subprocess.TimeoutExpired:
        return f"'{cmd[:80]}' timed out after {timeout:g}s. Nothing else was run."
    except Exception as e:
        return f"Could not run '{cmd[:80]}': {e}"


# ── Help: common Mint / LMDE commands grouped ────────────────────────────────
_HELP = """Linux Mint terminal (works on Mint + LMDE) — say e.g. "run ls in terminal" or "type sudo apt update in terminal".

PACKAGES (Debian/Ubuntu base):
  sudo apt update / sudo apt upgrade / sudo apt install <pkg> / sudo apt remove <pkg>
  apt search <name> / apt show <pkg> / apt list --installed | dpkg -l | sudo dpkg -i file.deb
  flatpak list / flatpak install flathub <app> · mintupdate (Update Manager) · mintinstall (Software Manager)

SYSTEM:
  systemctl status|start|stop|restart <svc> · journalctl -xe · uname -a · hostnamectl
  timedatectl · lsblk · blkid · df -h · du -sh <dir> · free -h · top / htop · ps aux | uptime

FILES:
  ls -la · pwd · cd <dir> · cp -r / mv / rm / mkdir -p / touch · cat / less / head / tail -f
  grep -r "text" . · find . -name "*.log" · chmod +x file · tar -xzf f.tar.gz · zip / unzip

NETWORK:
  ping -c4 8.8.8.8 · ip a · ss -tulpn · nmcli device status · curl -I <url> · wget <url> · ufw status

MINT TOOLS:
  cinnamon-settings · nemo · xed · timeshift · mintsources · mintdrivers · inxi -Fxxxz

Modes: run = background + output back to chat. type = typed visibly into a terminal window (for sudo prompts / interactive tools). Destructive commands are blocked; sudo+dangerous ones ask for on-screen CONFIRM first."""


def terminal(parameters: dict = None, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "run")).lower().strip() or "run"
    command = str(params.get("command", "")).strip()
    cwd = str(params.get("cwd", "")).strip()
    try:
        timeout = float(params.get("timeout", _DEFAULT_TIMEOUT))
    except Exception:
        timeout = _DEFAULT_TIMEOUT

    if player:
        try:
            player.write_log(f"[Terminal] {action} {command[:60]}")
        except Exception:
            pass

    if action in ("help", "list", "commands"):
        if player:
            try:
                if hasattr(player, "show_content"):
                    player.show_content("TERMINAL — Linux Mint commands", _HELP)
            except Exception:
                pass
        return _HELP

    if action == "info":
        d = _distro_info()
        term = _find_terminal() or "(none found)"
        return (f"Distro: {d.get('name','unknown')} · Mint: {'yes' if _is_mint() else 'not detected'} · "
                f"Terminal: {term} · Shell run + type-to-terminal ready.")

    if action == "open":
        return open_terminal()

    if action == "type":
        if not command:
            return "Tell me which command to type, e.g. type action with command='sudo apt update'."
        press_enter = str(params.get("press_enter", "true")).lower().strip() not in ("false", "0", "no")
        return type_command(command, press_enter)

    if action == "run":
        if not command:
            return "Tell me which command to run, e.g. run action with command='ls -la'."
        return run_command(command, timeout, cwd)

    return f"Unknown terminal action '{action}'. Use action=run | type | open | info | help."


TOOL = {
    "name": "terminal",
    "description": (
        "Linux Mint (incl. LMDE/Debian) terminal. "
        "Use action='run' with a shell command to execute it in the background and get its output "
        "(ls, apt list, systemctl status, df, grep, etc). "
        "Use action='type' with a command to OPEN a terminal window and TYPE it there visibly "
        "(for installs, sudo prompts, htop, or whenever the user says 'type ... in/to the terminal'). "
        "Use action='open' to just open a terminal. "
        "Use action='help' to list common Mint commands. "
        "Destructive commands are blocked; dangerous sudo commands ask for on-screen CONFIRM."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "run | type | open | info | help",
            },
            "command": {
                "type": "STRING",
                "description": "Shell command, e.g. 'ls -la', 'sudo apt update', 'systemctl status ssh'",
            },
            "cwd": {
                "type": "STRING",
                "description": "Working directory for action='run' (optional)",
            },
            "timeout": {
                "type": "NUMBER",
                "description": "Timeout in seconds for action='run' (default 30, max 120)",
            },
            "press_enter": {
                "type": "STRING",
                "description": "For action='type': 'true' presses Enter after typing (default), 'false' types only",
            },
        },
        "required": ["action"],
    },
    "handler": terminal,
}
