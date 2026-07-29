#!/usr/bin/env python3

import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SCRIPT = Path(__file__).with_name("agent_relay.py")


class MemoryHubIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.hub = self.root / "hub"
        self.project = self.root / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_hub(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), "--hub", str(self.hub), *arguments],
            text=True,
            capture_output=True,
            check=check,
            env={**os.environ, "PYTHONUTF8": "1"},
        )

    def test_cross_agent_handoff_and_resume(self) -> None:
        self.run_hub("init")
        self.run_hub("agent", "add", "codex", "--kind", "codex")
        self.run_hub("agent", "add", "trae", "--kind", "trae")
        project = json.loads(
            self.run_hub(
                "project",
                "add",
                str(self.project),
                "--name",
                "Demo Project",
                "--goal",
                "Prove cross-agent continuity",
            ).stdout
        )

        self.run_hub(
            "capture",
            "--project",
            project["project_id"],
            "--agent",
            "codex",
            "--status",
            "in_progress",
            "--summary",
            "Implemented the first workflow",
            "--decision",
            "Use append-only events",
            "--artifact",
            str(self.project / "workflow.py"),
            "--next-step",
            "TRAE validates the workflow",
        )
        first_resume = self.run_hub(
            "resume",
            "--project",
            str(self.project),
            "--agent",
            "trae",
            "--json",
        )
        resumed = json.loads(first_resume.stdout)
        self.assertEqual(len(resumed["unseen_events"]), 1)
        self.assertEqual(
            resumed["unseen_events"][0]["summary"], "Implemented the first workflow"
        )

        self.run_hub(
            "capture",
            "--project",
            str(self.project),
            "--agent",
            "trae",
            "--type",
            "artifact",
            "--status",
            "in_progress",
            "--summary",
            "Validated the workflow",
            "--artifact",
            str(self.project / "test-results.json"),
            "--next-step",
            "Codex reviews the validation",
        )
        self.run_hub(
            "handoff",
            "--project",
            str(self.project),
            "--from-agent",
            "trae",
            "--to-agent",
            "codex",
            "--status",
            "in_progress",
            "--summary",
            "Validation passed; review the remaining edge case",
            "--next-step",
            "Review the retry edge case",
        )

        codex_resume = json.loads(
            self.run_hub(
                "resume",
                "--project",
                str(self.project),
                "--agent",
                "codex",
                "--json",
            ).stdout
        )
        self.assertEqual(len(codex_resume["unseen_events"]), 3)
        self.assertEqual(
            codex_resume["unseen_events"][-1]["event_type"], "relay.created"
        )
        current = (self.hub / "projects" / project["project_id"] / "CURRENT.md").read_text()
        self.assertIn("Review the retry edge case", current)
        self.assertIn("Validation passed", current)

        search = self.run_hub("search", "retry", "--json")
        results = json.loads(search.stdout)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["event_type"], "relay.created")
        self.assertIn("Doctor: OK", self.run_hub("doctor").stdout)

        self.run_hub(
            "capture",
            "--project",
            str(self.project),
            "--agent",
            "codex",
            "--status",
            "completed",
            "--summary",
            "Review completed",
            "--next-step",
            "Use the hub on the next project",
        )
        current = (self.hub / "projects" / project["project_id"] / "CURRENT.md").read_text()
        self.assertIn("Use the hub on the next project", current)
        self.assertNotIn("Review the retry edge case\n\n## Blockers", current)

    def test_secret_guard_rejects_values(self) -> None:
        self.run_hub("init")
        project = json.loads(
            self.run_hub("project", "add", str(self.project)).stdout
        )
        result = self.run_hub(
            "capture",
            "--project",
            project["project_id"],
            "--agent",
            "codex",
            "--summary",
            "api_key=secret-value",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Possible secret detected", result.stderr)

    def test_concurrent_writes_and_project_identity(self) -> None:
        self.run_hub("init")
        first = json.loads(
            self.run_hub(
                "project", "add", str(self.project), "--name", "Original Name"
            ).stdout
        )
        renamed = json.loads(
            self.run_hub(
                "project", "add", str(self.project), "--name", "Renamed Project"
            ).stdout
        )
        self.assertEqual(first["project_id"], renamed["project_id"])

        def write_event(index: int) -> None:
            self.run_hub(
                "capture",
                "--project",
                str(self.project),
                "--agent",
                f"agent-{index}",
                "--summary",
                f"Concurrent event {index}",
                "--type",
                "progress",
            )

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(write_event, range(12)))

        doctor = self.run_hub("doctor")
        self.assertIn("Events: 12", doctor.stdout)
        self.assertIn("Index records: 12", doctor.stdout)
        self.assertEqual(
            len(list((self.hub / "projects").glob("*/events/*.jsonl"))), 1
        )

    def test_readonly_resume_and_doctor(self) -> None:
        self.run_hub("init")
        project = json.loads(
            self.run_hub("project", "add", str(self.project)).stdout
        )
        self.run_hub(
            "capture",
            "--project",
            project["project_id"],
            "--agent",
            "codex",
            "--summary",
            "Readonly consumers can inspect this event",
        )
        paths = [self.hub, *self.hub.rglob("*")]
        original_modes = {
            path: path.stat().st_mode & 0o777
            for path in paths
            if not path.is_symlink()
        }
        try:
            for path in sorted(original_modes, key=lambda item: len(item.parts), reverse=True):
                path.chmod(0o555 if path.is_dir() else 0o444)
            resume = self.run_hub(
                "resume",
                "--project",
                str(self.project),
                "--agent",
                "unregistered-readonly-agent",
                "--no-mark",
                "--json",
            )
            self.assertEqual(len(json.loads(resume.stdout)["unseen_events"]), 1)
            self.assertIn("Doctor: OK", self.run_hub("doctor").stdout)
            backup_path = self.root / "readonly-backup.tar.gz"
            backup = self.run_hub(
                "backup", "--output", str(backup_path)
            )
            self.assertEqual(Path(backup.stdout.strip()), backup_path)
            self.assertTrue(backup_path.exists())
            with tarfile.open(backup_path) as archive:
                names = archive.getnames()
            self.assertEqual(len(names), len(set(names)))
            self.assertIn("agent-relay/manifest.json", names)
            self.assertNotIn("agent-relay/index.sqlite", names)
        finally:
            for path in sorted(original_modes, key=lambda item: len(item.parts)):
                path.chmod(original_modes[path])


if __name__ == "__main__":
    unittest.main()
