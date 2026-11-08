// Push: the windowed digest + triggered alerts, as a triage queue you clear to zero.
import S, { catLabel, markDone, savePrefs, nameOf, postJSON, getJSON, load } from "./store.js";
import { h, mount, fmt, sign, swipeable, toast, initials, sheet } from "./ui.js";
import { tierOf, isFilingAlert, moveAlerts, mergeByDay } from "./rules.js";

const WINDOWS = [[24, "24 hours"], [72, "3 days"], [168, "7 days"], [720, "30 days"]];

export function render(root, rerender) {
  const p = S.prefs;
  const now = Date.now();
  const inWin = S.feed.announcements.filter((a) => a.t >= now - p.windowH * 3600e3 && a.t <= now + 60e3);
  const all = inWin.map((a) => ({ a, tier: tierOf(a, p), alert: isFilingAlert(a, p), done: !!S.done[a.id] }));
  const act = all.filter((x) => x.tier === "act").sort(byPriority);
  const know = all.filter((x) => x.tier === "know").sort(byPriority);
  const routine = all.filter((x) => x.tier === "routine");
  const open = act.filter((x) => !x.done);
  const cleared = [...act, ...know].filter((x) => x.done);

  const moves = mergeByDay(moveAlerts(S.feed.prices, p, 1));
  const lastSession = Object.values(S.feed.prices)[0]?.d.at(-1);

  mount(root, 
    topbar(rerender),
    hero(open.length, inWin.length, act, know.filter((x) => !x.done).length, routine.length, p.windowH),
    h("div", { class: "chips", role: "group", "aria-label": "Time window" },
      WINDOWS.map(([hrs, label]) => h("button", {
        class: "chip", "aria-pressed": String(p.windowH === hrs),
        onclick: () => { p.windowH = hrs; savePrefs(); rerender(); },
      }, label))),
    movesStrip(moves, lastSession),
    inWin.length === 0 ? emptyWindow(rerender) : null,
    act.length ? [
      h("div", { class: "sec" }, h("h2", {}, "Needs you"), h("span", { class: "count" }, open.length ? `${open.length} open` : "all cleared")),
      open.map((x) => actCard(x, rerender)),
    ] : null,
    know.some((x) => !x.done) ? [
      h("div", { class: "sec" }, h("h2", {}, "Worth knowing"), h("span", { class: "count" }, String(know.filter((x) => !x.done).length))),
      h("div", { class: "rows" }, know.filter((x) => !x.done).map((x) => knowRow(x, rerender))),
    ] : null,
    routine.length ? routineFold(routine) : null,
    cleared.length ? clearedFold(cleared, rerender) : null,
    footer(),
  );
}

function byPriority(x, y) {
  return (y.alert - x.alert) || (y.a.score - x.a.score) || (y.a.t - x.a.t);
}

function topbar(rerender) {
  const f = S.feed;
  return h("header", { class: "top" },
    h("span", { class: "brand" }, "FINN"),
    h("span", { class: "date" }, new Date().toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })),
    h("span", { class: "spacer" }),
    f.mode === "sample"
      ? h("button", { class: "pill sample", onclick: () => sampleNote() }, "Sample data")
      : h("span", { class: "pill live", title: `Built ${fmt.when(f.generated_at)}` }, `Updated ${fmt.ago(f.generated_at)}`),
    S.api ? h("button", { class: "icon-btn", "aria-label": "Refresh filings and prices", onclick: () => refresh(rerender) },
      h("span", { html: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/></svg>' })) : null,
  );
}

function sampleNote() {
  sheet("You're looking at sample data",
    h("p", {}, "Every company, person and number on this screen is fictional, so the app can be tried without a network or API key."),
    h("p", { class: "muted" }, "Run ", h("code", {}, "python -m finn refresh"), " to replace it with your real portfolio's NSE filings and prices."));
}

async function refresh(rerender) {
  try {
    const { job } = await postJSON("api/refresh", {});
    toast("Refreshing filings and prices…");
    for (;;) {
      await new Promise((r) => setTimeout(r, 2500));
      const j = await getJSON(`api/jobs/${job}`);
      if (j.status === "done") { await load(); rerender(); toast("Brief updated"); return; }
      if (j.status === "error") { toast(`Refresh failed: ${j.error}`); return; }
    }
  } catch (e) { toast(e.message); }
}

function hero(open, total, act, knowOpen, routineN, windowH) {
  const win = WINDOWS.find(([x]) => x === windowH)?.[1] || `${windowH} hours`;
  const zero = open === 0;
  return h("section", { class: "hero" },
    h("div", { class: `ratio${zero ? " zero" : ""}`, "aria-hidden": "true" },
      h("span", { class: "n" }, String(open)), h("span", { class: "of" }, `of ${total}`)),
    h("h1", {}, zero
      ? (total ? "Nothing left that needs you" : "A quiet stretch")
      : `${open === 1 ? "filing needs" : "filings need"} you, from the last ${win}`),
    h("p", {}, total
      ? `${knowOpen} more worth knowing. ${routineN} routine ${routineN === 1 ? "filing" : "filings"} folded away.` +
        (zero ? "" : " Swipe a card left when you're done with it.")
      : "No filings from your holdings in this window."),
    act.length ? h("div", { class: "meter", "aria-label": `${act.length - open} of ${act.length} cleared` },
      act.map((x) => h("i", { class: x.done ? "done" : "" }))) : null,
  );
}

function movesStrip(moves, lastSession) {
  const p = S.prefs;
  const when = lastSession ? fmt.day(lastSession) : "";
  if (!moves.length) {
    return h("p", { class: "small muted", style: { margin: "14px 0 0" } },
      `No holding moved more than ±${p.price}% or traded ${p.volume}× its average at the ${when} close.`);
  }
  return [
    h("div", { class: "sec" }, h("h2", {}, "Unusual moves"), h("span", { class: "count" }, `${when} close`)),
    h("div", { class: "moves" }, moves.map((m) => h("button", {
      class: "move", onclick: () => { location.hash = `#holdings/${m.symbol}`; },
    },
      h("div", { class: "sym" }, nameOf(m.symbol)),
      h("div", { class: `val ${sign(m.price ?? 0)}` }, m.price != null ? fmt.pct(m.price) : `${m.volume.toFixed(1)}×`),
      h("div", { class: "why" }, [
        m.price != null ? null : "volume",
        m.price != null && m.volume != null ? `on ${m.volume.toFixed(1)}× volume` : null,
        m.price != null && m.volume == null ? "price move" : null,
      ].filter(Boolean).join(" "))))),
  ];
}

function emptyWindow(rerender) {
  const latest = S.feed.announcements.reduce((m, a) => (a.t > m ? a.t : m), 0);
  const p = S.prefs;
  const next = WINDOWS.find(([hrs]) => hrs > p.windowH && latest >= Date.now() - hrs * 3600e3);
  return h("div", { class: "empty" },
    h("h3", {}, "Nothing filed in this window"),
    h("p", {}, latest ? `The most recent filing came in ${fmt.when(latest)}.` : "There are no filings in the data yet."),
    next ? h("button", { class: "btn primary", onclick: () => { p.windowH = next[0]; savePrefs(); rerender(); } }, `Show the last ${next[1]}`) : null);
}

function done(a, rerender, label = "Cleared") {
  markDone(a.id);
  rerender();
  toast(`${label}: ${a.name}`, { label: "Undo", fn: () => { markDone(a.id, false); rerender(); } });
}

// ---------------------------------------------------------------- act cards
function actCard(x, rerender) {
  const a = x.a;
  const el = h("article", { class: `act${x.alert ? " alerted" : ""}`, "aria-label": `${a.name}: ${catLabel(a.category)}` },
    h("div", { class: "head" },
      h("span", { class: "co" }, a.name),
      h("span", { class: "kind" }, kindLabel(a)),
      h("span", { class: "when" }, fmt.ago(a.t))),
    cardBody(a),
    h("div", { class: "why-line" }, `Why it's here: ${a.why.join(", ")}`),
    h("div", { class: "foot" },
      h("a", { class: "btn", href: `#research/${a.symbol}` }, "Research"),
      a.url ? h("a", { class: "btn ghost", href: a.url, target: "_blank", rel: "noopener" }, "Open filing") : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn", onclick: () => done(a, rerender) }, "Done")),
  );
  swipeable(el, () => done(a, rerender));
  return el;
}

function kindLabel(a) {
  if (a.card?.type === "results") return `Results ${a.card.periods?.[0] || ""}`.trim();
  return catLabel(a.category);
}

function cardBody(a) {
  const c = a.card;
  if (c?.type === "results") return resultsBody(c);
  if (c?.type === "mgmt") return mgmtBody(c);
  return h("div", { class: "headline" }, a.summary || cleanText(a.text) || a.subject);
}

export function cleanText(t) {
  return (t || "").replace(/^.*?\bha(?:s|ve) informed the exchange (?:about|regarding|that|of)\s*/i, "").replace(/^\w/, (m) => m.toUpperCase());
}

function rowOf(c, m) { return c.rows.find((r) => r.metric === m); }

// A deterministic first read of the numbers: what moved, and whether profit kept up with sales.
export function resultsHeadline(c) {
  const rev = rowOf(c, "Revenue"), eb = rowOf(c, "EBITDA"), pat = rowOf(c, "PAT");
  const mg = c.ebitda_margin;
  if (!rev) return "Results filed";
  const r = rev.yoy, e = eb?.yoy, pt = pat?.yoy;
  if (r == null) return "Results filed";
  if (r < -2) return `Sales fell ${Math.abs(r).toFixed(1)}% YoY${pt != null ? `; profit ${pt >= 0 ? "up" : "down"} ${Math.abs(pt).toFixed(1)}%` : ""}`;
  if (e != null && e < r - 3) return `Sales up ${r.toFixed(1)}% YoY but EBITDA only ${fmt.pct(e)}: margin ${mg ? fmt.bps(mg.yoy_bps) : "down"}`;
  if (e != null && e > r + 3) return `Operating leverage: EBITDA ${fmt.pct(e)} on sales ${fmt.pct(r)} YoY`;
  return `Steady quarter: sales ${fmt.pct(r)}, profit ${fmt.pct(pt)} YoY`;
}

function resultsBody(c) {
  const [cur] = c.periods || [];
  return h("div", { class: "body" },
    h("div", { class: "headline" }, resultsHeadline(c)),
    h("table", { class: "rtable" },
      h("thead", {}, h("tr", {}, h("th", {}, c.unit), h("th", {}, cur || "This qtr"), h("th", {}, "QoQ"), h("th", {}, "YoY"))),
      h("tbody", {}, c.rows.map((r) => h("tr", {},
        h("td", {}, r.metric),
        h("td", { class: "v" }, fmt.num(r.cur)),
        h("td", {}, h("span", { class: `delta ${sign(r.qoq)}` }, fmt.pct(r.qoq))),
        h("td", {}, h("span", { class: `delta ${sign(r.yoy)}` }, fmt.pct(r.yoy))))))),
    c.ebitda_margin ? h("div", { class: "margin-line" },
      `EBITDA margin ${c.ebitda_margin.cur}%: `, h("span", { class: sign(c.ebitda_margin.qoq_bps) }, fmt.bps(c.ebitda_margin.qoq_bps)), " QoQ, ",
      h("span", { class: sign(c.ebitda_margin.yoy_bps) }, fmt.bps(c.ebitda_margin.yoy_bps)), " YoY") : null,
    h("div", { class: "src" }, [
      c.source === "filing" ? `Read from the ${c.basis || ""} results filing` : "From standardised quarterly statements",
      c.note ? `. ${c.note}` : "",
    ].join("")),
  );
}

function mgmtBody(c) {
  const changes = [...c.changes].sort((x) => (x.change === "appointed" ? 1 : -1));
  return h("div", { class: "body" }, changes.map((p) => {
    const out = ["resigned", "retired", "ceased"].includes(p.change);
    return h("div", { class: "person" },
      h("div", { class: `avatar${out ? " out" : ""}`, "aria-hidden": "true" }, initials(p.person || "?")),
      h("div", {},
        h("div", { class: "name" }, p.person),
        h("div", { class: "role" }, [out ? "Leaving" : p.change === "appointed" ? "Joining" : cap(p.change), p.role ? ` as ${p.role}` : "",
          p.effective ? `, effective ${p.effective}` : ""].join("")),
        p.reason && out ? h("div", { class: "role" }, `Stated reason: ${p.reason}`) : null,
        p.bio ? h("div", { class: "bio" }, p.bio, h("span", { class: "tag-src" }, "from the filing")) : null,
        p.web_bio ? h("div", { class: "bio" }, p.web_bio, h("span", { class: "tag-src" }, "from the web")) : null,
        p.previous?.length ? h("ul", { class: "prev" }, p.previous.map((x) => h("li", {}, x))) : null));
  }));
}
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");

// ---------------------------------------------------------------- know rows
function knowRow(x, rerender) {
  const a = x.a;
  const el = h("div", { class: "row", role: "button", tabindex: "0", "aria-expanded": "false" },
    h("div", {}, h("span", { class: "co" }, a.name), h("span", { class: "kind" }, catLabel(a.category))),
    h("span", { class: "when" }, fmt.ago(a.t)),
    h("div", { class: "line" }, a.summary || cleanText(a.text) || a.subject),
    h("div", { class: "more" },
      a.card ? cardBody(a) : null,
      h("div", { class: "why-line" }, `Ranked here because: ${a.why.join(", ")}`),
      h("div", { class: "foot" },
        h("a", { class: "btn", href: `#research/${a.symbol}` }, "Research"),
        a.url ? h("a", { class: "btn ghost", href: a.url, target: "_blank", rel: "noopener" }, "Open filing") : null,
        h("button", { class: "btn", onclick: (e) => { e.stopPropagation(); done(a, rerender); } }, "Done"))));
  const toggle = (e) => {
    if (e.target.closest("a,button")) return;
    el.classList.toggle("open");
    el.setAttribute("aria-expanded", String(el.classList.contains("open")));
  };
  el.addEventListener("click", toggle);
  el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(e); } });
  swipeable(el, () => done(a, rerender));
  return el;
}

// ---------------------------------------------------------------- routine
function routineFold(items) {
  const groups = new Map();
  for (const x of items) {
    const k = routineLabel(x.a);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(x.a);
  }
  const sorted = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  const wrap = h("section", { class: "fold" });
  const btn = h("button", { "aria-expanded": "false", onclick: () => {
    wrap.classList.toggle("open"); btn.setAttribute("aria-expanded", String(wrap.classList.contains("open")));
  } },
  h("span", {}, `${items.length} routine ${items.length === 1 ? "filing" : "filings"} folded`),
  h("span", { class: "faint small" }, `mostly ${sorted.slice(0, 2).map(([k]) => lowerFirst(k)).join(" and ")}`),
  h("span", { class: "chev", "aria-hidden": "true" }, "›"));
  wrap.append(btn, h("div", { class: "groups" }, sorted.map(([k, list]) => h("details", { class: "group" },
    h("summary", {}, k, h("span", { class: "n" }, String(list.length))),
    h("ul", {}, list.sort((a, b) => b.t - a.t).map((a) => h("li", {},
      a.url ? h("a", { href: a.url, target: "_blank", rel: "noopener" }, a.name) : h("span", {}, a.name),
      h("span", { class: "t" }, fmt.ago(a.t)))))))));
  return wrap;
}

const lowerFirst = (s) => (/^[A-Z]{2}/.test(s) ? s : s[0].toLowerCase() + s.slice(1));

function routineLabel(a) {
  const s = `${a.subject} ${a.text}`.toLowerCase();
  if (/trading window/.test(s)) return "Trading window notices";
  if (/newspaper|publication/.test(s)) return "Newspaper ads";
  if (/74 ?\(5\)|depositories/.test(s)) return "Depository certificates";
  if (/esop|esos|esps/.test(s)) return "ESOP allotments";
  if (/share certificate/.test(s)) return "Lost share certificates";
  if (a.category === "results") return "Results date notices";
  return catLabel(a.category);
}

function clearedFold(items, rerender) {
  const wrap = h("section", { class: "fold" });
  const btn = h("button", { "aria-expanded": "false", onclick: () => {
    wrap.classList.toggle("open"); btn.setAttribute("aria-expanded", String(wrap.classList.contains("open")));
  } }, h("span", {}, `${items.length} cleared`), h("span", { class: "chev", "aria-hidden": "true" }, "›"));
  wrap.append(btn, h("div", { class: "groups" }, h("ul", { class: "group", style: { listStyle: "none", margin: 0 } },
    items.map((x) => h("li", {}, h("span", {}, `${x.a.name}: ${catLabel(x.a.category).toLowerCase()}`),
      h("button", { class: "btn ghost t", onclick: () => { markDone(x.a.id, false); rerender(); } }, "Restore"))))));
  return wrap;
}

function footer() {
  const c = S.feed.cost;
  if (!c) return null;
  return h("p", { class: "tiny faint", style: { marginTop: "28px" } },
    `This brief took ${c.calls} model ${c.calls === 1 ? "call" : "calls"} (${c.cached} cached) to build, about $${c.usd.toFixed(4)}. ` +
    "Classification and scoring are rule-based; models only summarise what rules can't.");
}
