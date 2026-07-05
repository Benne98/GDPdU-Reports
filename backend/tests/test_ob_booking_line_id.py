"""Regression coverage for the OB booking_line_id collision bug.

Background
----------
``_ob_bid`` (nested inside ``opening_balance_commit``) used to compute::

    _OB_BID_BASE + int(str(jegn) + str(fy)) % 10 ** 12

The ``% 10**12`` operation kept only the 12 low-order digits of the
concatenated string.  For JEGN strings like "049000011720" the high-order
prefix digits ("04" / "05" …) were shifted beyond the 12-digit window, so
the *same* account + FY in *different* entities produced an identical residue
— violating the ``fact_gl_line_booking_line_id_key`` unique constraint.

Worked example (from docs/financial-logic.md):
  pre-fix:  e04/11720/2024 → 800117202024,  e05/11720/2024 → 800117202024  (COLLISION)
  post-fix: e04/11720/2024 → 831700345255,  e05/11720/2024 → 899612369732  (distinct)

The fix hashes the full jegn|fy string with BLAKE2b-64::

    _OB_BID_BASE + (int.from_bytes(
        blake2b(f"{jegn}|{int(fy)}".encode(), digest_size=8).digest(), "big"
    ) % 100_000_000_000)

``_ob_jegn`` already encodes the entity prefix in the first two chars of the
12-char JEGN (``<EE>9<acct9>``) so distinct entities always produce distinct
digests.

Why existing tests missed the bug
----------------------------------
All 59 DB-free OB tests target the upload / parse layer (before BID minting)
or use a single entity prefix.  The 12 DB round-trip tests (skipped without
``DB_NAME=finssentials_v2``) exercise two-prefix commits (D3 / D4) but
check only row counts and ``entry_type`` — never ``booking_line_id``
uniqueness.  No test ever staged two-entity input and verified the minted BIDs.

Approach
--------
``_ob_bid`` and ``_ob_jegn`` are **nested closures** inside
``opening_balance_commit`` and are **not importable**.  We drive every
assertion through the full commit path:

1. Stage synthetic CSV files in ``UPLOAD_DIR`` (same pattern as
   ``test_ob_number_format_parse.py``).
2. Use a ``MagicMock`` session whose ``execute().fetchall()`` returns
   ``[]`` (no in-data collisions) and ``execute().fetchone()`` returns
   ``(1,)`` (metadata INSERT load_id).
3. Monkeypatch ``etl.account_fill.fill_account_rows_for_keys`` → empty
   unresolved, and ``etl.load._bulk_insert_lines`` → DataFrame capture.

This calls the ACTUAL production code.  A regression to the old formula would
make the two-entity BIDs collide and the assertion would fail.

All data is synthetic.  No real customer data.  No live DB required.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import app.routers.ingest as ing
from app.auth import User
from app.routers.ingest import UPLOAD_DIR, _OB_BID_BASE

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ADMIN = User(user_id=1, email="admin@test", display_name="Admin", is_admin=True)

#: BIDs must be strictly below the carry-forward OB base (900 billion),
#: below 2^63 (BIGINT upper limit in Postgres), and at or above the file-OB
#: band floor.
_OB_BID_UPPER = 900_000_000_000   # exclusive: carry-forward OB base
_BIGINT_MAX    = 2 ** 63 - 1


@contextmanager
def _staged(content: bytes, filename: str) -> Iterator[str]:
    """Write *content* to UPLOAD_DIR, yield the file_id, then remove the file."""
    fid = uuid.uuid4().hex[:16]
    dest = UPLOAD_DIR / f"{fid}_{filename}"
    try:
        dest.write_bytes(content)
        yield fid
    finally:
        dest.unlink(missing_ok=True)


def _ob_profile(entity: str, fy: int) -> dict:
    """Minimal OB mapping profile for (entity, fixed fy) — no posting_date needed."""
    return {
        "entity": {"mode": "fixed", "value": entity},
        "fiscal_year": {"mode": "fixed", "value": fy},
        "sign": {"mode": "signed", "amount": "Amount"},
        # No 'decimal'/'thousands' -> profile defaults to '.' (US).
        "columns": {"account_number": "Account"},
        "linking_strategy": "none",
        "entry_type": "actual",
        "source_system": "ob_bid_regression_test",
    }


def _mock_session() -> MagicMock:
    """Session mock that satisfies every DB call in opening_balance_commit.

    - ``execute(...).fetchall()`` → ``[]``   (no in-data OB collisions)
    - ``execute(...).fetchone()`` → ``(1,)`` (metadata INSERT RETURNING load_id)
    - ``rollback()`` / ``commit()``          → no-op (MagicMock default)
    """
    s = MagicMock()
    s.execute.return_value.fetchall.return_value = []
    s.execute.return_value.fetchone.return_value = (1,)
    return s


def _commit_and_capture(
    entity: str,
    fy: int,
    accounts: list[str],
) -> list[int]:
    """Stage a synthetic OB CSV for (entity, fy, accounts) and call
    ``opening_balance_commit``.  Returns the list of minted booking_line_ids.

    The real ``_ob_bid`` / ``_ob_jegn`` code runs because we drive the test
    through the actual commit function — only the DB write helpers are mocked.
    """
    captured_bids: list[int] = []

    def _capture_lines(session: object, line_rows: pd.DataFrame) -> None:
        captured_bids.extend(line_rows["booking_line_id"].astype(int).tolist())

    csv_rows = "\n".join(f"{acc},1000.00" for acc in accounts)
    csv_content = f"Account,Amount\n{csv_rows}\n".encode()

    with _staged(csv_content, "ob.csv") as fid:
        session = _mock_session()
        body = ing.OpeningBalanceCommitRequest(
            file_id=fid,
            profile=_ob_profile(entity, fy),
            scope="all",
        )
        with (
            patch(
                "etl.account_fill.fill_account_rows_for_keys",
                return_value={
                    "unresolved_no_name": set(),
                    "unresolved_no_resolution": set(),
                    "cloned": 0,
                },
            ),
            patch("etl.load._bulk_insert_entries"),
            patch("etl.load._bulk_insert_lines", side_effect=_capture_lines),
        ):
            ing.opening_balance_commit(body, session=session, _admin=_ADMIN)

    return captured_bids


# ---------------------------------------------------------------------------
# A) Multi-entity uniqueness — THE PRIMARY REGRESSION TEST
# ---------------------------------------------------------------------------

class TestMultiEntityUniqueness:
    """Two entities with the SAME account number(s) in the SAME fiscal year
    must produce DISTINCT booking_line_ids.

    This is the exact scenario that triggered the UniqueViolation pre-fix.
    """

    _ACCOUNTS = ["11720", "1200", "1600", "3000"]
    _FY = 2024
    _ENTITY_A = "04"
    _ENTITY_B = "05"

    def test_same_account_different_entity_distinct_bids(self):
        """Core regression: e04/11720/2024 and e05/11720/2024 must NOT collide.

        Pre-fix: both gave 800_117_202_024 (collision).
        Post-fix: distinct BLAKE2b digests per entity.
        """
        bids_a = _commit_and_capture(self._ENTITY_A, self._FY, ["11720"])
        bids_b = _commit_and_capture(self._ENTITY_B, self._FY, ["11720"])

        assert len(bids_a) == 1 and len(bids_b) == 1, (
            "Expected exactly one BID per single-account commit"
        )
        assert bids_a[0] != bids_b[0], (
            f"booking_line_id collision detected: "
            f"e{self._ENTITY_A}/11720/{self._FY} → {bids_a[0]}, "
            f"e{self._ENTITY_B}/11720/{self._FY} → {bids_b[0]}. "
            "Pre-fix: both were 800_117_202_024.  Fix: full-JEGN BLAKE2b hash."
        )

    def test_all_accounts_across_two_entities_no_collision(self):
        """A shared chart of accounts (4 accounts, 2 entities) produces 8 distinct BIDs."""
        bids_a = _commit_and_capture(self._ENTITY_A, self._FY, self._ACCOUNTS)
        bids_b = _commit_and_capture(self._ENTITY_B, self._FY, self._ACCOUNTS)

        all_bids = bids_a + bids_b
        assert len(all_bids) == 8, (
            f"Expected 8 BIDs (4 accounts × 2 entities); got {len(all_bids)}"
        )
        assert len(set(all_bids)) == 8, (
            f"Duplicate BIDs detected across two entities: "
            f"entity {self._ENTITY_A}: {bids_a!r}, "
            f"entity {self._ENTITY_B}: {bids_b!r}"
        )

    def test_five_entities_shared_chart_no_collision(self):
        """Broader sweep: 5 entities × 4 shared accounts × 2 FYs = 40 BIDs, all unique."""
        entities = ["01", "02", "03", "04", "05"]
        accounts = ["11720", "1200", "1600", "3000"]
        fys = [2023, 2024]

        all_bids: list[int] = []
        for ent in entities:
            for fy in fys:
                bids = _commit_and_capture(ent, fy, accounts)
                all_bids.extend(bids)

        expected = len(entities) * len(fys) * len(accounts)
        assert len(all_bids) == expected, (
            f"Expected {expected} BIDs; got {len(all_bids)}"
        )
        assert len(set(all_bids)) == expected, (
            f"Collision(s) detected in multi-entity sweep — "
            f"{expected - len(set(all_bids))} duplicate(s). "
            f"This would produce a fact_gl_line_booking_line_id_key UniqueViolation."
        )


# ---------------------------------------------------------------------------
# B) Determinism / idempotency
# ---------------------------------------------------------------------------

class TestBidDeterminism:
    """Identical (entity, account, FY) inputs must always mint the SAME BID.

    The idempotent re-commit (DELETE by JEGN, then re-INSERT) relies on this:
    the second run must produce the same BIDs so ON CONFLICT DO NOTHING holds.
    """

    def test_repeated_commit_produces_identical_bids(self):
        """Calling the commit twice for the same input gives the same BIDs."""
        bids_first  = _commit_and_capture("04", 2024, ["11720", "1200"])
        bids_second = _commit_and_capture("04", 2024, ["11720", "1200"])

        assert bids_first == bids_second, (
            f"Non-deterministic BIDs: first={bids_first!r}, second={bids_second!r}. "
            "A re-commit would fail the unique constraint instead of being a no-op."
        )

    def test_different_fy_same_entity_account_distinct_bids(self):
        """Same (entity, account) but different FY must produce different BIDs."""
        bids_2023 = _commit_and_capture("04", 2023, ["11720"])
        bids_2024 = _commit_and_capture("04", 2024, ["11720"])

        assert len(bids_2023) == 1 and len(bids_2024) == 1
        assert bids_2023[0] != bids_2024[0], (
            f"FY change did not change BID: "
            f"FY2023={bids_2023[0]}, FY2024={bids_2024[0]}"
        )


# ---------------------------------------------------------------------------
# C) In-band + BIGINT safety
# ---------------------------------------------------------------------------

class TestBidBandCompliance:
    """Every minted BID must sit inside the reserved file-OB band
    [800_000_000_000, 900_000_000_000) and be below 2^63 (BIGINT max).

    The upper bound is the carry-forward-OB base (etl.opening_balance._SYNTHETIC_BID_BASE);
    the three synthetic-OB conventions occupy DISTINCT bands (see docs/financial-logic.md).
    """

    _ENTITIES  = ["01", "02", "03", "04", "05", "10", "99"]
    _ACCOUNTS  = ["1", "1200", "11720", "999999999"]
    _FYS       = [2020, 2024, 2030]

    def test_bids_in_file_ob_band(self):
        """All BIDs fall in [_OB_BID_BASE, 900_000_000_000)."""
        for ent in self._ENTITIES:
            for fy in self._FYS:
                bids = _commit_and_capture(ent, fy, self._ACCOUNTS)
                for bid in bids:
                    assert _OB_BID_BASE <= bid < _OB_BID_UPPER, (
                        f"BID {bid} out of file-OB band [{_OB_BID_BASE}, {_OB_BID_UPPER}) "
                        f"for entity={ent!r} fy={fy}."
                    )

    def test_bids_safe_as_bigint(self):
        """All BIDs fit in a signed 64-bit integer (Postgres BIGINT)."""
        for ent in self._ENTITIES:
            for fy in self._FYS:
                bids = _commit_and_capture(ent, fy, self._ACCOUNTS)
                for bid in bids:
                    assert bid <= _BIGINT_MAX, (
                        f"BID {bid} exceeds BIGINT max {_BIGINT_MAX}."
                    )

    def test_file_ob_band_does_not_overlap_carryforward_ob_band(self):
        """Smoke-check that _OB_BID_BASE and the carry-forward base are DISTINCT bands."""
        from etl.opening_balance import _SYNTHETIC_BID_BASE as _CF_BASE
        assert _OB_BID_BASE < _CF_BASE, (
            f"file-OB base {_OB_BID_BASE} must be below carry-forward base {_CF_BASE}"
        )
        assert _OB_BID_UPPER == _CF_BASE, (
            f"file-OB upper bound {_OB_BID_UPPER} must equal carry-forward base {_CF_BASE} "
            "so the two bands are contiguous without gap or overlap."
        )


# ---------------------------------------------------------------------------
# D) JEGN distinctness (entity prefix correctly encoded) — DB-free unit layer
# ---------------------------------------------------------------------------

class TestJEGNEntityEncoding:
    """The 12-char JEGN (journal_entry_group_number) encodes the entity prefix
    in the first two characters: ``<EE>9<acct9>``.

    This is a NECESSARY precondition for BID uniqueness under the new BLAKE2b
    formula — if two entities produced the same JEGN for the same account, they
    would also produce the same BID (determinism).

    We verify JEGN distinctness by inspecting the ``journal_entry_group_number``
    column that ``opening_balance_commit`` writes into the captured line_rows
    DataFrame, driving it via the same monkeypatch approach as the BID tests.
    """

    def test_same_account_different_entities_distinct_jegns(self):
        """Entities 04 and 05 with account 11720 must produce distinct JEGNs.

        This also proves that the fix has a valid foundation: distinct JEGNs
        feed distinct blake2b inputs, guaranteeing distinct BIDs (collision
        probability negligible for realistic volumes).
        """
        captured_jegns: list[list[str]] = []

        def _capture_entries(session: object, entries: pd.DataFrame) -> None:
            # fact_gl_entry rows carry the journal_entry_group_number.
            captured_jegns.append(
                entries["journal_entry_group_number"].tolist()
            )

        csv_content = b"Account,Amount\n11720,1000.00\n"
        for entity in ("04", "05"):
            with _staged(csv_content, "ob.csv") as fid:
                session = _mock_session()
                body = ing.OpeningBalanceCommitRequest(
                    file_id=fid,
                    profile=_ob_profile(entity, 2024),
                    scope="all",
                )
                with (
                    patch(
                        "etl.account_fill.fill_account_rows_for_keys",
                        return_value={
                            "unresolved_no_name": set(),
                            "unresolved_no_resolution": set(),
                            "cloned": 0,
                        },
                    ),
                    patch("etl.load._bulk_insert_entries", side_effect=_capture_entries),
                    patch("etl.load._bulk_insert_lines"),
                ):
                    ing.opening_balance_commit(body, session=session, _admin=_ADMIN)

        assert len(captured_jegns) == 2, "Expected entries captured for both entities"
        jegn_e04 = captured_jegns[0][0]
        jegn_e05 = captured_jegns[1][0]

        # JEGN for entity 04 must start with "04".
        assert jegn_e04.startswith("04"), (
            f"JEGN for entity '04' should start with '04'; got {jegn_e04!r}"
        )
        # JEGN for entity 05 must start with "05".
        assert jegn_e05.startswith("05"), (
            f"JEGN for entity '05' should start with '05'; got {jegn_e05!r}"
        )
        # They must be distinct (necessary condition for BID uniqueness).
        assert jegn_e04 != jegn_e05, (
            f"JEGNs are identical for distinct entities: {jegn_e04!r}. "
            "The BLAKE2b BID hash is deterministic, so identical JEGNs → identical BIDs."
        )
        # The '9' file-OB discriminator must be at char 3 (index 2).
        assert jegn_e04[2] == "9", (
            f"JEGN char-3 must be '9' (file-OB sentinel); got {jegn_e04!r}"
        )
        assert jegn_e05[2] == "9", (
            f"JEGN char-3 must be '9' (file-OB sentinel); got {jegn_e05!r}"
        )
