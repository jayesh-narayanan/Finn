// The portfolio at a glance: what moved, what traded heavily, what filed.
import S, { catLabel } from "./store.js";
import { h, mount, fmt, sign, spark, sheet, svg } from "./ui.js";
import { tierOf } from "./rules.js";

let sortBy = "move";

export function render(root, rerender, sym) {
  const H = S.feed.holdings.filter((x) => x.close != null);
  const p = S.prefs;
  const up = H.filter((x) => x.chg_pct > 0).length;
  const weekAgo = Date.now() - 7 * 864e5;
  const filings = {};
  for (const a of S.feed.announcements) {
    if (a.t >= weekAgo && tierOf(a, p) !== "routine") filings[a.symbol] = (filings[a.symbol] || 0) + 1;
  }
  const sorted = [...H].sort(sortBy === "move" ? (a, b) => Math.abs(b.chg_pct) - Math.abs(a.chg_pct)
    : sortBy === "volume" ? (a, b) => b.vol_x - a.vol_x : (a, b) => a.name.localeCompare(b.name));
  const asof = H[0]?.asof;
  const unpriced = S.feed.holdings.filter((x) => x.close == null);

  mount(root, 
    h("header", { class: "top" }, h("span", { class: "brand" }, "Holdings"), h("span", { class: "spacer" }),
      h("span", { class: "date" }, asof ? `${fmt.day(asof)} close` : "")),
    h("p", { class: "muted small", style: { margin: "2px 0 0" } }, H.length
      ? `${up} up, ${H.length - up} down across ${H.length} holdings.`
      : "No prices in the last refresh. Run python -m finn doctor to see which price source works on your network."),
    unpriced.length && H.length ? h("p", { class: "small down", style: { margin: "4px 0 0" } },
      `No prices for ${unpriced.map((x) => x.name).join(", ")}.`) : null,
    h("div", { class: "heat", role: "list", "aria-label": "Day move by holding" }, [...H].sort((a, b) => b.chg_pct - a.chg_pct).map((x) =>
      h("button", {
        role: "listitem", title: `${x.name} ${fmt.pct(x.chg_pct)}`, "aria-label": `${x.name} ${fmt.pct(x.chg_pct)}`,
        style: { background: heat(x.chg_pct) }, onclick: () => (location.hash = `#holdings/${x.symbol}`),
      }, x.symbol.slice(0, 5)))),
    h("div", { class: "chips", role: "group", "aria-label": "Sort" },
      [["move", "Biggest moves"], ["volume", "Heaviest volume"], ["name", "A to Z"]].map(([k, l]) =>
        h("button", { class: "chip", "aria-pressed": String(sortBy === k), onclick: () => { sortBy = k; rerender(); } }, l))),
    h("div", { class: "rows", style: { marginTop: "8px" } }, sorted.map((x) => h("button", {
      class: "hold", onclick: () => (location.hash = `#holdings/${x.symbol}`),
    },
      h("div", {},
        h("div", { class: "co" }, x.name),
        h("div", { class: "sub" }, x.sector,
          x.vol_x >= p.volume ? h("span", { class: "flag vol" }, `${x.vol_x.toFixed(1)}× vol`) : null,
          filings[x.symbol] ? h("span", { class: "flag f" }, `${filings[x.symbol]} ${filings[x.symbol] === 1 ? "filing" : "filings"}`) : null)),
      spark(x.spark),
      h("div", { class: "px" }, h("div", { class: `chg ${sign(x.chg_pct)}` }, fmt.pct(x.chg_pct)), h("div", { class: "sub" }, fmt.px(x.close)))))),
  );
  if (sym) openHolding(sym);
}

function heat(v) {
  const k = Math.min(85, 12 + Math.abs(v) * 14);
  return `color-mix(in srgb, ${v >= 0 ? "var(--up)" : "var(--down)"} ${k}%, var(--surface))`;
}

function openHolding(sym) {
  const x = S.feed.holdings.find((y) => y.symbol === sym);
  const series = S.feed.prices[sym];
  if (!x || !series) return;
  const recent = S.feed.announcements.filter((a) => a.symbol === sym && a.t >= Date.now() - 30 * 864e5).sort((a, b) => b.t - a.t);
  const material = recent.filter((a) => tierOf(a, S.prefs) !== "routine");
  const routineN = recent.length - material.length;
  sheet(x.name,
    h("div", { class: "muted small" }, `${x.sector}. Close ${fmt.px(x.close)}, `,
      h("span", { class: sign(x.chg_pct) }, fmt.pct(x.chg_pct)), " on the day, ",
      h("span", { class: sign(x.chg_1m) }, fmt.pct(x.chg_1m)), " over a month."),
    priceChart(series, S.prefs),
    h("div", { class: "legend" }, h("span", {}, h("i", { style: { background: "var(--ink)" } }), "Close"),
      h("span", {}, h("i", { style: { background: "var(--line)" } }), "Volume"),
      h("span", {}, h("i", { style: { background: "var(--signal)" } }), `Over ${S.prefs.volume}× average`)),
    h("h3", { style: { marginTop: "18px", fontSize: "15px" } }, "Filings, last 30 days"),
    material.length ? h("ul", { class: "notes" }, material.map((a) =>
      h("li", { class: tierOf(a, S.prefs) === "act" ? "bad" : "" },
        h("b", {}, catLabel(a.category)), ` ${fmt.ago(a.t)}. `, h("span", { class: "muted" }, a.summary || a.subject))))
      : h("p", { class: "muted small" }, "Nothing material in the last 30 days."),
    routineN ? h("p", { class: "tiny faint" }, `Plus ${routineN} routine ${routineN === 1 ? "filing" : "filings"}.`) : null,
    h("div", { style: { display: "flex", gap: "8px", marginTop: "14px" } },
      h("a", { class: "btn primary", href: `#research/${sym}` }, `Research ${x.name}`)));
}

export function priceChart(p, prefs, n = 90) {
  const d = p.d.slice(-n), c = p.c.slice(-n), v = p.v.slice(-n);
  const W = 340, H = 150, top = 8, vh = 34, ph = H - vh - top - 14;
  const lo = Math.min(...c), hi = Math.max(...c), span = hi - lo || 1;
  const vmax = Math.max(...v) || 1;
  const x = (i) => (i / (c.length - 1)) * (W - 4) + 2;
  const y = (val) => top + ph - ((val - lo) / span) * ph;
  const lb = prefs.lookback;
  const fullV = p.v, off = p.v.length - v.length;
  const bars = v.map((vol, i) => {
    const j = i + off;
    const win = fullV.slice(Math.max(0, j - lb), j).filter(Boolean);
    const avg = win.length ? win.reduce((a, b) => a + b, 0) / win.length : vol;
    const hot = win.length >= 5 && vol / avg >= prefs.volume;
    const bh = (vol / vmax) * vh;
    return `<rect x="${(x(i) - 1.3).toFixed(1)}" y="${(H - 12 - bh).toFixed(1)}" width="2.6" height="${bh.toFixed(1)}" fill="${hot ? "var(--signal)" : "var(--line)"}"/>`;
  }).join("");
  const line = c.map((val, i) => `${x(i).toFixed(1)},${y(val).toFixed(1)}`).join(" ");
  return svg(`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Close and volume, last ${c.length} sessions">
    ${bars}<polyline fill="none" stroke="var(--ink)" stroke-width="1.6" stroke-linejoin="round" points="${line}"/>
    <text x="2" y="${H - 1}">${fmt.day(d[0])}</text><text x="${W - 2}" y="${H - 1}" text-anchor="end">${fmt.day(d.at(-1))}</text>
    <text x="${W - 2}" y="${top + 2}" text-anchor="end">${fmt.px(hi)}</text><text x="${W - 2}" y="${top + ph}" text-anchor="end">${fmt.px(lo)}</text></svg>`);
}
