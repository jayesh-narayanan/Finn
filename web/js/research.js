// Pull: research grounded in primary sources, led by "what was promised vs what was delivered".
import S, { getJSON, postJSON } from "./store.js";
import { h, mount, fmt, sheet, svg, toast } from "./ui.js";

const VERDICT = {
  delivered: "Delivered", on_track: "On track", behind: "Behind", missed: "Missed",
  walked_back: "Walked back", too_early: "Too early", agrees: "Matches", diverges: "Diverges",
};
const cache = {};
let building = null; // {symbol, job, step, pct}

export async function render(root, rerender, sym) {
  if (!sym) return renderList(root, rerender);
  sym = sym.toUpperCase();
  if (!cache[sym]) {
    const known = S.research.find((x) => x.symbol === sym);
    if (known) {
      mount(root, h("p", { class: "muted", style: { padding: "24px 0" } }, "Opening research…"));
      try { cache[sym] = await getJSON(`data/research/${sym}.json`); } catch { /* fall through */ }
    }
  }
  if (!cache[sym]) return renderList(root, rerender, sym);
  renderDossier(root, cache[sym]);
}

// ------------------------------------------------------------------ list + build
function renderList(root, rerender, wanted) {
  const input = h("input", {
    name: "symbol", value: wanted || "", placeholder: S.api ? "Symbol or company, e.g. Pidilite" : "NSE symbol, e.g. PIDILITIND", autocomplete: "off",
    autocapitalize: "characters", spellcheck: "false", "aria-label": "NSE symbol or company name", maxlength: "60",
  });
  const form = h("form", { class: "search", onsubmit: (e) => { e.preventDefault(); go(input.value, rerender); } },
    input, h("button", { class: "btn primary", type: "submit" }, "Open"));
  mount(root, 
    h("header", { class: "top" }, h("span", { class: "brand" }, "Research")),
    h("p", { class: "muted small", style: { margin: "2px 0 0" } },
      "Any NSE-listed company. Answers come only from its annual reports, earnings-call transcripts and reported financials, with every claim linked to its source."),
    form,
    building ? progress(building) : null,
    wanted && !building ? missing(wanted, rerender) : null,
    S.research.length ? [
      h("div", { class: "sec" }, h("h2", {}, "Ready to read"), h("span", { class: "count" }, String(S.research.length))),
      h("div", { class: "dlist" }, S.research.map((d) => h("a", { class: "dlink", href: `#research/${d.symbol}` },
        h("div", {}, h("div", { class: "co" }, d.name), h("div", { class: "tiny faint" }, `${d.symbol}, built ${fmt.ago(d.generated_at)}`)),
        h("div", { class: "score" }, d.promises ? [dots(d), h("div", {}, `${d.kept} of ${d.promises} promises kept`)] : "Numbers only")))),
    ] : null,
    S.api ? [
      h("div", { class: "sec" }, h("h2", {}, "From your holdings")),
      h("div", { class: "chips", style: { flexWrap: "wrap" } }, S.feed.holdings.map((x) =>
        h("button", { class: "chip", onclick: () => go(x.symbol, rerender) }, x.name))),
    ] : null,
  );
  if (!wanted) setTimeout(() => input.focus({ preventScroll: true }), 50);
}

function dots(d) {
  const k = d.kept, n = d.promises;
  return h("span", { class: "dots", "aria-hidden": "true" }, Array.from({ length: n }, (_, i) => h("i", { class: i < k ? "k" : "m" })));
}

function missing(sym, rerender) {
  return h("div", { class: "empty", style: { marginTop: "12px" } },
    h("h3", {}, `No research on ${sym} yet`),
    S.api
      ? [h("p", {}, "Building it reads the last four call transcripts and the latest annual report. It takes two to four minutes; you can leave this screen and come back."),
         h("button", { class: "btn primary", onclick: () => build(sym, rerender) }, `Build research on ${sym}`)]
      : h("p", {}, "Building new research needs the FINN server running (python -m finn serve). The companies below are ready to read."));
}

async function go(raw, rerender) {
  const q = raw.trim().replace(/^(NSE|BSE)\s*:\s*/i, "").replace(/\.(NS|BO)$/i, "");
  if (q.length < 2) { toast("Type an NSE symbol or a company name"); return; }
  const sym = q.toUpperCase();
  const known = S.research.find((x) => x.symbol === sym || x.name.toUpperCase() === sym);
  if (known) { location.hash = `#research/${known.symbol}`; return; }
  if (S.api) build(q, rerender);
  else if (/^[A-Z0-9&-]{2,20}$/.test(sym)) location.hash = `#research/${sym}`;
  else toast("Searching by name needs the FINN server. Try a symbol from the list below.");
}

async function build(query, rerender) {
  try {
    const { job } = await postJSON("api/research", { query });
    building = { symbol: query.toUpperCase(), job, step: "Finding the company", pct: 2 };
    location.hash = "#research";
    rerender();
    let misses = 0;
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      let j;
      try { j = await getJSON(`api/jobs/${job}`); misses = 0; } catch (e) {
        if (++misses < 5) continue;  // a slow response or a brief network blip: keep waiting
        throw new Error("Lost touch with the server while building. It may have restarted; try again.");
      }
      Object.assign(building, j);
      if (j.status === "done") {
        const sym = j.symbol;
        building = null;
        S.research = await getJSON("data/research/index.json").catch(() => S.research);
        delete cache[sym];
        location.hash = `#research/${sym}`;
        return;
      }
      if (j.status === "error") { building = null; toast(j.error); rerender(); return; }
      if (location.hash === "#research") rerender();
    }
  } catch (e) { building = null; toast(e.message); rerender(); }
}

function progress(b) {
  return h("div", { class: "progress", role: "status" },
    h("div", {}, h("b", {}, b.symbol), " ", h("span", { class: "muted" }, b.step)),
    h("div", { class: "bar" }, h("i", { style: { width: `${b.pct}%` } })));
}

// ------------------------------------------------------------------ dossier
function evidenceIndex(d) {
  const ev = {};
  for (const g of d.guidance || []) ev[g.id] = { kind: "said", ...g, text: g.statement };
  for (const c of d.claims || []) ev[c.id] = { kind: "said", ...c, text: c.claim };
  for (const c of d.pushback || []) ev[c.id] = { kind: "asked", ...c, text: c.question };
  const m = d.metrics;
  [...m.trajectory.notes, ...m.balance_sheet.notes, ...m.cash.notes].forEach((n, i) => { ev[`M${i + 1}`] = { kind: "computed", ...n }; });
  const q = m.trajectory.quarterly;
  // Quarterly ids are positional within the 8-quarter window the reconciler saw (see metrics.actuals_table).
  q?.periods.forEach((p, i) => {
    ev[`F:Q${i}`] = { kind: "reported", label: `${p} quarter`, rows: [
      ["Revenue", fmt.cr(q.revenue[i]), q.rev_yoy[i] != null ? `${fmt.pct(q.rev_yoy[i])} YoY` : ""],
      ["Margin", q.margin[i] != null ? `${q.margin[i]}%` : "—", ""],
      ["PAT", fmt.cr(q.pat[i]), q.pat_yoy[i] != null ? `${fmt.pct(q.pat_yoy[i])} YoY` : ""]] };
  });
  const t = m.trajectory;
  t.years.forEach((y, i) => {
    ev[`F:${y}`] = { kind: "reported", label: `${y} annual accounts`, rows: [
      ["Revenue", fmt.cr(t.revenue[i]), ""], ["Margin", t.margin[i] != null ? `${t.margin[i]}%` : "—", ""], ["PAT", fmt.cr(t.pat[i]), ""]] };
  });
  return ev;
}

function citeLabel(id, ev, d) {
  const e = ev[id];
  if (!e) return id;
  if (e.kind === "said" || e.kind === "asked") return `${e.quarter} call, p${e.page}`;
  if (e.kind === "reported") return e.label.replace(" annual accounts", " accounts");
  if (e.kind === "computed") return "From the accounts";
  return id;
}

function showEvidence(id, ev, d) {
  const e = ev[id];
  if (!e) return;
  const src = d.sources.find((s) => s.id === (e.src || e.cite));
  if (e.kind === "said" || e.kind === "asked") {
    sheet(`${e.quarter} earnings call`,
      h("p", { class: "muted small" }, `${e.kind === "asked" ? "An analyst asked" : "Management said"}${e.page ? `, on page ${e.page}` : ""}:`),
      h("blockquote", {}, `“${e.quote}”`),
      h("p", { class: "small" }, e.text),
      src?.url ? h("a", { class: "btn", href: src.url, target: "_blank", rel: "noopener" }, "Open transcript") :
        h("p", { class: "tiny faint" }, d.mode === "sample" ? "Fictional sample transcript." : ""));
  } else if (e.kind === "reported") {
    sheet(e.label,
      h("table", { class: "rtable" }, h("tbody", {}, e.rows.map(([k, v, x]) => h("tr", {}, h("td", {}, k), h("td", { class: "v" }, v), h("td", {}, x))))),
      h("p", { class: "tiny faint" }, "As reported in the company's results, standardised by screener.in."));
  } else if (e.kind === "computed") {
    sheet("Computed from the accounts", h("p", {}, e.text),
      h("p", { class: "tiny faint" }, `Arithmetic on ${d.sources.find((s) => s.id === e.cite)?.label?.toLowerCase() || "the statements"}; no model involved.`));
  }
}

function cites(ids, ev, d) {
  return h("div", { class: "cites" }, (ids || []).map((id) =>
    h("button", { class: "cite", onclick: () => showEvidence(id, ev, d) }, citeLabel(id, ev, d))));
}

function renderDossier(root, d) {
  const ev = evidenceIndex(d);
  const a = d.analysis;
  const m = d.metrics;
  const lenses = [["promises", "Promises"], ["story", "Story vs numbers"], ["case", "Bull and bear"], ["business", "Business"],
    ["numbers", "Numbers"], ["trajectory", "Trajectory"], ["balance", "Balance sheet"], ["cash", "Cash"], ["guidance", "Guidance"], ["sources", "Sources"]]
    .filter(([k]) => a || !["promises", "story", "case"].includes(k));
  const transcripts = d.sources.filter((s) => s.type === "transcript").length;
  const ar = d.sources.find((s) => s.type === "annual_report");
  const answers = h("div", { id: "answers" });

  mount(root, 
    h("div", { class: "dhead" },
      h("button", { class: "back", onclick: () => (location.hash = "#research") }, "‹ All research"),
      h("h1", {}, d.name),
      h("div", { class: "meta" },
        h("span", {}, `${d.symbol}, ${d.basis}`),
        h("span", {}, `${transcripts} call ${transcripts === 1 ? "transcript" : "transcripts"}${ar ? `, ${ar.label.toLowerCase()}` : ""}`),
        d.cost ? h("span", {}, `${d.cost.calls} model calls, $${d.cost.usd.toFixed(3)}`) : null,
        d.mode === "sample" ? h("span", { class: "pill sample" }, "Fictional sample") : h("span", {}, `Built ${fmt.ago(d.generated_at)}`))),
    a?.one_line ? h("div", { class: "verdict" }, h("span", { class: "lbl" }, "On management's credibility"), a.one_line) : null,
    h("nav", { class: "lens", "aria-label": "Jump to" }, lenses.map(([k, l]) =>
      h("button", { class: "chip", onclick: () => document.getElementById(`s-${k}`)?.scrollIntoView({ behavior: "smooth" }) }, l))),
    a ? promises(a, ev, d) : noModel(),
    a ? story(a, ev, d) : null,
    a ? bullBear(a, ev, d) : null,
    business(d),
    numbers(m),
    trajectory(m),
    balance(m),
    cash(m),
    guidance(d, ev),
    sources(d, a),
    answers,
    askBar(d, answers),
  );
}

function section(id, title, lede, ...body) {
  return h("section", { class: "dsec", id: `s-${id}` }, h("h2", {}, title), lede ? h("p", { class: "lede" }, lede) : null, ...body);
}

function noModel() {
  return section("promises", "Promises vs delivery", null, h("div", { class: "empty" },
    h("h3", {}, "Numbers only for now"),
    h("p", {}, "Reading what management promised needs a model key. Add FINN_LLM_API_KEY to .env and rebuild; everything below is computed from the accounts.")));
}

function promises(a, ev, d) {
  const sc = a.scorecard || [];
  const count = (v) => sc.filter((x) => v.includes(x.verdict)).length;
  return section("promises", "Promises vs delivery",
    "Each piece of guidance from the last four calls, checked against the numbers reported since.",
    h("div", { class: "tally" },
      h("span", {}, h("b", {}, String(count(["delivered", "on_track"]))), " kept or on track"),
      h("span", {}, h("b", {}, String(count(["behind", "missed", "walked_back"]))), " slipped"),
      h("span", {}, h("b", {}, String(count(["too_early"]))), " too early to tell")),
    h("div", { class: "card" }, sc.map((p) => h("div", { class: "promise" },
      h("div", { class: "top2" },
        h("div", { class: "what" }, p.promise, h("div", { class: "when" }, `Said on the ${p.made_in} call, for ${p.period}`)),
        h("span", { class: `vb ${p.verdict}` }, VERDICT[p.verdict] || p.verdict)),
      h("div", { class: "got" }, p.delivered),
      cites(p.evidence, ev, d)))));
}

function story(a, ev, d) {
  if (!a.narrative_gaps?.length) return null;
  return section("story", "Story vs numbers", "Where management's description of the business matches the accounts, and where it doesn't.",
    h("div", { class: "card" }, a.narrative_gaps.map((g) => h("div", { class: "gap" },
      h("div", {}, h("div", { class: "lbl" }, "Management says"), h("div", { class: "txt" }, g.says)),
      h("div", {}, h("div", { class: "lbl" }, "The numbers show"), h("div", { class: "txt" }, g.numbers_show)),
      h("div", { class: "foot2" }, h("span", { class: `vb ${g.verdict}` }, VERDICT[g.verdict]), cites(g.evidence, ev, d))))));
}

function bullBear(a, ev, d) {
  const side = (cls, title, items) => h("div", { class: `side ${cls}` }, h("h3", {}, title),
    h("ol", {}, items.map((x) => h("li", {}, x.text, cites(x.evidence, ev, d)))));
  return section("case", "Bull and bear", "The tightest case each way, using only what's in the sources.",
    h("div", { class: "bb" }, side("bull", "Bull case", a.bull || []), side("bear", "Bear case", a.bear || [])));
}

const SEG_COLORS = ["var(--ink)", "var(--calm)", "var(--signal)", "var(--up)", "var(--violet)", "var(--ink-3)"];

function business(d) {
  const s = d.snapshot || {};
  const segs = (s.segments || []).filter((x) => x.share_pct != null);
  return section("business", "Business", null,
    h("div", { class: "card", style: { padding: "12px 14px" } },
      h("p", { style: { margin: 0 } }, s.what_it_does || "No description available."),
      segs.length ? [
        h("div", { class: "segbar", role: "img", "aria-label": "Revenue mix" }, segs.map((x, i) =>
          h("i", { style: { flex: String(x.share_pct), background: SEG_COLORS[i % SEG_COLORS.length] } }))),
        h("ul", { class: "seglist" }, segs.map((x, i) => h("li", {},
          h("i", { style: { background: SEG_COLORS[i % SEG_COLORS.length] } }),
          h("span", {}, x.name, x.note ? h("span", { class: "faint small" }, ` ${x.note}`) : null),
          h("span", { class: "p" }, `${x.share_pct}%`)))),
      ] : (s.segments || []).length ? h("ul", { class: "notes" }, s.segments.map((x) => h("li", {}, `${x.name}${x.note ? `: ${x.note}` : ""}`))) : null,
      s.geography ? h("p", { class: "small muted", style: { margin: "8px 0 0" } }, s.geography) : null,
      s.customers ? h("p", { class: "small muted", style: { margin: "2px 0 0" } }, s.customers) : null,
      h("p", { class: "tiny faint" }, s.source === "A1" ? "From the annual report." : "From the company description.")));
}

function numbers(m) {
  return section("numbers", "Numbers at a glance", null,
    h("div", { class: "figs" }, m.headline.map((f) => h("div", { class: "fig" },
      h("div", { class: "l" }, f.label),
      h("div", { class: "v" }, f.unit === "₹ cr" ? fmt.cr(f.value) : `${fmt.num(f.value, 1)}${f.unit === "%" ? "%" : f.unit}`),
      f.hint ? h("div", { class: "hint" }, f.hint) : null))));
}

function notesList(notes) {
  return notes?.length ? h("ul", { class: "notes" }, notes.map((n) => h("li", { class: n.tone }, n.text))) : null;
}

function trajectory(m) {
  const t = m.trajectory, q = t.quarterly;
  return section("trajectory", "Trajectory", "Revenue bars with margin line, then quarterly growth.",
    h("div", { class: "card", style: { padding: "10px 14px" } },
      barLine(t.years, t.revenue, t.margin, "Revenue (₹ cr)", m.is_financial ? "Financing margin" : "EBITDA margin"),
      h("div", { class: "legend" }, h("span", {}, h("i", { style: { background: "var(--calm-soft)", border: "1px solid var(--calm)" } }), "Revenue"),
        h("span", {}, h("i", { style: { background: "var(--ink)" } }), "Margin %")),
      q ? [h("div", { class: "small muted", style: { marginTop: "14px" } }, "Revenue growth, year on year, by quarter"), growthBars(q.periods, q.rev_yoy)] : null,
      notesList(t.notes)));
}

function balance(m) {
  const b = m.balance_sheet;
  const rows = b.years.map((y, i) => [y, b.debt[i], b.de[i], b.ccc?.[i]]).slice(-5);
  return section("balance", "Balance sheet", null,
    h("div", { class: "card", style: { padding: "10px 14px" } },
      h("table", { class: "rtable" },
        h("thead", {}, h("tr", {}, h("th", {}, ""), rows.map((r) => h("th", {}, r[0])))),
        h("tbody", {},
          h("tr", {}, h("td", {}, "Debt, ₹ cr"), rows.map((r) => h("td", {}, fmt.num(r[1])))),
          m.is_financial ? null : h("tr", {}, h("td", {}, "Debt / equity"), rows.map((r) => h("td", {}, r[2] == null ? "—" : r[2].toFixed(2)))),
          b.ccc ? h("tr", {}, h("td", {}, "Cash cycle, days"), rows.map((r) => h("td", {}, fmt.num(r[3])))) : null)),
      notesList(b.notes)));
}

function cash(m) {
  const c = m.cash;
  return section("cash", "Cash quality", "Do profits turn into cash? Operating cash flow against reported profit, by year.",
    h("div", { class: "card", style: { padding: "10px 14px" } },
      pairBars(c.years.slice(-6), c.cfo.slice(-6), c.pat.slice(-6)),
      h("div", { class: "legend" }, h("span", {}, h("i", { style: { background: "var(--ink)" } }), "Operating cash flow"),
        h("span", {}, h("i", { style: { background: "var(--line)" } }), "Profit after tax")),
      notesList(c.notes)));
}

function guidance(d, ev) {
  const g = [...(d.guidance || [])].sort((x, y) => (x.src > y.src ? 1 : -1));
  return section("guidance", "What management has guided", "Forward statements from the calls, newest first. Quotes are verified against the transcript text.",
    g.length ? h("div", { class: "card" }, g.map((x) => h("div", { class: "gq" },
      h("div", { class: "q" }, `${x.quarter} call, for ${x.period}`),
      h("div", {}, x.statement),
      h("blockquote", {}, `“${x.quote}”`),
      cites([x.id], ev, d))))
      : h("p", { class: "muted small" }, d.llm ? "No explicit numeric guidance found in the last four calls." : "Needs a model key to read the transcripts."));
}

function sources(d, a) {
  return section("sources", "Sources", "Primary documents only: no news, broker notes or model memory.",
    h("div", { class: "card" }, h("ul", { class: "notes" }, d.sources.filter((s) => !s.id.startsWith("SCR") || s.id === "SCR-PL").map((s) =>
      h("li", {}, s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener" }, s.id === "SCR-PL" ? "Financial statements (screener.in)" : s.label) :
        (s.id === "SCR-PL" ? "Financial statements" : s.label), s.error ? h("span", { class: "faint small" }, ` (couldn't read: ${s.error})`) : null)))),
    a?.dropped_ungrounded ? h("p", { class: "tiny faint" }, `${a.dropped_ungrounded} model ${a.dropped_ungrounded === 1 ? "statement was" : "statements were"} dropped for not citing evidence.`) : null);
}

// ------------------------------------------------------------------ ask
function askBar(d, answers) {
  const input = h("input", { placeholder: `Ask about ${d.name}`, "aria-label": `Ask a question about ${d.name}`, enterkeyhint: "send" });
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null;
  const mic = SR ? h("button", { type: "button", class: "icon-btn mic", "aria-label": "Ask by voice", onclick: () => {
    if (rec) { rec.stop(); return; }
    rec = new SR(); rec.lang = "en-IN"; rec.interimResults = true;
    mic.classList.add("on");
    rec.onresult = (e) => { input.value = [...e.results].map((r) => r[0].transcript).join(""); if (e.results[e.results.length - 1].isFinal) submit(); };
    rec.onend = () => { mic.classList.remove("on"); rec = null; };
    rec.start();
  } }, h("span", { html: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>' })) : null;
  const submit = async () => {
    const q = input.value.trim();
    if (!q) return;
    if (!S.api) { toast("Questions need the FINN server running with a model key."); return; }
    if (d.mode === "sample") { toast("Sample research is fictional, so there are no documents to question."); return; }
    const box = h("div", { class: "answer", role: "status" }, h("div", { class: "q" }, q), h("p", { class: "muted" }, "Reading the sources…"));
    answers.append(box);
    box.scrollIntoView({ behavior: "smooth", block: "center" });
    input.value = "";
    try {
      const r = await postJSON("api/ask", { symbol: d.symbol, question: q });
      box.replaceChildren(h("div", { class: "q" }, q), h("p", {}, r.answer),
        r.cites?.length ? h("div", { class: "cites" }, r.cites.map((c) => c.url
          ? h("a", { class: "cite", href: c.url, target: "_blank", rel: "noopener" }, `${c.label}${c.page ? `, p${c.page}` : ""}`)
          : h("span", { class: "cite" }, c.label || c.src))) : null,
        h("div", { class: "tiny faint", style: { marginTop: "6px" } }, `${r.model_tier === "smart" ? "Reasoning" : "Fast"} model, $${r.cost.usd.toFixed(4)}`));
    } catch (e) { box.replaceChildren(h("div", { class: "q" }, q), h("p", { class: "down" }, e.message)); }
  };
  return h("form", { class: "ask", onsubmit: (e) => { e.preventDefault(); submit(); } },
    input, mic, h("button", { class: "btn primary", type: "submit" }, "Ask"));
}

// ------------------------------------------------------------------ charts
function barLine(labels, bars, line, barLabel, lineLabel) {
  const W = 340, H = 150, top = 12, bottom = 18, ch = H - top - bottom;
  const n = labels.length;
  if (!n) return null;
  const bmax = Math.max(...bars.filter((x) => x != null), 1);
  const lv = line.filter((x) => x != null);
  const lmin = Math.min(...lv) - 2, lmax = Math.max(...lv) + 2;
  const bw = (W - 8) / n;
  const bx = (i) => 4 + i * bw;
  const rects = bars.map((v, i) => v == null ? "" : `<rect x="${(bx(i) + bw * 0.18).toFixed(1)}" y="${(top + ch - (v / bmax) * ch).toFixed(1)}" width="${(bw * 0.64).toFixed(1)}" height="${((v / bmax) * ch).toFixed(1)}" rx="2" fill="var(--calm-soft)" stroke="var(--calm)" stroke-width=".8"/>`).join("");
  const ly = (v) => top + ch - ((v - lmin) / (lmax - lmin || 1)) * ch;
  const pts = line.map((v, i) => (v == null ? null : `${(bx(i) + bw / 2).toFixed(1)},${ly(v).toFixed(1)}`)).filter(Boolean);
  const dots = line.map((v, i) => (v == null ? "" : `<circle cx="${(bx(i) + bw / 2).toFixed(1)}" cy="${ly(v).toFixed(1)}" r="2.4" fill="var(--ink)"/>`)).join("");
  const xl = labels.map((l, i) => `<text x="${(bx(i) + bw / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle">${l}</text>`).join("");
  const last = line.at(-1);
  return svg(`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${barLabel} with ${lineLabel}">${rects}
    <polyline fill="none" stroke="var(--ink)" stroke-width="1.6" points="${pts.join(" ")}"/>${dots}${xl}
    ${last != null ? `<text x="${W - 4}" y="${(ly(last) - 7).toFixed(1)}" text-anchor="end" style="fill:var(--ink);font-weight:600">${last}%</text>` : ""}</svg>`);
}

function growthBars(labels, vals) {
  const W = 340, H = 90, mid = 46, n = labels.length;
  const mx = Math.max(...vals.filter((v) => v != null).map(Math.abs), 1);
  const bw = (W - 8) / n;
  const rects = vals.map((v, i) => {
    if (v == null) return "";
    const hh = (Math.abs(v) / mx) * 34;
    const y = v >= 0 ? mid - hh : mid;
    return `<rect x="${(4 + i * bw + bw * 0.2).toFixed(1)}" y="${y.toFixed(1)}" width="${(bw * 0.6).toFixed(1)}" height="${hh.toFixed(1)}" rx="2" fill="${v >= 0 ? "var(--up)" : "var(--down)"}"/>
      <text x="${(4 + i * bw + bw / 2).toFixed(1)}" y="${(v >= 0 ? y - 3 : y + hh + 9).toFixed(1)}" text-anchor="middle">${Math.round(v)}%</text>`;
  }).join("");
  const xl = labels.map((l, i) => `<text x="${(4 + i * bw + bw / 2).toFixed(1)}" y="${H - 2}" text-anchor="middle">${l.replace(/^(\w{3})\w* 20(\d\d)$/, "$1 $2")}</text>`).join("");
  return svg(`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Quarterly revenue growth"><line x1="0" x2="${W}" y1="${mid}" y2="${mid}" stroke="var(--line)"/>${rects}${xl}</svg>`);
}

function pairBars(labels, a, b) {
  const W = 340, H = 130, top = 8, bottom = 18, ch = H - top - bottom, n = labels.length;
  if (!n) return null;
  const mx = Math.max(...[...a, ...b].filter((x) => x != null).map(Math.abs), 1);
  const bw = (W - 8) / n;
  const scale = ch / (mx * (Math.min(...[...a, ...b].filter((x) => x != null), 0) < 0 ? 2 : 1));
  const y0 = Math.min(...[...a, ...b].filter((x) => x != null), 0) < 0 ? top + ch / 2 : top + ch;
  const bar = (v, x, fill) => {
    if (v == null) return "";
    const hh = Math.abs(v) * scale;
    return `<rect x="${x.toFixed(1)}" y="${(v >= 0 ? y0 - hh : y0).toFixed(1)}" width="${(bw * 0.3).toFixed(1)}" height="${hh.toFixed(1)}" rx="1.5" fill="${fill}"/>`;
  };
  const r = labels.map((l, i) => bar(a[i], 4 + i * bw + bw * 0.17, "var(--ink)") + bar(b[i], 4 + i * bw + bw * 0.53, "var(--line)") +
    `<text x="${(4 + i * bw + bw / 2).toFixed(1)}" y="${H - 4}" text-anchor="middle">${l}</text>`).join("");
  return svg(`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Operating cash flow versus profit"><line x1="0" x2="${W}" y1="${y0}" y2="${y0}" stroke="var(--line)"/>${r}</svg>`);
}

