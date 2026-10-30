/* Guides sidebar: moves between the family how-to pages, marks the current
   page, and points back to the dashboard. Injected on every guide page.
   Links are relative and the pages set <base href="/guides/">, so they resolve
   under the Traefik StripPrefix(/guides) regardless of the trailing slash. */
(function () {
  var HOME = "https://home.kmkdp.com/";
  var GUIDES = [
    ["index.html",          "Guides home"],
    ["recipes.html",        "Dinner & Recipes"],
    ["jellyfin.html",       "Watch Movies & TV"],
    ["seerr.html",          "Request a Movie or Show"],
    ["audiobookshelf.html", "Audiobooks"],
    ["libreseerr.html",     "Books & eBooks"],
    ["photos.html",         "Family Photos"],
    ["budget.html",         "Budget"]
  ];
  var BEAR = '<svg viewBox="19 31 82 64" width="26" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" d="M60 40 C53 33 45 31 38 32 C28 33 19 43 19 56 C19 71 28 85 42 91 C50 95 56 93 60 90 C64 93 70 95 78 91 C92 85 101 71 101 56 C101 43 92 33 82 32 C75 31 67 33 60 40 Z M47 56 C40 54 33 58 32 66 C31 73 36 77 42 75 C38 72 39 65 46 62 C50 60 50 57 47 56 Z M73 56 C80 54 87 58 88 66 C89 73 84 77 78 75 C82 72 81 65 74 62 C70 60 70 57 73 56 Z M58.5 90 C58.5 82 58.5 70 58.5 60 C58.5 57 61.5 57 61.5 60 C61.5 70 61.5 82 61.5 90 C61.5 92 58.5 92 58.5 90 Z"/></svg>';

  // Current page file name, tolerant of /guides, /guides/ and /guides/x.html.
  var file = location.pathname.split("/").pop();
  if (file === "" || file === "guides") { file = "index.html"; }

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  var items = GUIDES.map(function (g) {
    var active = g[0] === file;
    return '<li><a href="' + g[0] + '"' +
      (active ? ' class="active" aria-current="page"' : "") + ">" +
      esc(g[1]) + "</a></li>";
  }).join("");

  var nav = document.createElement("nav");
  nav.id = "kodiak-sidebar";
  nav.setAttribute("aria-label", "Guides");
  nav.innerHTML =
    '<a class="ks-brand" href="index.html">' + BEAR + "<span>Guides</span></a>" +
    '<a class="ks-home" href="' + HOME + '">&larr; The Mack House</a>' +
    "<ul>" + items + "</ul>";
  document.body.appendChild(nav);
})();
