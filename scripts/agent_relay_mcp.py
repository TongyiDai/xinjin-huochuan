#!/usr/bin/env python3
"""Zero-dependency stdio MCP server for Agent Relay."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).with_name("agent_relay.py")
DEFAULT_HUB = os.environ.get(
    "AGENT_RELAY_HOME",
    os.environ.get("AGENT_MEMORY_HUB_HOME", "~/.agent-relay"),
)
PROTOCOL_VERSION = "2024-11-05"


TOOLS: list[dict[str, Any]] = [
    {
        "name": "relay_bootstrap",
        "description": "Initialize Agent Relay, register the current agent, and add a project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "agent": {"type": "string"},
                "project_name": {"type": "string"},
                "goal": {"type": "string"},
                "agent_kind": {"type": "string"},
                "native_memory": {"type": "string"},
            },
            "required": ["project", "agent"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "relay_context",
        "description": "Read a bounded Agent Relay context pack for a project and agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "agent": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 500, "default": 8000},
            },
            "required": ["project", "agent"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "relay_inbox",
        "description": "List relay work awaiting, owned by, or awaiting verification from an agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string"},
                "project": {"type": "string"},
                "include_terminal": {"type": "boolean", "default": False},
            },
            "required": ["agent"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "relay_show",
        "description": "Read one relay, its lifecycle state, evidence, and complete event history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "relay_id": {"type": "string"},
                "project": {"type": "string"},
            },
            "required": ["relay_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "relay_search",
        "description": "Search Agent Relay events across one project or the whole hub.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "relay_offer",
        "description": "Offer a verifiable unit of work from one agent to another.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "from_agent": {"type": "string"},
                "to_agent": {"type": "string"},
                "summary": {"type": "string"},
                "acceptance": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "next_steps": {"type": "array", "items": {"type": "string"}},
                "artifacts": {"type": "array", "items": {"type": "string"}},
                "source_refs": {"type": "array", "items": {"type": "string"}},
                "priority": {
                    "type": "string",
                    "enum": ["urgent", "high", "normal", "low"],
                    "default": "normal",
                },
                "expires_in": {"type": "integer", "minimum": 1, "default": 604800},
                "lease_seconds": {"type": "integer", "minimum": 1, "default": 3600},
                "depends_on": {"type": "array", "items": {"type": "string"}},
                "parent_relay_id": {"type": "string"},
                "verifier": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["project", "from_agent", "to_agent", "summary", "acceptance"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
    {
        "name": "relay_heartbeat",
        "description": "Renew the execution lease for accepted relay work.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "relay_id": {"type": "string"},
                "agent": {"type": "string"},
                "summary": {"type": "string", "default": "Relay work is still active"},
                "lease_seconds": {"type": "integer", "minimum": 1},
                "idempotency_key": {"type": "string"},
            },
            "required": ["relay_id", "agent"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
    {
        "name": "relay_reject",
        "description": "Reject an offered relay with a durable reason.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "relay_id": {"type": "string"},
                "agent": {"type": "string"},
                "summary": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["relay_id", "agent", "summary"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
    {
        "name": "relay_accept",
        "description": "Accept an offered relay after verifying its input artifacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "relay_id": {"type": "string"},
                "agent": {"type": "string"},
                "summary": {"type": "string"},
                "allow_changed_artifacts": {"type": "boolean", "default": False},
                "idempotency_key": {"type": "string"},
            },
            "required": ["relay_id", "agent", "summary"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
    {
        "name": "relay_fail",
        "description": "Mark accepted relay work failed with a durable reason.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "relay_id": {"type": "string"},
                "agent": {"type": "string"},
                "summary": {"type": "string"},
                "source_refs": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": {"type": "string"},
            },
            "required": ["relay_id", "agent", "summary"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
    {
        "name": "relay_cancel",
        "description": "Cancel an offered or accepted relay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "relay_id": {"type": "string"},
                "agent": {"type": "string"},
                "summary": {"type": "string"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["relay_id", "agent", "summary"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    },
    {
        "name": "relay_expire",
        "description": "Close relay offers or accepted leases whose expiry has passed.",
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "relay_doctor",
        "description": "Validate event, index, state machine, evidence, and security integrity.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "relay_complete",
        "description": "Complete accepted relay work with artifact or source evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "relay_id": {"type": "string"},
                "agent": {"type": "string"},
                "summary": {"type": "string"},
                "artifacts": {"type": "array", "items": {"type": "string"}},
                "source_refs": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": {"type": "string"},
            },
            "required": ["relay_id", "agent", "summary"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
    {
        "name": "relay_verify",
        "description": "Verify completed relay artifacts and close the relay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "relay_id": {"type": "string"},
                "agent": {"type": "string"},
                "summary": {"type": "string"},
                "criteria_met": {"type": "array", "items": {"type": "string"}},
                "accept_all_criteria": {"type": "boolean", "default": False},
                "allow_changed_artifacts": {"type": "boolean", "default": False},
                "idempotency_key": {"type": "string"},
            },
            "required": ["relay_id", "agent", "summary"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
    {
        "name": "relay_capture",
        "description": "Append a durable project fact, decision, blocker, or artifact outside a relay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "agent": {"type": "string"},
                "summary": {"type": "string"},
                "event_type": {
                    "type": "string",
                    "enum": ["progress", "decision", "artifact", "blocker", "note"],
                    "default": "progress",
                },
                "status": {
                    "type": "string",
                    "enum": ["unknown", "planned", "in_progress", "blocked", "completed", "paused"],
                },
                "decisions": {"type": "array", "items": {"type": "string"}},
                "next_steps": {"type": "array", "items": {"type": "string"}},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "artifacts": {"type": "array", "items": {"type": "string"}},
                "source_refs": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": {"type": "string"},
            },
            "required": ["project", "agent", "summary"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
]


def add_repeat(args: list[str], flag: str, values: list[str] | None) -> None:
    for value in values or []:
        args.extend([flag, value])


def cli_args(tool: str, values: dict[str, Any]) -> list[str]:
    args = [sys.executable, str(SCRIPT), "--hub", str(Path(DEFAULT_HUB).expanduser())]
    if tool == "relay_bootstrap":
        result = [
            *args,
            "bootstrap",
            "--project",
            values["project"],
            "--agent",
            values["agent"],
        ]
        if values.get("project_name"):
            result.extend(["--name", values["project_name"]])
        if values.get("goal"):
            result.extend(["--goal", values["goal"]])
        if values.get("agent_kind"):
            result.extend(["--agent-kind", values["agent_kind"]])
        if values.get("native_memory"):
            result.extend(["--native-memory", values["native_memory"]])
        return result
    if tool == "relay_context":
        return [
            *args,
            "context",
            "--project",
            values["project"],
            "--agent",
            values["agent"],
            "--max-chars",
            str(values.get("max_chars", 8000)),
            "--json",
        ]
    if tool == "relay_inbox":
        result = [*args, "inbox", "--agent", values["agent"], "--json"]
        if values.get("project"):
            result.extend(["--project", values["project"]])
        if values.get("include_terminal"):
            result.append("--all")
        return result
    if tool == "relay_show":
        result = [*args, "show", "--relay", values["relay_id"], "--json"]
        if values.get("project"):
            result.extend(["--project", values["project"]])
        return result
    if tool == "relay_search":
        result = [
            *args,
            "search",
            values["query"],
            "--limit",
            str(values.get("limit", 20)),
            "--json",
        ]
        if values.get("project"):
            result.extend(["--project", values["project"]])
        return result
    if tool == "relay_offer":
        result = [
            *args,
            "offer",
            "--project",
            values["project"],
            "--from-agent",
            values["from_agent"],
            "--to-agent",
            values["to_agent"],
            "--summary",
            values["summary"],
            "--priority",
            values.get("priority", "normal"),
            "--expires-in",
            str(values.get("expires_in", 604800)),
            "--lease-seconds",
            str(values.get("lease_seconds", 3600)),
        ]
        add_repeat(result, "--acceptance", values.get("acceptance"))
        add_repeat(result, "--next-step", values.get("next_steps"))
        add_repeat(result, "--artifact", values.get("artifacts"))
        add_repeat(result, "--source-ref", values.get("source_refs"))
        add_repeat(result, "--depends-on", values.get("depends_on"))
        if values.get("parent_relay_id"):
            result.extend(["--parent-relay", values["parent_relay_id"]])
        if values.get("verifier"):
            result.extend(["--verifier", values["verifier"]])
    elif tool == "relay_accept":
        result = [
            *args,
            "accept",
            "--relay",
            values["relay_id"],
            "--agent",
            values["agent"],
            "--summary",
            values["summary"],
        ]
        if values.get("allow_changed_artifacts"):
            result.append("--allow-changed-artifacts")
    elif tool == "relay_heartbeat":
        result = [
            *args,
            "heartbeat",
            "--relay",
            values["relay_id"],
            "--agent",
            values["agent"],
            "--summary",
            values.get("summary", "Relay work is still active"),
        ]
        if values.get("lease_seconds"):
            result.extend(["--lease-seconds", str(values["lease_seconds"])])
    elif tool == "relay_complete":
        result = [
            *args,
            "complete",
            "--relay",
            values["relay_id"],
            "--agent",
            values["agent"],
            "--summary",
            values["summary"],
        ]
        add_repeat(result, "--artifact", values.get("artifacts"))
        add_repeat(result, "--source-ref", values.get("source_refs"))
    elif tool == "relay_verify":
        result = [
            *args,
            "verify",
            "--relay",
            values["relay_id"],
            "--agent",
            values["agent"],
            "--summary",
            values["summary"],
        ]
        add_repeat(result, "--criterion-met", values.get("criteria_met"))
        if values.get("accept_all_criteria"):
            result.append("--accept-all-criteria")
        if values.get("allow_changed_artifacts"):
            result.append("--allow-changed-artifacts")
    elif tool in {"relay_reject", "relay_fail", "relay_cancel"}:
        command = {
            "relay_reject": "reject",
            "relay_fail": "fail",
            "relay_cancel": "cancel",
        }[tool]
        result = [
            *args,
            command,
            "--relay",
            values["relay_id"],
            "--agent",
            values["agent"],
            "--summary",
            values["summary"],
        ]
        add_repeat(result, "--source-ref", values.get("source_refs"))
    elif tool == "relay_expire":
        result = [*args, "expire", "--json"]
        if values.get("project"):
            result.extend(["--project", values["project"]])
    elif tool == "relay_doctor":
        return [*args, "doctor"]
    elif tool == "relay_capture":
        result = [
            *args,
            "capture",
            "--project",
            values["project"],
            "--agent",
            values["agent"],
            "--summary",
            values["summary"],
            "--type",
            values.get("event_type", "progress"),
        ]
        if values.get("status"):
            result.extend(["--status", values["status"]])
        add_repeat(result, "--decision", values.get("decisions"))
        add_repeat(result, "--next-step", values.get("next_steps"))
        add_repeat(result, "--blocker", values.get("blockers"))
        add_repeat(result, "--artifact", values.get("artifacts"))
        add_repeat(result, "--source-ref", values.get("source_refs"))
    else:
        raise ValueError(f"Unknown tool: {tool}")
    if values.get("idempotency_key"):
        result.extend(["--idempotency-key", values["idempotency_key"]])
    return result


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in {tool["name"] for tool in TOOLS}:
        raise ValueError(f"Unknown tool: {name}")
    process = subprocess.run(
        cli_args(name, arguments),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    output = process.stdout.strip() or process.stderr.strip()
    if process.returncode:
        return {
            "content": [{"type": "text", "text": output}],
            "isError": True,
        }
    return {
        "content": [{"type": "text", "text": output}],
        "isError": False,
    }


def respond(identifier: Any, result: Any = None, error: dict[str, Any] | None = None) -> None:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": identifier}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    identifier = message.get("id")
    if identifier is None:
        return
    try:
        if method == "initialize":
            requested = message.get("params", {}).get("protocolVersion")
            respond(
                identifier,
                {
                    "protocolVersion": requested or PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "agent-relay", "version": "2.0.0"},
                },
            )
        elif method == "ping":
            respond(identifier, {})
        elif method == "tools/list":
            respond(identifier, {"tools": TOOLS})
        elif method == "tools/call":
            params = message.get("params", {})
            respond(
                identifier,
                call_tool(params.get("name", ""), params.get("arguments", {})),
            )
        else:
            respond(
                identifier,
                error={"code": -32601, "message": f"Method not found: {method}"},
            )
    except Exception as exc:
        respond(
            identifier,
            error={"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
        )


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            respond(None, error={"code": -32700, "message": str(exc)})
            continue
        handle(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
