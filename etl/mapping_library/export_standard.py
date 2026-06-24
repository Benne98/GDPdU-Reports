"""Generate the standard mapping library JSON from a live/clone ``dim_gl_account``.

Run this ONCE against a DB that already carries the correct mapping (e.g.
``finssentials_v2``) to mint ``finssentials_standard_v1.json`` — the auto-suggest
source for new clients (``etl/mapping_suggest.py``), the same concept as the FDD
bot's ``BS_/PL_Kontenmapping.xlsx``.

USAGE
─────
    # from repo root, with the backend venv active
    $env:DB_PASSWORD='...'; $env:DB_NAME='finssentials_v2'
    python etl/mapping_library/export_standard.py \
        --out etl/mapping_library/finssentials_standard_v1.json

    # or point at an explicit URL
    python etl/mapping_library/export_standard.py \
        --url postgresql+psycopg2://postgres:pw@localhost:5432/finssentials_v2

FILE FORMAT  (finssentials_standard_v1.json)
────────────────────────────────────────────
A single JSON object::

    {
      "version": "finssentials_standard_v1",
      "source_db": "finssentials_v2",
      "generated_at": "2026-06-21T...Z",
      "entry_count": 1234,
      "entries": [
        {
          "gl_account_id": "16100",          # stable business key (primary)
          "account_name": "Bau- und Wohnwagen",  # stable business key (secondary)
          "level_0": "BS",
          "level_1": "Assets",
          "level_2": "Fixed assets",
          "level_3": "Tangible assets",
          "level_4": null,
          "l4_sub": null,
          "level_1_sort": 1,
          "level_2_sort": 1,
          "level_3_sort": 13,
          "level_4_sort": null
        },
        ...
      ]
    }

KEYING / DEDUP
──────────────
``dim_gl_account`` is keyed on (account_number_group, fiscal_year), so the SAME
business account appears once per year (and per entity).  The library is a *chart*
of distinct accounts, so we collapse to ONE entry per ``gl_account_id`` (falling
back to ``account_name`` when no id), preferring the most recent fiscal year so
the latest agreed hierarchy wins.  ``entries`` is sorted by ``gl_account_id`` then
``account_name`` for a stable, diff-friendly file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DEFAULT_OUT = _REPO_ROOT / "etl" / "mapping_library" / "finssentials_standard_v1.json"
LIBRARY_VERSION = "finssentials_standard_v1"

#: Library entry hierarchy/sort fields (must match etl.mapping_suggest.PROPOSAL_FIELDS).
_ENTRY_FIELDS = (
    "level_0",
    "level_1",
    "level_2",
    "level_3",
    "level_4",
    "l4_sub",
    "level_1_sort",
    "level_2_sort",
    "level_3_sort",
    "level_4_sort",
)


def export_library(session, *, source_db: str | None = None) -> dict:
    """Read ``dim_gl_account`` via *session* and return the library dict.

    Pure-ish: takes an open session, returns a plain dict (no file I/O), so it is
    callable from the generator CLI and from a DB-backed test.
    """
    from sqlalchemy import text

    rows = session.execute(
        text(
            """
            SELECT account_number_group, fiscal_year, gl_account_id, account_name,
                   level_0, level_1, level_2, level_3, level_4, l4_sub,
                   level_1_sort, level_2_sort, level_3_sort, level_4_sort
            FROM dim_gl_account
            ORDER BY gl_account_id, account_name, fiscal_year DESC
            """
        )
    ).fetchall()

    # Collapse to one entry per business key (gl_account_id, else account_name),
    # keeping the most recent fiscal year (rows arrive newest-year-first per key).
    by_key: dict[str, dict] = {}
    for r in rows:
        m = dict(r._mapping)
        gid = (m.get("gl_account_id") or "").strip()
        name = (m.get("account_name") or "").strip()
        key = gid or name
        if not key or key in by_key:
            continue
        entry = {
            "gl_account_id": gid or None,
            "account_name": name or None,
        }
        for f in _ENTRY_FIELDS:
            v = m.get(f)
            # nullable Int columns come back as int already; keep None for blanks
            if isinstance(v, str):
                v = v.strip() or None
            entry[f] = v
        by_key[key] = entry

    entries = sorted(
        by_key.values(),
        key=lambda e: (str(e.get("gl_account_id") or ""), str(e.get("account_name") or "")),
    )

    return {
        "version": LIBRARY_VERSION,
        "source_db": source_db,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entry_count": len(entries),
        "entries": entries,
    }


def _make_session(url: str | None):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    if not url:
        from app.config import settings  # type: ignore

        url = settings.database_url
    engine = create_engine(url)
    return Session(engine), url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the standard mapping library JSON.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path.")
    parser.add_argument("--url", default=None, help="SQLAlchemy URL (else from settings/env).")
    args = parser.parse_args(argv)

    session, url = _make_session(args.url)
    try:
        source_db = url.rsplit("/", 1)[-1] if url else None
        library = export_library(session, source_db=source_db)
    finally:
        session.close()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(library, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(f"[export_standard] wrote {library['entry_count']} entries to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
