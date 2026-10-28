"use strict";

// Violation statistics at the top of the violations page, with a calendar of one month, from /api/stats/
// (refreshed every 5 seconds)
// and /api/stats/repeat/ (every minute, since grouping look-alike plates takes longer), for the chosen dates
// and video source
window.Dashboard.onPage("#per-day", (page) => {
  const REFRESH_MS = 5000;
  const REPEAT_REFRESH_MS = 60000;
  const WEEKDAYS = [gettext("Mon"), gettext("Tue"), gettext("Wed"), gettext("Thu"), gettext("Fri"), gettext("Sat"),
                    gettext("Sun")];
  const { api, el, provinceOf } = window.Dashboard;
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

  // ---------- month calendar ----------
  // null: the current month, so the calendar moves on by itself when a new month starts
  let calendarMonth = null;
  let shownCalendar = null;

  // "2026-10" one month later or earlier
  function shiftMonth(month, by) {
    const [year, m] = month.split("-").map(Number);
    const date = new Date(year, m - 1 + by, 1);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
  }

  // Heatmap cells laid out as a calendar: a column per weekday (Monday first), a row per week, each day's
  // colour stronger the more violations. days ends today, so the rest of the current month is drawn as
  // days still to come
  function renderCalendar(cal) {
    shownCalendar = cal;
    const [year, month] = cal.month.split("-").map(Number);
    const first = new Date(year, month - 1, 1);
    const daysInMonth = new Date(year, month, 0).getDate();
    const blanks = (first.getDay() + 6) % 7;   // getDay() counts from Sunday
    const max = Math.max(0, ...cal.days.map((d) => d.violation));
    const isThisMonth = cal.month === cal.this_month;

    const days = Array.from({ length: daysInMonth }, (_, i) => {
      const d = cal.days[i];
      if (!d) return el("span", { class: "heat-cell heat-future" });
      const today = isThisMonth && i === cal.days.length - 1;
      const cell = el("span", {
        class: `heat-cell${d.violation ? "" : " heat-empty"}${today ? " heat-today" : ""}`,
        title: interpolate(gettext("%(date)s: %(violations)s violations of %(total)s"),
                           { date: d.date, violations: d.violation, total: d.total }, true),
      });
      if (d.violation) cell.style.opacity = 0.2 + 0.8 * (d.violation / max);
      return cell;
    });
    $("calendar").replaceChildren(...WEEKDAYS.map((w) => el("span", { class: "heat-weekday" }, w)),
      ...Array.from({ length: blanks }, () => el("span")), ...days);

    $("cal-month").textContent = first.toLocaleDateString(document.documentElement.lang || undefined,
                                                          { month: "long", year: "numeric" });
    $("cal-next").disabled = isThisMonth;
    const peak = cal.days.find((d) => max && d.violation === max);
    $("cal-peak").textContent = peak
      ? interpolate(gettext("Busiest: %s"),
                    [`${peak.date} (${interpolate(ngettext("%s violation", "%s violations", max), [max])})`])
      : gettext("No violations");
  }

  function showMonth(by) {
    if (!shownCalendar) return;
    const month = shiftMonth(shownCalendar.month, by);
    calendarMonth = month === shownCalendar.this_month ? null : month;
    refresh();
  }
  $("cal-prev").addEventListener("click", () => showMonth(-1));
  $("cal-next").addEventListener("click", () => showMonth(1));

  // Plates with more than one violation; a click searches the plate in the list below
  function renderRepeat(rows) {
    if (!rows.length) {
      $("repeat").replaceChildren(el("li", { class: "repeat-none muted" }, gettext("No plate has more than one violation")));
      return;
    }
    $("repeat").replaceChildren(...rows.map((r) => el("li", {},
      el("button", { type: "button", class: "repeat-item", "data-plate": r.plate },
        el("span", {}, el("span", { class: "repeat-plate" }, r.plate), ` · ${provinceOf(r)}`),
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
    note.textContent = diff
      ? `${diff > 0 ? "▲" : "▼"} ${interpolate(gettext("%s vs yesterday"), [Math.abs(diff)])}`
      : gettext("Same as yesterday");
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
    $("st-rate").textContent = s.total
      ? interpolate(gettext("%(rate)s% of %(total)s motorcycles"), { rate, total: s.total }, true)
      : gettext("No motorcycles recorded");

    const reviewed = s.violation - s.pending_review;
    $("st-pending").textContent = s.pending_review;
    $("st-review-bar").style.width = `${s.violation ? (reviewed / s.violation) * 100 : 0}%`;
    $("st-confirmed").textContent = s.violation
      ? interpolate(gettext("%(reviewed)s of %(violations)s reviewed · %(confirmed)s confirmed"),
          { reviewed, violations: s.violation, confirmed: s.confirmed }, true) : gettext("Nothing to review");

    renderByType(s.by_type, s.violation);
    renderPerDay(s.per_day);
    renderCalendar(s.calendar);
    if (!startInput.value) startInput.value = s.start;
    if (!endInput.value) endInput.value = s.end;
    const updated = new Date().toLocaleTimeString(document.documentElement.lang);
    $("stats-meta").textContent = `${rangeText(s)} · ${interpolate(gettext("updated %s"), [updated])}`;
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

  // The range and the video source, unless they are the defaults
  function filterParams() {
    const params = new URLSearchParams(customRange ? { start: startInput.value, end: endInput.value } : {});
    if (source) params.set("source", source);
    return params;
  }

  function rangeQuery() {
    const params = filterParams();
    return params.size ? `?${params}` : "";
  }

  // The range and source plus the month the calendar shows, unless it is the current one
  function statsQuery() {
    const params = filterParams();
    if (calendarMonth) params.set("month", calendarMonth);
    return params.size ? `?${params}` : "";
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

  // ---------- video source ----------
  // "" = all sources. The choice is kept in the URL (?source=), so a reload or a link shows the same source;
  // the template marks it in the menu (_source_menu.html)
  let source = new URLSearchParams(location.search).get("source") ?? "";

  $("stats-source-menu").addEventListener("source-chosen", (ev) => {
    if (source === ev.detail.value) return;
    source = ev.detail.value;
    const url = new URL(location.href);
    if (source) url.searchParams.set("source", source);
    else url.searchParams.delete("source");
    history.replaceState(history.state, "", url);   // the same history entry, so Back still leaves the page
    reload();
  });

  // ---------- loading ----------

  async function refresh() {
    try {
      render(await api(`/api/stats/${statsQuery()}`));
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

  // Records deleted on this page (search.js): show the new numbers now rather than at the next poll
  document.addEventListener("records-changed", reload, { signal: page.signal });
  // No requests while the tab is hidden; refreshed as soon as it is shown again
  page.every(refresh, REFRESH_MS);
  page.every(refreshRepeat, REPEAT_REFRESH_MS);
});
