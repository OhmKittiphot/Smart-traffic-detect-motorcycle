"use strict";

(() => {
  const STATUS_EVERY_MS = 1000;
  const ROWS_EVERY_MS = 2000;
  // This account's live view preferences, rendered into the page by the view and changed from the
  // Settings pop-up (see "preferences" below)
  const prefs = JSON.parse(document.getElementById("prefs").textContent);
  const MAX_ROWS = 300;
  const { api, el, units, thumbCell, plateCell, helmetCell, statusCell, openSnapshotsFrom } = window.Dashboard;

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
  const pauseOverlay = $("pause-overlay");
  const playFlash = $("play-flash");
  const videoFrame = document.querySelector(".video-frame");
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
      s.state === "running" ? gettext("Detection running") :
      s.state === "error" ? interpolate(gettext("System error: %s"), [s.error]) : gettext("Loading models…");

    const badge = $("source-badge");
    let kind = "nosignal";
    let label = gettext("NO SIGNAL");
    if (s.signal) {
      if (s.paused) { kind = "paused"; label = gettext("PAUSED"); }
      else if (s.mode === "live") { kind = "live"; label = gettext("LIVE"); }
      else { kind = "file"; label = gettext("FILE"); }
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
    pauseOverlay.hidden = !(isFile && s.paused && !s.loading);
    videoFrame.classList.toggle("is-file", isFile);
    if (isFile) {
      playback.pos = s.pos_ms;
      playback.total = s.total_ms;
      playback.playing = !s.paused && !s.loading;
      playback.at = performance.now();
      seekInput.max = s.total_ms;
      $("total-text").textContent = fmtTime(s.total_ms);
      playBtn.classList.toggle("is-paused", s.paused);
      playBtn.setAttribute("aria-label", s.paused ? gettext("Play") : gettext("Pause"));
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

  // True after a status poll failed, e.g. while the server restarts
  let serverLost = false;

  async function pollStatus() {
    if (document.hidden) return;   // a hidden tab does not keep detection on (see engine.IDLE_STOP_S)
    try {
      const s = await api("/api/status/");
      // The browser gives no event when the server closes the video stream: the picture just freezes
      // on its last frame. So reopen the stream once the server answers again
      if (serverLost) {
        serverLost = false;
        reloadStream();
      }
      renderStatus(s);
      window.FpsChart.push(s.state === "running" ? s.fps : null, s.state === "running" && s.paused);
    } catch (e) {
      serverLost = true;   // keep last state; next poll retries
    }
  }

  // ---------- vehicle table ----------

  function buildRow(r) {
    const tr = el("tr", { class: r.violation ? "row-bad" : "row-ok" });
    tr.dataset.id = r.id;

    tr.append(
      el("td", { class: "time-cell" }, r.time, el("small", {}, r.date)),
      el("td", {}, thumbCell(r)),
      el("td", { class: "plate" }, plateCell(r)),
      el("td", {}, r.province || "—"),
      el("td", { class: "num" }, r.riders ?? "—"),
      el("td", {}, helmetCell(r)),
      el("td", { class: "num" }, units.speed(r.speed) ?? "—"),
      el("td", {}, statusCell(r)),
    );
    return tr;
  }

  function addRows(rows, isNew) {
    if (!rows.length) return;
    // API returns newest first; keep that order at the top of the table
    const frag = document.createDocumentFragment();
    for (const r of rows) frag.append(buildRow(r));
    const oldFirst = rowsEl.firstElementChild;
    rowsEl.prepend(frag);
    if (isNew && oldFirst) printRows(oldFirst.offsetTop - rowsEl.firstElementChild.offsetTop);
    lastId = Math.max(lastId, ...rows.map((r) => r.id));
    while (rowsEl.children.length > MAX_ROWS) rowsEl.lastElementChild.remove();
    updateEmpty();
  }

  // New rows come out from under the sticky header like a receipt from a printer: the table body starts
  // moved up by their height and slides down. Skipped when the table is scrolled, so it does not jump.
  function printRows(height) {
    if (!height || table.parentElement.scrollTop > 0 || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    rowsEl.animate([{ transform: `translateY(${-height}px)` }, { transform: "none" }],
                   { duration: 450, easing: "cubic-bezier(.25, .8, .3, 1)" });
  }

  function updateEmpty() {
    const filter = table.dataset.filter;
    const visible = [...rowsEl.children].some((tr) =>
      filter === "all" || (filter === "violation") === tr.classList.contains("row-bad"));
    emptyMsg.hidden = visible;
    emptyMsg.textContent = rowsEl.children.length ? gettext("No motorcycles match this filter") : gettext("No motorcycles yet");
  }

  async function pollVehicles() {
    if (document.hidden) return;   // caught up on return (see visibilitychange)
    try {
      const data = await api(`/api/vehicles/?after=${lastId}`);
      addRows(data.rows, lastId > 0);
    } catch (e) { /* retry next tick */ }
  }

  function selectFilter(filter) {
    for (const b of document.querySelectorAll(".filters .filter")) {
      const on = b.dataset.filter === filter;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    }
    table.dataset.filter = filter;
    updateEmpty();
  }

  document.querySelector(".filters").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".filter");
    if (btn) selectFilter(btn.dataset.filter);
  });

  // Evidence snapshot preview
  openSnapshotsFrom(rowsEl);

  // Delete all records (after confirmation); the button is only there with the delete_records permission
  const clearDialog = $("clear-dialog");
  const clearBtn = $("clear-btn");
  clearBtn?.addEventListener("click", () => {
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
    if (document.hidden) return;
    streamImg.src = `/stream/?t=${Date.now()}`;
  }

  // Close the stream and stop polling while the tab is hidden, so detection stops and frees the GPU;
  // reopen and catch up on return
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      streamImg.removeAttribute("src");
    } else {
      reloadStream();
      pollStatus();
      pollVehicles();
    }
  });

  form.addEventListener("change", syncFormState);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const mode = selectedMode();
    if (mode === "file" && !fileSelect.value) {
      showMsg(gettext("No video file to choose"), "error");
      return;
    }
    busy = true;
    applyBtn.disabled = true;
    showMsg(gettext("Switching source…"), "busy");
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
      showMsg(gettext("Source switched"), "ok");
      reloadStream();
    } catch (e) {
      showMsg(e.message, "error");
    } finally {
      busy = false;
      applyBtn.disabled = false;
    }
  });

  // ---------- playback control (video files) ----------

  // Play a CSS animation again from the start, also when it is still running
  function restartAnimation(node, cls) {
    node.classList.remove(cls);
    void node.offsetWidth;   // reflow, so adding the class back starts the animation over
    node.classList.add(cls);
  }

  async function togglePause() {
    try {
      const r = await api("/api/pause/", { method: "POST", body: "{}" });
      playBtn.classList.toggle("is-paused", r.paused);
      pauseOverlay.hidden = !r.paused;   // pops in when shown
      if (!r.paused) restartAnimation(playFlash, "is-shown");
      restartAnimation(playBtn, "is-bumped");
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
  // Clicking on a video file plays / pauses it, like a video player
  videoFrame.addEventListener("click", () => { if (!seekbar.hidden) togglePause(); });
  $("back-btn").addEventListener("click", () => skip(-prefs.seek_step_s * 1000));
  $("fwd-btn").addEventListener("click", () => skip(prefs.seek_step_s * 1000));

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
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); skip(-prefs.seek_step_s * 1000); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); skip(prefs.seek_step_s * 1000); }
  });

  // Leaving the live view pauses a playing video file, so it does not run on while no one watches.
  // keepalive lets the request finish after the page is gone
  window.addEventListener("pagehide", () => {
    if (seekbar.hidden || !playback.playing) return;
    api("/api/pause/", { method: "POST", body: JSON.stringify({ paused: true }), keepalive: true })
      .catch(() => {});
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
    calibBtn.textContent = s.speed_calibrated ? gettext("Speed calibrated") : gettext("Calibrate speed");
    calibBtn.title = calibBtn.disabled ? gettext("Needs a video signal")
      : s.speed_calibrated ? gettext("Click to redraw the calibration") : gettext("Speed is not measured until calibrated");
    calibReset.hidden = !s.speed_calibrated || s.state !== "running";
  }

  calibBtn.addEventListener("click", async () => {
    try {
      const c = await api("/api/calibration/");
      window.CalibrationEditor.open({
        ...c,
        frameUrl: "/api/calibration/frame/",
        save: (body) => api("/api/calibration/save/", { method: "POST", body: JSON.stringify(body) }),
        onSaved: () => { showMsg(gettext("Speed calibration saved"), "ok"); pollStatus(); },
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
      showMsg(gettext("Speed calibration reset"), "ok");
      pollStatus();
    } catch (e) {
      showMsg(e.message, "error");
    }
  });

  // ---------- preferences ----------
  // Saved in the Settings pop-up (settings.js): applied at once, as the page would show them after a reload

  // The speed chart slides open / closed: its height (and the grid gap above it) grows from or shrinks to 0
  const fpsChart = $("fps-chart");
  let chartShown = !fpsChart.hidden;

  function showChart(show) {
    if (show === chartShown) return;
    chartShown = show;
    for (const a of fpsChart.getAnimations()) a.cancel();   // switched again mid-way: start over
    fpsChart.hidden = false;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      fpsChart.hidden = !show;
      return;
    }
    const cs = getComputedStyle(fpsChart);
    const open = { height: `${fpsChart.offsetHeight}px`, paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom,
                   borderTopWidth: cs.borderTopWidth, borderBottomWidth: cs.borderBottomWidth, marginTop: "0px", opacity: 1 };
    const closed = { height: "0px", paddingTop: "0px", paddingBottom: "0px", borderTopWidth: "0px",
                     borderBottomWidth: "0px", marginTop: `-${getComputedStyle(fpsChart.parentElement).rowGap}`, opacity: 0 };
    fpsChart.style.overflow = "hidden";
    const anim = fpsChart.animate(show ? [closed, open] : [open, closed], { duration: 250, easing: "ease" });
    anim.onfinish = () => {
      fpsChart.style.overflow = "";
      if (!show) fpsChart.hidden = true;
    };
  }

  document.addEventListener("settings:preferences", (ev) => {
    Object.assign(prefs, ev.detail);
    selectFilter(prefs.table_filter);
    showChart(prefs.show_fps_chart);
    const steps = [["back-btn", gettext("Back %s seconds"), "←"], ["fwd-btn", gettext("Forward %s seconds"), "→"]];
    for (const [id, text, key] of steps) {
      const label = interpolate(text, [prefs.seek_step_s]);
      $(id).title = `${label} (${key})`;
      $(id).setAttribute("aria-label", label);
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
