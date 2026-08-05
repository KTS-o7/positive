/* Positive — renderer.
 *
 * Reads /data/stories.json, paints one long page, each story a section.
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

  function boot() {
    var stored = getStoredTheme();
    applyTheme(stored || "light");

    var toggle = $(".theme-toggle");
    if (toggle) toggle.addEventListener("click", toggleTheme);

    fetch("data/stories.json", { cache: "no-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(renderSite)
      .catch(function (e) { showError("Could not load stories: " + e.message); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();