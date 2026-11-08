"""PDF text extraction, page-aware chunking and a dependency-free BM25 retriever.

Annual reports run to 300+ pages; we never send one to a model whole. Text is
extracted once (cached), split into page-tagged chunks, and only the chunks that
score highest for a specific question are sent.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path

from .. import cache
from .http import download

log = logging.getLogger("finn.docs")


def pdf_pages(path: Path, max_pages: int | None = None) -> list[str]:
    k = cache.key(str(path), path.stat().st_size, max_pages)
    hit = cache.get("pdftext", k)
    if hit is not None:
        return hit
    pages: list[str] = []
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        for i, p in enumerate(reader.pages):
            if max_pages and i >= max_pages:
                break
            pages.append(p.extract_text() or "")
    except Exception as e:  # noqa: BLE001
        log.warning("pypdf failed on %s (%s); trying pdfplumber", path.name, e)
    if sum(len(p) for p in pages) < 200:  # scanned or odd encoding: try the slower parser
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                pages = [(pg.extract_text() or "") for i, pg in enumerate(pdf.pages)
                         if not max_pages or i < max_pages]
        except Exception as e:  # noqa: BLE001
            log.warning("pdfplumber failed on %s: %s", path.name, e)
    cache.put("pdftext", k, pages)
    return pages


def fetch_pages(url: str, max_pages: int | None = None) -> list[str]:
    return pdf_pages(download(url), max_pages)


def chunks(pages: list[str], source_id: str, size: int = 1400, overlap: int = 200) -> list[dict]:
    out = []
    for pno, text in enumerate(pages, start=1):
        text = re.sub(r"[ \t]+", " ", text).strip()
        i = 0
        while i < len(text):
            piece = text[i:i + size]
            if len(piece) > 80:
                out.append({"src": source_id, "page": pno, "text": piece})
            i += size - overlap
    return out


_TOK = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?%?")


def tokens(s: str) -> list[str]:
    return _TOK.findall(s.lower())


class BM25:
    def __init__(self, docs: list[dict], k1: float = 1.4, b: float = 0.75):
        self.docs = docs
        self.tf = [Counter(tokens(d["text"])) for d in docs]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if docs else 1
        df: Counter = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self.k1, self.b = k1, b

    def search(self, query: str, k: int = 8) -> list[dict]:
        q = tokens(query)
        scored = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for w in q:
                if w in tf:
                    f = tf[w]
                    s += self.idf.get(w, 0) * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [self.docs[i] | {"score": round(s, 2)} for s, i in scored[:k]]


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def quote_in(quote: str, pages: list[str]) -> int | None:
    """Return the 1-based page where a (normalised) quote occurs, else None.

    Used to throw away any model 'quote' that is not actually in the source.
    Long quotes are checked on a 12-word window so minor PDF line-break noise passes.
    """
    q = norm(quote)
    if len(q) < 12:
        return None
    words = q.split()
    probes = [q] if len(words) <= 14 else [" ".join(words[i:i + 12]) for i in (0, len(words) // 2 - 6, len(words) - 12)]
    for pno, p in enumerate(pages, start=1):
        np_ = norm(p)
        if sum(1 for pr in probes if pr in np_) >= max(1, len(probes) - 1):
            return pno
    return None
