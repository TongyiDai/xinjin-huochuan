#!/usr/bin/env python3
"""Agent Relay: append-only, verifiable work handoffs across AI agents."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, 2}
PRODUCT_NAME = "薪尽火传 · Agent Relay"
VERSION = "2.0.0"
LEGACY_HUB = Path("~/.agent-memory-hub").expanduser()
PREFERRED_HUB = Path("~/.agent-relay").expanduser()
DEFAULT_HUB = Path(
    os.environ.get(
        "AGENT_RELAY_HOME",
        os.environ.get(
            "AGENT_MEMORY_HUB_HOME",
            str(PREFERRED_HUB if PREFERRED_HUB.exists() or not LEGACY_HUB.exists() else LEGACY_HUB),
        ),
    )
).expanduser()
EVENT_TYPES = {
    "progress",
    "decision",
    "artifact",
    "blocker",
    "note",
    "handoff",
    "relay.created",
    "relay.accepted",
    "relay.heartbeat",
    "relay.rejected",
    "relay.cancelled",
    "relay.completed",
    "relay.failed",
    "relay.verified",
    "relay.expired",
}
RELAY_STATES = {
    "offered",
    "accepted",
    "rejected",
    "cancelled",
    "completed",
    "failed",
    "verified",
    "expired",
}
RELAY_TRANSITIONS = {
    "offered": {"accepted", "rejected", "cancelled", "expired"},
    "accepted": {"accepted", "completed", "failed", "cancelled", "expired"},
    "completed": {"verified"},
    "rejected": set(),
    "cancelled": set(),
    "failed": set(),
    "verified": set(),
    "expired": set(),
}
TERMINAL_RELAY_STATES = {
    "rejected",
    "cancelled",
    "failed",
    "verified",
    "expired",
}
DEFAULT_ARTIFACT_HASH_LIMIT = 64 * 1024 * 1024
SENSITIVE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|private[_-]?key|bearer)\b\s*[:=]?\s*[^\s,;]+"
)
PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
KNOWN_SECRET_PATTERN = re.compile(
    r"(?i)\b(sk-[a-z0-9_-]{16,}|gh[opsu]_[a-z0-9]{20,}|"
    r"github_pat_[a-z0-9_]{20,}|xox[baprs]-[a-z0-9-]{16,}|"
    r"akia[0-9a-z]{16})\b"
)


class HubError(RuntimeError):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HubError(f"Invalid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def future_iso(seconds: int) -> str:
    if seconds <= 0:
        raise HubError("Expiry duration must be greater than zero.")
    return (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
    ).replace(microsecond=0).isoformat()


def local_stamp() -> str:
    return dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def slugify(value: str, fallback: str = "item") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return normalized[:48] or fallback


def stable_project_id(path: str, name: str) -> str:
    normalized = str(Path(path).expanduser().resolve(strict=False))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"{slugify(name, 'project')}-{digest}"


def new_relay_id() -> str:
    return f"relay-{uuid.uuid4().hex[:12]}"


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HubError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HubError(f"Invalid JSON in {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_metadata(path: Path) -> dict[str, Any] | None:
    target = path if path.is_dir() else path.parent
    try:
        root = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", root, "branch", "--show-current"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        relative = str(path.resolve(strict=False).relative_to(Path(root)))
        status = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "--", relative],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        return None
    return {
        "repo_root": root,
        "head": head,
        "branch": branch,
        "relative_path": relative,
        "dirty": bool(status),
    }


def snapshot_artifact(value: str) -> dict[str, Any]:
    expanded = Path(value).expanduser()
    looks_like_path = (
        expanded.is_absolute()
        or value.startswith(("./", "../", "~/"))
        or "/" in value
    )
    if not looks_like_path:
        return {"reference": value, "kind": "reference", "verifiable": False}

    path = expanded.resolve(strict=False)
    snapshot: dict[str, Any] = {
        "reference": value,
        "path": str(path),
        "kind": "path",
        "exists": path.exists(),
        "verifiable": True,
    }
    if not path.exists():
        return snapshot
    stat_result = path.stat()
    snapshot.update(
        {
            "kind": "directory" if path.is_dir() else "file",
            "size": stat_result.st_size,
            "modified_ns": stat_result.st_mtime_ns,
        }
    )
    if path.is_file() and stat_result.st_size <= DEFAULT_ARTIFACT_HASH_LIMIT:
        snapshot["sha256"] = sha256_file(path)
    elif path.is_dir():
        files = sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
        total_size = sum(candidate.stat().st_size for candidate in files)
        content_mode = len(files) <= 5000 and total_size <= DEFAULT_ARTIFACT_HASH_LIMIT
        digest = hashlib.sha256()
        for candidate in files:
            relative = str(candidate.relative_to(path))
            stat_candidate = candidate.stat()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            if content_mode:
                digest.update(sha256_file(candidate).encode("ascii"))
            else:
                digest.update(
                    f"{stat_candidate.st_size}:{stat_candidate.st_mtime_ns}".encode(
                        "ascii"
                    )
                )
            digest.update(b"\n")
        snapshot["file_count"] = len(files)
        snapshot["total_size"] = total_size
        snapshot["tree_sha256"] = digest.hexdigest()
        snapshot["tree_digest_mode"] = "content" if content_mode else "metadata"
    metadata = git_metadata(path)
    if metadata:
        snapshot["git"] = metadata
    return snapshot


def snapshot_artifacts(values: list[str]) -> list[dict[str, Any]]:
    return [snapshot_artifact(value) for value in values]


def verify_artifact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not snapshot.get("verifiable"):
        return {
            "reference": snapshot.get("reference"),
            "result": "unverifiable-reference",
        }
    path = Path(snapshot["path"])
    if not path.exists():
        return {
            "reference": snapshot.get("reference"),
            "path": str(path),
            "result": "missing",
        }
    current = snapshot_artifact(str(path))
    comparable = [
        "kind",
        "size",
        "sha256",
        "tree_sha256",
        "file_count",
        "total_size",
    ]
    changed = {
        field: {"expected": snapshot.get(field), "actual": current.get(field)}
        for field in comparable
        if field in snapshot and snapshot.get(field) != current.get(field)
    }
    return {
        "reference": snapshot.get("reference"),
        "path": str(path),
        "result": "changed" if changed else "verified",
        "changes": changed,
        "current": current,
    }


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextlib.contextmanager
def exclusive_lock(hub: Path) -> Iterable[None]:
    lock_path = hub / "locks" / "hub.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def shared_lock(hub: Path) -> Iterable[None]:
    lock_path = hub / "locks" / "hub.lock"
    try:
        handle = lock_path.open("r", encoding="utf-8")
    except FileNotFoundError:
        yield
        return
    with handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def connect_index(hub: Path, readonly: bool = False) -> sqlite3.Connection:
    database_path = hub / "index.sqlite"
    if readonly:
        database = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    else:
        database = sqlite3.connect(database_path)
    database.row_factory = sqlite3.Row
    if not readonly:
        database.execute("PRAGMA journal_mode=DELETE")
    if not readonly:
        database.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT,
                summary TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        database.execute(
            "CREATE INDEX IF NOT EXISTS events_project_time "
            "ON events(project_id, timestamp DESC)"
        )
        try:
            database.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                    summary,
                    payload,
                    content='events',
                    content_rowid='rowid'
                )
                """
            )
        except sqlite3.OperationalError:
            pass
        database.commit()
    return database


def validate_hub(hub: Path) -> dict[str, Any]:
    manifest_path = hub / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        raise HubError(
            f"Unsupported schema version: {manifest.get('schema_version')}"
        )
    return manifest


def ensure_hub(hub: Path) -> None:
    hub.mkdir(parents=True, exist_ok=True)
    for relative in ("agents", "projects", "locks", "backups", "sources"):
        (hub / relative).mkdir(parents=True, exist_ok=True)
    manifest_path = hub / "manifest.json"
    if not manifest_path.exists():
        atomic_write_json(
            manifest_path,
            {
                "schema_version": SCHEMA_VERSION,
                "product": PRODUCT_NAME,
                "created_at": now_iso(),
                "principles": [
                    "append-only-events",
                    "provenance-required",
                    "no-secrets",
                    "native-agent-memory-remains-independent",
                    "explicit-relay-acknowledgement",
                    "artifact-verification",
                ],
            },
        )
    manifest = validate_hub(hub)
    manifest_changed = False
    if manifest.get("schema_version") == 1:
        manifest["schema_version"] = SCHEMA_VERSION
        manifest["product"] = PRODUCT_NAME
        manifest["upgraded_at"] = now_iso()
        manifest_changed = True
        principles = list(manifest.get("principles", []))
        for principle in (
            "explicit-relay-acknowledgement",
            "artifact-verification",
        ):
            if principle not in principles:
                principles.append(principle)
        manifest["principles"] = principles
    if manifest.get("product") != PRODUCT_NAME:
        manifest["product"] = PRODUCT_NAME
        manifest_changed = True
    if manifest.get("product_version") != VERSION:
        manifest["product_version"] = VERSION
        manifest_changed = True
    if manifest_changed:
        atomic_write_json(manifest_path, manifest)
    connect_index(hub).close()


def agent_path(hub: Path, agent_id: str) -> Path:
    return hub / "agents" / f"{slugify(agent_id, 'agent')}.json"


def project_path(hub: Path, project_id: str) -> Path:
    return hub / "projects" / project_id


def list_agents(hub: Path) -> list[dict[str, Any]]:
    return [read_json(path) for path in sorted((hub / "agents").glob("*.json"))]


def list_projects(hub: Path) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for path in sorted((hub / "projects").glob("*/profile.json")):
        profiles.append(read_json(path))
    return profiles


def register_agent(
    hub: Path,
    name: str,
    kind: str,
    native_memory: str | None,
    description: str | None,
) -> dict[str, Any]:
    ensure_hub(hub)
    agent_id = slugify(name, "agent")
    path = agent_path(hub, agent_id)
    current = read_json(path) if path.exists() else {}
    profile = {
        **current,
        "agent_id": agent_id,
        "name": name,
        "kind": kind,
        "description": description or current.get("description", ""),
        "native_memory": (
            str(Path(native_memory).expanduser().resolve(strict=False))
            if native_memory
            else current.get("native_memory")
        ),
        "updated_at": now_iso(),
        "created_at": current.get("created_at", now_iso()),
        "last_seen": current.get("last_seen", {}),
    }
    with exclusive_lock(hub):
        atomic_write_json(path, profile)
    return profile


def ensure_agent(hub: Path, name: str, kind: str = "generic") -> dict[str, Any]:
    path = agent_path(hub, name)
    if path.exists():
        return read_json(path)
    return register_agent(hub, name, kind, None, None)


def register_project(
    hub: Path, path_value: str, name: str | None, goal: str | None
) -> dict[str, Any]:
    ensure_hub(hub)
    normalized_path = str(Path(path_value).expanduser().resolve(strict=False))
    project_name = name or Path(normalized_path).name or "Project"
    existing = next(
        (
            project
            for project in list_projects(hub)
            if project.get("path") == normalized_path
        ),
        None,
    )
    project_id = (
        existing["project_id"]
        if existing
        else stable_project_id(normalized_path, project_name)
    )
    directory = project_path(hub, project_id)
    profile_path = directory / "profile.json"
    current = existing or (read_json(profile_path) if profile_path.exists() else {})
    profile = {
        **current,
        "project_id": project_id,
        "name": project_name,
        "path": normalized_path,
        "goal": goal if goal is not None else current.get("goal", ""),
        "status": current.get("status", "unknown"),
        "created_at": current.get("created_at", now_iso()),
        "updated_at": now_iso(),
        "latest_event_id": current.get("latest_event_id"),
    }
    with exclusive_lock(hub):
        (directory / "events").mkdir(parents=True, exist_ok=True)
        atomic_write_json(profile_path, profile)
        render_current(hub, project_id)
    return profile


def resolve_project(hub: Path, value: str) -> dict[str, Any]:
    projects = list_projects(hub)
    for project in projects:
        if value == project["project_id"] or value == project["name"]:
            return project
    normalized = str(Path(value).expanduser().resolve(strict=False))
    for project in projects:
        if normalized == project["path"]:
            return project
    containing = [
        project
        for project in projects
        if Path(normalized).is_relative_to(Path(project["path"]))
    ]
    if containing:
        return max(containing, key=lambda item: len(Path(item["path"]).parts))
    raise HubError(
        f"Unknown project '{value}'. Register it with `project add` first."
    )


def event_files(hub: Path, project_id: str | None = None) -> list[Path]:
    if project_id:
        return sorted((project_path(hub, project_id) / "events").glob("*.jsonl"))
    return sorted((hub / "projects").glob("*/events/*.jsonl"))


def iter_events(hub: Path, project_id: str | None = None) -> Iterable[dict[str, Any]]:
    for path in event_files(hub, project_id):
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise HubError(f"Invalid event {path}:{line_number}: {exc}") from exc


def project_events(hub: Path, project_id: str) -> list[dict[str, Any]]:
    return sorted(iter_events(hub, project_id), key=lambda item: item["timestamp"])


def relay_state_from_event_type(event_type: str) -> str | None:
    return {
        "relay.created": "offered",
        "relay.accepted": "accepted",
        "relay.heartbeat": "accepted",
        "relay.rejected": "rejected",
        "relay.cancelled": "cancelled",
        "relay.completed": "completed",
        "relay.failed": "failed",
        "relay.verified": "verified",
        "relay.expired": "expired",
    }.get(event_type)


def derive_relays(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    relays: dict[str, dict[str, Any]] = {}
    for event in sorted(events, key=lambda item: item["timestamp"]):
        event_type = event.get("event_type")
        payload = event.get("payload", {})
        if event_type == "handoff":
            relay_id = payload.get("relay_id") or f"legacy-{event['event_id'][:12]}"
            relays[relay_id] = {
                "relay_id": relay_id,
                "project_id": event["project_id"],
                "from_agent": event["agent_id"],
                "to_agent": payload.get("to_agent"),
                "verifier_agent": payload.get("verifier_agent", event["agent_id"]),
                "state": "legacy",
                "priority": payload.get("priority", "normal"),
                "summary": event["summary"],
                "created_at": event["timestamp"],
                "updated_at": event["timestamp"],
                "expires_at": payload.get("expires_at"),
                "acceptance_criteria": payload.get("acceptance_criteria", []),
                "depends_on": payload.get("depends_on", []),
                "parent_relay_id": payload.get("parent_relay_id"),
                "lease_seconds": payload.get("lease_seconds", 3600),
                "input_artifacts": payload.get("artifact_snapshots", []),
                "completion_artifacts": [],
                "history": [event],
                "legacy": True,
            }
            continue

        state = relay_state_from_event_type(event_type)
        relay_id = payload.get("relay_id")
        if state is None or not relay_id:
            continue
        if event_type == "relay.created":
            relays[relay_id] = {
                "relay_id": relay_id,
                "project_id": event["project_id"],
                "from_agent": event["agent_id"],
                "to_agent": payload.get("to_agent"),
                "verifier_agent": payload.get("verifier_agent", event["agent_id"]),
                "state": state,
                "priority": payload.get("priority", "normal"),
                "summary": event["summary"],
                "created_at": event["timestamp"],
                "updated_at": event["timestamp"],
                "expires_at": payload.get("expires_at"),
                "acceptance_criteria": payload.get("acceptance_criteria", []),
                "depends_on": payload.get("depends_on", []),
                "parent_relay_id": payload.get("parent_relay_id"),
                "input_artifacts": payload.get("artifact_snapshots", []),
                "completion_artifacts": [],
                "accepted_by": None,
                "accepted_at": None,
                "history": [event],
                "legacy": False,
            }
            continue
        relay = relays.get(relay_id)
        if relay is None:
            continue
        relay["state"] = state
        relay["updated_at"] = event["timestamp"]
        relay["history"].append(event)
        if event_type == "relay.accepted":
            relay["accepted_by"] = event["agent_id"]
            relay["accepted_at"] = event["timestamp"]
            relay["lease_expires_at"] = payload.get("lease_expires_at")
            relay["input_verification"] = payload.get("artifact_verification", [])
        elif event_type == "relay.heartbeat":
            relay["lease_expires_at"] = payload.get("lease_expires_at")
            relay["last_heartbeat_at"] = event["timestamp"]
        elif event_type == "relay.completed":
            relay["completion_artifacts"] = payload.get("artifact_snapshots", [])
            relay["completion_summary"] = event["summary"]
        elif event_type == "relay.failed":
            relay["failure_summary"] = event["summary"]
        elif event_type == "relay.verified":
            relay["verified_by"] = event["agent_id"]
            relay["verified_at"] = event["timestamp"]
            relay["completion_verification"] = payload.get(
                "artifact_verification", []
            )
    return relays


def relay_is_expired(relay: dict[str, Any], at: dt.datetime | None = None) -> bool:
    current = at or dt.datetime.now(dt.timezone.utc)
    if relay.get("state") == "offered":
        expires_at = relay.get("expires_at")
        return bool(expires_at and parse_iso(expires_at) <= current)
    if relay.get("state") == "accepted":
        lease_expires_at = relay.get("lease_expires_at")
        return bool(lease_expires_at and parse_iso(lease_expires_at) <= current)
    return False


def relay_for_id(
    hub: Path, relay_id: str, project: dict[str, Any] | None = None
) -> dict[str, Any]:
    projects = [project] if project else list_projects(hub)
    for candidate in projects:
        relays = derive_relays(project_events(hub, candidate["project_id"]))
        if relay_id in relays:
            return relays[relay_id]
    raise HubError(f"Unknown relay: {relay_id}")


def relay_inbox(
    hub: Path,
    agent_id: str,
    project_id: str | None = None,
    include_terminal: bool = False,
) -> list[dict[str, Any]]:
    normalized_agent = slugify(agent_id, "agent")
    projects = (
        [read_json(project_path(hub, project_id) / "profile.json")]
        if project_id
        else list_projects(hub)
    )
    inbox: list[dict[str, Any]] = []
    for project in projects:
        for relay in derive_relays(
            project_events(hub, project["project_id"])
        ).values():
            state = relay["state"]
            addressed = relay.get("to_agent") == normalized_agent
            owned = relay.get("accepted_by") == normalized_agent
            sent = relay.get("from_agent") == normalized_agent
            verifies = relay.get("verifier_agent") == normalized_agent
            if include_terminal:
                include = addressed or owned or sent or verifies
            else:
                include = (
                    (addressed and state == "offered")
                    or (owned and state == "accepted")
                    or (verifies and state == "completed")
                )
            if not include or state == "legacy":
                continue
            relay_view = {key: value for key, value in relay.items() if key != "history"}
            relay_view["project_name"] = project["name"]
            relay_view["expired"] = relay_is_expired(relay)
            inbox.append(relay_view)
    return sorted(
        inbox,
        key=lambda item: (
            {"urgent": 0, "high": 1, "normal": 2, "low": 3}.get(
                item.get("priority"), 2
            ),
            item.get("updated_at", ""),
        ),
    )


def assert_relay_actor(
    relay: dict[str, Any], actor: str, action: str
) -> None:
    actor_id = slugify(actor, "agent")
    if action in {"accept", "reject"} and actor_id != relay.get("to_agent"):
        raise HubError(
            f"Only target agent '{relay.get('to_agent')}' can {action} relay "
            f"{relay['relay_id']}."
        )
    if action in {"complete", "fail"} and actor_id != relay.get("accepted_by"):
        raise HubError(
            f"Only accepting agent '{relay.get('accepted_by')}' can {action} relay "
            f"{relay['relay_id']}."
        )
    if action == "verify" and actor_id != relay.get("verifier_agent"):
        raise HubError(
            f"Only verifier '{relay.get('verifier_agent')}' can verify relay "
            f"{relay['relay_id']}."
        )
    if action == "cancel" and actor_id not in {
        relay.get("from_agent"),
        relay.get("accepted_by"),
    }:
        raise HubError(
            f"Only source or accepting agent can cancel relay {relay['relay_id']}."
        )


def assert_relay_transition(relay: dict[str, Any], next_state: str) -> None:
    current = relay["state"]
    if next_state not in RELAY_TRANSITIONS.get(current, set()):
        raise HubError(
            f"Invalid relay transition {current} -> {next_state} for "
            f"{relay['relay_id']}."
        )


def normalize_list(values: list[str] | None) -> list[str]:
    return [value.strip() for value in (values or []) if value.strip()]


def reject_sensitive(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    match = (
        SENSITIVE_PATTERN.search(serialized)
        or PRIVATE_KEY_PATTERN.search(serialized)
        or KNOWN_SECRET_PATTERN.search(serialized)
    )
    if match:
        label = match.group(1) if match.lastindex else "private key"
        raise HubError(
            f"Possible secret detected near '{label}'. "
            "Store a file or vault reference instead of secret values."
        )


def index_event(database: sqlite3.Connection, event: dict[str, Any]) -> None:
    payload_json = json_dump(event["payload"])
    cursor = database.execute(
        """
        INSERT OR IGNORE INTO events(
            event_id, project_id, timestamp, agent_id, event_type,
            status, summary, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            event["project_id"],
            event["timestamp"],
            event["agent_id"],
            event["event_type"],
            event.get("status"),
            event["summary"],
            payload_json,
        ),
    )
    if cursor.rowcount:
        rowid = cursor.lastrowid
        try:
            database.execute(
                "INSERT INTO events_fts(rowid, summary, payload) VALUES (?, ?, ?)",
                (rowid, event["summary"], payload_json),
            )
        except sqlite3.OperationalError:
            pass
    database.commit()


def append_event(
    hub: Path,
    project: dict[str, Any],
    agent: dict[str, Any],
    event_type: str,
    status: str | None,
    summary: str,
    payload: dict[str, Any],
    relay_transition: tuple[str, str] | None = None,
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise HubError(f"Unsupported event type: {event_type}")
    reject_sensitive({"summary": summary, "payload": payload})
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "timestamp": now_iso(),
        "project_id": project["project_id"],
        "agent_id": agent["agent_id"],
        "event_type": event_type,
        "status": status,
        "summary": summary.strip(),
        "payload": payload,
    }
    if not event["summary"]:
        raise HubError("Summary must not be empty.")

    event_directory = project_path(hub, project["project_id"]) / "events"
    month = event["timestamp"][:7]
    target = event_directory / f"{month}.jsonl"
    with exclusive_lock(hub):
        idempotency_key = payload.get("idempotency_key")
        if idempotency_key:
            for existing in iter_events(hub, project["project_id"]):
                if existing.get("payload", {}).get("idempotency_key") != idempotency_key:
                    continue
                same_operation = (
                    existing["agent_id"] == event["agent_id"]
                    and existing["event_type"] == event["event_type"]
                    and existing["summary"] == event["summary"]
                )
                if not same_operation:
                    raise HubError(
                        f"Idempotency key '{idempotency_key}' already belongs to "
                        "a different operation."
                    )
                return existing

        if relay_transition:
            expected_state, next_state = relay_transition
            relay_id = payload.get("relay_id")
            current = derive_relays(
                project_events(hub, project["project_id"])
            ).get(relay_id)
            if current is None:
                raise HubError(f"Unknown relay: {relay_id}")
            if current["state"] != expected_state:
                raise HubError(
                    f"Relay {relay_id} changed concurrently: expected "
                    f"{expected_state}, found {current['state']}."
                )
            if next_state not in RELAY_TRANSITIONS.get(expected_state, set()):
                raise HubError(
                    f"Invalid relay transition {expected_state} -> {next_state}."
                )

        event_directory.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json_dump(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        database = connect_index(hub)
        try:
            index_event(database, event)
        finally:
            database.close()

        profile_path = project_path(hub, project["project_id"]) / "profile.json"
        profile = read_json(profile_path)
        profile["updated_at"] = event["timestamp"]
        profile["latest_event_id"] = event["event_id"]
        if status:
            profile["status"] = status
        atomic_write_json(profile_path, profile)
        render_current(hub, project["project_id"])
    return event


def latest_nonempty(
    events: list[dict[str, Any]], field: str, limit: int = 10
) -> list[str]:
    values: list[str] = []
    for event in reversed(events):
        raw = event.get("payload", {}).get(field, [])
        candidates = raw if isinstance(raw, list) else [raw]
        for candidate in candidates:
            if candidate and candidate not in values:
                values.append(str(candidate))
                if len(values) >= limit:
                    return values
    return values


def markdown_list(values: list[str], empty: str = "- None recorded") -> str:
    if not values:
        return empty
    return "\n".join(f"- {value}" for value in values)


def render_current(hub: Path, project_id: str) -> None:
    directory = project_path(hub, project_id)
    profile = read_json(directory / "profile.json")
    events = sorted(iter_events(hub, project_id), key=lambda item: item["timestamp"])
    latest = events[-1] if events else None
    relays = derive_relays(events)
    active_relays = [
        relay
        for relay in relays.values()
        if relay["state"] not in TERMINAL_RELAY_STATES
        and relay["state"] != "legacy"
    ]
    pending_offers = [relay for relay in active_relays if relay["state"] == "offered"]
    accepted_relays = [
        relay for relay in active_relays if relay["state"] == "accepted"
    ]
    awaiting_verification = [
        relay for relay in active_relays if relay["state"] == "completed"
    ]
    handoffs = [
        event
        for event in events
        if event["event_type"] in {"handoff", "relay.created"}
    ]
    latest_handoff = handoffs[-1] if handoffs else None
    recent = events[-8:]

    decisions = latest_nonempty(events, "decisions")
    current_events = [latest] if latest else []
    blockers = latest_nonempty(current_events, "blockers")
    next_steps = latest_nonempty(current_events, "next_steps")
    artifacts = latest_nonempty(events, "artifacts")

    lines = [
        f"# {profile['name']}",
        "",
        f"- Project ID: `{project_id}`",
        f"- Path: `{profile['path']}`",
        f"- Status: `{profile.get('status', 'unknown')}`",
        f"- Updated: `{profile.get('updated_at', '')}`",
        f"- Goal: {profile.get('goal') or 'Not recorded'}",
        f"- Relay offers: `{len(pending_offers)}`",
        f"- Relays in progress: `{len(accepted_relays)}`",
        f"- Awaiting verification: `{len(awaiting_verification)}`",
        "",
        "## Current State",
        "",
        latest["summary"] if latest else "No project events recorded.",
        "",
        "## Next Steps",
        "",
        markdown_list(next_steps),
        "",
        "## Blockers",
        "",
        markdown_list(blockers),
        "",
        "## Decisions",
        "",
        markdown_list(decisions),
        "",
        "## Key Artifacts",
        "",
        markdown_list(artifacts),
        "",
        "## Relay Board",
        "",
    ]
    if active_relays:
        for relay in sorted(
            active_relays, key=lambda item: item.get("updated_at", ""), reverse=True
        ):
            expiry = (
                f" · expires `{relay['expires_at']}`"
                if relay.get("expires_at")
                else ""
            )
            overdue = " · **OVERDUE**" if relay_is_expired(relay) else ""
            lines.append(
                f"- `{relay['relay_id']}` · `{relay['state']}` · "
                f"`{relay['from_agent']}` → `{relay.get('to_agent')}`"
                f"{expiry}{overdue} · {relay['summary']}"
            )
    else:
        lines.append("- No active relays")

    lines.extend(
        [
            "",
        "## Latest Relay Offer",
            "",
        ]
    )
    if latest_handoff:
        payload = latest_handoff["payload"]
        lines.extend(
            [
                f"- From: `{latest_handoff['agent_id']}`",
                f"- To: `{payload.get('to_agent') or 'unspecified'}`",
                f"- Time: `{latest_handoff['timestamp']}`",
                f"- Event: `{latest_handoff['event_id']}`",
                "",
                latest_handoff["summary"],
            ]
        )
    else:
        lines.append("No handoff recorded.")

    lines.extend(["", "## Recent Activity", ""])
    if recent:
        for event in reversed(recent):
            lines.append(
                f"- `{event['timestamp']}` · `{event['agent_id']}` · "
                f"`{event['event_type']}` · {event['summary']} "
                f"(`{event['event_id']}`)"
            )
    else:
        lines.append("- No activity recorded")
    lines.append("")
    atomic_write_text(directory / "CURRENT.md", "\n".join(lines))


def payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if getattr(args, "file", None):
        payload.update(read_json(Path(args.file).expanduser()))
    mapping = {
        "decisions": "decision",
        "next_steps": "next_step",
        "blockers": "blocker",
        "artifacts": "artifact",
        "source_refs": "source_ref",
        "tags": "tag",
    }
    for target, source in mapping.items():
        values = normalize_list(getattr(args, source, None))
        if values:
            existing = payload.get(target, [])
            if not isinstance(existing, list):
                existing = [existing]
            payload[target] = [*existing, *values]
        else:
            payload.setdefault(target, [])
    notes = getattr(args, "notes", None)
    if notes:
        payload["notes"] = notes
    idempotency_key = getattr(args, "idempotency_key", None)
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    artifacts = payload.get("artifacts", [])
    if artifacts:
        payload["artifact_snapshots"] = snapshot_artifacts(artifacts)
    return payload


def format_event(event: dict[str, Any]) -> str:
    payload = event.get("payload", {})
    lines = [
        f"- {event['timestamp']} · {event['project_id']} · "
        f"{event['agent_id']} · {event['event_type']}",
        f"  {event['summary']}",
    ]
    for field in ("decisions", "next_steps", "blockers", "artifacts"):
        values = payload.get(field, [])
        if values:
            lines.append(f"  {field}: " + " | ".join(values))
    return "\n".join(lines)


def format_relay(relay: dict[str, Any]) -> str:
    lines = [
        f"- `{relay['relay_id']}` · `{relay['state']}` · "
        f"`{relay['from_agent']}` → `{relay.get('to_agent') or 'unspecified'}`",
        f"  {relay['summary']}",
        f"  project: {relay.get('project_name') or relay['project_id']}",
        f"  priority: {relay.get('priority', 'normal')}",
    ]
    if relay.get("expires_at"):
        lines.append(f"  expires: {relay['expires_at']}")
    if relay.get("lease_expires_at"):
        lines.append(f"  lease_expires: {relay['lease_expires_at']}")
    if relay.get("acceptance_criteria"):
        lines.append(
            "  acceptance: " + " | ".join(relay["acceptance_criteria"])
        )
    if relay.get("depends_on"):
        lines.append("  depends_on: " + " | ".join(relay["depends_on"]))
    return "\n".join(lines)


def build_context_pack(
    hub: Path,
    project: dict[str, Any],
    agent_id: str,
    max_chars: int,
) -> dict[str, Any]:
    if max_chars < 500:
        raise HubError("Context budget must be at least 500 characters.")
    current_path = project_path(hub, project["project_id"]) / "CURRENT.md"
    with shared_lock(hub):
        current = current_path.read_text(encoding="utf-8")
        inbox = relay_inbox(
            hub, agent_id, project_id=project["project_id"], include_terminal=False
        )
        events = project_events(hub, project["project_id"])

    sections = [
        "# Agent Relay Context",
        "",
        f"- Agent: `{slugify(agent_id, 'agent')}`",
        f"- Project: `{project['name']}`",
        f"- Path: `{project['path']}`",
        "",
        "## Current Project State",
        "",
        current,
        "",
        "## Relay Inbox",
        "",
    ]
    if inbox:
        sections.extend(format_relay(relay) for relay in inbox)
    else:
        sections.append("- No active relays for this agent")
    sections.extend(
        [
            "",
            "## Operating Rule",
            "",
            "Verify referenced artifacts before continuing. Accept a relay explicitly "
            "before owning it; complete it with evidence; the source agent verifies closure.",
        ]
    )
    full = "\n".join(sections)
    truncated = len(full) > max_chars
    if truncated:
        recent = events[-5:]
        compact = [
            "# Agent Relay Context",
            "",
            f"- Agent: `{slugify(agent_id, 'agent')}`",
            f"- Project: `{project['name']}`",
            f"- Path: `{project['path']}`",
            f"- Status: `{project.get('status', 'unknown')}`",
            f"- Goal: {project.get('goal') or 'Not recorded'}",
            "",
            "## Relay Inbox",
            "",
        ]
        compact.extend(
            format_relay(relay) for relay in inbox[:5]
        )
        if not inbox:
            compact.append("- No active relays for this agent")
        compact.extend(["", "## Recent Events", ""])
        compact.extend(format_event(event) for event in reversed(recent))
        compact.extend(
            [
                "",
                f"Full state: `{current_path}`",
                "Verify artifacts before continuing.",
            ]
        )
        full = "\n".join(compact)
        if len(full) > max_chars:
            full = full[: max_chars - 80].rstrip() + "\n\n[Context truncated to budget]"
    return {
        "project": project,
        "agent_id": slugify(agent_id, "agent"),
        "context": full,
        "chars": len(full),
        "max_chars": max_chars,
        "truncated": truncated,
        "relay_count": len(inbox),
    }


def rebuild_index(hub: Path) -> int:
    database_path = hub / "index.sqlite"
    with exclusive_lock(hub):
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                (hub / f"index.sqlite{suffix}").unlink()
        database = connect_index(hub)
        count = 0
        try:
            for event in iter_events(hub):
                index_event(database, event)
                count += 1
        finally:
            database.close()
    return count


def command_init(args: argparse.Namespace) -> None:
    ensure_hub(args.hub)
    print(f"Initialized {PRODUCT_NAME} at {args.hub}")


def command_agent_add(args: argparse.Namespace) -> None:
    profile = register_agent(
        args.hub, args.name, args.kind, args.native_memory, args.description
    )
    print(json.dumps(profile, ensure_ascii=False, indent=2))


def command_agent_list(args: argparse.Namespace) -> None:
    validate_hub(args.hub)
    agents = list_agents(args.hub)
    if args.json:
        print(json.dumps(agents, ensure_ascii=False, indent=2))
        return
    for agent in agents:
        print(
            f"{agent['agent_id']}\t{agent['kind']}\t"
            f"{agent.get('native_memory') or '-'}"
        )


def command_project_add(args: argparse.Namespace) -> None:
    profile = register_project(args.hub, args.path, args.name, args.goal)
    print(json.dumps(profile, ensure_ascii=False, indent=2))


def command_bootstrap(args: argparse.Namespace) -> None:
    ensure_hub(args.hub)
    agent = register_agent(
        args.hub,
        args.agent,
        args.agent_kind,
        args.native_memory,
        args.agent_description,
    )
    project = register_project(args.hub, args.project, args.name, args.goal)
    result = {
        "hub": str(args.hub),
        "agent": agent,
        "project": project,
        "next": (
            f"agent-relay context --agent {agent['agent_id']} "
            f"--project {project['path']}"
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_project_list(args: argparse.Namespace) -> None:
    validate_hub(args.hub)
    projects = list_projects(args.hub)
    if args.json:
        print(json.dumps(projects, ensure_ascii=False, indent=2))
        return
    for project in sorted(
        projects, key=lambda item: item.get("updated_at", ""), reverse=True
    ):
        print(
            f"{project['project_id']}\t{project.get('status', 'unknown')}\t"
            f"{project['name']}\t{project['path']}"
        )


def command_capture(args: argparse.Namespace) -> None:
    ensure_hub(args.hub)
    project = resolve_project(args.hub, args.project)
    agent = ensure_agent(args.hub, args.agent, args.agent_kind)
    payload = payload_from_args(args)
    event = append_event(
        args.hub,
        project,
        agent,
        args.type,
        args.status,
        args.summary,
        payload,
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))


def command_handoff(args: argparse.Namespace) -> None:
    command_relay_offer(args)


def command_relay_offer(args: argparse.Namespace) -> None:
    ensure_hub(args.hub)
    project = resolve_project(args.hub, args.project)
    source = ensure_agent(args.hub, args.from_agent, args.from_kind)
    target = ensure_agent(args.hub, args.to_agent, args.to_kind)
    if source["agent_id"] == target["agent_id"]:
        raise HubError("Source and target agent must be different.")
    payload = payload_from_args(args)
    acceptance_criteria = normalize_list(getattr(args, "acceptance", None))
    if not acceptance_criteria:
        acceptance_criteria = list(payload.get("next_steps", []))
    if not acceptance_criteria:
        raise HubError(
            "Relay offer requires at least one --acceptance or --next-step."
        )
    relay_id = new_relay_id()
    expires_at = (
        None
        if getattr(args, "no_expiry", False)
        else future_iso(args.expires_in)
    )
    payload["relay_id"] = relay_id
    payload["to_agent"] = target["agent_id"]
    verifier = ensure_agent(
        args.hub,
        getattr(args, "verifier", None) or source["agent_id"],
        getattr(args, "verifier_kind", "generic"),
    )
    payload["verifier_agent"] = verifier["agent_id"]
    payload["priority"] = getattr(args, "priority", "normal")
    payload["expires_at"] = expires_at
    payload["acceptance_criteria"] = acceptance_criteria
    dependencies = normalize_list(getattr(args, "depends_on", None))
    parent_relay_id = getattr(args, "parent_relay", None)
    if parent_relay_id and parent_relay_id not in dependencies:
        dependencies.append(parent_relay_id)
    for dependency in dependencies:
        dependency_relay = relay_for_id(args.hub, dependency, project)
        if dependency_relay.get("legacy"):
            raise HubError("Legacy handoffs cannot be relay dependencies.")
        if dependency_relay["project_id"] != project["project_id"]:
            raise HubError("Relay dependencies must belong to the same project.")
    payload["depends_on"] = dependencies
    payload["parent_relay_id"] = parent_relay_id
    payload["lease_seconds"] = args.lease_seconds
    event = append_event(
        args.hub,
        project,
        source,
        "relay.created",
        args.status,
        args.summary,
        payload,
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))


def relay_action_event(
    args: argparse.Namespace,
    action: str,
    next_state: str,
    event_type: str,
) -> dict[str, Any]:
    ensure_hub(args.hub)
    project = (
        resolve_project(args.hub, args.project)
        if getattr(args, "project", None)
        else None
    )
    relay = relay_for_id(args.hub, args.relay, project)
    if relay.get("legacy"):
        raise HubError("Legacy handoffs cannot enter the v2 relay state machine.")
    idempotency_key = getattr(args, "idempotency_key", None)
    if idempotency_key:
        for candidate in iter_events(args.hub, relay["project_id"]):
            payload = candidate.get("payload", {})
            if payload.get("idempotency_key") != idempotency_key:
                continue
            if (
                candidate["event_type"] == event_type
                and candidate["agent_id"] == slugify(args.agent, "agent")
                and candidate["summary"] == args.summary
            ):
                return candidate
            raise HubError(
                f"Idempotency key '{idempotency_key}' already belongs to "
                "a different operation."
            )
    if relay_is_expired(relay) and action not in {"expire", "cancel"}:
        raise HubError(
            f"Relay {relay['relay_id']} expired at {relay['expires_at']}. "
            "Run `agent-relay expire`."
        )
    required_state = {
        "accept": "offered",
        "reject": "offered",
        "complete": "accepted",
        "fail": "accepted",
        "verify": "completed",
    }.get(action)
    if required_state and relay["state"] != required_state:
        raise HubError(
            f"Invalid relay transition {relay['state']} -> {next_state} for "
            f"{relay['relay_id']}; action '{action}' requires {required_state}."
        )
    assert_relay_actor(relay, args.agent, action)
    assert_relay_transition(relay, next_state)
    actor = ensure_agent(
        args.hub, args.agent, getattr(args, "agent_kind", "generic")
    )
    profile = read_json(
        project_path(args.hub, relay["project_id"]) / "profile.json"
    )
    payload = payload_from_args(args)
    payload["relay_id"] = relay["relay_id"]
    payload["from_state"] = relay["state"]
    payload["to_state"] = next_state
    if action == "accept":
        unresolved = []
        for dependency in relay.get("depends_on", []):
            dependency_relay = relay_for_id(args.hub, dependency, profile)
            if dependency_relay["state"] != "verified":
                unresolved.append(
                    f"{dependency}:{dependency_relay['state']}"
                )
        if unresolved:
            raise HubError(
                "Relay dependencies are not verified: " + " | ".join(unresolved)
            )
        payload["artifact_verification"] = [
            verify_artifact_snapshot(snapshot)
            for snapshot in relay.get("input_artifacts", [])
        ]
        failed = [
            result
            for result in payload["artifact_verification"]
            if result["result"] not in {"verified", "unverifiable-reference"}
        ]
        if failed and not getattr(args, "allow_changed_artifacts", False):
            raise HubError(
                "Input artifacts changed or are missing. Re-run with "
                "--allow-changed-artifacts only after manual inspection."
            )
        lease_seconds = getattr(args, "lease_seconds", None) or relay.get(
            "lease_seconds", 3600
        )
        payload["lease_seconds"] = lease_seconds
        payload["lease_expires_at"] = future_iso(lease_seconds)
    elif action == "complete":
        if not payload.get("artifact_snapshots") and not payload.get("source_refs"):
            raise HubError(
                "Completing a relay requires at least one --artifact or --source-ref."
            )
    elif action == "verify":
        criteria = relay.get("acceptance_criteria", [])
        criteria_met = (
            list(criteria)
            if getattr(args, "accept_all_criteria", False)
            else normalize_list(getattr(args, "criterion_met", None))
        )
        missing_criteria = [
            criterion for criterion in criteria if criterion not in criteria_met
        ]
        if missing_criteria:
            raise HubError(
                "Unverified acceptance criteria: " + " | ".join(missing_criteria)
            )
        payload["criteria_met"] = criteria_met
        payload["artifact_verification"] = [
            verify_artifact_snapshot(snapshot)
            for snapshot in relay.get("completion_artifacts", [])
        ]
        failed = [
            result
            for result in payload["artifact_verification"]
            if result["result"] not in {"verified", "unverifiable-reference"}
        ]
        if failed and not getattr(args, "allow_changed_artifacts", False):
            raise HubError(
                "Completion artifacts changed or are missing. Re-run with "
                "--allow-changed-artifacts only after manual verification."
            )
    return append_event(
        args.hub,
        profile,
        actor,
        event_type,
        getattr(args, "status", None),
        args.summary,
        payload,
        relay_transition=(relay["state"], next_state),
    )


def command_relay_accept(args: argparse.Namespace) -> None:
    event = relay_action_event(
        args, action="accept", next_state="accepted", event_type="relay.accepted"
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))


def command_relay_heartbeat(args: argparse.Namespace) -> None:
    ensure_hub(args.hub)
    project = (
        resolve_project(args.hub, args.project)
        if getattr(args, "project", None)
        else None
    )
    relay = relay_for_id(args.hub, args.relay, project)
    if relay.get("legacy"):
        raise HubError("Legacy handoffs cannot receive heartbeats.")
    if relay["state"] != "accepted":
        raise HubError(
            f"Relay {relay['relay_id']} is {relay['state']}, not accepted."
        )
    assert_relay_actor(relay, args.agent, "complete")
    actor = ensure_agent(args.hub, args.agent, args.agent_kind)
    profile = read_json(
        project_path(args.hub, relay["project_id"]) / "profile.json"
    )
    lease_seconds = args.lease_seconds or relay.get("lease_seconds", 3600)
    payload = {
        "relay_id": relay["relay_id"],
        "from_state": "accepted",
        "to_state": "accepted",
        "lease_seconds": lease_seconds,
        "lease_expires_at": future_iso(lease_seconds),
    }
    if args.idempotency_key:
        payload["idempotency_key"] = args.idempotency_key
    event = append_event(
        args.hub,
        profile,
        actor,
        "relay.heartbeat",
        profile.get("status"),
        args.summary,
        payload,
        relay_transition=("accepted", "accepted"),
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))


def command_relay_reject(args: argparse.Namespace) -> None:
    event = relay_action_event(
        args, action="reject", next_state="rejected", event_type="relay.rejected"
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))


def command_relay_complete(args: argparse.Namespace) -> None:
    event = relay_action_event(
        args,
        action="complete",
        next_state="completed",
        event_type="relay.completed",
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))


def command_relay_fail(args: argparse.Namespace) -> None:
    event = relay_action_event(
        args, action="fail", next_state="failed", event_type="relay.failed"
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))


def command_relay_verify(args: argparse.Namespace) -> None:
    event = relay_action_event(
        args, action="verify", next_state="verified", event_type="relay.verified"
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))


def command_relay_cancel(args: argparse.Namespace) -> None:
    event = relay_action_event(
        args, action="cancel", next_state="cancelled", event_type="relay.cancelled"
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))


def command_relay_show(args: argparse.Namespace) -> None:
    validate_hub(args.hub)
    project = (
        resolve_project(args.hub, args.project)
        if getattr(args, "project", None)
        else None
    )
    relay = relay_for_id(args.hub, args.relay, project)
    output = {key: value for key, value in relay.items() if key != "history"}
    output["events"] = relay.get("history", [])
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(format_relay(output))
        print("\n## Events")
        for event in output["events"]:
            print(format_event(event))


def command_relay_inbox(args: argparse.Namespace) -> None:
    validate_hub(args.hub)
    project_id = None
    if args.project:
        project_id = resolve_project(args.hub, args.project)["project_id"]
    relays = relay_inbox(
        args.hub,
        args.agent,
        project_id=project_id,
        include_terminal=args.all,
    )
    if args.json:
        print(json.dumps(relays, ensure_ascii=False, indent=2))
        return
    if not relays:
        print("No relays.")
        return
    for relay in relays:
        print(format_relay(relay))


def command_relay_expire(args: argparse.Namespace) -> None:
    ensure_hub(args.hub)
    expired_events: list[dict[str, Any]] = []
    projects = (
        [resolve_project(args.hub, args.project)]
        if args.project
        else list_projects(args.hub)
    )
    for project in projects:
        relays = derive_relays(project_events(args.hub, project["project_id"]))
        for relay in relays.values():
            if not relay_is_expired(relay):
                continue
            source = ensure_agent(args.hub, relay["from_agent"])
            event = append_event(
                args.hub,
                project,
                source,
                "relay.expired",
                project.get("status"),
                (
                    f"Relay lease expired: {relay['summary']}"
                    if relay["state"] == "accepted"
                    else f"Relay expired before acceptance: {relay['summary']}"
                ),
                {
                    "relay_id": relay["relay_id"],
                    "from_state": relay["state"],
                    "to_state": "expired",
                    "expires_at": relay.get("expires_at"),
                    "lease_expires_at": relay.get("lease_expires_at"),
                },
                relay_transition=(relay["state"], "expired"),
            )
            expired_events.append(event)
    if args.json:
        print(json.dumps(expired_events, ensure_ascii=False, indent=2))
    else:
        print(f"Expired relays: {len(expired_events)}")


def command_context(args: argparse.Namespace) -> None:
    validate_hub(args.hub)
    project = resolve_project(args.hub, args.project or os.getcwd())
    pack = build_context_pack(args.hub, project, args.agent, args.max_chars)
    if args.json:
        print(json.dumps(pack, ensure_ascii=False, indent=2))
    else:
        print(pack["context"])


def command_resume(args: argparse.Namespace) -> None:
    if args.no_mark:
        validate_hub(args.hub)
    else:
        ensure_hub(args.hub)
    project = resolve_project(args.hub, args.project)
    registered_agent = agent_path(args.hub, args.agent)
    if registered_agent.exists():
        agent = read_json(registered_agent)
    elif args.no_mark:
        agent = {
            "agent_id": slugify(args.agent, "agent"),
            "name": args.agent,
            "kind": args.agent_kind,
            "last_seen": {},
        }
    else:
        agent = ensure_agent(args.hub, args.agent, args.agent_kind)
    with shared_lock(args.hub):
        events = sorted(
            iter_events(args.hub, project["project_id"]),
            key=lambda item: item["timestamp"],
        )
        current_path = project_path(args.hub, project["project_id"]) / "CURRENT.md"
        current = current_path.read_text(encoding="utf-8")
    previous_event_id = agent.get("last_seen", {}).get(project["project_id"])
    unseen: list[dict[str, Any]] = []
    found_previous = False
    for event in events:
        if found_previous:
            unseen.append(event)
        elif previous_event_id and event["event_id"] == previous_event_id:
            found_previous = True
    if previous_event_id is None:
        unseen = events[-10:]
    elif not found_previous:
        unseen = events[-10:]

    result = {
        "project": project,
        "current_markdown": current,
        "unseen_events": unseen[-20:],
        "previous_event_id": previous_event_id,
        "relay_inbox": relay_inbox(
            args.hub, agent["agent_id"], project_id=project["project_id"]
        ),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(current.rstrip())
        print("\n## Changes Since This Agent Last Resumed\n")
        if unseen:
            for event in unseen[-20:]:
                print(format_event(event))
        else:
            print("- No new shared-memory events")
        print("\n## Relay Inbox\n")
        if result["relay_inbox"]:
            for relay in result["relay_inbox"]:
                print(format_relay(relay))
        else:
            print("- No relays")

    if events and not args.no_mark:
        with exclusive_lock(args.hub):
            profile = read_json(agent_path(args.hub, agent["agent_id"]))
            profile.setdefault("last_seen", {})[project["project_id"]] = events[-1][
                "event_id"
            ]
            profile["updated_at"] = now_iso()
            atomic_write_json(agent_path(args.hub, agent["agent_id"]), profile)


def command_status(args: argparse.Namespace) -> None:
    validate_hub(args.hub)
    if args.project:
        project = resolve_project(args.hub, args.project)
        current = project_path(args.hub, project["project_id"]) / "CURRENT.md"
        with shared_lock(args.hub):
            print(current.read_text(encoding="utf-8"))
        return
    projects = list_projects(args.hub)
    print(f"Hub: {args.hub}")
    print(f"Agents: {len(list_agents(args.hub))}")
    print(f"Projects: {len(projects)}")
    print(f"Events: {sum(1 for _ in iter_events(args.hub))}")
    for project in sorted(
        projects, key=lambda item: item.get("updated_at", ""), reverse=True
    ):
        print(
            f"- {project['name']} [{project.get('status', 'unknown')}] "
            f"updated {project.get('updated_at', '-')}"
        )


def command_search(args: argparse.Namespace) -> None:
    validate_hub(args.hub)
    project_id = None
    if args.project:
        project = resolve_project(args.hub, args.project)
        project_id = project["project_id"]
    terms = [term.casefold() for term in args.query.split() if term.strip()]
    events = []
    with shared_lock(args.hub):
        for event in iter_events(args.hub, project_id):
            haystack = json.dumps(event, ensure_ascii=False).casefold()
            if all(term in haystack for term in terms):
                events.append(event)
    events.sort(key=lambda item: item["timestamp"], reverse=True)
    events = events[: args.limit]
    if args.json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
    else:
        for event in events:
            print(format_event(event))


def command_source_add(args: argparse.Namespace) -> None:
    ensure_hub(args.hub)
    source_id = slugify(args.name, "source")
    path = Path(args.path).expanduser().resolve(strict=False)
    profile = {
        "source_id": source_id,
        "name": args.name,
        "path": str(path),
        "kind": args.kind,
        "agent_id": slugify(args.agent, "agent") if args.agent else None,
        "created_at": now_iso(),
    }
    with exclusive_lock(args.hub):
        atomic_write_json(args.hub / "sources" / f"{source_id}.json", profile)
    print(json.dumps(profile, ensure_ascii=False, indent=2))


def command_rebuild(args: argparse.Namespace) -> None:
    ensure_hub(args.hub)
    count = rebuild_index(args.hub)
    for project in list_projects(args.hub):
        render_current(args.hub, project["project_id"])
    print(f"Rebuilt index and current views from {count} events")


def command_backup(args: argparse.Namespace) -> None:
    validate_hub(args.hub)
    destination = (
        Path(args.output).expanduser()
        if args.output
        else args.hub / "backups" / f"agent-relay-{local_stamp()}.tar.gz"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with shared_lock(args.hub):
        with tarfile.open(destination, "w:gz") as archive:
            for path in sorted(args.hub.iterdir()):
                if path.name in {"backups", "locks"} or path.name.startswith(
                    "index.sqlite"
                ):
                    continue
                archive.add(path, arcname=Path("agent-relay") / path.name)
    print(destination)


def safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve(strict=False)
    for member in archive.getmembers():
        target = (destination / member.name).resolve(strict=False)
        if not target.is_relative_to(destination):
            raise HubError(f"Unsafe backup member path: {member.name}")
    archive.extractall(destination, filter="data")


def command_restore(args: argparse.Namespace) -> None:
    source = Path(args.archive).expanduser().resolve(strict=True)
    destination = args.hub
    if destination.exists() and any(destination.iterdir()) and not args.force:
        raise HubError(
            f"Destination is not empty: {destination}. Use --force after backing it up."
        )
    stage_parent = destination.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".agent-relay-restore-", dir=stage_parent))
    try:
        with tarfile.open(source, "r:*") as archive:
            safe_extract_tar(archive, stage)
        roots = [
            candidate
            for candidate in (stage / "agent-relay", stage / "agent-memory-hub")
            if candidate.exists()
        ]
        if len(roots) != 1:
            raise HubError("Backup must contain exactly one Agent Relay root.")
        restored = roots[0]
        validate_hub(restored)
        if destination.exists():
            if args.force:
                shutil.rmtree(destination)
            else:
                destination.rmdir()
        shutil.move(str(restored), str(destination))
        ensure_hub(destination)
        rebuild_index(destination)
        for project in list_projects(destination):
            render_current(destination, project["project_id"])
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    print(destination)


def command_doctor(args: argparse.Namespace) -> None:
    manifest = validate_hub(args.hub)
    issues: list[str] = []
    warnings: list[str] = []
    event_ids: set[str] = set()
    event_count = 0
    events_by_project: dict[str, list[dict[str, Any]]] = {}
    if manifest.get("product") != PRODUCT_NAME:
        warnings.append(
            f"Manifest product is {manifest.get('product')!r}, expected {PRODUCT_NAME!r}"
        )
    if manifest.get("product_version") != VERSION:
        warnings.append(
            f"Manifest product version is {manifest.get('product_version')!r}, "
            f"expected {VERSION!r}; run any write command to upgrade metadata"
        )
    with shared_lock(args.hub):
        for event in iter_events(args.hub):
            event_count += 1
            event_id = event.get("event_id")
            if event_id in event_ids:
                issues.append(f"Duplicate event ID: {event_id}")
            event_ids.add(event_id)
            for required in (
                "schema_version",
                "event_id",
                "timestamp",
                "project_id",
                "agent_id",
                "event_type",
                "summary",
                "payload",
            ):
                if required not in event:
                    issues.append(f"Event {event_id} missing {required}")
            if event.get("event_type") not in EVENT_TYPES:
                issues.append(
                    f"Event {event_id} has unsupported type "
                    f"{event.get('event_type')}"
                )
            if event.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                issues.append(
                    f"Event {event_id} has unsupported schema "
                    f"{event.get('schema_version')}"
                )
            serialized = json.dumps(event, ensure_ascii=False)
            if SENSITIVE_PATTERN.search(serialized) or PRIVATE_KEY_PATTERN.search(
                serialized
            ):
                issues.append(f"Possible secret in event {event_id}")
            events_by_project.setdefault(event.get("project_id", ""), []).append(
                event
            )

        database = connect_index(args.hub, readonly=True)
        indexed_count = database.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        database.close()
    if indexed_count != event_count:
        issues.append(
            f"Index mismatch: {indexed_count} indexed vs {event_count} event records"
        )

    for project in list_projects(args.hub):
        current = project_path(args.hub, project["project_id"]) / "CURRENT.md"
        if not current.exists():
            issues.append(f"Missing CURRENT.md for {project['project_id']}")
        events = events_by_project.get(project["project_id"], [])
        relays = derive_relays(events)
        attached_event_ids: set[str] = set()
        for relay in relays.values():
            if relay.get("legacy"):
                continue
            history = relay.get("history", [])
            if not history or history[0].get("event_type") != "relay.created":
                issues.append(f"Relay {relay['relay_id']} has no creation event")
                continue
            simulated = "offered"
            for index, event in enumerate(history):
                attached_event_ids.add(event["event_id"])
                state = relay_state_from_event_type(event["event_type"])
                if index == 0:
                    if state != "offered":
                        issues.append(
                            f"Relay {relay['relay_id']} starts at invalid state "
                            f"{state}"
                        )
                    continue
                if state not in RELAY_TRANSITIONS.get(simulated, set()):
                    issues.append(
                        f"Relay {relay['relay_id']} invalid history transition "
                        f"{simulated} -> {state}"
                    )
                    break
                simulated = state
            if relay_is_expired(relay):
                warnings.append(
                    f"Relay {relay['relay_id']} expired but remains offered"
                )
            if relay["state"] == "completed" and not relay.get(
                "completion_artifacts"
            ):
                completion = history[-1].get("payload", {}) if history else {}
                if not completion.get("source_refs"):
                    issues.append(
                        f"Relay {relay['relay_id']} completed without evidence"
                    )
            for snapshot in [
                *relay.get("input_artifacts", []),
                *relay.get("completion_artifacts", []),
            ]:
                if snapshot.get("verifiable") and not snapshot.get("path"):
                    issues.append(
                        f"Relay {relay['relay_id']} has malformed artifact snapshot"
                    )
        for event in events:
            if (
                event["event_type"].startswith("relay.")
                and event["event_type"] != "relay.created"
                and event["event_id"] not in attached_event_ids
            ):
                issues.append(
                    f"Orphan relay event {event['event_id']} for "
                    f"{event.get('payload', {}).get('relay_id')}"
                )

    for agent in list_agents(args.hub):
        native = agent.get("native_memory")
        if native and not Path(native).exists():
            warnings.append(
                f"Native memory path for {agent['agent_id']} is missing: {native}"
            )

    print(f"Hub: {args.hub}")
    print(f"Agents: {len(list_agents(args.hub))}")
    print(f"Projects: {len(list_projects(args.hub))}")
    print(f"Events: {event_count}")
    print(f"Index records: {indexed_count}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for issue in issues:
        print(f"ERROR: {issue}")
    if issues:
        raise HubError(f"Doctor found {len(issues)} issue(s)")
    print("Doctor: OK")


def add_event_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", required=True, help="Project ID, name, or path")
    parser.add_argument("--status", choices=["unknown", "planned", "in_progress", "blocked", "completed", "paused"])
    parser.add_argument("--summary", required=True)
    add_payload_arguments(parser)


def add_payload_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--decision", action="append")
    parser.add_argument("--next-step", action="append")
    parser.add_argument("--blocker", action="append")
    parser.add_argument("--artifact", action="append")
    parser.add_argument("--source-ref", action="append")
    parser.add_argument("--tag", action="append")
    parser.add_argument("--notes")
    parser.add_argument("--idempotency-key")
    parser.add_argument(
        "--file",
        help="Optional JSON payload; explicit CLI values take precedence",
    )


def add_relay_action_arguments(
    parser: argparse.ArgumentParser,
    *,
    artifacts: bool = False,
    allow_changed: bool = False,
) -> None:
    parser.add_argument("--relay", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--agent-kind", default="generic")
    parser.add_argument("--project")
    parser.add_argument("--summary", required=True)
    parser.add_argument(
        "--status",
        choices=["unknown", "planned", "in_progress", "blocked", "completed", "paused"],
    )
    add_payload_arguments(parser)
    if not artifacts:
        parser.set_defaults(artifact=None)
    if allow_changed:
        parser.add_argument("--allow-changed-artifacts", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name or "agent-relay",
        description=(
            "Agent Relay: verifiable, append-only work handoffs across AI agents."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    parser.add_argument(
        "--hub",
        type=Path,
        default=DEFAULT_HUB,
        help=f"Hub directory (default: {DEFAULT_HUB})",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="Initialize a hub")
    init_parser.set_defaults(func=command_init)

    bootstrap = commands.add_parser(
        "bootstrap", help="Initialize hub, register one agent, and add a project"
    )
    bootstrap.add_argument("--project", default=os.getcwd())
    bootstrap.add_argument("--name")
    bootstrap.add_argument("--goal")
    bootstrap.add_argument("--agent", required=True)
    bootstrap.add_argument("--agent-kind", default="generic")
    bootstrap.add_argument("--native-memory")
    bootstrap.add_argument("--agent-description")
    bootstrap.set_defaults(func=command_bootstrap)

    agent = commands.add_parser("agent", help="Manage agents")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    agent_add = agent_commands.add_parser("add")
    agent_add.add_argument("name")
    agent_add.add_argument("--kind", default="generic")
    agent_add.add_argument("--native-memory")
    agent_add.add_argument("--description")
    agent_add.set_defaults(func=command_agent_add)
    agent_list = agent_commands.add_parser("list")
    agent_list.add_argument("--json", action="store_true")
    agent_list.set_defaults(func=command_agent_list)

    project = commands.add_parser("project", help="Manage projects")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_add = project_commands.add_parser("add")
    project_add.add_argument("path")
    project_add.add_argument("--name")
    project_add.add_argument("--goal")
    project_add.set_defaults(func=command_project_add)
    project_list = project_commands.add_parser("list")
    project_list.add_argument("--json", action="store_true")
    project_list.set_defaults(func=command_project_list)

    capture = commands.add_parser("capture", help="Append progress or a decision")
    add_event_arguments(capture)
    capture.add_argument("--agent", required=True)
    capture.add_argument("--agent-kind", default="generic")
    capture.add_argument(
        "--type",
        default="progress",
        choices=["progress", "decision", "artifact", "blocker", "note"],
    )
    capture.set_defaults(func=command_capture)

    offer = commands.add_parser("offer", help="Offer work to another agent")
    add_event_arguments(offer)
    offer.add_argument("--from-agent", required=True)
    offer.add_argument("--to-agent", required=True)
    offer.add_argument("--from-kind", default="generic")
    offer.add_argument("--to-kind", default="generic")
    offer.add_argument("--verifier")
    offer.add_argument("--verifier-kind", default="generic")
    offer.add_argument("--acceptance", action="append")
    offer.add_argument(
        "--priority", choices=["urgent", "high", "normal", "low"], default="normal"
    )
    offer.add_argument(
        "--expires-in",
        type=int,
        default=7 * 24 * 60 * 60,
        help="Seconds until an unaccepted relay expires",
    )
    offer.add_argument("--no-expiry", action="store_true")
    offer.add_argument("--depends-on", action="append")
    offer.add_argument("--parent-relay")
    offer.add_argument(
        "--lease-seconds",
        type=int,
        default=3600,
        help="Default accepted-work lease duration",
    )
    offer.set_defaults(func=command_relay_offer)

    handoff = commands.add_parser(
        "handoff", help="Compatibility alias for `offer`"
    )
    add_event_arguments(handoff)
    handoff.add_argument("--from-agent", required=True)
    handoff.add_argument("--to-agent", required=True)
    handoff.add_argument("--from-kind", default="generic")
    handoff.add_argument("--to-kind", default="generic")
    handoff.add_argument("--verifier")
    handoff.add_argument("--verifier-kind", default="generic")
    handoff.add_argument("--acceptance", action="append")
    handoff.add_argument(
        "--priority", choices=["urgent", "high", "normal", "low"], default="normal"
    )
    handoff.add_argument("--expires-in", type=int, default=7 * 24 * 60 * 60)
    handoff.add_argument("--no-expiry", action="store_true")
    handoff.add_argument("--depends-on", action="append")
    handoff.add_argument("--parent-relay")
    handoff.add_argument("--lease-seconds", type=int, default=3600)
    handoff.set_defaults(func=command_handoff)

    accept = commands.add_parser("accept", help="Accept an offered relay")
    add_relay_action_arguments(accept, allow_changed=True)
    accept.add_argument("--lease-seconds", type=int)
    accept.set_defaults(func=command_relay_accept)

    heartbeat = commands.add_parser(
        "heartbeat", help="Renew the lease for accepted relay work"
    )
    heartbeat.add_argument("--relay", required=True)
    heartbeat.add_argument("--agent", required=True)
    heartbeat.add_argument("--agent-kind", default="generic")
    heartbeat.add_argument("--project")
    heartbeat.add_argument("--summary", default="Relay work is still active")
    heartbeat.add_argument("--lease-seconds", type=int)
    heartbeat.add_argument("--idempotency-key")
    heartbeat.set_defaults(func=command_relay_heartbeat)

    reject = commands.add_parser("reject", help="Reject an offered relay")
    add_relay_action_arguments(reject)
    reject.set_defaults(func=command_relay_reject)

    complete = commands.add_parser(
        "complete", help="Mark accepted relay work complete"
    )
    add_relay_action_arguments(complete, artifacts=True)
    complete.set_defaults(func=command_relay_complete)

    fail = commands.add_parser("fail", help="Mark accepted relay work failed")
    add_relay_action_arguments(fail)
    fail.set_defaults(func=command_relay_fail)

    verify = commands.add_parser(
        "verify", help="Verify completed relay and close the loop"
    )
    add_relay_action_arguments(verify, allow_changed=True)
    verify.add_argument("--criterion-met", action="append")
    verify.add_argument("--accept-all-criteria", action="store_true")
    verify.set_defaults(func=command_relay_verify)

    cancel = commands.add_parser("cancel", help="Cancel an active relay")
    add_relay_action_arguments(cancel)
    cancel.set_defaults(func=command_relay_cancel)

    relay_show = commands.add_parser("show", help="Show one relay and its history")
    relay_show.add_argument("--relay", required=True)
    relay_show.add_argument("--project")
    relay_show.add_argument("--json", action="store_true")
    relay_show.set_defaults(func=command_relay_show)

    inbox = commands.add_parser("inbox", help="Show work awaiting an agent")
    inbox.add_argument("--agent", required=True)
    inbox.add_argument("--project")
    inbox.add_argument("--all", action="store_true")
    inbox.add_argument("--json", action="store_true")
    inbox.set_defaults(func=command_relay_inbox)

    expire = commands.add_parser("expire", help="Close expired relay offers")
    expire.add_argument("--project")
    expire.add_argument("--json", action="store_true")
    expire.set_defaults(func=command_relay_expire)

    context = commands.add_parser(
        "context", help="Build a bounded context pack for session startup"
    )
    context.add_argument("--project")
    context.add_argument("--agent", required=True)
    context.add_argument("--max-chars", type=int, default=8000)
    context.add_argument("--json", action="store_true")
    context.set_defaults(func=command_context)

    resume = commands.add_parser("resume", help="Read current context and new work")
    resume.add_argument("--project", required=True)
    resume.add_argument("--agent", required=True)
    resume.add_argument("--agent-kind", default="generic")
    resume.add_argument("--no-mark", action="store_true")
    resume.add_argument("--json", action="store_true")
    resume.set_defaults(func=command_resume)

    status = commands.add_parser("status", help="Show hub or project status")
    status.add_argument("--project")
    status.set_defaults(func=command_status)

    search = commands.add_parser("search", help="Search shared events")
    search.add_argument("query")
    search.add_argument("--project")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=command_search)

    source = commands.add_parser("source", help="Register an external memory source")
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_add = source_commands.add_parser("add")
    source_add.add_argument("name")
    source_add.add_argument("path")
    source_add.add_argument("--kind", default="native-memory")
    source_add.add_argument("--agent")
    source_add.set_defaults(func=command_source_add)

    rebuild = commands.add_parser("rebuild", help="Rebuild views and search index")
    rebuild.set_defaults(func=command_rebuild)

    backup = commands.add_parser("backup", help="Create a portable backup")
    backup.add_argument("--output")
    backup.set_defaults(func=command_backup)

    restore = commands.add_parser("restore", help="Restore a portable backup")
    restore.add_argument("archive")
    restore.add_argument("--force", action="store_true")
    restore.set_defaults(func=command_restore)

    doctor = commands.add_parser("doctor", help="Validate hub integrity")
    doctor.set_defaults(func=command_doctor)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.hub = args.hub.expanduser().resolve(strict=False)
    try:
        args.func(args)
        return 0
    except HubError as exc:
        print(f"agent-relay: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
