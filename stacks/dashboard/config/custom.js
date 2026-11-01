/* Kodiak navigation loader. Homepage only auto-loads config/custom.js, so this
   pulls in the ONE shared sidebar that the guides and /meal pages use too. The
   nav markup, link set, order, labels, active state and mobile behaviour all live
   in /guides/shared-nav.js (its styling is pulled in by custom.css, which
   @imports /guides/shared-nav.css). Both are served same-origin from /guides,
   behind Authelia -- no external CDN. Keeping one source means the three apps
   never drift. */
(function () {
  if (document.getElementById("kodiak-nav-loader")) { return; }
  var s = document.createElement("script");
  s.id = "kodiak-nav-loader";
  s.src = "/guides/shared-nav.js?v=nav20261101b";
  s.defer = true;
  (document.head || document.documentElement).appendChild(s);
})();
