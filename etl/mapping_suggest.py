"""Auto-suggest a chart-of-accounts hierarchy for a NEW dataset (reporting-v2 Phase 4).

Given a new dataset's accounts and a standard mapping *library* (see
``etl/mapping_library/finssentials_standard_v1.json``), propose
``level_0 … level_4`` + sort columns per account.  This is the auto-suggest source
for new clients, the same concept as the FDD bot's ``BS_/PL_Kontenmapping.xlsx``.

PURE / DB-FREE
──────────────
``suggest_account_mapping`` takes plain Python data (a list of account dicts and a
library dict) and returns plain Python data, so it is fully unit-testable without a
database.  Persisting the result (into ``dim_gl_account`` / a project override /
client-CoA path) is the caller's job.

MATCH STRATEGY (highest confidence first)
─────────────────────────────────────────
1. ``gl_account_id`` exact match     → ``match='gl_account_id'``
2. normalized ``account_name`` match → ``match='name'``
3. otherwise                         → goes to ``unmatched`` (for the editor)

Normalisation of the name key is intentionally simple and locale-tolerant
(casefold, collapse whitespace, drop punctuation) so trivial formatting
differences ("Trade receivables" vs "trade  receivables.") still match.  It never
guesses across different concepts — only an exact normalized-name hit counts.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

#: Hierarchy + sort fields a proposal carries (mirrors dim_gl_account / the
#: project-override table).  Kept here as the single source of truth so the
#: library generator and the suggest function agree on the payload shape.
PROPOSAL_FIELDS: tuple[str, ...] = (
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

#: Library top-level keys.
LIBRARY_VERSION_KEY = "version"
LIBRARY_ENTRIES_KEY = "entries"

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def normalize_name(name: Any) -> str:
    """Normalise an account name to a stable match key.

    casefold → drop punctuation → collapse internal whitespace → strip.
    Returns "" for None / blank so blank names never match each other by accident
    (callers must treat "" as "no name key").
    """
    if name is None:
        return ""
    s = str(name)
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s.casefold()


def normalize_gl_id(gl_account_id: Any) -> str:
    """Normalise a gl_account_id for exact matching.

    Strips whitespace and the Excel '.0' float artifact (e.g. '38235.0' → '38235'),
    matching ``etl.transform.normalize_token`` semantics, then casefolds so a
    library minted from one DB matches a dataset from another regardless of case.
    """
    if gl_account_id is None:
        return ""
    s = str(gl_account_id).strip()
    s = re.sub(r"\.0$", "", s)
    return s.casefold()


def _proposal_from_entry(entry: dict) -> dict:
    """Project a library entry down to exactly the PROPOSAL_FIELDS (missing → None)."""
    return {f: entry.get(f) for f in PROPOSAL_FIELDS}


def _build_indexes(library: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """Build (by_gl_id, by_name) lookup indexes from a library dict.

    The library is keyed structurally by ``entries`` → list of entries each with a
    ``gl_account_id`` and/or ``account_name`` plus the hierarchy fields.  On a
    duplicate normalized key the FIRST entry wins (deterministic; the generator
    emits a stable order) and later collisions are ignored so a noisy library can
    never produce a non-deterministic suggestion.
    """
    entries = library.get(LIBRARY_ENTRIES_KEY) or []
    by_gl_id: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        gid = normalize_gl_id(entry.get("gl_account_id"))
        if gid and gid not in by_gl_id:
            by_gl_id[gid] = entry
        nkey = normalize_name(entry.get("account_name"))
        if nkey and nkey not in by_name:
            by_name[nkey] = entry
    return by_gl_id, by_name


def suggest_account_mapping(
    accounts: Iterable[dict],
    library: dict,
) -> dict[str, list[dict]]:
    """Propose a hierarchy mapping for *accounts* from a standard *library*.

    Parameters
    ----------
    accounts : iterable of dict
        New dataset's accounts.  Each dict SHOULD carry an identity — any of
        ``account_number_group`` / ``gl_account_id`` / ``account_name``.  Identity
        keys are echoed back on each result so the caller can join the proposal
        back to its account.
    library : dict
        A loaded mapping library (the JSON produced by
        ``etl/mapping_library/export_standard.py``): ``{"version": ..., "entries":
        [ {gl_account_id, account_name, level_0..level_4, l4_sub, *_sort}, ... ]}``.

    Returns
    -------
    dict with two lists:
        ``proposals``  — one dict per matched account:
            {account_number_group, gl_account_id, account_name,
             match: 'gl_account_id' | 'name',
             level_0..level_4, l4_sub, level_1_sort..level_4_sort}
        ``unmatched``  — one dict per account with NO library hit:
            {account_number_group, gl_account_id, account_name}

    Matching is deterministic and never cross-guesses: gl_account_id exact first,
    then exact normalized-name; anything else is unmatched (for the CoA editor).
    """
    by_gl_id, by_name = _build_indexes(library)

    proposals: list[dict] = []
    unmatched: list[dict] = []

    for acc in accounts:
        ang = acc.get("account_number_group")
        gid_raw = acc.get("gl_account_id")
        name_raw = acc.get("account_name")

        identity = {
            "account_number_group": ang,
            "gl_account_id": gid_raw,
            "account_name": name_raw,
        }

        gid = normalize_gl_id(gid_raw)
        nkey = normalize_name(name_raw)

        entry = None
        match = None
        if gid and gid in by_gl_id:
            entry = by_gl_id[gid]
            match = "gl_account_id"
        elif nkey and nkey in by_name:
            entry = by_name[nkey]
            match = "name"

        if entry is None:
            unmatched.append(identity)
            continue

        proposal = {**identity, "match": match, **_proposal_from_entry(entry)}
        proposals.append(proposal)

    return {"proposals": proposals, "unmatched": unmatched}
