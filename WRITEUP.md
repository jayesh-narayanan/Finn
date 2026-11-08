# FINN — covering note (draft, edit into your own voice)

**What I built.** FINN is a mobile web app with three screens. *Brief* is the push side: it opens on the filter itself ("5 of 67 filings need you"), shows the moves that crossed my thresholds, then three visibly different tiers: full cards for what needs me (results with Revenue/EBITDA/PBT/PAT QoQ and YoY read from the filing; management changes with the incoming person's background), one-line rows for what's worth knowing, and a single folded line for routine paperwork. I clear it to zero by swiping. *Research* is the pull side, for any NSE symbol: it leads with promises vs delivery, every piece of guidance from the last four calls checked against the numbers reported since, with every line linked to the quote or figure behind it. *Watch* is where I define "unusual" and route each filing type to Alert, Brief or Mute, with a live backtest showing how many alerts a setting would have produced over 90 sessions.

**Key decisions.**
- Rules before models: classification and materiality scoring are deterministic and explain themselves; models only summarise what rules can't.
- Results numbers are parsed from the filing's consolidated table; the model is a fallback whose numbers must appear in the PDF.
- A grounding contract for research: the model only sees evidence ids (verified quotes, reported quarters, computed observations) and must cite them; uncited lines are dropped and counted. Arithmetic (trajectory, leverage, cash conversion) never goes to a model.
- Routing and caching: a fast model for extraction, one smart-model call per dossier, everything cached and metered. The app shows what each output cost.
- Alert rules run on the phone so thresholds can be calibrated instantly against history.

**Left out.** Real push notifications (local notifications only), BSE, intraday data, embeddings, accounts.

**Next.** An after-close scheduler with Web Push; "since you last looked" as the default window; a multi-year management credibility score; annual-report notes (contingent liabilities, RPTs, auditor remarks) in the bear case; an evaluation set of hand-checked results filings.

Links: prototype <URL> · repo <URL> · build history <URL>
