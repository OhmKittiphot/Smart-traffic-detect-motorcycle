"use strict";

// Helpers shared by the dashboard pages (dashboard.js, search.js), exposed as window.Dashboard, and the start
// and end of each page (onPage).
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
    speedUnit: () => units.imperial() ? "mph" : "km/h",
    // A whole km/h speed (a speed limit) as shown in an input, and back
    speedToShown: (kmh) => units.imperial() ? Math.round(kmh / KMH_PER_MPH) : kmh,
    speedToKmh: (shown) => units.imperial() ? Math.round(shown * KMH_PER_MPH) : shown,
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

  // ---------- pages ----------
  // Links between the dashboard pages are htmx boosted (_topbar.html): they swap the <body> instead of loading
  // a new document, so the scripts are loaded once (_scripts.html) and each page script hands its setup to
  // onPage(selector, setup). The setup runs whenever the page shown has an element matching selector (on
  // load, and after each swap), and gets a `page` that ties what it starts to that page, stopped when it is
  // left:
  // - page.signal: for listeners on document / window, addEventListener(..., { signal: page.signal })
  // - page.every(fn, ms): fn now and every ms while the tab is shown, and as soon as it is shown again
  // - page.onLeave(fn): anything else to stop, e.g. an event stream (watchEvents returns its stop function)

  const setups = [];
  let shown = null;   // AbortController of the page shown, aborted when it is left

  function onPage(selector, setup) {
    setups.push({ selector, setup });
  }

  function leavePage() {
    shown?.abort();
    shown = null;
  }

  function startPage() {
    leavePage();
    shown = new AbortController();
    const { signal } = shown;
    const onLeave = (fn) => signal.addEventListener("abort", fn);
    const page = {
      signal,
      onLeave,
      every(fn, ms) {
        const run = () => { if (!document.hidden) fn(); };
        const timer = setInterval(run, ms);
        document.addEventListener("visibilitychange", run, { signal });
        onLeave(() => clearInterval(timer));
        run();
      },
    };
    for (const { selector, setup } of setups) {
      if (!document.querySelector(selector)) continue;
      // One that fails leaves the others (e.g. the notifications) working
      try { setup(page); } catch (e) { console.error(e); }
    }
  }

  document.addEventListener("DOMContentLoaded", startPage);
  // A boosted link brings another page: the one shown stops before its body is replaced
  document.addEventListener("htmx:beforeSwap", (ev) => { if (ev.detail.boosted) leavePage(); });
  document.addEventListener("htmx:afterSettle", (ev) => { if (ev.detail.boosted) startPage(); });

  // Back / Forward load the page of that history entry. htmx would reload it (it keeps no copies of pages,
  // _scripts.html), but a reload gets no view transition (app.css) and location.replace() does. This listener
  // comes before htmx's (window.onpopstate, set once the page has loaded), so it can stop it
  window.addEventListener("popstate", (ev) => {
    ev.stopImmediatePropagation();
    location.replace(location.href);
  });

  // ---------- Back (_back_link.html) ----------

  // The page before this one in the tab's history, when it is a page of this site (not the login page)
  function previousPageOfSite() {
    if (window.navigation) {
      // Only this site's pages are listed, so there is none when this tab came here from elsewhere
      const entry = navigation.entries()[navigation.currentEntry.index - 1];
      return entry ? new URL(entry.url) : null;
    }
    // Browsers without the Navigation API: the page that linked here (for a page swapped in by a boosted
    // link, the one first loaded)
    const from = document.referrer ? new URL(document.referrer) : null;
    return from?.origin === location.origin ? from : null;
  }

  // Coming from another page of this site: go back in the browser's history, so pages opened one after another
  // go back the same way, e.g. stats › search › live. Its href (the live view) is for any other way in.
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest("#back-link")) return;
    const previous = previousPageOfSite();
    if (previous && previous.pathname !== "/login/") {
      ev.preventDefault();
      history.back();
    }
  });

  // ---------- cells of a vehicle row (one row of /api/vehicles/) ----------

  function thumbCell(r) {
    return r.snapshot
      ? el("button", { type: "button", class: "thumb-btn", "data-snap": r.id,
                       "aria-label": gettext("View evidence photo") },
          el("img", { class: "thumb", src: `/snapshot/${r.id}/`, alt: "", loading: "lazy" }))
      : el("span", { class: "muted" }, "—");
  }

  // The province read off a plate; "Unknown Province" when its letters were read but the province was not,
  // "" when nothing was read
  function provinceOf(r) {
    return r.province || (r.plate ? gettext("Unknown Province") : "");
  }

  // The plate photo when one was saved (opens larger like the evidence photo), and beside it the plate's
  // letters and numbers with the province under them, as on the plate itself
  function plateCell(r) {
    const text = el("span", { class: "plate-text" },
      r.plate ? el("span", { class: "plate-number" }, r.plate) : el("span", { class: "muted" }, gettext("Unreadable")),
      r.plate ? el("small", { class: "plate-province" }, provinceOf(r)) : null);
    if (!r.plate_snapshot) return text;
    const src = `/snapshot/${r.id}/plate/`;
    return el("span", { class: "plate-with-photo" },
      el("button", { type: "button", class: "thumb-btn", "data-snap": r.id, "data-snap-src": src,
        "aria-label": gettext("View plate photo") }, el("img", { class: "plate-thumb", src, alt: "", loading: "lazy" })),
      text);
  }

  function helmetCell(r) {
    if (r.helmet == null) return el("span", { class: "muted" }, gettext("Unknown"));
    const parts = [];
    if (r.helmet) parts.push(el("span", { class: "tag tag-ok" }, interpolate(gettext("Helmet %s"), [r.helmet])));
    if (r.no_helmet) parts.push(el("span", { class: "tag tag-bad" }, interpolate(gettext("No helmet %s"), [r.no_helmet])));
    return parts.length ? el("span", {}, ...parts) : el("span", { class: "muted" }, gettext("Unknown"));
  }

  function statusCell(r) {
    if (!r.violation) return el("span", { class: "tag tag-ok" }, gettext("Normal"));
    return el("span", {}, ...r.violations.map((v) => el("span", { class: "tag tag-bad" }, v)));
  }

  // Where a video file was filmed: in a table only its first part ("Chalong Krung Road" of
  // "Chalong Krung Road, Lat Krabang"), the whole name in its tooltip
  function locationCell(r) {
    return r.location
      ? el("span", { class: "location-cell", title: r.location }, r.location.split(",")[0])
      : el("span", { class: "muted" }, "—");
  }

  // ---------- htmx (the records list of the search pages) ----------

  // Requests made by htmx send the CSRF token like api(), and a lost session goes to the login page
  document.addEventListener("htmx:configRequest", (ev) => { ev.detail.headers["X-CSRFToken"] = csrf; });
  document.addEventListener("htmx:responseError", (ev) => {
    if ([401, 403].includes(ev.detail.xhr.status)) window.location.href = "/login/";
  });
  // Speeds in the HTML htmx puts into the page are in km/h
  document.addEventListener("htmx:afterSwap", units.refresh);

  // ---------- pushed by the server (Server-Sent Events, views/events.py) ----------

  // Listens to the event stream at url() while the tab is shown: it is closed while the tab is hidden, so
  // it holds no server thread, and opened again (url() is asked again) when it is shown. listeners:
  // {event name: function(ev)}. The browser reconnects by itself if the stream drops; one the server
  // refuses (a lost session) goes to the login page, like api(). Returns a function that stops it.
  function watchEvents(url, listeners) {
    let source = null;

    function open() {
      source = new EventSource(url());
      for (const [name, listener] of Object.entries(listeners)) source.addEventListener(name, listener);
      source.addEventListener("error", () => {
        if (source?.readyState === EventSource.CLOSED) window.location.href = "/login/";
      });
    }

    function close() {
      source?.close();
      source = null;
    }

    function onVisibility() {
      if (document.hidden) close();
      else if (!source) open();
    }

    document.addEventListener("visibilitychange", onVisibility);
    onVisibility();
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      close();
    };
  }

  // Calls onRecords({rows, total}, lastId) with the records saved after `after` (rows newest first; total
  // also counts those not sent), once at the start with no rows, and then as each one is saved. after:
  // a record id, or null for "from now on"; violations: only violations. A hidden tab catches up from the
  // last id seen when it is shown again.
  function watchRecords({ after = null, violations = false }, onRecords) {
    return watchEvents(() => {
      const params = new URLSearchParams();
      if (after !== null) params.set("after", after);
      if (violations) params.set("violations", "true");
      return `/api/events/records/?${params}`;
    }, {
      records: (ev) => {
        after = Number(ev.lastEventId);
        onRecords(JSON.parse(ev.data), after);
      },
    });
  }

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

  return { api, el, units, onPage, thumbCell, plateCell, helmetCell, statusCell, locationCell, provinceOf, watchEvents, watchRecords,
           openSnapshotsFrom };
})();
