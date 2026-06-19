"""Shared narrative-analysis engine for the GDPdU legacy compat layer.

This module ports the *architecture* of the legacy statement-specific narrative
pipelines (``services/pl_narrative`` and ``services/bs_narrative`` in the sibling
repo) to the GL-only GDPdU schema, while staying **generic over a statement
tree**.  The P&L (``fin_compat_narrative.build_pl_narrative``) and the balance
sheet (``fin_compat_bs.build_bs_narrative``) both compose the same stages here and
supply their own statement-specific phrasing through :class:`ProseContext`.

Analysis stages (mirror the legacy pipeline_v2):

  1. FACTS      — flatten the statement tree to candidate *mapping* lines and
                  compute, per line: the displayed movement (cm − pm), the
                  favourable-signed movement (the statement's already
                  invert-applied ``deltas.mom``), YoY, share-of-base, the MoM %,
                  *hidden netting* (parent ~flat while children material) and GL
                  account / booking concentration (injected by the caller via a
                  detail callback that reuses ``build_*_line_detail``).
  2. SELECTION  — materiality gates + a magnitude/relevance/concentration score;
                  pick the top-N drivers (``MIN_BULLETS`` .. ``MAX_BULLETS_CAP``),
                  emitted in table order like the legacy.
  3. PROSE      — contextual sentence builders that weave the facts into a bullet
                  (MoM clause → share-of-base → GL concentration → hidden netting
                  → YoY), with statement-specific anchor phrasing.

=== SIGN CONVENTIONS (authoritative for this layer) ===

GDPdU statement rows arrive **presented** and the row ``deltas`` are **already
invert-applied** (see ``fin_compat_pl._build_rows``: ``deltas = _deltas(am, inv)``)
and, for the balance sheet, **already display-flipped** for the credit side
(``fin_compat_bs._flip_row_tree`` negates both ``amounts`` and ``deltas``).
Therefore this module NEVER re-applies ``invert_delta``.  It distinguishes two
movement signals per line:

  * ``display_mom``   = ``amounts.cm − amounts.pm`` — the raw movement of the
                       number shown in the table.  Drives the verb (rose/fell)
                       and the magnitude (€Xk) so prose never claims the wrong
                       direction.
  * ``favorable_mom`` = ``deltas.mom`` — the favourable-signed movement (cost
                       lines already negated upstream).  Drives **P&L tone** only.

For the balance sheet ``deltas.mom == display_mom`` (invert=False, both flipped),
so BS tone is purely directional (a balance going up reads "positive"), matching
the legacy ``bs_narrative`` behaviour.

All statement ``amounts``/``deltas`` are in **EUR**.  ``build_*_line_detail``
account/booking figures are in **kEUR**; concentration shares are therefore
computed entirely within the detail payload's own (kEUR) units, so the ratio is
unit-free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ===========================================================================
# Thresholds (EUR unless noted) — the GDPdU analogues of the legacy
# pl_narrative/thresholds.py + bs_narrative/thresholds.py constants.
# ===========================================================================
MIN_BULLETS = 2          # GL-only data can be sparse → do not pad with noise
MAX_BULLETS_CAP = 6
TARGET_BULLETS = 5

MOM_FLOOR_EUR = 30_000.0           # absolute MoM materiality floor
SIZE_FLOOR_EUR = 50_000.0          # absolute size (cm) floor
SIZE_PCT_OF_BASE = 0.02            # a line >= 2% of the base is "large"
MOM_PCT_OF_CM = 0.03               # MoM >= 3% of the line's own cm is material
YOY_REL_FACTOR = 0.8               # YoY floor relative to the MoM floor

# GL concentration (shares are fractions of the line's |delta|, 0..1)
ACCOUNT_CONCENTRATION_PCT = 0.50   # one account drives >= 50% of the move
BOOKING_SHARE_PCT = 0.25           # one posting drives >= 25% of the move

# Hidden netting: a child whose |MoM| is material while the parent is ~flat.
NETTING_RATIO = 0.35               # parent_mom < child_mom * 0.35  → netting
NETTING_CHILD_FLOOR_EUR = 5_000.0  # ignore near-zero children

# Score weights (magnitude in EUR → ~0..100 band)
_W_MOM = 0.0005
_W_YOY = 0.0003
_W_REL = 50.0
_BONUS_ACCOUNT = 10.0
_BONUS_BOOKING = 8.0
_BONUS_NETTING = 12.0


def clamp_cap(
    max_bullets: Optional[int],
    *,
    target: int = TARGET_BULLETS,
    lo: int = MIN_BULLETS,
    hi: int = MAX_BULLETS_CAP,
) -> int:
    """Clamp a requested bullet cap into ``[lo, hi]`` (legacy MIN/MAX policy).

    Worked example: ``clamp_cap(None) == 5``; ``clamp_cap(99) == 6``;
    ``clamp_cap(1) == 2``.  Edge: ``clamp_cap(0) == lo``.
    """
    cap = target if max_bullets is None else int(max_bullets)
    return max(lo, min(cap, hi))


# ===========================================================================
# Formatting
# ===========================================================================

def fmt_keur(eur: float) -> str:
    """EUR → compact magnitude string: ``€50k`` / ``€1.2m`` (sign preserved).

    Worked example: 50_000 → '€50k'; -1_250_000 → '€-1.2m'; 0 → '€0k'.
    """
    keur = eur / 1000.0
    if abs(keur) >= 1000:
        return f"€{keur / 1000:.1f}m"
    return f"€{keur:.0f}k"


def fmt_keur_signed(eur: float) -> str:
    """Signed delta string: ``+€50k`` / ``-€1.2m``.

    Worked example: 50_000 → '+€50k'; -30_000 → '-€30k'; 0 → '+€0k'.
    """
    sign = "+" if eur >= 0 else "-"
    a = abs(eur) / 1000.0
    if a >= 1000:
        return f"{sign}€{a / 1000:.1f}m"
    return f"{sign}€{a:.0f}k"


def cap_first(s: str) -> str:
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else s


def lower_first(s: str) -> str:
    s = (s or "").strip()
    if not s or s[0] in ("∆", "Δ"):
        return s
    return s[:1].lower() + s[1:] if len(s) > 1 else s.lower()


def _sanitize_account_name(name: str) -> str:
    return re.sub(r"\s*\|\s*", " ", (name or "").strip())


def _account_number(gl_account_id: Optional[str], account_name: str) -> str:
    raw = (gl_account_id or "").strip()
    if raw.isdigit():
        return raw
    m = re.search(r"(\d{4,})", account_name or "")
    return m.group(1) if m else raw


def format_account(account_name: Optional[str], gl_account_id: Optional[str] = None) -> str:
    """Report-style account reference: ``account 840001 *Erl-SR Bauunternehmen*``.

    Mirrors the legacy ``prose_style.format_account_prose`` (markdown italics for
    the frontend).  Edge: name-only or number-only inputs degrade gracefully.
    """
    name = _sanitize_account_name(account_name or "")
    num = _account_number(gl_account_id, name)
    if name and num and name != (gl_account_id or ""):
        return f"account {num} *{name}*"
    if name:
        return f"account *{name}*"
    if num:
        return f"account {num}"
    return "one account"


# ===========================================================================
# Stage 1 — FACTS
# ===========================================================================

def flatten_candidate_lines(
    rows: list[dict],
    *,
    aggregate_codes: frozenset[str] = frozenset(),
) -> list[dict]:
    """Depth-first list of *mapping* lines in table order (leaves, not sub-detail).

    A ``row_kind == 'line'`` row is a candidate leaf: it is collected and its own
    children are NOT collected as further candidates (they are kept on the row for
    netting / sub-line analysis).  Container rows (title/subtotal) are descended
    into.  ``aggregate_codes`` excludes any line that is really an aggregate.

    Edge: rows without ``amounts``/``deltas`` are skipped (never bulletable).
    """
    out: list[dict] = []

    def walk(rs: list[dict]) -> None:
        for r in rs:
            lc = r.get("line_code") or ""
            if (
                r.get("row_kind") == "line"
                and r.get("amounts")
                and r.get("deltas")
                and lc not in aggregate_codes
            ):
                out.append(r)
            else:
                walk(r.get("children") or [])

    walk(rows)
    return out


def _descendant_lines(row: dict) -> list[dict]:
    """All descendant rows (children + accounts) that carry amounts & deltas."""
    out: list[dict] = []

    def walk(rs: list[dict]) -> None:
        for r in rs:
            if r.get("amounts") and r.get("deltas"):
                out.append(r)
            walk(r.get("children") or [])
            walk(r.get("accounts") or [])

    walk(row.get("children") or [])
    walk(row.get("accounts") or [])
    return out


def compute_line_facts(row: dict, base_cm: float) -> dict[str, Any]:
    """Per-line fact dict.

    Formulae:
        display_mom   = amounts.cm − amounts.pm
        favorable_mom = deltas.mom            (already invert-applied upstream)
        display_yoy   = amounts.cm − amounts.py_cm
        share_of_base = |amounts.cm| / max(|base_cm|, 1)
        pct_mom       = display_mom / |amounts.pm| * 100   (None if |pm| < 1)

    Worked example (revenue line, base = revenue): cm=520k, pm=500k, py_cm=470k,
    base_cm=520k → display_mom=+20k, display_yoy=+50k, share_of_base=1.0,
    pct_mom = 20/500*100 = +4.0%.

    Edge cases: pm≈0 → pct_mom None (no div-by-zero); base_cm≈0 → share uses the
    floor of 1.0 (share ≈ 0).
    """
    am = row.get("amounts") or {}
    d = row.get("deltas") or {}
    cm = float(am.get("cm") or 0)
    pm = float(am.get("pm") or 0)
    py_cm = float(am.get("py_cm") or 0)
    display_mom = cm - pm
    display_yoy = cm - py_cm
    favorable_mom = float(d.get("mom") or 0)
    base = abs(float(base_cm or 0)) or 1.0
    share = abs(cm) / base
    pct_mom = (display_mom / abs(pm) * 100.0) if abs(pm) >= 1.0 else None
    return {
        "line_code": row.get("line_code") or "",
        "label": row.get("label") or row.get("line_code") or "",
        "kpi_code": row.get("kpi_code") or "",
        "cm": cm,
        "pm": pm,
        "py_cm": py_cm,
        "display_mom": display_mom,
        "display_yoy": display_yoy,
        "favorable_mom": favorable_mom,
        "share_of_base": share,
        "pct_mom": pct_mom,
        "row": row,
        "hidden_netting": [],
        "concentration": {},
        "score": 0.0,
        "reasons": [],
        "material": False,
    }


def detect_hidden_netting(
    row: dict,
    *,
    child_floor: float = NETTING_CHILD_FLOOR_EUR,
    ratio: float = NETTING_RATIO,
) -> list[dict[str, Any]]:
    """Flag children with a material MoM while the parent line is ~flat.

    Rule (mirrors legacy ``_detect_subline_netting``):
        parent_mom = |parent.deltas.mom|
        for each descendant child c with |c.deltas.mom| >= child_floor:
            if parent_mom < |c.deltas.mom| * ratio:  → hidden netting

    Worked example: parent MoM = +5k; children A = +90k, B = −85k. parent_mom=5k,
    child A: 5k < 90k*0.35 (31.5k) → flagged; child B: 5k < 85k*0.35 → flagged.
    The line looks calm but is hiding two offsetting €~90k moves.

    Edge cases: no children → []; children below ``child_floor`` ignored; a parent
    that genuinely moved a lot (parent_mom large) flags nothing.
    """
    parent_mom = abs(float((row.get("deltas") or {}).get("mom") or 0))
    flagged: list[dict[str, Any]] = []
    for ch in _descendant_lines(row):
        cmom = float((ch.get("deltas") or {}).get("mom") or 0)
        if abs(cmom) < child_floor:
            continue
        if parent_mom < abs(cmom) * ratio:
            flagged.append({
                "line_code": ch.get("line_code") or "",
                "label": ch.get("label") or "",
                "mom": cmom,
                "hidden_netting": True,
            })
    flagged.sort(key=lambda x: abs(x["mom"]), reverse=True)
    return flagged


def gl_concentration_from_detail(payload: Optional[dict], *, sign: float = 1.0) -> dict[str, Any]:
    """GL account / booking concentration for a line, from a line-detail payload.

    The GDPdU ``build_*_line_detail`` returns ``accounts`` (kEUR ``balance_cm`` /
    ``balance_pm`` / ``delta``) and ``top_bookings`` (kEUR ``amount``, sorted by
    |amount| desc).  This reproduces the legacy ``gl_insights`` fields *from those*
    (the GL-only schema does not precompute ``outlier_facts``).

    ``sign`` (+1 / -1) re-orients the raw stored detail signs to the statement's
    *display* sign.  The balance sheet stores credit-side balances negative but
    presents them positive (``fin_compat_bs._flip_row_tree``); passing ``sign=-1``
    for a credit-side line makes the reported account/booking deltas read in the
    same direction as the bullet (so a growing payable shows ``+€Yk``).  Shares are
    sign-invariant (computed on magnitudes).

    Formulae (all in kEUR, so the ratios are unit-free):
        line_delta_keur = Σ account.delta
        line_cm_keur    = Σ |account.balance_cm|
        base            = |line_delta_keur| if non-trivial else line_cm_keur
        top_account     = argmax_a |a.delta|     (fallback: argmax_a |a.balance_cm|)
        top_account_share = min(|top.delta| / base, 1.0)
        booking_share     = min(|largest_booking.amount| / base, 1.0)

    Worked example: accounts = [A: delta +80, bal 300], [B: delta -10, bal 50];
    line_delta=70, base=70, top=A, share = 80/70 capped to 1.0 (offsetting → ≥100%
    is clamped).  largest booking +60 → booking_share = 60/70 = 0.86.

    Edge cases: empty payload/accounts → {}; line_delta≈0 → fall back to balance
    share; shares clamped to ≤ 1.0 so prose never prints "180%".
    """
    if not payload:
        return {}
    accounts = list(payload.get("accounts") or [])
    bookings = list(payload.get("top_bookings") or [])
    if not accounts and not bookings:
        return {}

    line_delta_keur = sum(float(a.get("delta") or 0) for a in accounts)
    line_cm_keur = sum(abs(float(a.get("balance_cm") or 0)) for a in accounts)
    base = abs(line_delta_keur)
    if base < 1e-9:
        base = line_cm_keur

    by_delta = sorted(accounts, key=lambda a: abs(float(a.get("delta") or 0)), reverse=True)
    if by_delta and abs(float(by_delta[0].get("delta") or 0)) < 1e-9:
        by_delta = sorted(accounts, key=lambda a: abs(float(a.get("balance_cm") or 0)), reverse=True)
    top = by_delta[0] if by_delta else {}
    top_delta = float(top.get("delta") or 0)
    top_bal = float(top.get("balance_cm") or 0)
    top_share = min(abs(top_delta) / base, 1.0) if base > 1e-9 else 0.0

    driver_accounts: list[dict[str, Any]] = []
    for a in by_delta[:3]:
        dlt = float(a.get("delta") or 0)
        driver_accounts.append({
            "account_name": a.get("account_name") or a.get("gl_account_id"),
            "gl_account_id": a.get("gl_account_id"),
            "delta_keur": round(dlt * sign, 2),
            "balance_cm_keur": round(float(a.get("balance_cm") or 0) * sign, 2),
            "share": min(abs(dlt) / base, 1.0) if base > 1e-9 else 0.0,
        })

    largest = bookings[0] if bookings else None
    booking_amount = float(largest.get("amount") or 0) if largest else 0.0
    booking_ref = ""
    if largest:
        booking_ref = (largest.get("line_note") or largest.get("reference") or "").strip()
    booking_share = min(abs(booking_amount) / base, 1.0) if base > 1e-9 else 0.0

    return {
        "top_account_name": top.get("account_name") or top.get("gl_account_id") or "",
        "top_account_gl_id": top.get("gl_account_id"),
        "top_account_delta_keur": round(top_delta * sign, 2),
        "top_account_balance_keur": round(top_bal * sign, 2),
        "top_account_share": round(top_share, 4),
        "driver_accounts": driver_accounts,
        "largest_booking_ref": _sanitize_account_name(booking_ref)[:120],
        "largest_booking_amount_keur": round(booking_amount * sign, 2),
        "booking_share": round(booking_share, 4),
        "line_delta_keur": round(line_delta_keur * sign, 2),
        "line_cm_keur": round(line_cm_keur, 2),
    }


# ===========================================================================
# Stage 2 — SELECTION / SCORING
# ===========================================================================

def is_material(facts: dict[str, Any], *, base_cm: float, peer_mom_sum: float) -> tuple[bool, list[str]]:
    """Materiality gate (returns (eligible, reasons)).

    A line is eligible if ANY of:
        movement   : |display_mom| >= max(MOM_FLOOR_EUR, MOM_PCT_OF_CM*|cm|)
        size       : |cm| >= max(SIZE_FLOOR_EUR, SIZE_PCT_OF_BASE*|base|) AND it moved
        yoy        : |display_yoy| >= max(MOM_FLOOR_EUR, MOM_PCT_OF_CM*|cm|*YOY_REL)
        peer_rank  : |display_mom| >= 20% of Σ peer |display_mom|
        booking    : a single posting drives >= BOOKING_SHARE_PCT of the move
        hidden_netting : the line hides offsetting children

    Worked example: revenue cm=520k, base=520k, display_mom=+20k. movement?
    20k < max(30k, 3%*520k=15.6k)=30k → no. size? 520k>=max(50k,2%*520k=10.4k) and
    moved → yes ("size"). Eligible.

    Edge cases: a big but perfectly flat line (no mom/yoy) is NOT eligible (a key-
    drivers narrative explains *movers*); zero base → size gate uses the EUR floor.
    """
    cm = abs(facts["cm"])
    mom = abs(facts["display_mom"])
    yoy = abs(facts["display_yoy"])
    base = abs(float(base_cm or 0))
    conc = facts.get("concentration") or {}
    reasons: list[str] = []

    if mom >= max(MOM_FLOOR_EUR, MOM_PCT_OF_CM * max(cm, 1.0)):
        reasons.append("movement")
    if cm >= max(SIZE_FLOOR_EUR, SIZE_PCT_OF_BASE * base) and (mom > 0.5 or yoy > 0.5):
        reasons.append("size")
    if yoy >= max(MOM_FLOOR_EUR, MOM_PCT_OF_CM * max(cm, 1.0) * YOY_REL_FACTOR):
        reasons.append("yoy")
    if peer_mom_sum > MOM_FLOOR_EUR and mom >= 0.20 * peer_mom_sum:
        reasons.append("peer_rank")
    if conc.get("booking_share", 0) >= BOOKING_SHARE_PCT and mom >= MOM_FLOOR_EUR * 0.5:
        reasons.append("booking_anomaly")
    if facts.get("hidden_netting"):
        reasons.append("hidden_netting")

    return (len(reasons) > 0, reasons)


def score_line(
    facts: dict[str, Any],
    *,
    base_cm: float,
    anchor_boost: float = 0.0,
) -> float:
    """Relevance score (higher = more newsworthy).

    Formula:
        score = |display_mom|*_W_MOM + |display_yoy|*_W_YOY
              + (|cm|/|base|)*_W_REL
              + bonus(account concentration) + bonus(booking) + bonus(netting)
              + anchor_boost
    The anchor_boost lets a statement promote its headline line (e.g. revenue).

    Worked example: revenue cm=520k base=520k mom=20k yoy=50k, anchor_boost=30 →
    20000*0.0005 + 50000*0.0003 + 1.0*50 + 30 = 10 + 15 + 50 + 30 = 105.0.

    Edge: base≈0 → relative term 0 (no div-by-zero).
    """
    cm = abs(facts["cm"])
    mom = abs(facts["display_mom"])
    yoy = abs(facts["display_yoy"])
    base = abs(float(base_cm or 0))
    rel = (cm / base) * _W_REL if base > 1e-9 else 0.0
    score = mom * _W_MOM + yoy * _W_YOY + rel + anchor_boost
    conc = facts.get("concentration") or {}
    if conc.get("top_account_share", 0) >= ACCOUNT_CONCENTRATION_PCT:
        score += _BONUS_ACCOUNT
    if conc.get("booking_share", 0) >= BOOKING_SHARE_PCT:
        score += _BONUS_BOOKING
    if facts.get("hidden_netting"):
        score += _BONUS_NETTING
    return score


def select_drivers(
    candidate_facts: list[dict[str, Any]],
    cap: int,
    *,
    min_bullets: int = MIN_BULLETS,
) -> list[dict[str, Any]]:
    """Pick the bullet drivers: top-``cap`` by score, emitted in **table order**.

    Mirrors the legacy ``select_bullets_v2``: keep the highest-scoring eligible
    lines but output them in display order (reads like a statement walk).  If
    fewer than ``min(min_bullets, len(candidates))`` lines pass the materiality
    gate, pad with the largest |display_mom| movers so the narrative is never
    emptier than the data allows.

    Edge cases: no candidates → []; all immaterial → top |display_mom| up to
    ``min_bullets``.
    """
    material = [c for c in candidate_facts if c.get("material")]
    if len(material) <= cap:
        chosen = list(material)
    else:
        by_score = sorted(material, key=lambda c: c["score"], reverse=True)[:cap]
        keep = {id(c) for c in by_score}
        chosen = [c for c in material if id(c) in keep]

    floor = min(min_bullets, len(candidate_facts))
    if len(chosen) < floor:
        chosen_ids = {id(c) for c in chosen}
        extras = sorted(
            (c for c in candidate_facts if id(c) not in chosen_ids),
            key=lambda c: abs(c["display_mom"]),
            reverse=True,
        )
        for c in extras:
            if len(chosen) >= floor:
                break
            chosen.append(c)
        # restore table order
        order = {id(c): i for i, c in enumerate(candidate_facts)}
        chosen.sort(key=lambda c: order.get(id(c), 0))
    return chosen[:cap]


# ===========================================================================
# Stage 3 — PROSE
# ===========================================================================

@dataclass
class ProseContext:
    """Statement-specific phrasing knobs shared by the generic bullet builder."""

    statement_kind: str            # "pl" | "bs"
    period_label: str              # current column label, e.g. "Jun25"
    prior_label: str               # prior month label, e.g. "May25"
    base_label: str                # "revenue" | "total assets"
    base_cm: float                 # base magnitude (EUR) for share-of-base
    balance_style: bool            # True → point-in-time (BS); False → flow (P&L)
    tone_mode: str = "favorable"   # "favorable" (P&L) | "directional" (BS)
    share_floor: float = 0.10      # min share-of-base before it is mentioned
    movement_noun: str = "the movement"
    period_grain: str = "month"    # month | week | year — prior-period phrasing
    children: list = field(default_factory=list)


def compute_tone(facts: dict[str, Any], ctx: ProseContext) -> str:
    """Bullet tone.

    P&L ("favorable"): sign of ``favorable_mom`` (cost lines pre-negated upstream).
    BS  ("directional"): sign of ``display_mom`` (a balance going up reads positive,
    matching the legacy bs_narrative).
    """
    val = facts["favorable_mom"] if ctx.tone_mode == "favorable" else facts["display_mom"]
    if val > 1e-3:
        return "positive"
    if val < -1e-3:
        return "negative"
    return "neutral"


def _pct_clause(facts: dict[str, Any]) -> str:
    pct = facts.get("pct_mom")
    if pct is None or abs(pct) < 0.05:
        return ""
    sign = "+" if pct >= 0 else "-"
    return f" ({sign}{abs(pct):.0f}%)"


def gl_concentration_sentence(conc: dict[str, Any], ctx: ProseContext) -> str:
    """Sentence describing which GL account / posting drove the line's move.

    Tiered on the top account's share of the move:
        >= 50%  → "concentrated in {account} (XX% of the movement, +€Yk)"
        >= 20%  → "largest contributor is {account} (XX% of the movement)"
        else    → "largest single account is {account}"
    A dominant single posting (booking_share >= 25%) is appended.
    """
    name = conc.get("top_account_name")
    if not name:
        return ""
    acct = format_account(name, conc.get("top_account_gl_id"))
    share = float(conc.get("top_account_share") or 0)
    delta_keur = float(conc.get("top_account_delta_keur") or 0)
    noun = ctx.movement_noun
    if share >= ACCOUNT_CONCENTRATION_PCT:
        sentence = (
            f" The move is concentrated in {acct} "
            f"({share * 100:.0f}% of {noun}, {fmt_keur_signed(delta_keur * 1000)})."
        )
    elif share >= 0.20:
        sentence = f" The largest contributor is {acct} ({share * 100:.0f}% of {noun})."
    else:
        sentence = f" The largest single account is {acct}."

    bshare = float(conc.get("booking_share") or 0)
    bref = conc.get("largest_booking_ref")
    if bshare >= BOOKING_SHARE_PCT and bref:
        bamt = float(conc.get("largest_booking_amount_keur") or 0)
        sentence += (
            f" A single posting — {bref} ({fmt_keur_signed(bamt * 1000)}, "
            f"{bshare * 100:.0f}% of {noun}) — stands out."
        )
    return sentence


def subline_leader_sentence(row: Optional[dict], parent_mom: float, ctx: ProseContext) -> str:
    """Name the largest child contributors when the parent line moved materially."""
    if not row or abs(parent_mom) < MOM_FLOOR_EUR * 0.5:
        return ""
    leaders: list[dict[str, Any]] = []
    for ch in _descendant_lines(row):
        cmom = float((ch.get("deltas") or {}).get("mom") or 0)
        if abs(cmom) < MOM_FLOOR_EUR * 0.15:
            continue
        leaders.append({"label": ch.get("label") or "", "mom": cmom})
    if not leaders:
        return ""
    leaders.sort(key=lambda x: abs(x["mom"]), reverse=True)
    lead = leaders[0]
    share = abs(lead["mom"]) / abs(parent_mom) if abs(parent_mom) > 1e-6 else 0.0
    if share < 0.15:
        return ""
    noun = ctx.movement_noun
    sentence = (
        f" The move is led by {lower_first(lead['label'])} "
        f"({fmt_keur_signed(lead['mom'])}, {share * 100:.0f}% of {noun})"
    )
    if len(leaders) >= 2:
        sec = leaders[1]
        share2 = abs(sec["mom"]) / abs(parent_mom) if abs(parent_mom) > 1e-6 else 0.0
        if share2 >= 0.10:
            sentence += (
                f"; a secondary contributor is {lower_first(sec['label'])} "
                f"({fmt_keur_signed(sec['mom'])}, {share2 * 100:.0f}% of {noun})"
            )
    return sentence + "."


def hidden_netting_sentence(flagged: list[dict], ctx: ProseContext) -> str:
    """Sentence explaining offsetting sub-line moves behind a ~flat parent."""
    if not flagged:
        return ""
    a = flagged[0]
    bit = f"{lower_first(a.get('label') or '')} ({fmt_keur_signed(float(a.get('mom') or 0))})"
    if len(flagged) >= 2:
        b = flagged[1]
        bit2 = f"{lower_first(b.get('label') or '')} ({fmt_keur_signed(float(b.get('mom') or 0))})"
        return (
            f" The line looks calm, but it nets off larger sub-line moves — "
            f"{bit} against {bit2}."
        )
    return f" The headline line is broadly flat but masks a sizeable {bit} underneath."


def _cw_display(label: str) -> str:
    """Normalise ISO week labels for narrative prose (CW not KW)."""
    if label.upper().startswith("KW"):
        return "CW" + label[2:]
    return label


def _balance_move_phrase(ctx: ProseContext, dmom: float) -> str:
    """How a BS balance moved vs the prior period (week-aware)."""
    updown = "up" if dmom >= 0 else "down"
    amt = fmt_keur(abs(dmom))
    if ctx.period_grain == "week":
        prior = _cw_display(ctx.prior_label)
        return f"{updown} {amt} compared to {prior}"
    return f"{updown} {amt} from the {ctx.prior_label} month-end"


def build_bullet(facts: dict[str, Any], ctx: ProseContext) -> tuple[str, str]:
    """Assemble one bullet's text and tone from the facts.

    Order: opening (flow or point-in-time) → share-of-base → GL concentration →
    hidden netting → YoY.  Returns ``(text, tone)``.
    """
    label = facts["label"] or facts["line_code"]
    cm = facts["cm"]
    dmom = facts["display_mom"]
    yoy = facts["display_yoy"]
    parts: list[str] = []

    if ctx.balance_style:
        if abs(dmom) < MOM_FLOOR_EUR * 0.25:
            parts.append(f"{cap_first(label)} stood at {fmt_keur(cm)} as of {ctx.period_label}.")
        else:
            parts.append(
                f"{cap_first(label)} stood at {fmt_keur(cm)} as of {ctx.period_label}, "
                f"{_balance_move_phrase(ctx, dmom)}{_pct_clause(facts)}."
            )
    else:
        if abs(dmom) < 0.5:
            parts.append(
                f"{cap_first(label)} was broadly flat at {fmt_keur(cm)} versus {ctx.prior_label}."
            )
        else:
            verb = "rose" if dmom > 0 else "fell"
            parts.append(
                f"{cap_first(label)} {verb} {fmt_keur(abs(dmom))} versus {ctx.prior_label}"
                f"{_pct_clause(facts)}, to {fmt_keur(cm)}."
            )

    share = float(facts.get("share_of_base") or 0)
    if share >= ctx.share_floor and facts["line_code"] != "__ANCHOR__":
        parts.append(f" It is {share * 100:.0f}% of {ctx.base_label}.")

    row = facts.get("row")
    flagged = facts.get("hidden_netting") or []
    if not flagged and row:
        parts.append(subline_leader_sentence(row, dmom, ctx))

    parts.append(gl_concentration_sentence(facts.get("concentration") or {}, ctx))
    parts.append(hidden_netting_sentence(flagged, ctx))

    if abs(yoy) >= MOM_FLOOR_EUR:
        yd = "up" if yoy > 0 else "down"
        parts.append(f" Year-on-year it is {yd} {fmt_keur(abs(yoy))}.")

    text = "".join(p for p in parts if p)
    return text, compute_tone(facts, ctx)


def build_bullets(
    drivers: list[dict[str, Any]],
    ctx: ProseContext,
) -> list[dict[str, Any]]:
    """Render selected drivers into the frontend bullet shape.

    Each bullet: ``index, line_code, label, text, tone, deep_links, facts``.
    ``facts`` carries the numeric provenance the frontend may surface.
    """
    bullets: list[dict[str, Any]] = []
    for i, f in enumerate(drivers):
        text, tone = build_bullet(f, ctx)
        conc = f.get("concentration") or {}
        bullets.append({
            "index": i,
            "line_code": f["line_code"],
            "label": f["label"],
            "text": text,
            "tone": tone,
            "deep_links": [],
            "facts": {
                "cm": round(f["cm"], 2),
                "mom": round(f["display_mom"], 2),
                "yoy": round(f["display_yoy"], 2),
                "share_of_base": round(float(f.get("share_of_base") or 0), 4),
                "top_account": conc.get("top_account_name") or None,
                "top_account_share": conc.get("top_account_share"),
                "hidden_netting": bool(f.get("hidden_netting")),
                "reasons": f.get("reasons") or [],
            },
        })
    return bullets


# ===========================================================================
# Orchestration helper
# ===========================================================================

def analyze_statement(
    rows: list[dict],
    *,
    base_cm: float,
    cap: int,
    aggregate_codes: frozenset[str] = frozenset(),
    anchor_boost_fn=None,
    gl_detail_fn=None,
    gl_sign_fn=None,
    gl_detail_floor_eur: float = MOM_FLOOR_EUR,
) -> list[dict[str, Any]]:
    """Run facts → enrich → score → select and return the chosen driver facts.

    ``anchor_boost_fn(line_code) -> float`` promotes statement-specific anchors.
    ``gl_detail_fn(line_code, signed_mom_eur) -> payload|None`` supplies the line
    detail used for GL concentration; it is only called for the biggest movers
    (|display_mom| >= ``gl_detail_floor_eur``) to bound DB work, mirroring the
    legacy ``flagged_accounts`` loop.  ``gl_sign_fn(line_code) -> float`` (+1/-1)
    re-orients the raw detail sign to display sign (BS credit side → -1).  All
    callbacks are optional (kept DB-free for unit tests).
    """
    candidates = flatten_candidate_lines(rows, aggregate_codes=aggregate_codes)
    facts = [compute_line_facts(r, base_cm) for r in candidates]

    # hidden netting (pure, from the tree)
    for f, r in zip(facts, candidates):
        f["hidden_netting"] = detect_hidden_netting(r)

    # GL concentration for the biggest movers only
    if gl_detail_fn is not None:
        for f in facts:
            if abs(f["display_mom"]) < gl_detail_floor_eur:
                continue
            try:
                payload = gl_detail_fn(f["line_code"], f["favorable_mom"])
            except Exception:
                payload = None
            if payload:
                sign = gl_sign_fn(f["line_code"]) if gl_sign_fn else 1.0
                f["concentration"] = gl_concentration_from_detail(payload, sign=sign)

    peer_mom_sum = sum(abs(f["display_mom"]) for f in facts)
    for f in facts:
        ok, reasons = is_material(f, base_cm=base_cm, peer_mom_sum=peer_mom_sum)
        f["material"] = ok
        f["reasons"] = reasons
        boost = anchor_boost_fn(f["line_code"]) if anchor_boost_fn else 0.0
        f["score"] = score_line(f, base_cm=base_cm, anchor_boost=boost)

    return select_drivers(facts, cap)


# ---------------------------------------------------------------------------
# Annual statement → narrative shape (cm / pm / py_cm)
# ---------------------------------------------------------------------------

_NARR_AM_KEYS = ("cm", "pm", "py_cm", "ytd", "ytd_py")


def _round_narr_deltas(am: dict[str, float]) -> dict[str, float]:
    return {
        "mom": round(am["cm"] - am["pm"], 2),
        "yoy": round(am["cm"] - am["py_cm"], 2),
        "ytd": round(am["ytd"] - am["ytd_py"], 2),
    }


def narrative_labels_from_flow_annual(raw: dict[str, str]) -> dict[str, str]:
    """Map exit-readiness flow labels (ytd/ltm/…) to narrative cm/pm/py_cm labels."""
    return {
        "cm": raw.get("ytd") or "",
        "pm": raw.get("ltm") or "",
        "py_cm": raw.get("ytd_py") or "",
        "ytd": raw.get("ytd") or "",
        "ytd_py": raw.get("ytd_py") or "",
    }


def narrative_labels_from_snapshot_annual(raw: dict[str, str]) -> dict[str, str]:
    """Map BS/WC snapshot labels to narrative period labels."""
    return {
        "cm": raw.get("cm") or raw.get("fy") or "",
        "pm": raw.get("cm_py") or raw.get("fy_py") or "",
        "py_cm": raw.get("cm_py") or raw.get("fy_py") or "",
        "ytd": raw.get("cm") or "",
        "ytd_py": raw.get("cm_py") or "",
    }


def _normalize_row_amounts_for_narrative(
    am: dict[str, Any],
    *,
    snapshot: bool,
) -> dict[str, float]:
    if snapshot:
        cm = float(am.get("cm") or am.get("fy") or 0.0)
        pm = float(am.get("cm_py") or am.get("fy_py") or 0.0)
        py = float(am.get("fy_py") or am.get("cm_py") or pm)
    elif "ytd" in am or "ltm" in am:
        cm = float(am.get("ytd") or 0.0)
        pm = float(am.get("ltm") or 0.0)
        py = float(am.get("ytd_py") or 0.0)
    else:
        return {k: float(am.get(k) or 0.0) for k in _NARR_AM_KEYS}
    out = {"cm": cm, "pm": pm, "py_cm": py, "ytd": cm, "ytd_py": py}
    return {k: round(out[k], 2) for k in _NARR_AM_KEYS}


def _normalize_rows_for_narrative(
    rows: list[dict[str, Any]],
    *,
    snapshot: bool,
) -> list[dict[str, Any]]:
    """Deep-copy statement rows; map annual amount keys to narrative cm/pm/py_cm."""

    def _walk(rs: list[dict]) -> list[dict]:
        out: list[dict] = []
        for r in rs:
            nr = dict(r)
            am = nr.get("amounts")
            if isinstance(am, dict) and am:
                narr_am = _normalize_row_amounts_for_narrative(am, snapshot=snapshot)
                nr["amounts"] = narr_am
                nr["deltas"] = _round_narr_deltas(narr_am)
            if nr.get("children"):
                nr["children"] = _walk(nr.get("children") or [])
            if nr.get("accounts"):
                nr["accounts"] = _walk(nr.get("accounts") or [])
            out.append(nr)
        return out

    return _walk(rows)


def normalize_annual_flow_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _normalize_rows_for_narrative(rows, snapshot=False)


def normalize_annual_snapshot_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _normalize_rows_for_narrative(rows, snapshot=True)
