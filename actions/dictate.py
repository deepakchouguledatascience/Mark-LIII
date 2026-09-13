#dictate.py — voice typing into ANY application's textbox + voice review tools.
#
# Two ways to use it:
#   1. One-shot: user clicks a textbox, says "type hello world" → the model
#      calls dictate(action="type", text="hello world") → typed at the cursor.
#   2. Dictation mode: user says "start dictating" → dictate(action="start").
#      From then on main.py types EVERY finalized utterance at the cursor
#      automatically, until "stop dictating" (dictate(action="stop")).
#
# Review (voice commands, never typed into the app):
#   "read that back" / "repeat last"      → dictate(action="readback")
#   "fix grammar" / "proofread" / "review"→ dictate(action="fix")
#   "correct X to Y" / "replace X with Y" → dictate(action="correct", old="X", new="Y")
#   "undo last" / "scratch that"          → dictate(action="undo_last")
#   "show history" / "show dictation"     → dictate(action="history")
#
# Typing itself is clipboard-free key synthesis via computer_control's keyboard
# backend (pyautogui when present, pynput otherwise), so it works on Linux
# without system tkinter. Nothing is ever typed while the assistant is muted.
import re
import time
from collections import deque


_ACTIVE = False  # process-wide dictation-mode flag (read by main.py)

# Last N dictated segments, oldest → newest. Used for read-back / fix / undo.
_HISTORY: deque = deque(maxlen=50)

# Utterances that must never be typed — they ARE the off switch. The tool call
# that stops the mode arrives in the same turn, after transcription.
_STOP_PHRASES = (
    "stop dictating",
    "stop dictation",
    "dictation off",
    "stop typing",
    "stop writing",
    "dictation stop",
)

# ── Review-command patterns (checked BEFORE typing in main.py) ──────────────
# Each returns a (action, params) pair via parse_review_command().
_READBACK_PATTERNS = (
    r"\bread\s+(that\s+)?back\b",
    r"\bread\s+(me\s+)?(the\s+)?last\b",
    r"\brepeat\s+(that|last)\b",
    r"\bwhat\s+did\s+i\s+(just\s+)?(say|dictate)\b",
    r"\bread\s+(my\s+)?dictation\b",
)

_FIX_PATTERNS = (
    r"\bfix\s+(grammar|spelling|text|that|last|it|my\s+dictation)\b",
    r"\bcheck\s+(grammar|spelling)\b",
    r"\bproofread\b",
    r"\breview\s+(text|dictation|that|my\s+text)\b",
    r"\bcorrect\s+(grammar|spelling)\b",
    r"\bfix\s+last\s+sentence\b",
)

_UNDO_PATTERNS = (
    r"\bundo\s+last\b",
    r"\bdelete\s+last\b",
    r"\bscratch\s+that\b",
    r"\bremove\s+last\b",
    r"\bforget\s+last\b",
)

_HISTORY_PATTERNS = (
    r"\bshow\s+(history|dictation|text|what\s+i\s+dictated)\b",
    r"\bwhat\s+have\s+i\s+dictated\b",
)

# "correct X to Y" / "change X to Y" / "replace X with Y"
_CORRECT_RE = re.compile(
    r"\b(?:correct|change|replace)\s+(.+?)\s+(?:to|with)\s+(.+)",
    re.IGNORECASE,
)


def is_active() -> bool:
    return _ACTIVE


def is_stop_phrase(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(p in t for p in _STOP_PHRASES)


def _matches(text: str, patterns) -> bool:
    t = (text or "").strip().lower()
    return any(re.search(p, t) for p in patterns)


def is_review_command(text: str) -> bool:
    """True if this utterance is a review command that must NOT be typed."""
    if not text:
        return False
    t = text.strip()
    if _matches(t, _READBACK_PATTERNS):
        return True
    if _matches(t, _FIX_PATTERNS):
        return True
    if _matches(t, _UNDO_PATTERNS):
        return True
    if _matches(t, _HISTORY_PATTERNS):
        return True
    if _CORRECT_RE.search(t):
        return True
    return False


def parse_review_command(text: str):
    """Map a raw utterance to (action, params). Returns None if not a review cmd."""
    if not text:
        return None
    t = text.strip()
    if _matches(t, _UNDO_PATTERNS):
        return ("undo_last", {})
    if _matches(t, _HISTORY_PATTERNS):
        return ("history", {})
    if _matches(t, _READBACK_PATTERNS):
        # "read last 3" → count=3, else last 1 segment
        m = re.search(r"last\s+(\d+)", t, re.IGNORECASE)
        count = int(m.group(1)) if m else 1
        return ("readback", {"count": min(max(count, 1), 5)})
    if _matches(t, _FIX_PATTERNS):
        scope = "all" if re.search(r"\b(all|everything|whole)\b", t, re.IGNORECASE) else "last"
        return ("fix", {"scope": scope})
    m = _CORRECT_RE.search(t)
    if m:
        return ("correct", {"old": m.group(1).strip(), "new": m.group(2).strip()})
    return None


def get_history(count: int = 0):
    """Return last `count` segments (0 = all), oldest first."""
    items = list(_HISTORY)
    if count and count < len(items):
        items = items[-count:]
    return items


def full_text() -> str:
    return " ".join(_HISTORY).strip()


def _remember(text: str) -> None:
    clean = (text or "").strip()
    if clean:
        _HISTORY.append(clean)


def clear_history() -> None:
    _HISTORY.clear()


def type_text(text: str) -> str:
    """Type `text` at the currently focused cursor. Never raises."""
    from actions import computer_control as _cc
    clean = (text or "").strip()
    if not clean:
        return "Nothing to type."
    _remember(clean)
    # Small beat so the keystrokes land in the focused app, not in transit.
    time.sleep(0.2)
    try:
        # Trailing space separates dictated sentences in the target app.
        return _cc._smart_type(clean + " ", clear_first=False)
    except Exception as e:
        return f"Typing failed: {e}"


def _delete_chars(n: int) -> str:
    """Press backspace N times to erase recently typed text. Never raises."""
    n = max(0, int(n))
    if n <= 0:
        return "Nothing to delete."
    try:
        from actions import computer_control as _cc
        # Chunked so a long delete doesn't block forever; ~120 keys/sec.
        remaining = n
        while remaining > 0:
            chunk = min(remaining, 60)
            for _ in range(chunk):
                try:
                    _cc._press_backend("backspace")
                except Exception:
                    # Fallback through the public helper
                    _cc._press("backspace")
            remaining -= chunk
            time.sleep(0.05)
        return f"Deleted {n} characters."
    except Exception as e:
        return f"Delete failed: {e}"


def _gemini_fix(text: str) -> tuple[str, str]:
    """Fix grammar/spelling via Gemini. Returns (fixed, note).

    Raises nothing — on failure returns (original, error note).
    """
    clean = (text or "").strip()
    if not clean:
        return ("", "no text")
    try:
        import json
        from pathlib import Path
        from google import genai
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
        api_key = json.loads(cfg_path.read_text(encoding="utf-8"))["gemini_api_key"]
        client = genai.Client(api_key=api_key)
        prompt = (
            "Fix grammar, spelling and punctuation in the text below. "
            "Keep the meaning and words as close to the original as possible. "
            "Default language is English — output English unless the text is "
            "clearly in another language. "
            "Return ONLY the corrected text, no explanations, no quotes.\n\n"
            f"{clean}"
        )
        resp = client.models.generate_content(model="gemini-flash-latest", contents=prompt)
        fixed = (getattr(resp, "text", "") or "").strip()
        if not fixed:
            return (clean, "empty model reply, kept original")
        return (fixed, "ok")
    except Exception as e:
        return (clean, f"review unavailable ({e})")


def _do_readback(count: int = 1) -> str:
    items = get_history(count)
    if not items:
        return "Nothing dictated yet. Confirm briefly that there is nothing to read back."
    quoted = " ".join(items)
    return (
        f"[READBACK] {quoted}\n"
        "Speak the quoted text back verbatim in English so the user can review it. "
        "Do not add explanations."
    )


def _do_history(player=None) -> str:
    items = list(_HISTORY)
    if not items:
        return "Nothing dictated yet."
    lines = "\n".join(f"{i+1}. {s}" for i, s in enumerate(items))
    if player:
        try:
            if hasattr(player, "show_content"):
                player.show_content("DICTATION — history", lines)
            elif hasattr(player, "write_log"):
                player.write_log("SYS: Dictation history shown.")
        except Exception:
            pass
    return (
        f"Dictated so far ({len(items)} segments):\n{lines}\n"
        "Briefly tell the user the full list is on screen."
    )


def _do_undo_last(player=None) -> str:
    if not _HISTORY:
        return "Nothing to undo — nothing dictated yet."
    removed = _HISTORY.pop()
    # Erase what type_text() typed: segment + trailing space.
    _delete_chars(len(removed) + 1)
    if player:
        try:
            player.write_log(f"SYS: Undid last dictation ({len(removed)} chars).")
        except Exception:
            pass
    return (
        f"Undid last dictation: \"{removed}\". "
        "Confirm briefly in English."
    )


def _do_fix(scope: str = "last", player=None, replace: bool = True) -> str:
    items = list(_HISTORY)
    if not items:
        return "Nothing to fix — nothing dictated yet."
    target = " ".join(items) if scope == "all" else items[-1]
    fixed, note = _gemini_fix(target)
    if fixed != target and replace:
        from actions import computer_control as _cc
        if scope == "all":
            _HISTORY.clear()
            _HISTORY.append(fixed)
            # External app: can't surgically rewrite everything reliably —
            # update the review buffer and retype only if it was short.
            if len(target) < 500:
                _delete_chars(len(target) + len(items))  # text + spaces
                try:
                    time.sleep(0.2)
                    _cc._smart_type(fixed + " ", clear_first=False)
                except Exception as e:
                    return f"Fixed text ready but retype failed: {e}\nFixed: {fixed}"
                action_note = "Replaced the text in the app."
            else:
                action_note = (
                    "Updated in the review buffer — the text is long, "
                    "so say 'retype' explicitly if you want it retyped."
                )
        else:
            _HISTORY[-1] = fixed
            _delete_chars(len(target) + 1)
            # type without double-remembering
            try:
                time.sleep(0.2)
                _cc._smart_type(fixed + " ", clear_first=False)
            except Exception as e:
                return f"Fixed text ready but retype failed: {e}\nFixed: {fixed}"
            action_note = "Replaced the last sentence in the app."
    elif fixed != target:
        action_note = "Showing the fix — not applied to the app."
    else:
        action_note = "No changes needed." if note == "ok" else f"Kept original ({note})."
    if player:
        try:
            if hasattr(player, "show_content"):
                player.show_content("DICTATION — grammar fix", f"Before: {target}\n\nAfter: {fixed}")
        except Exception:
            pass
    return (
        f"Original: {target}\nFixed: {fixed}\n{action_note} "
        "Briefly summarise the fix in English and read the fixed sentence."
    )


def _do_correct(old: str, new: str, player=None) -> str:
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or not new:
        return "Say what to correct, e.g. 'correct their to there'."
    items = list(_HISTORY)
    if not items:
        return "Nothing dictated yet — nothing to correct."
    # Prefer the most recent segment containing `old` (case-insensitive).
    idx = -1
    for i in range(len(items) - 1, -1, -1):
        if old.lower() in items[i].lower():
            idx = i
            break
    if idx == -1:
        return (
            f"Could not find \"{old}\" in the last {len(items)} dictated segments. "
            f"Say 'show history' to review, then try again."
        )
    # Case-preserving single replacement within that segment.
    pat = re.compile(re.escape(old), re.IGNORECASE)
    corrected = pat.sub(new, items[idx], count=1)
    _HISTORY[idx] = corrected
    if idx == len(items) - 1:
        # Last segment → can fix the app by deleting + retyping it.
        # items[idx] still holds the ORIGINAL (list() copy taken before edit).
        _delete_chars(len(items[idx]) + 1)  # segment + trailing space
        from actions import computer_control as _cc
        try:
            time.sleep(0.2)
            _cc._smart_type(corrected + " ", clear_first=False)
        except Exception as e:
            return f"Updated review buffer but retype failed: {e}"
        note = "Replaced the last sentence in the app."
    else:
        note = (
            f"Updated segment {idx+1} in the review buffer — it is not the last "
            "sentence, so the app on screen still shows the old wording. "
            "Say 'show history' to verify."
        )
    if player:
        try:
            player.write_log(f"SYS: Corrected \"{old}\" → \"{new}\".")
        except Exception:
            pass
    return f"Corrected \"{old}\" to \"{new}\". {note} Confirm briefly in English."


def dictate(parameters: dict, player=None, speak=None) -> str:
    """Handler for the dictate tool (auto-discovered via TOOL below)."""
    global _ACTIVE
    params = parameters or {}
    action = str(params.get("action", "")).lower().strip()

    if action == "start":
        _ACTIVE = True
        if player:
            player.write_log("SYS: Dictation ON — everything you say will be typed where you click.")
        return (
            "Dictation mode ON. Tell the user, in one short sentence, to click "
            "a textbox and just speak — everything they say will be typed there "
            "until they say 'stop dictating'. "
            "Review commands are available any time: 'read that back', "
            "'fix grammar', 'correct X to Y', 'undo last', 'show history' — "
            "none of them get typed."
        )

    if action == "stop":
        _ACTIVE = False
        if player:
            player.write_log("SYS: Dictation OFF.")
        return (
            "Dictation mode OFF. Confirm briefly that you stopped typing "
            "and are back to normal."
        )

    if action == "status":
        state = "ON" if _ACTIVE else "OFF"
        n = len(_HISTORY)
        return f"Dictation mode is {state}. {n} segment(s) in this session."

    if action == "type":
        text = params.get("text", "")
        if not text:
            return "No text given to type."
        # Never type review commands even if the model routes them here.
        if is_stop_phrase(text) or is_review_command(text):
            parsed = parse_review_command(text)
            if parsed:
                act, kw = parsed
                return dictate({"action": act, **kw}, player, speak)
            return "Skipped — that was a dictation control phrase, not typed."
        return type_text(text)

    if action == "readback":
        try:
            count = int(params.get("count", 1))
        except Exception:
            count = 1
        return _do_readback(max(1, min(count, 5)))

    if action == "fix":
        scope = str(params.get("scope", "last")).lower().strip()
        if scope not in ("last", "all"):
            scope = "last"
        replace = str(params.get("replace", "true")).lower().strip() not in ("false", "0", "no")
        return _do_fix(scope, player, replace)

    if action == "correct":
        return _do_correct(params.get("old", ""), params.get("new", ""), player)

    if action == "undo_last":
        return _do_undo_last(player)

    if action == "history":
        return _do_history(player)

    if action == "clear":
        clear_history()
        return "Dictation history cleared."

    return (
        "Unknown dictate action. "
        "Use action=start | stop | status | type | readback | fix | correct | undo_last | history | clear."
    )


TOOL = {
    "name": "dictate",
    "description": (
        "Type text into WHATEVER textbox the user has clicked, in ANY application "
        "(browser, editor, chat, document — anywhere with a cursor). "
        "Use action='type' with the exact text when the user says 'type ...', "
        "'write ... there', or dictates a sentence. "
        "Use action='start' when the user says 'start dictating' or 'dictation on' — "
        "then everything they say is typed automatically. "
        "Use action='stop' when they say 'stop dictating'. "
        "Review the dictated text WITHOUT typing the command itself: "
        "action='readback' when they say 'read that back' / 'repeat last' / "
        "'what did I just say' (optional count=1-5); "
        "action='fix' when they say 'fix grammar' / 'proofread' / 'review text' "
        "(scope=last|all, Gemini-powered); "
        "action='correct' with old+new when they say 'correct X to Y' / "
        "'replace X with Y' / 'change X to Y'; "
        "action='undo_last' when they say 'undo last' / 'scratch that' / "
        "'delete last'; "
        "action='history' when they say 'show history' / 'show dictation'. "
        "Use action='status' to check whether dictation mode is on."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "type | start | stop | status | readback | fix | correct | undo_last | history | clear",
            },
            "text": {
                "type": "STRING",
                "description": "Exact text to type (for action='type')",
            },
            "count": {
                "type": "NUMBER",
                "description": "How many recent segments to read back (for action='readback', 1-5)",
            },
            "scope": {
                "type": "STRING",
                "description": "Which text to fix (for action='fix'): last | all",
            },
            "old": {
                "type": "STRING",
                "description": "Phrase to replace (for action='correct')",
            },
            "new": {
                "type": "STRING",
                "description": "Replacement phrase (for action='correct')",
            },
            "replace": {
                "type": "STRING",
                "description": "For action='fix': 'true' retypes the fix in the app, 'false' review only",
            },
        },
        "required": ["action"],
    },
    "handler": dictate,
}
