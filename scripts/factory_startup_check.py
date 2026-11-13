"""Confirm the local product entrypoint without opening a port."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_stage() -> dict:
    env = os.environ.copy()
    source = str(ROOT / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-m", "entity_matcher"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "entity_matcher stage failed")
    payload = json.loads(result.stdout)
    if payload.get("application") != "entity-matcher":
        raise RuntimeError("unexpected application stage")
    if payload.get("provisional_rulings_available") is not True:
        raise RuntimeError("provisional review is not available")
    if payload.get("candidate_inspection_available") is not True:
        raise RuntimeError("candidate inspection is not available")
    return payload


def main() -> int:
    run_stage()
    print("FACTORY_STARTUP_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
