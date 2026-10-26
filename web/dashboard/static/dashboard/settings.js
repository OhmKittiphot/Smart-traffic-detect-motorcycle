"use strict";

// Settings pop-up of the top bar (_settings_panel.html): each .settings-form is sent as JSON to its data-api,
// and the answer shown under it. Number inputs are sent as numbers, checkboxes as true / false.
// Forms marked data-autosave save on every change. Saved preferences are announced with a
// "settings:preferences" event, so the live view and the notifications (notify.js) apply them at once.
(() => {
  const panel = document.getElementById("settings-panel");
  if (!panel) return;
  // common.js is loaded after the top bar, so window.Dashboard is looked up when a form is sent
  const api = (...args) => window.Dashboard.api(...args);

  function showMsg(form, text, kind) {
    const msg = form.querySelector(".form-msg");
    msg.textContent = text;
    msg.className = "form-msg" + (kind ? " is-" + kind : "");
  }

  function formData(form) {
    const data = {};
    for (const input of form.querySelectorAll("input[name], select[name]")) {
      if (input.type === "checkbox") data[input.name] = input.checked;
      else if (input.type === "number") data[input.name] = input.value === "" ? null : Number(input.value);
      else data[input.name] = input.value;
    }
    return data;
  }

  async function send(form) {
    const buttons = form.querySelectorAll("button");
    buttons.forEach((b) => { b.disabled = true; });
    showMsg(form, gettext(gettext("Saving…")), "busy");
    try {
      const saved = await api(form.dataset.api, { method: "POST", body: JSON.stringify(formData(form)) });
      if ("reset" in form.dataset) form.reset();   // never leave passwords in the fields
      if ("autosave" in form.dataset) {
        // The page's texts come from the server: a new language shows after a reload
        if (saved.language && saved.language !== document.documentElement.lang) {
          location.reload();
          return;
        }
        document.dispatchEvent(new CustomEvent("settings:preferences", { detail: saved }));
      }
      showMsg(form, form.dataset.ok, "ok");
    } catch (e) {
      showMsg(form, e.message, "error");
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  for (const form of panel.querySelectorAll(".settings-form")) {
    form.addEventListener("submit", (ev) => { ev.preventDefault(); send(form); });
    // Fields without a name (the theme) are not sent; they are saved by their own script
    if ("autosave" in form.dataset) form.addEventListener("change", (ev) => { if (ev.target.name) send(form); });
  }

  // Admin: apply the retention limits now (the saved ones, so unsaved changes are not used)
  const cleanupBtn = document.getElementById("cleanup-btn");
  if (cleanupBtn) {
    const form = cleanupBtn.closest("form");
    cleanupBtn.addEventListener("click", async () => {
      cleanupBtn.disabled = true;
      showMsg(form, gettext(gettext("Cleaning up…")), "busy");
      try {
        const r = await api(cleanupBtn.dataset.api, { method: "POST", body: "{}" });
        const mb = (r.bytes_freed / 1024 / 1024).toFixed(1);
        showMsg(form, interpolate(gettext("Removed %(records)s old records and the photos of %(photos)s (%(mb)s MB)"),
          { records: r.records_deleted, photos: r.photos_removed, mb }, true), "ok");
      } catch (e) {
        showMsg(form, e.message, "error");
      } finally {
        cleanupBtn.disabled = false;
      }
    });
  }
})();
