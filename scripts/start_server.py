#!/usr/bin/env python3
"""Start the PCB2CNC local server and open it in a browser."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
REQUIREMENTS = BACKEND / "requirements.txt"
VENV = ROOT / ".venv"
HOST = "127.0.0.1"
BIND = "0.0.0.0"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def health_ok() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/health", timeout=1.5) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((HOST, PORT)) == 0


def ensure_venv() -> Path:
    python = venv_python()
    if not python.exists():
        print("Creating a local Python environment (.venv)…")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
        python = venv_python()
    probe = subprocess.run(
        [str(python), "-c", "import uvicorn, fastapi"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        print("Installing packages (first run can take a minute)…")
        subprocess.check_call(
            [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        )
        subprocess.check_call(
            [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        )
    return python


def open_when_ready() -> None:
    for _ in range(60):
        if health_ok():
            webbrowser.open(URL)
            return
        time.sleep(0.25)


def main() -> int:
    if not (BACKEND / "app" / "main.py").exists():
        print(f"Cannot find the app at {BACKEND / 'app' / 'main.py'}")
        return 1

    if port_in_use():
        if health_ok():
            print(f"PCB2CNC is already running at {URL}")
            webbrowser.open(URL)
            return 0
        print(f"Port {PORT} is already in use by another program.")
        print("Close that program, or stop the other PCB2CNC window, then try again.")
        return 1

    try:
        python = ensure_venv()
    except subprocess.CalledProcessError as exc:
        print("Failed to set up Python packages.")
        print(exc)
        return 1

    print(f"Starting PCB2CNC at {URL}")
    print("Leave this window open while you use the app. Press Ctrl+C to stop.")
    threading.Thread(target=open_when_ready, daemon=True).start()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    try:
        return subprocess.call(
            [
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                BIND,
                "--port",
                str(PORT),
            ],
            cwd=str(BACKEND),
            env=env,
        )
    except KeyboardInterrupt:
        print("\nServer stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
