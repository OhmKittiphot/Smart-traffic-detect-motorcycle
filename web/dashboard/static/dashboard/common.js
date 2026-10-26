"use strict";

// Helpers shared by the dashboard pages (dashboard.js, search.js), exposed as window.Dashboard.
// Texts go through gettext() / ngettext() / interpolate() from /jsi18n/ (Django's JavaScript catalog), which
// gives them in the language the account chose in Settings.
window.Dashboard = (() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;

  async function api(url, options = {}) {
    const res = await fetch(url, {
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, "Content-Type": "application/json" },
      ...options,
    });
    if (res.status === 401 || res.status === 403) {
      // Session expired or logged out elsewhere
      window.location.href = "/login/";
      throw new Error("unauthenticated");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorMessage(data) || `HTTP ${res.status}`);
    return data;
  }

  // The API answers errors as {"detail": "..."} or, for invalid input, {"<field>": ["..."]}
  // (nested for lists, e.g. {"points": {"0": {"1": ["..."]}}}). Show the first message found.
  function errorMessage(data) {
    if (typeof data === "string") return data;
    if (data && typeof data === "object") {
      if (data.detail) return errorMessage(data.detail);
      for (const value of Object.values(data)) {
        const message = errorMessage(value);
        if (message) return message;
      }
    }
    return "";
  }

  // Build elements with textContent only: plate / province text comes from OCR and is never parsed as HTML
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else node.setAttribute(k, v);
    }
    for (const c of children) {
      if (c == null) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  // ---------- units ----------
  // Speeds and distances come from the server in metric (km/h, m). Staff may choose to see them in
  // imperial (Settings, data-units on #settings-panel); only what the page shows is converted.

  const KMH_PER_MPH = 1.609344;
  const M_PER_FT = 0.3048;

  const units = {
    imperial: () => document.getElementById("settings-panel")?.dataset.units === "imperial",
    speedText: (kmh) => units.imperial() ? `${Math.round(kmh / KMH_PER_MPH)} mph` : `${Math.round(kmh)} km/h`,
    // A speed that is redrawn when the units are changed (see refresh)
    speed: (kmh) => kmh == null ? null : el("span", { "data-kmh": kmh }, units.speedText(kmh)),
    lengthUnit: () => units.imperial() ? "ft" : "m",
    // A length in metres as shown in an input, and back
    toShown: (m) => units.imperial() ? Math.round(m / M_PER_FT * 100) / 100 : m,
    // Rounded to the millimetre, so the largest length shown (1640.42 ft) is not just over the 500 m limit
    toMetres: (shown) => units.imperial() ? Math.round(shown * M_PER_FT * 1000) / 1000 : shown,
    refresh() {
      for (const node of document.querySelectorAll("[data-kmh]")) {
        node.textContent = units.speedText(Number(node.dataset.kmh));
      }
    },
  };

  // Units changed in Settings: redraw every speed on the page at once
  document.addEventListener("settings:preferences", (ev) => {
    const panel = document.getElementById("settings-panel");
    if (!panel || !ev.detail.units || ev.detail.units === panel.dataset.units) return;
    panel.dataset.units = ev.detail.units;
    units.refresh();
  });
  // Speeds written into the page by the server (e.g. the speed limit) are in km/h
  document.addEventListener("DOMContentLoaded", units.refresh);

  // ---------- Back (_back_link.html) ----------

  // Coming from another page of this site: go back in the browser's history, so pages opened one after another
  // go back the same way, e.g. stats › search › live. Its href (the live view) is for any other way in.
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest("#back-link")) return;
    const from = document.referrer ? new URL(document.referrer) : null;
    if (from && from.origin === location.origin && from.pathname !== "/login/") {
      ev.preventDefault();
      history.back();
    }
  });

  // ---------- cells of a vehicle row (one row of /api/vehicles/) ----------

  function thumbCell(r) {
    return r.snapshot
      ? el("button", { type: "button", class: "thumb-btn", "data-snap": r.id, "aria-label": gettext(gettext("View evidence photo")) },
          el("img", { class: "thumb", src: `/snapshot/${r.id}/`, alt: "", loading: "lazy" }))
      : el("span", { class: "muted" }, "—");
  }

  // Plate text, with the plate photo beside it when one was saved (opens larger like the evidence photo)
  function plateCell(r) {
    const text = r.plate || el("span", { class: "muted" }, gettext(gettext("Unreadable")));
    if (!r.plate_snapshot) return text;
    const src = `/snapshot/${r.id}/plate/`;
    return el("span", { class: "plate-with-photo" },
      el("button", { type: "button", class: "thumb-btn", "data-snap": r.id, "data-snap-src": src,
        "aria-label": gettext(gettext("View plate photo")) }, el("img", { class: "plate-thumb", src, alt: "", loading: "lazy" })),
      text);
  }

  function helmetCell(r) {
    if (r.helmet == null) return el("span", { class: "muted" }, gettext(gettext("Unknown")));
    const parts = [];
    if (r.helmet) parts.push(el("span", { class: "tag tag-ok" }, interpolate(gettext("Helmet %s"), [r.helmet])));
    if (r.no_helmet) parts.push(el("span", { class: "tag tag-bad" }, interpolate(gettext("No helmet %s"), [r.no_helmet])));
    return parts.length ? el("span", {}, ...parts) : el("span", { class: "muted" }, gettext(gettext("Unknown")));
  }

  function statusCell(r) {
    if (!r.violation) return el("span", { class: "tag tag-ok" }, gettext(gettext("Normal")));
    return el("span", {}, ...r.violations.map((v) => el("span", { class: "tag tag-bad" }, v)));
  }

  // ---------- htmx (the records list of the search pages) ----------

  // Requests made by htmx send the CSRF token like api(), and a lost session goes to the login page
  document.addEventListener("htmx:configRequest", (ev) => { ev.detail.headers["X-CSRFToken"] = csrf; });
  document.addEventListener("htmx:responseError", (ev) => {
    if ([401, 403].includes(ev.detail.xhr.status)) window.location.href = "/login/";
  });
  // Speeds in the HTML htmx puts into the page are in km/h
  document.addEventListener("htmx:afterSwap", units.refresh);

  // Clicking a thumbnail inside `container` opens the evidence photo in the #snap-dialog
  function openSnapshotsFrom(container) {
    const dialog = document.getElementById("snap-dialog");
    container.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-snap]");
      if (!btn) return;
      document.getElementById("snap-img").src = btn.dataset.snapSrc || `/snapshot/${btn.dataset.snap}/`;
      dialog.showModal();
    });
  }

  return { api, el, units, thumbCell, plateCell, helmetCell, statusCell, openSnapshotsFrom };
})();
