"""
analyzer.py — EPHEMERAL lease / rental-application analysis. Read the user's lease or
application (PDF or pasted text), extract the application-fee facts with Claude, and
cross-reference their state law. NOTHING is stored or logged — the text lives only in
memory for one request, then is discarded.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

MODEL = os.environ.get("APPFEEATLAS_MODEL", "claude-haiku-4-5-20251001")
MAX_PAGES = 30
MAX_CHARS = 16000
_KEY_FILES = [Path(r"c:/Users/dwayn/OneDrive/Desktop/WorkShield/.env")]


def _key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_KEY", "")
    if k:
        return k
    for f in _KEY_FILES:
        try:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return ""


def extract_text(data: bytes, filename: str) -> str:
    """Extract text from an uploaded PDF or text file. In-memory only."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        try:
            import fitz  # PyMuPDF (lazy import)
            doc = fitz.open(stream=data, filetype="pdf")
            parts = []
            for i, page in enumerate(doc):
                if i >= MAX_PAGES:
                    break
                parts.append(page.get_text())
            doc.close()
            return "".join(parts)
        except Exception:
            return ""
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def analyze_lease(text: str, state: dict | None) -> dict:
    """Return application-fee analysis JSON. Never logs/stores the document text."""
    text = (text or "").strip()
    if len(text) < 80:
        return {"error": "too_little_text"}
    text = text[:MAX_CHARS]
    key = _key()
    if not key:
        return {"error": "no_key"}

    law = ""
    if state and state.get("has_fee_law"):
        law = (f"USER'S STATE LAW — {state['state']} ({state.get('statute_citation')}): "
               f"{state.get('cap_summary')} Refund rule: {state.get('refund_rule')}")

    prompt = f"""You are a renter-rights analyst for AppFeeAtlas, firmly on the renter's side.
Read this lease / rental-application excerpt and extract ONLY the application-fee facts. A full
tenant screen (credit + criminal + eviction) actually costs a landlord about $30, so flag any
application fee well above that as marked up. {('Cross-reference this law: ' + law) if law else 'No state law was provided.'}

DOCUMENT EXCERPT:
\"\"\"{text}\"\"\"

Return exactly ONE JSON object and nothing else:
{{
 "application_fee": "the application/screening fee as stated (with $), or 'not stated'",
 "application_fee_amount": "just the number if identifiable, e.g. '75', else ''",
 "refundable": "what the document says about refunds, or 'not stated'",
 "covers": "what the fee is said to cover (credit/background/admin), or 'not stated'",
 "other_fees": "other move-in fees mentioned (admin, holding, deposit), or 'none found'",
 "state_verdict": "1-2 sentences on what the user's state law says about this fee (only if a state law was provided), else ''",
 "likely_illegal": true/false,
 "red_flags": ["specific problems in THIS document a renter should know — non-refundable fees, fees far above the ~$30 real cost, vague 'admin' fees, charging at every application (max 5)"],
 "steps": ["concrete, ordered next steps for THIS renter (max 5)"],
 "suggested_reason": "",
 "confidence": "high|medium|low"
}}
Base everything ONLY on the excerpt and the provided law. Informational only, not legal advice."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 1600, "messages": [{"role": "user", "content": prompt}]},
            timeout=120,
        )
        if r.status_code != 200:
            return {"error": f"llm_{r.status_code}"}
        out = re.sub(r"```[a-z]*", "", r.json()["content"][0]["text"]).replace("```", "")
        m = re.search(r"\{.*\}", out, re.DOTALL)
        return json.loads(m.group()) if m else {"error": "parse"}
    except Exception:
        return {"error": "exception"}
