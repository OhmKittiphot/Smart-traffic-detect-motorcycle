"use strict";

// Violation statistics at the top of the search page, from /api/stats/ (refreshed every 5 seconds)
(() => {
  const REFRESH_MS = 5000;
  const { api, el } = window.Dashboard;
  const $ = (id) => document.getElementById(id);

  // Bar sizes are set through element.style, which the page's CSP allows (unlike style="" in markup)
  function bar(className, percent, prop) {
    const node = el("span", { class: className });
    node.style[prop] = `${percent}%`;
    return node;
  }

  // Top of a chart axis, rounded up so its half is a whole number too (e.g. 37 -> 40, ticks 0 / 20 / 40)
  function axisMax(value) {
    const step = value <= 10 ? 2 : value <= 50 ? 10 : value <= 200 ? 20 : 100;
    return Math.max(step, Math.ceil(value / step) * step);
  }

  // Bars scaled to the largest kind; the share is of all violating motorcycles
  function renderByType(byType, violations) {
    const max = Math.max(1, ...byType.map((t) => t.count));
    $("by-type").replaceChildren(...byType.map((t) => el("div", { class: "hbar" },
      el("span", { class: "hbar-label" }, t.label),
      el("span", { class: "hbar-track" }, bar("hbar-fill", (t.count / max) * 100, "width")),
      el("span", { class: "hbar-value" }, t.count),
      el("span", { class: "hbar-share" }, violations ? `${Math.round((t.count / violations) * 100)}%` : "—"))));
  }

  // Bars on a y-axis with three gridlines; the date is written under every fifth day, counted back from today
  function renderPerDay(perDay) {
    const max = axisMax(Math.max(1, ...perDay.map((d) => d.total)));
    const grid = el("div", { class: "day-grid", "aria-hidden": "true" },
      ...[max, max / 2, 0].map((v) => el("span", { class: "day-gridline" }, el("span", { class: "day-tick" }, v))));
    const days = perDay.map((d, i) => {
      const column = el("span", { class: "day-col", title: `${d.date}: ${d.violation} violations of ${d.total}` },
        bar("day-total", (d.total / max) * 100, "height"),
        bar("day-violation", (d.violation / max) * 100, "height"));
      const label = el("span", { class: "day-label" }, (perDay.length - 1 - i) % 5 === 0 ? d.date : "");
      return el("div", { class: "day" }, column, label);
    });
    $("per-day").replaceChildren(grid, el("div", { class: "day-plot" }, ...days));
  }

  // Today against yesterday (per_day ends with today); more violations is worse, so up is red
  function renderToday(s) {
    const yesterday = s.per_day.length > 1 ? s.per_day[s.per_day.length - 2].violation : 0;
    const diff = s.today - yesterday;
    const note = $("st-today-note");
    note.dataset.trend = diff > 0 ? "up" : diff < 0 ? "down" : "same";
    note.textContent = diff ? `${diff > 0 ? "▲" : "▼"} ${Math.abs(diff)} vs yesterday` : "Same as yesterday";
  }

  // Violations of each of the last 7 days, as small bars
  function renderWeekSpark(perDay) {
    const week = perDay.slice(-7);
    const max = Math.max(1, ...week.map((d) => d.violation));
    $("st-week-spark").replaceChildren(...week.map((d) => {
      const b = bar("spark-bar", Math.max(6, (d.violation / max) * 100), "height");
      b.title = `${d.date}: ${d.violation}`;
      return b;
    }));
  }

  function render(s) {
    $("st-today").textContent = s.today;
    renderToday(s);
    $("st-week").textContent = s.week;
    renderWeekSpark(s.per_day);

    const rate = s.total ? Math.round((s.violation / s.total) * 100) : 0;
    $("st-range-label").textContent = `Last ${s.days} days`;
    $("st-violation").textContent = s.violation;
    $("st-rate-bar").style.width = `${rate}%`;
    $("st-rate").textContent = s.total ? `${rate}% of ${s.total} motorcycles` : "No motorcycles recorded";

    const reviewed = s.violation - s.pending_review;
    $("st-pending").textContent = s.pending_review;
    $("st-review-bar").style.width = `${s.violation ? (reviewed / s.violation) * 100 : 0}%`;
    $("st-confirmed").textContent = s.violation
      ? `${reviewed} of ${s.violation} reviewed · ${s.confirmed} confirmed` : "Nothing to review";

    renderByType(s.by_type, s.violation);
    renderPerDay(s.per_day);
    $("stats-meta").textContent = `Last ${s.days} days · updated ${new Date().toLocaleTimeString()}`;
  }

  async function refresh() {
    try {
      render(await api("/api/stats/"));
      $("stats-msg").textContent = "";
    } catch (e) {
      $("stats-msg").textContent = `Could not load statistics: ${e.message}`;
    }
  }

  // No requests while the tab is hidden; refreshed as soon as it is shown again
  async function poll() {
    if (!document.hidden) await refresh();
    setTimeout(poll, REFRESH_MS);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  // Records deleted on this page (search.js): show the new numbers now rather than at the next poll
  document.addEventListener("records-changed", refresh);
  poll();
})();
