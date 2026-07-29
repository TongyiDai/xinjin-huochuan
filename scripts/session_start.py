#!/usr/bin/env python3
"""Inject a bounded, read-only Agent Relay context pack at session start."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_relay import DEFAULT_HUB, HubError, build_context_pack, resolve_project, validate_hub


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--hub", type=Path, default=DEFAULT_HUB)
    parser.add_argument("--max-chars", type=int, default=8000)
    args = parser.parse_args()

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    cwd = payload.get("cwd") or payload.get("workspace") or os.getcwd()
    hub = args.hub.expanduser().resolve(strict=False)
    try:
        validate_hub(hub)
        project = resolve_project(hub, cwd)
        pack = build_context_pack(hub, project, args.agent, args.max_chars)
    except HubError:
        return 0

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": pack["context"],
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
