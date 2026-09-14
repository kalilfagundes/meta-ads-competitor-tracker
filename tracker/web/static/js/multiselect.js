/* The Company filter (templates/_company_filter.html): a <details> dropdown of
   checkboxes whose summary names what's ticked ("All", a company, "3 companies").
   It closes on a click outside it or Esc. With data-submit, its form is submitted
   when it closes with a different pick; otherwise the page's script listens for
   "change" on it (each checkbox's bubbles up; "All companies" sends one). Strings
   go through _() / ngettext() from static/js/i18n.js. */
(function () {
  "use strict";

  function boxes(ms) { return Array.prototype.slice.call(ms.querySelectorAll("input[type=checkbox]")); }
  function ticked(ms) { return boxes(ms).filter(function (b) { return b.checked; }); }
  function pick(ms) { return ticked(ms).map(function (b) { return b.value; }).join("\n"); }

  function relabel(ms) {
    var on = ticked(ms);
    ms.querySelector("[data-ms-label]").textContent = !on.length ? _("All")
      : on.length === 1 ? on[0].closest("label").textContent.trim()
      : ngettext("%(num)s company", "%(num)s companies", on.length);
  }

  function wire(ms) {
    var opened = null;  // the pick when it opened, to submit only on a change
    ms.addEventListener("change", function () { relabel(ms); });
    var all = ms.querySelector("[data-ms-all]");
    if (all) {
      all.addEventListener("click", function () {
        boxes(ms).forEach(function (b) { b.checked = false; });
        ms.dispatchEvent(new Event("change", { bubbles: true }));
        ms.open = false;
      });
    }
    ms.addEventListener("toggle", function () {
      if (!ms.hasAttribute("data-submit")) return;
      if (ms.open) { opened = pick(ms); return; }
      var form = ms.closest("form");
      if (opened !== null && opened !== pick(ms) && form) {
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      }
      opened = null;
    });
  }

  function openOnes() { return document.querySelectorAll("details[data-multisel][open]"); }

  document.querySelectorAll("details[data-multisel]").forEach(wire);
  document.addEventListener("click", function (e) {
    openOnes().forEach(function (ms) { if (!ms.contains(e.target)) ms.open = false; });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    openOnes().forEach(function (ms) { ms.open = false; ms.querySelector("summary").focus(); });
  });
})();
