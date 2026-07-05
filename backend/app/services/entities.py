"""Canonical entity identity map — the ONE source of truth for the OPOS / AR-AP
subledger load and any read path that must resolve an entity.

RESOLVED CONFLICT (read this before touching entity logic)
----------------------------------------------------------
Three *independent* identifiers describe the same five legal entities and they do
NOT share an ordering — you must never derive one from the other arithmetically:

  * ``Buchungskreis`` (company code, INTEGER) — as it appears in the subledger.
  * ``entity_prefix``  (2-char, the LEFT(2) of customer_id / supplier_id / konto).
  * entity ``name``.

The historical assumption that ``entity_prefix`` 02 mapped to Calypto is WRONG.
The architect-resolved, data-verified mapping is::

    Buchungskreis  entity_prefix  name
    1000           01             Atlas
    2000           05             Calypto
    3000           02             Meridian      <-- 02 = Meridian, NOT Calypto
    4000           03             Novara
    5000           04             Venturo

This agrees with BOTH the subledger ``Entity`` column (Buchungskreis -> name) and
the master ``customer_id`` / ``supplier_id`` prefix (prefix -> name): a 100%
cross-validation join of subledger partner numbers to dim_customer/dim_supplier
resolves to a single prefix per Buchungskreis with no conflicts.

Because the two orderings differ, ALWAYS store ``buchungskreis`` explicitly on the
fact row and map the name from it; derive ``entity_prefix`` only via
:data:`BUKRS_TO_PREFIX`.  Never compute ``entity_prefix`` from ``Buchungskreis``
by arithmetic.
"""
from __future__ import annotations

# Buchungskreis (company code) -> entity name.
ENTITY_BUKRS: dict[int, str] = {
    1000: "Atlas",
    2000: "Calypto",
    3000: "Meridian",
    4000: "Novara",
    5000: "Venturo",
}

# entity_prefix (LEFT(2) of customer_id/supplier_id/konto) -> entity name.
ENTITY_PREFIX: dict[str, str] = {
    "01": "Atlas",
    "02": "Meridian",   # resolved: 02 = Meridian (NOT Calypto)
    "03": "Novara",
    "04": "Venturo",
    "05": "Calypto",
}

# Buchungskreis -> entity_prefix.  The two orderings differ; this is the ONLY
# permitted bridge between them.
BUKRS_TO_PREFIX: dict[int, str] = {
    1000: "01",
    2000: "05",
    3000: "02",
    4000: "03",
    5000: "04",
}

# Reverse bridge (entity_prefix -> Buchungskreis), derived from BUKRS_TO_PREFIX so
# the two can never drift.
PREFIX_TO_BUKRS: dict[str, int] = {v: k for k, v in BUKRS_TO_PREFIX.items()}


def entity_name_from_bukrs(bukrs: int) -> str:
    """Return the entity name for a Buchungskreis, or raise KeyError if unknown."""
    return ENTITY_BUKRS[int(bukrs)]


def entity_name_from_prefix(prefix: str) -> str:
    """Return the entity name for a 2-char entity_prefix, or raise KeyError."""
    return ENTITY_PREFIX[str(prefix).strip().zfill(2)]


def prefix_from_bukrs(bukrs: int) -> str:
    """Return the 2-char entity_prefix for a Buchungskreis, or raise KeyError.

    This is the ONLY sanctioned way to obtain an entity_prefix from a
    Buchungskreis — never derive it arithmetically (the orderings differ).
    """
    return BUKRS_TO_PREFIX[int(bukrs)]
