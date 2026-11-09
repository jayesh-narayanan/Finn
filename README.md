# FINN

A mobile-first investment agent for an Indian equity portfolio. It does two things:

- **Push (Brief).** Opens on the filter itself: *"5 of 67 filings need you."* Every NSE filing in the window is classified, scored for materiality and sorted into three tiers that look different on purpose: full cards for what needs you, one-line rows for what's worth knowing, and a single folded line for routine paperwork. You clear the queue to zero like an inbox.
- **Pull (Research).** For any NSE symbol, a dossier led by **promises vs delivery**: every piece of guidance from the last four earnings calls, checked against the numbers reported since. Every line links to the transcript quote or the reported figure it rests on.

The third screen, **Watch**, is where the investor defines "unusual" (price move, volume multiple, baseline) and routes each filing type to Alert, Brief or Mute, with a live 90-session backtest showing how many alerts that setting would have produced.

## Run it

Set up once (Python 3.10+):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Try it on fictional sample data, with no network or key needed. The second command starts the app at http://localhost:8000; open it on your phone at your computer's LAN address.

```bash
python -m finn sample
python -m finn serve
```

For live data, first check which data sources your network can reach:

```bash
python -m finn doctor
```

Then add a model key and refresh. `.env.example` explains the free options (Gemini, Groq, or a local Ollama). Open `.env` in any editor and paste your key after `FINN_LLM_API_KEY=`.

```bash
cp .env.example .env
python -m finn refresh
python -m finn research --demo
python -m finn research HAL
python -m finn serve
```

Run the tests (they use a throwaway data folder):

```bash
python -m pytest -q
```

Edit the portfolio in `config/portfolio.json`. Without a model key everything still runs: classification, scoring, alerts and all the financial analysis are deterministic; only one-liners, management bios, guidance extraction and the promises scorecard need a model.

### Where prices come from

Yahoo rate-limits scripts (HTTP 429), so FINN tries three sources and uses the first that works, per symbol:

1. **yfinance** (needs `curl_cffi`, which makes requests look like a real browser to Yahoo), one batch for the whole portfolio.
2. **Yahoo's chart endpoint**, directly.
3. **NSE's daily bhavcopy archive.** One file per trading day covers every listed stock, so it doesn't depend on Yahoo at all. The first refresh downloads ~125 files (a minute or two). Each day is cached for good, so later refreshes only fetch new days. Set `FINN_PRICE_SESSIONS=60` in `.env` to halve the first download.

If a source fails, the refresh carries on and the app shows an "Incomplete refresh" note that says what's missing. NSE's filings API is treated the same way: after three blocked requests, FINN stops trying for that run instead of spending minutes on retries.

### Deploy

**Full app on Render (recommended: research works on any company).** The repo includes `render.yaml`.

1. On https://render.com sign in with GitHub, then **New > Blueprint** and pick this repo.
2. Render asks for `FINN_LLM_API_KEY` (and optionally `FINN_SEARCH_API_KEY`). Paste your key and click **Apply**.
3. The first build takes about 5 minutes. Your app is then at `https://finn-XXXX.onrender.com`.

Things to know about the free tier:

- It sleeps after 15 idle minutes, and the first visit after that takes ~30-50 s to wake. A free pinger (cron-job.org or UptimeRobot) hitting `/api/health` every 10 minutes keeps it awake; one service running all month fits in the free hours.
- Its disk resets on every deploy or restart. The brief and the research committed in `data/` always come back; reports built on the server last until the next restart.
- NSE often blocks cloud servers, so **Refresh** on Render may be refused. FINN then keeps the last good brief and says so; it never publishes an empty one. Refresh locally and push `data/` to update it (Render redeploys on push).

What it handles in public: symbols, Yahoo-style tickers (`TCS.NS`) or company names (`Asian Paints`), resolved through screener.in's search; two people asking for the same company share one job; at most two reports build at once (`FINN_MAX_BUILDS`); each visitor gets 8 reports, 30 questions and 2 refreshes an hour, so the model quota can't be drained (`FINN_RESEARCH_PER_HOUR`, `FINN_ASKS_PER_HOUR`, `FINN_REFRESH_PER_HOUR`).

**Static (no server).** `python -m finn build-static` writes `dist/` with the app and the latest data, for Netlify, Vercel or GitHub Pages. Research is limited to the reports you built, and the ask box is off.

## How it works

Push (`python -m finn refresh` writes `data/feed.json`):

```
prices    yfinance -> Yahoo chart -> NSE bhavcopy   first that works, per symbol
filings   NSE announcements API
            -> classify    ordered rules; model only for leftovers, batched
            -> score       category + content + deal size vs market cap + price reaction
            -> enrich      results: parse the filing's table; model fallback, numbers verified
                           mgmt: profile from the filing (+ optional web search)
                           others: exchange text, or one batched one-line summary
app       evaluates your alert rules on the device
```

Pull (`python -m finn research SYM` writes `data/research/SYM.json`):

```
screener.in tables  -> metrics.py         arithmetic only, no model
last 4 transcripts  -> BM25 top 10 chunks -> fast model -> quotes checked against PDF text
latest annual report -> BM25 top 8 chunks -> fast model -> business snapshot
all of the above    -> smart model, ONE call -> scorecard, story vs numbers, bull/bear
                    -> every line must cite evidence ids that exist, or it is dropped
```

### Decisions and trade-offs

- **Rules before models.** Exchange subjects are semi-standardised, so ~25 ordered regexes classify most filings for free; only leftovers go to the model, batched 25 at a time. Materiality scoring is rules too, and every point it adds is shown to the user as a reason ("CFO change", "₹420 cr ≈ 5.9% of market cap", "stock moved −5.8%, 2.4σ"). A ranking the investor can audit beats one they have to trust.
- **Results are read from the filing, not a model's memory.** Indian results filings print exactly the three columns the brief asks for (this quarter, last quarter, same quarter last year), so a parser reads Revenue, PBT, PAT, finance costs, depreciation and other income from the consolidated table, and EBITDA is derived and labelled as such. The fast model is a fallback, and any number it returns must appear in the PDF text or it's discarded.
- **Management bios come from the filing first.** SEBI requires a brief profile of appointees in the disclosure itself, so that's the primary source. Web enrichment (Tavily) is optional and labelled "from the web". LinkedIn is not scraped: it's behind a login and against its terms.
- **The grounding contract on the pull side.** The model never sees "the company"; it sees ids: `T2-G1` (a verified transcript quote), `F:Q6` (a reported quarter), `M3` (a computed observation). Every output line must cite ids that exist; anything else is dropped and the count is shown on the dossier. Quotes the extraction model paraphrased or invented are dropped before analysis.
- **Arithmetic is not judgment.** Trajectory, leverage, working-capital cycle and cash conversion are computed in Python from the statements. They cost nothing and cannot hallucinate. The model is spent only where reading and reasoning are needed.
- **Model routing.** Fast tier (default `gemini-2.5-flash-lite`) for bulk extraction; smart tier (default `gemini-2.5-flash`) for one reconciliation call per dossier and for "why/compare/risk" questions. Any OpenAI-compatible endpoint works. Every call is cached on (model, prompt) in SQLite and metered; the app shows what each brief and dossier cost.
- **Alerts run on the device.** The feed ships 120 sessions of EOD data, so moving a slider re-ranks the brief and re-runs the backtest instantly, with no round trip.
- **Vanilla JS, no build.** The app is static files and JSON, so it hosts anywhere and works without the server.
- **Sample data is fictional.** So the demo runs offline without attaching invented facts to real companies.

### Left out, deliberately

- Real push notifications (needs a push service and subscriptions). The app fires local notifications for new alerts when opened, and installs to the home screen.
- BSE as a second filings source, and intraday data. EOD is what the brief asked for.
- Embeddings. BM25 over page-tagged chunks is enough to pick the ~10 forward-looking passages from a transcript, and needs no extra model or index.
- Accounts, multi-user state, and editing the portfolio in the app.

### Next

1. A server-side scheduler (after market close) that refreshes, diffs against the last run and sends Web Push.
2. "Since you last looked" as the default window, from the cleared queue.
3. Scorecard history: a management credibility score per company across years, not just the last four calls.
4. Contingent liabilities, related-party transactions and auditor remarks pulled from annual-report notes into the bear case.
5. An evaluation set: ~50 real results filings with hand-checked numbers, to measure the parser and model fallback.

## Layout

```
config/portfolio.json   holdings, research demo set, default thresholds
finn/sources/           prices, nse, screener, documents (PDF text, chunking, BM25, quote check)
finn/push/              classify, score, enrich, alerts, digest
finn/pull/              metrics, extract, reconcile, dossier (+ grounded ask)
finn/llm.py             model router, cache, cost ledger
finn/server.py          Flask: app, data, /api/research, /api/ask, /api/refresh
web/                    the app (index.html, styles.css, js/)
tests/                  rules, results parser, grounding contract, end-to-end dossier with mocks
```
