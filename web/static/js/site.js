/* JS chung: giao diện sáng/tối, menu, modal ủng hộ, theo dõi/đánh giá không tải lại trang, lịch sử đọc cục bộ. */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var store = {
    get: function (k, d) { try { var v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  };
  window.__store = store;

  function toast(msg) {
    var old = $(".toast"); if (old) old.remove();
    var t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg;
    document.body.appendChild(t); autoHide(t);
  }
  window.__toast = toast;
  function autoHide(t) { setTimeout(function () { t.classList.add("hide"); setTimeout(function () { t.remove(); }, 400); }, 3800); }
  var ft = $(".toast"); if (ft) autoHide(ft);

  /* ---- giao diện ---- */
  function setTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("theme", t); } catch (e) {}
    document.dispatchEvent(new CustomEvent("themechange", { detail: t }));
  }
  window.__setTheme = setTheme;
  $$("[data-theme-toggle]").forEach(function (b) {
    b.addEventListener("click", function () {
      setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
    });
  });

  /* ---- dropdown ---- */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-dd]");
    $$(".dd.open").forEach(function (d) { if (!btn || d !== btn.parentElement) d.classList.remove("open"); });
    if (btn) { btn.parentElement.classList.toggle("open"); e.stopPropagation(); }
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") $$(".dd.open").forEach(function (d) { d.classList.remove("open"); }); });

  /* ---- menu điện thoại, ô tìm kiếm ---- */
  var drawer = $("#drawer");
  $$("[data-open-drawer]").forEach(function (b) { b.addEventListener("click", function () { drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); }); });
  $$("[data-close-drawer]").forEach(function (b) { b.addEventListener("click", function () { drawer.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); }); });
  var ms = $("#mobile-search");
  $$("[data-toggle-search]").forEach(function (b) {
    b.addEventListener("click", function () { ms.classList.toggle("open"); if (ms.classList.contains("open")) $("input", ms).focus(); });
  });

  /* ---- xác nhận trước khi gửi form ---- */
  document.addEventListener("submit", function (e) {
    var f = e.target, sub = e.submitter;
    var msg = (sub && sub.getAttribute("data-confirm")) || f.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    var p = f.getAttribute("data-prompt");
    if (p) {
      var v = window.prompt(p);
      if (!v) { e.preventDefault(); return; }
      f.querySelector("[name=confirm]").value = v.trim();
    }
  }, true);

  /* ---- chép ---- */
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy]"); if (!b) return;
    var text = b.getAttribute("data-copy");
    var done = function () { var o = b.textContent; b.classList.add("done"); b.textContent = "Đã chép"; setTimeout(function () { b.classList.remove("done"); b.textContent = o; }, 1500); };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () { window.prompt("Chép nội dung:", text); });
    else window.prompt("Chép nội dung:", text);
  });

  /* ---- modal ủng hộ + mã VietQR ---- */
  function qrUrl(box, amount) {
    var bank = box.getAttribute("data-bank"), acc = box.getAttribute("data-account");
    if (!bank || !acc) return "";
    var u = "https://img.vietqr.io/image/" + encodeURIComponent(bank) + "-" + encodeURIComponent(acc) + "-compact2.png";
    var p = [];
    if (amount) p.push("amount=" + Math.round(amount));
    var note = box.getAttribute("data-note"); if (note) p.push("addInfo=" + encodeURIComponent(note));
    var name = box.getAttribute("data-holder"); if (name) p.push("accountName=" + encodeURIComponent(name));
    return u + (p.length ? "?" + p.join("&") : "");
  }
  function bindDonate(scope, box) {
    if (!scope || !box) return;
    var img = $("[data-qr]", scope), input = $("[data-amount-input]", scope), claim = $("[data-claim-amount]", scope.closest("dialog") || document);
    var timer;
    function setAmount(a) {
      if (img) { var u = qrUrl(box, a); if (u) img.src = u; }
      if (claim && a) claim.value = a;
    }
    $$("[data-amount]", scope).forEach(function (b) {
      b.addEventListener("click", function () {
        $$("[data-amount]", scope).forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on"); if (input) input.value = "";
        setAmount(parseInt(b.getAttribute("data-amount"), 10));
      });
    });
    if (input) input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var a = parseInt(input.value, 10);
        if (a >= 1000) { $$("[data-amount]", scope).forEach(function (x) { x.classList.remove("on"); }); setAmount(a); }
      }, 400);
    });
  }
  var dlg = $("#donate-modal");
  if (dlg) {
    bindDonate(dlg, dlg);
    $$("[data-open-donate]").forEach(function (b) {
      b.addEventListener("click", function () { if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", ""); });
    });
    $$("[data-close-dialog]", dlg).forEach(function (b) { b.addEventListener("click", function () { dlg.close(); }); });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); });
  }
  var inline = $("[data-inline-donate]");
  if (inline) bindDonate(inline.parentElement, inline);

  /* ---- theo dõi, đánh giá (không tải lại trang) ---- */
  function ajaxForm(form, onDone) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var fd = new FormData(form);
      if (e.submitter && e.submitter.name) fd.append(e.submitter.name, e.submitter.value);
      fetch(form.action, { method: "POST", body: fd, headers: { "Accept": "application/json" }, credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw r; return r.json(); })
        .then(onDone)
        .catch(function () { form.submit(); });
    });
  }
  var ff = $("[data-ajax-follow]");
  if (ff) ajaxForm(ff, function (d) {
    var b = $("button", ff); b.classList.toggle("is-on", d.following);
    $("span", b).textContent = d.following ? "Đang theo dõi" : "Theo dõi";
    var c = $("[data-follow-count]"); if (c) c.textContent = d.count;
    toast(d.following ? "Đã thêm vào tủ truyện" : "Đã bỏ theo dõi");
  });
  var rf = $("[data-ajax-rate]");
  if (rf) ajaxForm(rf, function (d) {
    $$("button", rf).forEach(function (b) { b.classList.toggle("on", parseInt(b.value, 10) <= d.stars); });
    toast("Bạn đã chấm " + d.stars + " sao. Điểm trung bình: " + d.avg);
  });

  /* ---- giới thiệu dài: thu gọn ---- */
  $$("[data-collapse]").forEach(function (w) {
    var d = $(".desc", w);
    if (d.scrollHeight > 260) {
      w.classList.add("collapsed");
      var b = document.createElement("button"); b.type = "button"; b.className = "desc-toggle"; b.textContent = "Xem thêm";
      b.addEventListener("click", function () { var c = w.classList.toggle("collapsed"); b.textContent = c ? "Xem thêm" : "Thu gọn"; });
      w.appendChild(b);
    }
  });

  /* ---- trang truyện: "Đọc tiếp" theo lịch sử trên máy ---- */
  var sa = $("[data-story-actions]");
  if (sa) {
    var sid = sa.getAttribute("data-story-id");
    var h = store.get("history", {})[sid];
    var cont = $("[data-read-continue]", sa);
    if (cont && h && h.n && !sa.getAttribute("data-server-n")) {
      cont.href = "/truyen/" + sa.getAttribute("data-slug") + "/chuong-" + h.n;
      $("span", cont).textContent = "Đọc tiếp chương " + h.n;
      cont.classList.remove("hidden");
    }
    var readN = (h && h.n) || parseInt(sa.getAttribute("data-server-n") || "0", 10);
    if (readN) $$("[data-chapter-list] a").forEach(function (a) {
      var n = parseInt(a.getAttribute("data-n"), 10);
      if (n === readN) a.classList.add("cur"); else if (n < readN) a.classList.add("read");
    });
  }

  /* ---- tủ truyện khi chưa đăng nhập: lịch sử trên máy ---- */
  var lh = $("[data-local-history]");
  if (lh) {
    var all = store.get("history", {});
    var items = Object.keys(all).map(function (k) { return all[k]; }).sort(function (a, b) { return b.t - a.t; });
    if (items.length) {
      lh.innerHTML = "";
      items.slice(0, 60).forEach(function (it) {
        var li = document.createElement("li");
        li.style.gridTemplateColumns = "minmax(0,1fr) auto";
        var left = document.createElement("div"); left.style.minWidth = "0";
        var a = document.createElement("a"); a.className = "t clamp-1"; a.href = "/truyen/" + it.slug; a.textContent = it.title;
        var c = document.createElement("div"); c.className = "c";
        var ca = document.createElement("a"); ca.href = "/truyen/" + it.slug + "/chuong-" + it.n; ca.textContent = "Đọc tiếp chương " + it.n;
        c.appendChild(ca); left.appendChild(a); left.appendChild(c);
        var w = document.createElement("div"); w.className = "when"; w.textContent = new Date(it.t).toLocaleDateString("vi-VN");
        li.appendChild(left); li.appendChild(w); lh.appendChild(li);
      });
    }
  }

  /* ---- carousel trang chủ ---- */
  var slides = $("#slides"), dots = $("#dots");
  if (slides && dots) {
    var btns = $$("button", dots), idx = 0, timerS;
    var go = function (i) { idx = (i + btns.length) % btns.length; slides.scrollTo({ left: slides.clientWidth * idx, behavior: "smooth" }); };
    btns.forEach(function (b, i) { b.addEventListener("click", function () { go(i); restart(); }); });
    slides.addEventListener("scroll", function () {
      var i = Math.round(slides.scrollLeft / Math.max(1, slides.clientWidth));
      btns.forEach(function (b, j) { b.classList.toggle("on", i === j); }); idx = i;
    }, { passive: true });
    var restart = function () { clearInterval(timerS); timerS = setInterval(function () { if (!document.hidden) go(idx + 1); }, 6000); };
    slides.addEventListener("pointerdown", function () { clearInterval(timerS); });
    restart();
  }

  /* ---- tabs ---- */
  $$("[data-tabs]").forEach(function (box) {
    $$("[data-tab]", box).forEach(function (b) {
      b.addEventListener("click", function () {
        $$("[data-tab]", box).forEach(function (x) { x.classList.toggle("on", x === b); });
        $$("[data-pane]", box).forEach(function (p) { p.classList.toggle("hidden", p.getAttribute("data-pane") !== b.getAttribute("data-tab")); });
      });
    });
  });

  /* ---- form truyện: chỉ hiện ô tên gốc khi là truyện dịch ---- */
  var origin = $("[data-origin]");
  if (origin) {
    var tr = $("[data-translated-only]");
    var upd = function () { tr.classList.toggle("hidden", origin.value !== "translated"); };
    origin.addEventListener("change", upd); upd();
  }

  /* ---- chọn tất cả ---- */
  var ca = $("[data-check-all]");
  if (ca) ca.addEventListener("change", function () { $$("input[name=n]", ca.closest("form")).forEach(function (c) { c.checked = ca.checked; }); });

  /* ---- đếm chữ trong trình soạn chương ---- */
  var ed = $("[data-editor]");
  if (ed) {
    var ta = $("textarea[name=content]", ed), counter = $("[data-counter]", ed);
    var count = function () { var w = (ta.value.match(/[\p{L}\p{N}]+/gu) || []).length; counter.textContent = w.toLocaleString("vi-VN") + " chữ"; };
    ta.addEventListener("input", count); count();
    var dirty = false; ta.addEventListener("input", function () { dirty = true; });
    ed.addEventListener("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  }

  /* ---- trang việc dịch: tự cập nhật tiến độ ---- */
  var job = $("[data-job]");
  if (job) {
    var st = job.getAttribute("data-job-status");
    var poll = function () {
      fetch("/nhap-truyen/" + job.getAttribute("data-job") + "/trang-thai.json", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var set = function (sel, v) { var el = $(sel, job); if (el) el.textContent = v; };
          set("[data-st-text]", d.status_text); set("[data-st-msg]", d.message); set("[data-st-done]", d.done);
          set("[data-st-total]", d.total); set("[data-st-target]", d.target); set("[data-st-cost]", "$" + d.cost.toFixed(3));
          var bar = $("[data-st-bar]", job); if (bar) bar.style.width = (d.total ? d.done / d.total * 100 : 0) + "%";
          var dot = $("[data-st-dot]", job); if (dot) dot.className = "status-dot " + d.status;
          if (d.status !== st && (d.status === "done" || d.status === "error" || st === "queued")) { location.reload(); return; }
          if (d.status === "running" || d.status === "queued") setTimeout(poll, 4000);
        }).catch(function () { setTimeout(poll, 10000); });
    };
    if (st === "running" || st === "queued") setTimeout(poll, 3000);
  }
})();
