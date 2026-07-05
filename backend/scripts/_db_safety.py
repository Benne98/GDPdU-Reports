"""Pre-flight live-DB safety guard for the OPOS loader / reconcile scripts.

These scripts DELETE + bulk-INSERT (``load_opos_subledger``), UPSERT
(``load_opos_master_dims``) or run reconcile queries (``reconcile_opos``)
against whatever DB the resolved ``DB_NAME`` points at.  ``config.py`` defaults
``DB_NAME`` to the LIVE production database ``"Finssentials"``, so an operator
who forgets ``DB_NAME=finssentials_v4`` would hit live data.

This mirrors the reset-data endpoint's Layer-2 "hard refuse" guard
(see ``app.routers.projects.LIVE_DB_NAME`` and the reset endpoint): refuse to
run against the live DB unless an explicit override flag is passed.  The
intended target is ``finssentials_v4``.
"""
from __future__ import annotations

#: The LIVE production database name.  Kept in lock-step with
#: ``app.routers.projects.LIVE_DB_NAME`` (case-insensitive exact match).
LIVE_DB_NAME = "Finssentials"


def assert_not_live_db(engine, allow_live: bool = False) -> str:
    """Refuse to proceed when ``engine`` is connected to the live DB.

    Reads ``engine.url.database`` and compares it (casefold) to
    :data:`LIVE_DB_NAME`.  On a match, raises ``SystemExit`` with a clear
    message unless ``allow_live`` is True (operator passed
    ``--i-know-this-is-live``).  Returns the resolved database name so callers
    can print it before proceeding.
    """
    dbname = (engine.url.database or "").strip()
    if dbname.casefold() == LIVE_DB_NAME.casefold():
        if allow_live:
            print(
                f"WARNING: proceeding against the LIVE production database "
                f"{dbname!r} because --i-know-this-is-live was passed."
            )
            return dbname
        raise SystemExit(
            f"REFUSING to run: connected to the LIVE production database "
            f"{dbname!r}.\n"
            f"These scripts DELETE / INSERT / UPSERT rows — the intended target "
            f"is 'finssentials_v4'.\n"
            f"Set DB_NAME=finssentials_v4 (recommended), or pass "
            f"--i-know-this-is-live to override."
        )
    return dbname
