import configparser
import difflib
import os
import shlex
import time
import subprocess
import platform
import shutil

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

_APP_ALIASES: dict[str, dict[str, str]] = {

    "jarvis":             {"Windows": "Jarvis",                  "Darwin": "Jarvis",               "Linux": "jarvis"},
    "jarvis 3.0":         {"Windows": "Jarvis",                  "Darwin": "Jarvis",               "Linux": "jarvis"},
    "jarvis 3":           {"Windows": "Jarvis",                  "Darwin": "Jarvis",               "Linux": "jarvis"},

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "xed"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "xed"},
    "text editor":        {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "xed"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nemo"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nemo"},
    "files":              {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nemo"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nemo"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "cinnamon-settings"},
    "system settings":    {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "cinnamon-settings"},
    "software":           {"Windows": "ms-windows-store:",       "Darwin": "App Store",            "Linux": "mintinstall"},
    "software manager":   {"Windows": "ms-windows-store:",       "Darwin": "App Store",            "Linux": "mintinstall"},
    "store":              {"Windows": "ms-windows-store:",       "Darwin": "App Store",            "Linux": "mintinstall"},
    "update manager":     {"Windows": "ms-settings:windowsupdate", "Darwin": "Software Update",    "Linux": "mintupdate"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

def _launch_windows(app_name: str) -> bool:

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


_LINUX_TERMINAL_FALLBACKS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]

# ── Linux app registry: every installed GUI app ships a .desktop file ────────
# Scanning them (Name + GenericName + Keywords + the binary in Exec) is what
# makes "open ANY app" actually true — hardcoded alias maps and PATH lookups
# can never cover Snap/FlatPak apps or display names like "Files" or "Text
# Editor". Built once per process and cached.
_LINUX_DESKTOP_DIRS = [
    os.path.expanduser("~/.local/share/applications"),
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/snapd/desktop",
    "/var/lib/flatpak/exports/share/applications",
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
]

_DESKTOP_INDEX: list[dict] | None = None

_EXEC_FIELD_CODES = {
    "%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N",
    "%i", "%c", "%k", "%v", "%m",
}


def _build_desktop_index() -> list[dict]:
    """Parse every .desktop file into {id, name, keys, exec} (cached)."""
    global _DESKTOP_INDEX
    if _DESKTOP_INDEX is not None:
        return _DESKTOP_INDEX
    index: list[dict] = []
    seen: set[str] = set()
    for d in _LINUX_DESKTOP_DIRS:
        try:
            files = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in files:
            if not fn.endswith(".desktop") or fn in seen:
                continue
            seen.add(fn)
            path = os.path.join(d, fn)
            try:
                cp = configparser.ConfigParser(strict=False, interpolation=None,
                                               allow_no_value=True)
                cp.optionxform = str  # keep key case
                cp.read(path, encoding="utf-8")
                if "Desktop Entry" not in cp:
                    continue
                entry = cp["Desktop Entry"]
                if entry.get("NoDisplay", "false").lower() == "true":
                    continue
                if entry.get("Type", "Application") != "Application":
                    continue
                name = (entry.get("Name") or "").strip()
                if not name:
                    continue
                keys = {name.lower()}
                for field in ("GenericName", "Comment"):
                    val = (entry.get(field) or "").strip().lower()
                    if val:
                        keys.add(val)
                for kw in (entry.get("Keywords") or "").replace(";", " ").split():
                    kw = kw.strip().lower()
                    if kw:
                        keys.add(kw)
                # The binary itself is a searchable key too ("code" in Exec).
                for tok in shlex.split(entry.get("Exec", "")):
                    if tok.startswith("%") or tok.startswith("-") or "/" in tok:
                        if "/" in tok:
                            keys.add(os.path.basename(tok).lower())
                        continue
                    keys.add(tok.lower())
                index.append({
                    "id": fn[:-len(".desktop")],
                    "name": name,
                    "keys": keys,
                    "exec": (entry.get("Exec") or "").strip(),
                    "path": path,
                    "terminal": (entry.get("Terminal") or "false").lower() == "true",
                })
            except Exception:
                continue
    _DESKTOP_INDEX = index
    print(f"[open_app] Desktop index: {len(index)} apps")
    return index


def _match_desktop(query: str) -> dict | None:
    """Best .desktop entry for `query`:
    exact name → token coverage (ties broken by name similarity) →
    fuzzy → substring."""
    import re as _re
    q = query.lower().strip()
    if not q:
        return None
    index = _build_desktop_index()
    for e in index:
        if e["name"].lower() == q:
            return e
    tokens = [t for t in _re.split(r"[\s\-_]+", q) if len(t) >= 2]

    def _plausible(e: dict) -> bool:
        """Guard against keyword-soup misfires: 'python3' must not open Hermes
        just because a keyword mentions python3.11. Accept when a token is in
        the app's NAME, is a whole word in its keys, or the name is close."""
        name = e["name"].lower()
        if any(t in name for t in tokens):
            return True
        # NB: dots do NOT split words here — 'python3' must not equal the
        # 'python3.11' inside some other app's keyword (the Hermes misfire).
        words = {w for k in e["keys"] for w in _re.split(r"[\s\-_]+", k)}
        if any(t in words for t in tokens):
            return True
        return difflib.SequenceMatcher(None, q, name).ratio() >= 0.5

    if tokens:
        def _strength(e: dict) -> tuple:
            """(tokens hit, name hits, whole-word hits) — higher is better."""
            name = e["name"].lower()
            hit = sum(1 for t in tokens if any(t in k for k in e["keys"]))
            in_name = sum(1 for t in tokens if t in name)
            whole = sum(
                1 for t in tokens
                for k in e["keys"]
                if t in _re.split(r"[\s\-_]+", k)
            )
            sim = difflib.SequenceMatcher(None, q, name).ratio()
            return (hit, in_name, whole, sim)
        scored = [( _strength(e), e) for e in index]
        scored = [(s, e) for s, e in scored if s[0] and _plausible(e)]
        if scored:
            return max(scored, key=lambda se: se[0])[1]
    names = [e["name"].lower() for e in index]
    for cand in difflib.get_close_matches(q, names, n=1, cutoff=0.7):
        return next(e for e in index if e["name"].lower() == cand)
    # NB: no fuzzy matching on keywords — it turns 'clock' into the 'lock'
    # keyword (0.89 similarity) and opens the wrong app. Keyword typos are
    # rare; app-name typos are already covered above.
    for e in index:
        if any(q in k for k in e["keys"]) and _plausible(e):
            return e
    return None


def _entry_program_missing(entry: dict) -> bool:
    """True when the program behind a menu entry is provably absent — e.g.
    vim.desktop ships on Mint without vim itself, and gtk-launch refuses to
    run such entries. `sh -c` wrappers and snap/flatpak runners are given the
    benefit of the doubt (their argv[0] exists)."""
    try:
        argv = shlex.split(entry.get("exec") or "")
    except Exception:
        return True
    if not argv:
        return True
    prog = argv[0]
    if "/" in prog:
        return not os.path.exists(prog)
    return shutil.which(prog) is None


def _launch_desktop_entry(entry: dict) -> bool:
    """Launch via gtk-launch (correct env for snap/flatpak), else raw Exec."""
    try:
        result = subprocess.run(
            ["gtk-launch", entry["id"]],
            capture_output=True, timeout=8,
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except FileNotFoundError:
        pass
    except Exception:
        pass
    # Fallback: run the Exec line directly, minus field codes (%f, %U, …).
    try:
        argv = [t for t in shlex.split(entry["exec"]) if t not in _EXEC_FIELD_CODES]
        if not argv:
            return False
        if "/" not in argv[0] and shutil.which(argv[0]) is None:
            return False
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=os.path.expanduser("~"))
        time.sleep(1.0)
        return True
    except Exception:
        return False


# ── Linux Mint compliance + Desktop / files / folders ────────────────────────
# Mint is Cinnamon-based: the file manager is nemo (not nautilus), the editor
# is xed (not gedit) and settings is cinnamon-settings. Detect Mint once so
# folder opens and fallbacks prefer native tools.

_IS_MINT: bool | None = None

def _is_mint() -> bool:
    global _IS_MINT
    if _IS_MINT is None:
        try:
            with open("/etc/os-release", encoding="utf-8") as f:
                _IS_MINT = "ID=linuxmint" in f.read()
        except Exception:
            _IS_MINT = False
    return _IS_MINT


_MINT_FILE_MANAGERS = ["nemo", "nautilus", "thunar", "dolphin"]


def _open_path(path: str) -> bool:
    """Open a file or folder with its default app (Mint: nemo for folders)."""
    from pathlib import Path as _P
    p = _P(path).expanduser()
    if not p.exists():
        return False
    try:
        if p.is_dir():
            for fm in (_MINT_FILE_MANAGERS if _is_mint()
                       else ["nautilus", "nemo", "thunar", "dolphin"]):
                if shutil.which(fm):
                    subprocess.Popen(
                        [fm, str(p)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    time.sleep(0.8)
                    return True
        subprocess.Popen(
            ["xdg-open", str(p)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)
        return True
    except Exception:
        return False


def _desktop_dir() -> str:
    try:
        out = subprocess.run(
            ["xdg-user-dir", "DESKTOP"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if out and os.path.isdir(out):
            return out
    except Exception:
        pass
    fallback = os.path.expanduser("~/Desktop")
    return fallback if os.path.isdir(fallback) else ""


def _search_dirs() -> list[str]:
    """Places a bare file/folder name is looked up: Desktop + XDG dirs + ~."""
    from pathlib import Path as _P
    home = _P.home()
    candidates = []
    dd = _desktop_dir()
    if dd:
        candidates.append(dd)
    for var, sub in (("DOCUMENTS", "Documents"), ("DOWNLOAD", "Downloads"),
                     ("PICTURES", "Pictures"), ("VIDEOS", "Videos"),
                     ("MUSIC", "Music")):
        try:
            out = subprocess.run(
                ["xdg-user-dir", var],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if out and os.path.isdir(out):
                candidates.append(out)
                continue
        except Exception:
            pass
        p = str(home / sub)
        if os.path.isdir(p):
            candidates.append(p)
    candidates.append(str(home))
    seen, ordered = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def _match_desktop_item(query: str) -> str:
    """Match `query` to something on the Desktop: a launcher (.desktop Name),
    a file, or a folder. Returns the absolute path, or ''."""
    dd = _desktop_dir()
    if not dd or not query:
        return ""
    q = query.lower().strip()
    try:
        names = os.listdir(dd)
    except OSError:
        return ""
    # 1) launcher display name
    for fn in names:
        if not fn.endswith(".desktop"):
            continue
        try:
            cp = configparser.ConfigParser(strict=False, interpolation=None,
                                           allow_no_value=True)
            cp.optionxform = str
            cp.read(os.path.join(dd, fn), encoding="utf-8")
            disp = (cp["Desktop Entry"].get("Name") or "").strip()
            if disp and (disp.lower() == q or q in disp.lower()
                         or disp.lower() in q):
                return os.path.join(dd, fn)
        except Exception:
            continue
    # 2) file/folder name: exact, then basename prefix ("ksnip" →
    # "ksnip_2026….png"), then substring. Prefix/substring only win when
    # unique, so a vague word never opens the wrong file.
    others = [fn for fn in names if not fn.endswith(".desktop")]
    for fn in others:
        if fn.lower() == q:
            return os.path.join(dd, fn)
    base = os.path.splitext(q)[0]
    starts = [fn for fn in others
              if os.path.splitext(fn.lower())[0].startswith(base) and len(base) >= 3]
    if len(starts) == 1:
        return os.path.join(dd, starts[0])
    if not starts:
        ins = [fn for fn in others if base in fn.lower() and len(base) >= 4]
        if len(ins) == 1:
            return os.path.join(dd, ins[0])
    return ""


def _match_home_item(query: str) -> str:
    """Match `query` to a file/folder in the standard home dirs. Top-level
    only; prefix/substring matches must be unique to win."""
    q = (query or "").lower().strip()
    if not q or "/" in q or q.startswith((".", "~")):
        return ""
    base = os.path.splitext(q)[0]
    for d in _search_dirs():
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for fn in names:
            if fn.lower() == q:
                return os.path.join(d, fn)
        if len(base) >= 3:
            starts = [fn for fn in names
                      if os.path.splitext(fn.lower())[0].startswith(base)]
            if len(starts) == 1:
                return os.path.join(d, starts[0])
            if not starts and len(base) >= 4:
                ins = [fn for fn in names if base in fn.lower()]
                if len(ins) == 1:
                    return os.path.join(d, ins[0])
    return ""


def _launch_desktop_file(path: str) -> bool:
    """Launch a ~/Desktop .desktop launcher via its own Exec line (works even
    when the launcher isn't trusted/marked executable yet)."""
    try:
        cp = configparser.ConfigParser(strict=False, interpolation=None,
                                       allow_no_value=True)
        cp.optionxform = str
        cp.read(path, encoding="utf-8")
        exe = (cp["Desktop Entry"].get("Exec") or "").strip()
        argv = [t for t in shlex.split(exe) if t not in _EXEC_FIELD_CODES]
        if argv and ("/" in argv[0] or shutil.which(argv[0])):
            subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             cwd=os.path.expanduser("~"))
            time.sleep(1.0)
            return True
    except Exception:
        pass
    try:
        result = subprocess.run(["gio", "launch", path],
                                capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass
    return False


# ── CLI fallback: menu first, terminal second ────────────────────────────────
# "Open htop" must not die silently: TUI programs have no window of their own
# and need a terminal emulator around them. Order per request: the app menu
# (registry above) is always tried first; only when nothing matches there do
# we resolve a PATH binary — wrapped in a terminal when it is text-based.
_KNOWN_TUI = {
    # monitors / sysadmin
    "top", "htop", "btop", "atop", "nvtop", "iotop", "powertop", "ctop",
    "gotop", "cava", "neofetch", "fastfetch", "screenfetch", "alsamixer",
    "pulsemixer", "nmtui", "nmtui-connect", "nmtui-edit", "nmtui-hostname",
    # editors / pagers
    "vi", "vim", "nvim", "nano", "pico", "micro", "joe", "less", "more",
    "most", "tmux", "screen", "byobu",
    # file managers / git
    "ranger", "mc", "nnn", "lf", "tig", "lazygit", "lazydocker",
    # REPLs / runtimes
    "python", "python3", "ipython", "bpython", "node", "deno", "bun",
    "lua", "luajit", "ruby", "irb", "perl", "php", "ghci", "iex",
    # database / network CLIs
    "sqlite3", "mysql", "psql", "mongosh", "redis-cli", "mosh",
    # players / readers / chat
    "cmus", "mocp", "ncmpcpp", "mpv", "newsboat", "newsbeuter", "irssi",
    "weechat", "finch", "mutt", "neomutt", "alpine", "lynx", "w3m",
    "links", "elinks", "rTorrent", "rtorrent",
}

# Terminals whose "run a command" flag is `--` (gnome family) vs `-e`.
_DASHDASH_TERMS = {"gnome-terminal", "tilix", "kgx", "gnome-console", "ptyxis"}


def _terminal_prefix() -> list[str]:
    """First available terminal emulator + its run flag, e.g.
    ['gnome-terminal', '--']. Empty when no emulator is installed.

    The flag comes from the REAL binary (via realpath): on Mint
    `x-terminal-emulator` is an alternatives symlink to gnome-terminal, whose
    modern versions reject `-e` — so we must not pick the flag from the
    symlink's name."""
    for term in _LINUX_TERMINAL_FALLBACKS:
        path = shutil.which(term)
        if not path:
            continue
        try:
            real = os.path.basename(os.path.realpath(path)).lower()
        except Exception:
            real = os.path.basename(path).lower()
        real = real[:-len(".wrapper")] if real.endswith(".wrapper") else real
        if real == "kitty":
            return [path]  # kitty takes the program directly
        flag = "--" if real in _DASHDASH_TERMS else "-e"
        return [path, flag]
    return []


def _resolve_binary(name: str) -> str:
    for variant in (name, name.lower(),
                    name.lower().replace(" ", "-"),
                    name.lower().replace(" ", "_"),
                    name.lower().replace(" ", "")):
        hit = shutil.which(variant)
        if hit:
            return hit
    return ""


def _needs_terminal(binary: str) -> bool:
    """True when `binary` is a text UI: known-TUI list, or a .desktop entry
    for the same program that declares Terminal=true."""
    import os as _os
    base = _os.path.basename(binary).lower()
    if base in _KNOWN_TUI:
        return True
    try:
        for e in _build_desktop_index():
            if not e.get("terminal"):
                continue
            try:
                argv = shlex.split(e.get("exec") or "")
            except Exception:
                continue
            if argv and _os.path.basename(argv[0]).lower() == base:
                return True
    except Exception:
        pass
    return False


def _launch_cli(app_name: str) -> bool:
    """PATH-binary fallback: GUI programs run directly, TUI programs inside
    a terminal emulator so they get a visible window."""
    binary = _resolve_binary(app_name)
    if not binary:
        return False
    try:
        if _needs_terminal(binary):
            prefix = _terminal_prefix()
            if not prefix:
                print("[open_app] CLI app needs a terminal, none installed")
                return False
            subprocess.Popen(
                prefix + [binary],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=os.path.expanduser("~"),
            )
            print(f"[open_app] → CLI in terminal: {' '.join(prefix)} {binary}")
        else:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print(f"[open_app] → binary '{binary}'")
        time.sleep(1.0)
        return True
    except Exception as e:
        print(f"[open_app] CLI launch failed: {e}")
        return False


def _launch_linux(app_name: str) -> bool:

    # terminal emulators: try common ones in order
    if app_name in ("x-terminal-emulator", "gnome-terminal", "terminal"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                try:
                    subprocess.Popen([term], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    continue

    # 0) Explicit or home-relative path — "open ~/Documents/report.pdf".
    try:
        if ("/" in app_name or app_name.startswith("~")
                or app_name.startswith(".")) and _open_path(app_name):
            print(f"[open_app] → path '{app_name}'")
            return True
    except Exception as e:
        print(f"[open_app] path open failed: {e}")

    # 1) ~/Desktop items first — what the user deliberately put in front of
    #    them (launchers, files, folders) beats a fuzzy registry guess.
    try:
        hit = _match_desktop_item(app_name)
        if hit:
            if hit.endswith(".desktop") and _launch_desktop_file(hit):
                print(f"[open_app] → desktop launcher '{os.path.basename(hit)}'")
                return True
            if not hit.endswith(".desktop") and _open_path(hit):
                print(f"[open_app] → desktop item '{hit}'")
                return True
    except Exception as e:
        print(f"[open_app] desktop lookup failed: {e}")

    # 1b) Well-known places — "open documents/downloads/pictures/home/trash"
    #     unambiguously means the folder, never a fuzzy app guess.
    try:
        _well_known = {
            "desktop": _desktop_dir(),
            "home": os.path.expanduser("~"),
            "documents": os.path.expanduser("~/Documents"),
            "downloads": os.path.expanduser("~/Downloads"),
            "pictures": os.path.expanduser("~/Pictures"),
            "videos": os.path.expanduser("~/Videos"),
            "music": os.path.expanduser("~/Music"),
        }
        low = app_name.lower().strip()
        if low in _well_known and _well_known[low] and os.path.isdir(_well_known[low]):
            if _open_path(_well_known[low]):
                print(f"[open_app] → folder '{low}'")
                return True
        if low in ("trash", "rubbish bin", "recycle bin"):
            for fm in (_MINT_FILE_MANAGERS if _is_mint()
                       else ["nautilus", "nemo", "thunar", "dolphin"]):
                if shutil.which(fm):
                    subprocess.Popen(
                        [fm, "trash:///"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    time.sleep(0.8)
                    print("[open_app] → trash")
                    return True
    except Exception as e:
        print(f"[open_app] well-known folder failed: {e}")

    # 2) Installed-app registry = the app menu (covers snap/flatpak/display
    #    names). Always checked before CLI, per request.
    try:
        entry = _match_desktop(app_name)
        if entry and _launch_desktop_entry(entry):
            print(f"[open_app] → desktop entry '{entry['name']}'")
            return True
    except Exception as e:
        print(f"[open_app] desktop-index launch failed: {e}")

    # 3) CLI fallback — PATH binaries not in the menu. Text UIs (htop, vim,
    #    python, …) are wrapped in a terminal emulator so they get a window.
    try:
        if _launch_cli(app_name):
            return True
    except Exception as e:
        print(f"[open_app] CLI launch failed: {e}")

    # 4) Files/folders in the home dirs — only when no app matched, so a
    #    stray file (e.g. a downloaded .deb) never shadows the real app.
    try:
        hit = _match_home_item(app_name)
        if hit and _open_path(hit):
            print(f"[open_app] → home item '{hit}'")
            return True
    except Exception as e:
        print(f"[open_app] home lookup failed: {e}")

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


def _list_desktop_items() -> list[str]:
    """Display names of everything sitting on the user's Desktop."""
    dd = _desktop_dir()
    if not dd:
        return []
    try:
        names = os.listdir(dd)
    except OSError:
        return []
    out = []
    for fn in sorted(names):
        if fn.endswith(".desktop"):
            try:
                cp = configparser.ConfigParser(strict=False, interpolation=None,
                                               allow_no_value=True)
                cp.optionxform = str
                cp.read(os.path.join(dd, fn), encoding="utf-8")
                disp = (cp["Desktop Entry"].get("Name") or "").strip()
                out.append(disp or fn)
            except Exception:
                out.append(fn)
        else:
            out.append(fn)
    return out


def _list_linux_apps(query: str = "") -> str:
    """Human-readable list of installed apps (+ Desktop items), optionally filtered."""
    q = (query or "").lower().strip()
    names = sorted({e["name"] for e in _build_desktop_index()})
    if q:
        names = [n for n in names
                 if q in n.lower()
                 or difflib.SequenceMatcher(None, q, n.lower()).ratio() > 0.5]
    if not names and not q:
        return "No installed apps found."
    if not names:
        return f"No installed apps matching '{query}'."
    shown = names[:60]
    total = f"{len(names)} total" if len(names) > 60 else f"{len(names)}"
    text = f"Installed apps ({total}, showing {len(shown)}): " + ", ".join(shown)
    if not q:
        desk = _list_desktop_items()
        if desk:
            text += f"\nOn your Desktop ({len(desk)}): " + ", ".join(desk[:40])
    return text


def _strip_filler(raw: str) -> str:
    """Voice transcripts often wrap the name: 'open the chrome browser please'."""
    t = raw.strip()
    low = t.lower()
    for prefix in ("please open the ", "please launch the ", "please open ",
                   "please launch ", "open the ", "open my ", "launch the ",
                   "start the ", "open ", "launch ", "start ", "my "):
        if low.startswith(prefix):
            t, low = t[len(prefix):], low[len(prefix):]
            break
    for suffix in (" browser", " app", " application", " program", " please"):
        if low.endswith(suffix):
            t, low = t[:-len(suffix)], low[:-len(suffix)]
    return t.strip() or raw.strip()


def _is_jarvis_running() -> bool:
    """True if another Jarvis (Mark-LIII main.py) process is already alive.

    Opening Jarvis by voice while it is listening must NOT spawn a clone —
    two instances would fight over the microphone and the dashboard port.
    """
    try:
        import psutil as _ps
    except Exception:
        return False
    me = os.getpid()
    try:
        for p in _ps.process_iter(["pid", "cmdline"]):
            if p.info["pid"] == me:
                continue
            args = p.info.get("cmdline") or []
            for a in args:
                al = a.strip().lower().replace("\\", "/")
                # A real interpreter arg: a path (or bare name) ENDING in
                # main.py — not a script that merely mentions it. This keeps
                # the check from matching its own `-c "..."` test snippet.
                if not (al == "main.py" or al.endswith("/main.py")):
                    continue
                blob = " ".join(args).lower()
                if "mark-liii" in blob:
                    return True
                if al == "main.py":
                    # Relative launch: confirm via the process working dir.
                    try:
                        if "mark-liii" in str(p.cwd()).lower():
                            return True
                    except Exception:
                        pass
    except Exception:
        pass
    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = str(params.get("action", "open")).lower().strip() or "open"

    if action == "list":
        if _SYSTEM != "Linux":
            return "Listing installed apps is only supported on Linux."
        return _list_linux_apps(params.get("query", params.get("app_name", "")))

    app_name = _strip_filler(params.get("app_name", ""))

    if not app_name:
        return "No application name provided."

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    # Jarvis itself: if it is already listening, say so instead of cloning it.
    if "jarvis" in f"{app_name} {normalized}".lower() and _is_jarvis_running():
        return "Jarvis is already running — I'm right here and listening."

    try:
        if launcher(normalized):
            return f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            if launcher(app_name):
                return f"Opened {app_name}."
        # Genuinely missing? Say exactly that — plus the one-line fix on Mint.
        # (A menu entry alone doesn't count: vim.desktop ships without vim,
        # and gtk-launch refuses such entries — so check the program too.)
        if _SYSTEM == "Linux" and _is_mint():
            for cand in (normalized, app_name):
                c = cand.strip()
                if ("/" in c or c.startswith(("~", ".")) or " " in c):
                    continue
                if _resolve_binary(c):
                    continue  # installed; failure was elsewhere → generic msg
                entry = _match_desktop(c)
                if entry is not None and not _entry_program_missing(entry):
                    continue  # installed via menu; failure was elsewhere
                if _match_desktop_item(c) == "" and _match_home_item(c) == "":
                    return (
                        f"'{c}' is not installed on this laptop, so there "
                        f"is nothing to open. Install it with: "
                        f"sudo apt install {c.lower()} — then just "
                        f"ask me to open it again."
                    )
        return (
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed."
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "open_app",
    "description": (
        "Opens ANYTHING on the computer: installed applications (no fixed list — "
        "it searches the system's own app registry first: browsers, editors, files, "
        "calculator, store apps, snap/flatpak apps), terminal/CLI tools not in "
        "the menu (htop, vim, python, … — opened inside a terminal window), "
        "anything on the Desktop (launchers, files, folders), and any file or "
        "folder by path or by name (e.g. 'my report', 'Downloads'). "
        "Use this whenever the user asks to open, launch, start, or show any app, "
        "file, or folder — just pass what they said as app_name. "
        "Use action='list' (with optional query) when they ask what apps are installed. "
        "Always call this tool — never just say you opened it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "open (default) | list"
            },
            "app_name": {
                "type": "STRING",
                "description": "Name of the application as the user said it (e.g. 'WhatsApp', 'Chrome', 'files')"
            },
            "query": {
                "type": "STRING",
                "description": "Filter for action='list' (e.g. 'photo')"
            }
        },
        "required": []
    },
    "handler": open_app,
}
