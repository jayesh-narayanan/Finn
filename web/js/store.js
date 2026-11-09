// Data + preferences. Works against the FINN server or a static folder of JSON.

const PREFS_KEY = "finn.prefs.v1";
const DONE_KEY = "finn.done.v1";

export const CATEGORY_INFO = {
  results: ["Results", "Quarterly or annual numbers"],
  "M&A": ["M&A", "Acquisitions, mergers, stake sales"],
  management_change: ["Management change", "CEO, CFO, MD or directors joining or leaving"],
  litigation: ["Legal or regulatory", "Orders, penalties, warnings, insolvency"],
  fund_raise: ["Fund raise", "Rights, QIP, preferential, buyback, debt"],
  credit_rating: ["Credit rating", "Upgrades, downgrades, reaffirmations"],
  pledging: ["Promoter pledge", "Promoter shares pledged or released"],
  insider_trading: ["Insider trade", "Promoters or insiders buying or selling"],
  capex: ["Capex", "New plants and capacity"],
  press_release: ["Company update", "Orders, launches, partnerships, monthly sales"],
  dividend: ["Dividend", "Cash payouts and record dates"],
  bonus_split: ["Bonus or split", "Share count changes, no cash"],
  earnings_call: ["Earnings call", "Call notices, transcripts, recordings"],
  investor_meetings: ["Investor meeting", "One-on-ones and conferences"],
  agm_egm: ["Shareholder meeting", "AGM and EGM notices and results"],
  postal_ballot: ["Postal ballot", "Shareholder votes by post"],
  unclassified: ["Routine filing", "Compliance paperwork"],
};
export const catLabel = (c) => (CATEGORY_INFO[c] || [c])[0];

const S = {
  feed: null, research: [], api: false, llm: false,
  prefs: null, done: {}, shift: 0,
};
export default S;

export async function load() {
  const [feed, idx, health] = await Promise.all([
    getJSON("data/feed.json"),
    getJSON("data/research/index.json").catch(() => []),
    fetch("api/health", { signal: AbortSignal.timeout(8000) }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);
  S.feed = feed;
  S.research = idx;
  S.api = !!health;
  S.llm = !!health?.llm;
  // Sample data is stamped relative to when it was generated: slide it to "now" so windows stay meaningful.
  S.shift = feed.mode === "sample" ? Date.now() - Date.parse(feed.generated_at) : 0;
  for (const a of feed.announcements) a.t = Date.parse(a.ts) + S.shift;
  S.prefs = loadPrefs(feed.default_rules);
  try { S.done = JSON.parse(localStorage.getItem(DONE_KEY) || "{}"); } catch { S.done = {}; }
  return S;
}

export async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

function defaults(rules) {
  const route = {};
  for (const c of Object.keys(CATEGORY_INFO)) {
    route[c] = rules.alert_categories.includes(c) ? "alert" : rules.muted_categories.includes(c) ? "mute" : "brief";
  }
  return { price: rules.price_move_pct, volume: rules.volume_multiple, lookback: rules.volume_lookback, route, windowH: 24, notify: false };
}

function loadPrefs(rules) {
  const d = defaults(rules);
  try {
    const p = JSON.parse(localStorage.getItem(PREFS_KEY) || "null");
    return p ? { ...d, ...p, route: { ...d.route, ...p.route } } : d;
  } catch { return d; }
}
export function savePrefs() { try { localStorage.setItem(PREFS_KEY, JSON.stringify(S.prefs)); } catch {} }
export function resetPrefs() { S.prefs = defaults(S.feed.default_rules); savePrefs(); }

export function markDone(id, on = true) {
  if (on) S.done[id] = Date.now(); else delete S.done[id];
  try { localStorage.setItem(DONE_KEY, JSON.stringify(S.done)); } catch {}
}

export const nameOf = (sym) => S.feed.holdings.find((h) => h.symbol === sym)?.name || sym;

export async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `Request failed (${r.status})`);
  return j;
}
