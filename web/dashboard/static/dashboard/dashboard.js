"use strict";

(() => {
  const STATUS_EVERY_MS = 1000;
  const ROWS_EVERY_MS = 2000;
  const SEEK_STEP_MS = 10000;
  const MAX_ROWS = 300;
  const { api, el, thumbCell, helmetCell, statusCell, openSnapshotsFrom } = window.Dashboard;

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
    renderCalibration(s);

    applyBtn.disabled = busy || s.state !== "running";

    // Loading: models loading, playback held for the GPU, or detection falling behind
    loadingEl.hidden = !s.loading;

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
      const s = await api("/api/status/");
      renderStatus(s);
      window.FpsChart.push(s.state === "running" ? s.fps : null);
    } catch (e) { /* keep last state; next poll retries */ }
  }

  // ---------- vehicle table ----------

  function buildRow(r, isNew) {
    const tr = el("tr", { class: (r.violation ? "row-bad" : "row-ok") + (isNew ? " is-new" : "") });
    tr.dataset.id = r.id;

    tr.append(
      el("td", { class: "time-cell" }, r.time, el("small", {}, r.date)),
      el("td", {}, thumbCell(r)),
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
    for (const b of document.querySelectorAll(".filters .filter")) {
      b.classList.toggle("is-active", b === btn);
      b.setAttribute("aria-selected", b === btn ? "true" : "false");
    }
    table.dataset.filter = btn.dataset.filter;
    updateEmpty();
  });

  // Evidence snapshot preview
  openSnapshotsFrom(rowsEl);

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
    showMsg("Switching source…", "busy");
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
  // Yellow button: not calibrated yet; green: calibrated (click to redraw). Reset removes the calibration.

  const calibBtn = $("calib-btn");
  const calibReset = $("calib-reset");
  const resetDialog = $("calib-reset-dialog");

  function renderCalibration(s) {
    calibBtn.disabled = s.state !== "running" || !s.signal;
    calibBtn.classList.toggle("btn-ok", s.speed_calibrated);
    calibBtn.classList.toggle("btn-warn", !s.speed_calibrated);
    calibBtn.textContent = s.speed_calibrated ? "Speed calibrated" : "Calibrate speed";
    calibBtn.title = calibBtn.disabled ? "Needs a video signal"
      : s.speed_calibrated ? "Click to redraw the calibration" : "Speed is not measured until calibrated";
    calibReset.hidden = !s.speed_calibrated || s.state !== "running";
  }

  calibBtn.addEventListener("click", async () => {
    try {
      const c = await api("/api/calibration/");
      window.CalibrationEditor.open({
        ...c,
        frameUrl: "/api/calibration/frame/",
        save: (body) => api("/api/calibration/save/", { method: "POST", body: JSON.stringify(body) }),
        onSaved: () => { showMsg("Speed calibration saved", "ok"); pollStatus(); },
      });
    } catch (e) {
      showMsg(e.message, "error");
    }
  });

  calibReset.addEventListener("click", () => {
    $("calib-reset-source").textContent = $("source-text").textContent;
    resetDialog.returnValue = "";
    resetDialog.showModal();
  });
  resetDialog.addEventListener("close", async () => {
    if (resetDialog.returnValue !== "confirm") return;
    try {
      await api("/api/calibration/remove/", { method: "POST", body: "{}" });
      showMsg("Speed calibration reset", "ok");
      pollStatus();
    } catch (e) {
      showMsg(e.message, "error");
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
