"use strict";

(() => {
  // Search as the user types, once they stop for this long
  const TYPE_DELAY_MS = 300;
  const { api, el, units, thumbCell, plateCell, helmetCell, statusCell } = window.Dashboard;

  const $ = (id) => document.getElementById(id);
  const form = $("search-form");
  const qInput = $("search-q");
  const provinceInput = $("search-province");
  const sourceInput = $("search-source");
  const violationsBox = $("search-violations");
  const rowsEl = $("result-rows");
  const emptyMsg = $("empty-msg");
  const msg = $("search-msg");
  const table = $("results");
  const loadMoreBtn = $("load-more");
  // Violations page: every record is listed while no plate is typed; Admins can select and delete rows
  const browse = table.dataset.browse === "true";
  const canDelete = table.dataset.canDelete === "true";

  let typeTimer = null;
  let requestNo = 0;   // only the answer to the latest request is shown
  const rowsById = new Map();   // result rows as the API sent them, for the details dialog
  const selected = new Set();   // ids of the rows ticked for deleting
  let selecting = false;        // selection mode (Delete was pressed): checkboxes are shown
  let allSelected = false;      // "All": every record of the list, also rows not loaded yet
  let list = null;              // what the table shows: {params, total, nextBefore}, or null

  function showMsg(target, text, kind) {
    target.textContent = text;
    target.className = "form-msg" + (kind ? " is-" + kind : "");
  }

  function reviewTag(r) {
    const kind = r.review === "confirmed" ? "tag-ok" : r.review === "rejected" ? "tag-bad" : "tag-muted";
    return el("span", { class: `tag ${kind}` }, r.review_label);
  }

  function buildRow(r) {
    const tr = el("tr", { class: (r.violation ? "row-bad" : "row-ok") + " is-clickable", tabindex: "0",
      "aria-label": interpolate(gettext("Details of %s"), [r.plate]) });
    tr.dataset.id = r.id;
    if (canDelete) {
      const box = el("input", { type: "checkbox", "data-select": r.id, "aria-label": r.plate ? interpolate(gettext("Select %s"), [r.plate]) : gettext("Select record") });
      box.checked = allSelected || selected.has(r.id);
      if (box.checked) {
        selected.add(r.id);
        tr.classList.add("is-selected");
      }
      tr.append(el("td", { class: "check-col" }, el("span", { class: "check-wrap" }, box)));
    }
    tr.append(
      el("td", { class: "time-cell" }, r.time, el("small", {}, r.date)),
      el("td", {}, thumbCell(r)),
      el("td", { class: "plate" }, plateCell(r)),
      el("td", {}, r.province || "—"),
      el("td", { class: "num" }, r.riders ?? "—"),
      el("td", {}, helmetCell(r)),
      el("td", { class: "num" }, units.speed(r.speed) ?? "—"),
      el("td", {}, statusCell(r)),
      el("td", {}, reviewTag(r)),
      el("td", { class: "muted" }, r.source),
    );
    return tr;
  }

  // append: a further page of the full list ("Load more"), added under the rows already shown
  function showResults(data, append) {
    if (!append) {
      rowsById.clear();
      selected.clear();
      allSelected = false;
      rowsEl.replaceChildren();
    }
    for (const r of data.rows) rowsById.set(r.id, r);
    rowsEl.append(...data.rows.map(buildRow));
    Object.assign(list, { total: data.total, nextBefore: data.next_before });

    const shown = rowsById.size;
    emptyMsg.hidden = shown > 0;
    emptyMsg.textContent = data.query ? interpolate(gettext("No plate looks like “%s”"), [data.query])
      : gettext("No motorcycles recorded");
    loadMoreBtn.hidden = !data.next_before;
    const more = data.total <= shown ? ""
      : " " + interpolate(data.query ? gettext("(best %s shown)") : gettext("(newest %s shown)"), [shown]);
    $("result-title").textContent = !data.total ? gettext("Results")
      : data.query ? interpolate(ngettext("%s motorcycle found", "%s motorcycles found", data.total), [data.total]) + more
      : interpolate(gettext("All %s motorcycles"), [data.total]) + more;
    updateBulk();
  }

  async function fetchPage(append) {
    const params = new URLSearchParams(list.params);
    if (append) params.set("before", list.nextBefore);
    const no = ++requestNo;
    showMsg(msg, append ? gettext("Loading…") : gettext("Searching…"), "busy");
    try {
      const data = await api(`/api/vehicles/search/?${params}`);
      if (no !== requestNo) return;
      showResults(data, append);
      showMsg(msg, "");
    } catch (e) {
      if (no === requestNo) showMsg(msg, e.message, "error");
    }
  }

  async function search() {
    clearTimeout(typeTimer);
    // Several plates may be typed, separated by commas; blank entries ("1กข987, ,") are left out
    const q = qInput.value.split(",").map((plate) => plate.trim()).filter(Boolean).join(", ");
    if (!q && !browse) {
      list = null;
      rowsById.clear();
      rowsEl.replaceChildren();
      emptyMsg.hidden = false;
      emptyMsg.textContent = gettext("Type a plate to search the recorded motorcycles");
      $("result-title").textContent = gettext("Results");
      showMsg(msg, "");
      return;
    }
    const params = new URLSearchParams({ q, province: provinceInput.value, source: sourceInput.value });
    if (violationsBox.checked) params.set("violations_only", "true");
    list = { params, total: 0, nextBefore: null };
    await fetchPage(false);
  }

  function searchSoon() {
    clearTimeout(typeTimer);
    typeTimer = setTimeout(search, TYPE_DELAY_MS);
  }

  // ---------- source menu ----------
  // Nested: "All sources", then each group (live cameras, video files) with its sources under it, shown with
  // the arrow beside the group. Choosing a group lists the records of every source in it. The choice is
  // written into the hidden #search-source, which then sends "change" like the other filters.

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

  form.addEventListener("submit", (ev) => { ev.preventDefault(); search(); });
  qInput.addEventListener("input", searchSoon);
  provinceInput.addEventListener("change", search);
  sourceInput.addEventListener("change", search);
  violationsBox.addEventListener("change", search);
  loadMoreBtn.addEventListener("click", () => fetchPage(true));

  // ---------- select & delete (Admin, violations page) ----------
  // Delete turns on selection mode and hides itself: checkboxes slide in, and All, Cancel and Delete
  // selected take its place. While selecting, clicking a row ticks it instead of opening it.

  const rowBoxes = () => [...rowsEl.querySelectorAll("input[data-select]")];

  function updateBulk() {
    if (!canDelete) return;
    const n = allSelected ? list.total : selected.size;
    $("delete-btn").disabled = !(list && list.total);
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
      const n = allSelected ? list.total : selected.size;
      pending = allSelected ? { all: true } : { ids: [...selected] };
      $("delete-title").textContent = interpolate(allSelected ? gettext("Delete all %s motorcycles in this list?")
        : ngettext("Delete %s selected motorcycle?", "Delete %s selected motorcycles?", n), [n]);
      deleteDialog.returnValue = "";
      deleteDialog.showModal();
    });

    deleteDialog.addEventListener("close", async () => {
      if (deleteDialog.returnValue !== "confirm") return;
      // "All" sends the list's filters, so it removes every record they match, not only the loaded rows
      const body = { ...Object.fromEntries(list.params), ...pending };
      showMsg(msg, gettext("Deleting…"), "busy");
      try {
        const r = await api("/api/vehicles/delete/", { method: "POST", body: JSON.stringify(body) });
        setSelecting(false);
        await search();
        showMsg(msg, interpolate(ngettext("Deleted %s record", "Deleted %s records", r.deleted), [r.deleted]), "ok");
        document.dispatchEvent(new Event("records-changed"));   // stats.js reloads the statistics
      } catch (e) {
        showMsg(msg, e.message, "error");
      }
    });
  }

  // ---------- details & review ----------

  const dialog = $("detail-dialog");
  const detailImg = $("detail-img");
  const noteInput = $("detail-note");
  const detailMsg = $("detail-msg");
  const reviewButtons = [$("review-confirm"), $("review-reject"), $("review-undo")];
  // Only users who may review get the note and the review buttons (see search.html)
  const canReview = reviewButtons.every(Boolean);
  let current = null;   // the row shown in the dialog

  function detailItem(label, value) {
    return [el("dt", {}, label), el("dd", {}, value ?? "—")];
  }

  function fillDetail(r) {
    $("detail-title").textContent = r.plate;
    const tag = $("detail-review");
    tag.replaceWith(Object.assign(reviewTag(r), { id: "detail-review" }));

    detailImg.hidden = !r.snapshot;
    $("detail-nophoto").hidden = r.snapshot;
    if (r.snapshot) detailImg.src = `/snapshot/${r.id}/`;

    const reviewed = r.review !== "pending"
      ? interpolate(gettext("%(review)s by %(user)s · %(when)s"),
                    { review: r.review_label, user: r.reviewed_by || gettext("a deleted user"), when: r.reviewed_at }, true)
      : r.review_label;
    $("detail-list").replaceChildren(
      ...detailItem(gettext("Date & time"), `${r.date} ${r.time}`),
      ...detailItem(gettext("Plate"), r.plate || null),
      ...(r.plate_snapshot ? detailItem(gettext("Plate photo"),
        el("img", { class: "detail-plate", src: `/snapshot/${r.id}/plate/`, alt: interpolate(gettext("Plate %s"), [r.plate]) })) : []),
      ...detailItem(gettext("Province"), r.province || null),
      ...detailItem(gettext("OCR confidence"), r.plate_conf != null ? `${r.plate_conf}%` : null),
      ...detailItem(gettext("Riders"), r.riders),
      ...detailItem(gettext("Helmet"), helmetCell(r)),
      ...detailItem(gettext("Speed"), units.speed(r.speed)),
      ...detailItem(gettext("Status"), statusCell(r)),
      ...detailItem(gettext("Source"), r.source),
      ...detailItem(gettext("Track ID"), r.track_id),
      ...detailItem(gettext("Review"), reviewed),
      ...(r.review_note ? detailItem(gettext("Note"), r.review_note) : []),
    );

    if (!canReview) return;
    noteInput.value = r.review_note;
    $("review-confirm").hidden = r.review === "confirmed";
    $("review-reject").hidden = r.review === "rejected";
    $("review-undo").hidden = r.review === "pending";
  }

  function openDetail(id) {
    current = rowsById.get(id);
    if (!current) return;
    fillDetail(current);
    if (canReview) showMsg(detailMsg, "");
    dialog.showModal();
  }

  async function setReview(review) {
    reviewButtons.forEach((b) => { b.disabled = true; });
    showMsg(detailMsg, gettext("Saving…"), "busy");
    try {
      const r = await api(`/api/vehicles/${current.id}/review/`, {
        method: "POST",
        body: JSON.stringify({ review, note: noteInput.value.trim() }),
      });
      current = r;
      rowsById.set(r.id, r);
      rowsEl.querySelector(`tr[data-id="${r.id}"]`)?.replaceWith(buildRow(r));
      fillDetail(r);
      showMsg(detailMsg, review === "pending" ? gettext("Review undone") : interpolate(gettext("Saved: %s"), [r.review_label]), "ok");
    } catch (e) {
      showMsg(detailMsg, e.message, "error");
    } finally {
      reviewButtons.forEach((b) => { b.disabled = false; });
    }
  }

  rowsEl.addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-id]");
    if (!tr) return;
    // While selecting, a click anywhere on the row ticks it instead of opening it
    if (selecting) {
      if (ev.target.tagName !== "INPUT") tr.querySelector("input[data-select]").click();
      return;
    }
    openDetail(Number(tr.dataset.id));
  });
  rowsEl.addEventListener("keydown", (ev) => {
    if (ev.target.tagName === "INPUT") return;   // Space ticks a checkbox
    const tr = ev.target.closest("tr[data-id]");
    if (tr && (ev.key === "Enter" || ev.key === " ")) {
      ev.preventDefault();
      if (selecting) tr.querySelector("input[data-select]").click();
      else openDetail(Number(tr.dataset.id));
    }
  });
  if (canReview) {
    $("review-confirm").addEventListener("click", () => setReview("confirmed"));
    $("review-reject").addEventListener("click", () => setReview("rejected"));
    $("review-undo").addEventListener("click", () => setReview("pending"));
  }
  $("detail-close").addEventListener("click", () => dialog.close());

  // A plate typed in the top bar's search box arrives as ?q=; the violations page opens on the full list
  const startQuery = new URLSearchParams(location.search).get("q");
  if (startQuery) qInput.value = startQuery.trim();
  if (browse || qInput.value) search();
})();
