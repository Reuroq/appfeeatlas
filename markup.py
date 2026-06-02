"""
markup.py — the INFORMATION-GAIN layer.

Nobody publishes the join of (1) each state's application-fee law, (2) what tenant
screening ACTUALLY costs a landlord, and (3) what landlords typically charge. This
module computes that markup index — the original research that ranks and gets cited,
and the spine of the /markup page + the checker.
"""
from __future__ import annotations

import appdata as A

_CAP_LABEL = {
    "prohibited": "Banned — no fee allowed",
    "fixed_dollar": "Hard $ cap",
    "actual_cost": "Actual cost only",
    "actual_cost_capped": "Actual cost (with $ ceiling)",
    "refund_required": "Refundable over a threshold",
    "none": "No cap",
}


def the_legal_line(s: dict) -> str:
    """A short human label for where the law draws the line in this state."""
    ct = s.get("cap_type")
    cap = s.get("cap_amount")
    if ct == "prohibited":
        return "$0 (banned)"
    if ct == "fixed_dollar" and cap is not None:
        return f"${cap:.0f} max"
    if ct == "actual_cost_capped" and cap:
        return f"actual cost, ≤ ~${cap:.0f}"
    if ct == "actual_cost":
        return "actual cost only"
    if ct == "refund_required":
        return (f"refund over ${cap:.0f}" if cap else "refundable balance")
    return "no cap"


def state_markup(s: dict) -> dict:
    cost = A.actual_cost()
    charged = A.vendors().get("typical_charged", {})
    return {
        "state": s["state"], "slug": s["slug"],
        "has_law": bool(s.get("has_fee_law")),
        "cap_type": s.get("cap_type"),
        "cap_label": _CAP_LABEL.get(s.get("cap_type"), "—"),
        "legal_line": the_legal_line(s),
        "actual_cost": cost.get("typical", 30),
        "typical_charged": charged.get("typical", 55),
        "overcharge_illegal": bool(s.get("overcharge_illegal")),
        "reusable_report": bool(s.get("reusable_report")),
        "statute": s.get("statute_citation", ""),
        "verify": "UNCERTAIN" in (s.get("notes", "") or ""),
    }


def index_rows() -> list[dict]:
    """All states, strongest protections first (banned, then capped, then none)."""
    order = {"prohibited": 0, "fixed_dollar": 1, "actual_cost_capped": 1,
             "actual_cost": 2, "refund_required": 3, "none": 4}
    rows = [state_markup(s) for s in A.states()]
    rows.sort(key=lambda r: (order.get(r["cap_type"], 5), r["state"]))
    return rows


def overall_summary() -> dict:
    ss = A.states()
    cost = A.actual_cost()
    charged = A.vendors().get("typical_charged", {})
    capped = [s for s in ss if s.get("cap_type") in ("fixed_dollar", "actual_cost", "actual_cost_capped")]
    banned = [s for s in ss if s.get("cap_type") == "prohibited"]
    typ_charged = charged.get("typical", 55)
    typ_cost = cost.get("typical", 30)
    return {
        "states": len(ss),
        "with_law": len(A.states_with_law()),
        "capped": len(capped),
        "banned": len(banned),
        "actual_cost": typ_cost,
        "typical_charged": typ_charged,
        "typical_markup": round(typ_charged - typ_cost),
        "typical_markup_x": round(typ_charged / typ_cost, 1) if typ_cost else 0,
        "reusable_states": len([s for s in ss if s.get("reusable_report")]),
    }
