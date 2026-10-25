"use strict";

// Live line chart of the processing speed under the video (#fps-chart). dashboard.js calls push() with
// each status poll (once a second); null marks a moment with detection off, which breaks the line.
// Exposed as window.FpsChart.
window.FpsChart = (() => {
  const WINDOW_S = 120;   // seconds of history shown
  const HEIGHT = 96;
  const PAD = { top: 10, right: 8, bottom: 18, left: 36 };
  const SVG_NS = "http://www.w3.org/2000/svg";

  const box = document.getElementById("fps-chart");
  const plot = box.querySelector(".fps-plot");
  const tip = box.querySelector(".fps-tip");
  const nowText = document.getElementById("fps-now");
  const avgText = document.getElementById("fps-avg");
  const samples = [];   // {t: ms timestamp, fps: number | null}
  let hoverX = null;    // pointer x in the plot, or null

  function svgEl(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  }

  // Round the top of the y axis up to 5 / 10 / 15 / 30 / 60 fps so the gridlines read cleanly
  function niceMax(v) {
    return [5, 10, 15, 30, 60, 120].find((m) => v <= m) ?? Math.ceil(v / 60) * 60;
  }

  function draw() {
    const width = plot.clientWidth;
    if (!width) return;
    const now = Date.now();
    const values = samples.filter((s) => s.fps != null).map((s) => s.fps);
    const yMax = niceMax(Math.max(1, ...values));
    const x = (t) => PAD.left + (1 - (now - t) / (WINDOW_S * 1000)) * (width - PAD.left - PAD.right);
    const y = (v) => PAD.top + (1 - v / yMax) * (HEIGHT - PAD.top - PAD.bottom);

    const svg = svgEl("svg", { width, height: HEIGHT, viewBox: `0 0 ${width} ${HEIGHT}`, role: "img",
      "aria-label": `Processing speed over the last ${WINDOW_S / 60} minutes` });
    for (const v of [0, yMax / 2, yMax]) {
      svg.append(svgEl("line", { class: "fps-grid", x1: PAD.left, x2: width - PAD.right, y1: y(v), y2: y(v) }));
      svg.append(svgEl("text", { class: "fps-axis", x: PAD.left - 6, y: y(v) + 4, "text-anchor": "end" }, v));
    }
    svg.append(svgEl("text", { class: "fps-axis", x: PAD.left, y: HEIGHT - 4 }, `${WINDOW_S / 60} min ago`));
    svg.append(svgEl("text", { class: "fps-axis", x: width - PAD.right, y: HEIGHT - 4, "text-anchor": "end" }, "now"));

    // One path per unbroken run of samples
    let d = "";
    let inRun = false;
    for (const s of samples) {
      if (s.fps == null) { inRun = false; continue; }
      d += `${inRun ? "L" : "M"}${x(s.t).toFixed(1)},${y(s.fps).toFixed(1)}`;
      inRun = true;
    }
    if (d) svg.append(svgEl("path", { class: "fps-line", d }));

    const last = samples[samples.length - 1];
    if (last && last.fps != null) {
      svg.append(svgEl("circle", { class: "fps-dot", cx: x(last.t), cy: y(last.fps), r: 4 }));
    }

    // Hover: crosshair on the nearest sample, and its value in the tooltip
    tip.hidden = true;
    if (hoverX != null && samples.length) {
      const nearest = samples.reduce((a, b) => (Math.abs(x(b.t) - hoverX) < Math.abs(x(a.t) - hoverX) ? b : a));
      const cx = x(nearest.t);
      svg.append(svgEl("line", { class: "fps-cross", x1: cx, x2: cx, y1: PAD.top, y2: HEIGHT - PAD.bottom }));
      if (nearest.fps != null) svg.append(svgEl("circle", { class: "fps-dot", cx, cy: y(nearest.fps), r: 4 }));
      tip.textContent = `${new Date(nearest.t).toLocaleTimeString()} · `
        + (nearest.fps != null ? `${nearest.fps.toFixed(1)} fps` : "detection off");
      tip.hidden = false;
      tip.style.left = `${plot.offsetLeft + Math.min(Math.max(cx, 60), width - 60)}px`;   // tip sits in the card
    }
    plot.replaceChildren(svg);
  }

  function push(fps) {
    const now = Date.now();
    samples.push({ t: now, fps });
    while (samples.length && samples[0].t < now - WINDOW_S * 1000) samples.shift();
    const values = samples.filter((s) => s.fps != null).map((s) => s.fps);
    nowText.textContent = fps != null ? `${fps.toFixed(1)} fps` : "Detection off";
    avgText.textContent = values.length
      ? `average ${(values.reduce((a, b) => a + b, 0) / values.length).toFixed(1)} fps` : "";
    draw();
  }

  plot.addEventListener("pointermove", (ev) => { hoverX = ev.clientX - plot.getBoundingClientRect().left; draw(); });
  plot.addEventListener("pointerleave", () => { hoverX = null; draw(); });
  new ResizeObserver(draw).observe(plot);

  return { push };
})();
