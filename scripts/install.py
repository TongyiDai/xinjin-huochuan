#!/usr/bin/env python3
"""Install, upgrade, or uninstall Agent Relay for local AI agents."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "agent-relay"
LEGACY_SKILL_NAME = "agent-memory-hub"
DEFAULT_TARGETS = {
    "codex": Path("~/.codex/skills").expanduser(),
    "trae": Path("~/.trae/skills").expanduser(),
    "claude": Path("~/.claude/skills").expanduser(),
    "cursor": Path("~/.cursor/skills").expanduser(),
}
HOOK_CONFIGS = {
    "codex": Path("~/.codex/hooks.json").expanduser(),
    "trae": Path("~/.trae/hooks.json").expanduser(),
    "claude": Path("~/.claude/settings.json").expanduser(),
    "cursor": Path("~/.cursor/hooks.json").expanduser(),
}
AGENT_COMMANDS = {
    "codex": ["/Applications/ChatGPT.app/Contents/Resources/codex"],
    "trae": [str(Path("~/.local/bin/traex").expanduser())],
    "claude": [str(Path("~/.local/bin/claude").expanduser())],
}
NATIVE_MEMORY_PATHS = {
    "codex": Path("~/.codex/memories").expanduser(),
    "trae": Path("~/.trae/cli/memories").expanduser(),
    "claude": Path("~/.claude").expanduser(),
    "cursor": Path("~/.cursor").expanduser(),
}
MCP_CONFIGS = {
    "codex": [Path("~/.codex/config.toml").expanduser()],
    "trae": [
        Path("~/.trae/cli/config.toml").expanduser(),
        Path("~/.trae/traecli.toml").expanduser(),
    ],
    "claude": [Path("~/.claude.json").expanduser()],
    "cursor": [Path("~/.cursor/mcp.json").expanduser()],
}
LEGACY_DATA_HOME = Path("~/.agent-memory-hub").expanduser()
DATA_HOME = Path("~/.agent-relay").expanduser()


def install_link(source: Path, target: Path, force: bool) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() and target.resolve() == source:
        return "already-installed"
    if target.exists() or target.is_symlink():
        if not force:
            raise RuntimeError(f"Target exists: {target}")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.symlink_to(source, target_is_directory=True)
    return "installed"


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.agent-relay.{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def migrate_data_home() -> str:
    if DATA_HOME.exists():
        if LEGACY_DATA_HOME.is_symlink() and LEGACY_DATA_HOME.resolve() == DATA_HOME:
            return "already-migrated"
        if LEGACY_DATA_HOME.exists() and not LEGACY_DATA_HOME.is_symlink():
            return "both-exist"
        if not LEGACY_DATA_HOME.exists():
            LEGACY_DATA_HOME.symlink_to(DATA_HOME, target_is_directory=True)
        return "already-migrated"
    if LEGACY_DATA_HOME.is_symlink():
        raise RuntimeError(
            f"Legacy data link target is missing: {LEGACY_DATA_HOME}"
        )
    if LEGACY_DATA_HOME.exists():
        LEGACY_DATA_HOME.rename(DATA_HOME)
        LEGACY_DATA_HOME.symlink_to(DATA_HOME, target_is_directory=True)
        return "migrated"
    DATA_HOME.mkdir(parents=True, exist_ok=True)
    LEGACY_DATA_HOME.symlink_to(DATA_HOME, target_is_directory=True)
    return "initialized"


def initialize_runtime(agents: list[str]) -> None:
    cli = SKILL_ROOT / "scripts" / "agent_relay.py"
    subprocess.run(
        [sys.executable, str(cli), "--hub", str(DATA_HOME), "init"],
        text=True,
        capture_output=True,
        check=True,
    )
    for agent in agents:
        subprocess.run(
            [
                sys.executable,
                str(cli),
                "--hub",
                str(DATA_HOME),
                "agent",
                "add",
                agent,
                "--kind",
                agent,
                "--native-memory",
                str(NATIVE_MEMORY_PATHS[agent]),
            ],
            text=True,
            capture_output=True,
            check=True,
        )


def hook_entry(agent: str) -> dict:
    script = SKILL_ROOT / "scripts" / "session_start.py"
    return {
        "hooks": [
            {
                "type": "command",
                "command": f"{sys.executable} {script} --agent {agent}",
                "timeout": 10,
            }
        ]
    }


def install_json_hook(agent: str, force: bool) -> str:
    path = HOOK_CONFIGS[agent]
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {}
    if agent in {"trae", "cursor"}:
        data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    event_name = "sessionStart" if agent == "cursor" else "SessionStart"
    entries = hooks.setdefault(event_name, [])
    marker = str(SKILL_ROOT / "scripts" / "session_start.py")
    if any(marker in json.dumps(entry) for entry in entries):
        return "already-installed"
    backup_file(path)
    if agent == "cursor":
        entries.append(
            {
                "command": (
                    f"{sys.executable} {SKILL_ROOT / 'scripts' / 'session_start.py'} "
                    f"--agent cursor"
                )
            }
        )
    else:
        entries.append(hook_entry(agent))
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)
    return "installed"


def remove_json_hook(agent: str) -> str:
    path = HOOK_CONFIGS[agent]
    if not path.exists():
        return "not-installed"
    data = json.loads(path.read_text())
    event_name = "sessionStart" if agent == "cursor" else "SessionStart"
    entries = data.get("hooks", {}).get(event_name, [])
    marker = str(SKILL_ROOT / "scripts" / "session_start.py")
    filtered = [entry for entry in entries if marker not in json.dumps(entry)]
    if len(filtered) == len(entries):
        return "not-installed"
    backup_file(path)
    data["hooks"][event_name] = filtered
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)
    return "removed"


def mcp_configured(agent: str) -> bool:
    if agent == "cursor":
        path = MCP_CONFIGS["cursor"][0]
        if not path.exists():
            return False
        data = json.loads(path.read_text())
        return "agent-relay" in data.get("mcpServers", {})
    command = AGENT_COMMANDS.get(agent)
    if not command or not Path(command[0]).exists():
        return False
    result = subprocess.run(
        [*command, "mcp", "list"],
        text=True,
        capture_output=True,
        env=agent_environment(agent),
    )
    return "agent-relay" in (result.stdout + result.stderr)


def install_mcp(agent: str) -> str:
    if agent == "cursor":
        if mcp_configured(agent):
            return "already-installed"
        path = MCP_CONFIGS["cursor"][0]
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text()) if path.exists() else {}
        backup_file(path)
        data.setdefault("mcpServers", {})["agent-relay"] = {
            "command": sys.executable,
            "args": [str(SKILL_ROOT / "scripts" / "agent_relay_mcp.py")],
            "env": {"AGENT_RELAY_HOME": str(DATA_HOME)},
        }
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, path)
        return "installed"
    command = AGENT_COMMANDS.get(agent)
    if not command or not Path(command[0]).exists():
        return "unsupported"
    if mcp_configured(agent):
        return "already-installed"
    for config in MCP_CONFIGS.get(agent, []):
        backup_file(config)
    server = SKILL_ROOT / "scripts" / "agent_relay_mcp.py"
    env_pair = f"AGENT_RELAY_HOME={Path('~/.agent-relay').expanduser()}"
    if agent == "claude":
        args = [
            *command,
            "mcp",
            "add",
            "-s",
            "user",
            "agent-relay",
            "--",
            "env",
            env_pair,
            sys.executable,
            str(server),
        ]
    else:
        args = [
            *command,
            "mcp",
            "add",
            "--env",
            env_pair,
            "agent-relay",
            "--",
            sys.executable,
            str(server),
        ]
    result = subprocess.run(
        args, text=True, capture_output=True, env=agent_environment(agent)
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return "installed"


def remove_mcp(agent: str) -> str:
    if agent == "cursor":
        path = MCP_CONFIGS["cursor"][0]
        if not path.exists():
            return "not-installed"
        data = json.loads(path.read_text())
        servers = data.get("mcpServers", {})
        if "agent-relay" not in servers:
            return "not-installed"
        backup_file(path)
        del servers["agent-relay"]
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, path)
        return "removed"
    command = AGENT_COMMANDS.get(agent)
    if not command or not Path(command[0]).exists():
        return "unsupported"
    if not mcp_configured(agent):
        return "not-installed"
    args = [*command, "mcp", "remove", "agent-relay"]
    if agent == "claude":
        args = [*command, "mcp", "remove", "-s", "user", "agent-relay"]
    result = subprocess.run(
        args, text=True, capture_output=True, env=agent_environment(agent)
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return "removed"


def remove_link(path: Path, source: Path | None = None) -> str:
    if not path.is_symlink():
        return "not-installed"
    if source is not None and path.resolve() != source:
        return "different-target"
    path.unlink()
    return "removed"


def agent_environment(agent: str) -> dict[str, str]:
    environment = dict(os.environ)
    if agent == "codex":
        environment["CODEX_HOME"] = str(Path("~/.codex").expanduser())
    elif agent == "trae":
        environment["CODEX_HOME"] = str(Path("~/.trae/cli").expanduser())
    return environment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--agents",
        default="codex,trae,claude",
        help="Comma-separated agents: codex,trae,claude,cursor",
    )
    parser.add_argument("--command-dir", default="~/.local/bin")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-command", action="store_true")
    parser.add_argument("--with-mcp", action="store_true")
    parser.add_argument("--with-hooks", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument(
        "--compat",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep legacy agent-memory-hub and memory-hub aliases",
    )
    args = parser.parse_args()

    agents = [item.strip() for item in args.agents.split(",") if item.strip()]
    unsupported = sorted(set(agents) - set(DEFAULT_TARGETS))
    if unsupported:
        parser.error(f"Unsupported agents: {', '.join(unsupported)}")

    if not args.uninstall:
        print(f"data: {migrate_data_home()}: {DATA_HOME}")
        initialize_runtime(agents)
        print(f"runtime: initialized: {DATA_HOME}")

    for agent in agents:
        target = DEFAULT_TARGETS[agent] / SKILL_NAME
        legacy = DEFAULT_TARGETS[agent] / LEGACY_SKILL_NAME
        if args.uninstall:
            print(f"{agent} skill: {remove_link(target, SKILL_ROOT)}")
            print(f"{agent} legacy skill: {remove_link(legacy, SKILL_ROOT)}")
            if args.with_hooks and agent in HOOK_CONFIGS:
                print(f"{agent} hook: {remove_json_hook(agent)}")
            if args.with_mcp:
                print(f"{agent} mcp: {remove_mcp(agent)}")
            continue
        status = install_link(SKILL_ROOT, target, args.force)
        print(f"{agent} skill: {status}: {target} -> {SKILL_ROOT}")
        if args.compat:
            legacy_status = install_link(SKILL_ROOT, legacy, args.force)
            print(
                f"{agent} legacy skill: {legacy_status}: {legacy} -> {SKILL_ROOT}"
            )
        if args.with_hooks and agent in HOOK_CONFIGS:
            print(f"{agent} hook: {install_json_hook(agent, args.force)}")
        if args.with_mcp:
            print(f"{agent} mcp: {install_mcp(agent)}")

    if not args.no_command:
        command_dir = Path(args.command_dir).expanduser()
        command_dir.mkdir(parents=True, exist_ok=True)
        command = command_dir / "agent-relay"
        legacy_command = command_dir / "memory-hub"
        script = SKILL_ROOT / "scripts" / "agent_relay.py"
        mode = script.stat().st_mode
        script.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if args.uninstall:
            print(f"command: {remove_link(command, script)}")
            print(f"legacy command: {remove_link(legacy_command)}")
        else:
            status = install_link(script, command, args.force)
            print(f"command: {status}: {command} -> {script}")
            if args.compat:
                legacy_script = SKILL_ROOT / "scripts" / "memory_hub.py"
                legacy_status = install_link(
                    legacy_script, legacy_command, args.force
                )
                print(
                    f"legacy command: {legacy_status}: "
                    f"{legacy_command} -> {legacy_script}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
