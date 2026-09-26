"""
core/os_detect.py — one place that answers "what machine am I on?"

THE PROBLEM THIS REPLACES
    Nine modules each rolled their own platform check. `platform.system()` in
    five of them, a hand-rolled /etc/os-release parser in two more (and the two
    disagreed with each other), `sys.platform == "win32"` in a seventh, and two
    of them defaulted to "windows" — so on this Linux Mint box a missing config
    key silently told the assistant it was on Windows. Meanwhile
    actions/terminal.py advertised itself as "Linux Mint terminal" in its tool
    description while calling `subprocess.run(shell=True)`, which is portable —
    so the one file that most needed to work everywhere was the one file that
    only worked on one platform.

THE DESIGN HERE
    Detection happens once, is cached, and everything else asks this module.
    It answers four questions the rest of the app keeps re-asking:

        1. Which OS, and on Linux, which distro?      os_key() / linux_distro()
        2. What can I run, and where is it?           find_cli()
        3. How do I run a shell string here?          shell_argv()
        4. How do I open a terminal window here?     find_terminal()

    The values are deliberately boring and low-cased ("windows"/"macos"/"linux")
    because they end up in JSON config, in the system prompt, and in tool
    descriptions — where "Darwin" and "macOS" and "mac" drifting apart is how
    a branch stops being taken.

MINT IS FIRST-CLASS, NOT A SPECIAL CASE
    Linux Mint is the primary target, and `linux_distro()` reports
    family="mint" with the edition (Cinnamon/MATE/XFCE) read from
    /etc/linuxmint/info. But the detection is derived from ID/ID_LIKE, not from
    a hardcoded "mint" branch, so Ubuntu, Debian, Fedora and Arch resolve
    correctly on a machine that happens to share the codebase.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

# Windows-only constants, resolved once. Referenced as attributes of the module
# so that a caller never has to remember to guard with `if is_windows()` before
# touching them — the guard belongs at the call site, not the definition site.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
_IS_WINDOWS = sys.platform.startswith("win")


# ── 1. Which OS? ─────────────────────────────────────────────────────────────

def os_key() -> str:
    """Normalized OS key: "windows", "macos" or "linux".

    Anything unrecognized is reported as "linux" rather than raising: the
    assistant still has a working shell there, and a hard failure at startup
    over an exotic kernel would be a worse outcome than a slightly wrong label.
    """
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    return "linux"


def is_windows() -> bool:
    return os_key() == "windows"


def is_macos() -> bool:
    return os_key() == "macos"


def is_linux() -> bool:
    return os_key() == "linux"


@lru_cache(maxsize=1)
def _os_release() -> dict:
    """Parsed /etc/os-release, lower-cased keys, empty dict if unavailable.

    Prefers platform.freedesktop_os_release() (stdlib, 3.10+) and falls back to
    reading the file directly, because that helper raises on the handful of
    systems where the file is missing or malformed and a distro name is not
    worth an exception traceback.
    """
    try:
        data = platform.freedesktop_os_release()
        if isinstance(data, dict) and data:
            return {str(k).lower(): str(v) for k, v in data.items()}
    except Exception:
        # The helper raises on systems where the file is absent or malformed,
        # and a distro name is not worth an exception traceback.
        pass

    for path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        try:
            if not path.is_file():
                continue
            out: dict = {}
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip().lower()] = value.strip().strip('"').strip("'")
            if out:
                return out
        except OSError:
            continue
    return {}


@lru_cache(maxsize=1)
def linux_distro() -> dict:
    """Linux distribution identity. Returns a "linux" fallback shape off Linux.

    Keys: family, id, name, version, edition, like.
      family  "mint" | "ubuntu" | "debian" | "fedora" | "arch" | "linux"
      name    human label, e.g. "Linux Mint 22.1 Xia"
      edition Mint's desktop environment ("cinnamon"/"mate"/"xfce"), else ""
    """
    fallback = {"family": "linux", "id": "", "name": "Linux", "version": "",
                "edition": "", "like": ""}
    if not is_linux():
        return fallback

    release = _os_release()
    distro_id = (release.get("id") or "").lower()
    like = (release.get("id_like") or "").lower()
    pretty = (release.get("pretty_name") or "").strip()
    version = (release.get("version_id") or "").strip()
    if not pretty:
        name = (release.get("name") or "Linux").strip()
        pretty = f"{name} {version}".strip()

    # Most specific first. Resolved by scanning THIS list rather than by trusting
    # the order of ID_LIKE, because Mint declares ID_LIKE="ubuntu debian" — a
    # naive "first token wins" reports a Linux Mint 22.3 Cinnamon box as
    # family=ubuntu, which then routes every settings command to the wrong
    # backend and tells the user they are on Ubuntu. Checking our own priority
    # order against both ID and ID_LIKE makes "linuxmint" resolve to mint.
    known = ("mint", "ubuntu", "pop", "fedora", "arch", "debian", "manjaro",
             "opensuse", "rhel", "centos")
    like_tokens = set(like.split())
    family = ""
    for candidate in known:
        if (distro_id == candidate or distro_id.endswith(candidate)
                or candidate in like_tokens):
            family = candidate
            break
    family = family or "linux"

    edition = ""
    if family == "mint":
        mint = _mint_info()
        raw_edition = mint.get("edition", "")
        edition = raw_edition.lower()
        if raw_edition:
            pretty = f"{pretty} {raw_edition}".strip() if pretty else raw_edition
        # Mint's PRETTY_NAME is just "Linux Mint 22.3"; the codename ("Zena")
        # only appears in /etc/linuxmint/info, and it is what a user recognises.
        codename = mint.get("codename", "")
        if codename and codename.lower() not in pretty.lower():
            pretty = f"{pretty} {codename.capitalize()}"

    return {"family": family, "id": distro_id, "name": pretty or "Linux",
            "version": version, "edition": edition, "like": like}


@lru_cache(maxsize=1)
def _mint_info() -> dict:
    """Mint's extras from /etc/linuxmint/info: edition, codename, description.

    Not the same file as /etc/os-release and not the same keys. Mint writes
    EDITION/CODENAME/DESCRIPTION there; there is no NAME= line, so an earlier
    version of this that looked for NAME silently reported an empty edition on
    every Mint machine. The edition decides which settings backend exists at
    all (cinnamon-settings is absent on an MATE install) and is spoken to the
    user, since "open display settings" means a different app on each one.
    """
    out = {"edition": "", "codename": "", "description": ""}
    try:
        path = Path("/etc/linuxmint/info")
        if not path.is_file():
            return out
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out

    def _value(key: str) -> str:
        match = re.search(rf"^\s*{key}\s*=\s*(.+?)\s*$", text, re.MULTILINE)
        return match.group(1).strip().strip('"').strip("'") if match else ""

    out["edition"] = _value("EDITION").lower() or _value("NAME").lower()
    out["codename"] = _value("CODENAME")
    out["description"] = _value("DESCRIPTION")
    return out


@lru_cache(maxsize=1)
def os_info() -> dict:
    """Everything worth knowing about the host, for the system prompt.

    Cached, so the prompt is built from one probe rather than one per caller.
    """
    key = os_key()
    info = {
        "key": key,
        "system": platform.system() or key,
        "release": platform.release() or "",
        "version": platform.version() or "",
        "machine": platform.machine() or "",
        "python": platform.python_version(),
        "shell": _default_shell_name(),
        "home": str(Path.home()),
    }
    if key == "linux":
        info.update(linux_distro())
    elif key == "macos":
        info.update({"family": "macos", "id": "macos", "name": f"macOS {info['release']}",
                     "version": info["release"], "edition": "", "like": ""})
    else:
        info.update({"family": "windows", "id": "windows",
                     "name": f"Windows {info['release']}", "version": info["release"],
                     "edition": info["release"], "like": ""})
    return info


def describe_os() -> str:
    """One-line human label, e.g. "Linux Mint 22.1 Xia (cinnamon), x86_64"."""
    info = os_info()
    name = info.get("name") or info["key"]
    bits = [name]
    if info.get("machine"):
        bits.append(info["machine"])
    return ", ".join(b for b in bits if b)


def prompt_block() -> str:
    """Compact [OPERATING SYSTEM] block for the Live system prompt.

    Rebuilt from cached detection on every reconnect, so it cannot describe a
    machine the assistant is no longer on. Written for the model, not the user:
    it states the shell and the terminal so the tool descriptions below it do
    not each have to restate "Linux Mint" in prose.
    """
    info = os_info()
    family = info.get("family") or info["key"]
    distro = info.get("name") or info["key"]

    if info["key"] == "windows":
        detail = ("Windows. Use PowerShell or cmd syntax (e.g. `Get-Process`, "
                  "`dir`, `ipconfig`). `wt` or `cmd` opens a terminal.")
        terminal_hint = "wt / cmd"
    elif info["key"] == "macos":
        detail = ("macOS. Use zsh/bash syntax (e.g. `ps aux`, `ls`, `open -a`). "
                  "Terminal.app opens a shell; `open -a <App>` launches apps.")
        terminal_hint = "Terminal.app"
    else:
        detail = (f"Linux — {distro}. Use bash syntax (apt/dpkg, systemctl, "
                  f"pactl, xrandr). Shell: {info.get('shell') or 'bash'}.")
        if family == "mint":
            detail += " Linux Mint: use apt for packages, cinnamon-settings for system settings."
        elif family in ("ubuntu", "debian"):
            detail += " Debian-family: use apt, not yum."
        elif family == "arch":
            detail += " Arch: use pacman, not apt."
        elif family == "fedora":
            detail += " Fedora: use dnf, not apt."
        terminal_hint = "gnome-terminal"

    return (
        "[OPERATING SYSTEM]\n"
        f"Detected: {describe_os()}\n"
        f"family={family}  shell={info.get('shell') or 'unknown'}  "
        f"terminal={terminal_hint}\n"
        f"{detail}\n"
        "Command syntax and tool names must match this OS — never give a Windows "
        "command on Linux or a shell script on Windows."
    )


# ── 2. What can I run, and where is it? ──────────────────────────────────────

def _extra_search_dirs() -> list:
    """Install locations that are frequently absent from a GUI app's PATH.

    A frozen GUI binary launched from a desktop file inherits the session PATH,
    which on some desktops excludes ~/.local/bin, ~/.opencode/bin, ~/.bun/bin
    and the version managers. Tools installed by a user are exactly the ones
    that go missing, and "command not found" for an installed CLI is a bug the
    user experiences as Jarvis being broken.
    """
    home = Path.home()
    candidates = [
        home / ".local/bin", home / ".opencode/bin", home / ".bun/bin",
        home / ".npm-global/bin", home / ".cargo/bin", home / ".deno/bin",
        home / ".local/share/pnpm", home / ".volta/bin", home / ".yarn/bin",
        home / "bin", home / ".local/share/filippo.io/bin",
    ]
    if _IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates += [Path(local) / "Programs" / "opencode",
                           Path(local) / "Microsoft" / "WindowsApps"]
    else:
        candidates += [Path("/usr/local/bin"), Path("/opt/homebrew/bin"),
                       Path("/snap/bin"), Path.home() / ".opencode" / "bin"]
    seen, out = set(), []
    for path in candidates:
        try:
            resolved = str(path)
            if resolved not in seen and path.is_dir():
                seen.add(resolved)
                out.append(resolved)
        except OSError:
            continue
    return out


def find_cli(name: str) -> str:
    """Absolute path to an executable, or "" if it is not installed.

    Checks PATH first, then the extra directories above. Never raises and never
    returns a relative path — callers pass the result straight to subprocess,
    where a bare name resolves against whatever PATH the child happens to get.
    """
    if not name:
        return ""
    found = shutil.which(name)
    if found:
        return found
    suffixes = [".exe", ".cmd", ".bat", ""] if _IS_WINDOWS else [""]
    for directory in _extra_search_dirs():
        for suffix in suffixes:
            candidate = Path(directory) / f"{name}{suffix}"
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
            except OSError:
                continue
    return ""


def cli_version(name: str, timeout: float = 8.0) -> str:
    """First line of `<name> --version`, or "" if it is absent or slow.

    Used to prove an integration is really installed rather than merely
    discoverable, and to catch a PATH entry that points at a broken wrapper.
    """
    path = find_cli(name)
    if not path:
        return ""
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True,
            timeout=timeout, **_hide_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    output = (result.stdout or result.stderr or "").strip()
    return output.splitlines()[0].strip() if output else ""


def _hide_kwargs() -> dict:
    """CREATE_NO_WINDOW on Windows so a console binary never flashes a window."""
    return {"creationflags": _NO_WINDOW} if _IS_WINDOWS else {}


# ── 3. How do I run a shell string here? ─────────────────────────────────────

@lru_cache(maxsize=1)
def _default_shell_name() -> str:
    """Preferred shell name for this OS ("bash", "zsh", "powershell", ...)."""
    if _IS_WINDOWS:
        return os.environ.get("COMSPEC") or "cmd.exe"
    shell_env = os.environ.get("SHELL", "")
    if shell_env:
        return Path(shell_env).name
    return "bash" if is_linux() else "zsh"


def shell_argv(command: str) -> list:
    """argv that runs `command` through the platform's shell.

    Returned as a list and passed to subprocess WITHOUT shell=True, so a string
    containing shell metacharacters cannot be re-split into a different command
    than the one that was written. The shell is still asked to interpret the
    string, because `a && b` and `$(x)` are the point — this is about removing
    one layer of injection, not about pretending a shell is not a shell.
    """
    if _IS_WINDOWS:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
    shell = find_cli("bash") or find_cli("zsh") or "/bin/sh"
    return [shell, "-lc", command]


def run_shell(command: str, timeout: float = 30.0, cwd: str = "") -> tuple:
    """Run a shell string. Returns (exit_code, combined_output). Never raises."""
    workdir = None
    if cwd:
        try:
            if Path(cwd).is_dir():
                workdir = cwd
        except OSError:
            workdir = None
    try:
        result = subprocess.run(
            shell_argv(command), capture_output=True, text=True,
            timeout=max(1.0, min(float(timeout or 30.0), 600.0)),
            cwd=workdir, **_hide_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return 124, f"Timed out after {timeout:.0f}s."
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"Could not run it: {e}"
    output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
    return result.returncode, output.strip()


# ── 4. How do I open a terminal window here? ─────────────────────────────────

# Ordered most-preferred first; the first entry present wins, so this encodes a
# preference and not merely a set. GNOME leads because it is what Linux Mint
# Cinnamon ships, and the XDG generic binaries sit last because they exist on
# nearly every Linux box and would otherwise shadow a real terminal.
#
# The third field is the important one and the reason this is a table and not a
# list of names. Terminal emulators do NOT agree on how to accept a command:
#
#   argv    — `gnome-terminal -- bash -c ls`   (each argument separate; the
#             emulator re-execs argv, so quoting is handled by execve)
#   string  — `konsole -e "bash -c ls"`        (ONE quoted string, which the
#             emulator splits with its OWN rules, which are not always a shell's)
#
# Passing separate argv to a string-style emulator is the classic version of
# this bug: `xfce4-terminal -e bash -c ls` runs `bash` with `-c` and `ls` as its
# $0/$1, so `ls` never executes and the window just sits there. Passing a single
# string to an argv-style emulator is worse — the whole thing becomes one
# program name and nothing runs at all. So the style travels with the name.
_T_ARGV = "argv"
_T_STRING = "string"

_TERMINALS = {
    "linux": [
        ("gnome-terminal",     ["--"],                  _T_ARGV),
        ("cinnamon-terminal",  ["--"],                  _T_ARGV),
        ("mate-terminal",      ["--"],                  _T_ARGV),
        ("xfce4-terminal",     ["-e"],                  _T_STRING),
        ("konsole",            ["-e"],                  _T_STRING),
        ("alacritty",          ["-e"],                  _T_ARGV),
        ("kitty",              [],                      _T_ARGV),
        ("wezterm",            ["start", "--"],         _T_ARGV),
        ("x-terminal-emulator", ["-e"],                 _T_STRING),
        ("xterm",              ["-e"],                  _T_ARGV),
    ],
    "macos": [
        ("terminal", [], "osascript"),
    ],
    "windows": [
        ("wt", [], _T_ARGV),
    ],
}


def find_terminal() -> tuple:
    """(argv_prefix, style, terminal_path) able to run a command in a window.

    Returns ([], "", "") when nothing is available, so a caller can degrade to
    running the command in the background instead of failing. The style is one
    of the _T_* constants above and tells the caller how to append the command.
    """
    key = os_key()

    if key == "macos":
        return [], "osascript", "/usr/bin/osascript"

    for name, flags, style in _TERMINALS.get(key, []):
        path = find_cli(name)
        if path:
            return [path, *flags], style, path

    if key == "windows":
        # No Windows Terminal. `start` is a cmd builtin, so cmd.exe has to be
        # the process that runs it — and /k keeps the window alive afterwards,
        # which is the whole point of showing the user a terminal.
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/c", "start", "", "cmd.exe", "/k"], _T_ARGV, comspec

    return [], "", ""


def has_terminal() -> bool:
    """Whether a GUI terminal exists on this machine."""
    return bool(find_terminal()[2])


def quote_argv(argv: list) -> str:
    """Join argv into ONE shell-safe string, for string-style emulators.

    Each element is single-quoted, which suppresses every form of shell
    expansion — so a command containing `$PATH`, `*` or `;` is passed through as
    literal text for the inner shell to interpret, rather than being expanded
    here by the wrong shell at the wrong time. Embedded single quotes are
    closed, escaped and reopened, the standard POSIX idiom.
    """
    out = []
    for part in argv:
        text = str(part)
        out.append("'" + text.replace("'", "'\\''") + "'")
    return " ".join(out)


def terminal_command(command: str, keep_open: bool = True, cwd: str = "") -> tuple:
    """argv that opens a visible terminal window running `command`.

    THE POINT OF THIS FUNCTION is that the terminal is given the command as an
    argument, so the terminal itself runs it. The previous approach in this
    codebase was to open a blank terminal, try to focus it by guessing its
    window title, then send keystrokes at whatever happened to have focus. That
    has three failure modes — a race between the window appearing and the
    keystrokes landing, a title match that misses on a localized or
    differently-themed system, and worst of all, keystrokes delivered to the
    wrong window entirely, which silently types a command into whatever the user
    had open. Handing the command to the emulator removes all three.

    keep_open appends a fresh shell after the command, so the window survives
    the command finishing and the output stays readable. Without it a
    one-shot command closes the window the instant it exits, and the user sees
    a flash of nothing. Interactive programs (htop, watch, nano, a sudo
    password prompt) are unaffected either way — they own the tty until they
    exit.

    Returns (None, "none") when no terminal is available.
    """
    command = (command or "").strip()
    prefix, style, _path = find_terminal()
    if not prefix and style != "osascript":
        return None, "none"

    if cwd:
        try:
            if Path(cwd).is_dir():
                command = f"cd {quote_argv([str(Path(cwd).resolve())])} && {command}"
        except OSError:
            pass

    if style == "osascript":
        # AppleScript string literal: backslash and double-quote are the only
        # characters needing escape. This is AppleScript's syntax, not a
        # shell's, which is why quote_argv is not used here.
        payload = command.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "Terminal" to do script "{payload}"'
        return ["/usr/bin/osascript", "-e", script], "osascript"

    shell_argv = interactive_shell_argv(command, keep_open=keep_open)
    if style == _T_STRING:
        return [*prefix, quote_argv(shell_argv)], _T_STRING
    return [*prefix, *shell_argv], _T_ARGV


def interactive_shell_argv(command: str, keep_open: bool = True) -> list:
    """argv for a shell that runs `command` with a real tty on this OS.

    `-l` so the user's own profile is sourced — otherwise a command they can
    run by hand fails here because PATH is not set up yet, which reads as
    Jarvis being broken rather than as a login-shell difference.
    """
    if _IS_WINDOWS:
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/c", command] if not keep_open else [comspec, "/k", command]

    shell = find_cli("bash") or find_cli("zsh") or find_cli("sh") or "/bin/sh"
    if keep_open:
        # `exec` replaces the shell rather than nesting one, so Ctrl-C and
        # window-close behave as if the user's prompt were still there.
        command = f"{command}; exec {quote_argv([shell])}"
    return [shell, "-lc", command]


def launch_detached(argv: list, cwd: str = "") -> bool:
    """Start a GUI/long-lived process that must outlive this one. Never raises.

    CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS on Windows, start_new_session on
    POSIX, so that Ctrl-C in Jarvis's console does not take the child with it.
    """
    if not argv:
        return False
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    try:
        if _IS_WINDOWS:
            kwargs["creationflags"] = _NO_WINDOW | _DETACHED | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        if cwd:
            try:
                if Path(cwd).is_dir():
                    kwargs["cwd"] = cwd
            except OSError:
                pass
        subprocess.Popen(argv, **kwargs)
        return True
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print(f"[os_detect] launch_detached failed: {e}")
        return False
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    try:
        if _IS_WINDOWS:
            kwargs["creationflags"] = _NO_WINDOW | _DETACHED | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        if cwd:
            try:
                if Path(cwd).is_dir():
                    kwargs["cwd"] = cwd
            except OSError:
                pass
        subprocess.Popen(argv, **kwargs)
        return True
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print(f"[os_detect] launch_detached failed: {e}")
        return False
