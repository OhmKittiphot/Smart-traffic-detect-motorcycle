"use strict";

// Admin system status page: polls /api/system/ (cameras are re-checked by the server every 30 s)
window.Dashboard.onPage("#system-page", (page) => {
  const POLL_MS = 5000;
  const { api, el, locationCell } = window.Dashboard;
  const $ = (id) => document.getElementById(id);

  const CAMERA_STATES = {
    online: ["tag-ok", gettext("Online")],
    offline: ["tag-bad", gettext("Offline")],
    unconfigured: ["tag-muted", gettext("Not configured")],
  };

  function bytes(n) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
  }

  const time = (iso) => (iso ? new Date(iso).toLocaleString(document.documentElement.lang) : "—");

  // Name of a camera / file, marked when it is the source being processed now
  function sourceCell(name, source) {
    return el("td", {}, name, source.in_use ? el("span", { class: "tag tag-ok sys-in-use" }, gettext("In use")) : null);
  }

  const canCalibrate = $("system-page").dataset.canCalibrate === "true";
  const sourcesByKey = new Map();   // camera / file rows as the API sent them, for the dialogs
  // The points are clicked on a frame the server grabs from the source itself (detection need not be on),
  // so they can be redrawn on any video file and on cameras that are online
  const canRedraw = (source) => !source.state || source.state === "online";

  // Status of a source's speed calibration, with Calibrate / Edit / Remove for users who may change it
  function calibrationCell(source, name) {
    sourcesByKey.set(source.source_key, { ...source, name });
    const c = source.calibration;
    const cell = el("td", { class: "sys-calib" });
    const size = c && interpolate(gettext("Calibrated · %(width)s × %(length)s m"),
                                  { width: c.width_m, length: c.length_m }, true);
    if (c) cell.append(el("span", { class: "tag tag-ok" }, size));
    else cell.append(el("span", { class: "tag tag-muted" }, gettext("Not calibrated")));
    if (canCalibrate && (c || canRedraw(source))) {
      const title = canRedraw(source) ? gettext("Redraw the points and change the size")
        : gettext("Change the size (the camera is offline, so the points cannot be redrawn now)");
      cell.append(el("button", { type: "button", class: "btn btn-sm btn-ghost", "data-edit": source.source_key, title },
        c ? gettext("Edit") : gettext("Calibrate")));
    }
    if (canCalibrate && c) {
      cell.append(el("button", { type: "button", class: "btn btn-sm btn-danger-ghost", "data-remove": source.source_key },
        gettext("Remove")));
    }
    return cell;
  }

  // Health is checked by the server on every poll without starting detection (it runs only while staff
  // have the live view open)
  function renderEngine({ health }) {
    $("engine-state").textContent = health.label;
    $("engine-card").className = "stat " + (health.state === "ok" ? "stat-ok"
      : health.state === "problem" || health.state === "error" ? "stat-bad" : "");
    $("engine-detail").textContent = health.detail;
  }

  // Space used by the evidence photos (limited by MEDIA_MAX_MB, see dashboard/storage.py) and the database
  function renderStorage(s) {
    const share = s.photos_limit ? Math.round((s.photos / s.photos_limit) * 100) : 0;
    const line = $("storage-use");
    line.textContent = interpolate(
      gettext("Photos %(used)s of %(limit)s (%(share)s%) · database %(db)s · %(free)s free on disk"),
      { used: bytes(s.photos), limit: bytes(s.photos_limit), share, db: bytes(s.database), free: bytes(s.disk_free) }, true);
    line.classList.toggle("is-warning", share >= 90);
  }

  function renderRecords(records) {
    $("records").textContent = `${records.today} / ${records.total}`;
    $("last-record").textContent = interpolate(gettext("Last detection: %s"), [time(records.last_detected_at)]);
    $("pending").textContent = records.pending_review;
    $("violations-today").textContent = interpolate(ngettext("%s violation today", "%s violations today", records.violations_today),
      [records.violations_today]);
  }

  function renderCameras(cameras, checkedAt) {
    $("cameras-checked").textContent = interpolate(gettext("Checked %s"), [time(checkedAt)]);
    $("camera-rows").replaceChildren(...cameras.map((c) => {
      const [kind, label] = CAMERA_STATES[c.state];
      return el("tr", {},
        sourceCell(c.label, c),
        el("td", {}, el("span", { class: `tag ${kind}` }, label)),
        el("td", { class: "num" }, c.ms == null ? "—" : `${c.ms} ms`),
        calibrationCell(c, c.label),
        el("td", { class: "muted" }, c.detail || ""));
    }));
  }

  const RECORDED_FROM = { set: gettext("set by staff"), file: gettext("from the file") };

  // When a file started recording (its motorcycles are saved at that time plus their time in the clip).
  // Staff set it on the live view while the file plays.
  function recordingCell(f) {
    return el("td", {},
      f.recorded_at ? time(f.recorded_at)
        : el("span", { class: "tag tag-muted", title: gettext("Its motorcycles are saved at the time they are played") },
             gettext("Unknown")),
      f.recorded_from ? el("small", { class: "muted" }, ` · ${RECORDED_FROM[f.recorded_from]}`) : null);
  }

  function renderFiles(files) {
    $("no-files").hidden = files.length > 0;
    $("file-rows").replaceChildren(...files.map((f) => el("tr", {},
      sourceCell(f.name, f),
      el("td", { class: "num" }, bytes(f.size)),
      el("td", {}, time(f.modified)),
      recordingCell(f),
      el("td", {}, locationCell(f)),
      calibrationCell(f, f.name))));
  }

  // ---------- edit distances / remove a calibration (Admin) ----------

  const distDialog = $("dist-dialog");
  const removeDialog = $("remove-dialog");
  let editing = null;   // the source the open dialog is about

  function showDistMsg(text, kind) {
    $("dist-msg").textContent = text;
    $("dist-msg").className = "form-msg" + (kind ? " is-" + kind : "");
  }

  function openEdit(key) {
    editing = sourcesByKey.get(key);
    if (canRedraw(editing)) {
      const source = editing;
      const c = source.calibration || {};
      window.CalibrationEditor.open({
        source: source.name, points: c.points, width_m: c.width_m, length_m: c.length_m,
        frameUrl: `/api/system/calibration/frame/?source_key=${encodeURIComponent(source.source_key)}`,
        save: (body) => api("/api/system/calibration/points/", {
          method: "POST", body: JSON.stringify({ ...body, source_key: source.source_key }),
        }),
        onSaved: refresh,
      });
      return;
    }
    if (!editing.calibration) {   // the camera went offline since the buttons were drawn
      $("sys-msg").textContent = gettext("No video signal to calibrate on");
      return;
    }
    $("dist-source").textContent = editing.name;
    $("dist-w").value = editing.calibration.width_m;
    $("dist-l").value = editing.calibration.length_m;
    showDistMsg("");
    distDialog.showModal();
  }

  async function saveDistances(ev) {
    ev.preventDefault();
    showDistMsg(gettext("Saving…"), "busy");
    try {
      await api("/api/system/calibration/", {
        method: "POST",
        body: JSON.stringify({ source_key: editing.source_key,
          width_m: Number($("dist-w").value), length_m: Number($("dist-l").value) }),
      });
      distDialog.close();
      refresh();
    } catch (e) {
      showDistMsg(e.message, "error");
    }
  }

  function openRemove(key) {
    editing = sourcesByKey.get(key);
    $("remove-source").textContent = editing.name;
    removeDialog.returnValue = "";
    removeDialog.showModal();
  }

  removeDialog.addEventListener("close", async () => {
    if (removeDialog.returnValue !== "confirm") return;
    try {
      await api("/api/system/calibration/remove/", {
        method: "POST", body: JSON.stringify({ source_key: editing.source_key }),
      });
      refresh();
    } catch (e) {
      $("sys-msg").textContent = interpolate(gettext("Could not remove calibration: %s"), [e.message]);
    }
  });

  // Rows are rebuilt on every poll, so the buttons are handled on the tables
  for (const tbody of [$("camera-rows"), $("file-rows")]) {
    tbody.addEventListener("click", (ev) => {
      const edit = ev.target.closest("[data-edit]");
      const remove = ev.target.closest("[data-remove]");
      if (edit) openEdit(edit.dataset.edit);
      if (remove) openRemove(remove.dataset.remove);
    });
  }
  $("dist-form").addEventListener("submit", saveDistances);
  $("dist-cancel").addEventListener("click", () => distDialog.close());

  // ---------- models card ----------
  // The list is cut to the height of the other cards; the button only shows when something is cut off

  const modelCard = $("model-card");
  const modelList = $("model-list");
  const modelToggle = $("model-toggle");
  const modelCount = modelToggle.textContent;
  modelToggle.hidden = modelList.scrollHeight <= modelList.clientHeight;
  function setModelsOpen(open) {
    modelCard.classList.toggle("is-open", open);
    modelToggle.setAttribute("aria-expanded", String(open));
    modelToggle.textContent = open ? gettext("Show less") : modelCount;
  }
  modelToggle.addEventListener("click", () => setModelsOpen(!modelCard.classList.contains("is-open")));
  // Opened, the card covers the table below it: a click elsewhere or Esc closes it
  document.addEventListener("click", (ev) => {
    if (modelCard.classList.contains("is-open") && !modelCard.contains(ev.target)) setModelsOpen(false);
  }, { signal: page.signal });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && modelCard.classList.contains("is-open")) setModelsOpen(false);
  }, { signal: page.signal });

  // ---------- polling ----------

  async function refresh() {
    try {
      const data = await api("/api/system/");
      renderEngine(data.engine);
      renderRecords(data.records);
      renderStorage(data.storage);
      sourcesByKey.clear();
      renderCameras(data.cameras, data.cameras_checked_at);
      renderFiles(data.video_files);
      for (const tbody of [$("camera-rows"), $("file-rows")]) tbody.removeAttribute("aria-busy");
      // The page is locked until the first status arrives (see system.html)
      $("system-page").inert = false;
      $("system-page").removeAttribute("aria-busy");
      $("sys-msg").textContent = "";
    } catch (e) {
      $("sys-msg").textContent = interpolate(gettext("Could not load status: %s"), [e.message]);
    }
  }

  // No requests while the tab is hidden; refreshed as soon as it is shown again
  page.every(refresh, POLL_MS);
});
