// Router + boot. Four places: Brief (push), Holdings, Research (pull), Watch (your rules).
import S, { load } from "./store.js";
import { h, closeSheet } from "./ui.js";
import { tierOf, isFilingAlert, moveAlerts, mergeByDay } from "./rules.js";
import * as brief from "./brief.js";
import * as holdings from "./holdings.js";
import * as research from "./research.js";
import * as watch from "./watch.js";

const VIEWS = { brief, holdings, research, watch };
const root = document.getElementById("view");
let lastTab = null;

function route() {
  const [tab = "brief", arg] = location.hash.replace(/^#/, "").split("/");
  const view = VIEWS[tab] ? tab : "brief";
  closeSheet(true);
  document.querySelector(".toast")?.remove();
  const rerender = () => { const y = scrollY; VIEWS[view].render(root, rerender, arg); scrollTo(0, y); badge(); };
  VIEWS[view].render(root, rerender, arg);
  if (view !== lastTab) { scrollTo(0, 0); lastTab = view; }
  document.querySelectorAll("nav.tabs a").forEach((a) => a.setAttribute("aria-current", a.dataset.tab === view ? "page" : "false"));
  badge();
}

function openNeeds() {
  const p = S.prefs;
  const since = Date.now() - p.windowH * 3600e3;
  return S.feed.announcements.filter((a) => a.t >= since && tierOf(a, p) === "act" && !S.done[a.id]).length;
}

function badge() {
  const n = openNeeds();
  const b = document.getElementById("badge");
  b.textContent = String(n);
  b.classList.toggle("hidden", n === 0);
  document.title = n ? `(${n}) FINN` : "FINN";
}

// Local notifications for alerts not yet notified (real push needs a push service; see README).
function notify() {
  if (!S.prefs.notify || !("Notification" in window) || Notification.permission !== "granted") return;
  let seen = {};
  try { seen = JSON.parse(localStorage.getItem("finn.notified.v1") || "{}"); } catch {}
  const fresh = [];
  for (const a of S.feed.announcements) {
    if (isFilingAlert(a, S.prefs) && Date.now() - a.t < 864e5 && !seen[a.id]) fresh.push([a.id, a.name, a.summary || a.subject]);
  }
  for (const m of mergeByDay(moveAlerts(S.feed.prices, S.prefs, 1))) {
    const id = `mv-${m.symbol}-${m.date}`;
    if (!seen[id]) fresh.push([id, m.symbol, [m.price != null ? `${m.price.toFixed(1)}% on the day` : null, m.volume ? `${m.volume.toFixed(1)}× volume` : null].filter(Boolean).join(", ")]);
  }
  fresh.slice(0, 3).forEach(([, title, body]) => new Notification(title, { body, icon: "icon.svg", tag: title }));
  fresh.forEach(([id]) => { seen[id] = Date.now(); });
  try { localStorage.setItem("finn.notified.v1", JSON.stringify(seen)); } catch {}
}

async function boot() {
  try {
    await load();
  } catch (e) {
    root.replaceChildren(h("div", { class: "empty", style: { marginTop: "40px" } },
      h("h3", {}, "No data yet"),
      h("p", {}, "FINN couldn't find data/feed.json. Run python -m finn sample for demo data, or python -m finn refresh for your portfolio."),
      h("p", { class: "tiny faint" }, String(e.message))));
    return;
  }
  addEventListener("hashchange", route);
  route();
  notify();
}
boot();
