"""Serves the app, the generated data, and the on-demand endpoints.

Built to be left running in public (e.g. Render's free tier) with strangers poking at it:
  * research accepts a symbol, a Yahoo-style ticker or a company name, and resolves it
  * two requests for the same company share one job; at most FINN_MAX_BUILDS build at once
  * per-visitor hourly limits protect the model quota
  * a refresh that NSE blocks never overwrites the last good feed

The web app also works with no server at all (static hosting of web/ + data/), in which case
research is limited to the reports already built.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque

from flask import Flask, jsonify, request, send_from_directory

from . import llm
from .config import DATA, RESEARCH, WEB
from .pull import dossier
from .push import digest
from .sources import screener

if not logging.getLogger().handlers:  # under gunicorn nothing has configured logging yet
    logging.basicConfig(level=logging.INFO, format="%(levelname).1s %(name)s: %(message)s")
    for noisy in ("pypdf", "pdfminer"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

log = logging.getLogger("finn.server")
app = Flask(__name__, static_folder=None)

JOBS: dict[str, dict] = {}
_ACTIVE: dict[str, str] = {}  # what was asked for (upper-cased) -> running job id
_lock = threading.Lock()
_BUILD_SLOTS = threading.BoundedSemaphore(int(os.environ.get("FINN_MAX_BUILDS", "2")))
LIMITS = {"research": int(os.environ.get("FINN_RESEARCH_PER_HOUR", "8")),
          "ask": int(os.environ.get("FINN_ASKS_PER_HOUR", "30")),
          "refresh": int(os.environ.get("FINN_REFRESH_PER_HOUR", "2"))}
_hits: dict[tuple, deque] = defaultdict(deque)


def _visitor() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() or request.remote_addr or "?"


def _limited(kind: str) -> str | None:
    """Sliding one-hour window per visitor. Returns an error message when over the limit."""
    cap = LIMITS[kind]
    if cap <= 0:
        return None
    q = _hits[(kind, _visitor())]
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= cap:
        mins = int((3600 - (now - q[0])) / 60) + 1
        return f"That's the limit of {cap} {kind} requests an hour on this demo server. Try again in {mins} min."
    q.append(now)
    return None


def _prune_jobs() -> None:
    cutoff = time.time() - 3600
    for jid in [j for j, v in JOBS.items() if v.get("status") != "running" and v.get("t", 0) < cutoff]:
        JOBS.pop(jid, None)


@app.after_request
def _headers(r):
    r.headers["Cache-Control"] = "no-store" if request.path.startswith(("/api", "/data")) else "no-cache"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/data/<path:p>")
def data(p):
    if p.startswith("cache"):
        return jsonify(error="not found"), 404
    return send_from_directory(DATA, p)


@app.get("/<path:p>")
def static_files(p):
    return send_from_directory(WEB, p)


@app.get("/api/health")
def health():
    n = len([p for p in RESEARCH.glob("*.json") if p.name != "index.json"])
    return jsonify(ok=True, llm=llm.available(), research=n,
                   running=sum(1 for j in JOBS.values() if j.get("status") == "running"))


# ------------------------------------------------------------------ research
def _research_job(jid: str, query: str, key: str) -> None:
    job = JOBS[jid]

    def progress(step, pct):
        job.update(step=step, pct=pct)
    try:
        sym = screener.resolve(query)
        job["symbol"] = sym
        if not _BUILD_SLOTS.acquire(blocking=False):
            progress("Waiting for another research job to finish", 3)
            _BUILD_SLOTS.acquire()
        try:
            dossier.build(sym, progress)
        finally:
            _BUILD_SLOTS.release()
        job.update(status="done", pct=100, step="Done")
    except screener.NotFound as e:
        job.update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("research %s failed", query)
        job.update(status="error", error=_friendly(e, job.get("symbol") or query))
    finally:
        job["t"] = time.time()
        with _lock:
            if _ACTIVE.get(key) == jid:
                _ACTIVE.pop(key, None)


def _friendly(e: Exception, what: str) -> str:
    s = str(e)
    if "screener" in s and ("404" in s or "could not load" in s):
        return f"screener.in has no financials for {what}, so there's nothing to analyse."
    if isinstance(e, llm.LLMConfigError):
        return f"The model rejected the request ({s[:160]}). The numbers still work; check the model key."
    return f"Something went wrong building {what}: {s[:200]}"


@app.post("/api/research")
def research():
    body = request.get_json(silent=True) or {}
    query = screener.clean_query(body.get("symbol") or body.get("query") or "")
    if len(query) < 2:
        return jsonify(error="Type an NSE symbol or a company name, like PIDILITIND or Pidilite"), 400
    key = query.upper()
    with _lock:
        jid = _ACTIVE.get(key)
        if jid and JOBS.get(jid, {}).get("status") == "running":
            return jsonify(job=jid, joined=True)  # someone is already building this one
        if (msg := _limited("research")):
            return jsonify(error=msg), 429
        _prune_jobs()
        jid = uuid.uuid4().hex[:10]
        JOBS[jid] = {"status": "running", "step": "Finding the company", "pct": 1, "symbol": key, "t": time.time()}
        _ACTIVE[key] = jid
    threading.Thread(target=_research_job, args=(jid, query, key), daemon=True).start()
    return jsonify(job=jid)


# ------------------------------------------------------------------ refresh
def _refresh_job(jid: str) -> None:
    job = JOBS[jid]
    try:
        feed = digest.build()
        job.update(status="done", pct=100, filings=len(feed["announcements"]), notes=feed.get("notes", []))
    except digest.KeptPrevious as e:
        job.update(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("refresh failed")
        job.update(status="error", error=f"Refresh failed: {str(e)[:200]}. The previous brief is still shown.")
    finally:
        job["t"] = time.time()
        with _lock:
            _ACTIVE.pop("__refresh__", None)


@app.post("/api/refresh")
def refresh():
    with _lock:
        jid = _ACTIVE.get("__refresh__")
        if jid and JOBS.get(jid, {}).get("status") == "running":
            return jsonify(job=jid, joined=True)
        if (msg := _limited("refresh")):
            return jsonify(error=msg), 429
        jid = uuid.uuid4().hex[:10]
        JOBS[jid] = {"status": "running", "step": "Refreshing portfolio feed", "pct": 5, "t": time.time()}
        _ACTIVE["__refresh__"] = jid
    threading.Thread(target=_refresh_job, args=(jid,), daemon=True).start()
    return jsonify(job=jid)


@app.get("/api/jobs/<jid>")
def job(jid):
    j = JOBS.get(jid)
    if not j:
        return jsonify(error="This job is no longer tracked (the server may have restarted). Try again."), 404
    return jsonify({k: v for k, v in j.items() if k not in ("result", "t")})


# ------------------------------------------------------------------ ask
@app.post("/api/ask")
def ask():
    body = request.get_json(silent=True) or {}
    sym = (body.get("symbol") or "").upper().strip()
    q = (body.get("question") or "").strip()
    if not sym or len(sym) > 20 or not q:
        return jsonify(error="A company and a question are both needed"), 400
    if not llm.available():
        return jsonify(error="Questions need a model key. Add FINN_LLM_API_KEY and restart."), 503
    if (msg := _limited("ask")):
        return jsonify(error=msg), 429
    try:
        return jsonify(dossier.ask(sym, q[:500]))
    except FileNotFoundError as e:
        return jsonify(error=str(e)), 404
    except Exception as e:  # noqa: BLE001
        log.exception("ask failed")
        return jsonify(error=f"Couldn't answer: {str(e)[:200]}"), 500


def serve(host: str = "0.0.0.0", port: int = 8000):
    app.run(host=host, port=port, threaded=True, debug=False)
