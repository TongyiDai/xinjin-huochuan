#!/usr/bin/env python3
"""Check the Agent Relay CLI without changing the user's relay state."""

from __future__ import annotations

import argparse
import json
import py_compile
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    cli = root / "scripts" / "agent_relay.py"
    compile_ok = True
    try:
        for path in root.glob("scripts/*.py"):
            py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError:
        compile_ok = False
    help_ok = subprocess.run(["python3", str(cli), "--help"], capture_output=True).returncode == 0
    result = {
        "ok": compile_ok and help_ok,
        "skill_root": str(root),
        "required": {"python3": shutil.which("python3") is not None, "cli": help_ok, "scripts_compile": compile_ok},
        "optional": {"mcp": (root / "scripts" / "agent_relay_mcp.py").exists(), "session_start_hook": (root / "scripts" / "session_start.py").exists()},
        "state": {"default_path": "~/.agent-relay", "checked": False, "reason": "doctor is read-only; pass --hub to the CLI doctor when a project context is known"},
        "next": "run agent_relay.py doctor --hub <approved-hub> after selecting the project" if help_ok else "repair the CLI before installation",
    }
    print(json.dumps(result, ensure_ascii=False) if args.json else "\n".join(f"{k}={v}" for k, v in result.items()))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
