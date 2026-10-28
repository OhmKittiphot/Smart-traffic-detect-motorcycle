"use strict";

// Light / dark theme. Loaded in <head> without defer so the theme is set before the page is drawn.
// The choice ("auto", "light" or "dark") is made with the button in the Settings pop-up (#theme-btn) and is a
// per-browser preference kept in localStorage; "auto" follows the clock: light from 06:00 to 18:00, dark otherwise.
(() => {
  const KEY = "cctv-theme";
  const CHOICES = ["auto", "light", "dark"];
  const LABELS = { auto: gettext("Auto"), light: gettext("Light"), dark: gettext("Dark") };
  const DAY_START = 6;
  const DAY_END = 18;
  const FADE_MS = 400;
  const root = document.documentElement;
  let ready = false;     // no fade while the page is loading
  let fadeTimer = null;

  function readPref() {
    try {
      const v = localStorage.getItem(KEY);
      return CHOICES.includes(v) ? v : "auto";
    } catch (e) {
      return "auto";   // storage blocked (private mode, policy): just follow the clock
    }
  }

  function savePref(pref) {
    try { localStorage.setItem(KEY, pref); } catch (e) { /* not remembered, still applied */ }
  }

  function resolve(pref) {
    if (pref !== "auto") return pref;
    const h = new Date().getHours();
    return h >= DAY_START && h < DAY_END ? "light" : "dark";
  }

  function setTheme(pref, theme) {
    root.dataset.themePref = pref;
    root.dataset.theme = theme;
    updateButton(pref);
  }

  // Fade between themes. The View Transitions API cross-fades a snapshot of the whole page in one
  // layer, so nothing can snap to its final colour when the fade ends. Older browsers fade each
  // element with CSS transitions instead; the class stays long enough for every element to finish.
  function fadeTo(pref, theme) {
    if (!ready || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setTheme(pref, theme);
    } else if (document.startViewTransition) {
      document.startViewTransition(() => setTheme(pref, theme));
    } else {
      root.classList.add("theme-fading");
      setTheme(pref, theme);
      clearTimeout(fadeTimer);
      fadeTimer = setTimeout(() => root.classList.remove("theme-fading"), FADE_MS * 2 + 200);
    }
  }

  function apply(pref) {
    const theme = resolve(pref);
    if (theme !== root.dataset.theme) fadeTo(pref, theme);
    else setTheme(pref, theme);
  }

  function updateButton(pref) {
    const btn = document.getElementById("theme-btn");
    if (btn) {
      btn.querySelector(".theme-label").textContent = LABELS[pref];
      btn.title = pref === "auto"
        ? interpolate(gettext("Theme: Auto (light %(start)s:00–%(end)s:00, now %(now)s). Click to change."),
                      { start: DAY_START, end: DAY_END, now: LABELS[root.dataset.theme] }, true)
        : interpolate(gettext("Theme: %s. Click to change."), [LABELS[pref]]);
    }
  }

  apply(readPref());

  // Auto switches at 06:00 / 18:00 without a reload
  setInterval(() => { if (readPref() === "auto") apply("auto"); }, 60 * 1000);

  document.addEventListener("DOMContentLoaded", () => {
    apply(readPref());
    ready = true;
  });
  // A page swapped in by a boosted link (common.js) has a new button, labelled by the server
  document.addEventListener("htmx:afterSettle", () => updateButton(readPref()));

  // The button is in the Settings pop-up (the login page has none)
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest("#theme-btn")) return;
    const next = CHOICES[(CHOICES.indexOf(readPref()) + 1) % CHOICES.length];
    savePref(next);
    apply(next);
  });
})();
