"""Run the editor's interior design solver (solver.mjs, bundled from app/src/lib/constructos) in Node.

The editor and the agent use the same code: the agent proposes a design brief, this checks and
places it exactly as the editor will when the user accepts it.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

SOLVER = Path(__file__).with_name("solver.mjs")


class SolverError(Exception):
    pass


def node() -> str:
    found = os.environ.get("CONSTRUCTOS_NODE") or shutil.which("node")
    if not found:
        raise SolverError("Node.js is not available to run the interior solver.")
    return found


def run(payload: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    try:
        done = subprocess.run([node(), str(SOLVER)], input=json.dumps(payload), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise SolverError("The interior solver took too long.") from e
    try:
        out = json.loads(done.stdout or "{}")
    except json.JSONDecodeError as e:
        raise SolverError(f"The interior solver failed: {(done.stderr or '').strip()[:200]}") from e
    if "error" in out:
        raise SolverError(f"The interior solver failed: {out['error']}")
    return out


def facts(floor: dict, room_ids: list[str] | None = None) -> list[dict]:
    return run({"cmd": "facts", "floor": floor, "room_ids": room_ids})["rooms"]


def catalog() -> dict:
    return run({"cmd": "catalog"})


def plan(floor: dict, brief: dict, room_ids: list[str] | None = None, furnish: bool = True, home_has_pooja: bool = False) -> dict:
    return run({"cmd": "plan", "floor": floor, "brief": brief, "room_ids": room_ids, "furnish": furnish, "home_has_pooja": home_has_pooja})
