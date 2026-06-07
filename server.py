"""AppFeeAtlas — FastAPI server. Serves the generated static site + the checker/letter/analyze APIs.

Security posture: static pages + stateless compute (the fee checker) + a stateless letter
builder + one ephemeral AI analyze endpoint. Hardening that fits: security headers, per-IP
rate limits + request-size caps, path-traversal guard, slug validation, a daily spend ceiling
on the paid LLM endpoint, and NOTHING about an uploaded document is stored or logged.
"""
import datetime
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import appdata as A
import sitegen as S
import analyzer

MAX_UPLOAD = 5 * 1024 * 1024
ANALYZE_DAILY_CEILING = int(os.environ.get("APPFEEATLAS_DAILY_ANALYSES", "150"))
_ANALYZE_DAY = {"date": "", "count": 0}


def _analyze_budget_ok() -> bool:
    today = datetime.date.today().isoformat()
    if _ANALYZE_DAY["date"] != today:
        _ANALYZE_DAY.update(date=today, count=0)
    if _ANALYZE_DAY["count"] >= ANALYZE_DAILY_CEILING:
        return False
    _ANALYZE_DAY["count"] += 1
    return True


app = FastAPI(title="AppFeeAtlas")
ROOT = Path(__file__).parent
app.mount("/static", StaticFiles(directory="static"), name="static")
SEO = (ROOT / "static" / "seo").resolve()

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,60}$")
_HITS: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"))


def _rate_ok(ip: str, limit: int = 30, window: int = 60) -> bool:
    now = time.time()
    q = _HITS[ip]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://www.googletagmanager.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://www.googletagmanager.com https://www.google-analytics.com; "
        "connect-src 'self' https://www.google-analytics.com https://*.google-analytics.com "
        "https://*.analytics.google.com https://www.googletagmanager.com; "
        "base-uri 'self'; frame-ancestors 'self'")
    return resp


def _slug(slug: str) -> str:
    if not _SLUG_RE.match(slug or ""):
        raise HTTPException(404, "Not found")
    return slug


def _seo(rel):
    p = (SEO / rel).resolve()
    if not str(p).startswith(str(SEO)) or not p.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(p)


@app.get("/")
async def home():
    return _seo("index.html")

@app.get("/check")
async def check_page():
    return _seo("hubs/check.html")

@app.get("/markup")
async def markup_page():
    return _seo("hubs/markup.html")

@app.get("/letter")
async def letter_page():
    return _seo("hubs/letter.html")

@app.get("/analyze")
async def analyze_page():
    return _seo("hubs/analyze.html")

@app.get("/states")
async def states_hub():
    return _seo("hubs/states.html")

@app.get("/states/{slug}")
async def state(slug: str):
    return _seo(f"states/{_slug(slug)}.html")

@app.get("/guides")
async def guides_hub():
    return _seo("hubs/guides.html")

@app.get("/guides/{slug}")
async def guide(slug: str):
    return _seo(f"guides/{_slug(slug)}.html")


@app.post("/api/check")
async def api_check(request: Request):
    if not _rate_ok(_client_ip(request)):
        raise HTTPException(429, "Too many requests — please slow down.")
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > 4096:
        raise HTTPException(413, "Request too large.")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid request body.")
    if not isinstance(data, dict):
        raise HTTPException(400, "Invalid request body.")
    slug = str(data.get("state_slug", ""))[:60]
    state = A.state_by_slug(slug) if _SLUG_RE.match(slug) else None
    try:
        amount = float(str(data.get("amount", "")).replace("$", "").strip() or 0)
    except ValueError:
        amount = 0.0
    return {"assessment": A.assess(state, amount)}


@app.post("/api/letter")
async def api_letter(request: Request):
    if not _rate_ok(_client_ip(request)):
        raise HTTPException(429, "Too many requests — please slow down.")
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > 8192:
        raise HTTPException(413, "Request too large.")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid request body.")
    if not isinstance(data, dict):
        raise HTTPException(400, "Invalid request body.")
    return {"letter": A.build_letter(data)}


@app.post("/api/analyze")
async def api_analyze(request: Request, file: UploadFile = File(None),
                      pasted: str = Form(""), state_slug: str = Form("")):
    if not _rate_ok("analyze:" + _client_ip(request), limit=5, window=60):
        raise HTTPException(429, "Too many analyses — please wait a minute.")
    if not _analyze_budget_ok():
        raise HTTPException(503, "Daily free-analysis limit reached — use the checker, or try again tomorrow.")

    text = ""
    if file is not None and file.filename:
        fn = file.filename.lower()
        if not (fn.endswith(".pdf") or fn.endswith(".txt")):
            raise HTTPException(415, "Please upload a PDF or .txt file.")
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "File too large (5 MB max).")
        text = analyzer.extract_text(data, file.filename)
        del data
    elif pasted:
        text = pasted[:40000]

    if not text or len(text.strip()) < 80:
        return {"analysis": {"error": "too_little_text"},
                "hint": "If your PDF is a scan/image, paste the application-fee section as text instead."}

    state = None
    if state_slug and _SLUG_RE.match(state_slug):
        state = A.state_by_slug(state_slug)
    result = analyzer.analyze_lease(text, state)
    del text  # EPHEMERAL: never stored or logged
    return {"analysis": result, "state": (state or {}).get("state", "")}


@app.get("/sitemap.xml")
async def sitemap():
    return FileResponse("static/sitemap.xml", media_type="application/xml")

@app.get("/robots.txt")
async def robots():
    return FileResponse("static/robots.txt", media_type="text/plain")

@app.get("/llms.txt")
async def llms():
    return FileResponse("static/llms.txt", media_type="text/plain")

@app.get("/e77bfaf0c0a1d708365cc37b4b61c4ee.txt")
async def indexnow_key():
    return FileResponse("static/e77bfaf0c0a1d708365cc37b4b61c4ee.txt", media_type="text/plain")

@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "AppFeeAtlas", **A.stats()}


@app.on_event("startup")
async def _build():
    try:
        A.reload()
        S.build()
        print("[startup] AppFeeAtlas site rebuilt from data.")
    except Exception as e:
        print(f"[startup] build skipped: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8812")))
