/* Client-side search over /data/search-index.json. */
(function () {
  "use strict";

  var input = document.getElementById("q");
  var resultsEl = document.getElementById("results");
  var countEl = document.getElementById("count");
  var updatedEl = document.getElementById("search-updated");
  if (!input || !resultsEl) return;

  var index = [];
  var loaded = false;

  function t(key) { return window.I18N ? window.I18N.t(key) : key; }
  function lang() { return window.I18N ? window.I18N.getLang() : "en"; }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function normalize(value) {
    return String(value || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }

  function score(item, terms) {
    var hay = normalize([
      item.title,
      item.summary_en,
      item.summary_nl,
      item.purpose_en,
      item.purpose_nl,
      (item.tags || []).join(" "),
      item.category,
      item.source
    ].join(" "));
    var total = 0;
    for (var i = 0; i < terms.length; i++) {
      var term = terms[i];
      if (!term) continue;
      var position = hay.indexOf(term);
      if (position === -1) return 0;
      total += 1;
      if (normalize(item.title).indexOf(term) !== -1) total += 3;
      if ((item.tags || []).some(function (tag) { return normalize(tag).indexOf(term) !== -1; })) total += 2;
    }
    return total;
  }

  function render(items, terms) {
    resultsEl.innerHTML = "";
    if (!loaded) return;
    if (!terms.length) {
      countEl.textContent = index.length + " " + t("items") + " · " + t("type_to_search");
      return;
    }
    countEl.textContent = items.length + (items.length === 1 ? " " + t("results_one") : " " + t("results"));
    if (!items.length) {
      resultsEl.innerHTML = '<div class="result"><div class="result-desc">' + escapeHtml(t("no_results")) + "</div></div>";
      return;
    }
    var currentLang = lang();
    items.slice(0, 100).forEach(function (item) {
      var summary = currentLang === "nl" ? (item.summary_nl || item.summary_en) : (item.summary_en || item.summary_nl);
      var purpose = currentLang === "nl" ? (item.purpose_nl || item.purpose_en) : (item.purpose_en || item.purpose_nl);
      if (summary && summary.length > 240) summary = summary.slice(0, 237).replace(/\s+\S*$/, "") + "…";
      var node = document.createElement("a");
      node.className = "result";
      node.href = "/items/" + encodeURIComponent(item.slug) + ".html";
      node.innerHTML =
        '<div class="result-title">' + escapeHtml(item.title) + "</div>" +
        '<div class="result-meta">' +
        "<span>" + escapeHtml(item.category) + "</span>" +
        "<span>" + escapeHtml(item.source) + "</span>" +
        (item.kind ? "<span>" + escapeHtml(item.kind) + "</span>" : "") +
        "<span>trend " + Math.round(item.trend || 0) + "</span>" +
        "<span>rel " + (item.relevance || 0) + "/10</span>" +
        "</div>" +
        (purpose ? '<div class="result-purpose">' + escapeHtml(purpose) + "</div>" : "") +
        (summary ? '<div class="result-desc">' + escapeHtml(summary) + "</div>" : "");
      resultsEl.appendChild(node);
    });
  }

  function search() {
    var terms = normalize(input.value).split(/\s+/).filter(Boolean);
    if (!terms.length) { render([], []); return; }
    var ranked = [];
    index.forEach(function (item) {
      var value = score(item, terms);
      if (value > 0) ranked.push({ item: item, value: value });
    });
    ranked.sort(function (a, b) { return b.value - a.value || (b.item.trend || 0) - (a.item.trend || 0); });
    render(ranked.map(function (entry) { return entry.item; }), terms);
  }

  fetch("/data/search-index.json")
    .then(function (res) { return res.json(); })
    .then(function (data) {
      index = Array.isArray(data) ? data : [];
      loaded = true;
      if (updatedEl) updatedEl.textContent = new Date().toISOString().slice(0, 16).replace("T", " ") + "Z";
      render([], []);
      if (input.value) search();
    })
    .catch(function () {
      countEl.textContent = t("error_load");
    });

  input.addEventListener("input", search);
  document.addEventListener("langchange", function () { search(); });
})();
