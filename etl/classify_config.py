"""etl/classify_config.py — Configurable account-class rules (DF2).

Each economic class is defined by:
  - ``level_column``: which hierarchy column to match against (e.g. 'level_2', 'level_1').
  - ``labels``: frozenset of label strings that map an account to this class.

Decision (§5.1, 2026-06-11): level_2 for revenue/material; level_3 for receivable/payable
(level_3 is more precise than level_1 — avoids misclassifying VAT/prepayment accounts).
German DATEV/SKR defaults; overridable per source_system via OVERRIDES dict.

Usage
-----
  from etl.classify_config import DEFAULT_RULES, OVERRIDES, build_account_classes

  rules = OVERRIDES.get(source_system, DEFAULT_RULES)
  account_classes = build_account_classes(dim_accounts_df, rules)
  # -> etl.derive.AccountClasses (revenue/material/receivable/payable as frozensets of level labels)

``build_account_classes`` extracts the *distinct labels* for each class by reading
``rules[cls].level_column`` from ``dim_accounts_df``, then constructs an
``AccountClasses`` whose sets contain those labels.  The ``classify`` function in
``etl.derive`` is then called with the *canonical GL lines'* matching level column
(which must also be present on those lines for the classify step to work).

Backward compatibility
----------------------
``etl.derive.AccountClasses`` and ``etl.derive.classify`` are unchanged.  Callers
that already build ``AccountClasses`` manually (e.g. tests) continue to work.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from etl.derive import AccountClasses


# --------------------------------------------------------------------------- #
# Rule structure
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ClassRule:
    """Rule for one economic class: which dim_gl_account column to inspect + label set."""
    level_column: str
    labels: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ClassificationRules:
    """Per-class rules.  One ``ClassRule`` per economic class recognised by ``AccountClasses``."""
    revenue: ClassRule
    material: ClassRule
    receivable: ClassRule
    payable: ClassRule


# --------------------------------------------------------------------------- #
# German DATEV / SKR03 defaults
#
# level_2 for P&L classes (revenue, material): the level_2 column in dim_gl_account
# carries the P&L grouping label in standard DATEV/SKR03 exports.
#
# level_3 for balance-sheet classes (receivable, payable): level_3 carries
# account-type-specific labels ("Forderungen aus L+L", "Verbindlichkeiten aus L+L")
# that are precise enough to distinguish trade AR/AP from VAT accounts, prepayments, etc.
# An override ('datev_skr03_l1') is provided for systems where level_3 is unreliable.
#
# Label sets are intentionally small; R3/R4 reconciliation is the correctness guard.
# --------------------------------------------------------------------------- #

DEFAULT_RULES = ClassificationRules(
    revenue=ClassRule(
        # level_2 carries the P&L grouping in standard DATEV/SKR03 exports.
        level_column="level_2",
        labels=frozenset({
            "Umsatzerlöse",
            "Umsatzerlöse aus Lieferungen und Leistungen",
            "Umsatzerlöse aus Lieferungen u. Leistungen",
            "Net sales",           # English synonym used in synthetic fixtures
            "Revenue",
        }),
    ),
    material=ClassRule(
        # level_2 carries the P&L grouping in standard DATEV/SKR03 exports.
        level_column="level_2",
        labels=frozenset({
            "Materialaufwand",
            "Aufwendungen für Roh-, Hilfs- und Betriebsstoffe",
            "Wareneinsatz",
            "Cost of materials",   # English synonym
            "Cost of goods sold",
        }),
    ),
    receivable=ClassRule(
        # level_3 is more specific than level_1 for AR: "Forderungen aus L+L"
        # distinguishes trade AR from VAT receivables, prepayments, etc.
        # Using level_1 ("Umlaufvermögen") would misclassify VAT accounts.
        level_column="level_3",
        labels=frozenset({
            "Forderungen aus Lieferungen und Leistungen",
            "Forderungen aus Lieferungen u. Leistungen",
            "Trade receivables",   # English synonym used in synthetic fixtures
        }),
    ),
    payable=ClassRule(
        # level_3 is more specific than level_1 for AP: "Verbindlichkeiten aus L+L"
        # distinguishes trade AP from VAT payables, accruals, etc.
        level_column="level_3",
        labels=frozenset({
            "Verbindlichkeiten aus Lieferungen und Leistungen",
            "Verbindlichkeiten aus Lieferungen u. Leistungen",
            "Trade payables",      # English synonym used in synthetic fixtures
        }),
    ),
)

# --------------------------------------------------------------------------- #
# Per-source-system overrides
#
# Format: source_system_string -> ClassificationRules
# Only define overrides for systems that differ from DEFAULT_RULES.
# --------------------------------------------------------------------------- #
OVERRIDES: dict[str, ClassificationRules] = {
    # Example override: use level_1 for AR/AP (broader, matches SKR03 level_1 section labels).
    # Use when level_3 labels are inconsistent in a particular source system.
    "datev_skr03_l1": ClassificationRules(
        revenue=DEFAULT_RULES.revenue,
        material=DEFAULT_RULES.material,
        receivable=ClassRule(
            level_column="level_1",
            labels=frozenset({
                "Forderungen",
                "Forderungen aus Lieferungen und Leistungen",
            }),
        ),
        payable=ClassRule(
            level_column="level_1",
            labels=frozenset({
                "Verbindlichkeiten",
                "Verbindlichkeiten aus Lieferungen und Leistungen",
            }),
        ),
    ),
    # Synthetic-test source system: uses level_3 for all classes (matches fixtures.py _L3).
    "test": ClassificationRules(
        revenue=ClassRule(
            level_column="level_3",
            labels=frozenset({"Net sales"}),
        ),
        material=ClassRule(
            level_column="level_3",
            labels=frozenset({"Cost of materials"}),
        ),
        receivable=ClassRule(
            level_column="level_3",
            labels=frozenset({"Trade receivables"}),
        ),
        payable=ClassRule(
            level_column="level_3",
            labels=frozenset({"Trade payables"}),
        ),
    ),
}


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #

def build_account_classes(
    dim_accounts_df: pd.DataFrame,
    rules: ClassificationRules,
) -> AccountClasses:
    """Derive ``AccountClasses`` from a canonical dim_gl_account DataFrame.

    For each economic class the function reads ``rules.<cls>.level_column`` from
    ``dim_accounts_df``, takes the set of distinct label values present in that
    column that also appear in ``rules.<cls>.labels``, and stores them as a
    ``frozenset`` inside the returned ``AccountClasses``.

    This means ``AccountClasses`` will contain *only the labels that actually
    appear in the uploaded mapping* (intersection with the configured label set),
    so ``classify`` never matches phantom labels.

    Parameters
    ----------
    dim_accounts_df : pd.DataFrame
        Canonical account-mapping rows (output of ``apply_account_mapping``, or
        rows fetched from ``dim_gl_account`` enriched onto canonical GL lines).
        Must contain at least the level columns referenced by ``rules``.
    rules : ClassificationRules
        Rule set to apply.  Use ``DEFAULT_RULES`` or a value from ``OVERRIDES``.

    Returns
    -------
    AccountClasses
        Populated frozensets for revenue / material / receivable / payable.
    """
    class_sets: dict[str, frozenset[str]] = {}

    for cls in ("revenue", "material", "receivable", "payable"):
        rule: ClassRule = getattr(rules, cls)
        col = rule.level_column

        if col not in dim_accounts_df.columns:
            # Column absent: no labels can match → empty frozenset (safe fallback)
            class_sets[cls] = frozenset()
            continue

        # Collect distinct non-null labels present in the mapping that are in the rule set
        present = (
            dim_accounts_df[col]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
        )
        matched = frozenset(lbl for lbl in present if lbl in rule.labels)
        class_sets[cls] = matched

    return AccountClasses(
        revenue=class_sets["revenue"],
        material=class_sets["material"],
        receivable=class_sets["receivable"],
        payable=class_sets["payable"],
    )
