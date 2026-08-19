# Runtime and installation contract

The core CLI, MCP server, and SessionStart hook use Python 3.10+ standard
library modules. Run `scripts/doctor.py --json` before installation. It checks
the package without changing `~/.agent-relay`, agent configuration, or hooks.

Installation may modify agent configuration and create backups. It requires an
explicit agent list. Run it only after the target agents and paths are known.
After installation, verify the installed Skill path, registered agent, MCP
entry, and hook separately. A symlink existing on disk does not prove that the
agent can execute it.

The canonical state directory is `~/.agent-relay`; legacy
`~/.agent-memory-hub` is compatibility-only. Relay state is project-scoped and
append-only. `completed` is an intermediate state; only `verified` closes the
handoff.
