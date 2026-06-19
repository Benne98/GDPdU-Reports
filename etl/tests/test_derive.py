import pandas as pd

from etl import derive as D
from etl.tests import fixtures as F


def test_classify_maps_labels_to_classes():
    lvl = pd.Series(["Net sales", "Trade payables", "Something else", None])
    out = D.classify(lvl, F.ACCOUNT_CLASSES)
    assert out.tolist() == ["revenue", "payable", "other", "other"]


def test_propagate_partners_method_a():
    lines = F.canonical_lines()
    out = D.propagate_partners(lines)

    rev = out[out["account_class"] == "revenue"]
    # both revenue lines belong to bookings with a single customer (01100)
    assert set(rev["customer_id"]) == {"01100"}
    assert (rev["link_method"] == "txn").all()

    mat = out[out["account_class"] == "material"]
    assert set(mat["supplier_id"]) == {"01200"}
    assert (mat["link_method"] == "txn").all()


def test_propagate_partners_ambiguous_stays_unlinked():
    lines = F.canonical_lines()
    # inject a second, different customer on booking 1's receivable side
    extra = lines.iloc[[0]].copy()
    extra["line_number"] = 4
    extra["booking_line_id"] = 99
    extra["customer_id"] = "01999"
    extra["amount"] = 0.0
    lines = pd.concat([lines, extra], ignore_index=True)

    out = D.propagate_partners(lines)
    booking1_rev = out[(out["journal_entry_group_number"] == "010000000001") & (out["account_class"] == "revenue")]
    assert (booking1_rev["link_method"] == "none").all()
    assert booking1_rev["customer_id"].isna().all()


def test_derive_sales_and_com_signs():
    lines = F.canonical_lines()
    sales = D.derive_sales(lines)
    com = D.derive_com(lines)
    assert sales["gross_sales"].sum() == 3000.0   # = -(-3000)
    assert com["cost_of_materials"].sum() == 500.0
    # gross_sales is the sign-flipped credit amount, always positive here
    assert (sales["gross_sales"] > 0).all()


def test_derive_ar_ap_totals():
    lines = F.canonical_lines()
    assert D.derive_ar(lines)["amount"].sum() == 3570.0
    assert D.derive_ap(lines)["amount"].sum() == -595.0


# --------------------------------------------------------------------------- #
# DF2 — derived facts are NON-EMPTY when account_class is built from the mapping
# --------------------------------------------------------------------------- #

def test_derived_facts_non_empty_with_classes_from_mapping():
    """With AccountClasses built from the DF1 mapping fixture, derive_sales/com/ar/ap
    must all be non-empty.  This proves the classification wiring is correct and
    that account_class='other' (the pre-DF2 bug) is fixed.

    Uses the 'test' source_system rules (level_3 for all classes) to match the
    synthetic fixture's level_3 labels exactly.
    """
    from etl.classify_config import OVERRIDES, build_account_classes

    mapping_df = F.canonical_account_mapping()
    rules = OVERRIDES["test"]
    classes = build_account_classes(mapping_df, rules)

    # Confirm classification built correctly
    assert len(classes.revenue) > 0, "revenue must be non-empty"
    assert len(classes.material) > 0, "material must be non-empty"
    assert len(classes.receivable) > 0, "receivable must be non-empty"
    assert len(classes.payable) > 0, "payable must be non-empty"

    # Apply classification to canonical lines
    lines = F.canonical_lines()
    # classify uses the single level_3 column (consistent with 'test' rules)
    lines["account_class"] = D.classify(lines["level_3"], classes)

    sales = D.derive_sales(lines)
    com = D.derive_com(lines)
    ar = D.derive_ar(lines)
    ap = D.derive_ap(lines)

    # All four derived DataFrames must be non-empty
    assert not sales.empty, "derive_sales returned empty — classification failed"
    assert not com.empty, "derive_com returned empty — classification failed"
    assert not ar.empty, "derive_ar returned empty — classification failed"
    assert not ap.empty, "derive_ap returned empty — classification failed"


def test_derived_facts_signs_with_classes_from_mapping():
    """Sign conventions must hold when classes come from the mapping fixture.

    gross_sales = -amount  (revenue is credit = negative amount)
    cost_of_materials = +amount  (material is debit = positive amount)
    """
    from etl.classify_config import OVERRIDES, build_account_classes

    mapping_df = F.canonical_account_mapping()
    classes = build_account_classes(mapping_df, OVERRIDES["test"])

    lines = F.canonical_lines()
    lines["account_class"] = D.classify(lines["level_3"], classes)

    sales = D.derive_sales(lines)
    com = D.derive_com(lines)

    # Totals must match the known fixture values
    assert sales["gross_sales"].sum() == 3000.0, (
        f"gross_sales total wrong: expected 3000.0, got {sales['gross_sales'].sum()}"
    )
    assert com["cost_of_materials"].sum() == 500.0, (
        f"cost_of_materials total wrong: expected 500.0, got {com['cost_of_materials'].sum()}"
    )

    # gross_sales is sign-flipped: revenue lines have negative amount, so gross_sales > 0
    assert (sales["gross_sales"] > 0).all(), "gross_sales must be positive (= -credit amount)"
    # cost_of_materials equals the debit amount directly: positive
    assert (com["cost_of_materials"] > 0).all(), "cost_of_materials must be positive (= debit amount)"
