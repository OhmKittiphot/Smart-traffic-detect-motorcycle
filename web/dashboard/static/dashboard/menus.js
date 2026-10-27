"use strict";

// The source menus of the live view and the search pages, as Alpine.js components. vendor/alpine-csp.min.js is
// Alpine's build for pages with a CSP: the markup only names the properties and methods defined here, never
// JavaScript of its own.
//
// <div x-data="sourceMenu" @click.outside="close" @keydown.escape="escape">: a button (x-ref="button") that
//   opens the list, and items (.source-item, @click="choose", data-label) to choose one. Choosing one marks it,
//   shows its label on the button and sends a "source-chosen" event with its data-mode / data-value, which the
//   page acts on. The item marked aria-pressed="true" in the markup starts chosen, or else the first one of
//   the group that starts open.
// <div x-data="sourceGroup" data-expanded="true|false">: a kind of source whose items open under its button.
document.addEventListener("alpine:init", () => {
  Alpine.data("sourceMenu", () => ({
    open: false,
    label: "",

    init() {
      const item = this.$el.querySelector('.source-item[aria-pressed="true"]')
        ?? this.$el.querySelector('[data-expanded="true"] .source-item');
      if (item) this.mark(item);
    },

    get closed() {
      return !this.open;
    },

    toggleList() {
      this.open = !this.open;
    },

    close() {
      this.open = false;
    },

    escape() {
      if (!this.open) return;
      this.open = false;
      this.$refs.button.focus();
    },

    mark(item) {
      // Not this.$root: for an item inside a sourceGroup, that is the group
      const menu = item.closest('[x-data="sourceMenu"]');
      for (const i of menu.querySelectorAll(".source-item")) i.setAttribute("aria-pressed", String(i === item));
      this.label = item.dataset.label;
    },

    choose(ev) {
      const item = ev.currentTarget;
      this.mark(item);
      this.escape();
      this.$dispatch("source-chosen", { mode: item.dataset.mode, value: item.dataset.value });
    },
  }));

  Alpine.data("sourceGroup", () => ({
    expanded: false,

    init() {
      this.expanded = this.$el.dataset.expanded === "true";
    },

    get collapsed() {
      return !this.expanded;
    },

    toggleGroup() {
      this.expanded = !this.expanded;
    },
  }));
});
