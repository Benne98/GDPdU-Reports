# ETL — GoBD/DATEV → canonical GL

Pipeline to load GDPdU/General-Ledger data into the canonical schema (see `../docs/db/`).

Planned (Phase P1+):
- GoBD loader: source CSV → `fact_gl_entry` / `fact_gl_line` (signed amount, entity-prefixed keys).
- Account-mapping loader → `dim_gl_account` / `dim_gl_na` / `dim_gl_cf` (per entity & fiscal year).
- DATEV SKR03 path: `Konto` + `Gegenkonto` resolution (see migration plan §9).
- Derived facts: `fact_ar/ap/sales/com` (linking strategy: txn-propagation vs. Konto/Gegenkonto).
- Synthetic plan generation → `fact_gl_plan` / `fact_sales_plan` (Phase P3).

Placeholder — implementation starts in P1.
