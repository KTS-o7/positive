/* Positive — renderer.
 *
 * Reads /data/latest.json (with the hand-written seed set as a safe fallback),
 * then paints one long page, each story a section.
 * Reads/writes the theme preference from localStorage. Stays out of the way.
 *
 * No frameworks. No fetches beyond the JSON. No analytics.
 */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };

  /* -------- theme handling -------- */
  var THEME_KEY = "positive.theme";
  function getStoredTheme() {
    try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; }
  }
  function setStoredTheme(t) {
    try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* ignore */ }
  }
  function applyTheme(t) {
    var v = (t === "dark") ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", v);
    var btn = $(".theme-toggle");
    if (btn) {
      btn.setAttribute("aria-pressed", v === "dark" ? "true" : "false");
      btn.setAttribute("title", v === "dark" ? "Switch to light" : "Switch to dark");
      var label = btn.querySelector(".label");
      if (label) label.textContent = v === "dark" ? "Night" : "Day";
    }
  }
  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") || "light";
    var next = cur === "dark" ? "light" : "dark";
    applyTheme(next);
    setStoredTheme(next);
  }

  /* -------- story rendering -------- */
  function renderStory(s) {
    var sec = document.createElement("article");
    sec.className = "story";
    sec.id = s.id || "";
    sec.appendChild(makeMeta(s));
    var h2 = document.createElement("h2");
    h2.textContent = s.title || "(untitled)";
    sec.appendChild(h2);
    var body = document.createElement("div");
    body.className = "body";
    (s.body || []).forEach(function (para) {
      var p = document.createElement("p");
      p.textContent = para;
      body.appendChild(p);
    });
    sec.appendChild(body);
    return sec;
  }

  function makeMeta(s) {
    var meta = document.createElement("div");
    meta.className = "meta";
    if (s.published_at) {
      var d = new Date(s.published_at + "T00:00:00Z");
      if (!isNaN(d)) {
        var months = ["January","February","March","April","May","June",
                      "July","August","September","October","November","December"];
        var dateEl = document.createElement("span");
        dateEl.className = "date";
        dateEl.textContent = months[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + d.getUTCFullYear();
        meta.appendChild(dateEl);
      }
    }
    if (s.source) {
      var src = document.createElement("span");
      src.className = "src";
      src.textContent = s.source;
      meta.appendChild(src);
    }
    return meta;
  }

  function renderSite(d) {
    var site = d.site || {};
    document.title = (site.name || "Positive") + " · " + (site.tagline || "");

    var h1 = $(".mast h1");
    if (h1) {
      var name = site.name || "Positive";
      h1.innerHTML = name.replace(/\./g, '<span class="dot">.</span>');
    }
    var tag = $(".mast .tag");
    if (tag) tag.textContent = site.tagline || "";
    var intro = $(".mast .intro");
    if (intro) intro.textContent = site.intro || "";

    var main = $("#stories");
    if (main) {
      main.innerHTML = "";
      (d.stories || []).forEach(function (s) { main.appendChild(renderStory(s)); });
    }

    var repoLink = $("#repo-link");
    if (repoLink && site.repo) repoLink.href = site.repo;
  }

  function showError(msg) {
    var main = $("#stories");
    if (!main) return;
    main.innerHTML = "";
    var div = document.createElement("div");
    div.style.cssText = "padding:80px 20px;text-align:center;color:var(--mut2);font-style:italic";
    div.textContent = msg;
    main.appendChild(div);
  }

  var MONTHS = ["January","February","March","April","May","June",
                "July","August","September","October","November","December"];
  function fmtDate(iso) {
    var d = new Date(iso + "T00:00:00Z");
    return isNaN(d) ? iso : MONTHS[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + d.getUTCFullYear();
  }

  /* -------- paused mode: list of past editions -------- */
  function renderEditions(editions) {
    var main = $("#stories");
    if (!main) return;
    main.innerHTML = "";
    var list = document.createElement("div");
    list.className = "editions";
    editions.forEach(function (ed) {
      var block = document.createElement("section");
      block.className = "edition";
      var h = document.createElement("h3");
      var a = document.createElement("a");
      a.href = "?date=" + ed.date;
      a.textContent = fmtDate(ed.date);
      h.appendChild(a);
      block.appendChild(h);
      var ul = document.createElement("ul");
      (ed.stories || []).forEach(function (s) {
        var li = document.createElement("li");
        var sa = document.createElement("a");
        sa.href = "?date=" + ed.date + "#" + encodeURIComponent(s.id || "");
        sa.textContent = s.title || "(untitled)";
        li.appendChild(sa);
        if (s.source) {
          var src = document.createElement("span");
          src.className = "src";
          src.textContent = s.source;
          li.appendChild(src);
        }
        ul.appendChild(li);
      });
      block.appendChild(ul);
      list.appendChild(block);
    });
    main.appendChild(list);
  }

  /* -------- single edition, opened from the list -------- */
  function renderOneEdition(d, date) {
    renderSite(d);
    var notice = $("#paused");
    if (notice) notice.style.display = "none";
    var main = $("#stories");
    var bar = document.createElement("div");
    bar.className = "edition-bar";
    var back = document.createElement("a");
    back.href = "./";
    back.textContent = "← All editions";
    var when = document.createElement("span");
    when.textContent = fmtDate(date);
    bar.appendChild(back);
    bar.appendChild(when);
    main.insertBefore(bar, main.firstChild);
    if (location.hash) {
      var target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
      /* After layout settles, and instant: the page-wide smooth scroll gets cut short while rendering. */
      if (target) setTimeout(function () { target.scrollIntoView({ behavior: "instant", block: "start" }); }, 60);
    }
  }

  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(url + " HTTP " + r.status);
      return r.json();
    });
  }

  function boot() {
    var stored = getStoredTheme();
    applyTheme(stored || "light");

    var toggle = $(".theme-toggle");
    if (toggle) toggle.addEventListener("click", toggleTheme);

    var m = location.search.match(/[?&]date=(\d{4}-\d{2}-\d{2})/);
    if (m) {
      getJSON("data/" + m[1] + ".json")
        .then(function (d) { renderOneEdition(d, m[1]); })
        .catch(function (e) { showError("Could not load that edition: " + e.message); });
      return;
    }
    /* Paused: show every past edition. Fall back to the last edition, then the seed set,
       so a missing index never blanks the site. */
    getJSON("data/editions.json")
      .then(renderEditions)
      .catch(function () {
        return getJSON("data/latest.json")
          .catch(function () { return getJSON("data/stories.json"); })
          .then(renderSite);
      })
      .catch(function (e) { showError("Could not load stories: " + e.message); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();