# Development Workflow — Orchestrator v2

> How agents, skills, and humans collaborate. **Ceremony scales with task tier** —
> do not run the full 8-step loop for every typo.

## Start here

1. **Multi-step task** → `orchestrator` agent (or classify tier yourself).
2. **Delegate** via Task tool to specialist agents with explicit paths + acceptance criteria.
3. **Verify** with `/run-quality-gate` then `/verify-done` before declaring done.

## Ceremony by tier

### L0 — Trivial (typo, CSS, comment, docs only)

| Step | Agent / Skill |
|------|---------------|
| Implement | One engineer (`frontend-engineer` or `backend-engineer`) |
| Gate | `/run-quality-gate` (minimal subset) |
| Close | `/verify-done` |

Skip: architect, security, full review.

### L1 — Standard (router, component, ETL without new KPI)

| Step | Agent / Skill |
|------|---------------|
| Optional | 3-line plan in chat |
| Implement | One specialist engineer |
| Gate | `/run-quality-gate` |
| Optional | `code-reviewer` (readonly) |
| Close | `/verify-done` |

Skip: `/plan-feature`, architect, security (unless uploads/auth).

### L2 — Financial-critical (KPI, periods, GL mapping, schema, signs)

| Step | Agent / Skill |
|------|---------------|
| Spec | `/plan-feature` |
| Architecture | `software-architect` (readonly) |
| Implement | `financial-calculation-engineer` (if numeric) + domain engineer |
| Tests | `test-engineer`, `/financial-metric-test` |
| Gate | `/run-quality-gate` (full touched areas) |
| Review | `code-reviewer`, `security-privacy-reviewer` |
| Close | `/verify-done` |

## Full loop reference (L2 only)

| Step | What | Agent / Skill |
|------|------|---------------|
| 1. Idea | Brief problem statement | — |
| 2. Spec | AC, edge cases | `/plan-feature` |
| 3. Architecture | Module boundaries | `software-architect` |
| 4. Implement | Small diff | engineer agents |
| 5. Tests | Regression / golden | `test-engineer` |
| 6. Quality gate | Build, import, pytest | `/run-quality-gate` |
| 7. Review | Diff + security | `code-reviewer`, `security-privacy-reviewer` |
| 8. Doc/PR | Docs, PR prep | human |

Use **1–3 agents per task**, not the whole roster. Exploration → `explore` subagent.

## Branching

- `main` stable · `feat/<topic>` / `fix/<topic>` branches · squash merge to `main`.
- Commit **only when the human asks**. Never force-push / hard reset / skip hooks.

## Quality gate — GDPdU-Reports

```bash
cd frontend && npm run build
cd backend && python -c "from app import main"
cd backend && python -m pytest -q backend/tests/test_<area>.py
cd etl && python -m pytest -q etl/tests/test_<area>.py    # if etl/ touched
cd rasa && rasa data validate                             # FDD only
```

## Quality gate — finssentials monorepo

```bash
cd frontend && npm run build
cd backend && python -c "import main"
cd backend && python -m pytest -q backend/tests
cd rasa && rasa data validate
cd rasa-expert && rasa data validate
cd rasa-readiness && rasa data validate
```

See `/run-quality-gate` skill and `/verify-done` for mandatory close checklist.

## Model policy (v2)

| Agent | Model |
|-------|-------|
| `orchestrator`, `software-architect`, `financial-calculation-engineer`, `security-privacy-reviewer` | Opus 4.8 |
| `backend-engineer`, `data-transformation-engineer` | Opus 4.8 (GDPdU cross-module) |
| `frontend-engineer`, `test-engineer`, `code-reviewer`, `skill-builder` | Sonnet 4.6 |

Orchestration saves **context**, not reasoning depth on L2 tasks.

## When to escalate to the human

- Ambiguous accounting/sign semantics.
- Secrets, prod, real data, migrations.
- Weakening hooks, permissions, or CI checks.
