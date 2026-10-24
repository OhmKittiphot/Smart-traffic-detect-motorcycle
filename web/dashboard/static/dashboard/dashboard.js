"use strict";

(() => {
  const STATUS_EVERY_MS = 1000;
  const ROWS_EVERY_MS = 2000;
  const SEEK_STEP_MS = 10000;
  const MAX_ROWS = 300;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;

  const $ = (id) => document.getElementById(id);
  const streamImg = $("stream");
  const form = $("source-form");
  const fileSelect = $("file-select");
  const liveSelect = $("live-select");   // only rendered when an add-on adds live cameras
  const applyBtn = $("apply-btn");
  const playBtn = $("play-btn");
  const seekbar = $("seekbar");
  const seekInput = $("seek");
  const loadingEl = $("loading");
  const formMsg = $("form-msg");
  const rowsEl = $("vehicle-rows");
  const table = $("vehicles");
  const emptyMsg = $("empty-msg");

  let lastId = 0;
  let busy = false;
  // Playback position, advanced locally between status polls so the seek bar moves smoothly
  const playback = { pos: 0, total: 0, playing: false, at: performance.now() };
  let dragging = false;

  // ---------- helpers ----------

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
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
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

  function showMsg(text, kind) {
    formMsg.textContent = text;
    formMsg.className = "form-msg" + (kind ? " is-" + kind : "");
  }

  function selectedMode() {
    return form.querySelector('input[name="mode"]:checked').value;
  }

  function syncFormState() {
    const mode = selectedMode();
    fileSelect.disabled = mode !== "file" || !fileSelect.options.length || !fileSelect.value;
    if (liveSelect) {
      // Show only the dropdown that belongs to the selected mode
      liveSelect.hidden = mode !== "live";
      fileSelect.hidden = mode === "live";
    }
  }

  // ---------- status ----------

  function renderStatus(s) {
    const chip = $("engine-chip");
    chip.dataset.state = s.state;
    $("engine-text").textContent =
      s.state === "running" ? "Detection running" :
      s.state === "error" ? "System error: " + s.error : "Loading models…";

    const badge = $("source-badge");
    let kind = "nosignal";
    let label = "NO SIGNAL";
    if (s.signal) {
      if (s.paused) { kind = "paused"; label = "PAUSED"; }
      else if (s.mode === "live") { kind = "live"; label = "LIVE"; }
      else { kind = "file"; label = "FILE"; }
    }
    badge.dataset.kind = kind;
    badge.textContent = label;
    $("source-text").textContent = s.source;
    $("fps").textContent = s.fps.toFixed(1);
    $("calib").textContent = s.speed_calibrated ? "· Speed calibrated" : "· Speed not calibrated";
    calibBtn.disabled = s.state !== "running" || !s.signal;
    calibBtn.title = calibBtn.disabled ? "Needs a video signal" : "";

    applyBtn.disabled = busy || s.state !== "running";

    // Loading: models loading, playback held for the GPU, or detection falling behind
    loadingEl.hidden = !s.loading;
    $("loading-text").textContent = s.loading;

    const isFile = s.mode === "file" && s.state === "running" && s.total_ms > 0;
    seekbar.hidden = !isFile;
    if (isFile) {
      playback.pos = s.pos_ms;
      playback.total = s.total_ms;
      playback.playing = !s.paused && !s.loading;
      playback.at = performance.now();
      seekInput.max = s.total_ms;
      $("total-text").textContent = fmtTime(s.total_ms);
      playBtn.classList.toggle("is-paused", s.paused);
      playBtn.setAttribute("aria-label", s.paused ? "Play" : "Pause");
      drawPosition();
    }

    if (s.today) {
      $("stat-total").textContent = s.today.total;
      $("stat-normal").textContent = s.today.normal;
      $("stat-violation").textContent = s.today.violation;
    }
  }

  function fmtTime(ms) {
    const t = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    const sec = String(t % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
  }

  function currentPos() {
    const elapsed = playback.playing ? performance.now() - playback.at : 0;
    return Math.min(playback.total, playback.pos + elapsed);
  }

  function drawPosition() {
    if (dragging || seekbar.hidden) return;
    const pos = currentPos();
    seekInput.value = pos;
    $("pos-text").textContent = fmtTime(pos);
  }

  async function pollStatus() {
    try {
      renderStatus(await api("/api/status/"));
    } catch (e) { /* keep last state; next poll retries */ }
  }

  // ---------- vehicle table ----------

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

  function buildRow(r, isNew) {
    const tr = el("tr", { class: (r.violation ? "row-bad" : "row-ok") + (isNew ? " is-new" : "") });
    tr.dataset.id = r.id;

    const thumb = r.snapshot
      ? el("button", { type: "button", class: "thumb-btn", "data-snap": r.id, "aria-label": "View evidence photo" },
          el("img", { class: "thumb", src: `/snapshot/${r.id}/`, alt: "", loading: "lazy" }))
      : el("span", { class: "muted" }, "—");

    tr.append(
      el("td", { class: "time-cell" }, r.time, el("small", {}, r.date)),
      el("td", {}, thumb),
      el("td", { class: "plate" }, r.plate || el("span", { class: "muted" }, "Unreadable")),
      el("td", {}, r.province || "—"),
      el("td", { class: "num" }, r.riders ?? "—"),
      el("td", {}, helmetCell(r)),
      el("td", { class: "num" }, r.speed != null ? `${Math.round(r.speed)} km/h` : "—"),
      el("td", {}, statusCell(r)),
    );
    return tr;
  }

  function addRows(rows, isNew) {
    if (!rows.length) return;
    // API returns newest first; keep that order at the top of the table
    const frag = document.createDocumentFragment();
    for (const r of rows) frag.append(buildRow(r, isNew));
    rowsEl.prepend(frag);
    lastId = Math.max(lastId, ...rows.map((r) => r.id));
    while (rowsEl.children.length > MAX_ROWS) rowsEl.lastElementChild.remove();
    updateEmpty();
  }

  function updateEmpty() {
    const filter = table.dataset.filter;
    const visible = [...rowsEl.children].some((tr) =>
      filter === "all" || (filter === "violation") === tr.classList.contains("row-bad"));
    emptyMsg.hidden = visible;
    emptyMsg.textContent = rowsEl.children.length ? "No motorcycles match this filter" : "No motorcycles yet";
  }

  async function pollVehicles() {
    try {
      const data = await api(`/api/vehicles/?after=${lastId}`);
      addRows(data.rows, lastId > 0);
    } catch (e) { /* retry next tick */ }
  }

  document.querySelector(".filters").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".filter");
    if (!btn) return;
    for (const b of document.querySelectorAll(".filter")) {
      b.classList.toggle("is-active", b === btn);
      b.setAttribute("aria-selected", b === btn ? "true" : "false");
    }
    table.dataset.filter = btn.dataset.filter;
    updateEmpty();
  });

  // Evidence snapshot preview
  const dialog = $("snap-dialog");
  rowsEl.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-snap]");
    if (!btn) return;
    $("snap-img").src = `/snapshot/${btn.dataset.snap}/`;
    dialog.showModal();
  });

  // Delete all records (after confirmation)
  const clearDialog = $("clear-dialog");
  const clearBtn = $("clear-btn");
  clearBtn.addEventListener("click", () => {
    clearDialog.returnValue = "";
    clearDialog.showModal();
  });
  clearDialog.addEventListener("close", async () => {
    if (clearDialog.returnValue !== "confirm") return;
    clearBtn.disabled = true;
    try {
      await api("/api/vehicles/clear/", { method: "POST", body: "{}" });
      rowsEl.replaceChildren();
      updateEmpty();
      pollStatus();
    } catch (e) {
      showMsg(e.message, "error");
    } finally {
      clearBtn.disabled = false;
    }
  });

  // ---------- source control ----------

  function reloadStream() {
    streamImg.src = `/stream/?t=${Date.now()}`;
  }

  form.addEventListener("change", syncFormState);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const mode = selectedMode();
    if (mode === "file" && !fileSelect.value) {
      showMsg("No video file to choose", "error");
      return;
    }
    busy = true;
    applyBtn.disabled = true;
    showMsg("Switching source…");
    try {
      const s = await api("/api/source/", {
        method: "POST",
        body: JSON.stringify({
          mode,
          file: mode === "file" ? fileSelect.value : "",
          live: mode === "live" && liveSelect ? liveSelect.value : "",
        }),
      });
      renderStatus(s);
      showMsg("Source switched", "ok");
      reloadStream();
    } catch (e) {
      showMsg(e.message, "error");
    } finally {
      busy = false;
      applyBtn.disabled = false;
    }
  });

  // ---------- playback control (video files) ----------

  async function togglePause() {
    try {
      const r = await api("/api/pause/", { method: "POST", body: "{}" });
      playBtn.classList.toggle("is-paused", r.paused);
      pollStatus();
    } catch (e) {
      showMsg(e.message, "error");
    }
  }

  async function seekTo(posMs) {
    posMs = Math.max(0, Math.min(playback.total, posMs));
    // Show the jump right away; the server holds playback until the new frame is processed
    Object.assign(playback, { pos: posMs, playing: false, at: performance.now() });
    loadingEl.hidden = false;
    $("loading-text").textContent = "Processing frame…";
    drawPosition();
    try {
      await api("/api/seek/", { method: "POST", body: JSON.stringify({ pos_ms: posMs }) });
    } catch (e) {
      showMsg(e.message, "error");
    }
    pollStatus();
  }

  // "+10s" / "-10s" shown on the video; repeated presses add up ("+20s") like most video players
  const skipHint = $("skip-hint");
  let skipTotal = 0;
  let skipTimer = null;

  function skip(deltaMs) {
    const sameWay = skipTotal !== 0 && Math.sign(skipTotal) === Math.sign(deltaMs);
    skipTotal = (sameWay ? skipTotal : 0) + deltaMs;
    const secs = Math.round(skipTotal / 1000);
    skipHint.textContent = `${secs > 0 ? "+" : "-"}${Math.abs(secs)}s`;
    skipHint.dataset.side = secs > 0 ? "right" : "left";
    // Restart the fade animation
    skipHint.classList.remove("is-shown");
    void skipHint.offsetWidth;
    skipHint.classList.add("is-shown");
    clearTimeout(skipTimer);
    skipTimer = setTimeout(() => { skipTotal = 0; skipHint.classList.remove("is-shown"); }, 900);
    seekTo(currentPos() + deltaMs);
  }

  playBtn.addEventListener("click", togglePause);
  $("back-btn").addEventListener("click", () => skip(-SEEK_STEP_MS));
  $("fwd-btn").addEventListener("click", () => skip(SEEK_STEP_MS));

  seekInput.addEventListener("input", () => {
    dragging = true;
    $("pos-text").textContent = fmtTime(Number(seekInput.value));
  });
  seekInput.addEventListener("change", () => {
    dragging = false;
    seekTo(Number(seekInput.value));
  });

  // Keyboard: Space = play/pause, ←/→ = 10 s back/forward (not while typing or on a control)
  document.addEventListener("keydown", (ev) => {
    if (seekbar.hidden || ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (ev.target.closest("input, select, textarea, button, dialog")) return;
    if (ev.key === " ") { ev.preventDefault(); togglePause(); }
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); skip(-SEEK_STEP_MS); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); skip(SEEK_STEP_MS); }
  });

  // The MJPEG connection drops if the server restarts; reconnect after a short wait
  streamImg.addEventListener("error", () => setTimeout(reloadStream, 2000));

  // ---------- speed calibration ----------
  // Points are kept in frame pixels (the image's natural size), the same units the server uses.

  const SVG_NS = "http://www.w3.org/2000/svg";
  const POINT_NAMES = ["far-left", "far-right", "near-right", "near-left"];
  const calibBtn = $("calib-btn");
  const calibDialog = $("calib-dialog");
  const calibImg = $("calib-img");
  const calibSvg = $("calib-svg");
  const calibW = $("calib-w");
  const calibL = $("calib-l");
  const calibMsg = $("calib-msg");
  const calibSave = $("calib-save");
  const calibRemove = $("calib-remove");
  let calibPts = [];
  let dragIndex = -1;

  function calibStatus(text, kind) {
    calibMsg.textContent = text;
    calibMsg.className = "form-msg" + (kind ? " is-" + kind : "");
  }

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }

  function drawCalib() {
    const w = calibImg.naturalWidth || 1;
    const r = Math.max(6, w / 130);
    calibSvg.replaceChildren();
    if (calibPts.length > 1) {
      const pts = calibPts.map((p) => p.join(",")).join(" ");
      calibSvg.append(svgEl(calibPts.length === 4 ? "polygon" : "polyline", {
        points: pts, fill: calibPts.length === 4 ? "rgba(59,130,246,0.18)" : "none",
        stroke: "#facc15", "stroke-width": 2, "vector-effect": "non-scaling-stroke",
      }));
    }
    calibPts.forEach(([x, y], i) => {
      const g = svgEl("g", { class: "pt", "data-i": i });
      g.append(svgEl("circle", { cx: x, cy: y, r, fill: "#e5484d", stroke: "#fff", "stroke-width": 2,
        "vector-effect": "non-scaling-stroke" }));
      const label = svgEl("text", { x: x + r * 1.3, y: y - r * 1.3, fill: "#fff", "font-size": r * 2.6,
        "font-weight": 700, stroke: "#000", "stroke-width": 3, "paint-order": "stroke",
        "vector-effect": "non-scaling-stroke" });
      label.textContent = String(i + 1);
      g.append(label);
      calibSvg.append(g);
    });
    $("calib-next").textContent = calibPts.length < 4
      ? `Click point ${calibPts.length + 1}: ${POINT_NAMES[calibPts.length]}` : "";
    updateCalibSave();
  }

  function updateCalibSave() {
    const ok = calibPts.length === 4 && Number(calibW.value) > 0 && Number(calibL.value) > 0;
    calibSave.disabled = !ok;
  }

  // Pointer position in frame pixels
  function svgPoint(ev) {
    const pt = calibSvg.createSVGPoint();
    pt.x = ev.clientX;
    pt.y = ev.clientY;
    const p = pt.matrixTransform(calibSvg.getScreenCTM().inverse());
    const clamp = (v, max) => Math.round(Math.max(0, Math.min(max, v)) * 10) / 10;
    return [clamp(p.x, calibImg.naturalWidth), clamp(p.y, calibImg.naturalHeight)];
  }

  calibSvg.addEventListener("pointerdown", (ev) => {
    const g = ev.target.closest(".pt");
    if (g) {
      dragIndex = Number(g.dataset.i);
      g.classList.add("is-dragging");
      calibSvg.setPointerCapture(ev.pointerId);
    } else if (calibPts.length < 4) {
      calibPts.push(svgPoint(ev));
      drawCalib();
    }
    ev.preventDefault();
  });
  calibSvg.addEventListener("pointermove", (ev) => {
    if (dragIndex < 0) return;
    calibPts[dragIndex] = svgPoint(ev);
    drawCalib();
  });
  const endDrag = () => { dragIndex = -1; };
  calibSvg.addEventListener("pointerup", endDrag);
  calibSvg.addEventListener("pointercancel", endDrag);

  $("calib-undo").addEventListener("click", () => { calibPts.pop(); drawCalib(); });
  $("calib-clear-pts").addEventListener("click", () => { calibPts = []; drawCalib(); });
  calibW.addEventListener("input", updateCalibSave);
  calibL.addEventListener("input", updateCalibSave);

  function loadCalibFrame() {
    return new Promise((resolve, reject) => {
      calibImg.onload = () => {
        calibSvg.setAttribute("viewBox", `0 0 ${calibImg.naturalWidth} ${calibImg.naturalHeight}`);
        resolve();
      };
      calibImg.onerror = () => reject(new Error("Could not load a frame from the video"));
      calibImg.src = `/api/calibration/frame/?t=${Date.now()}`;
    });
  }

  $("calib-refresh").addEventListener("click", async () => {
    try { await loadCalibFrame(); drawCalib(); } catch (e) { calibStatus(e.message, "error"); }
  });

  calibBtn.addEventListener("click", async () => {
    calibStatus("");
    try {
      const [c] = await Promise.all([api("/api/calibration/"), loadCalibFrame()]);
      $("calib-source").textContent = c.source;
      calibPts = c.points ? c.points.map((p) => [p[0], p[1]]) : [];
      calibW.value = c.width_m ?? "";
      calibL.value = c.length_m ?? "";
      calibRemove.hidden = !c.points;
      drawCalib();
      calibDialog.showModal();
    } catch (e) {
      showMsg(e.message, "error");
    }
  });

  $("calib-cancel").addEventListener("click", () => calibDialog.close());

  calibSave.addEventListener("click", async () => {
    calibSave.disabled = true;
    calibStatus("Saving…");
    try {
      await api("/api/calibration/save/", {
        method: "POST",
        body: JSON.stringify({ points: calibPts, width_m: Number(calibW.value), length_m: Number(calibL.value) }),
      });
      calibDialog.close();
      showMsg("Speed calibration saved", "ok");
      pollStatus();
    } catch (e) {
      calibStatus(e.message, "error");
      updateCalibSave();
    }
  });

  calibRemove.addEventListener("click", async () => {
    try {
      await api("/api/calibration/remove/", { method: "POST", body: "{}" });
      calibDialog.close();
      showMsg("Speed calibration removed", "ok");
      pollStatus();
    } catch (e) {
      calibStatus(e.message, "error");
    }
  });

  // ---------- start ----------
  syncFormState();
  pollStatus();
  pollVehicles();
  setInterval(pollStatus, STATUS_EVERY_MS);
  setInterval(pollVehicles, ROWS_EVERY_MS);
  setInterval(drawPosition, 250);
})();
