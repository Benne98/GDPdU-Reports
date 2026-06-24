# Phase 0 Audit — Orchestrator v2 (2026-06-14)

## Sources compared

| Source | Role | Notes |
|--------|------|-------|
| `sn7xmhpr29-sys/Finssentials-Orchestrator` (cloned) | Upstream template | Claude Code edition (`claude/`), 9 agents, 11 skills, `install.sh` |
| `Desktop/Project SELFMADE/Cursor` | Cursor port reference | Full `.cursor/` tree; basis for `cursor/` in this repo |
| `GDPdU-Reports` | Primary install target | No orchestrator before v2; `backend/app/` layout, ports 5176/8010/5005 |

## Key gaps in v1 (why worse than single Opus)

1. No dedicated route-only orchestrator agent — main session implemented directly.
2. Fixed 8-step ceremony for all task sizes.
3. Implementers pinned to Sonnet 4.6 while baseline was Opus end-to-end.
4. Architecture docs referenced `backend/routers/` (finssentials monorepo), not `backend/app/`.
5. Quality gate was a non-blocking hook nudge, not a mandatory verify step.
6. GDPdU stack (ETL, single Rasa, port 5176) not documented.

## v2 changes (this repo)

- `cursor/agents/orchestrator.md` — Opus, route-only, tier matrix L0/L1/L2.
- Structured HANDOFF block in all engineer agents.
- `docs/architecture.md` — GDPdU-Reports default; `docs/profiles/finssentials.md` secondary.
- `profiles/gdpdu-reports/AGENTS.md` — short project memory for GDPdU.
- Skills: `verify-done`, `gdpdu-stack`; updated `run-quality-gate`.
- `session-start.py` — detects repo profile from filesystem heuristics.
- `scripts/install-orchestrator.ps1` — deploy to GDPdU-Reports (Windows).

## Diff: upstream claude vs cursor edition

| Item | `claude/` | `cursor/` |
|------|-----------|-----------|
| Agents | 9 | 10 (+ orchestrator) |
| Hooks contract | Claude Code settings.json | Cursor hooks.json |
| Project memory | CLAUDE.md | AGENTS.md (per profile) |
| MCP | `.mcp.json` at root | `.cursor/mcp.json` |

Both editions share `docs/` (architecture, workflow, financial-logic, security).
