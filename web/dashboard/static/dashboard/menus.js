"use strict";

// The source menus of the live view and the search pages, as Alpine.js components. vendor/alpine-csp.min.js is
// Alpine's build for pages with a CSP: the markup only names the properties and methods defined here, never
// JavaScript of its own.
//
// <div x-data="sourceMenu">: a button (.source-btn) that opens the list (.source-list), and items (.source-item,
//   @click="choose", data-label) to choose one. The list is a popover (popovertarget on the button): the
//   browser opens and closes it, also on Esc or a click outside. Choosing an item marks it, shows its label on
//   the button, closes the list and sends a "source-chosen" event with its data-mode / data-value, which the
//   page acts on. The item marked aria-pressed="true" in the markup starts chosen, or else the first one of
//   the group that starts open.
// <div x-data="sourceGroup" data-expanded="true|false">: a kind of source whose items open under its button. It
//   also starts open when one of its items starts chosen, so the list opens on the chosen source.
document.addEventListener("alpine:init", () => {
  Alpine.data("sourceMenu", () => ({
    label: "",

    init() {
      const item = this.$el.querySelector('.source-item[aria-pressed="true"]')
        ?? this.$el.querySelector('[data-expanded="true"] .source-item');
      if (item) this.mark(item);
    },

    mark(item) {
      for (const i of this.menuOf(item).querySelectorAll(".source-item")) {
        i.setAttribute("aria-pressed", String(i === item));
      }
      this.label = item.dataset.label;
    },

    // Not this.$root: for an item inside a sourceGroup, that is the group
    menuOf(item) {
      return item.closest('[x-data="sourceMenu"]');
    },

    choose(ev) {
      const item = ev.currentTarget;
      const menu = this.menuOf(item);
      this.mark(item);
      menu.querySelector(".source-list").hidePopover();
      menu.querySelector(".source-btn").focus();
      this.$dispatch("source-chosen", { mode: item.dataset.mode, value: item.dataset.value });
    },
  }));

  Alpine.data("sourceGroup", () => ({
    expanded: false,

    init() {
      this.expanded = this.$el.dataset.expanded === "true"
        || Boolean(this.$el.querySelector('.source-children .source-item[aria-pressed="true"]'));
    },

    get collapsed() {
      return !this.expanded;
    },

    toggleGroup() {
      this.expanded = !this.expanded;
    },
  }));
});
