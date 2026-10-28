"use strict";

// In-page notifications, bottom right of every page (#toasts, _settings_panel.html). Each kind is switched on
// per account in the Settings pop-up:
// - new violations (off by default): the server pushes each violation as it is saved (watchRecords in
//   common.js). The last record id seen is kept for the browser tab, so moving to another page neither
//   repeats nor misses one.
// - live camera status (admins, on by default): /api/system/cameras/ is asked every CAMERA_POLL_MS and each
//   camera's state is compared with the one last seen, also kept for the browser tab.
// Only on pages with #toasts: accounts that can see records or the system status.
window.Dashboard.onPage("#toasts", (page) => {
  const box = document.getElementById("toasts");
  const SHOW_MS = 10000;   // a notification closes itself after this
  const MAX_SHOWN = 3;     // at most this many violations; the rest are counted in one "+N more" under them
  const KEY = "cctv-notify-after";
  let on = box.dataset.on === "true";
  let stopWatching = null;
  page.onLeave(() => stopWatching?.());
  let more = null;         // the "+N more" notification while it is open
  let moreCount = 0;
  let moreTimer = null;

  // The last record id seen in this browser tab, or null: start from now
  function readAfter() {
    try {
      const id = sessionStorage.getItem(KEY);
      return id === null ? null : Number(id);
    } catch (e) { return null; }
  }

  function saveAfter(id) {
    try {
      if (id === null) sessionStorage.removeItem(KEY);
      else sessionStorage.setItem(KEY, String(id));
    } catch (e) { /* only this page remembers it */ }
  }

  function addCloseButton(toast, close) {
    toast.append(Object.assign(document.createElement("button"), {
      type: "button", className: "toast-close", textContent: "✕", ariaLabel: gettext("Dismiss"), onclick: close }));
  }

  // Newest on top. Pushing a 4th violation out counts it in "+N more" instead (camera notifications are not counted)
  function show(toast) {
    const close = () => toast.remove();
    addCloseButton(toast, close);
    box.prepend(toast);
    htmx.process(toast);   // its link is boosted like the top bar's (hx-boost on #toasts)
    const shown = box.querySelectorAll(".toast:not(.toast-more):not(.toast-camera)");
    for (let i = MAX_SHOWN; i < shown.length; i++) {
      shown[i].remove();
      addMore(1);
    }
    setTimeout(close, SHOW_MS);
  }

  // One "+N more violations" at the bottom; each violation added to it keeps it open SHOW_MS longer
  function addMore(count) {
    if (!more) {
      more = moreToast();
      addCloseButton(more, closeMore);
      box.append(more);
      htmx.process(more);
    }
    moreCount += count;
    more.querySelector("b").textContent = interpolate(
      ngettext("+%s more violation", "+%s more violations", moreCount), [moreCount]);
    clearTimeout(moreTimer);
    moreTimer = setTimeout(closeMore, SHOW_MS);
  }

  function closeMore() {
    clearTimeout(moreTimer);
    more?.remove();
    more = null;
    moreCount = 0;
  }

  // One violation: what was broken, the plate and when / where. Opens the violations page.
  function violationToast(r) {
    const { el, provinceOf } = window.Dashboard;   // builds with textContent only: plate text comes from OCR
    return el("div", { class: "toast" },
      el("a", { class: "toast-link", href: "/violations/" },
        el("span", { class: "toast-text" },
          el("b", {}, `${gettext("Violation")} · ${r.violations.join(", ")}`),
          el("span", {}, r.plate ? `${r.plate} · ${provinceOf(r)}` : gettext("Unreadable plate")),
          el("small", {}, `${r.time} · ${r.source}`))));
  }

  function moreToast() {
    const { el } = window.Dashboard;
    return el("div", { class: "toast toast-more" },
      el("a", { class: "toast-link", href: "/violations/" },
        el("span", { class: "toast-text" }, el("b", {}),
          el("small", {}, gettext("See them on the violations page")))));
  }

  function onViolations(data, lastId) {
    // Oldest first, so the newest ends up on top
    for (const r of [...data.rows].reverse()) show(violationToast(r));
    if (data.total > data.rows.length) addMore(data.total - data.rows.length);   // not sent, only counted
    saveAfter(lastId);
  }

  function watchViolations() {
    stopWatching?.();
    stopWatching = on ? window.Dashboard.watchRecords({ after: readAfter(), violations: true }, onViolations) : null;
  }

  // ---------- live camera status (admins) ----------

  const CAMERA_API = box.dataset.cameraApi;   // only there for accounts that see the system status
  const CAMERA_POLL_MS = 15000;   // the server re-checks the cameras every 30 s (system_health.py)
  const CAMERA_KEY = "cctv-camera-states";
  let camerasOn = box.dataset.cameras === "true";

  // {camera key: "online" | "offline" | "unconfigured"} as last seen, or null before the first check
  function readStates() {
    try { return JSON.parse(sessionStorage.getItem(CAMERA_KEY)); } catch (e) { return null; }
  }

  function saveStates(states) {
    try {
      if (states) sessionStorage.setItem(CAMERA_KEY, JSON.stringify(states));
      else sessionStorage.removeItem(CAMERA_KEY);
    } catch (e) { /* only this page remembers them */ }
  }

  // One camera that went offline or came back. Opens the system status page.
  function cameraToast(c, time) {
    const { el } = window.Dashboard;
    const online = c.state === "online";
    return el("div", { class: "toast toast-camera" + (online ? " is-ok" : "") },
      el("a", { class: "toast-link", href: "/system/" },
        el("span", { class: "toast-text" },
          el("b", {}, online ? gettext("Camera back online") : gettext("Camera offline")),
          el("span", {}, c.label),
          el("small", {}, online ? time : `${time} · ${c.detail || gettext("Not answering")}`))));
  }

  // Many cameras at once (e.g. the network dropped): one notification counting them
  function camerasToast(changed, time) {
    const { el } = window.Dashboard;
    const offline = changed.filter((c) => c.state === "offline").length;
    const back = changed.length - offline;
    const parts = [offline && interpolate(gettext("%s offline"), [offline]),
                   back && interpolate(gettext("%s back online"), [back])];
    return el("div", { class: "toast toast-camera" + (offline ? "" : " is-ok") },
      el("a", { class: "toast-link", href: "/system/" },
        el("span", { class: "toast-text" },
          el("b", {}, interpolate(gettext("%s cameras changed"), [changed.length])),
          el("span", {}, parts.filter(Boolean).join(" · ")),
          el("small", {}, `${time} · ${gettext("See them on the system status page")}`))));
  }

  async function pollCameras() {
    if (!camerasOn) return;
    try {
      const data = await window.Dashboard.api(CAMERA_API);
      const last = readStates();
      const states = {};
      for (const c of data.cameras) states[c.key] = c.state;
      saveStates(states);
      if (!last) return;   // the first check only sets what is "before"
      // Only online <-> offline is news; a camera that was just added or is not set up is not
      const changed = data.cameras.filter((c) => last[c.key] && last[c.key] !== c.state
        && c.state !== "unconfigured" && last[c.key] !== "unconfigured");
      const time = new Date(data.checked_at).toLocaleTimeString(document.documentElement.lang,
                                                                { hour: "2-digit", minute: "2-digit" });
      if (changed.length > MAX_SHOWN) show(camerasToast(changed, time));
      else for (const c of changed) show(cameraToast(c, time));
    } catch (e) { /* try again on the next poll */ }
  }

  // Switched on or off in the Settings pop-up (settings.js): start from now, not from before
  document.addEventListener("settings:preferences", (ev) => {
    if (ev.detail.violation_notify !== on) {
      on = ev.detail.violation_notify;
      saveAfter(null);
      watchViolations();
    }
    if (CAMERA_API && ev.detail.camera_notify !== camerasOn) {
      camerasOn = ev.detail.camera_notify;
      saveStates(null);
      pollCameras();
    }
  }, { signal: page.signal });

  watchViolations();
  // Camera checks skip a hidden tab and catch up as soon as it is shown again
  if (CAMERA_API) page.every(pollCameras, CAMERA_POLL_MS);
});
