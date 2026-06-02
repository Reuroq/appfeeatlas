"""
appdata.py — AppFeeAtlas data spine + the fee-legality assessment + refund letter.

The moat: real US state application-fee laws (data/states.json) joined against what
tenant screening ACTUALLY costs a landlord (data/vendors.json). That join — your
state's cap vs the real ~$30 cost vs what you were charged — is the "markup gap"
nobody else publishes, and it's what powers the checker, the letter, and the index.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data"


def _load(name, default):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


@lru_cache(maxsize=1)
def states() -> list[dict]:
    return _load("states.json", [])


@lru_cache(maxsize=1)
def vendors() -> dict:
    return _load("vendors.json", {})


def reload():
    states.cache_clear(); vendors.cache_clear()


def state_by_slug(slug):
    return next((s for s in states() if s.get("slug") == slug), None)


def states_with_law():
    return [s for s in states() if s.get("has_fee_law")]


def actual_cost() -> dict:
    return vendors().get("actual_cost", {"low": 18, "typical": 30, "high": 45})


def stats() -> dict:
    ss = states()
    return {
        "states": len(ss),
        "states_with_law": len(states_with_law()),
        "capped": len([s for s in ss if s.get("cap_type") in ("fixed_dollar", "actual_cost", "actual_cost_capped")]),
        "prohibited": len([s for s in ss if s.get("cap_type") == "prohibited"]),
        "typical_cost": actual_cost().get("typical", 30),
    }


# ---------------------------------------------------------------------------
# The assessment — "is my application fee legal, and how marked up is it?"
# ---------------------------------------------------------------------------
def assess(state: dict | None, amount: float) -> dict:
    """Given a state and a fee amount, return a structured verdict + the markup gap."""
    cost = actual_cost()
    cost_typ = cost.get("typical", 30)
    cost_high = cost.get("high", 45)
    amount = max(0.0, float(amount or 0))
    markup = round(amount - cost_typ, 2)
    markup_x = round(amount / cost_typ, 1) if cost_typ else 0

    base = {"amount": amount, "actual_cost": cost_typ, "markup": markup, "markup_x": markup_x,
            "state": (state or {}).get("state", ""), "statute": (state or {}).get("statute_citation", ""),
            "statute_url": (state or {}).get("statute_url", "")}

    if not state:
        base.update(tier="unknown", headline="Pick your state to check the law",
                    detail="Choose your state and we'll check it against the actual cost of screening.")
        return base

    ct = state.get("cap_type")
    cap = state.get("cap_amount")
    refundable = state.get("refund_rule", "")

    if ct == "prohibited":
        base.update(tier=("illegal" if amount > 0 else "ok"), the_line=0,
                    headline=(f"{state['state']} bans application fees — this one is unlawful"
                              if amount > 0 else f"{state['state']} bans application fees"),
                    detail=(f"{state['state']} does not permit application or screening fees at all. "
                            f"The ${amount:.0f} you were charged is unlawful and fully refundable." if amount > 0
                            else "No application fee is allowed here."),
                    refundable="Any application fee is unlawful and fully refundable.")
        return base

    if ct in ("fixed_dollar",) and cap is not None:
        over = round(amount - cap, 2)
        if amount > cap + 0.01:
            base.update(tier="illegal", the_line=cap, overage=over,
                        headline=f"Over {state['state']}'s ${cap:.0f} cap by ${over:.0f} — likely unlawful",
                        detail=(f"{state['state']} caps the application/screening fee at ${cap:.0f}. You were charged "
                                f"${amount:.0f}, which is ${over:.0f} over the legal limit."),
                        refundable=refundable)
        else:
            base.update(tier="ok", the_line=cap,
                        headline=f"Within {state['state']}'s ${cap:.0f} cap",
                        detail=f"${amount:.0f} is at or under {state['state']}'s ${cap:.0f} statutory cap.",
                        refundable=refundable)
        return base

    if ct in ("actual_cost", "actual_cost_capped"):
        ceiling = cap if (ct == "actual_cost_capped" and cap) else None
        line = ceiling or cost_high
        if ceiling and amount > ceiling + 0.01:
            base.update(tier="illegal", the_line=ceiling, overage=round(amount - ceiling, 2),
                        headline=f"Above {state['state']}'s ${ceiling:.0f} ceiling — unlawful",
                        detail=(f"{state['state']} limits the fee to actual screening cost, capped at ~${ceiling:.0f}. "
                                f"You were charged ${amount:.0f}."), refundable=refundable)
        elif amount > cost_high + 0.01:
            base.update(tier="overcharge", the_line=cost_high,
                        headline=f"{state['state']} allows only your landlord's actual cost — this looks marked up",
                        detail=(f"{state['state']} caps the fee at the landlord's actual screening cost (about "
                                f"${cost_typ:.0f}, rarely over ${cost_high:.0f}). At ${amount:.0f} you can demand an "
                                f"itemized receipt and a refund of anything above their real cost."),
                        refundable=refundable)
        else:
            base.update(tier="ok", the_line=line,
                        headline=f"In line with actual screening cost in {state['state']}",
                        detail=f"${amount:.0f} is close to the real cost of a screen, which is what {state['state']} allows.",
                        refundable=refundable)
        return base

    if ct == "refund_required":
        thresh = cap or cost_typ
        base.update(tier=("overcharge" if amount > thresh + 0.01 else "ok"), the_line=thresh,
                    headline=(f"{state['state']}: the amount over ${thresh:.0f} is refundable"
                              if amount > thresh else f"{state['state']}: refund rights apply"),
                    detail=(f"{state['state']} doesn't set a hard cap, but {refundable.lower()} "
                            f"You were charged ${amount:.0f}."),
                    refundable=refundable)
        return base

    # no cap / no law — the markup gap is the leverage
    big = amount > cost_typ * 2
    base.update(tier=("overcharge" if big else "no_cap"), the_line=None,
                headline=(f"{state['state']} has no cap — but you were charged {markup_x}× the real cost"
                          if big else f"{state['state']} has no statutory cap"),
                detail=(f"There's no application-fee cap in {state['state']}. A full screen costs a landlord about "
                        f"${cost_typ:.0f}; you were charged ${amount:.0f}"
                        + (f" — a ${markup:.0f} markup ({markup_x}×)." if markup > 0 else ".")
                        + " You can still ask for an itemized receipt and reuse a screening report across listings."),
                refundable=(refundable or "No statutory refund right — leverage is the markup and reusable reports."))
    return base


# ---------------------------------------------------------------------------
# Refund / dispute letter generator
# ---------------------------------------------------------------------------
def _clip(v, n=160):
    return str(v if v is not None else "").replace("\r", " ").replace("\n", " ").strip()[:n]


def build_letter(inputs: dict) -> str:
    """inputs: name, address, email, landlord, property, state_slug, amount, paid_date."""
    s = state_by_slug(_clip(inputs.get("state_slug", ""), 60)) or {}
    name = _clip(inputs.get("name")) or "[Your Name]"
    address = _clip(inputs.get("address"), 200) or "[Your Address]"
    email = _clip(inputs.get("email"), 120)
    landlord = _clip(inputs.get("landlord"), 120) or "the landlord / property manager"
    prop = _clip(inputs.get("property"), 160)
    paid_date = _clip(inputs.get("paid_date"), 40) or "[date paid]"
    try:
        amount = float(str(inputs.get("amount", "")).replace("$", "").strip() or 0)
    except ValueError:
        amount = 0.0
    a = assess(s or None, amount)

    cite = s.get("statute_citation", "")
    law_line = ""
    if s.get("has_fee_law") and cite:
        law_line = f"Under {s.get('state')}'s rental application-fee law ({cite}), {a.get('detail','')} "
        if a.get("refundable"):
            law_line += f"{a['refundable']} "
    demand_amt = (f"the ${a.get('overage'):.0f} charged above the legal limit"
                  if a.get("overage") else f"the unlawful ${amount:.0f} fee" if a.get("tier") == "illegal"
                  else f"the portion of the ${amount:.0f} fee above your actual cost of screening")

    lines = [
        f"{name}", f"{address}", (email if email else ""), "",
        "[Date]", "",
        f"{landlord}" + (f" — {prop}" if prop else ""),
        "Re: Demand for Refund of Rental Application Fee", "",
        "To Whom It May Concern:", "",
        (f"On {paid_date} I paid a ${amount:.0f} application/screening fee" + (f" for {prop}" if prop else "")
         + ". " + law_line + "I am requesting a refund of " + demand_amt + "."),
        "",
        ("Please also provide, in writing within a reasonable time: (1) an itemized receipt showing the actual "
         "cost of any background or credit check you obtained; (2) the name of the screening company used; and "
         "(3) confirmation of the refund and the method by which it will be returned."),
        "",
        ("I am keeping a copy of this letter and my payment records. If the fee is not refunded as required, I may "
         "report this to my state Attorney General's consumer-protection division and pursue any remedies available "
         "under state law."),
        "", "Sincerely,", f"{name}",
    ]
    return "\n".join(l for l in lines if l is not None)
