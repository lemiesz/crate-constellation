"""What code is actually running.

Python imports a module once per process, so a long-lived server serves
whatever the source said at startup. Editing the sync engine and clicking
sync then silently produces a stick from the OLD writer. That happened three
times during the artwork investigation and each time cost a trip to the
hardware, because nothing on the stick said which build had written it.

Two defences. `run.sh` always passes --reload so the server restarts on any
engine edit. And every sync stamps this build id into its log and into the
manifest on the stick, so a device can always be traced back to the code
that wrote it.
"""

import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STARTED = time.time()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args],
                              capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:                                # noqa: BLE001
        return ""


def newest_engine_mtime() -> float:
    return max((f.stat().st_mtime for f in (ROOT / "engine").glob("*.py")),
               default=0.0)


def build_info() -> dict:
    commit = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    newest = newest_engine_mtime()
    return {
        "commit": commit or "unknown",
        "dirty": dirty,
        "server_started": STARTED,
        "newest_engine_edit": newest,
        # True when the process was started before the newest engine edit and
        # is therefore serving code older than what is on disk.
        "stale": newest > STARTED,
    }


def build_id() -> str:
    b = build_info()
    return f"{b['commit']}{'+dirty' if b['dirty'] else ''}"
