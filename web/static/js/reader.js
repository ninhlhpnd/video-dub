/* Trang đọc: cài đặt chữ/nền, phím ← →, thanh tiến độ, lưu chương đang đọc, ô chọn chương. */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var store = window.__store;
  var reader = $("#reader"); if (!reader) return;
  var root = document.documentElement.style;
  var DEF = { size: 20, lh: "1.85", font: "serif", width: "760" };
  var prefs = Object.assign({}, DEF, store.get("reader", {}));

  function apply() {
    root.setProperty("--r-size", prefs.size + "px");
    root.setProperty("--r-lh", prefs.lh);
    root.setProperty("--r-width", prefs.width + "px");
    root.setProperty("--r-font", prefs.font === "sans" ? "var(--font-ui)" : "var(--font-read)");
    store.set("reader", prefs);
    $$("[data-pref]").forEach(function (box) {
      var key = box.getAttribute("data-pref");
      var cur = key === "theme" ? document.documentElement.getAttribute("data-theme") : String(prefs[key]);
      $$("[data-v]", box).forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-v") === cur); });
      var out = $("output", box); if (out) out.textContent = prefs[key];
    });
  }
  $$("[data-pref]").forEach(function (box) {
    var key = box.getAttribute("data-pref");
    $$("[data-v]", box).forEach(function (b) {
      b.addEventListener("click", function () {
        if (key === "theme") window.__setTheme(b.getAttribute("data-v"));
        else prefs[key] = b.getAttribute("data-v");
        apply();
      });
    });
    $$("[data-d]", box).forEach(function (b) {
      b.addEventListener("click", function () {
        var min = +box.getAttribute("data-min"), max = +box.getAttribute("data-max");
        prefs[key] = Math.min(max, Math.max(min, +prefs[key] + +b.getAttribute("data-d")));
        apply();
      });
    });
  });
  document.addEventListener("themechange", apply);
  apply();

  /* bảng cài đặt */
  var sheet = $("#reader-sheet");
  $$("[data-open-sheet]").forEach(function (b) { b.addEventListener("click", function () { sheet.classList.add("open"); sheet.setAttribute("aria-hidden", "false"); }); });
  $$("[data-close-sheet]").forEach(function (b) { b.addEventListener("click", function () { sheet.classList.remove("open"); sheet.setAttribute("aria-hidden", "true"); }); });
  $$("[data-scroll-top]").forEach(function (b) { b.addEventListener("click", function () { window.scrollTo({ top: 0, behavior: "smooth" }); }); });

  /* phím tắt */
  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    var t = e.target.tagName;
    if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT" || e.target.isContentEditable) return;
    if (document.querySelector("dialog[open]")) return;
    if (e.key === "ArrowLeft") { var p = $("[data-prev]"); if (p) location.href = p.href; }
    else if (e.key === "ArrowRight") { var n = $("[data-next]"); if (n) location.href = n.href; }
    else if (e.key === "Escape") { sheet.classList.remove("open"); }
  });

  /* thanh tiến độ + lưu chương đang đọc */
  var bar = $("#progress"), text = $("#chap-text");
  var sid = reader.getAttribute("data-story-id"), n = parseInt(reader.getAttribute("data-n"), 10);
  var saved = false;
  function save() {
    var h = store.get("history", {});
    h[sid] = { n: n, slug: reader.getAttribute("data-slug"), title: reader.getAttribute("data-title"), t: Date.now() };
    store.set("history", h);
  }
  save();
  function onScroll() {
    var r = text.getBoundingClientRect();
    var total = r.height - window.innerHeight * 0.6;
    var pct = total > 0 ? Math.min(1, Math.max(0, -r.top / total)) : 1;
    bar.style.width = (pct * 100).toFixed(1) + "%";
    if (!saved && pct > 0.85 && reader.getAttribute("data-logged") === "1") {
      saved = true;
      fetch("/api/progress", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ story_id: +sid, n: n }) }).catch(function () {});
    }
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* ô chọn chương: tải đủ danh sách khi người dùng bấm vào */
  var loaded = false;
  function loadAll(sel) {
    if (loaded) return; loaded = true;
    fetch("/api/stories/" + sid + "/toc").then(function (r) { return r.json(); }).then(function (d) {
      $$("[data-chapter-select]").forEach(function (s) {
        var frag = document.createDocumentFragment();
        d.chapters.forEach(function (c) {
          var o = document.createElement("option"); o.value = c[0];
          var t = c[1] && c[1].length > 60 ? c[1].slice(0, 59) + "…" : c[1];
          o.textContent = "Chương " + c[0] + (t ? ": " + t : "");
          if (c[0] === n) o.selected = true;
          frag.appendChild(o);
        });
        s.innerHTML = ""; s.appendChild(frag);
      });
    }).catch(function () { loaded = false; });
  }
  $$("[data-chapter-select]").forEach(function (s) {
    s.addEventListener("focus", function () { loadAll(s); });
    s.addEventListener("mousedown", function () { loadAll(s); });
    s.addEventListener("change", function () { location.href = "/truyen/" + s.getAttribute("data-slug") + "/chuong-" + s.value; });
  });
})();
