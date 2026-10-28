"use strict";

// Picking a video file's location on a map (#map-dialog, dashboard.html): an OpenStreetMap map drawn with
// Leaflet (vendor/leaflet), a pin where it is clicked, and finding places by name with OpenStreetMap's
// Nominatim. Exposed as window.LocationMap: open(options) and nameOf(latitude, longitude).
window.Dashboard.onPage("#map-dialog", () => {
  const $ = (id) => document.getElementById(id);
  const dialog = $("map-dialog");
  const NOMINATIM = "https://nominatim.openstreetmap.org";
  const THAILAND = { center: [13.75, 100.5], zoom: 6 };   // shown until a location is picked
  const PICKED_ZOOM = 17;
  // OpenStreetMap's servers refuse requests that do not say which site they come from; the pages send no
  // Referer to other sites (Referrer-Policy: same-origin), so these send this site's address, not the page's
  const REFERRER_POLICY = "strict-origin-when-cross-origin";
  let map = null;
  let pin = null;       // the circle on the picked spot
  let picked = null;    // [latitude, longitude] or null
  let options = null;   // of the open() call the dialog is showing

  function showMsg(text, kind) {
    $("map-msg").textContent = text;
    $("map-msg").className = "form-msg" + (kind ? " is-" + kind : "");
  }

  function setPin(latlng) {
    picked = latlng ? [Number(latlng[0].toFixed(6)), Number(latlng[1].toFixed(6))] : null;
    if (pin) pin.remove();
    pin = picked && L.circleMarker(picked, { radius: 9, color: "#fff", weight: 3, fillColor: "#e5484d", fillOpacity: 1 })
      .addTo(map);
    $("map-use").disabled = !picked;
    $("map-picked").textContent = picked ? picked.join(", ") : gettext("Click the map where the video was filmed");
  }

  // Made the first time the dialog opens: Leaflet needs the map's box on screen to size it
  function makeMap() {
    map = L.map("map");
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      referrerPolicy: REFERRER_POLICY,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    map.on("click", (ev) => setPin([ev.latlng.lat, ev.latlng.lng]));
  }

  async function nominatim(path, params) {
    const query = new URLSearchParams({ format: "jsonv2", "accept-language": document.documentElement.lang, ...params });
    const res = await fetch(`${NOMINATIM}/${path}?${query}`, { referrerPolicy: REFERRER_POLICY });
    if (!res.ok) throw new Error(gettext("The map search is not answering"));
    return res.json();
  }

  // A short name for a spot on the map ("Chalong Krung Road, Lat Krabang"), or "" when none is found
  async function nameOf(latitude, longitude) {
    const found = await nominatim("reverse", { lat: latitude, lon: longitude, zoom: 17 });
    const a = found.address || {};
    const area = a.suburb || a.city_district || a.town || a.city || a.county || a.state;
    const parts = [...new Set([found.name || a.road, area].filter(Boolean))];
    return (parts.join(", ") || found.display_name || "").slice(0, 100);
  }

  $("map-search").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const q = $("map-query").value.trim();
    if (!q) return;
    showMsg(gettext("Searching…"), "busy");
    try {
      const [first] = await nominatim("search", { q, limit: 1 });
      if (!first) {
        showMsg(gettext("Nothing found by that name"), "error");
        return;
      }
      map.setView([first.lat, first.lon], PICKED_ZOOM);
      setPin([Number(first.lat), Number(first.lon)]);
      showMsg("");
    } catch (e) {
      showMsg(e.message, "error");
    }
  });

  $("map-use").addEventListener("click", () => {
    dialog.close();
    options.onPick(picked);
  });
  $("map-cancel").addEventListener("click", () => dialog.close());

  /* options: latitude / longitude (the location picked before, or null), onPick([latitude, longitude]) */
  function open(opts) {
    options = opts;
    showMsg("");
    $("map-query").value = "";
    dialog.showModal();
    if (!map) makeMap();
    map.invalidateSize();
    const before = opts.latitude != null ? [opts.latitude, opts.longitude] : null;
    if (before) map.setView(before, PICKED_ZOOM);
    else map.setView(THAILAND.center, THAILAND.zoom);
    setPin(before);
  }

  window.LocationMap = { open, nameOf };
});
