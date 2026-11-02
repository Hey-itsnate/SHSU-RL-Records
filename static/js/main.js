/* Tiny progressive enhancements: accessible season tabs + mobile menu. */
(() => {
  "use strict";
  const menu = document.getElementById("menu-toggle");
  const sidebar = document.getElementById("site-sidebar");
  if (menu && sidebar) {
    const setMenu = (open) => {
      document.body.classList.toggle("nav-open", open);
      menu.setAttribute("aria-expanded", String(open));
    };
    menu.addEventListener("click", () => setMenu(!document.body.classList.contains("nav-open")));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setMenu(false);
    });
    document.addEventListener("click", (e) => {
      if (document.body.classList.contains("nav-open") &&
          !sidebar.contains(e.target) && !menu.contains(e.target)) setMenu(false);
    });
  }

  const tabs = Array.from(document.querySelectorAll("[data-tab]"));
  const panels = Array.from(document.querySelectorAll("[data-panel]"));
  if (!tabs.length) return;
  const names = tabs.map((tab) => tab.dataset.tab);

  function select(name, updateHash = true, focus = false) {
    if (!names.includes(name)) return;
    for (const tab of tabs) {
      const active = tab.dataset.tab === name;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      if (active && focus) tab.focus();
    }
    for (const panel of panels) panel.hidden = panel.dataset.panel !== name;
    if (updateHash && window.location.hash !== `#${name}`) {
      history.replaceState(null, "", `#${name}`);
    }
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => select(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let target = index;
      if (event.key === "Home") target = 0;
      if (event.key === "End") target = tabs.length - 1;
      if (event.key === "ArrowLeft") target = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "ArrowRight") target = (index + 1) % tabs.length;
      select(tabs[target].dataset.tab, true, true);
    });
  });
  const fromHash = window.location.hash.slice(1);
  select(names.includes(fromHash) ? fromHash : names[0], false);
  window.addEventListener("hashchange", () => {
    const name = window.location.hash.slice(1);
    if (names.includes(name)) select(name, false);
  });
})();