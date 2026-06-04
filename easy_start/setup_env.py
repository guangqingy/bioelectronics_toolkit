#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
PYTHON_IN_VENV = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def log(message: str = "") -> None:
    print(message, flush=True)


def run(cmd: list[str], *, label: str) -> None:
    log(f"\n==> {label}")
    log("    " + " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)


def supported_python() -> bool:
    version = sys.version_info
    return (3, 10) <= (version.major, version.minor) <= (3, 12)


def explain_python_problem() -> None:
    log("\nThis installer needs Python 3.10, 3.11, or 3.12.")
    log(f"Current Python is: {sys.version.split()[0]}")
    log("\nPlease install Python 3.12 from https://www.python.org/downloads/")
    log("Then double-click the install script again.")


def create_venv() -> None:
    if PYTHON_IN_VENV.exists():
        log(f"Using existing environment: {VENV}")
        return
    run([sys.executable, "-m", "venv", str(VENV)], label="Create local Python environment")


def install_package(*, dev: bool, locked: bool) -> None:
    python = str(PYTHON_IN_VENV)
    run([python, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], label="Update installer tools")
    cmd = [python, "-m", "pip", "install"]
    lock = ROOT / "requirements-lock.txt"
    if locked and lock.exists():
        cmd.extend(["-c", str(lock)])
    cmd.extend(["-e", ".[dev]" if dev else "."])
    run(cmd, label="Install DataProcess and dependencies")


def self_check() -> None:
    run(
        [str(PYTHON_IN_VENV), "web_app.py", "--self-check", "--no-browser"],
        label="Check bundled examples and runtime dependencies",
    )


def launch() -> None:
    log("\nDataProcess Web will open at http://127.0.0.1:7433")
    log("Leave this window open while using the app. Press Ctrl+C here to stop it.\n")
    subprocess.check_call([str(PYTHON_IN_VENV), "web_app.py"], cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up and optionally launch DataProcess Web.")
    parser.add_argument("--run", action="store_true", help="Launch the Web app after setup.")
    parser.add_argument("--dev", action="store_true", help="Install developer/test extras.")
    parser.add_argument(
        "--locked",
        action="store_true",
        help="Use requirements-lock.txt constraints when installing dependencies.",
    )
    parser.add_argument(
        "--skip-check",
        action="store_true",
        help="Skip the post-install self-check.",
    )
    args = parser.parse_args()

    log("DataProcess Web one-click setup")
    log(f"Project folder: {ROOT}")
    log(f"Python: {sys.executable} ({sys.version.split()[0]})")

    if not supported_python():
        explain_python_problem()
        return 2

    try:
        create_venv()
        install_package(dev=args.dev, locked=args.locked)
        if not args.skip_check:
            self_check()
        log("\nSetup finished.")
        if args.run:
            launch()
    except subprocess.CalledProcessError as exc:
        log("\nSetup did not finish successfully.")
        log(f"Failed command exit code: {exc.returncode}")
        return int(exc.returncode or 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
