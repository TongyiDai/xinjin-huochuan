#!/usr/bin/env python3
"""Compatibility entrypoint for Agent Relay."""
from agent_relay import *  # noqa: F401,F403
from agent_relay import main

if __name__ == "__main__":
    raise SystemExit(main())
