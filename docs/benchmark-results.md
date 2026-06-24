# Benchmark Results — Orchestrator v2 (static design validation)

Date: 2026-06-14. Full timed runs against live Opus baseline are **pending manual sessions**;
this document records **install verification** and **design-level** comparison.

## Install verification (GDPdU-Reports)

| Check | Result |
|-------|--------|
| `.cursor/agents/orchestrator.md` present | PASS |
| 10 subagents installed | PASS |
| Skills: verify-done, gdpdu-stack, run-quality-gate (updated) | PASS |
| `AGENTS.md` GDPdU profile | PASS |
| `docs/architecture.md` GDPdU default | PASS |
| `.claude/` mirror | PASS |
| `scripts/install-orchestrator.ps1` | PASS (fixed PS encoding) |

## v1 vs v2 capability matrix

| Capability | v1 (upstream + Desktop port) | v2 |
|------------|------------------------------|-----|
| Route-only orchestrator | No | Yes (`orchestrator` agent, readonly) |
| Tiered ceremony L0/L1/L2 | No (fixed 8-step) | Yes (`development-workflow.md`) |
| Structured HANDOFF | No | Yes (all engineer agents) |
| GDPdU path correctness | Wrong (`backend/routers/`) | Correct (`backend/app/`) |
| GDPdU ports in docs | 5173/8000 | 5176/8010/5005 |
| ETL pipeline in routing | Minimal | `data-transformation-engineer` + `etl/` |
| Hard verify-before-done | Soft hook nudge | `/verify-done` skill |
| Profile auto-detect | No | `session-start.py` heuristic |
| finssentials secondary profile | Monorepo-only docs | `docs/profiles/finssentials.md` |
| Claude Code + Cursor | Separate ports | Dual install via `install-orchestrator.ps1` |

## Expected benchmark outcomes (from `docs/benchmark-tasks.md`)

| Task | Tier | v1 risk | v2 mitigation |
|------|------|---------|---------------|
| B1 Rasa proxy | L1 | Fix vite only, miss PS scripts | Routing table + `gdpdu-stack` |
| B2 GL ingest errors | L1 | Router-only fix | ETL + backend split |
| B3 Empty state | L0 | Full ceremony overhead | L0 direct path |
| B4 Gross margin KPI | L2 | Skip financial proof | Mandatory L2 + financial-calculation-engineer |
| B5 Version restore | L2 | Miss derive on restore | ETL specialist + test-engineer |

## Orchestrator route-not-implement

Design: `orchestrator` agent has `readonly: true` — cannot edit product code in Cursor subagent mode.
Main session should delegate via Task tool when user names orchestrator.

## Latency expectation

- L0: v2 ≤ 1.5× Opus solo (acceptable overhead for gate)
- L1: comparable; better correctness on cross-file tasks
- L2: v2 may be slower but higher safety on financial/GL changes

## Next: manual timed benchmark

Run each prompt in `docs/benchmark-tasks.md` twice (Opus solo vs orchestrator-led) in
GDPdU-Reports workspace. Record wall-clock, diff stat (`git diff --stat`), and gate result.
