"use strict";

// The live view: video, status, playback and source controls, and the table of motorcycles passing
window.Dashboard.onPage("#stream", (page) => {
  // This account's live view preferences, rendered into the page by the view and changed from the
  // Settings pop-up (see "preferences" below)
  const prefs = JSON.parse(document.getElementById("prefs").textContent);
  const MAX_ROWS = 300;
  const {
    api, el, units, thumbCell, plateCell, helmetCell, statusCell, locationCell, watchEvents, watchRecords, openSnapshotsFrom,
  } = window.Dashboard;

  const $ = (id) => document.getElementById(id);
  const streamImg = $("stream");
  const form = $("source-form");
  const sourceList = $("source-pick-list");   // the source menu, an Alpine.js component (menus.js)
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
  const loadMoreBtn = $("load-more");

  let lastId = 0;
  let busy = false;
  // Playback position, advanced locally between status events so the seek bar moves smoothly
  const playback = { pos: 0, total: 0, playing: false, at: performance.now() };
  let dragging = false;

  // ---------- helpers ----------

  function showMsg(text, kind) {
    formMsg.textContent = text;
    formMsg.className = "form-msg" + (kind ? " is-" + kind : "");
  }

  // The source chosen in the source menu (a .source-item), or null before one is
  function chosenSource() {
    return sourceList.querySelector('.source-item[aria-pressed="true"]');
  }

  // ---------- status ----------

  function renderStatus(s) {
    const chip = $("engine-chip");
    chip.dataset.state = s.state;
    $("engine-text").textContent =
      s.state === "running" ? gettext("Detection running") :
      s.state === "error" ? interpolate(gettext("System error: %s"), [s.error]) :
      s.state === "stopped" ? gettext("Detection stopped") : gettext("Loading models…");

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
    renderRules(s);
    // The video settings menu (source-settings.js) follows the status too
    document.dispatchEvent(new CustomEvent("live:status", { detail: s }));

    applyBtn.disabled = busy || s.state !== "running";

    // Loading: models loading, playback held for the GPU, or detection falling behind
    loadingEl.hidden = !s.loading;

    const isFile = s.mode === "file" && s.state === "running" && s.total_ms > 0;
    seekbar.hidden = !isFile;
    pauseOverlay.hidden = !(isFile && s.paused && !s.loading);
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

  // True after the status stream dropped, e.g. while the server restarts
  let serverLost = false;

  // The server sends the status every second (views/events.py)
  function onStatus(ev) {
    const s = JSON.parse(ev.data);
    // The browser gives no event when the server closes the video stream: the picture just freezes
    // on its last frame. So reopen the stream once the server answers again
    if (serverLost) {
      serverLost = false;
      reloadStream();
    }
    renderStatus(s);
    window.FpsChart.push(s.state === "running" ? s.fps : null, s.state === "running" && s.paused);
  }

  // The speed limit of the source shown (its own, or the system's); 0 = speed is not a violation
  function renderRules(s) {
    if (s.speed_limit_kmh == null) return;
    $("rules-speed").hidden = !s.speed_limit_kmh;
    const limit = $("rules-speed").querySelector("[data-kmh]");
    limit.dataset.kmh = s.speed_limit_kmh;
    limit.textContent = units.speedText(s.speed_limit_kmh);
  }

  // ---------- vehicle table ----------

  function buildRow(r) {
    const tr = el("tr", { class: r.violation ? "row-bad" : "row-ok" });
    tr.dataset.id = r.id;

    tr.append(
      el("td", { class: "time-cell" }, r.time, el("small", {}, r.date)),
      el("td", {}, thumbCell(r)),
      el("td", { class: "plate" }, plateCell(r)),   // with the province: one column less, so the table fits
      el("td", { class: "num" }, r.riders ?? "—"),
      el("td", { class: "helmet-cell" }, helmetCell(r)),
      el("td", { class: "num" }, units.speed(r.speed) ?? "—"),
      el("td", { class: "status-cell" }, statusCell(r)),
      el("td", {}, locationCell(r)),
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
    if (rowsEl.children.length > MAX_ROWS) {
      while (rowsEl.children.length > MAX_ROWS) rowsEl.lastElementChild.remove();
      loadMoreBtn.hidden = false;   // the rows dropped at the bottom can be loaded again
    }
    updateEmpty();
  }

  // Older rows below the ones shown (Load more)
  function appendRows(rows) {
    const frag = document.createDocumentFragment();
    for (const r of rows) frag.append(buildRow(r));
    rowsEl.append(frag);
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

  // New rows wait while the pointer is over the table: a row arriving between pressing and releasing the
  // mouse moved the photo away from under it, and the browser then fires no click (about 1 press in 10 while
  // a video played). They are added as soon as the pointer leaves. A touch leaves right after each tap.
  let pointerOverTable = false;
  let heldRows = [];   // newest first, like the API

  function addNewRows(rows) {
    if (pointerOverTable) heldRows = rows.concat(heldRows);
    else addRows(rows, true);
  }

  table.parentElement.addEventListener("pointerenter", () => { pointerOverTable = true; });
  table.parentElement.addEventListener("pointerleave", () => {
    pointerOverTable = false;
    addRows(heldRows, true);
    heldRows = [];
  });

  // The latest few rows, then each new one as the server saves it (pushed, see watchRecords in common.js);
  // older rows only when Load more is pressed
  async function loadVehicles() {
    try {
      const data = await api("/api/vehicles/");
      addRows(data.rows, false);
      loadMoreBtn.hidden = !data.more;
    } catch (e) { /* the stream still brings the newest ones */ }
    if (page.signal.aborted) return;   // left while the rows were on their way
    page.onLeave(watchRecords({ after: lastId }, (data) => addNewRows(data.rows)));
  }

  loadMoreBtn.addEventListener("click", async () => {
    const oldest = rowsEl.lastElementChild?.dataset.id;
    loadMoreBtn.disabled = true;
    try {
      const data = await api(`/api/vehicles/?before=${oldest}`);
      appendRows(data.rows);
      loadMoreBtn.hidden = !data.more;
    } catch (e) {
      showMsg(e.message, "error");
    } finally {
      loadMoreBtn.disabled = false;
    }
  });

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

  // Delete all records (after confirmation)
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
      heldRows = [];
      loadMoreBtn.hidden = true;
      updateEmpty();
    } catch (e) {
      showMsg(e.message, "error");
    } finally {
      clearBtn.disabled = false;
    }
  });

  // ---------- video ----------

  const reloadStream = () => LiveVideo.reload();

  // Close the video while the tab is hidden, so it costs no bandwidth (detection goes on: it runs while
  // staff are logged in, see engine.py), and reopen it on return. The event streams do the same
  // (watchEvents in common.js)
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) LiveVideo.stop();
    else reloadStream();
  }, { signal: page.signal });

  // ---------- source control ----------
  // The source menu (menus.js) marks the source chosen; Switch source switches to it

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const source = chosenSource();
    if (!source) {
      showMsg(gettext("No video file to choose"), "error");
      return;
    }
    const mode = source.dataset.mode;
    // The playlist item plays the ticked files, in the order listed
    const playlist = source.dataset.playlist
      ? [...sourceList.querySelectorAll('input[name="playlist"]:checked')].map((box) => box.value)
      : [];
    if (source.dataset.playlist && playlist.length < 2) {
      showMsg(gettext("Tick at least two files for the playlist"), "error");
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
          file: mode === "file" ? source.dataset.value : "",
          live: mode === "live" ? source.dataset.value : "",
          playlist,
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

  // paused: true / false sets it; left out, it flips
  async function setPaused(paused) {
    try {
      const body = JSON.stringify(paused === undefined ? {} : { paused });
      const r = await api("/api/pause/", { method: "POST", body });
      playBtn.classList.toggle("is-paused", r.paused);
      pauseOverlay.hidden = !r.paused;   // pops in when shown
      if (!r.paused) restartAnimation(playFlash, "is-shown");
      restartAnimation(playBtn, "is-bumped");
    } catch (e) {
      showMsg(e.message, "error");
    }
  }

  const togglePause = () => setPaused();

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
  }, { signal: page.signal });

  // Leaving the live view (to another page, or closing the tab) pauses a playing video file, so it does not
  // run on while no one watches. keepalive lets the request finish after the page is gone
  function pauseOnLeave() {
    if (seekbar.hidden || !playback.playing) return;
    api("/api/pause/", { method: "POST", body: JSON.stringify({ paused: true }), keepalive: true })
      .catch(() => {});
  }
  window.addEventListener("pagehide", pauseOnLeave, { signal: page.signal });
  page.onLeave(pauseOnLeave);

  // The MJPEG connection drops if the server restarts; reconnect after a short wait
  streamImg.addEventListener("error", () => setTimeout(reloadStream, 2000));

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
  }, { signal: page.signal });

  // ---------- start ----------
  page.onLeave(watchEvents(() => "/api/events/status/", { status: onStatus, error: () => { serverLost = true; } }));
  loadVehicles();
  page.every(drawPosition, 250);
});
