"""
MARK LIII — one-time setup.

Installs the Python dependencies for THIS operating system only: the OS-specific
packages in requirements.txt carry `sys_platform` markers, so a macOS or Linux
user never pulls Windows-only libraries (and vice-versa). Then it fetches the
Playwright browsers needed for web automation (current-OS builds only).

The optional local wake word ("Hey Jarvis") is NOT installed here — it's a
one-click, opt-in download from ⚙ → WAKE WORD inside the app.
"""
import platform
import subprocess
import sys
from pathlib import Path

OS = platform.system()  # "Windows" | "Darwin" | "Linux"
ROOT = Path(__file__).resolve().parent


def _run(label: str, args: list[str]) -> None:
    print(f"\n▶ {label}")
    subprocess.run(args, check=True)


def _ensure_pip() -> None:
    """Bootstrap pip for this interpreter if `python -m pip` is missing.

    This is the exact failure you hit when the default `python3` is a
    source-built /usr/local/bin/python3.14 without pip, while pip only
    exists for the distro's /usr/bin/python3.12.
    """
    probe = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        capture_output=True, text=True,
    )
    if probe.returncode == 0:
        return
    print("\n⚠️  pip not found for this interpreter — bootstrapping via ensurepip…")
    print(f"   interpreter: {sys.executable}")
    try:
        subprocess.run(
            [sys.executable, "-m", "ensurepip", "--upgrade"],
            check=True,
        )
    except subprocess.CalledProcessError:
        sys.exit(
            "\n❌ Could not bootstrap pip for:\n"
            f"   {sys.executable}\n\n"
            "   You have two Pythons on this machine:\n"
            "     • /usr/local/bin/python3 → 3.14 (no pip)\n"
            "     • /usr/bin/python3       → 3.12 (has pip)\n\n"
            "   Fix — use the distro Python with a venv:\n"
            "     /usr/bin/python3 -m venv .venv\n"
            "     .venv/bin/python -m pip install -r requirements.txt\n"
            "     .venv/bin/python main.py\n"
        )
    # Verify the bootstrap actually worked.
    probe = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        sys.exit(
            "\n❌ ensurepip ran but `python -m pip` still fails.\n"
            "   Use the distro Python instead:\n"
            "     /usr/bin/python3 -m venv .venv\n"
            "     .venv/bin/python setup.py"
        )


def _in_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _pip_install_requirements() -> None:
    """Install requirements, auto-falling back to a .venv on PEP 668 systems."""
    try:
        _run("Installing Python dependencies (OS-specific extras auto-filtered)…",
             [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    except subprocess.CalledProcessError as exc:
        # PEP 668 (Debian/Ubuntu/Mint): system pip refuses to install.
        # Fall back to a project-local venv instead of --break-system-packages.
        venv_py = ROOT / ".venv" / "bin" / "python"
        if not _in_venv():
            print(
                "\n⚠️  System Python refused a global install "
                "(externally-managed-environment, PEP 668)."
                "\n▶ Creating project venv at .venv and retrying inside it…"
            )
            subprocess.run([sys.executable, "-m", "venv", ROOT / ".venv"], check=True)
            _run("Installing Python dependencies inside .venv…",
                 [str(venv_py), "-m", "pip", "install", "--upgrade", "pip"])
            _run("Installing Python dependencies inside .venv…",
                 [str(venv_py), "-m", "pip", "install", "-r", "requirements.txt"])
            print(
                "\nℹ️  Installed into .venv. From now on use:\n"
                "     .venv/bin/python setup.py\n"
                "     .venv/bin/python main.py"
            )
            return
        raise exc


def main() -> None:
    print(f"⚙  MARK LIII setup — detected OS: {OS or 'unknown'}")
    print(f"   interpreter: {sys.executable}")
    if not _in_venv():
        print("   env: system python (will auto-use .venv if the OS blocks global installs)")

    _ensure_pip()
    # requirements.txt filters OS-specific extras by itself via pip markers.
    _pip_install_requirements()

    # Chromium covers Chrome/Edge/Opera/Brave/Vivaldi; Firefox for Firefox.
    # (Safari automation additionally needs: python -m playwright install webkit)
    _run("Installing Playwright browsers (chromium + firefox)…",
         [sys.executable, "-m", "playwright", "install", "chromium", "firefox"])

    # ── OS-specific post-install notes ────────────────────────────────────────
    if OS == "Windows":
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            postinstall = Path(sys.executable).parent / "Scripts" / "pywin32_postinstall.py"
            print(
                "\n⚠️  pywin32 did not register correctly — desktop-shortcut "
                "creation will use a slower fallback. To fix it, run:\n"
                f'    "{sys.executable}" -m pip install --force-reinstall pywin32\n'
                f'    "{sys.executable}" "{postinstall}" -install'
            )
    elif OS == "Linux":
        print(
            "\nℹ️  Linux note — a few voice-controlled OS actions shell out to "
            "native tools. Install the ones you'll use via your package manager:\n"
            "    • volume      → pulseaudio-utils   (pactl)\n"
            "    • brightness  → brightnessctl\n"
            "    • reminders   → systemd (systemd-run) or 'at'\n"
            "    • open URLs   → xdg-utils          (xdg-open)"
        )
    elif OS == "Darwin":
        print(
            "\nℹ️  macOS note — volume, brightness and reminders use the built-in "
            "'osascript' / LaunchAgents, so no extra tools are required.\n"
            "    For Safari automation only: python -m playwright install webkit"
        )

    print("\n✅ Setup complete!")
    launch = ".venv/bin/python main.py" if _in_venv() else "python main.py"
    print(f"   1) Launch it:  {launch}")
    print("   2) Paste your free Gemini API key when the setup screen appears.")
    print("   3) (Optional) Enable 'Hey Jarvis' from ⚙ → WAKE WORD.")


if __name__ == "__main__":
    main()
