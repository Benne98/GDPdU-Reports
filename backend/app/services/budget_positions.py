"""Partner-driven reporting positions for the manual-budget feature (Phase 4).

A handful of BS/PL reporting positions are planned **per partner** (debtor /
creditor) and roll up into the position total; every other position is planned
directly at position level.  This module is the single declaration of WHICH
line_codes are partner-driven, what KIND of partner drives them, and where their
ACTUALS come from (so the seed compute can profile each partner).

PURE — no DB access at import time.  ``resolve_partner_driven`` optionally takes a
live session to confirm the line_codes against ``dim_pl_structure`` (level_3
labels), but always merges with a HARD-CODED fallback so the feature works even
when the structure rows differ.  ``is_partner_driven`` / ``partner_kind_for``
operate purely on the resolved (or fallback) set.

============================================================ MAPPING (Phase 0)
Partner-driven positions confirmed against the live recon / structure mapping:

  PL  NET_SALES          (level_3 'Net sales')        → customer  (fact_sales)
  PL  COST_OF_MATERIALS  (level_3 'Cost of materials') → supplier  (fact_com)
  BS  AR                 (level_3 'Trade receivables') → customer  (fact_position_plan)
  BS  AP                 (level_3 'Trade payables')    → supplier  (fact_position_plan)

The PL pair has dedicated synthetic plan tables (fact_sales_plan /
fact_com_plan) the readers already consult; the BS pair is a STOCK (closing
balance) carried only in fact_position_plan partner rows.

============================================================ SIGN (for reference)
This module carries NO amounts — only metadata.  The single presented↔stored sign
flip lives in ``budget_service`` (PL: stored = −presented; BS asset: stored =
+presented; BS credit: stored = −presented).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Partner kinds.
CUSTOMER = "customer"
SUPPLIER = "supplier"


@dataclass(frozen=True)
class PartnerDrivenPosition:
    """Declaration of one partner-driven reporting position.

    line_code     : reporting position code in dim_pl_structure.
    statement     : 'PL' | 'BS'.
    partner_kind  : 'customer' | 'supplier' — which partner master drives it.
    level_3       : the structure level_3 label used to resolve / confirm it.
    actual_fact   : the actuals fact table the seed compute profiles the partner
                    from ('fact_sales' for customers, 'fact_com' for suppliers).
    actual_id_col : partner id column in ``actual_fact``.
    actual_value  : the positive measure column in ``actual_fact``.
    """

    line_code: str
    statement: str
    partner_kind: str
    level_3: str
    actual_fact: str
    actual_id_col: str
    actual_value: str


# --------------------------------------------------------------------------- #
# Hard-coded fallback list (Phase 0 mapping).  Used as-is when no DB session is
# supplied, and merged with the DB-resolved set otherwise.
# --------------------------------------------------------------------------- #
FALLBACK_POSITIONS: tuple[PartnerDrivenPosition, ...] = (
    PartnerDrivenPosition(
        line_code="NET_SALES", statement="PL", partner_kind=CUSTOMER,
        level_3="Net sales",
        actual_fact="fact_sales", actual_id_col="customer_id",
        actual_value="gross_sales",
    ),
    PartnerDrivenPosition(
        line_code="COST_OF_MATERIALS", statement="PL", partner_kind=SUPPLIER,
        level_3="Cost of materials",
        actual_fact="fact_com", actual_id_col="supplier_id",
        actual_value="cost_of_materials",
    ),
    PartnerDrivenPosition(
        line_code="AR", statement="BS", partner_kind=CUSTOMER,
        level_3="Trade receivables",
        actual_fact="fact_sales", actual_id_col="customer_id",
        actual_value="gross_sales",
    ),
    PartnerDrivenPosition(
        line_code="AP", statement="BS", partner_kind=SUPPLIER,
        level_3="Trade payables",
        actual_fact="fact_com", actual_id_col="supplier_id",
        actual_value="cost_of_materials",
    ),
)

# line_code → kind lookup over the fallback (the default authority).
_FALLBACK_BY_CODE: dict[str, PartnerDrivenPosition] = {
    p.line_code: p for p in FALLBACK_POSITIONS
}
# (statement, level_3) → fallback position, for DB resolution by label.
_FALLBACK_BY_LABEL: dict[tuple[str, str], PartnerDrivenPosition] = {
    (p.statement, p.level_3): p for p in FALLBACK_POSITIONS
}


def is_partner_driven(line_code: Optional[str]) -> bool:
    """True iff ``line_code`` is one of the partner-driven positions (fallback set)."""
    if not line_code:
        return False
    return str(line_code) in _FALLBACK_BY_CODE


def partner_kind_for(line_code: Optional[str]) -> Optional[str]:
    """'customer' | 'supplier' for a partner-driven line_code, else None."""
    if not line_code:
        return None
    pos = _FALLBACK_BY_CODE.get(str(line_code))
    return pos.partner_kind if pos else None


def position_for(line_code: Optional[str]) -> Optional[PartnerDrivenPosition]:
    """Return the full :class:`PartnerDrivenPosition` for a line_code, or None."""
    if not line_code:
        return None
    return _FALLBACK_BY_CODE.get(str(line_code))


def resolve_partner_driven(
    session: Any | None = None,
) -> dict[str, PartnerDrivenPosition]:
    """Resolve the partner-driven positions, optionally confirming against the DB.

    Without a session: returns the hard-coded fallback set.

    With a session: reads dim_pl_structure mapping rows and maps the configured
    level_3 labels back to their LIVE line_code (so a renamed line_code still
    resolves), then merges with the fallback.  The fallback always wins on
    partner_kind / actual source (those are stable Phase-0 facts); the DB only
    updates the live ``line_code`` for a label when it differs.

    GOLDEN-SAFETY: this is metadata only — it never reads or writes amounts, and a
    DB error degrades gracefully to the fallback.
    """
    resolved: dict[str, PartnerDrivenPosition] = dict(_FALLBACK_BY_CODE)
    if session is None:
        return resolved

    try:
        from sqlalchemy import text

        rows = session.execute(
            text(
                "SELECT line_code, level_3, row_type, kpi_code "
                "FROM dim_pl_structure "
                "WHERE row_type = 'mapping' "
                "UNION ALL "
                "SELECT line_code, level_3, row_type, kpi_code "
                "FROM dim_bs_structure "
                "WHERE row_type = 'mapping'"
            )
        ).fetchall()
    except Exception:  # noqa: BLE001 — never let resolution fail the feature
        return resolved

    for r in rows:
        line_code = (r[0] or "").strip()
        level_3 = (r[1] or "").strip()
        kpi_code = (r[3] or "")
        statement = "BS" if str(kpi_code).startswith("BS:") else "PL"
        fb = _FALLBACK_BY_LABEL.get((statement, level_3))
        if fb is None or not line_code:
            continue
        if line_code != fb.line_code:
            # Live line_code differs from the fallback label → trust the DB code
            # but keep the kind / actual-source metadata from the fallback.
            live = PartnerDrivenPosition(
                line_code=line_code,
                statement=fb.statement,
                partner_kind=fb.partner_kind,
                level_3=fb.level_3,
                actual_fact=fb.actual_fact,
                actual_id_col=fb.actual_id_col,
                actual_value=fb.actual_value,
            )
            resolved.pop(fb.line_code, None)
            resolved[line_code] = live
    return resolved
