"""FINN command line.

  python -m finn sample                 # fictional demo data, no network needed
  python -m finn refresh [--days 30]    # live: prices + NSE filings + enrichment -> data/feed.json
  python -m finn research PIDILITIND    # live: dossier -> data/research/PIDILITIND.json
  python -m finn research --demo        # dossiers for config research_demo
  python -m finn serve [--port 8000]    # app + API
  python -m finn build-static           # dist/ for static hosting (Vercel, Netlify, GitHub Pages)
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil

from .config import DATA, ROOT, WEB, portfolio_config


def main():
    ap = argparse.ArgumentParser(prog="finn")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    r = sub.add_parser("refresh")
    r.add_argument("--days", type=int, default=30)
    r.add_argument("--no-enrich", action="store_true")
    rs = sub.add_parser("research")
    rs.add_argument("symbols", nargs="*")
    rs.add_argument("--demo", action="store_true")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="0.0.0.0")
    sub.add_parser("build-static")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname).1s %(name)s: %(message)s")

    if a.cmd == "sample":
        from . import sample
        sample.write()
    elif a.cmd == "refresh":
        from .push import digest
        digest.build(days=a.days, enrich_items=not a.no_enrich)
    elif a.cmd == "research":
        from .pull import dossier
        syms = a.symbols or (portfolio_config()["research_demo"] if a.demo else [])
        for sym in syms:
            d = dossier.build(sym, progress=lambda st, p, s=sym: logging.info("%s %3d%% %s", s, p, st))
            print(json.dumps({"symbol": sym, "cost": d["cost"]}))
    elif a.cmd == "serve":
        from .server import serve
        serve(a.host, a.port)
    elif a.cmd == "build-static":
        dist = ROOT / "dist"
        shutil.rmtree(dist, ignore_errors=True)
        shutil.copytree(WEB, dist)
        shutil.copytree(DATA, dist / "data", ignore=shutil.ignore_patterns("cache"))
        print(f"Static site written to {dist}")


if __name__ == "__main__":
    main()
