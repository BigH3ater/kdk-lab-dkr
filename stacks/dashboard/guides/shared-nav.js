/* Kodiak shared navigation -- the ONE sidebar used by Homepage, the guides and
   the /meal sidecar pages, so navigation looks and behaves the same everywhere.
   Single source of truth for the link set, order, labels, active state and the
   collapsed/mobile behaviour. Styling lives in shared-nav.css.

   - Homepage loads it through config/custom.js (a tiny loader); custom.css pulls
     in shared-nav.css with @import.
   - Each guide page loads shared-nav.css + shared-nav.js directly.
   - The Flask /meal pages inject both in their <head>.

   The /meal grid, tasks and weather pages are embedded as iframes on the
   dashboard, so the nav MUST NOT be drawn a second time inside the frame: we bail
   out when we are not the top window. The standalone /meal/groceries page (used in
   the store on a phone) is not framed, so it gets the full nav. */
(function () {
  // Never render inside an iframe (the dashboard embeds /meal pages). Reading
  // window.top can throw across origins; same-origin here, but stay defensive.
  try { if (window.self !== window.top) return; } catch (e) { return; }

  // Same link set + order on every surface (the family launcher). home.* and
  // homeassistant.* stay same-tab (Kodiak-themed, OIDC via Authelia); the rest
  // open in a new tab.
  var LINKS = [
    ["Home",              "https://home.kmkdp.com/"],
    ["This Week",         "https://home.kmkdp.com/meal/"],
    ["Groceries",         "https://home.kmkdp.com/meal/groceries"],
    ["Meal Plan",         "https://recipes.kmkdp.com/mealplan"],
    ["Recipes",           "https://recipes.kmkdp.com/"],
    ["Tasks",             "https://tasks.kmkdp.com/"],
    ["Home Control",      "https://homeassistant.kmkdp.com/home-controls"],
    ["Vacuum",            "https://homeassistant.kmkdp.com/roborock-vac/mr-slave"],
    ["Homelab",           "https://homeassistant.kmkdp.com/homelab-hw"],
    ["Workout",           "https://homeassistant.kmkdp.com/workout"],
    ["Budget",            "https://budget.kmkdp.com/"],
    ["Watch",             "https://jellyfin.kmkdp.com/"],
    ["Listen",            "https://audiobookshelf.kmkdp.com/"],
    ["Photos",            "https://immich.kmkdp.com/"],
    ["Request TV/Movies", "https://seerr.kmkdp.com/"],
    ["Request Books",     "https://libreseerr.kmkdp.com/"],
    ["Guides",            "https://home.kmkdp.com/guides/"]
  ];
  var HOME = "https://home.kmkdp.com/";
  var BEAR = '<svg viewBox="19 31 82 64" width="26" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" d="M60 40 C53 33 45 31 38 32 C28 33 19 43 19 56 C19 71 28 85 42 91 C50 95 56 93 60 90 C64 93 70 95 78 91 C92 85 101 71 101 56 C101 43 92 33 82 32 C75 31 67 33 60 40 Z M47 56 C40 54 33 58 32 66 C31 73 36 77 42 75 C38 72 39 65 46 62 C50 60 50 57 47 56 Z M73 56 C80 54 87 58 88 66 C89 73 84 77 78 75 C82 72 81 65 74 62 C70 60 70 57 73 56 Z M58.5 90 C58.5 82 58.5 70 58.5 60 C58.5 57 61.5 57 61.5 60 C61.5 70 61.5 82 61.5 90 C61.5 92 58.5 92 58.5 90 Z"/></svg>';

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function attr(s) { return esc(s).replace(/"/g, "&quot;"); }

  // Drop a trailing slash (except for the bare root) so "/guides" and "/guides/"
  // compare equal.
  function normPath(p) {
    if (p.length > 1 && p.charAt(p.length - 1) === "/") { p = p.slice(0, -1); }
    return p;
  }

  // Active link = the one whose URL matches the current host and the longest path
  // prefix. A link to "/" matches only the exact root (so it is not marked active
  // on /guides/ or /meal/*). Compare the parsed hostname exactly -- a startsWith()
  // on the href string would also match look-alikes like home.kmkdp.com.evil.test
  // (CodeQL js/incomplete-url-substring-sanitization).
  var hereHost = location.hostname;
  var herePath = normPath(location.pathname);
  var activeIdx = -1, bestLen = -1;
  LINKS.forEach(function (l, i) {
    var u; try { u = new URL(l[1]); } catch (e) { return; }
    if (u.hostname !== hereHost) { return; }
    var lp = normPath(u.pathname);
    var match = lp === "" ? herePath === "" : (herePath === lp || herePath.indexOf(lp + "/") === 0);
    if (match && lp.length > bestLen) { bestLen = lp.length; activeIdx = i; }
  });

  var items = LINKS.map(function (l, i) {
    var label = l[0], href = l[1];
    var host = ""; try { host = new URL(href).hostname; } catch (e) {}
    var sameTab = host === "home.kmkdp.com" || host === "homeassistant.kmkdp.com";
    var active = i === activeIdx;
    return '<li><a href="' + attr(href) + '"' +
      (sameTab ? "" : ' target="_blank" rel="noopener"') +
      (active ? ' class="active" aria-current="page"' : "") +
      ">" + esc(label) + "</a></li>";
  }).join("");

  var brand = '<a class="ks-brand" href="' + attr(HOME) + '">' + BEAR + "<span>Mack House</span></a>";

  function build() {
    if (document.getElementById("kodiak-sidebar")) { return; }
    var body = document.body;
    if (!body) { return; }

    var nav = document.createElement("nav");
    nav.id = "kodiak-sidebar";
    nav.setAttribute("aria-label", "Mack House");
    nav.innerHTML = brand + "<ul>" + items + "</ul>";

    var bar = document.createElement("div");
    bar.id = "kodiak-topbar";
    bar.innerHTML = brand +
      '<button id="kodiak-menu" type="button" aria-label="Menu"' +
      ' aria-controls="kodiak-sidebar" aria-expanded="false">☰</button>';

    var backdrop = document.createElement("div");
    backdrop.id = "kodiak-backdrop";

    body.insertBefore(bar, body.firstChild);
    body.appendChild(nav);
    body.appendChild(backdrop);
    body.classList.add("has-kodiak-nav");

    var btn = bar.querySelector("#kodiak-menu");
    function setOpen(open) {
      body.classList.toggle("ks-open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        var first = nav.querySelector("a");
        if (first) { first.focus(); }
      } else {
        btn.focus();
      }
    }
    btn.addEventListener("click", function () {
      setOpen(!body.classList.contains("ks-open"));
    });
    backdrop.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && body.classList.contains("ks-open")) { setOpen(false); }
    });
    // Tapping any sidebar link closes the overlay (it navigates anyway).
    nav.addEventListener("click", function (e) {
      if (e.target.closest("a")) { setOpen(false); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
