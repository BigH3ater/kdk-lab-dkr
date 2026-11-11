/* Kitchen board + household page behaviour. Small on purpose: everything is
   rendered server-side; this only adds the clock, ticks, checklists, timers,
   pantry reorders and the grocery add box,
   wake lock, idle return, night view, anti-ghosting shift and the 2-minute
   fragment refresh. Pages work (read-only) without it. */
(function () {
  "use strict";
  var body = document.body;
  var page = body.dataset.page || "";
  var kiosk = body.classList.contains("kiosk");
  var TZ = "America/Chicago";

  function chicagoHour() {
    try {
      return parseInt(new Intl.DateTimeFormat("en-US", {timeZone: TZ, hour: "numeric", hourCycle: "h23"}).format(new Date()), 10) % 24;
    } catch (e) { return new Date().getHours(); }
  }
  function store(k, v) {            // localStorage, never fatal (private mode, previews)
    try {
      if (v === undefined) { return window.localStorage.getItem(k); }
      if (v === null) { window.localStorage.removeItem(k); } else { window.localStorage.setItem(k, v); }
    } catch (e) { return null; }
    return null;
  }

  // ---- clock -----------------------------------------------------------------
  function tickClock() {
    var s;
    try { s = new Intl.DateTimeFormat("en-US", {timeZone: TZ, hour: "numeric", minute: "2-digit"}).format(new Date()); }
    catch (e) { s = new Date().toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}); }
    document.querySelectorAll("[data-clock]").forEach(function (el) { el.textContent = s; });
  }
  if (document.querySelector("[data-clock]")) { tickClock(); setInterval(tickClock, 15000); }

  // ---- screen wake lock (iPadOS 18.4+ in Home Screen apps) ---------------------
  var lock = null;
  async function wake() {
    if (!kiosk || !("wakeLock" in navigator) || document.visibilityState !== "visible") { return; }
    try {
      lock = await navigator.wakeLock.request("screen");
      lock.addEventListener("release", function () { lock = null; });
    } catch (e) { lock = null; }
  }
  wake();
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { if (!lock) { wake(); } refresh(); }
  });

  // ---- anti-ghosting: move the layout a few px every hour -----------------------
  function shift() {
    var h = chicagoHour();
    var x = ((h % 3) - 1) * 3, y = ((Math.floor(h / 3) % 3) - 1) * 3;
    document.documentElement.style.setProperty("--sx", x + "px");
    document.documentElement.style.setProperty("--sy", y + "px");
  }
  if (kiosk) { shift(); setInterval(shift, 60000); }

  // ---- night view (kitchen board, 22:00-06:00 Chicago) -------------------------
  var wakeUntil = 0;
  function nightCheck() {
    if (page !== "kitchen") { return; }
    var h = chicagoHour();
    var night = (h >= 22 || h < 6) && Date.now() > wakeUntil;
    body.classList.toggle("is-night", night);
  }
  var nightEl = document.getElementById("night");
  if (nightEl) {
    var showBoard = function () { wakeUntil = Date.now() + 3 * 60 * 1000; nightCheck(); };
    nightEl.addEventListener("click", showBoard);
    nightEl.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { showBoard(); } });
    nightCheck(); setInterval(nightCheck, 20000);
  }

  // ---- idle return: sub-views go back to the board after 5 idle minutes --------
  var timersRunning = 0;
  if (page === "ingredients" || page === "steps" || page === "pantry" || page === "chores") {
    var home = body.dataset.home || "/meal/kitchen", last = Date.now();
    ["pointerdown", "keydown", "touchstart", "scroll"].forEach(function (ev) {
      document.addEventListener(ev, function () { last = Date.now(); }, {passive: true, capture: true});
    });
    setInterval(function () {
      if (timersRunning === 0 && Date.now() - last > 5 * 60 * 1000) { location.href = home; }
    }, 15000);
  }

  // ---- fragment refresh (every 2 min, no page flash) ---------------------------
  var board = document.getElementById("board");
  var busy = false;
  async function refresh() {
    if (!board || !board.dataset.fragment || busy) { return; }
    busy = true;
    var list = board.querySelector(".clist-wrap"), keep = list ? list.scrollTop : 0, own = board.scrollTop;
    var ingOpen = !!board.querySelector("details.hh-ings[open]");
    try {
      var r = await fetch(board.dataset.fragment, {credentials: "same-origin", cache: "no-store"});
      if (!r.ok || (r.headers.get("content-type") || "").indexOf("text/html") !== 0) { throw new Error(r.status); }
      var html = await r.text();
      if (html.indexOf("board-in") < 0 && html.indexOf("hh-grid") < 0 && html.indexOf("chores-in") < 0) {
        throw new Error("not a fragment");
      }
      board.innerHTML = html;
      list = board.querySelector(".clist-wrap"); if (list && keep) { list.scrollTop = keep; }
      if (own) { board.scrollTop = own; }               // the All chores view scrolls itself
      if (ingOpen) { var g = board.querySelector("details.hh-ings"); if (g) { g.open = true; } }
      syncNight(); restoreChecks(); markMore();
    } catch (e) { /* keep the old board; try again next cycle (a login redirect lands here too) */ }
    busy = false;
  }
  // ---- chore list: fade the bottom while there is more to scroll --------------
  var SCROLLERS = ".clist-wrap, .chores-scroll";
  function markMore() {
    document.querySelectorAll(SCROLLERS).forEach(function (l) {
      l.classList.toggle("more", l.scrollTop + l.clientHeight < l.scrollHeight - 2);
    });
  }
  document.addEventListener("scroll", function (e) {
    if (e.target && e.target.matches && e.target.matches(SCROLLERS)) { markMore(); }
  }, {passive: true, capture: true});
  document.addEventListener("toggle", markMore, true);       // a details fold opened
  window.addEventListener("resize", markMore);
  markMore();
  if (document.fonts && document.fonts.ready) { document.fonts.ready.then(markMore); }
  function syncNight() {
    var bi = board && board.querySelector(".board-in");
    var ne = document.querySelector(".night-ev");
    if (!bi || !nightEl) { return; }
    var ev = bi.dataset.nightEvent || "";
    if (!ne && ev) { ne = document.createElement("div"); ne.className = "night-ev"; nightEl.appendChild(ne); }
    if (ne) { ne.innerHTML = ev ? '<span class="muted">Tomorrow</span> ' : ""; if (ev) { ne.appendChild(document.createTextNode(ev)); } }
  }
  if (board && board.dataset.fragment) { setInterval(refresh, 2 * 60 * 1000); }

  // ---- chore ticks --------------------------------------------------------------
  document.addEventListener("submit", async function (e) {
    var f = e.target;
    if (!f.classList || !f.classList.contains("chore")) { return; }
    e.preventDefault();
    if (f.classList.contains("busy") || f.classList.contains("done")) { return; }
    f.classList.add("busy", "done");
    var fd = new FormData(f); fd.append("ajax", "1");
    try {
      var r = await fetch(f.action, {method: "POST", body: fd, credentials: "same-origin", redirect: "manual", headers: {"X-Kitchen": "1"}});
      if (r.status !== 204) { throw new Error(r.status); }
      f.classList.remove("busy");
      setTimeout(refresh, 600);
    } catch (err) {
      // never re-POST (a recurring chore would advance twice): show the real state
      f.classList.remove("busy", "done");
      refresh();
    }
  });

  // ---- ingredient checklist (ticks kept in this browser for the evening) -------
  var today = (document.querySelector("[data-check]") || {dataset: {}}).dataset.check;
  today = today ? today.split(":")[0] : "";
  function prune() {
    try {
      for (var i = window.localStorage.length - 1; i >= 0; i--) {
        var k = window.localStorage.key(i);
        if (k && k.indexOf("ing:") === 0 && k.slice(4, 14) !== today) { window.localStorage.removeItem(k); }
      }
    } catch (e) { /* no storage */ }
  }
  function tally() {
    var t = document.querySelector("[data-pulled]");
    if (t) { t.textContent = document.querySelectorAll(".ing.got").length; }
  }
  function restoreChecks() {
    document.querySelectorAll("[data-check]").forEach(function (b) {
      var on = store("ing:" + b.dataset.check) === "1";
      b.classList.toggle("got", on); b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    tally();
  }
  if (today) { prune(); restoreChecks(); }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-check]");
    if (!b) { return; }
    var on = !b.classList.contains("got");
    b.classList.toggle("got", on); b.setAttribute("aria-pressed", on ? "true" : "false");
    store("ing:" + b.dataset.check, on ? "1" : null);
    tally();
  });

  // ---- steps: one at a time, tabs + previous/next --------------------------------
  var steps = document.querySelectorAll(".step");
  if (steps.length) {
    var tabs = document.querySelectorAll(".tab"), next = document.querySelector("[data-next]"),
        prev = document.querySelector("[data-prev]"), labels = (next.dataset.labels || "").split("|"), cur = 0;
    var go = function (i) {
      if (i >= steps.length) { location.href = body.dataset.home || "/meal/kitchen"; return; }
      cur = Math.max(0, i);
      steps.forEach(function (s, k) { s.hidden = k !== cur; });
      tabs.forEach(function (t, k) { t.setAttribute("aria-selected", k === cur ? "true" : "false"); t.classList.toggle("past", k < cur); });
      next.textContent = labels[cur] || "Next";
      prev.disabled = cur === 0;
      if (tabs[cur] && tabs[cur].scrollIntoView) { tabs[cur].scrollIntoView({block: "nearest", inline: "nearest"}); }
    };
    tabs.forEach(function (t) { t.addEventListener("click", function () { go(parseInt(t.dataset.go, 10)); }); });
    next.addEventListener("click", function () { go(cur + 1); });
    prev.addEventListener("click", function () { go(cur - 1); });
    go(0);
  }

  // ---- timers with a chime ----------------------------------------------------
  var bar = document.getElementById("timerbar"), audio = null;
  function chime() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.45, 0.9].forEach(function (t) {
        var o = audio.createOscillator(), g = audio.createGain();
        o.type = "sine"; o.frequency.value = 880;
        g.gain.setValueAtTime(0.0001, audio.currentTime + t);
        g.gain.exponentialRampToValueAtTime(0.4, audio.currentTime + t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + t + 0.4);
        o.connect(g); g.connect(audio.destination);
        o.start(audio.currentTime + t); o.stop(audio.currentTime + t + 0.42);
      });
    } catch (e) { /* no audio */ }
  }
  function fmt(s) { var m = Math.floor(s / 60), r = s % 60; return m + ":" + (r < 10 ? "0" : "") + r; }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-timer]");
    if (!b || !bar) { return; }
    try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); } catch (err) { /* user gesture unlocks audio on iOS */ }
    var end = Date.now() + parseInt(b.dataset.timer, 10) * 60000;
    var chip = document.createElement("button");
    chip.type = "button"; chip.className = "tchip"; chip.setAttribute("aria-label", "Timer, tap to stop");
    bar.appendChild(chip); timersRunning++;
    var ringing = null;
    var stop = function () {
      clearInterval(iv); if (ringing) { clearInterval(ringing); }
      chip.remove(); timersRunning = Math.max(0, timersRunning - 1);
    };
    var iv = setInterval(function () {
      var left = Math.round((end - Date.now()) / 1000);
      if (left > 0) { chip.textContent = fmt(left); return; }
      clearInterval(iv); chip.textContent = "Done"; chip.classList.add("ring");
      chime(); ringing = setInterval(chime, 4000);
      setTimeout(stop, 2 * 60 * 1000);
    }, 250);
    chip.textContent = fmt(Math.round((end - Date.now()) / 1000));
    chip.addEventListener("click", stop);
  });

  // ---- pantry check: "Checked" ticks (this browser, tonight only) ---------------
  function chicagoDate() {
    try { return new Intl.DateTimeFormat("en-CA", {timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date()); }
    catch (e) { return new Date().toISOString().slice(0, 10); }
  }
  var pchecks = document.querySelectorAll("[data-pcheck]");
  if (pchecks.length) {
    var pday = pchecks[0].dataset.pcheck.slice(0, 10);
    try {
      for (var pi = window.localStorage.length - 1; pi >= 0; pi--) {
        var pk = window.localStorage.key(pi);
        if (pk && pk.indexOf("pan:") === 0 && pk.slice(4, 14) !== pday) { window.localStorage.removeItem(pk); }
      }
    } catch (e) { /* no storage */ }
    var setP = function (b, on) {
      b.classList.toggle("got", on); b.setAttribute("aria-pressed", on ? "true" : "false");
      if (b.parentNode) { b.parentNode.classList.toggle("got", on); }
      store("pan:" + b.dataset.pcheck, on ? "1" : null);
    };
    var ptally = function () {
      var t = document.querySelector("[data-pchecked]");
      if (t) { t.textContent = document.querySelectorAll("[data-sec=used] .pcheck.got").length; }
    };
    pchecks.forEach(function (b) { if (store("pan:" + b.dataset.pcheck) === "1") { setP(b, true); } });
    ptally();
    document.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest("[data-pcheck]");
      if (b) { setP(b, !b.classList.contains("got")); ptally(); return; }
      var m = e.target.closest && e.target.closest("[data-markall]");
      if (m) {
        document.querySelectorAll("[data-sec=" + m.dataset.markall + "] [data-pcheck]").forEach(function (x) { setP(x, true); });
        ptally();
      }
    });
  }

  // ---- pantry "Running low" + the manual add box (ajax; plain POST without JS) --
  function onList(f) {
    var s = document.createElement("span");
    s.className = "onlist"; s.textContent = "On the list";
    f.hidden = true; f.parentNode.insertBefore(s, f.nextSibling);
    return s;
  }
  var addDay = chicagoDate();
  function addedList(f) { return f.parentNode.querySelector("[data-added]"); }
  // An "Added tonight" chip: the item and where it went ("Added to New Pi ·
  // Dairy & Eggs"). Saved chips from before this change are plain strings.
  function showAdded(ul, x) {
    var item = typeof x === "string" ? x : String((x && x.item) || "");
    var where = typeof x === "string" ? "" : String((x && x.where) || "");
    if (!item) { return; }
    var li = document.createElement("li");
    var a = document.createElement("span"); a.className = "ai"; a.textContent = item; li.appendChild(a);
    if (where) { var w = document.createElement("span"); w.className = "where"; w.textContent = where; li.appendChild(w); }
    ul.insertBefore(li, ul.firstChild);
    ul.parentNode.hidden = false;
  }
  function savedAdds() {
    try { var v = JSON.parse(store("added:" + addDay) || "[]"); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }
  try {
    for (var ai = window.localStorage.length - 1; ai >= 0; ai--) {
      var ak = window.localStorage.key(ai);
      if (ak && ak.indexOf("added:") === 0 && ak !== "added:" + addDay) { window.localStorage.removeItem(ak); }
    }
  } catch (e) { /* no storage */ }
  document.querySelectorAll("form.additem").forEach(function (f) {
    var ul = addedList(f);
    if (ul) { savedAdds().slice().reverse().forEach(function (x) { showAdded(ul, x); }); }
  });
  document.addEventListener("submit", async function (e) {
    var f = e.target;
    if (!f.classList) { return; }
    if (f.classList.contains("reorder")) {
      e.preventDefault();
      if (f.classList.contains("busy")) { return; }
      f.classList.add("busy");
      var badge = onList(f);                      // optimistic
      var fd = new FormData(f); fd.append("ajax", "1");
      try {
        var r = await fetch(f.action, {method: "POST", body: fd, credentials: "same-origin", redirect: "manual", headers: {"X-Kitchen": "1"}});
        if (r.status !== 200) { throw new Error(r.status); }
      } catch (err) {
        badge.remove(); f.hidden = false;
        var b = f.querySelector("button"); if (b) { b.textContent = "Try again"; }
      }
      f.classList.remove("busy");
      return;
    }
    if (f.classList.contains("additem")) {
      e.preventDefault();
      var inp = f.querySelector("input[name=item]");
      var item = (inp.value || "").trim().slice(0, 80);
      if (!item || f.classList.contains("busy")) { inp.value = item; return; }
      f.classList.add("busy");
      var ul = addedList(f);
      var fd2 = new FormData(f); fd2.set("item", item); fd2.append("ajax", "1");
      try {
        var r2 = await fetch(f.action, {method: "POST", body: fd2, credentials: "same-origin", redirect: "manual", headers: {"X-Kitchen": "1"}});
        if (r2.status !== 200) { throw new Error(r2.status); }
        var j = await r2.json();
        var got = {item: String(j.item || item), where: String(j.text || "")};
        if (ul) { showAdded(ul, got); }
        var saved = savedAdds(); saved.unshift(got); store("added:" + addDay, JSON.stringify(saved.slice(0, 30)));
        inp.value = ""; f.classList.remove("err");
        var tog = f.querySelector("input[name=manual]"); if (tog) { tog.checked = false; }  // back to off
      } catch (err) {
        f.classList.add("err");                   // keep the text so it can be sent again
      }
      f.classList.remove("busy");
      inp.focus();
    }
  });
})();
