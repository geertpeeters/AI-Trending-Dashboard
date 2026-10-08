/* Tabs (kaartenbak), taalswitcher, fade-edges. */
(function () {
  "use strict";

  var isIndex = document.body.getAttribute("data-page") === "index";

  /* --- taal --- */
  function syncLangButtons() {
    var lang = window.I18N ? window.I18N.getLang() : "en";
    document.documentElement.lang = lang;
    var buttons = document.querySelectorAll(".lang-btn");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.toggle("is-active", buttons[i].getAttribute("data-lang") === lang);
    }
  }
  document.addEventListener("click", function (event) {
    var btn = event.target.closest ? event.target.closest(".lang-btn") : null;
    if (btn && window.I18N) {
      window.I18N.setLang(btn.getAttribute("data-lang"));
    }
  });
  syncLangButtons();

  /* --- tabs --- */
  var tabsWrap = document.querySelector(".tabs-wrap");
  var tabsBar = document.getElementById("tabs");
  var tabs = tabsBar ? Array.prototype.slice.call(tabsBar.querySelectorAll(".tab")) : [];

  function setActiveTab(cat) {
    tabs.forEach(function (tab) {
      tab.classList.toggle("is-active", tab.getAttribute("data-cat") === cat);
    });
  }

  function applyFilter(cat) {
    var cards = document.querySelectorAll(".cat-card");
    for (var i = 0; i < cards.length; i++) {
      var match = cat === "all" || cards[i].getAttribute("data-cat") === cat;
      cards[i].style.display = match ? "" : "none";
    }
    setActiveTab(cat);
    var overview = document.getElementById("overview");
    if (overview) {
      var hidden = cat !== "all";
      var intro = document.querySelector(".intro");
      if (intro) intro.style.display = hidden ? "none" : "";
      if (hidden) {
        window.scrollTo(0, 0);
      }
    }
  }

  if (isIndex && tabs.length) {
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function (event) {
        event.preventDefault();
        var cat = tab.getAttribute("data-cat");
        applyFilter(cat);
        try {
          history.replaceState(null, "", cat === "all" ? location.pathname : "#cat-" + cat);
        } catch (e) { /* ignore */ }
      });
    });
    var initial = (location.hash || "").replace("#cat-", "");
    if (initial) {
      var valid = tabs.some(function (t) { return t.getAttribute("data-cat") === initial; });
      if (valid && initial !== "all") applyFilter(initial);
    }
  } else if (tabsBar) {
    var current = tabsBar.querySelector(".tab.is-active");
    if (current) {
      tabsBar.scrollLeft = Math.max(0, current.offsetLeft - 120);
    }
  }

  function updateFades() {
    if (!tabsBar) return;
    var left = document.querySelector(".tabs-fade--left");
    var right = document.querySelector(".tabs-fade--right");
    var scrollable = tabsBar.scrollWidth > tabsBar.clientWidth + 4;
    if (left) left.classList.toggle("is-on", scrollable && tabsBar.scrollLeft > 4);
    if (right) {
      right.classList.toggle(
        "is-on",
        scrollable && tabsBar.scrollLeft + tabsBar.clientWidth < tabsBar.scrollWidth - 4
      );
    }
  }
  if (tabsBar) {
    tabsBar.addEventListener("scroll", updateFades);
    window.addEventListener("resize", updateFades);
    updateFades();
  }

  if (tabsWrap) {
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && window.I18N) {
        var input = document.querySelector(".search-input");
        if (input && document.activeElement === input) input.blur();
      }
    });
  }

  /* --- tooltips: data-tip-en / data-tip-nl --- */
  var tipEl = document.createElement("div");
  tipEl.className = "site-tip";
  tipEl.setAttribute("aria-hidden", "true");
  document.body.appendChild(tipEl);
  var tipTarget = null;

  function tipText(el) {
    var lang = window.I18N ? window.I18N.getLang() : "en";
    var txt = el.getAttribute(lang === "nl" ? "data-tip-nl" : "data-tip-en");
    if (!txt) txt = el.getAttribute("data-tip-en") || el.getAttribute("data-tip-nl") || "";
    return txt;
  }

  function placeTip(el) {
    tipEl.style.left = "0px";
    tipEl.style.top = "0px";
    var r = el.getBoundingClientRect();
    var tr = tipEl.getBoundingClientRect();
    var left = r.left + r.width / 2 - tr.width / 2;
    var top = r.top - tr.height - 8;
    if (top < 4) top = Math.min(r.bottom + 8, window.innerHeight - tr.height - 4);
    left = Math.max(4, Math.min(left, window.innerWidth - tr.width - 4));
    tipEl.style.left = Math.round(left) + "px";
    tipEl.style.top = Math.round(top) + "px";
  }

  function showTip(el) {
    var txt = tipText(el);
    if (!txt) return;
    tipTarget = el;
    tipEl.textContent = txt;
    tipEl.classList.add("is-on");
    placeTip(el);
  }

  function hideTip() {
    tipTarget = null;
    tipEl.classList.remove("is-on");
  }

  function tipNode(node) {
    return node && node.nodeType === 1 && node.closest
      ? node.closest("[data-tip-en],[data-tip-nl]")
      : null;
  }

  document.addEventListener("mouseover", function (event) {
    var t = tipNode(event.target);
    if (t) showTip(t);
    else if (tipTarget) hideTip();
  });
  document.addEventListener("mouseout", function (event) {
    var t = tipNode(event.target);
    if (t && (!event.relatedTarget || !t.contains(event.relatedTarget))) hideTip();
  });
  document.addEventListener("focusin", function (event) {
    var t = tipNode(event.target);
    if (t) showTip(t);
    else if (tipTarget) hideTip();
  });
  document.addEventListener("focusout", hideTip);
  window.addEventListener("scroll", function () {
    if (tipTarget) placeTip(tipTarget);
  }, true);
  window.addEventListener("resize", hideTip);
  document.addEventListener("langchange", function () {
    if (!tipTarget) return;
    var txt = tipText(tipTarget);
    if (!txt) hideTip();
    else {
      tipEl.textContent = txt;
      placeTip(tipTarget);
    }
  });
})();
