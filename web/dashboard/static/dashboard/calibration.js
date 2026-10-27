"use strict";

// Speed calibration editor (#calib-dialog, _calibration_dialog.html), used by the live view and the
// system status page. Points are kept in frame pixels (the image's natural size), the same units the
// server uses. Exposed as window.CalibrationEditor.open(options) on the pages with the dialog.
window.Dashboard.onPage("#calib-dialog", () => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const POINT_NAMES = [gettext("far-left"), gettext("far-right"), gettext("near-right"), gettext("near-left")];
  const MIN_M = 0.1;   // allowed width / length in metres (the server checks the same range)
  const MAX_M = 500;
  const { units } = window.Dashboard;
  const $ = (id) => document.getElementById(id);
  const dialog = $("calib-dialog");
  const img = $("calib-img");
  const svg = $("calib-svg");
  const widthInput = $("calib-w");
  const lengthInput = $("calib-l");
  const msg = $("calib-msg");
  const saveBtn = $("calib-save");
  const stage = $("calib-stage");
  const loading = $("calib-loading");
  const loadingText = $("calib-loading-text");
  let points = [];
  let dragIndex = -1;
  let options = null;   // of the open() call the dialog is showing
  let frameNo = 0;      // only the latest frame request is shown (cameras can take 15-20 s to answer)
  let frameReady = false;

  function status(text, kind) {
    msg.textContent = text;
    msg.className = "form-msg" + (kind ? " is-" + kind : "");
  }

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }

  function draw() {
    const w = img.naturalWidth || 1;
    const r = Math.max(6, w / 130);
    svg.replaceChildren();
    if (points.length > 1) {
      svg.append(svgEl(points.length === 4 ? "polygon" : "polyline", {
        points: points.map((p) => p.join(",")).join(" "),
        fill: points.length === 4 ? "rgba(59,130,246,0.18)" : "none",
        stroke: "#facc15", "stroke-width": 2, "vector-effect": "non-scaling-stroke",
      }));
    }
    points.forEach(([x, y], i) => {
      const g = svgEl("g", { class: "pt", "data-i": i });
      g.append(svgEl("circle", { cx: x, cy: y, r, fill: "#e5484d", stroke: "#fff", "stroke-width": 2,
        "vector-effect": "non-scaling-stroke" }));
      const label = svgEl("text", { x: x + r * 1.3, y: y - r * 1.3, fill: "#fff", "font-size": r * 2.6,
        "font-weight": 700, stroke: "#000", "stroke-width": 3, "paint-order": "stroke",
        "vector-effect": "non-scaling-stroke" });
      label.textContent = String(i + 1);
      g.append(label);
      svg.append(g);
    });
    $("calib-next").textContent = points.length < 4
      ? interpolate(gettext("Click point %(n)s: %(name)s"), { n: points.length + 1, name: POINT_NAMES[points.length] }, true)
      : "";
    updateSave();
  }

  function updateSave() {
    saveBtn.disabled = !(frameReady && points.length === 4
      && Number(widthInput.value) > 0 && Number(lengthInput.value) > 0);
  }

  // Spinner in the middle of the image area while a frame is on its way; error = why there is none
  function showLoading(on, error = "") {
    loading.hidden = !on && !error;
    loading.querySelector(".loader").hidden = !on;
    loadingText.classList.toggle("is-error", Boolean(error));
    loadingText.textContent = error || gettext("Loading…");
    stage.hidden = on || Boolean(error);
    $("calib-refresh").disabled = on;
  }

  // Pointer position in frame pixels
  function svgPoint(ev) {
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX;
    pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    const clamp = (v, max) => Math.round(Math.max(0, Math.min(max, v)) * 10) / 10;
    return [clamp(p.x, img.naturalWidth), clamp(p.y, img.naturalHeight)];
  }

  // Fetched rather than set as img.src, so the server's reason for not sending one can be shown
  async function loadFrame() {
    const no = ++frameNo;
    frameReady = false;
    updateSave();
    showLoading(true);
    try {
      const res = await fetch(options.frameUrl, { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || gettext("Could not load a frame from the video"));
      }
      const url = URL.createObjectURL(await res.blob());
      if (no !== frameNo) return;
      if (img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
      await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = () => reject(new Error(gettext("Could not load a frame from the video")));
        img.src = url;
      });
      svg.setAttribute("viewBox", `0 0 ${img.naturalWidth} ${img.naturalHeight}`);
      frameReady = true;
      showLoading(false);
      draw();
    } catch (e) {
      if (no === frameNo) showLoading(false, e.message);
    }
  }

  svg.addEventListener("pointerdown", (ev) => {
    const g = ev.target.closest(".pt");
    if (g) {
      dragIndex = Number(g.dataset.i);
      g.classList.add("is-dragging");
      svg.setPointerCapture(ev.pointerId);
    } else if (points.length < 4) {
      points.push(svgPoint(ev));
      draw();
    }
    ev.preventDefault();
  });
  svg.addEventListener("pointermove", (ev) => {
    if (dragIndex < 0) return;
    points[dragIndex] = svgPoint(ev);
    draw();
  });
  const endDrag = () => { dragIndex = -1; };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);

  $("calib-undo").addEventListener("click", () => { points.pop(); draw(); });
  $("calib-clear-pts").addEventListener("click", () => { points = []; draw(); });
  widthInput.addEventListener("input", updateSave);
  lengthInput.addEventListener("input", updateSave);
  $("calib-refresh").addEventListener("click", loadFrame);
  $("calib-cancel").addEventListener("click", () => dialog.close());

  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    status(gettext("Saving…"), "busy");
    try {
      // Typed in the account's units (m or ft); always saved in metres
      await options.save({ points, width_m: units.toMetres(Number(widthInput.value)),
                           length_m: units.toMetres(Number(lengthInput.value)) });
      dialog.close();
      options.onSaved?.();
    } catch (e) {
      status(e.message, "error");
      updateSave();
    }
  });

  /* options: source (label shown in the title), points / width_m / length_m (the saved calibration, or
     null to start empty), frameUrl (JPEG of the current frame), save(body) (returns a promise; rejects
     with the message to show), onSaved(). The dialog opens at once and the frame loads inside it. */
  function open(opts) {
    options = opts;
    status("");
    $("calib-source").textContent = opts.source;
    points = opts.points ? opts.points.map((p) => [p[0], p[1]]) : [];
    widthInput.value = opts.width_m != null ? units.toShown(opts.width_m) : "";
    lengthInput.value = opts.length_m != null ? units.toShown(opts.length_m) : "";
    for (const input of [widthInput, lengthInput]) {
      input.min = units.toShown(MIN_M);
      input.max = units.toShown(MAX_M);
    }
    for (const unit of dialog.querySelectorAll(".calib-unit")) unit.textContent = units.lengthUnit();
    dialog.showModal();
    loadFrame();
  }

  // A frame still on its way when the dialog closes is not shown in the next one
  dialog.addEventListener("close", () => { frameNo++; });

  window.CalibrationEditor = { open };
});
