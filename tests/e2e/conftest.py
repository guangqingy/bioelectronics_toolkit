from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("DP_E2E_BASE_URL", "http://127.0.0.1:7433")
ARTIFACT_DIR = ROOT / "test-results" / "e2e"


def _url_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.5) as response:
            return response.status < 500
    except (OSError, URLError):
        return False


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def server_url() -> str:
    proc = None
    if _url_ready(BASE_URL):
        yield BASE_URL
        return

    try:
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        proc = subprocess.Popen(
            [
                sys.executable,
                "web_app.py",
                "--host",
                "127.0.0.1",
                "--port",
                "7433",
                "--no-browser",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"web_app.py exited before it was ready:\n{output}")
            if _url_ready(BASE_URL):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(f"Timed out waiting for {BASE_URL}")

        yield BASE_URL
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser, request):
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    yield page
    artifact_base = ARTIFACT_DIR / safe_name
    page.screenshot(path=f"{artifact_base}.png", full_page=True)
    context.tracing.stop(path=f"{artifact_base}.zip")
    context.close()
