# terminal.py — the system shell for JARVIS, on any operating system.
#
# Three modes, because the user explicitly asked for all three behaviours:
#   1. run  — execute a shell command in the background, return its output.
#             Best for: ls, df, apt list --installed, systemctl status, etc.
#   2. type — open a REAL terminal window and run the command in it, so the
#             user watches it happen. Best for: interactive installs, sudo
#             prompts, htop, watch, anything the user wants to see.
#   3. open — open a terminal window and stop there.
#
# WHY MODE 2 NO LONGER TYPES
#   It used to open a blank terminal, guess its window title to focus it, and
#   send keystrokes at whatever had focus. That is three ways to fail: a race
#   between the window appearing and the keystrokes arriving, a title match
#   that misses on a localized or differently-themed desktop, and — worst —
#   the keystrokes landing in the WRONG window, silently typing a command into
#   the user's editor or chat box. It also only worked on X11.
#   The terminal is now given the command as an argument and runs it itself, so
#   there is no focus to guess at and no keystrokes to misdeliver. The old
#   blind-typing path survives only as a fallback for the one case the new path
#   cannot express: type a command WITHOUT running it.
#
# Safety: generic shell access is powerful. Destructive patterns are blocked
# outright; sudo + destructive combos are parked behind the on-screen
# CONFIRM gate (core/confirm.py) — the model can never self-confirm.
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from core import os_detect
except Exception:                                       # pragma: no cover
    os_detect = None


# ── Distro detection ─────────────────────────────────────────────────────────
# Now delegated to core/os_detect, which is cached and shared. The private
# /etc/os-release parser that used to live here had a second, disagreeing copy
# in open_app.py, and both reported Mint's ID_LIKE="ubuntu debian" as Ubuntu.
def _distro_info() -> dict:
    if os_detect is None:
        return {"id": "", "name": "", "version": "", "mint_edition": ""}
    try:
        info = dict(os_detect.linux_distro())
    except Exception:
        return {"id": "", "name": "", "version": "", "mint_edition": ""}
    # Keep the historical key names so nothing downstream has to change.
    return {"id": info.get("id", ""), "name": info.get("name", ""),
            "version": info.get("version", ""), "mint_edition": info.get("edition", "")}


def _is_mint() -> bool:
    if os_detect is None:
        return False
    try:
        return os_detect.linux_distro().get("family") == "mint"
    except Exception:
        return False


# ── Terminal emulator handling (delegated to core/os_detect) ─────────────────
# The candidate list and, critically, each emulator's argument style now live
# in one place. Duplicating that list here is how the two copies drifted.
def _find_terminal() -> str:
    """Path of the terminal that would be used, or None."""
    if os_detect is None:
        for name in ("gnome-terminal", "cinnamon-terminal", "mate-terminal",
                     "xfce4-terminal", "konsole", "xterm"):
            found = shutil.which(name)
            if found:
                return found
        return None
    try:
        return os_detect.find_terminal()[2] or None
    except Exception:
        return None


def open_terminal(cwd: str = "") -> str:
    """Open a terminal window with nothing typed in it."""
    argv, kind = _terminal_argv("", keep_open=True, cwd=cwd)
    if kind == "none" or not argv:
        return ("No terminal emulator found. On Linux I looked for gnome-terminal, "
                "cinnamon-terminal, mate-terminal, xfce4-terminal, konsole and xterm.")
    if _launch(argv, cwd):
        return f"Terminal opened ({os.path.basename(argv[0])})."
    return f"Could not open a terminal ({os.path.basename(argv[0])})."


def _terminal_argv(command: str, keep_open: bool = True, cwd: str = ""):
    """(argv, kind) for opening a terminal that runs `command`."""
    if os_detect is not None:
        try:
            return os_detect.terminal_command(command, keep_open=keep_open, cwd=cwd)
        except Exception:
            pass
    # os_detect unavailable: fall back to the old Linux-only guessing.
    term = _find_terminal()
    if not term:
        return None, "none"
    flags = ["-e"] if term in ("xfce4-terminal", "konsole", "xterm",
                               "x-terminal-emulator") else ["--"]
    return [term, *flags, "bash", "-c", command or "bash"], "argv"


def _launch(argv: list, cwd: str = "") -> bool:
    if os_detect is not None:
        try:
            return os_detect.launch_detached(argv, cwd)
        except Exception:
            pass
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, cwd=cwd or None)
        return True
    except Exception as e:
        print(f"[Terminal] launch failed: {e}")
        return False


def type_command(command: str, press_enter: bool = True, cwd: str = "",
                 keep_open: bool = True) -> str:
    """Open a real terminal window and run `command` in it, visibly.

    press_enter=True (the default) hands the command to the terminal emulator
    as an argument, so the terminal runs it and the user watches. That is the
    reliable path and the one used almost always.

    press_enter=False means "show me the command but do not run it", which the
    argument path cannot express — it falls back to opening a terminal and
    typing without pressing Enter, so the user can edit or reject it first.
    """
    cmd = (command or "").strip()
    if not cmd:
        return "No command given to run in the terminal."
    blocked = _safety_check(cmd)
    if blocked == "block":
        return (f"Refused to run '{cmd[:80]}' — that command can destroy the system. "
                "I did not run anything.")
    if blocked == "confirm":
        from core import confirm as _confirm
        if _confirm.pending_title():
            return ("There is already a confirmation waiting on screen. "
                    "Ask the user to answer that one first.")
        return _confirm.request(
            key="terminal-type", title=f"Run in a terminal: {cmd[:60]}",
            detail=f"JARVIS will run this in a visible terminal window:\n{cmd[:300]}",
            run=lambda c=cmd, w=cwd, k=keep_open: _run_in_terminal_now(c, w, k),
        )
    return _run_in_terminal_now(cmd, cwd, keep_open) if press_enter else _type_blind(cmd, False)


def _run_in_terminal_now(cmd: str, cwd: str = "", keep_open: bool = True) -> str:
    """Open a terminal window that runs `cmd`. No focusing, no keystrokes."""
    argv, kind = _terminal_argv(cmd, keep_open=keep_open, cwd=cwd)
    if kind == "none" or not argv:
        return ("No terminal emulator is available on this system, so I cannot show "
                "you a terminal window. I can still run the command in the "
                "background and read you the output — say 'run' instead of "
                "'type'.")
    if _launch(argv, cwd):
        where = f" in {cwd}" if cwd else ""
        tail = " The window stays open so you can read the output." if keep_open else ""
        return f"Running it in a terminal{where}: {cmd[:120]}{tail}"
    return f"Could not open a terminal window to run '{cmd[:80]}'."


def _type_blind(cmd: str, press_enter: bool = True) -> str:
    """Fallback: open a terminal, focus it by guessing its title, type into it.

    Kept only for press_enter=False, because that is the one request the
    argument-passing path genuinely cannot serve. It is less reliable by
    nature — see the note at the top of this file — so anything that actually
    needs to RUN goes through _run_in_terminal_now instead.
    """
    open_terminal()
    time.sleep(0.6)
    try:
        from actions import computer_control as _cc
        _focus_terminal()
        time.sleep(0.4)
        _cc._smart_type(cmd, clear_first=False)
        if press_enter:
            time.sleep(0.2)
            _cc._press_backend("enter")
        return (f"Typed in a terminal: {cmd[:120]}" +
                (" (not run — press Enter when you want it)." if not press_enter else ""))
    except Exception as e:
        # Focusing by title failed — most likely no window manager support, or a
        # desktop where pygetwindow cannot see the window. Say so rather than
        # leaving the user with a terminal containing nothing.
        return (f"I opened a terminal but could not type into it ({e}). "
                f"The command was: {cmd[:120]}")


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
        r = subprocess.run(_shell_argv(cmd), capture_output=True, text=True,
                           timeout=timeout, cwd=workdir, **_hide_kwargs())
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


def _shell_argv(command: str) -> list:
    """argv that runs a shell string, WITHOUT shell=True.

    This used to be `subprocess.run(cmd, shell=True)`, which on Windows means
    cmd.exe re-splits and re-interprets a string that Python has already quoted
    — so a command containing a quote or a caret behaved differently there than
    the same command did on Linux, with no way for the caller to tell. Naming
    the shell explicitly and passing argv makes the two platforms agree, and
    gives the Windows CREATE_NO_WINDOW flag somewhere to live.
    """
    if os_detect is not None:
        try:
            return os_detect.shell_argv(command)
        except Exception:
            pass
    return command                                     # last resort: old behaviour


def _hide_kwargs() -> dict:
    if os_detect is not None:
        try:
            if os_detect.is_windows():
                return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        except Exception:
            pass
    return {}


# ── Help: common commands for the OS this is actually running on ─────────────
# This text is what the user sees in the Jarvis window when they ask what they
# can say, so it has to describe THEIR machine. It used to be a hardcoded Mint
# cheat-sheet shown on every platform, which on Windows and macOS was simply a
# list of commands that do not exist there. Built per-OS now.
def _help_text() -> str:
    if os_detect is None:
        return ("Say e.g. \"run ls\" to execute a command in the background, or "
                "\"type ls in the terminal\" to watch it happen in a window. "
                "Destructive commands are blocked; dangerous ones ask for CONFIRM.")

    try:
        family = os_detect.linux_distro().get("family", "linux")
    except Exception:
        family = "linux"

    header = (f"{os_detect.describe_os()}\n"
              f"Shell: {os_detect.os_info().get('shell') or 'shell'}"
              f"  ·  Terminal: {os.path.basename(os_detect.find_terminal()[2]) or 'none found'}\n\n")

    tail = ("\nModes: run = background, output back to chat. "
            "type = runs in a visible terminal window you can watch "
            "(for sudo prompts and interactive tools). "
            "open = just opens a terminal. "
            "Destructive commands are blocked; dangerous sudo ones ask for on-screen CONFIRM first.")

    if os_detect.is_windows():
        body = """PACKAGES:  winget install <pkg> · choco install <pkg> · scoop install <pkg>
SYSTEM:    systeminfo · ipconfig /all · netstat -ano · tasklist · taskkill /PID <n> /F
           Get-Process · Get-Service · Get-ChildItem · Get-Content
DISK:      Get-PSDrive C · dir C:\\ · tree /F
FILES:     type <file> · copy / move / del / mkdir · findstr /s "text" *.txt
NETWORK:   ping 8.8.8.8 · nslookup github.com · curl https://x · Test-NetConnection github.com -Port 443
TASKS:     schtasks /query /fo LIST"""
    elif os_detect.is_macos():
        body = """PACKAGES:  brew install <pkg> · mas install <num> · softwareupdate --install-rosetta
SYSTEM:    sw_vers · system_profiler · launchctl list · defaults read <domain>
DISK:      df -h · diskutil list · du -sh <dir>
FILES:     ls -la · pwd · cd / cp / mv / rm / mkdir -p · cat / less / head / tail -f
           grep -r "text" . · find . -name "*.log" · chmod +x file · tar -xzf f.tar.gz
NETWORK:   ping -c4 8.8.8.8 · ifconfig · netstat -an · lsof -i :3000 · curl -I <url> · open -a <App>"""
    elif family in ("ubuntu", "debian", "mint", "linux"):
        extra = ""
        if family == "mint":
            extra = ("\nMINT TOOLS:  cinnamon-settings · nemo · xed · timeshift · "
                     "mintsources · mintdrivers · inxi -Fxxxz")
        body = f"""PACKAGES (Debian/Ubuntu base):
  sudo apt update / sudo apt upgrade / sudo apt install <pkg> / sudo apt remove <pkg>
  apt search <name> / apt show <pkg> / apt list --installed | dpkg -l | sudo dpkg -i file.deb
  flatpak list / flatpak install flathub <app> · mintupdate · mintinstall

SYSTEM:
  systemctl status|start|stop|restart <svc> · journalctl -xe · uname -a · hostnamectl
  timedatectl · lsblk · blkid · df -h · du -sh <dir> · free -h · top / htop · ps aux | uptime

FILES:
  ls -la · pwd · cd <dir> · cp -r / mv / rm / mkdir -p / touch · cat / less / head / tail -f
  grep -r "text" . · find . -name "*.log" · chmod +x file · tar -xzf f.tar.gz · zip / unzip

NETWORK:
  ping -c4 8.8.8.8 · ip a · ss -tulpn · nmcli device status · curl -I <url> · wget <url> · ufw status{extra}"""
    elif family == "arch":
        body = """PACKAGES:  pacman -Syu · pacman -S <pkg> · pacman -R <pkg> · pacman -Ss <name> · paru/yay
SYSTEM:    systemctl status|start|stop|restart <svc> · journalctl -xe · uname -a · lsblk
           df -h · free -h · top / htop · ps aux
FILES:     ls -la · pwd · cd / cp / mv / rm / mkdir -p · cat / less / grep -r "t" . · tar -xzf f.tgz
NETWORK:   ping -c4 8.8.8.8 · ip a · ss -tulpn · nmcli device status · curl -I <url>"""
    elif family in ("fedora", "rhel", "centos"):
        body = """PACKAGES:  sudo dnf update · sudo dnf install <pkg> · sudo dnf remove <pkg> · rpm -qa
SYSTEM:    systemctl status|start|stop|restart <svc> · journalctl -xe · uname -a · lsblk
           df -h · free -h · top / htop · ps aux
FILES:     ls -la · pwd · cd / cp / mv / rm / mkdir -p · cat / less / grep -r "t" . · tar -xzf f.tgz
NETWORK:   ping -c4 8.8.8.8 · ip a · ss -tulpn · nmcli device status · curl -I <url>"""
    else:
        body = """SYSTEM:    uname -a · lsblk · df -h · free -h · top / htop · ps aux · uptime
FILES:     ls -la · pwd · cd <dir> · cp / mv / rm / mkdir -p · cat / less / grep -r "text" .
NETWORK:   ping -c4 8.8.8.8 · ip a · ss -tulpn · curl -I <url>"""

    return header + body + tail


_HELP = _help_text()          # kept for anything that imported the old constant


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
        text = _help_text()
        if player:
            try:
                if hasattr(player, "show_content"):
                    player.show_content("TERMINAL — commands on this machine", text)
            except Exception:
                pass
        return text

    if action == "info":
        d = _distro_info()
        term = _find_terminal() or "(none found)"
        return (f"OS: {os_detect.describe_os() if os_detect else d.get('name','unknown')}"
                f" · Mint: {'yes' if _is_mint() else 'no'}"
                f" · Terminal: {term}"
                f" · Shell: {os_detect.os_info().get('shell') if os_detect else '?'}"
                f" · run + visible-terminal both ready.")

    if action == "open":
        return open_terminal(cwd)

    if action in ("type", "visible", "window", "show"):
        if not command:
            return ("Tell me which command to run in a terminal, e.g. action=type "
                    "with command='sudo apt update'.")
        press_enter = str(params.get("press_enter", "true")).lower().strip() not in ("false", "0", "no")
        keep_open = str(params.get("keep_open", "true")).lower().strip() not in ("false", "0", "no")
        return type_command(command, press_enter, cwd, keep_open)

    if action == "run":
        if not command:
            return "Tell me which command to run, e.g. run action with command='ls -la'."
        return run_command(command, timeout, cwd)

    return (f"Unknown terminal action '{action}'. Use action=run | type | open | info | help.")


TOOL = {
    "name": "terminal",
    "description": (
        "The system shell for the operating system this assistant is running on "
        "(see the [OPERATING SYSTEM] block in the system instructions — that "
        "block is the authority on syntax, not this description). "
        "Use action='run' with a shell command to execute it in the background and get its output "
        "(ls, df, grep, tasklist, ipconfig, systemctl status, etc). "
        "Use action='type' to OPEN A REAL TERMINAL WINDOW AND RUN the command in it, "
        "so the user watches it happen — this is what they mean by 'run it in the "
        "terminal', 'open a terminal and do it', 'show me'. Use it for installs, "
        "sudo prompts, htop, watch, or anything interactive; add press_enter=false "
        "to type it WITHOUT running it. "
        "Use action='open' to just open a terminal. "
        "Use action='help' to show the command list for THIS machine in the Jarvis window. "
        "For anything that means reading or changing a CODEBASE — writing a "
        "feature, fixing a test, refactoring — use opencode_agent instead of "
        "driving shell commands one at a time. "
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
                "description": (
                    "Shell command in the syntax of the detected OS, e.g. "
                    "'ls -la' on Linux/macOS or 'dir' on Windows"
                ),
            },
            "cwd": {
                "type": "STRING",
                "description": "Working directory (optional)",
            },
            "timeout": {
                "type": "NUMBER",
                "description": "Timeout in seconds for action='run' (default 30, max 120)",
            },
            "press_enter": {
                "type": "STRING",
                "description": (
                    "For action='type': 'true' (default) runs the command in the "
                    "terminal window; 'false' only types it, for the user to review "
                    "and run themselves"
                ),
            },
            "keep_open": {
                "type": "STRING",
                "description": (
                    "For action='type': 'true' (default) leaves the terminal window "
                    "open after the command finishes so the output stays readable"
                ),
            },
        },
        "required": ["action"],
    },
    "handler": terminal,
}
