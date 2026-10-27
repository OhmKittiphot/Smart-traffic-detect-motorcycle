"use strict";

// Live line chart of the processing speed under the video (#fps-chart), drawn with uPlot (vendor/). dashboard.js
// calls push() with each status event (once a second); null marks a moment with detection off, which breaks
// the line. While a video file is paused the chart's clock stops: nothing is added, nothing scrolls, and the
// paused time is left out of the timeline, so the line goes on where it stopped. Exposed as window.FpsChart
// while the live view is shown.
window.Dashboard.onPage("#fps-chart", (page) => {
  const WINDOW_S = 120;   // seconds of history shown
  const HEIGHT = 96;

  const box = document.getElementById("fps-chart");
  const plot = box.querySelector(".fps-plot");
  const tip = box.querySelector(".fps-tip");
  const nowText = document.getElementById("fps-now");
  const avgText = document.getElementById("fps-avg");
  const samples = [];   // {t: ms on the chart's clock, wall: real time, fps: number | null}
  let pausedTotal = 0;  // ms the video has been paused so far, taken off the wall clock
  let pausedAt = null;  // wall time the current pause began, or null while playing
  let chart = null;

  // The chart's clock: wall time minus every pause, standing still during a pause
  const clock = () => (pausedAt ?? Date.now()) - pausedTotal;

  // Round the top of the y axis up to 5 / 10 / 15 / 30 / 60 fps so the gridlines read cleanly
  function niceMax(v) {
    return [5, 10, 15, 30, 60, 120].find((m) => v <= m) ?? Math.ceil(v / 60) * 60;
  }

  // x: seconds before now on the chart's clock, so the line scrolls left as time goes on
  function chartData() {
    const now = clock();
    return [samples.map((s) => (s.t - now) / 1000), samples.map((s) => s.fps)];
  }

  // The nearest sample under the pointer, in the tooltip
  function showTip(u) {
    const i = u.cursor.idx;
    tip.hidden = i == null;
    if (i == null) return;
    const s = samples[i];
    tip.textContent = `${new Date(s.wall).toLocaleTimeString(document.documentElement.lang)} · `
      + (s.fps != null ? `${s.fps.toFixed(1)} fps` : gettext(gettext("detection off")));
    const cx = u.valToPos(u.data[0][i], "x");
    tip.style.left = `${plot.offsetLeft + Math.min(Math.max(cx, 60), plot.clientWidth - 60)}px`;   // stays in the card
  }

  // uPlot draws on a canvas, so the theme's colours are read from the CSS and the chart is made again
  // when the theme changes
  function makeChart() {
    const css = getComputedStyle(box);
    const color = (name) => css.getPropertyValue(name).trim();
    const axis = { stroke: color("--muted"), font: `10px ${color("--font-small")}`, ticks: { show: false },
                   grid: { stroke: color("--border"), width: 1 } };
    chart?.destroy();
    chart = new uPlot({
      width: plot.clientWidth,
      height: HEIGHT,
      legend: { show: false },
      scales: {
        x: { time: false, range: [-WINDOW_S, 0] },
        y: { range: (u, min, max) => [0, niceMax(Math.max(1, max ?? 1))] },
      },
      axes: [
        { ...axis, size: 18, grid: { show: false }, splits: () => [-WINDOW_S, 0],
          values: (u, splits) => splits.map((v) => (v === 0 ? gettext("now")
            : interpolate(gettext("%s min ago"), [WINDOW_S / 60]))) },
        { ...axis, size: 36, splits: (u) => [0, u.scales.y.max / 2, u.scales.y.max] },
      ],
      series: [{}, {
        stroke: color("--accent"),
        width: 2,
        // Only the newest point gets a dot
        points: { size: 8, fill: color("--accent"), stroke: color("--panel"),
                  filter: (u) => (u.data[1].at(-1) != null ? [u.data[1].length - 1] : null) },
      }],
      cursor: { y: false, points: { size: 8, fill: color("--accent") } },
      hooks: { setCursor: [showTip] },
    }, chartData(), plot);
    plot.querySelector(".u-wrap").setAttribute("aria-label",
      interpolate(gettext("Processing speed over the last %s minutes"), [WINDOW_S / 60]));
  }

  // paused: the video file is paused (the chart then stands still and shows "Paused")
  function push(fps, paused = false) {
    if (paused) {
      if (pausedAt == null) pausedAt = Date.now();
      nowText.textContent = gettext("Paused");
      return;
    }
    if (pausedAt != null) {
      pausedTotal += Date.now() - pausedAt;
      pausedAt = null;
    }
    const now = clock();
    samples.push({ t: now, wall: Date.now(), fps });
    while (samples.length && samples[0].t < now - WINDOW_S * 1000) samples.shift();
    const values = samples.filter((s) => s.fps != null).map((s) => s.fps);
    nowText.textContent = fps != null ? `${fps.toFixed(1)} fps` : gettext("Detection off");
    avgText.textContent = values.length
      ? interpolate(gettext("average %s fps"), [(values.reduce((a, b) => a + b, 0) / values.length).toFixed(1)]) : "";
    chart?.setData(chartData());
  }

  makeChart();
  const resized = new ResizeObserver(() => { if (plot.clientWidth) chart.setSize({ width: plot.clientWidth, height: HEIGHT }); });
  resized.observe(plot);
  const themed = new MutationObserver(makeChart);
  themed.observe(document.documentElement, { attributeFilter: ["data-theme"] });
  page.onLeave(() => {
    resized.disconnect();
    themed.disconnect();
    chart.destroy();
  });

  window.FpsChart = { push };
});
