"""
opencode_agent.py — hand a spoken task to opencode and report what it did.

WHY THIS EXISTS
    dev_agent.py can already write a small project from scratch, but it can only
    do that one shape of work: a green-field multi-file project built by a
    sequence of single-shot Gemini calls, with its own retry loop. The moment
    the user asks for real engineering work — "refactor this module", "why is
    this test failing", "add pagination to the users endpoint", "review my
    uncommitted diff" — it is the wrong tool, because answering those questions
    means reading a real repository and iterating, not generating files from a
    description.

    The coding agent is already installed and already authenticated on this
    machine. It already has the repository indexed, the LSP wired up, ripgrep
    available and a session that remembers the last twenty turns. So the
    assistant's job is not to re-implement any of that. Its job is to open a
    terminal, hand over the task, and report the outcome.

THE DESIGN PROBLEM, AND THE ANSWER
    `opencode run` takes minutes, not milliseconds. A voice assistant that
    blocks on it has said nothing for the entire duration, which from the
    user's chair is indistinguishable from a crash.

    So a run is a JOB, not a call. `action=run` starts a detached worker,
    returns a one-line acknowledgement immediately, and streams progress into
    the HUD log as it arrives. The user can keep talking. `action=status` and
    `action=result` are cheap polls; `action=cancel` stops it. The session id
    opencode prints is kept, so a follow-up ("now add tests for that") resumes
    the same session and inherits the context of the first task instead of
    starting cold.

    This is also why the model must be told, in the tool description, that a
    run is asynchronous — otherwise it narrates a task as finished the moment
    the tool returns, which is a lie the user discovers thirty seconds later.

WHY --format json, PARSED DEFENSIVELY
    The default output is a human-readable transcript. The json format is
    newline-delimited events. The obvious thing is to switch on `type` and read
    a fixed field path — and that is what breaks on the next opencode release,
    because the event schema is internal and versioned with the binary rather
    than with this file. So the parser below extracts text by trying several
    known shapes and keeping whatever is non-empty, and falls back to treating
    an unparseable line as plain text. A schema change degrades the log; it
    does not break the job.

SAFETY
    * No shell=True anywhere. argv lists only, so a task containing a quote or a
      semicolon is a task, not an injection.
    * The working directory must exist. A path that does not resolve is the
      difference between "work in my project" and "work in /" — and this is the
      one parameter that decides how much of the filesystem is reachable.
    * --auto is opt-in per call and defaults to the stored setting. It makes
      opencode approve its own edits, which is what "do this task" means, but
      it is a real widening of blast radius, so it is never enabled silently by
      a default that happens to be true.
    * Confirmed only via core/confirm.py, whose token the model cannot forge.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from core import os_detect
except Exception:                                       # pragma: no cover
    os_detect = None

try:
    from core import confirm as confirm_gate
except Exception:                                       # pragma: no cover
    confirm_gate = None

try:
    from memory import config_manager
except Exception:                                       # pragma: no cover
    config_manager = None

CLI_NAME = "opencode"

# How long a job may run before it is killed. Generous on purpose: a refactor
# with a test suite legitimately takes many minutes, and the user can always
# cancel. What matters is that a wedged process cannot pin a slot forever.
DEFAULT_TIMEOUT = 3600
MAX_TIMEOUT = 14400                    # 4 hours — beyond this, something is wrong

# Cap on the transcript kept per job. Jobs are held in memory for the life of
# the process, and an uncapped event stream from a long autonomous run will
# eventually take the assistant down with it.
MAX_TRANSCRIPT_LINES = 400
MAX_TRANSCRIPT_CHARS = 24000

# How many finished jobs to remember. Old ones are dropped with their
# transcripts; a user asking "what did it do" means the last few minutes.
MAX_JOBS = 12

_STALE_JOB_SECONDS = 30 * 60


# ── Job state ────────────────────────────────────────────────────────────────

@dataclass
class _Job:
    id: str
    task: str
    directory: str
    model: str = ""
    agent: str = ""
    auto: bool = False
    session_id: str = ""
    status: str = "running"           # running | done | failed | cancelled
    detail: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    returncode: int = -1
    transcript: list = field(default_factory=list)
    answer: str = ""
    events: int = 0
    process: object = None
    cancel_requested: bool = False
    _lock: object = field(default_factory=threading.Lock, repr=False)

    def add(self, line: str) -> None:
        text = (line or "").strip()
        if not text:
            return
        with self._lock:
            self.transcript.append(text[:600])
            while len(self.transcript) > MAX_TRANSCRIPT_LINES:
                self.transcript.pop(0)
            while sum(len(t) for t in self.transcript) > MAX_TRANSCRIPT_CHARS:
                self.transcript.pop(0)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id, "task": self.task, "directory": self.directory,
                "model": self.model, "agent": self.agent, "status": self.status,
                "detail": self.detail, "session_id": self.session_id,
                "events": self.events, "answer": self.answer,
                "returncode": self.returncode,
                "elapsed": round((self.finished or time.time()) - self.started, 1),
                "transcript": list(self.transcript),
            }


_jobs: dict = {}
_jobs_lock = threading.Lock()


def _store(job: _Job) -> None:
    with _jobs_lock:
        _jobs[job.id] = job
        # Drop the oldest finished jobs. Running ones are never evicted — losing
        # the handle to a process that is still writing files would be worse
        # than holding a little memory.
        finished = sorted(
            (j for j in _jobs.values() if j.status != "running"),
            key=lambda j: j.finished or j.started,
        )
        for stale in finished[: max(0, len(_jobs) - MAX_JOBS)]:
            _jobs.pop(stale.id, None)


def _get(job_id: str) -> "_Job | None":
    if not job_id:
        return None
    with _jobs_lock:
        return _jobs.get(str(job_id).strip())


def _latest() -> "_Job | None":
    with _jobs_lock:
        if not _jobs:
            return None
        return max(_jobs.values(), key=lambda j: j.started)


def _prune() -> None:
    """Forget jobs that finished long ago, so a morning session does not report
    yesterday's run as if it were current."""
    cutoff = time.time() - _STALE_JOB_SECONDS
    with _jobs_lock:
        for job_id, job in list(_jobs.items()):
            if job.status != "running" and job.finished and job.finished < cutoff:
                _jobs.pop(job_id, None)


# ── Config ───────────────────────────────────────────────────────────────────

def _settings() -> dict:
    if config_manager is None:
        return {}
    try:
        return config_manager.get_opencode_config()
    except Exception:
        return {}


def _setting(key: str, default=None):
    try:
        value = _settings().get(key, default)
    except Exception:
        return default
    return default if value is None else value


def _setting_bool(key: str, default: bool) -> bool:
    value = _setting(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ── Locating and describing the CLI ──────────────────────────────────────────

def _cli_path() -> str:
    """Absolute path to the opencode binary, or "" if it is not installed."""
    if os_detect is None:
        return ""
    try:
        return os_detect.find_cli(CLI_NAME)
    except Exception:
        return ""


def _not_installed() -> str:
    where = "your PATH" if not os_detect else "PATH or the usual install folders"
    return (
        f"I could not find the opencode command on {where}, so I cannot hand "
        f"the task over. It installs with: npm install -g opencode-ai"
    )


def _pop_kwargs() -> dict:
    if os_detect is None:
        return {}
    return ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
            if os_detect.is_windows() else {})


def _human_elapsed(seconds: float) -> str:
    seconds = int(max(0, seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


# ── Log / speak plumbing ─────────────────────────────────────────────────────

def _log(message: str, player=None) -> None:
    print(f"[opencode] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass


def _say(text: str, speak=None) -> None:
    if speak:
        try:
            speak(text)
        except Exception:
            pass


# ── Event parsing ────────────────────────────────────────────────────────────

def _dig(obj, *names):
    """First non-empty value among `names`, one level of nesting at a time.

    opencode's json event envelope has changed shape between releases, so the
    reader looks for a field under a few plausible names and in a few plausible
    places rather than binding to one path that a version bump would break.
    """
    if not isinstance(obj, dict):
        return ""
    for name in names:
        value = obj.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    for nested in ("part", "data", "properties", "info"):
        inner = obj.get(nested)
        if isinstance(inner, dict):
            for name in names:
                value = inner.get(name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


def _event_text(obj: dict) -> str:
    """A human-readable line from one json event, or "" if it has nothing to say."""
    kind = str(obj.get("type") or obj.get("event") or "").lower()

    if kind == "error":
        message = _dig(obj, "message", "error", "text")
        if not message:
            data = obj.get("error")
            if isinstance(data, dict):
                message = _dig(data, "message", "text", "name")
            elif isinstance(data, str):
                message = data
        return f"ERROR: {message}" if message else "ERROR (no detail reported)"

    # Tool activity: name plus a short status, so the HUD shows *what* it is
    # doing rather than a wall of prose. The state is at obj["state"] on some
    # events and obj["part"]["state"] on others, so both are tried.
    if "tool" in kind or kind in ("tool-start", "tool-result", "tool"):
        name = _dig(obj, "tool", "name")
        state = obj.get("state")
        if not isinstance(state, dict):
            part = obj.get("part")
            state = part.get("state") if isinstance(part, dict) else None
        status = str(state.get("status") or "") if isinstance(state, dict) else ""
        if name and status:
            return f"[{status}] {name}"
        if name:
            return f"using {name}"
        return ""

    if kind in ("text", "text-start", "message", "assistant"):
        return _dig(obj, "text", "content", "message")

    if kind in ("reasoning", "reasoning-start", "thinking"):
        text = _dig(obj, "text", "thinking", "content")
        return f"thinking: {text[:160]}" if text else ""

    if kind in ("step-start", "step-finish", "session-start", "session-idle"):
        session = _dig(obj, "sessionID", "session_id", "sessionID")
        if session:
            return f"session {session}"
        return ""

    if kind == "permission" or "permission" in kind:
        return _dig(obj, "message", "text") or "permission requested"

    # Unknown type: fall back to any string field that reads like prose.
    return _dig(obj, "text", "message", "content")


def _answer_from(obj: dict) -> str:
    """Assistant text worth treating as the final answer, or ""."""
    kind = str(obj.get("type") or "").lower()
    if kind not in ("text", "message", "assistant", "text-start"):
        return ""
    return _dig(obj, "text", "content", "message")


# ── Running the job ──────────────────────────────────────────────────────────

def _build_argv(job: _Job, task: str, session_id: str = "") -> list:
    """argv for one opencode invocation. Never uses a shell."""
    argv = [_cli_path(), "run", "--format", "json", "--dir", job.directory]
    if job.model:
        argv += ["--model", job.model]
    if job.agent:
        argv += ["--agent", job.agent]
    if job.auto:
        argv.append("--auto")
    if session_id:
        # Resuming by id rather than --continue: --continue silently attaches to
        # whatever ran last, which is the wrong session as soon as the user has
        # two projects open.
        argv += ["--session", session_id]
    argv.append(task)
    return argv


def _worker(job: _Job, task: str, session_id: str, player) -> None:
    """Body of a delegated task. Runs on its own thread; never raises."""
    started = time.time()
    try:
        argv = _build_argv(job, task, session_id)
        popen_kwargs = {
            "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
            "text": True, "bufsize": 1, "encoding": "utf-8",
            "errors": "replace", "cwd": job.directory, **_pop_kwargs(),
        }
        if os_detect is not None and os_detect.is_windows():
            popen_kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            popen_kwargs["start_new_session"] = True

        job.process = subprocess.Popen(argv, **popen_kwargs)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        job.status, job.detail, job.finished = "failed", f"could not start opencode: {e}", time.time()
        _log(f"Job {job.id[:6]} failed to start — {e}", player)
        return

    answers: list = []
    try:
        assert job.process.stdout is not None
        for raw in job.process.stdout:
            if job.cancel_requested:
                break
            line = (raw or "").strip()
            if not line:
                continue

            text, answer = "", ""
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    job.events += 1
                    session = _dig(event, "sessionID", "session_id")
                    if session and not job.session_id:
                        job.session_id = session
                    text = _event_text(event)
                    answer = _answer_from(event)
            except (ValueError, TypeError):
                # Not json: the default-format transcript, or a warning line.
                text = line

            if answer:
                answers.append(answer)
            if text:
                job.add(text)
                _log(text[:200], player)
    except Exception as e:                                  # pragma: no cover
        job.detail = f"output stream failed: {e}"
    finally:
        if job.cancel_requested and job.process.poll() is None:
            _terminate(job)
        try:
            job.returncode = job.process.wait(timeout=20)
        except subprocess.TimeoutExpired:                   # pragma: no cover
            _terminate(job)
            try:
                job.returncode = job.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                job.returncode = -1
        except Exception:                                   # pragma: no cover
            job.returncode = -1

    job.finished = time.time()
    if answers:
        job.answer = "\n".join(answers).strip()
    else:
        # No structured assistant text — fall back to the transcript, minus the
        # bracketed tool-status lines, which are progress noise rather than an
        # answer.
        tail = [t for t in job.transcript if not t.startswith("[")]
        job.answer = "\n".join(tail).strip()

    if job.cancel_requested:
        job.status = "cancelled"
        job.detail = job.detail or "stopped at your request"
    elif job.returncode == 0:
        job.status = "done"
        job.detail = f"finished in {_human_elapsed(job.finished - started)}"
    else:
        job.status = "failed"
        errors = [t for t in job.transcript if t.startswith("ERROR:")]
        job.detail = errors[0] if errors else f"opencode exited with code {job.returncode}"

    _log(f"Job {job.id[:6]} {job.status} — {job.detail}", player)


def _terminate(job: _Job) -> None:
    """Stop the process tree, not just the parent.

    opencode spawns ripgrep, language servers and test runners. Killing only the
    parent orphans them, they keep holding the working directory, and the next
    run in that project fails for reasons that look unrelated.
    """
    process = job.process
    if process is None or process.poll() is not None:
        return
    try:
        if os_detect is not None and os_detect.is_windows():
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=20, **_pop_kwargs())
        else:
            import signal
            group = os.getpgid(process.pid)
            os.killpg(group, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(group, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        print(f"[opencode] terminate failed: {e}")
        try:
            process.kill()
        except Exception:
            pass


# ── Working directory resolution ─────────────────────────────────────────────

def _resolve_dir(raw: str, strict: bool = False) -> str:
    """Turn a spoken directory into a real one. Falls back to home.

    A task with no directory runs in the user's home rather than in whatever
    the process happens to have been started from — which for a desktop app is
    '/' on Linux. Never returns a path that does not exist.

    `strict=True` refuses the parent-directory guess and returns home for
    anything unresolvable. Used when the result becomes a `cd` in a terminal
    the user is about to watch: quietly running opencode in the parent of the
    folder they named would be a confusing thing to see happen.
    """
    candidate = (raw or "").strip().strip('"').strip("'")
    if not candidate:
        return str(Path.home())

    expanded = Path(os.path.expandvars(candidate)).expanduser()
    if not expanded.is_absolute():
        # A relative path is ambiguous, so try both readings: CWD first, so that
        # "." and "./sub" mean what the caller plainly intended, then home, so a
        # bare "Documents" still resolves for an app whose CWD is "/".
        for base in (Path.cwd(), Path.home()):
            probe = base / expanded
            try:
                if probe.is_dir():
                    return str(probe.resolve())
            except OSError:
                continue
        return str(Path.home())
    try:
        if expanded.is_dir():
            return str(expanded.resolve())
    except OSError:
        pass
    if strict:
        return str(Path.home())

    # Not a directory as given — try the parent, which covers "~/projects/mark"
    # when only ~/projects exists, and is a far better guess than home.
    try:
        parent = expanded.parent
        if parent.is_dir():
            return str(parent.resolve())
    except OSError:
        pass
    return str(Path.home())


def _looks_destructive(task: str) -> bool:
    """Whether a task should be confirmed before it is handed over.

    Deliberately narrow. opencode is *supposed* to edit files, run tests and
    rewrite modules; a filter broad enough to catch that would gate every task
    and train the user to press CONFIRM without reading. These are the
    operations where the wrong answer destroys something outside the project.
    """
    lowered = (task or "").lower()
    markers = (
        "rm -rf", "rm -fr", "format the disk", "wipe", "erase the disk",
        "delete the repository", "delete the repo", "drop the database",
        "drop database", "delete all my files", "delete everything",
        "force push", "push --force", "git push -f", "reset --hard",
        "remove all branches", "revoke", "nuke",
    )
    return any(marker in lowered for marker in markers)


# ── Handler ──────────────────────────────────────────────────────────────────

def opencode_agent(parameters: dict = None, player=None, speak=None,
                   session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action", "run") or "run").strip().lower()
    task = str(params.get("task", "") or "").strip()
    directory = str(params.get("directory", "") or "").strip()
    model = str(params.get("model", "") or "").strip()
    agent = str(params.get("agent", "") or "").strip()
    job_id = str(params.get("job_id", "") or "").strip()
    timeout_raw = params.get("timeout", None)

    if action in ("info", "detect", "check", "doctor"):
        return _act_info(player)

    if not _cli_path():
        return _not_installed()

    if action in ("list", "agents", "models"):
        return _act_list()

    if action in ("open", "launch", "tui", "interactive"):
        return _act_open(directory or task, player, speak)

    if action == "status":
        return _act_status(job_id)

    if action in ("result", "output", "report", "wait"):
        return _act_result(job_id, wait=action == "wait")

    if action in ("cancel", "stop", "kill", "abort"):
        return _act_cancel(job_id)

    if action in ("continue", "resume", "followup"):
        return _act_continue(params, task, job_id, player, speak)

    if action in ("run", "task", "do", "delegate", "build", ""):
        return _act_run(params, task, directory, model, agent, timeout_raw,
                        player, speak)

    return (
        f"Unknown opencode_agent action '{action}'. Use one of: run, continue, "
        f"status, result, cancel, list, open, info."
    )


def _act_info(player=None) -> str:
    if os_detect is None:
        return "Operating system detection is unavailable, so opencode cannot be verified."
    path = _cli_path()
    info = os_detect.os_info()
    lines = [f"Operating system: {os_detect.describe_os()}",
             f"Shell: {info.get('shell') or 'unknown'}"]

    if not path:
        lines.append("opencode: NOT INSTALLED (searched PATH and the usual "
                     "install folders). Install with: npm install -g opencode-ai")
        return "\n".join(lines)

    version = os_detect.cli_version(CLI_NAME) if hasattr(os_detect, "cli_version") else ""
    lines.append(f"opencode: installed at {path}" + (f" (version {version})" if version else ""))
    has_terminal = os_detect.find_terminal()[1] != "none"
    lines.append("A graphical terminal is available."
                 if has_terminal else
                 "No graphical terminal found — interactive opencode will fall back to headless.")
    lines.append(f"Stored settings: model={_setting('model') or '(opencode config)'} "
                 f"agent={_setting('agent') or '(opencode default)'} "
                 f"auto-approve={_setting_bool('auto', True)}")
    return "\n".join(lines)


def _act_list() -> str:
    """What opencode can be: its configured agents, and a count of its models."""
    try:
        agents = subprocess.run(
            [_cli_path(), "agent", "list"], capture_output=True, text=True,
            timeout=25, **_pop_kwargs())
        raw = (agents.stdout or "") + (agents.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return f"Could not list opencode agents: {e}"

    names: list = []
    # "opencode agent list" prints "<name> (<mode>)" headings followed by that
    # agent's permission JSON. Only the headings are wanted — the JSON would
    # otherwise be read back to the user verbatim.
    heading = re.compile(r"^([A-Za-z0-9_-]+)\s*\((?:primary|subagent|all)\)\s*$")
    for line in raw.splitlines():
        match = heading.match(line.strip())
        if match and match.group(1) not in names:
            names.append(match.group(1))
    if not names:
        # Fall back to any indented "<name> (" line, in case the mode label
        # wording changed. Better a loose name than "could not parse".
        loose = re.compile(r"^\s{2,}([A-Za-z0-9_-]+)\s*\(")
        for line in raw.splitlines():
            match = loose.match(line)
            if match and match.group(1) not in names:
                names.append(match.group(1))

    try:
        models = subprocess.run(
            [_cli_path(), "models"], capture_output=True, text=True,
            timeout=25, **_pop_kwargs())
        model_count = len([l for l in (models.stdout or "").splitlines() if l.strip()])
    except (OSError, subprocess.SubprocessError):
        model_count = 0

    out = [f"opencode agents: {', '.join(names) if names else 'could not parse'}"]
    out.append(f"{model_count} model(s) available — name one with model=provider/model, "
               f"or omit it to use the opencode default.")
    out.append("Default agent: " + (_setting("agent") or "build (opencode's own default)"))
    return "\n".join(out)


def _act_open(target: str, player=None, speak=None) -> str:
    """Open the interactive opencode TUI in a visible terminal.

    This is the "open opencode" path: a real window the user drives themselves,
    for when they want to watch or take over rather than delegate.
    """
    if os_detect is None:
        return "Operating system detection is unavailable, so I cannot open a terminal."
    # strict: if the named folder does not exist, open in home rather than
    # silently cd-ing into whatever the parent happens to be.
    workdir = _resolve_dir(target, strict=True)
    path = _cli_path()

    # If a directory was named, cd there first so the TUI opens on that project.
    inner = f"cd {_sh_quote(workdir)} && exec {path}"
    argv, kind = os_detect.terminal_command(inner)
    if kind == "none":
        # No GUI terminal on this box. Fall back to the ACP/headless server so
        # the request is still honoured rather than refused.
        return (_fallback_headless(workdir, player)
                or "There is no terminal application available on this system "
                   "to open opencode in.")

    if os_detect.launch_detached(argv, workdir):
        _log(f"Opened opencode in a terminal at {workdir}", player)
        return (f"Opencode is open in a terminal at {workdir}. The cursor is in "
                f"that window — type your instructions there, or ask me to run a "
                f"task for you instead.")
    return "I could not open a terminal window on this system."


def _fallback_headless(workdir: str, player=None) -> str:
    """Last resort when no terminal exists: start a detached opencode server."""
    if os_detect is None:
        return ""
    argv = [_cli_path(), "serve", "--port", "0", "--hostname", "127.0.0.1"]
    if os_detect.launch_detached(argv, workdir):
        _log("Started opencode headless server (no terminal available)", player)
        return ("No terminal application is installed, so I started opencode as a "
                "headless server instead. I can still run tasks on it — just ask.")
    return ""


def _sh_quote(path: str) -> str:
    """Single-quote a path for the shell the terminal will start."""
    return "'" + str(path).replace("'", "'\\''") + "'"


def _act_status(job_id: str) -> str:
    _prune()
    job = _get(job_id) or _latest()
    if job is None:
        return ("I have not run any opencode task yet in this session. Say what "
                "you want done and I will start one.")
    snap = job.snapshot()
    if snap["status"] == "running":
        return (f"Opencode task {snap['id'][:6]} is still working on "
                f"\"{_short(snap['task'])}\" — {_human_elapsed(snap['elapsed'])} "
                f"so far, in {snap['directory']}. Ask for the result when you want it.")
    tail = snap["transcript"][-1] if snap["transcript"] else ""
    extra = f" Last thing it said: {tail[:180]}" if tail else ""
    return (f"Opencode task {snap['id'][:6]} {snap['status']} — {snap['detail']}. "
            f"Task: \"{_short(snap['task'])}\".{extra}")


def _act_result(job_id: str, wait: bool = False) -> str:
    _prune()
    job = _get(job_id) or _latest()
    if job is None:
        return "There is no opencode task to report on yet."

    if job.status == "running":
        if not wait:
            return _act_status(job_id)
        # Bounded wait, not an open-ended one: the Live session must keep
        # answering, so a tool call cannot block until the job finishes.
        deadline = time.time() + 55
        while time.time() < deadline and job.status == "running":
            time.sleep(1.5)
        if job.status == "running":
            return (f"Opencode is still working on \"{_short(job.task)}\" after "
                    f"{_human_elapsed(time.time() - job.started)}. It keeps running "
                    f"in the background — ask me for the result again in a bit, or "
                    f"carry on talking; I will not lose track of it.")

    snap = job.snapshot()
    header = f"Opencode {snap['status']} — {snap['detail']}. Task: \"{_short(snap['task'])}\"."
    if snap["directory"]:
        header += f" In: {snap['directory']}."

    if not snap["answer"]:
        tail = "\n".join(snap["transcript"][-12:]) or "(it produced no output)"
        return f"{header}\n\n{tail[:1600]}"

    answer = snap["answer"]
    # A long autonomous run ends with a summary worth reading, but the model's
    # context is not a log file: cap it and keep the tail, which is where the
    # conclusion is.
    if len(answer) > 3000:
        answer = "...(earlier output trimmed)...\n" + answer[-3000:]
    return f"{header}\n\n{answer}"


def _act_cancel(job_id: str) -> str:
    job = _get(job_id) or _latest()
    if job is None:
        return "There is no opencode task to cancel."
    if job.status != "running":
        return f"That task already {job.status} — nothing to cancel."
    job.cancel_requested = True
    threading.Thread(target=_terminate, args=(job,), daemon=True).start()
    return (f"I have asked opencode to stop working on \"{_short(job.task)}\". "
            f"It may take a few seconds to actually stop.")


def _act_continue(params: dict, task: str, job_id: str, player=None, speak=None) -> str:
    """Follow-up turn on the session a previous task established."""
    _prune()
    job = _get(job_id) or _latest()
    if job is None or not job.session_id:
        return ("I do not have an opencode session to continue — no earlier task "
                "finished far enough to create one. Give me the task as a whole "
                "and I will start one.")
    if not task:
        return "What should I ask opencode to do next?"

    session_id = job.session_id
    new_job = _new_job(
        task=task,
        directory=params.get("directory") or job.directory,
        model=params.get("model") or job.model,
        agent=params.get("agent") or job.agent,
        auto=job.auto,
    )
    threading.Thread(target=_worker, args=(new_job, task, session_id, player),
                     daemon=True).start()
    _log(f"Continuing session {session_id[:12]} — {task[:70]}", player)
    return (f"Opencode is continuing that work: {_short(task)}. It is running in "
            f"the background as task {new_job.id[:6]}; ask for the result whenever "
            f"you want it.")


def _act_run(params: dict, task: str, directory: str, model: str, agent: str,
             timeout_raw, player=None, speak=None) -> str:
    if not task:
        return ("What should opencode do? Describe the task in your own words and "
                "I will hand it over.")

    workdir = _resolve_dir(directory or str(_setting("workdir", "") or ""))
    resolved_model = model or str(_setting("model", "") or "")
    resolved_agent = agent or str(_setting("agent", "") or "")
    auto = params.get("auto")
    if auto is None:
        auto = _setting_bool("auto", True)
    else:
        auto = bool(auto) if isinstance(auto, bool) else str(auto).lower() in ("1", "true", "yes", "on")

    try:
        timeout = int(float(timeout_raw)) if timeout_raw not in (None, "") else int(
            _setting("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(30, min(timeout, MAX_TIMEOUT))

    if _looks_destructive(task):
        return _confirm_or_start(task, workdir, resolved_model, resolved_agent,
                                 auto, timeout, player, speak)

    new_job = _new_job(task=task, directory=workdir, model=resolved_model,
                       agent=resolved_agent, auto=auto)
    _launch(new_job, task, "", timeout, player, speak)
    return (_started_message(new_job, resolved_agent, resolved_model))


def _confirm_or_start(task: str, workdir: str, model: str, agent: str, auto: bool,
                      timeout: int, player=None, speak=None) -> str:
    """Route a destructive-looking task through the on-screen gate.

    Uses core/confirm.py, whose token is issued by the interface and cannot be
    forged by the model — the reason a `confirmed=yes` parameter is not good
    enough. Fails closed when no interface is bound.
    """
    if confirm_gate is None:
        return "I need confirmation on screen to do that, and it is unavailable. Nothing was run."

    def _do() -> str:
        new_job = _new_job(task=task, directory=workdir, model=model,
                           agent=agent, auto=auto)
        _launch(new_job, task, "", timeout, player, speak)
        return f"opencode task {new_job.id[:6]} started."

    return confirm_gate.request(
        key="opencode-destructive",
        title="Hand a destructive task to opencode",
        detail=(f"opencode will work in:\n{workdir}\n\nTask:\n{task[:400]}\n\n"
                f"It runs with edit approval {'ON' if auto else 'OFF'}. "
                f"Files outside this folder can still be touched if the task asks."),
        run=_do,
    )


def _new_job(task: str, directory: str, model: str = "", agent: str = "",
             auto: bool = False) -> _Job:
    job = _Job(id=uuid.uuid4().hex[:8], task=task, directory=directory,
               model=model, agent=agent, auto=auto)
    _store(job)
    return job


def _launch(job: _Job, task: str, session_id: str, timeout: int,
            player=None, speak=None) -> None:
    """Start the worker, and make sure the job cannot outlive its timeout."""
    threading.Thread(target=_worker, args=(job, task, session_id, player),
                     daemon=True).start()

    def _enforce() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if job.status != "running":
                return
            time.sleep(2.0)
        if job.status == "running":
            job.cancel_requested = True
            _terminate(job)

    threading.Thread(target=_enforce, daemon=True).start()


def _started_message(job: _Job, agent: str, model: str) -> str:
    which = f" with the {agent} agent" if agent else ""
    which += f" on {model}" if model else ""
    return (f"Opencode is on it{which} — working in {job.directory}. That takes a "
            f"few minutes, so keep talking; ask me how it is going or for the "
            f"result whenever you like. Task reference {job.id[:6]}.")


def _short(text: str, limit: int = 90) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
# Every action is named in the description, in the order the model should
# consider them. That is deliberate: the model picks a tool from this text
# alone, and an unnamed action is an action that never gets called.
TOOL = {
    "name": "opencode_agent",
    "description": (
        "Delegates real engineering work to the opencode coding agent on this "
        "machine, which already has the project indexed, ripgrep, the language "
        "servers and a session that remembers previous turns. Use this for any "
        "task that needs reading and changing a real codebase: writing a "
        "feature, fixing a failing test, refactoring a module, explaining or "
        "documenting existing code, reviewing a diff, investigating a bug, "
        "adding tests, writing a script, or a multi-step job that would take "
        "many tool calls. "
        "ACTIONS: "
        "action='run' starts the task in the background and returns "
        "IMMEDIATELY — it is asynchronous, so NEVER tell the user the task is "
        "finished when run returns; say it has started, and use action='status' "
        "or action='result' to report progress later. "
        "action='continue' sends a follow-up to the same opencode session, so "
        "'now add tests for that' keeps the context instead of starting cold. "
        "action='status' is a cheap progress check. "
        "action='result' returns what it said (action='wait' blocks up to ~55s "
        "first, for when the user is waiting). "
        "action='cancel' stops a running task. "
        "action='list' shows available agents and how many models exist. "
        "action='open' opens the interactive opencode window for the user to "
        "drive themselves, when they ask to 'see it' or 'open opencode'. "
        "action='info' reports the detected operating system, the opencode "
        "install path and version. "
        "PARAMETERS: task (what to do, in English, specific enough to act on), "
        "directory (the project folder; defaults to the home directory — pass it "
        "whenever the work belongs to a specific repository), model "
        "(optional provider/model), agent (optional opencode agent name), "
        "timeout (seconds, default 3600), job_id (for status/result/cancel), "
        "auto (approve edits automatically)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "run | continue | status | result | wait | cancel | list | "
                    "open | info"
                ),
            },
            "task": {
                "type": "STRING",
                "description": "What opencode should do, in English and specific.",
            },
            "directory": {
                "type": "STRING",
                "description": (
                    "Project folder to work in. Defaults to the home directory. "
                    "Always set this when the task belongs to a repository."
                ),
            },
            "model": {
                "type": "STRING",
                "description": "Optional model as provider/model, e.g. openrouter/anthropic/claude-sonnet-4-5.",
            },
            "agent": {
                "type": "STRING",
                "description": "Optional opencode agent name (build, plan, general ...).",
            },
            "job_id": {
                "type": "STRING",
                "description": "Task reference from a previous run, for status/result/cancel.",
            },
            "timeout": {
                "type": "INTEGER",
                "description": "Maximum seconds before the task is stopped (default 3600).",
            },
            "auto": {
                "type": "BOOLEAN",
                "description": "Let opencode approve its own edits. Default true.",
            },
        },
        "required": [],
    },
    "handler": opencode_agent,
}
