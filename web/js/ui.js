// Small DOM + formatting helpers. No framework: the whole app is a few hundred lines per view.

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export function mount(root, ...kids) {
  root.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
}

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const inr1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });

export const fmt = {
  cr(v) { return v == null ? "—" : `₹${Math.abs(v) >= 100 ? inr.format(v) : inr1.format(v)} cr`; },
  num(v, d = 0) { return v == null ? "—" : (d ? inr1 : inr).format(v); },
  px(v) { return v == null ? "—" : new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v); },
  pct(v, d = 1) { return v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`; },
  bps(v) { return v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v))} bps`; },
  ago(ts, now = Date.now()) {
    const t = typeof ts === "number" ? ts : Date.parse(ts);
    const m = Math.round((now - t) / 60000);
    if (m < 1) return "now";
    if (m < 60) return `${m}m ago`;
    const hr = Math.round(m / 60);
    if (hr < 24) return `${hr}h ago`;
    const d = Math.round(hr / 24);
    if (d < 7) return `${d}d ago`;
    return new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  },
  day(ts) { return new Date(ts).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" }); },
  when(ts) { return new Date(ts).toLocaleString("en-IN", { weekday: "long", hour: "numeric", minute: "2-digit" }); },
};

export const sign = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");

export function spark(vals, { w = 76, h = 26, color } = {}) {
  const v = (vals || []).filter((x) => x != null);
  if (v.length < 2) return h_svg(`<svg width="${w}" height="${h}"></svg>`);
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((y, i) => `${((i / (v.length - 1)) * (w - 2) + 1).toFixed(1)},${(h - 2 - ((y - lo) / span) * (h - 4)).toFixed(1)}`);
  const c = color || (v[v.length - 1] >= v[0] ? "var(--up)" : "var(--down)");
  return h_svg(`<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline fill="none" stroke="${c}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" points="${pts.join(" ")}"/></svg>`);
}

function h_svg(str) {
  const t = document.createElement("template");
  t.innerHTML = str.trim();
  return t.content.firstChild;
}
export const svg = h_svg;

// ---- bottom sheet
let current = null;
export function sheet(title, ...content) {
  closeSheet(true);
  const scrim = h("div", { class: "scrim", onclick: () => closeSheet() });
  const body = h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": title },
    h("div", { class: "grab" }), h("h3", {}, title), ...content);
  document.body.append(scrim, body);
  requestAnimationFrame(() => { scrim.classList.add("on"); body.classList.add("on"); });
  const onKey = (e) => { if (e.key === "Escape") closeSheet(); };
  document.addEventListener("keydown", onKey);
  current = { scrim, body, onKey };
  body.setAttribute("tabindex", "-1");
  body.focus();
  return body;
}
export function closeSheet(instant) {
  if (!current) return;
  const { scrim, body, onKey } = current;
  current = null;
  document.removeEventListener("keydown", onKey);
  if (instant) { scrim.remove(); body.remove(); return; }
  scrim.classList.remove("on"); body.classList.remove("on");
  setTimeout(() => { scrim.remove(); body.remove(); }, 260);
}

// ---- toast with optional undo
let toastTimer;
export function toast(msg, action) {
  document.querySelector(".toast")?.remove();
  clearTimeout(toastTimer);
  const t = h("div", { class: "toast", role: "status" }, msg,
    action && h("button", { onclick: () => { action.fn(); t.remove(); } }, action.label));
  document.body.append(t);
  toastTimer = setTimeout(() => t.remove(), 4000);
}

// ---- swipe-left to clear
export function swipeable(el, onClear) {
  let x0 = null, y0 = null, dx = 0, locked = false;
  el.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; dx = 0; locked = false; }, { passive: true });
  el.addEventListener("touchmove", (e) => {
    if (x0 == null) return;
    const mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
    if (!locked && Math.abs(my) > Math.abs(mx)) { x0 = null; return; }
    locked = true;
    dx = Math.min(0, mx);
    el.classList.add("swiping");
    el.style.transform = `translateX(${dx}px)`;
    el.style.opacity = String(1 + dx / 400);
  }, { passive: true });
  el.addEventListener("touchend", () => {
    el.classList.remove("swiping");
    if (dx < -90) { el.classList.add("leaving"); setTimeout(onClear, 180); }
    else { el.style.transform = ""; el.style.opacity = ""; }
    x0 = null;
  });
}

export const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((s) => s[0]).join("").toUpperCase();
