/* Settings screen: live collection status (polled), "Collect now",
   inline edit of companies/pages, and delete confirms. Reads window.__RUN_STATUS__
   from admin.html; strings go through _() /
   ngettext() from static/js/i18n.js. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var TRIGGERS = { manual: _("Manual"), schedule: _("Scheduled"), startup: _("Worker start") };
  var BUTTON_LABELS = { queued: _("Starting…"), running: _("Collecting…") };

  // What the worker reports as its current step (tracker/worker/scheduler.py), in
  // English; a Page name is shown as it is.
  var STEPS = [N_("Reading ads on Apify"), N_("Saving ads"), N_("Saving images and videos"),
               N_("Linking ads to landing pages"), N_("Fetching landing-page titles"), N_("Finishing"),
               // a run through Apify whose worker stopped, and the next worker taking it on
               // (tracker/worker/persist.py)
               N_("Paused until the worker is back"), N_("Resuming the Apify runs")];
  // Why the worker closed a run early (tracker/worker/persist.py), in English.
  var RUN_ERRORS = [N_("Interrupted: the worker stopped before this run finished."),
                    N_("Interrupted: the worker was stopped (restart or deploy) before this run finished.")];
  // A run's error in the interface language where the worker wrote it: one of
  // RUN_ERRORS, or "<a step>: <what went wrong>". The rest (Meta's, Apify's, a
  // Page's) is shown as it came.
  function errorLabel(msg) {
    if (!msg) return "";
    if (RUN_ERRORS.indexOf(msg) !== -1) return _(msg);
    var cut = msg.indexOf(": ");
    if (cut > 0 && STEPS.indexOf(msg.slice(0, cut)) !== -1) return _(msg.slice(0, cut)) + msg.slice(cut);
    return msg;
  }
  function stepLabel(step) {
    if (!step) return "";
    // "Saving ads (40/173)": done of total. "Reading ads on Apify (83)": a count of
    // ads read so far, shown as "(83 read)" so it isn't mistaken for a total.
    var m = /^(.*) \((\d+)(?:\/(\d+))?\)$/.exec(step);
    var base = m ? m[1] : step;
    if (STEPS.indexOf(base) === -1) return step;
    if (!m) return _(base);
    return _(base) + " (" + (m[3] ? m[2] + "/" + m[3]
      : ngettext("%(num)s read", "%(num)s read", +m[2])) + ")";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function dur(s) {
    s = Math.max(0, Math.round(s || 0));
    var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    if (h) return h + "h " + m + "m";
    if (m) return m + "m " + (x < 10 ? "0" : "") + x + "s";
    return x + "s";
  }
  function when(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(window.I18N_LANG || [], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  }
  function errors(n) { return ngettext("%(num)s error", "%(num)s errors", n); }
  // What Apify charged for a run (null: collected Self-hosted, nothing charged).
  // Runs usually cost fractions of a cent, so small amounts keep more decimals.
  function cost(r) {
    if (r.cost_usd == null) return "—";
    var n = Number(r.cost_usd);
    return "US$ " + n.toLocaleString(window.I18N_LANG || [], {
      minimumFractionDigits: 2, maximumFractionDigits: n < 1 ? 4 : 2 });
  }

  function badge(r, stale) {
    if (r.crashed) return '<span class="state bad" title="' + esc(_("No progress for over %(minutes)s min", { minutes: stale })) + '">' + esc(_("Stalled")) + "</span>";
    if (!r.finished_at) return '<span class="state run">' + esc(_("Running")) + "</span>";
    if (r.errors) return '<span class="state bad" title="' + esc(errorLabel(r.last_error)) + '">' + esc(errors(r.errors)) + "</span>";
    return '<span class="state on">' + esc(_("Done")) + "</span>";
  }

  // ---- run status (polled) ----------------------------------------------
  var prevState = null;

  function paint(st) {
    var r = st.run, title, meta = "", sub = "", pct = null, indet = false;

    if (st.state === "queued") {
      title = _("Starting a run…");
      indet = true;
      meta = esc((st.requested_ago_s || 0) > 30
        ? _("Requested %(ago)s ago and the worker hasn't picked it up yet. Is the worker running? Check: docker compose logs worker", { ago: dur(st.requested_ago_s) })
        : _("The worker picks it up within a few seconds."));
    } else if (st.state === "running") {
      var total = r.pages_total || 0, done = r.pages_done || 0;
      title = _("Collecting");
      if (done >= total) {
        // every Page collected: the run is in its post-collection steps
        meta = esc(stepLabel(r.current_step) || _("Finishing"));
        // a step that counts ("Saving images and videos (12/469)") fills the bar
        var part = /\((\d+)\/(\d+)\)$/.exec(r.current_step || "");
        if (part && +part[2] > 0) pct = +part[1] / +part[2];
        else indet = true;
      } else {
        // A Page has no fixed number of ads, so progress within it is a count, not a share.
        meta = esc(_("Page %(page)s of %(pages)s", { page: done + 1, pages: total }) +
                   (r.current_step ? " · " + stepLabel(r.current_step) : "") +
                   (r.current_page_ads ? " · " + ngettext("%(num)s ad", "%(num)s ads", r.current_page_ads) : ""));
        pct = done / total;
      }
      sub = _("<b>%(ads)s</b> ads so far · <b>%(new)s</b> new · %(elapsed)s elapsed",
              { ads: r.total_ads_collected, "new": r.new_ads_discovered, elapsed: dur(r.elapsed_s) });
      if (r.errors) sub += " · " + esc(errors(r.errors));
      if (r.cost_usd != null) sub += " · " + esc(_("Apify cost %(cost)s", { cost: cost(r) }));
      if (r.idle_s > 45) sub += " · " + esc(_("last activity %(ago)s ago", { ago: dur(r.idle_s) }));
    } else if (st.state === "stalled") {
      title = _("Stalled");
      meta = esc(_("Run #%(run)s stopped reporting progress %(ago)s ago. Check the worker: docker compose logs worker", { run: r.id, ago: dur(r.idle_s) }));
    } else {
      title = r ? _("Idle") : _("No runs yet");
      if (r) {
        meta = esc(_("Last run %(when)s · %(ads)s ads (%(new)s new) in %(took)s",
                     { when: when(r.finished_at), ads: r.total_ads_collected, "new": r.new_ads_discovered, took: dur(r.elapsed_s) }));
        if (r.cost_usd != null) meta += " · " + esc(_("Apify cost %(cost)s", { cost: cost(r) }));
        if (r.errors) meta += " · " + esc(errors(r.errors)) + ": " + esc(errorLabel(r.last_error));
      }
      if (prevState === "running" || prevState === "queued") {
        sub = esc(_("Done.")) + ' <a href="/">' + esc(_("Open the library →")) + "</a> ";
      }
      sub += esc(st.next_run_in_s != null
        ? _("Next automatic run in about %(time)s.", { time: dur(st.next_run_in_s) })
        : _("Runs automatically every %(hours)sh.", { hours: st.interval_hours }));
    }

    $("runstatus").dataset.state = st.state;
    $("rs-title").textContent = title;
    $("rs-meta").innerHTML = meta;
    $("rs-sub").innerHTML = sub;
    $("rs-sub").hidden = !sub;
    var bar = $("rs-bar");
    bar.hidden = pct == null && !indet;
    bar.classList.toggle("indet", indet);
    bar.firstElementChild.style.width = indet ? "" : Math.round((pct || 0) * 100) + "%";

    $("collect-btn").disabled = !!BUTTON_LABELS[st.state];
    $("collect-lbl").textContent = BUTTON_LABELS[st.state] || _("Collect now");

    var rows = (st.runs || []).map(function (x) {
      // a run's error shows under it, not only in the badge's tooltip
      var err = x.errors && x.last_error ? errorLabel(x.last_error) : "";
      return "<tr" + (err ? ' class="has-err"' : "") + ">" +
        '<td class="mono">' + esc(when(x.started_at)) + "</td>" +
        "<td>" + esc(TRIGGERS[x.trigger] || "—") + "</td>" +
        "<td>" + badge(x, st.stale_minutes) + "</td>" +
        '<td class="mono">' + x.pages_done + "/" + x.pages_total + "</td>" +
        '<td class="mono">' + x.total_ads_collected + "</td>" +
        '<td class="mono">' + x.new_ads_discovered + "</td>" +
        '<td class="mono">' + dur(x.elapsed_s) + "</td>" +
        '<td class="mono">' + esc(cost(x)) + "</td></tr>" +
        (err ? '<tr class="runerr"><td colspan="8"><span>' + esc(err) + "</span></td></tr>" : "");
    }).join("");
    $("runs-body").innerHTML = rows || '<tr><td class="tdempty" colspan="8">' + esc(_("No collection runs yet.")) + "</td></tr>";
    if (noteState && st.state !== noteState) note("");  // the note was about the state before
    prevState = st.state;
  }

  // ---- a note on the last "Collect now" -------------------------------------
  // Shown when the click didn't start a run, so it never just does nothing. A note
  // about a state (a run already going) goes when that state does; any note after 20s.
  var noteState = null, noteTimer = null;
  function note(text, state) {
    clearTimeout(noteTimer);
    noteState = text && state ? state : null;
    $("rs-note").textContent = text;
    $("rs-note").hidden = !text;
    if (text) noteTimer = setTimeout(function () { note(""); }, 20000);
  }
  function refusal(st) {
    if (st.state === "running") return _("A collection is already running (run #%(run)s): wait for it to finish before starting another.", { run: st.run.id });
    if (st.state === "queued") return _("A collection was already requested: the worker starts it in a few seconds.");
    return _("The previous collection was just finishing. Try again.");
  }

  var timer = null;
  function schedule(st) {
    clearTimeout(timer);
    var busy = st && !!BUTTON_LABELS[st.state];
    timer = setTimeout(poll, document.hidden ? 30000 : busy ? 2000 : 10000);
  }
  function getJSON(url, options) {
    return fetch(url, options).then(function (r) {
      if (r.status === 401) { location.href = "/login"; throw new Error("unauthorized"); }
      return r.json();
    });
  }
  var pollFailed = false;
  function poll() {
    getJSON("/admin/run-status", { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (st) {
        if (pollFailed) { pollFailed = false; note(""); }
        paint(st);
        schedule(st);
      })
      .catch(function (err) {
        if (err && err.message === "unauthorized") return;
        pollFailed = true;
        note(_("The server isn't answering, so this status may be out of date. Trying again…"));
        schedule(null);
      });
  }

  $("collect-form").addEventListener("submit", function (e) {
    e.preventDefault();
    $("collect-btn").disabled = true;
    $("collect-lbl").textContent = BUTTON_LABELS.queued;
    note("");
    getJSON("/admin/collect-now", { method: "POST", headers: { Accept: "application/json" } })
      .then(function (st) {
        paint(st);
        schedule(st);
        if (!st.accepted) note(refusal(st), st.state);
      })
      .catch(function (err) {
        if (err && err.message === "unauthorized") return;
        poll();
        note(_("Couldn't reach the server, so no collection was started. Try again."));
      });
  });

  if (window.__RUN_STATUS__) { paint(window.__RUN_STATUS__); schedule(window.__RUN_STATUS__); }

  // ---- inline edit (company header / page row) + delete confirms --------
  document.addEventListener("click", function (e) {
    var ed = e.target.closest("[data-edit]");
    if (ed) {
      var k = ed.dataset.edit, form = $(k + "-edit");
      $(k + "-view").hidden = true;
      form.hidden = false;
      var first = form.querySelector("input:not([readonly])");
      if (first) { first.focus(); first.select(); }
      return;
    }
    var cancel = e.target.closest("[data-cancel]");
    if (cancel) {
      var k2 = cancel.dataset.cancel, box = $(k2 + "-edit");
      var f = box.tagName === "FORM" ? box : box.querySelector("form");
      if (f) f.reset();
      box.hidden = true;
      $(k2 + "-view").hidden = false;
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var open = e.target.closest && e.target.closest("[id$='-edit']");
    if (open) { var c = open.querySelector("[data-cancel]"); if (c) c.click(); }
  });
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
})();
