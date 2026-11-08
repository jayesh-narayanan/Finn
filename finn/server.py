"""Serves the app, the generated data, and the on-demand endpoints.

The web app also works with no server at all (static hosting of web/ + data/),
in which case research is limited to the dossiers already built.
"""
from __future__ import annotations

import logging
import re
import threading
import uuid

from flask import Flask, jsonify, request, send_from_directory

from . import llm
from .config import DATA, WEB
from .pull import dossier
from .push import digest

log = logging.getLogger("finn.server")
app = Flask(__name__, static_folder=None)
JOBS: dict[str, dict] = {}
_SYM = re.compile(r"^[A-Z0-9&\-]{2,20}$")


@app.after_request
def _headers(r):
    r.headers["Cache-Control"] = "no-store" if request.path.startswith(("/api", "/data")) else "no-cache"
    return r


@app.get("/")
def index():
    return send_from_directory(WEB, "index.html")


@app.get("/data/<path:p>")
def data(p):
    return send_from_directory(DATA, p)


@app.get("/<path:p>")
def static_files(p):
    return send_from_directory(WEB, p)


@app.get("/api/health")
def health():
    return jsonify(ok=True, llm=llm.available())


def _run(job_id: str, fn, *args):
    def progress(step, pct):
        JOBS[job_id].update(step=step, pct=pct)
    try:
        JOBS[job_id]["result"] = fn(*args, progress) if fn is dossier.build else fn(*args)
        JOBS[job_id].update(status="done", pct=100)
    except Exception as e:  # noqa: BLE001
        log.exception("job failed")
        JOBS[job_id].update(status="error", error=str(e)[:300])


@app.post("/api/research")
def research():
    sym = (request.json or {}).get("symbol", "").upper().strip()
    if not _SYM.match(sym):
        return jsonify(error="Enter an NSE symbol, like PIDILITIND"), 400
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "running", "step": "Starting", "pct": 1, "symbol": sym}
    threading.Thread(target=_run, args=(jid, dossier.build, sym), daemon=True).start()
    return jsonify(job=jid)


@app.post("/api/refresh")
def refresh():
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"status": "running", "step": "Refreshing portfolio feed", "pct": 5}
    threading.Thread(target=_run, args=(jid, digest.build), daemon=True).start()
    return jsonify(job=jid)


@app.get("/api/jobs/<jid>")
def job(jid):
    j = JOBS.get(jid)
    if not j:
        return jsonify(error="unknown job"), 404
    return jsonify({k: v for k, v in j.items() if k != "result"})


@app.post("/api/ask")
def ask():
    body = request.json or {}
    sym, q = body.get("symbol", "").upper(), (body.get("question") or "").strip()
    if not _SYM.match(sym) or not q:
        return jsonify(error="symbol and question are required"), 400
    if not llm.available():
        return jsonify(error="Questions need a model key. Add FINN_LLM_API_KEY to .env and restart."), 503
    try:
        return jsonify(dossier.ask(sym, q[:500]))
    except FileNotFoundError as e:
        return jsonify(error=str(e)), 404
    except Exception as e:  # noqa: BLE001
        log.exception("ask failed")
        return jsonify(error=f"Couldn't answer: {e}"[:300]), 500


def serve(host: str = "0.0.0.0", port: int = 8000):
    app.run(host=host, port=port, threaded=True, debug=False)
