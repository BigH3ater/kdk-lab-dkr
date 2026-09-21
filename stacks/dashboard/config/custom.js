/* Kodiak sidebar navigation — Homepage loads config/custom.js.
   Injects a fixed left nav (bear mark + mono labels) linking every family
   surface; custom.css styles it and shifts #page_container right. */
(function () {
  const LINKS = [
    ["Home", "https://home.kmkdp.com/"],
    ["Meal Plan", "https://recipes.kmkdp.com/mealplan"],
    ["Recipes", "https://recipes.kmkdp.com/"],
    ["Tasks", "https://tasks.kmkdp.com/"],
    ["Budget", "https://budget.kmkdp.com/"],
    ["Watch", "https://jellyfin.kmkdp.com/"],
    ["Listen", "https://audiobookshelf.kmkdp.com/"],
    ["Photos", "https://immich.kmkdp.com/"],
    ["Request TV/Movies", "https://seerr.kmkdp.com/"],
    ["Request Books", "https://libreseerr.kmkdp.com/"],
    ["Guides", "https://home.kmkdp.com/guides/"],
  ];
  const BEAR =
    '<svg viewBox="19 31 82 64" width="30" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" d="M60 40 C53 33 45 31 38 32 C28 33 19 43 19 56 C19 71 28 85 42 91 C50 95 56 93 60 90 C64 93 70 95 78 91 C92 85 101 71 101 56 C101 43 92 33 82 32 C75 31 67 33 60 40 Z M47 56 C40 54 33 58 32 66 C31 73 36 77 42 75 C38 72 39 65 46 62 C50 60 50 57 47 56 Z M73 56 C80 54 87 58 88 66 C89 73 84 77 78 75 C82 72 81 65 74 62 C70 60 70 57 73 56 Z M58.5 90 C58.5 82 58.5 70 58.5 60 C58.5 57 61.5 57 61.5 60 C61.5 70 61.5 82 61.5 90 C61.5 92 58.5 92 58.5 90 Z"/></svg>';

  function build() {
    if (document.getElementById("kodiak-sidebar")) return;
    const nav = document.createElement("nav");
    nav.id = "kodiak-sidebar";
    nav.innerHTML =
      '<div class="ks-brand">' + BEAR + "<span>Mack House</span></div>" +
      '<ul>' +
      LINKS.map(
        ([label, href]) =>
          '<li><a href="' + href + '"' +
          (href.startsWith("https://home.kmkdp.com") ? "" : ' target="_blank" rel="noopener"') +
          ">" + label + "</a></li>"
      ).join("") +
      "</ul>";
    document.body.appendChild(nav);
    document.body.classList.add("has-kodiak-sidebar");
    const btn = document.createElement("button");
    btn.id = "ks-toggle"; btn.title = "Toggle menu"; btn.textContent = "\u2630";
    btn.addEventListener("click", function () {
      const small = window.matchMedia("(max-width: 820px)").matches;
      if (small) { document.body.classList.toggle("ks-open"); return; }
      const collapsed = document.body.classList.toggle("ks-collapsed");
      try { localStorage.setItem("ks-collapsed", collapsed ? "1" : "0"); } catch (e) {}
    });
    document.body.appendChild(btn);
    try { if (localStorage.getItem("ks-collapsed") === "1") document.body.classList.add("ks-collapsed"); } catch (e) {}
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
