/* Collection source fields (templates/_source_fields.html), in Settings and the
   setup wizard: shows the Apify fields only when Apify is picked, and "Test token"
   asks the server to check the token (no Actor run, nothing charged). Strings go
   through _() from static/js/i18n.js. */
(function () {
  "use strict";
  var pick = document.querySelector("[data-source-pick]");
  if (!pick) return;
  var fields = pick.querySelector("[data-apify-fields]");
  var button = pick.querySelector("[data-apify-test]");
  var result = pick.querySelector("[data-apify-result]");
  var token = pick.querySelector("[name=apify_token]");

  var ERRORS = {
    missing: N_("Type a token first."),
    unauthorized: N_("Apify didn't accept this token."),
    unreachable: N_("Couldn't reach Apify. Try again in a moment."),
  };

  pick.addEventListener("change", function (e) {
    if (e.target.name === "collect_source") fields.hidden = e.target.value !== "apify";
  });

  button.addEventListener("click", function () {
    var body = new FormData();
    body.append("apify_token", token.value);
    button.disabled = true;
    result.textContent = _("Checking…") + " ";
    fetch("/admin/apify/test", { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        result.textContent = (data.ok
          ? _("Token works: Apify account %(user)s.", { user: data.username })
          : _(ERRORS[data.error] || ERRORS.unreachable)) + " ";
        result.dataset.ok = data.ok ? "1" : "0";
      })
      .catch(function () { result.textContent = _(ERRORS.unreachable) + " "; result.dataset.ok = "0"; })
      .then(function () { button.disabled = false; });
  });
})();
