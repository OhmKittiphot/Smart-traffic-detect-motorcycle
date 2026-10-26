"use strict";

// Violation statistics at the top of the violations page, from /api/stats/ (refreshed every 5 seconds)
// and /api/stats/repeat/ (every minute, since grouping look-alike plates takes longer), for the chosen dates
(() => {
  const REFRESH_MS = 5000;
  const REPEAT_REFRESH_MS = 60000;
  const WEEKDAYS = [gettext("Mon"), gettext("Tue"), gettext("Wed"), gettext("Thu"), gettext("Fri"), gettext("Sat"),
                    gettext("Sun")];
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

  // Bars on a y-axis with three gridlines; about seven dates are written, counted back from the last day
  function renderPerDay(perDay) {
    const labelEvery = Math.ceil(perDay.length / 7);
    const max = axisMax(Math.max(1, ...perDay.map((d) => d.total)));
    const grid = el("div", { class: "day-grid", "aria-hidden": "true" },
      ...[max, max / 2, 0].map((v) => el("span", { class: "day-gridline" }, el("span", { class: "day-tick" }, v))));
    const days = perDay.map((d, i) => {
      const column = el("span", { class: "day-col", title: interpolate(gettext("%(date)s: %(violations)s violations of %(total)s"),
                                                     { date: d.date, violations: d.violation, total: d.total }, true) },
        bar("day-total", (d.total / max) * 100, "height"),
        bar("day-violation", (d.violation / max) * 100, "height"));
      const label = el("span", { class: "day-label" }, (perDay.length - 1 - i) % labelEvery === 0 ? d.date : "");
      return el("div", { class: "day" }, column, label);
    });
    $("per-day").replaceChildren(grid, el("div", { class: "day-plot" }, ...days));
  }

  // A cell per weekday and hour, its colour stronger the more violations; the busiest hour is named above
  function renderByHour(byHour) {
    const max = Math.max(0, ...byHour.flat());
    const cells = byHour.flatMap((hours, day) => [
      el("span", {}, WEEKDAYS[day]),
      ...hours.map((n, hour) => {
        const cell = el("span", { class: n ? "heat-cell" : "heat-cell heat-empty",
                                  title: `${WEEKDAYS[day]} ${hour}:00–${hour + 1}:00: `
                                    + interpolate(ngettext("%s violation", "%s violations", n), [n]) });
        if (n) cell.style.opacity = 0.2 + 0.8 * (n / max);
        return cell;
      }),
    ]);
    const hourLabels = [el("span"), ...Array.from({ length: 24 },
      (_, hour) => el("span", { class: "heat-hour" }, hour % 6 === 0 ? `${hour}:00` : ""))];
    $("by-hour").replaceChildren(...cells, ...hourLabels);

    const peak = byHour.flat().indexOf(max);
    $("by-hour-peak").textContent = max
      ? interpolate(gettext("Busiest: %s"), [`${WEEKDAYS[Math.floor(peak / 24)]} ${peak % 24}:00–${peak % 24 + 1}:00 (${max})`])
      : gettext("No violations");
  }

  // Plates with more than one violation; a click searches the plate in the list below
  function renderRepeat(rows) {
    if (!rows.length) {
      $("repeat").replaceChildren(el("li", { class: "repeat-none muted" }, gettext("No plate has more than one violation")));
      return;
    }
    $("repeat").replaceChildren(...rows.map((r) => el("li", {},
      el("button", { type: "button", class: "repeat-item", "data-plate": r.plate },
        el("span", {}, el("span", { class: "repeat-plate" }, r.plate), r.province ? ` · ${r.province}` : ""),
        el("span", { class: "repeat-count", title: gettext("Violations") }, `×${r.count}`),
        el("span", { class: "repeat-meta" }, `${r.types.join(", ")} · ${interpolate(gettext("last %s"), [r.last_seen])}`,
          r.also_read_as.length ? ` · ${interpolate(gettext("also read as %s"), [r.also_read_as.join(", ")])}` : "")))));
  }

  // Today against yesterday (lastWeek ends with today); more violations is worse, so up is red
  function renderToday(lastWeek) {
    const today = lastWeek[6].violation;
    const diff = today - lastWeek[5].violation;
    $("st-today").textContent = today;
    const note = $("st-today-note");
    note.dataset.trend = diff > 0 ? "up" : diff < 0 ? "down" : "same";
    note.textContent = diff ? `${diff > 0 ? "▲" : "▼"} ${interpolate(gettext("%s vs yesterday"), [Math.abs(diff)])}` : gettext("Same as yesterday");
  }

  // Violations of each of the last 7 days, as small bars
  function renderWeek(week) {
    $("st-week").textContent = week.reduce((sum, d) => sum + d.violation, 0);
    const max = Math.max(1, ...week.map((d) => d.violation));
    $("st-week-spark").replaceChildren(...week.map((d) => {
      const b = bar("spark-bar", Math.max(6, (d.violation / max) * 100), "height");
      b.title = `${d.date}: ${d.violation}`;
      return b;
    }));
  }

  function render(s) {
    renderToday(s.last_week);
    renderWeek(s.last_week);

    const rate = s.total ? Math.round((s.violation / s.total) * 100) : 0;
    $("st-range-label").textContent = rangeText(s);
    $("st-violation").textContent = s.violation;
    $("st-rate-bar").style.width = `${rate}%`;
    $("st-rate").textContent = s.total ? interpolate(gettext("%(rate)s% of %(total)s motorcycles"), { rate, total: s.total }, true) : gettext("No motorcycles recorded");

    const reviewed = s.violation - s.pending_review;
    $("st-pending").textContent = s.pending_review;
    $("st-review-bar").style.width = `${s.violation ? (reviewed / s.violation) * 100 : 0}%`;
    $("st-confirmed").textContent = s.violation
      ? interpolate(gettext("%(reviewed)s of %(violations)s reviewed · %(confirmed)s confirmed"),
          { reviewed, violations: s.violation, confirmed: s.confirmed }, true) : gettext("Nothing to review");

    renderByType(s.by_type, s.violation);
    renderPerDay(s.per_day);
    renderByHour(s.by_hour);
    if (!startInput.value) startInput.value = s.start;
    if (!endInput.value) endInput.value = s.end;
    $("stats-meta").textContent = `${rangeText(s)} · ${interpolate(gettext("updated %s"), [new Date().toLocaleTimeString()])}`;
  }

  // ---------- date range ----------
  // Until the user picks dates, the server's default range is shown (the last 30 days, moving with today)
  const startInput = $("st-start");
  const endInput = $("st-end");
  let customRange = false;

  function rangeText(s) {
    const day = (iso) => iso.split("-").reverse().slice(0, 2).join("/");   // 2026-10-26 -> 26/10
    return customRange ? `${day(s.start)} – ${day(s.end)}` : interpolate(gettext("Last %s days"), [s.days]);
  }

  function rangeQuery() {
    return customRange ? `?${new URLSearchParams({ start: startInput.value, end: endInput.value })}` : "";
  }

  function reload() {
    refresh();
    refreshRepeat();
  }

  function onRangeChange() {
    if (!startInput.value || !endInput.value) return;   // still typing
    customRange = true;
    $("st-range-reset").hidden = false;
    reload();
  }
  startInput.addEventListener("change", onRangeChange);
  endInput.addEventListener("change", onRangeChange);
  $("st-range-reset").addEventListener("click", () => {
    customRange = false;
    startInput.value = endInput.value = "";
    $("st-range-reset").hidden = true;
    reload();
  });

  // ---------- loading ----------

  async function refresh() {
    try {
      render(await api(`/api/stats/${rangeQuery()}`));
      $("stats-msg").textContent = "";
    } catch (e) {
      $("stats-msg").textContent = interpolate(gettext("Could not load statistics: %s"), [e.message]);
    }
  }

  async function refreshRepeat() {
    try {
      renderRepeat((await api(`/api/stats/repeat/${rangeQuery()}`)).rows);
    } catch (e) {
      $("stats-msg").textContent = interpolate(gettext("Could not load repeat offenders: %s"), [e.message]);
    }
  }

  $("repeat").addEventListener("click", (ev) => {
    const item = ev.target.closest(".repeat-item");
    if (!item) return;
    $("search-q").value = item.dataset.plate;
    $("search-form").requestSubmit();
    $("search-form").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // No requests while the tab is hidden; refreshed as soon as it is shown again
  function every(ms, load) {
    const poll = async () => {
      if (!document.hidden) await load();
      setTimeout(poll, ms);
    };
    poll();
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  // Records deleted on this page (search.js): show the new numbers now rather than at the next poll
  document.addEventListener("records-changed", reload);
  every(REFRESH_MS, refresh);
  every(REPEAT_REFRESH_MS, refreshRepeat);
})();
