# Security & Data Rules — for Claude Code

> Finssentials handles financial data. Some of it, in real use, is client-confidential.
> The agent setup must be safe by construction, not by good intentions. Hooks enforce
> the hard parts; this doc explains the policy and the reasoning.

## Data classification

| Class | Examples | Rule |
|-------|----------|------|
| Secret | `.env`, `.env.*`, API keys, DB passwords, `*.pem`, `*.key`, deploy SSH keys | Never read, echo, log, or commit. Editing requires explicit human confirmation. |
| Client data | Uploaded `*.xlsx`/`*.csv` under `uploads/`, DB dumps, real GL/sales exports | Never put into prompts, logs, commits, or test fixtures. Use synthetic data. |
| Config (prod) | `*.docker` prod configs, `infra/**`, `.github/workflows/**` | Treat as protected; change only with a plan and review. |
| Public/dev | Source code, docs, synthetic fixtures, schema DDL | Normal workflow. |

## Hard rules

1. **No secrets in context.** Do not open `.env`/credential files "just to check".
   If you need a value, ask the human to set it in the environment.
2. **No real customer data anywhere an agent can persist it** — not in prompts, not
   in test files, not in commit messages, not in screenshots/PR descriptions.
3. **Database is read-only by default.** Initial DB access (and the Postgres MCP) is
   `SELECT`-only against a **local/dev** database. Never connect agents to production.
   Forbidden: `DROP`, `TRUNCATE`, `DELETE`/`UPDATE` without `WHERE`, `ALTER` on prod,
   un-planned migrations.
4. **Destructive shell is blocked.** `rm -rf`, `git push --force`, `git reset --hard`,
   `docker volume rm`, history rewrites — blocked or confirmation-gated by hooks.
5. **Uploads are untrusted input.** Validate file type/size; never `eval` workbook
   content; guard against path traversal in `uploads/<session>/`.
6. **Mandantentrennung (multi-tenant isolation).** When tenant scoping exists, every
   query/aggregation must be tenant-filtered. Flag any code path that could leak data
   across tenants.

## How the setup enforces this

- `.claude/hooks/protected-files.sh` — blocks Edit/Write to secret & protected paths.
- `.claude/hooks/secrets-check.sh` — blocks Read/Bash that would surface secret files.
- `.claude/hooks/dangerous-commands.sh` — blocks destructive Bash.
- `.claude/hooks/db-safety.sh` — blocks destructive SQL in Bash/psql calls.
- `.claude/settings.json` — `permissions.deny` mirrors the above as a second layer.
- The repo already runs **gitleaks** + **Trivy** in CI; keep those green.

> Hooks are warn-first where reasonable, but secrets/destructive actions are
> **deny**. Do not edit hooks to weaken them as part of unrelated work.

## Incident response (if a secret leaks)

1. Stop. Do not commit/push further.
2. Rotate the credential immediately.
3. Follow the repo's history-cleanup guide (`docs/GITHUB_REPO_SETUP.md`).
4. Note it for the human; never try to "quietly" scrub it yourself.

## What's missing / to decide

- A documented synthetic-fixture generator for FDD scripts (so tests never need real
  files). Tracked in `SUGGESTIONS.md`.
- Tenant model is not fully formalized yet — when it is, add concrete query rules here.
