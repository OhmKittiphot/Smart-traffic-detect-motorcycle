"use strict";

// The live view's video settings menu (#source-dialog, dashboard.html): the settings of the source shown
// (views/source_settings.py). Speed measurement and the detection area are drawn on a frame in the
// calibration editor (calibration.js) and the frame rate is this account's preference: these apply at once.
// The rest is saved with Save. dashboard.js passes on each status as a "live:status" event.
window.Dashboard.onPage("#source-dialog", (page) => {
  const { api, units } = window.Dashboard;
  const $ = (id) => document.getElementById(id);
  const prefs = JSON.parse($("prefs").textContent);
  const dialog = $("source-dialog");
  const openBtn = $("source-settings-btn");
  const speedInput = $("speed-limit");
  const MAX_SPEED_LIMIT_KMH = 300;   // the server checks the same
  let status = {};      // the latest status
  let current = null;   // the settings shown, as the server sent them
  // The location picked on the map, saved with Save: {name, spot: [latitude, longitude]}, or null
  let picked = null;

  function showMsg(text, kind) {
    $("source-msg").textContent = text;
    $("source-msg").className = "form-msg" + (kind ? " is-" + kind : "");
  }

  const post = (url, body) => api(url, { method: "POST", body: JSON.stringify(body) });

  // ---------- the button and the status ----------

  function renderButton(s) {
    const running = s.state === "running";
    openBtn.disabled = !running || !s.signal;
    openBtn.title = openBtn.disabled ? gettext("Needs a video signal") : "";
  }

  document.addEventListener("live:status", (ev) => {
    const s = ev.detail;
    const changedSource = s.source !== status.source;
    status = s;
    renderButton(s);
    renderFps(s);
    if (!dialog.open || !current) return;
    if (changedSource) {
      load();
      showMsg(gettext("The video source has changed"), "error");
    } else if (s.speed_calibrated !== current.speed_calibrated) {
      current.speed_calibrated = s.speed_calibrated;
      renderCalibration();
    }
  }, { signal: page.signal });

  // ---------- the menu ----------

  async function load() {
    current = await api("/api/source-settings/");
    render();
  }

  function renderCalibration() {
    const calibrated = current.speed_calibrated;
    $("calib-state").textContent = calibrated ? gettext("Calibrated") : gettext("Not calibrated");
    $("calib-btn").textContent = calibrated ? gettext("Redraw") : gettext("Calibrate");
    $("calib-btn").classList.toggle("btn-warn", !calibrated);
    $("calib-reset").hidden = !calibrated;
  }

  function renderZone() {
    const zone = current.detection_zone;
    $("zone-state").textContent = zone
      ? interpolate(gettext("Area of %s points"), [zone.length]) : gettext("Whole frame");   // 3 or more
    $("zone-btn").textContent = zone ? gettext("Redraw") : gettext("Draw");
    $("zone-clear").hidden = !zone;
  }

  // Typed and shown in the account's units (km/h or mph), saved in km/h; empty = the system's limit
  function renderSpeedLimit() {
    const system = current.system_speed_limit_kmh;
    $("speed-limit-unit").textContent = units.speedUnit();
    speedInput.max = units.speedToShown(MAX_SPEED_LIMIT_KMH);
    speedInput.value = current.speed_limit_kmh == null ? "" : units.speedToShown(current.speed_limit_kmh);
    speedInput.placeholder = units.speedToShown(system);
    $("speed-limit-hint").textContent = interpolate(
      gettext("Empty: the system's limit (%s). 0: speed is not a violation on this source."),
      [system ? units.speedText(system) : gettext("none")]);
  }

  const RECORDED_FROM = {
    set: gettext("Set by staff."),
    file: gettext("Read from the file."),
    "": gettext("Unknown: its motorcycles are saved at the time they are played."),
  };

  function renderFileSettings() {
    // The server sends its own local time ("2026-10-27T08:55:10+07:00"); the field takes that wall-clock
    // time without the zone, and the server reads what is saved back as its local time
    $("rec-time").value = current.recorded_at ? current.recorded_at.slice(0, 19) : "";
    $("rec-time").max = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 19);
    $("rec-reset").hidden = current.recorded_from !== "set";
    $("rec-hint").textContent = `${RECORDED_FROM[current.recorded_from]} ${gettext("Motorcycles already saved keep their time.")}`;
    picked = current.latitude != null ? { name: current.location, spot: [current.latitude, current.longitude] } : null;
    renderLocation();
    $("skip-repeats").checked = current.skip_repeats;
  }

  function render() {
    $("source-name").textContent = current.source;
    for (const row of dialog.querySelectorAll("[data-file-only]")) row.hidden = !current.is_file;
    renderCalibration();
    renderZone();
    renderSpeedLimit();
    if (current.is_file) renderFileSettings();
  }

  openBtn.addEventListener("click", async () => {
    showMsg("");
    try {
      await load();
      dialog.showModal();
    } catch (e) {
      $("form-msg").textContent = e.message;
    }
  });
  $("source-close").addEventListener("click", () => dialog.close());

  $("source-form-settings").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const typed = speedInput.value;
    const body = { source_key: current.source_key, speed_limit_kmh: typed === "" ? null : units.speedToKmh(Number(typed)) };
    if (current.is_file) {
      body.location = picked ? picked.name : "";
      [body.latitude, body.longitude] = picked ? picked.spot : [null, null];
      body.skip_repeats = $("skip-repeats").checked;
      // Only when changed: sending the time read from the file would make it one set by staff
      const shown = current.recorded_at ? current.recorded_at.slice(0, 19) : "";
      if ($("rec-time").value !== shown) body.recorded_at = $("rec-time").value || null;
    }
    showMsg(gettext("Saving…"), "busy");
    try {
      current = await post("/api/source-settings/", body);
      render();
      showMsg(gettext("Saved"), "ok");
    } catch (e) {
      showMsg(e.message, "error");
    }
  });

  // ---------- the location on the map (location-map.js) ----------

  function renderLocation() {
    $("location-name").textContent = picked ? picked.name : gettext("Not picked");
    $("location-name").title = picked ? picked.spot.join(", ") : "";
    $("location-clear").hidden = !picked;
  }

  $("location-map-btn").addEventListener("click", () => window.LocationMap.open({
    latitude: picked?.spot[0],
    longitude: picked?.spot[1],
    // Named after the spot (street, district); its coordinates when the map service gives no name
    onPick: async (spot) => {
      let name = "";
      try {
        name = await window.LocationMap.nameOf(...spot);
      } catch (e) {
        showMsg(e.message, "error");
      }
      picked = { name: name || spot.join(", "), spot };
      renderLocation();
    },
  }));
  $("location-clear").addEventListener("click", () => {
    picked = null;
    renderLocation();
  });

  $("rec-reset").addEventListener("click", async () => {
    try {
      current = await post("/api/source-settings/", { source_key: current.source_key, recorded_at: null });
      renderFileSettings();
      showMsg(gettext("The time in the file is used again"), "ok");
    } catch (e) {
      showMsg(e.message, "error");
    }
  });

  // ---------- drawn on a frame: speed calibration and detection area ----------
  // A playing video file is paused while the points are clicked, then plays on when the editor closes

  async function editOnFrame(options) {
    const resume = status.state === "running" && status.mode === "file" && !status.paused;
    const play = () => { if (resume) post("/api/pause/", { paused: false }).catch(() => {}); };
    try {
      if (resume) await post("/api/pause/", { paused: true });
      window.CalibrationEditor.open({ frameUrl: "/api/calibration/frame/", ...options, onClose: play });
    } catch (e) {
      play();
      showMsg(e.message, "error");
    }
  }

  $("calib-btn").addEventListener("click", async () => {
    try {
      const saved = await api("/api/calibration/");
      editOnFrame({
        ...saved,
        save: (body) => post("/api/calibration/save/", body),
        onSaved: () => { showMsg(gettext("Speed calibration saved"), "ok"); load(); },
      });
    } catch (e) {
      showMsg(e.message, "error");
    }
  });

  const resetDialog = $("calib-reset-dialog");
  $("calib-reset").addEventListener("click", () => {
    $("calib-reset-source").textContent = current.source;
    resetDialog.returnValue = "";
    resetDialog.showModal();
  });
  resetDialog.addEventListener("close", async () => {
    if (resetDialog.returnValue !== "confirm") return;
    try {
      await post("/api/calibration/remove/", {});
      await load();
      showMsg(gettext("Speed calibration reset"), "ok");
    } catch (e) {
      showMsg(e.message, "error");
    }
  });

  const saveZone = async (points) => {
    current = await post("/api/source-settings/zone/", { source_key: current.source_key, points });
    renderZone();
  };

  $("zone-btn").addEventListener("click", () => editOnFrame({
    zone: true,
    source: current.source,
    points: current.detection_zone,
    save: (body) => saveZone(body.points),
    onSaved: () => showMsg(gettext("Detection area saved"), "ok"),
  }));

  $("zone-clear").addEventListener("click", async () => {
    try {
      await saveZone(null);
      showMsg(gettext("Motorcycles are detected in the whole frame"), "ok");
    } catch (e) {
      showMsg(e.message, "error");
    }
  });

  // ---------- frame rate (video files) ----------
  // The choices below the file's own rate, then the file's rate itself. That one is saved as the highest
  // choice, so it stays "the file's own rate" for every file; the server never sends faster than the file.

  const fpsSelect = $("fps-select");
  const fpsChoices = fpsSelect.dataset.choices.split(" ").map(Number);
  const fullRate = fpsChoices[fpsChoices.length - 1];
  let fpsBuiltFor = null;   // the file rate the choices were made for

  function renderFps(s) {
    const isFile = s.mode === "file" && s.state === "running" && s.source_fps > 0;
    if (!isFile || s.source_fps === fpsBuiltFor) return;
    fpsBuiltFor = s.source_fps;
    const values = [...fpsChoices.filter((n) => n < s.source_fps), fullRate];
    fpsSelect.replaceChildren(...values.map((n) =>
      new Option(`${n === fullRate ? s.source_fps : n} fps`, n)));
    // A choice at or above this file's rate plays it at its own rate
    fpsSelect.value = values.includes(prefs.stream_fps) ? prefs.stream_fps : fullRate;
  }

  fpsSelect.addEventListener("change", async () => {
    const value = Number(fpsSelect.value);
    try {
      await api("/api/settings/preferences/", { method: "POST", body: JSON.stringify({ stream_fps: value }) });
      prefs.stream_fps = value;
    } catch (e) {
      showMsg(e.message, "error");
      fpsBuiltFor = null;   // shows the saved choice again with the next status
    }
  });

  // Units changed in Settings: the speed limit is shown in the new ones
  document.addEventListener("settings:preferences", (ev) => {
    Object.assign(prefs, ev.detail);
    if (dialog.open && current) renderSpeedLimit();
  }, { signal: page.signal });
});
