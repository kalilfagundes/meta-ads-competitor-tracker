/* Translations for the browser scripts.
   Reads window.I18N_CATALOG (served by /i18n/<lang>.js, see tracker/i18n.py) and
   exposes the same calls the templates use:
     _("Page %(page)s of %(pages)s", {page: 1, pages: 3})
     ngettext("%(num)s error", "%(num)s errors", n)  // n is passed as %(num)s
     N_("…")                                          // mark only, translate later
   A string missing from the catalog falls back to English. */
(function () {
  "use strict";
  var CATALOG = window.I18N_CATALOG || {};

  function format(s, params) {
    return String(s).replace(/%\((\w+)\)s|%%/g, function (m, key) {
      if (!key) return "%";
      return params && params[key] != null ? params[key] : "";
    });
  }

  window._ = function (message, params) {
    var v = CATALOG[message];
    return format(typeof v === "string" ? v : message, params);
  };

  window.ngettext = function (singular, plural, n, params) {
    var v = CATALOG[singular], s;
    if (Array.isArray(v)) s = n === 1 ? v[0] : v[v.length - 1];
    else s = n === 1 ? singular : plural;
    var p = { num: n };
    for (var k in params || {}) p[k] = params[k];
    return format(s, p);
  };

  window.N_ = function (message) { return message; };

  window.I18N_LANG = document.documentElement.lang || "en";
})();
