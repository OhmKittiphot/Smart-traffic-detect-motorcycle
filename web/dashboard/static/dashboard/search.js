"use strict";

// The records list of the plate search and violations pages. htmx sends the search form and puts the rows the
// server renders into the table (_plate_search.html, _vehicle_rows.html); this script adds the source menu,
// opening a row's details, and selecting rows to delete them.
(() => {
  const { api } = window.Dashboard;

  const $ = (id) => document.getElementById(id);
  const form = $("search-form");
  const sourceInput = $("search-source");
  const rowsEl = $("result-rows");
  const table = $("results");
  // Violations page: Admins can select and delete rows
  const canDelete = table.dataset.canDelete === "true";

  // #search-msg and #result-title are replaced with each answer, so they are looked up when needed
  function showMsg(text, kind) {
    const msg = $("search-msg");
    msg.textContent = text;
    msg.className = "form-msg" + (kind ? " is-" + kind : "");
  }

  // The server could not answer (a lost session already went to the login page, see common.js)
  document.addEventListener("htmx:responseError", (ev) => {
    const status = ev.detail.xhr.status;
    if (status !== 401 && status !== 403) showMsg(`HTTP ${status}`, "error");
  });

  // ---------- source menu ----------
  // Nested: "All sources", then each group (live cameras, video files) with its sources under it, shown with
  // the arrow beside the group. Choosing a group lists the records of every source in it. The choice is
  // written into the hidden #search-source, whose "change" makes htmx search again.

  const sourceMenu = $("source-menu");
  const sourceBtn = sourceMenu.querySelector(".source-btn");
  const sourceList = $("source-list");

  function openSourceMenu(open) {
    sourceList.hidden = !open;
    sourceBtn.setAttribute("aria-expanded", String(open));
  }

  function expandGroup(group, open) {
    group.querySelector(".source-children").hidden = !open;
    group.querySelector(".source-toggle").setAttribute("aria-expanded", String(open));
  }

  sourceBtn.addEventListener("click", () => openSourceMenu(sourceList.hidden));
  sourceList.addEventListener("click", (ev) => {
    const toggle = ev.target.closest(".source-toggle");
    if (toggle) {
      const group = toggle.closest(".source-group");
      expandGroup(group, group.querySelector(".source-children").hidden);
      return;
    }
    const item = ev.target.closest(".source-item");
    if (!item) return;
    for (const i of sourceList.querySelectorAll(".source-item")) i.setAttribute("aria-pressed", String(i === item));
    sourceBtn.querySelector(".source-btn-label").textContent = item.dataset.label;
    openSourceMenu(false);
    sourceBtn.focus();
    if (sourceInput.value !== item.dataset.value) {
      sourceInput.value = item.dataset.value;
      sourceInput.dispatchEvent(new Event("change"));
    }
  });
  document.addEventListener("click", (ev) => { if (!sourceMenu.contains(ev.target)) openSourceMenu(false); });
  sourceMenu.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !sourceList.hidden) {
      openSourceMenu(false);
      sourceBtn.focus();
    }
  });

  // ---------- select & delete (Admin, violations page) ----------
  // Delete turns on selection mode and hides itself: checkboxes slide in, and All, Cancel and Delete
  // selected take its place. While selecting, clicking a row ticks it instead of opening it.

  const selected = new Set();   // ids of the rows ticked for deleting
  let selecting = false;        // selection mode (Delete was pressed): checkboxes are shown
  let allSelected = false;      // "All": every record of the list, also rows not loaded yet

  const rowBoxes = () => [...rowsEl.querySelectorAll("input[data-select]")];
  const listTotal = () => Number($("result-title").dataset.total || 0);

  function updateBulk() {
    if (!canDelete) return;
    const n = allSelected ? listTotal() : selected.size;
    $("delete-btn").disabled = !listTotal();
    $("delete-confirm").disabled = !n;
    $("delete-confirm").textContent = allSelected ? interpolate(gettext("Delete all (%s)"), [n])
      : n ? interpolate(gettext("Delete selected (%s)"), [n]) : gettext("Delete selected");
    $("select-all-btn").setAttribute("aria-pressed", String(allSelected));
  }

  function tick(box, on) {
    box.checked = on;
    box.closest("tr").classList.toggle("is-selected", on);
    const id = Number(box.dataset.select);
    if (on) selected.add(id); else selected.delete(id);
  }

  function setSelecting(on) {
    selecting = on;
    if (!on) {
      allSelected = false;
      for (const box of rowBoxes()) tick(box, false);
    }
    table.classList.toggle("is-selecting", on);
    $("bulk-actions").classList.toggle("is-selecting", on);
    $("bulk-extra").inert = !on;
    $("delete-btn").inert = on;
    // Keep keyboard focus on a visible button
    (on ? $("select-all-btn") : $("delete-btn")).focus({ preventScroll: true });
    updateBulk();
  }

  if (canDelete) {
    const deleteDialog = $("delete-dialog");
    let pending = null;   // {all: true} or {ids: [...]}: what the open dialog would delete

    // Rows arrived: a new search starts a new list with nothing selected; under "All", the rows of a
    // further page ("Load more") come in ticked
    document.addEventListener("htmx:afterRequest", (ev) => {
      if (ev.detail.target !== rowsEl) return;
      if (ev.detail.elt === form) {
        selected.clear();
        allSelected = false;
      }
      for (const box of rowBoxes()) tick(box, allSelected || selected.has(Number(box.dataset.select)));
      updateBulk();
    });

    rowsEl.addEventListener("change", (ev) => {
      const box = ev.target.closest("input[data-select]");
      if (!box) return;
      tick(box, box.checked);
      if (!box.checked) allSelected = false;   // one row left out: no longer the whole list
      updateBulk();
    });
    $("select-all-btn").addEventListener("click", () => {
      allSelected = !allSelected;
      for (const box of rowBoxes()) tick(box, allSelected);
      updateBulk();
    });
    $("select-cancel").addEventListener("click", () => setSelecting(false));
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && selecting && !document.querySelector("dialog[open]")) setSelecting(false);
    });

    $("delete-btn").addEventListener("click", () => setSelecting(true));
    $("delete-confirm").addEventListener("click", () => {
      const n = allSelected ? listTotal() : selected.size;
      pending = allSelected ? { all: true } : { ids: [...selected] };
      $("delete-title").textContent = interpolate(allSelected ? gettext("Delete all %s motorcycles in this list?")
        : ngettext("Delete %s selected motorcycle?", "Delete %s selected motorcycles?", n), [n]);
      deleteDialog.returnValue = "";
      deleteDialog.showModal();
    });

    deleteDialog.addEventListener("close", async () => {
      if (deleteDialog.returnValue !== "confirm") return;
      // "All" sends the list's filters, so it removes every record they match, not only the loaded rows
      const params = $("result-title").dataset.params;
      const body = { ...Object.fromEntries(new URLSearchParams(params)), ...pending };
      showMsg(gettext("Deleting…"), "busy");
      try {
        const r = await api("/api/vehicles/delete/", { method: "POST", body: JSON.stringify(body) });
        setSelecting(false);
        await htmx.ajax("GET", `${form.getAttribute("hx-get")}?${params}`, { target: rowsEl });
        showMsg(interpolate(ngettext("Deleted %s record", "Deleted %s records", r.deleted), [r.deleted]), "ok");
        document.dispatchEvent(new Event("records-changed"));   // stats.js reloads the statistics
      } catch (e) {
        showMsg(e.message, "error");
      }
    });
  }

  // ---------- details & review ----------
  // A row asks htmx for its details pop-up (_vehicle_detail.html), whose review buttons are htmx too

  const dialog = $("detail-dialog");

  function openRow(tr) {
    // While selecting, a click anywhere on the row ticks it instead of opening it
    if (selecting) tr.querySelector("input[data-select]").click();
    else htmx.trigger(tr, "open-detail");
  }

  rowsEl.addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[hx-get]");
    if (tr && ev.target.tagName !== "INPUT") openRow(tr);
  });
  rowsEl.addEventListener("keydown", (ev) => {
    if (ev.target.tagName === "INPUT") return;   // Space ticks a checkbox
    const tr = ev.target.closest("tr[hx-get]");
    if (tr && (ev.key === "Enter" || ev.key === " ")) {
      ev.preventDefault();
      openRow(tr);
    }
  });
  // The details arrived: show them (a failed request leaves the pop-up closed)
  rowsEl.addEventListener("htmx:afterRequest", (ev) => {
    if (ev.detail.target === dialog && ev.detail.successful && !dialog.open) dialog.showModal();
  });
  dialog.addEventListener("click", (ev) => { if (ev.target.closest("[data-close]")) dialog.close(); });
})();
