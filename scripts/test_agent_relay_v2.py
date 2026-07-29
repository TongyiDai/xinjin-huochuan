#!/usr/bin/env python3

import json
import os
import subprocess
import tarfile
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
CLI = SCRIPTS / "agent_relay.py"
MCP = SCRIPTS / "agent_relay_mcp.py"
HOOK = SCRIPTS / "session_start.py"
INSTALL = SCRIPTS / "install.py"


class RelayCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.hub = self.root / "hub"
        self.project = self.root / "project"
        self.project.mkdir()
        self.run_cli("init")
        self.run_cli(
            "project",
            "add",
            str(self.project),
            "--name",
            "Demo",
            "--goal",
            "Exercise Agent Relay",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(
        self, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(CLI), "--hub", str(self.hub), *arguments],
            text=True,
            capture_output=True,
            check=check,
            env={**os.environ, "PYTHONUTF8": "1"},
        )

    def json_cli(self, *arguments: str) -> dict:
        return json.loads(self.run_cli(*arguments).stdout)

    def offer(
        self,
        *,
        from_agent: str = "trae",
        to_agent: str = "codex",
        artifact: Path | None = None,
        acceptance: str = "Output is verified",
        extra: tuple[str, ...] = (),
    ) -> str:
        arguments = [
            "offer",
            "--project",
            str(self.project),
            "--from-agent",
            from_agent,
            "--to-agent",
            to_agent,
            "--summary",
            "Take over this work",
            "--acceptance",
            acceptance,
            "--lease-seconds",
            "60",
            *extra,
        ]
        if artifact is not None:
            arguments.extend(["--artifact", str(artifact)])
        return self.json_cli(*arguments)["payload"]["relay_id"]


class RelayLifecycleTest(RelayCase):
    def test_complete_lifecycle_and_context(self) -> None:
        source = self.project / "input.txt"
        source.write_text("input")
        relay_id = self.offer(artifact=source)

        inbox = self.json_cli("inbox", "--agent", "codex", "--json")
        self.assertEqual([item["relay_id"] for item in inbox], [relay_id])

        accepted = self.json_cli(
            "accept",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Inputs checked",
            "--idempotency-key",
            "accept-once",
        )
        retried = self.json_cli(
            "accept",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Inputs checked",
            "--idempotency-key",
            "accept-once",
        )
        self.assertEqual(accepted["event_id"], retried["event_id"])

        heartbeat = self.json_cli(
            "heartbeat",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Still running",
            "--lease-seconds",
            "120",
        )
        self.assertEqual(heartbeat["event_type"], "relay.heartbeat")

        output = self.project / "result.json"
        output.write_text('{"ok": true}')
        self.run_cli(
            "complete",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Work complete",
            "--artifact",
            str(output),
        )
        verified = self.json_cli(
            "verify",
            "--relay",
            relay_id,
            "--agent",
            "trae",
            "--summary",
            "Acceptance checked",
            "--accept-all-criteria",
        )
        self.assertEqual(verified["event_type"], "relay.verified")

        relay = self.json_cli("show", "--relay", relay_id, "--json")
        self.assertEqual(relay["state"], "verified")
        self.assertEqual(
            [event["event_type"] for event in relay["events"]],
            [
                "relay.created",
                "relay.accepted",
                "relay.heartbeat",
                "relay.completed",
                "relay.verified",
            ],
        )
        context = self.json_cli(
            "context",
            "--project",
            str(self.project / "nested"),
            "--agent",
            "trae",
            "--max-chars",
            "1600",
            "--json",
        )
        self.assertLessEqual(context["chars"], 1600)
        self.assertIn("Agent Relay Context", context["context"])
        self.assertIn("Doctor: OK", self.run_cli("doctor").stdout)

    def test_roles_and_state_transitions_are_enforced(self) -> None:
        relay_id = self.offer()
        wrong = self.run_cli(
            "accept",
            "--relay",
            relay_id,
            "--agent",
            "claude",
            "--summary",
            "Wrong agent",
            check=False,
        )
        self.assertEqual(wrong.returncode, 2)
        self.assertIn("Only target agent", wrong.stderr)

        self.run_cli(
            "accept",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Accepted",
        )
        duplicate = self.run_cli(
            "accept",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Accepted again",
            check=False,
        )
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("Invalid relay transition", duplicate.stderr)

        no_evidence = self.run_cli(
            "complete",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "No evidence",
            check=False,
        )
        self.assertEqual(no_evidence.returncode, 2)
        self.assertIn("requires at least one", no_evidence.stderr)

    def test_third_party_verifier_receives_completed_relay(self) -> None:
        relay_id = self.offer(
            extra=("--verifier", "claude"),
        )
        self.run_cli(
            "accept",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Accepted",
        )
        self.run_cli(
            "complete",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Ready for independent verification",
            "--source-ref",
            "test: independent verification requested",
        )
        verifier_inbox = self.json_cli(
            "inbox", "--agent", "claude", "--json"
        )
        self.assertEqual(
            [relay["relay_id"] for relay in verifier_inbox], [relay_id]
        )
        source_inbox = self.json_cli("inbox", "--agent", "trae", "--json")
        self.assertNotIn(
            relay_id, [relay["relay_id"] for relay in source_inbox]
        )
        self.run_cli(
            "verify",
            "--relay",
            relay_id,
            "--agent",
            "claude",
            "--summary",
            "Independently verified",
            "--accept-all-criteria",
        )

    def test_concurrent_accept_only_one_wins(self) -> None:
        relay_id = self.offer()

        def accept(index: int) -> subprocess.CompletedProcess[str]:
            return self.run_cli(
                "accept",
                "--relay",
                relay_id,
                "--agent",
                "codex",
                "--summary",
                f"Attempt {index}",
                check=False,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(accept, range(2)))
        self.assertEqual(sorted(result.returncode for result in results), [0, 2])
        relay = self.json_cli("show", "--relay", relay_id, "--json")
        self.assertEqual(
            [event["event_type"] for event in relay["events"]].count(
                "relay.accepted"
            ),
            1,
        )


class RelayEvidenceTest(RelayCase):
    def test_input_and_completion_tampering_are_detected(self) -> None:
        source = self.project / "source.txt"
        source.write_text("before")
        relay_id = self.offer(artifact=source)
        source.write_text("after")
        result = self.run_cli(
            "accept",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Accept changed input",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Input artifacts changed", result.stderr)

        self.run_cli(
            "accept",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Manually inspected",
            "--allow-changed-artifacts",
        )
        output = self.project / "output.txt"
        output.write_text("first")
        self.run_cli(
            "complete",
            "--relay",
            relay_id,
            "--agent",
            "codex",
            "--summary",
            "Output ready",
            "--artifact",
            str(output),
        )
        output.write_text("tampered")
        verify = self.run_cli(
            "verify",
            "--relay",
            relay_id,
            "--agent",
            "trae",
            "--summary",
            "Verify",
            "--accept-all-criteria",
            check=False,
        )
        self.assertEqual(verify.returncode, 2)
        self.assertIn("Completion artifacts changed", verify.stderr)

    def test_secret_patterns_are_rejected(self) -> None:
        fake_token = "sk-" + "abcdefghijklmnop" + "123456"
        result = self.run_cli(
            "capture",
            "--project",
            str(self.project),
            "--agent",
            "trae",
            "--summary",
            f"token {fake_token}",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Possible secret detected", result.stderr)


class RelayDependencyAndExpiryTest(RelayCase):
    def test_dependency_requires_verified_parent(self) -> None:
        parent = self.offer(to_agent="codex")
        child = self.offer(
            from_agent="codex",
            to_agent="claude",
            extra=("--depends-on", parent, "--parent-relay", parent),
        )
        blocked = self.run_cli(
            "accept",
            "--relay",
            child,
            "--agent",
            "claude",
            "--summary",
            "Too early",
            check=False,
        )
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("dependencies are not verified", blocked.stderr)

        self.run_cli(
            "accept",
            "--relay",
            parent,
            "--agent",
            "codex",
            "--summary",
            "Parent accepted",
        )
        self.run_cli(
            "complete",
            "--relay",
            parent,
            "--agent",
            "codex",
            "--summary",
            "Parent done",
            "--source-ref",
            "test: parent passed",
        )
        self.run_cli(
            "verify",
            "--relay",
            parent,
            "--agent",
            "trae",
            "--summary",
            "Parent verified",
            "--accept-all-criteria",
        )
        accepted = self.run_cli(
            "accept",
            "--relay",
            child,
            "--agent",
            "claude",
            "--summary",
            "Dependency ready",
        )
        self.assertEqual(accepted.returncode, 0)

    def test_offer_and_lease_expiry(self) -> None:
        relay_id = self.offer(extra=("--expires-in", "1"))
        time.sleep(1.1)
        self.run_cli("expire")
        self.assertEqual(
            self.json_cli("show", "--relay", relay_id, "--json")["state"],
            "expired",
        )

        lease_relay = self.offer(extra=("--lease-seconds", "1"))
        self.run_cli(
            "accept",
            "--relay",
            lease_relay,
            "--agent",
            "codex",
            "--summary",
            "Short lease",
            "--lease-seconds",
            "1",
        )
        time.sleep(1.1)
        self.run_cli("expire")
        self.assertEqual(
            self.json_cli("show", "--relay", lease_relay, "--json")["state"],
            "expired",
        )


class RelayBackupMcpHookTest(RelayCase):
    def test_backup_restore_and_readonly_hook(self) -> None:
        self.offer()
        archive = Path(self.run_cli("backup").stdout.strip())
        self.assertTrue(archive.exists())
        with tarfile.open(archive) as handle:
            names = handle.getnames()
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("agent-relay/manifest.json", names)
        self.assertNotIn("agent-relay/index.sqlite", names)

        restored = self.root / "restored"
        result = subprocess.run(
            [
                "python3",
                str(CLI),
                "--hub",
                str(restored),
                "restore",
                str(archive),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(
            Path(result.stdout.strip()).resolve(strict=False),
            restored.resolve(strict=False),
        )
        self.assertIn(
            "Doctor: OK",
            subprocess.run(
                ["python3", str(CLI), "--hub", str(restored), "doctor"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout,
        )

        hook = subprocess.run(
            [
                "python3",
                str(HOOK),
                "--hub",
                str(self.hub),
                "--agent",
                "codex",
                "--max-chars",
                "1400",
            ],
            input=json.dumps({"cwd": str(self.project)}),
            text=True,
            capture_output=True,
            check=True,
        )
        output = json.loads(hook.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), 1400)
        self.assertIn("Agent Relay Context", context)

    def test_mcp_initialize_tools_and_call(self) -> None:
        request_lines = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "relay_context",
                    "arguments": {
                        "project": str(self.project),
                        "agent": "codex",
                        "max_chars": 1200,
                    },
                },
            },
        ]
        process = subprocess.run(
            ["python3", str(MCP)],
            input="\n".join(json.dumps(item) for item in request_lines) + "\n",
            text=True,
            capture_output=True,
            check=True,
            env={
                **os.environ,
                "AGENT_RELAY_HOME": str(self.hub),
                "PYTHONUTF8": "1",
            },
        )
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "agent-relay")
        tool_names = {
            tool["name"] for tool in responses[1]["result"]["tools"]
        }
        self.assertTrue(
            {
                "relay_offer",
                "relay_accept",
                "relay_complete",
                "relay_verify",
                "relay_doctor",
                "relay_bootstrap",
                "relay_search",
            }.issubset(tool_names)
        )
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertIn("Agent Relay Context", responses[2]["result"]["content"][0]["text"])


class RelayInstallTest(unittest.TestCase):
    def test_install_migrates_data_and_keeps_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            legacy_data = home / ".agent-memory-hub"
            legacy_data.mkdir()
            (legacy_data / "sentinel").write_text("preserved")
            process = subprocess.run(
                [
                    "python3",
                    str(INSTALL),
                    "--agents",
                    "cursor",
                    "--command-dir",
                    str(home / "bin"),
                ],
                text=True,
                capture_output=True,
                check=True,
                env={**os.environ, "HOME": str(home), "PYTHONUTF8": "1"},
            )
            self.assertIn("data: migrated", process.stdout)
            self.assertEqual(
                (home / ".agent-relay" / "sentinel").read_text(), "preserved"
            )
            self.assertTrue((home / ".agent-memory-hub").is_symlink())
            self.assertTrue((home / ".cursor/skills/agent-relay").is_symlink())
            self.assertTrue(
                (home / ".cursor/skills/agent-memory-hub").is_symlink()
            )
            self.assertTrue((home / "bin/agent-relay").is_symlink())
            self.assertTrue((home / "bin/memory-hub").is_symlink())
            manifest = json.loads(
                (home / ".agent-relay/manifest.json").read_text()
            )
            self.assertEqual(manifest["product"], "薪尽火传 · Agent Relay")
            self.assertEqual(manifest["product_version"], "2.0.0")
            self.assertTrue(
                (home / ".agent-relay/agents/cursor.json").exists()
            )
            subprocess.run(
                [
                    "python3",
                    str(INSTALL),
                    "--agents",
                    "cursor",
                    "--command-dir",
                    str(home / "bin"),
                    "--uninstall",
                ],
                text=True,
                capture_output=True,
                check=True,
                env={**os.environ, "HOME": str(home), "PYTHONUTF8": "1"},
            )
            self.assertFalse((home / ".cursor/skills/agent-relay").exists())
            self.assertFalse((home / ".cursor/skills/agent-memory-hub").exists())
            self.assertFalse((home / "bin/agent-relay").exists())
            self.assertFalse((home / "bin/memory-hub").exists())
            self.assertEqual(
                (home / ".agent-relay" / "sentinel").read_text(), "preserved"
            )


if __name__ == "__main__":
    unittest.main()
