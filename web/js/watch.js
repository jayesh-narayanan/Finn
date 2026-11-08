// The investor decides what "unusual" and "important" mean; a live backtest shows what that would have cost in noise.
import S, { CATEGORY_INFO, savePrefs, resetPrefs, nameOf } from "./store.js";
import { h, mount, fmt, toast } from "./ui.js";
import { backtest } from "./rules.js";

export function render(root, rerender) {
  const p = S.prefs;
  const calib = h("div", { class: "calib", "aria-live": "polite" });
  const paint = () => calib.replaceChildren(...calibration());

  const slider = (key, label, help, min, max, step, show) => {
    const val = h("span", { class: "val" }, show(p[key]));
    const input = h("input", { type: "range", min, max, step, value: p[key], "aria-label": label,
      oninput: (e) => { p[key] = +e.target.value; val.textContent = show(p[key]); paint(); },
      onchange: () => savePrefs() });
    return h("div", { class: "ctl" }, h("label", {}, label, val), h("p", {}, help), input);
  };

  const counts = {};
  const monthAgo = Date.now() - 30 * 864e5;
  for (const a of S.feed.announcements) if (a.t >= monthAgo) counts[a.category] = (counts[a.category] || 0) + 1;

  mount(root, 
    h("header", { class: "top" }, h("span", { class: "brand" }, "Watch"), h("span", { class: "spacer" }),
      h("button", { class: "btn ghost", onclick: () => { resetPrefs(); rerender(); toast("Back to default thresholds"); } }, "Reset")),
    h("p", { class: "muted small", style: { margin: "2px 0 0" } }, "You decide what counts as unusual. FINN checks every holding against it after each close."),
    slider("price", "Price move", "Alert when a holding closes up or down by at least this much in a day.", 1, 10, 0.5, (v) => `±${v}%`),
    slider("volume", "Volume spike", "Alert when a day's volume is this multiple of its recent average.", 1.5, 5, 0.1, (v) => `${(+v).toFixed(1)}×`),
    h("div", { class: "ctl" }, h("label", {}, "Average over"),
      h("p", {}, "The baseline the volume spike is measured against."),
      h("div", { class: "chips", style: { paddingTop: "2px" } }, [10, 20, 50].map((n) => h("button", {
        class: "chip", "aria-pressed": String(p.lookback === n), onclick: () => { p.lookback = n; savePrefs(); rerender(); },
      }, `${n} sessions`)))),
    calib,
    h("div", { class: "sec" }, h("h2", {}, "Filings"), h("span", { class: "count" }, "count is the last 30 days")),
    h("p", { class: "muted small", style: { margin: "0 0 8px" } },
      "Alert puts every material filing of that type at the top of your brief. Brief ranks it by materiality. Mute folds it away."),
    h("div", { class: "rows" }, Object.entries(CATEGORY_INFO).map(([cat, [name, desc]]) => {
      const seg = h("div", { class: "seg", role: "group", "aria-label": name }, [["alert", "Alert"], ["brief", "Brief"], ["mute", "Mute"]].map(([k, l]) =>
        h("button", { class: k, "aria-pressed": String(p.route[cat] === k), onclick: () => { p.route[cat] = k; savePrefs(); rerender(); } }, l)));
      return h("div", { class: "cat" }, h("div", { class: "n" }, name, h("span", { class: "faint tiny" }, `  ${counts[cat] || 0}`)), h("div", { class: "d" }, desc), seg);
    })),
    notifyCtl(rerender),
  );
  paint();
}

function calibration() {
  const p = S.prefs;
  const bt = backtest(S.feed.prices, p, 90);
  const per = bt.perWeek;
  const verdict = per > 10 ? "That's noisy: consider raising a threshold."
    : per < 0.3 ? "Very quiet: you may miss real moves."
      : per <= 4 ? "A sensible level of interruption." : "Busy, but manageable.";
  const mx = Math.max(...bt.weeks, 1);
  return [
    h("div", { class: "big" }, h("b", {}, String(bt.hits.length)), `alerts across your holdings in the last ${bt.sessions} sessions, about ${per.toFixed(1)} a week. ${verdict}`),
    h("div", { class: "weeks", "aria-hidden": "true" }, bt.weeks.map((w) => h("i", { style: { height: `${(w / mx) * 100}%` }, title: `${w}` }))),
    h("div", { class: "axis" }, h("span", {}, bt.first ? fmt.day(bt.first) : ""), h("span", {}, "alerts per week"), h("span", {}, bt.last ? fmt.day(bt.last) : "")),
    bt.hits.length ? h("ul", {}, bt.hits.slice(0, 4).map((x) => h("li", {},
      h("span", {}, nameOf(x.symbol)),
      h("span", {}, [x.price != null ? fmt.pct(x.price) : null, x.volume != null ? `${x.volume.toFixed(1)}× vol` : null].filter(Boolean).join(", ")),
      h("span", { class: "d" }, fmt.day(x.date))))) : null,
  ];
}

function notifyCtl(rerender) {
  const p = S.prefs;
  const supported = "Notification" in window;
  const perm = supported ? Notification.permission : "unsupported";
  return h("div", { class: "ctl", style: { marginTop: "18px" } },
    h("label", {}, "Phone notifications"),
    h("p", {}, supported
      ? "Get a notification for new alerts when FINN refreshes. Add FINN to your home screen first on iPhone."
      : "This browser doesn't support notifications. Alerts still appear at the top of your brief."),
    supported ? h("button", {
      class: `btn${p.notify && perm === "granted" ? "" : " primary"}`, disabled: perm === "denied",
      onclick: async () => {
        if (p.notify && perm === "granted") { p.notify = false; savePrefs(); rerender(); return; }
        const r = await Notification.requestPermission();
        p.notify = r === "granted"; savePrefs(); rerender();
        if (r === "granted") new Notification("FINN alerts are on", { body: "You'll hear about moves and filings that cross your thresholds.", icon: "icon.svg" });
      },
    }, perm === "denied" ? "Blocked in browser settings" : p.notify && perm === "granted" ? "Turn off" : "Turn on notifications") : null);
}

