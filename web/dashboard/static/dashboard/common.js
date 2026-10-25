"use strict";

// Helpers shared by the dashboard pages (dashboard.js, search.js), exposed as window.Dashboard
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

  // ---------- cells of a vehicle row (one row of /api/vehicles/) ----------

  function thumbCell(r) {
    return r.snapshot
      ? el("button", { type: "button", class: "thumb-btn", "data-snap": r.id, "aria-label": "View evidence photo" },
          el("img", { class: "thumb", src: `/snapshot/${r.id}/`, alt: "", loading: "lazy" }))
      : el("span", { class: "muted" }, "—");
  }

  function helmetCell(r) {
    if (r.helmet == null) return el("span", { class: "muted" }, "Unknown");
    const parts = [];
    if (r.helmet) parts.push(el("span", { class: "tag tag-ok" }, `Helmet ${r.helmet}`));
    if (r.no_helmet) parts.push(el("span", { class: "tag tag-bad" }, `No helmet ${r.no_helmet}`));
    return parts.length ? el("span", {}, ...parts) : el("span", { class: "muted" }, "Unknown");
  }

  function statusCell(r) {
    if (!r.violation) return el("span", { class: "tag tag-ok" }, "Normal");
    return el("span", {}, ...r.violations.map((v) => el("span", { class: "tag tag-bad" }, v)));
  }

  // Clicking a thumbnail inside `container` opens the evidence photo in the #snap-dialog
  function openSnapshotsFrom(container) {
    const dialog = document.getElementById("snap-dialog");
    container.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-snap]");
      if (!btn) return;
      document.getElementById("snap-img").src = `/snapshot/${btn.dataset.snap}/`;
      dialog.showModal();
    });
  }

  return { api, el, thumbCell, helmetCell, statusCell, openSnapshotsFrom };
})();
