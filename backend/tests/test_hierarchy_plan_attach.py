"""Plan attachment on hierarchy-based BS/WC statement rows."""
from __future__ import annotations

from app.services.fin_compat_pl import attach_hierarchy_plan_to_rows


def test_attach_hierarchy_plan_sums_children_and_skips_accounts():
    struct = [
        {
            "line_code": "BS_AR",
            "row_type": "mapping",
            "level_2": "Assets",
            "level_3": "Receivables",
            "level_4": "",
        },
    ]
    plan_map = {"BS_AR": {"plan_cm": 100.0, "ytd_plan": 200.0, "ytg": 50.0}}
    rows = [
        {
            "id": "bs-d0-Assets",
            "line_code": "bs-d0-Assets",
            "row_kind": "subtotal",
            "label": "Assets",
            "amounts": {"cm": 100.0},
            "drill": {"level_2": "Assets"},
            "children": [
                {
                    "id": "bs-d1-Receivables",
                    "line_code": "bs-d1-Receivables",
                    "row_kind": "line",
                    "label": "Receivables",
                    "amounts": {"cm": 100.0},
                    "drill": {"level_2": "Assets", "level_3": "Receivables"},
                    "children": [
                        {
                            "id": "bs-acc-1",
                            "line_code": "1000",
                            "row_kind": "account",
                            "label": "1000 | AR",
                            "amounts": {"cm": 100.0},
                            "children": [],
                        },
                    ],
                },
            ],
        },
    ]

    attach_hierarchy_plan_to_rows(rows, plan_map, struct)

    leaf = rows[0]["children"][0]
    assert leaf["amounts"]["plan_cm"] == 100.0
    assert leaf["amounts"]["plan_vs_actual"] == 0.0
    assert rows[0]["children"][0]["children"][0]["amounts"].get("plan_cm") is None
    assert rows[0]["amounts"]["plan_cm"] == 100.0
