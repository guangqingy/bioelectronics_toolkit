from __future__ import annotations

import os
import subprocess
import sys
import time

from services import scripts_panel


def test_shutdown_running_scripts_terminates_registered_process() -> None:
    popen_kwargs = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        **popen_kwargs,
    )
    scripts_panel._register_script_process("test-shutdown", proc)
    try:
        result = scripts_panel.shutdown_running_scripts(grace_seconds=0.1)
        deadline = time.time() + 2
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)

        assert result["process_count"] >= 1
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1)
        scripts_panel._forget_script_process("test-shutdown")
