/* Live discover: /api/discover -> resultaten opslaan als referentie/topic. */
(function () {
  "use strict";

  var form = document.getElementById("discover-form");
  var input = document.getElementById("discover-q");
  var resultsEl = document.getElementById("discover-results");
  var statusEl = document.getElementById("discover-status");
  var categoryEl = document.getElementById("discover-category");
  if (!form || !input || !resultsEl) return;

  var categories = [];

  function t(key) { return window.I18N ? window.I18N.t(key) : key; }
  function lang() { return window.I18N ? window.I18N.getLang() : "en"; }
  function tip(key) { return window.I18N && window.I18N.tipAttr ? window.I18N.tipAttr(key) : ""; }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fillCategories() {
    if (!categoryEl) return;
    var current = categoryEl.value;
    categoryEl.innerHTML = "";
    categories.forEach(function (cat) {
      var option = document.createElement("option");
      option.value = cat.id;
      var title = cat.title || {};
      option.textContent = title[lang()] || title.en || cat.id;
      categoryEl.appendChild(option);
    });
    if (current) categoryEl.value = current;
  }

  fetch("/data/categories.json")
    .then(function (res) { return res.json(); })
    .then(function (data) {
      categories = data.categories || [];
      fillCategories();
    })
    .catch(function () { /* leeg = geen categorieen om in te bewaren */ });

  document.addEventListener("langchange", fillCategories);

  function selectedSources() {
    var boxes = form.querySelectorAll('input[name="src"]:checked');
    var out = [];
    for (var i = 0; i < boxes.length; i++) out.push(boxes[i].value);
    return out.join(",");
  }

  function setStatus(message) {
    if (statusEl) statusEl.textContent = message || "";
  }

  function post(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.text().then(function (text) {
        var data;
        try {
          data = JSON.parse(text);
        } catch (e) {
          var bad = new Error(t("not_json") + " (HTTP " + res.status + ")");
          bad.nonJson = true;
          throw bad;
        }
        if (!res.ok) {
          var err = new Error(data.error || res.status);
          err.forbidden = res.status === 403;
          throw err;
        }
        return data;
      });
    });
  }

  function render(results, query) {
    resultsEl.innerHTML = "";
    if (!results.length) {
      setStatus(t("no_results"));
      return;
    }
    setStatus(results.length + " " + t("results") + (query ? ' · "' + query + '"' : ""));
    results.forEach(function (item) {
      var node = document.createElement("div");
      node.className = "result discover-item";
      node.innerHTML =
        '<div class="result-title">' + escapeHtml(item.title) + "</div>" +
        '<div class="result-meta">' +
        "<span>" + escapeHtml(item.source) + "</span>" +
        (item.points ? "<span>" + escapeHtml(String(item.points)) + (item.source === "github" ? "★" : " pts") + "</span>" : "") +
        (item.created_at ? "<span>" + escapeHtml(item.created_at) + "</span>" : "") +
        "</div>" +
        (item.snippet ? '<div class="result-desc">' + escapeHtml(item.snippet) + "</div>" : "") +
        '<div class="discover-actions">' +
        '<a class="btn btn-ghost btn-sm" href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener noreferrer" ' + tip("tip_open") + ">" + t("open") + " ↗</a>" +
        '<button type="button" class="btn btn-sm" data-act="ref" ' + tip("tip_ref") + ">" + t("as_reference") + "</button>" +
        '<button type="button" class="btn btn-sm btn-ghost" data-act="topic" ' + tip("tip_topic") + ">" + t("as_topic") + "</button>" +
        "</div>";
      node.querySelector('[data-act="ref"]').addEventListener("click", function (event) {
        save(event.target, "reference", item, query);
      });
      node.querySelector('[data-act="topic"]').addEventListener("click", function (event) {
        save(event.target, "topic", item, query);
      });
      resultsEl.appendChild(node);
    });
  }

  function save(button, kind, item, query) {
    var category = categoryEl ? categoryEl.value : "";
    if (!category) {
      setStatus(t("load_error"));
      return;
    }
    button.disabled = true;
    var request;
    if (kind === "reference") {
      request = post("/api/references", {
        url: item.url,
        title: item.title,
        category: category,
        note: (item.snippet || "").slice(0, 300)
      }).then(function () { return t("saved_reference"); });
    } else {
      request = post("/api/topics", {
        category_id: category,
        title_en: item.title.slice(0, 80),
        query: (query || item.title).slice(0, 120)
      }).then(function () { return t("saved_topic"); });
    }
    request
      .then(function (message) {
        button.textContent = t("saved");
        setStatus(message);
      })
      .catch(function (err) {
        button.disabled = false;
        setStatus(err.forbidden ? t("forbidden") : t("save_failed") + ": " + err.message);
      });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var query = input.value.trim();
    var sources = selectedSources();
    if (!sources) {
      setStatus(t("no_results"));
      return;
    }
    setStatus(t("rating_loading"));
    resultsEl.innerHTML = "";
    var params = "?sources=" + encodeURIComponent(sources) + "&limit=10" +
      (query ? "&q=" + encodeURIComponent(query) : "");
    fetch("/api/discover" + params)
      .then(function (res) {
        return res.text().then(function (text) {
          var data;
          try {
            data = JSON.parse(text);
          } catch (e) {
            var bad = new Error(t("not_json") + " (HTTP " + res.status + ")");
            bad.nonJson = true;
            throw bad;
          }
          if (!res.ok) {
            var err = new Error(data.error || res.status);
            err.forbidden = res.status === 403;
            throw err;
          }
          return data;
        });
      })
      .then(function (data) {
        render(data.results || [], query);
        if (data.errors && data.errors.length) {
          setStatus((statusEl.textContent ? statusEl.textContent + " · " : "") + data.errors.join(" · "));
        }
      })
      .catch(function (err) {
        setStatus(err.forbidden ? t("forbidden") : t("save_failed") + ": " + err.message);
      });
  });

  input.placeholder = t("discover_placeholder");
  document.addEventListener("langchange", function () {
    input.placeholder = t("discover_placeholder");
  });
})();
