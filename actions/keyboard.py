#keyboard.py — press any keyboard key, combo, or sequence by voice.
#
# Use this tool when the user says things like:
#   "press enter", "hit escape", "press tab", "hit space",
#   "press backspace / delete", "press the arrow keys",
#   "press F5 / F11", "press ctrl+c", "hold shift", etc.
#
# Backend: pyautogui when present, else pynput (works on Linux without
# system tkinter). Imported lazily so a missing backend degrades the action,
# never startup. A fresh pynput Controller is built per call so executor
# threads never share one.

import time

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True
except KeyboardInterrupt:
    raise
except BaseException:
    # pyautogui -> MouseInfo calls sys.exit(1) (SystemExit, not ImportError)
    # when system tkinter is missing on Linux. A missing optional GUI
    # dependency must degrade the action, never kill the whole app.
    _PYAUTOGUI = False

_PYNPUT_OK = None


def _pynput_available() -> bool:
    global _PYNPUT_OK
    if _PYNPUT_OK is None:
        try:
            import pynput.keyboard  # noqa: F401
            _PYNPUT_OK = True
        except KeyboardInterrupt:
            raise
        except BaseException:
            _PYNPUT_OK = False
    return _PYNPUT_OK


def _require_keyboard():
    if not _PYAUTOGUI and not _pynput_available():
        raise RuntimeError(
            "No keyboard backend. Run: pip install pynput "
            "(or: sudo apt-get install python3-tk for PyAutoGUI)"
        )


# ── Key-name normalisation ────────────────────────────────────────────────────
# Accepts what people actually say ("escape", "esc", "return", "spacebar",
# "arrow up", "F5", "ctrl") and maps it to one canonical pyautogui key name.
# Single printable characters pass through unchanged.

_ALIASES = {
    "enter": "enter", "return": "enter", "ent": "enter", "\n": "enter",
    "escape": "escape", "esc": "escape",
    "tab": "tab",
    "space": "space", "spacebar": "space", " ": "space",
    "backspace": "backspace", "bksp": "backspace", "back": "backspace",
    "delete": "delete", "del": "delete",
    "shift": "shift", "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt",
    "win": "win", "super": "win", "cmd": "command", "command": "command",
    "up": "up", "arrowup": "up", "arrow_up": "up",
    "down": "down", "arrowdown": "down", "arrow_down": "down",
    "left": "left", "arrowleft": "left", "arrow_left": "left",
    "right": "right", "arrowright": "right", "arrow_right": "right",
    "home": "home", "end": "end",
    "pageup": "pageup", "pgup": "pageup", "page_up": "pageup",
    "pagedown": "pagedown", "pgdn": "pagedown", "page_down": "pagedown",
    "insert": "insert", "ins": "insert",
    "capslock": "capslock", "caps": "capslock", "caps_lock": "capslock",
    "numlock": "numlock", "num": "numlock", "num_lock": "numlock",
    "scrolllock": "scrolllock", "scroll_lock": "scrolllock",
    "printscreen": "printscreen", "prntscrn": "printscreen",
    "print_screen": "printscreen", "print screen": "printscreen",
    "pause": "pause", "break": "pause", "pausebreak": "pause",
    "menu": "apps", "apps": "apps",
    "mute": "volumemute", "volumemute": "volumemute",
    "volumeup": "volumeup", "volup": "volumeup", "volume_up": "volumeup",
    "volumedown": "volumedown", "voldn": "volumedown", "volume_down": "volumedown",
    "playpause": "playpause", "play": "playpause",
    "nexttrack": "nexttrack", "next": "nexttrack",
    "prevtrack": "prevtrack", "prev": "prevtrack", "previous": "prevtrack",
}


def normalize_key(name: str) -> str:
    """Map a human key name to a canonical pyautogui key name.

    Raises ValueError on empty/unknown names. Single printable characters
    (letters, digits, punctuation) pass through as-is.
    """
    if name is None:
        raise ValueError("No key specified.")
    s = str(name)
    if s == "":
        raise ValueError("No key specified.")
    if s.strip() == "":
        # Whitespace-only input is a real key, not an empty one.
        if "\n" in s or "\r" in s:
            return "enter"
        if "\t" in s:
            return "tab"
        return "space"
    raw = s.strip()
    if len(raw) == 1:
        return raw
    norm = raw.lower().replace(" ", "").replace("-", "").replace("_", "")
    if norm in _ALIASES:
        return _ALIASES[norm]
    if len(norm) > 1 and norm.startswith("f") and norm[1:].isdigit():
        n = int(norm[1:])
        if 1 <= n <= 24:
            return f"f{n}"
    # Already-canonical pyautogui names pass through.
    _CANONICAL = {
        "enter", "escape", "tab", "space", "backspace", "delete",
        "shift", "shiftleft", "shiftright",
        "ctrl", "ctrlleft", "ctrlright",
        "alt", "altleft", "altright",
        "win", "winleft", "winright", "command", "option",
        "up", "down", "left", "right", "home", "end",
        "pageup", "pagedown", "pgup", "pgdn",
        "insert", "capslock", "numlock", "scrolllock",
        "printscreen", "prntscrn", "pause", "apps",
        "volumemute", "volumeup", "volumedown",
        "playpause", "nexttrack", "prevtrack", "stop",
        "fn", "clear", "select", "execute", "help", "sleep",
    }
    if norm in _CANONICAL:
        return norm
    raise ValueError(
        f"Unknown key: '{raw}'. Supported: enter, tab, escape, space, "
        f"backspace, delete, arrows, home/end, pageup/pagedown, insert, "
        f"F1-F12, shift/ctrl/alt/win, capslock, printscreen, plus "
        f"single characters and combos like 'ctrl+c'."
    )


def _split_combo(keys) -> list:
    """Split 'ctrl+shift+esc' (or a list) into normalised key names."""
    if isinstance(keys, (list, tuple)):
        parts = list(keys)
    else:
        parts = str(keys or "").replace(",", "+").split("+")
    out = []
    for p in parts:
        p = str(p or "").strip()
        if p:
            out.append(normalize_key(p))
    if not out:
        raise ValueError("No keys specified for hotkey.")
    return out


def _split_sequence(seq) -> list:
    """Split 'enter, tab, enter' (or a list) into normalised key names."""
    if isinstance(seq, (list, tuple)):
        parts = list(seq)
    else:
        raw = str(seq or "").strip()
        if not raw:
            return []
        # Commas separate steps; a bare '+' inside one step is a chord.
        # e.g. "ctrl+s, enter" -> [["ctrl","s"], ["enter"]]
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    out = []
    for p in parts:
        if not p:
            continue
        if isinstance(p, (list, tuple)):
            out.append([normalize_key(k) for k in p])
        elif "+" in p and len(p) > 1:
            out.append(_split_combo(p))
        else:
            out.append(normalize_key(p))
    return out


# ── pynput mapping ─────────────────────────────────────────────────────────────

def _pynput_key(name: str):
    """Map a canonical key name to a pynput key / character."""
    from pynput.keyboard import Key, KeyCode
    n = (name or "").strip().lower()
    _special = {
        "enter": Key.enter, "tab": Key.tab,
        "esc": Key.esc, "escape": Key.esc, "space": Key.space,
        "backspace": Key.backspace, "delete": Key.delete,
        "shift": Key.shift, "shiftleft": Key.shift, "shiftright": Key.shift,
        "ctrl": Key.ctrl, "ctrlleft": Key.ctrl, "ctrlright": Key.ctrl,
        "control": Key.ctrl, "alt": Key.alt,
        "altleft": Key.alt, "altright": Key.alt,
        "cmd": Key.cmd, "command": Key.cmd, "win": Key.cmd,
        "winleft": Key.cmd, "winright": Key.cmd, "super": Key.cmd,
        "option": Key.alt, "menu": Key.menu, "apps": Key.menu,
        "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
        "home": Key.home, "end": Key.end,
        "pageup": Key.page_up, "pgup": Key.page_up,
        "pagedown": Key.page_down, "pgdn": Key.page_down,
        "insert": Key.insert, "pause": Key.pause,
        "capslock": Key.caps_lock, "numlock": Key.num_lock,
        "scrolllock": Key.scroll_lock, "printscreen": Key.print_screen,
        "prntscrn": Key.print_screen,
        "volumemute": getattr(Key, "media_volume_mute", None),
        "volumeup": getattr(Key, "media_volume_up", None),
        "volumedown": getattr(Key, "media_volume_down", None),
        "playpause": getattr(Key, "media_play_pause", None),
        "nexttrack": getattr(Key, "media_next", None),
        "prevtrack": getattr(Key, "media_previous", None),
    }
    if n in _special:
        return _special[n]
    if n.startswith("f") and n[1:].isdigit() and 1 <= int(n[1:]) <= 20:
        return getattr(Key, n, None)
    if len(name) == 1:
        return KeyCode.from_char(name)
    return None


def _press_backend(key: str) -> None:
    if _PYAUTOGUI:
        pyautogui.press(key)
    else:
        from pynput.keyboard import Controller
        mapped = _pynput_key(key)
        if mapped is None:
            raise RuntimeError(f"Unsupported key: {key}")
        kb = Controller()
        kb.press(mapped)
        kb.release(mapped)


def _hotkey_backend(keys: list) -> None:
    if _PYAUTOGUI:
        pyautogui.hotkey(*keys)
    else:
        from pynput.keyboard import Controller
        kb = Controller()
        mapped = [_pynput_key(k) for k in keys]
        if not mapped or any(k is None for k in mapped):
            raise RuntimeError(f"Unsupported key in hotkey: {'+'.join(keys)}")
        for k in mapped:
            kb.press(k)
        for k in reversed(mapped):
            kb.release(k)


def _keydown_backend(key: str) -> None:
    if _PYAUTOGUI:
        pyautogui.keyDown(key)
    else:
        from pynput.keyboard import Controller
        mapped = _pynput_key(key)
        if mapped is None:
            raise RuntimeError(f"Unsupported key: {key}")
        Controller().press(mapped)


def _keyup_backend(key: str) -> None:
    if _PYAUTOGUI:
        pyautogui.keyUp(key)
    else:
        from pynput.keyboard import Controller
        mapped = _pynput_key(key)
        if mapped is None:
            raise RuntimeError(f"Unsupported key: {key}")
        Controller().release(mapped)


def _type_backend(text: str, interval: float = 0.03) -> None:
    if _PYAUTOGUI:
        pyautogui.typewrite(text, interval=interval)
    else:
        from pynput.keyboard import Controller
        Controller().type(text)


# ── Public actions ─────────────────────────────────────────────────────────────

def _do_press(key: str, presses: int = 1, interval: float = 0.05) -> str:
    _require_keyboard()
    key = normalize_key(key)
    presses = max(1, min(int(presses or 1), 20))
    interval = max(0.0, min(float(interval or 0.0), 2.0))
    time.sleep(0.2)  # let focus settle
    for i in range(presses):
        _press_backend(key)
        if i < presses - 1 and interval > 0:
            time.sleep(interval)
    return f"Pressed: {key}" + (f" ×{presses}" if presses > 1 else "")


def _do_hotkey(keys) -> str:
    _require_keyboard()
    combo = _split_combo(keys)
    time.sleep(0.2)
    _hotkey_backend(combo)
    return f"Hotkey: {'+'.join(combo)}"


def _do_hold(key: str, hold_time: float = 1.0) -> str:
    _require_keyboard()
    key = normalize_key(key)
    hold_time = max(0.1, min(float(hold_time or 1.0), 10.0))
    time.sleep(0.2)
    _keydown_backend(key)
    time.sleep(hold_time)
    _keyup_backend(key)
    return f"Held {key} for {hold_time:g}s"


def _do_keydown(key: str) -> str:
    _require_keyboard()
    key = normalize_key(key)
    time.sleep(0.15)
    _keydown_backend(key)
    return f"Key down: {key} (call keyup to release)"


def _do_keyup(key: str) -> str:
    _require_keyboard()
    key = normalize_key(key)
    _keyup_backend(key)
    return f"Key up: {key}"


def _do_sequence(sequence) -> str:
    _require_keyboard()
    steps = _split_sequence(sequence)
    if not steps:
        return "No keys in sequence."
    if len(steps) > 20:
        return "Sequence too long (max 20 steps)."
    time.sleep(0.2)
    done = []
    for step in steps:
        if isinstance(step, list):
            _hotkey_backend(step)
            done.append("+".join(step))
        else:
            _press_backend(step)
            done.append(step)
        time.sleep(0.08)
    return f"Sequence done: {' → '.join(done)}"


def _do_type(text: str, press_enter: bool = False) -> str:
    _require_keyboard()
    text = str(text or "")
    if not text and not press_enter:
        return "No text given to type."
    time.sleep(0.2)
    if text:
        if len(text) > 1000:
            return "Text too long (max 1000 characters)."
        _type_backend(text)
    if press_enter:
        time.sleep(0.1)
        _press_backend("enter")
    shown = text[:60] + ("…" if len(text) > 60 else "")
    return f"Typed: {shown}" + (" + Enter" if press_enter else "")


def keyboard(parameters: dict, player=None, session_memory=None) -> str:
    """
    parameters:
      action     : press | hotkey | hold | keydown | keyup | sequence | type
      key        : single key name, e.g. 'enter' (for press/hold/keydown/keyup)
      keys       : combo e.g. 'ctrl+c' (for hotkey)
      sequence   : ordered keys e.g. 'ctrl+s, enter' (for sequence)
      text       : text to type (for type)
      presses    : repeat count 1-20 (for press, default 1)
      interval   : seconds between repeats (for press, default 0.05)
      hold_time  : seconds to hold (for hold, default 1.0, max 10)
      press_enter: type then hit Enter (for type: true/false, default false)
    """
    params = parameters or {}
    action = str(params.get("action", "press")).lower().strip().replace(" ", "_").replace("-", "_")

    if player:
        try:
            player.write_log(f"[Keyboard] {action}")
        except Exception:
            pass
    print(f"[Keyboard] ▶ {action}  {params}")

    try:
        if action == "press":
            return _do_press(
                params.get("key", "enter"),
                presses=params.get("presses", 1),
                interval=params.get("interval", 0.05),
            )
        if action == "hotkey":
            return _do_hotkey(params.get("keys", ""))
        if action == "hold":
            return _do_hold(
                params.get("key", ""),
                hold_time=params.get("hold_time", params.get("seconds", 1.0)),
            )
        if action == "keydown":
            return _do_keydown(params.get("key", ""))
        if action == "keyup":
            return _do_keyup(params.get("key", ""))
        if action == "sequence":
            return _do_sequence(params.get("sequence", params.get("keys", "")))
        if action == "type":
            raw_enter = params.get("press_enter", False)
            enter_after = str(raw_enter).lower() in ("true", "1", "yes") if not isinstance(raw_enter, bool) else raw_enter
            return _do_type(params.get("text", ""), press_enter=enter_after)
        return (
            f"Unknown keyboard action: '{action}'. "
            "Use action=press | hotkey | hold | keydown | keyup | sequence | type."
        )
    except (ValueError, RuntimeError) as e:
        print(f"[Keyboard] ❌ {action}: {e}")
        return f"keyboard '{action}' failed: {e}"
    except Exception as e:
        print(f"[Keyboard] ❌ {action}: {e}")
        return f"keyboard '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ────────────────
TOOL = {
    "name": "keyboard",
    "description": (
        "Presses keyboard keys: single keys (enter, tab, escape, space, "
        "backspace, delete, arrows, home/end, pageup/pagedown, F1-F12, "
        "shift/ctrl/alt/win), key combos (ctrl+c), held keys, and ordered "
        "key sequences. Use when the user says 'press ...', 'hit ...', "
        "'hold ...', or names a key like Enter/Escape/Tab/Space/F5. "
        "For typing words or sentences into a focused textbox use dictate "
        "instead; for OS settings/shortcuts (volume, close app) use "
        "computer_settings; for mouse clicks use computer_control."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "press (default) | hotkey | hold | keydown | keyup | sequence | type",
            },
            "key": {
                "type": "STRING",
                "description": "Single key: enter, tab, escape/esc, space, backspace, delete/del, up/down/left/right, home, end, pageup, pagedown, insert, F1-F12, shift, ctrl, alt, win, capslock, printscreen, volumemute/volumeup/volumedown, playpause, or any single character",
            },
            "keys": {
                "type": "STRING",
                "description": "Key combo for hotkey, e.g. 'ctrl+c', 'alt+tab', 'ctrl+shift+esc'",
            },
            "sequence": {
                "type": "STRING",
                "description": "Ordered keys for sequence, comma-separated, e.g. 'ctrl+s, enter'. A step with '+' is a chord.",
            },
            "text": {
                "type": "STRING",
                "description": "Text to type (for action='type')",
            },
            "presses": {
                "type": "NUMBER",
                "description": "Repeat count for press (1-20, default 1)",
            },
            "interval": {
                "type": "NUMBER",
                "description": "Seconds between repeats for press (default 0.05)",
            },
            "hold_time": {
                "type": "NUMBER",
                "description": "Seconds to hold the key for hold (default 1.0, max 10)",
            },
            "press_enter": {
                "type": "BOOLEAN",
                "description": "For action='type': hit Enter after typing (default false)",
            },
        },
        "required": [],
    },
    "handler": keyboard,
}
