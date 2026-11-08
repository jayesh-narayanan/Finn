// The investor's own definition of "unusual" and "important". Evaluated on the device so
// changing a threshold re-ranks the brief and re-runs the backtest instantly.
// Mirrors finn/push/alerts.py and finn/push/score.py's tier().

export function tierOf(item, prefs) {
  const route = prefs.route[item.category] || "brief";
  if (route === "mute") return "routine";
  if (route === "alert") return item.score >= 40 ? "act" : "routine";
  return item.score >= 70 ? "act" : item.score >= 40 ? "know" : "routine";
}

export const isFilingAlert = (item, prefs) => prefs.route[item.category] === "alert" && item.score >= 40;

export function moveAlerts(series, prefs, sessions = 1) {
  const out = [];
  for (const [sym, p] of Object.entries(series)) {
    const { d, c, v } = p;
    for (let i = Math.max(1, d.length - sessions); i < d.length; i++) {
      const move = (c[i] / c[i - 1] - 1) * 100;
      const hit = { symbol: sym, date: d[i], i };
      if (Math.abs(move) >= prefs.price) out.push({ ...hit, kind: "price", value: move });
      const win = v.slice(Math.max(0, i - prefs.lookback), i).filter(Boolean);
      if (win.length >= 5 && v[i]) {
        const mult = v[i] / (win.reduce((a, b) => a + b, 0) / win.length);
        if (mult >= prefs.volume) out.push({ ...hit, kind: "volume", value: mult });
      }
    }
  }
  return out;
}

// Group a symbol's price + volume hits on the same day into one alert.
export function mergeByDay(hits) {
  const m = new Map();
  for (const a of hits) {
    const k = a.symbol + a.date;
    const cur = m.get(k) || { symbol: a.symbol, date: a.date };
    cur[a.kind] = a.value;
    m.set(k, cur);
  }
  return [...m.values()].sort((a, b) => (b.date > a.date ? 1 : b.date < a.date ? -1 : Math.abs(b.price || 0) - Math.abs(a.price || 0)));
}

export function backtest(series, prefs, sessions = 90) {
  const hits = mergeByDay(moveAlerts(series, prefs, sessions));
  const anyDates = Object.values(series)[0]?.d || [];
  const days = anyDates.slice(-sessions);
  const weeks = [];
  for (let i = 0; i < days.length; i += 5) {
    const span = new Set(days.slice(i, i + 5));
    weeks.push(hits.filter((x) => span.has(x.date)).length);
  }
  return { hits, weeks, sessions: days.length, perWeek: hits.length / Math.max(1, days.length / 5), first: days[0], last: days[days.length - 1] };
}
