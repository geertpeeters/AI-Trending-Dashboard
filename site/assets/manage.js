/* Beheerpagina: referenties, topics, bronnen, changelog + crawl-trigger. */
(function () {
  "use strict";

  var statusEl = document.getElementById("manage-status");
  var crawlStatusEl = document.getElementById("crawl-status");
  var data = { categories: [], references: [], changelog: [] };

  function t(key) { return window.I18N ? window.I18N.t(key) : key; }
  function lang() { return window.I18N ? window.I18N.getLang() : "en"; }
  function tip(key) { return window.I18N && window.I18N.tipAttr ? window.I18N.tipAttr(key) : ""; }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function localizedTitle(title) {
    title = title || {};
    return title[lang()] || title.en || title.nl || "";
  }

  function setStatus(message, isError) {
    if (statusEl) {
      statusEl.textContent = message || "";
      statusEl.classList.toggle("is-error", !!isError);
    }
  }

  function request(method, url, payload) {
    return fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: payload ? JSON.stringify(payload) : undefined
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
          var err = new Error(data.error || String(res.status));
          err.forbidden = res.status === 403;
          throw err;
        }
        return data;
      });
    });
  }

  function errorMessage(err) {
    if (err.forbidden) return t("forbidden");
    return t("save_failed") + ": " + err.message;
  }

  /* ---------- renderen ---------- */
  function render() {
    fillCategorySelects();
    renderReferences();
    renderTopics();
    renderSources();
    renderChangelog();
  }

  function fillCategorySelects() {
    var selects = document.querySelectorAll("[data-category-select]");
    for (var i = 0; i < selects.length; i++) {
      var select = selects[i];
      var current = select.value;
      select.innerHTML = "";
      data.categories.forEach(function (cat) {
        var option = document.createElement("option");
        option.value = cat.id;
        option.textContent = localizedTitle(cat.title);
        select.appendChild(option);
      });
      if (current) select.value = current;
    }
  }

  function renderReferences() {
    var list = document.getElementById("list-references");
    if (!list) return;
    if (!data.references.length) {
      list.innerHTML = '<p class="empty">' + escapeHtml(t("nothing_here")) + "</p>";
      return;
    }
    list.innerHTML = "";
    data.references.slice().reverse().forEach(function (ref) {
      var node = document.createElement("div");
      node.className = "manage-row";
      node.innerHTML =
        '<div class="manage-row-main">' +
        '<a class="result-title" href="' + escapeHtml(ref.url) + '" target="_blank" rel="noopener noreferrer">' +
        escapeHtml(ref.title || ref.url) + " ↗</a>" +
        '<div class="result-meta"><span>' + escapeHtml(ref.category) + "</span>" +
        (ref.added_at ? "<span>" + escapeHtml(String(ref.added_at).slice(0, 10)) + "</span>" : "") +
        "</div>" +
        (ref.note ? '<div class="result-desc">' + escapeHtml(ref.note) + "</div>" : "") +
        "</div>" +
        '<button type="button" class="btn btn-sm btn-ghost" data-remove ' + tip("tip_remove") + ">" + escapeHtml(t("remove")) + "</button>";
      node.querySelector("[data-remove]").addEventListener("click", function (event) {
        removeItem(event.target, "/api/references", { id: ref.id });
      });
      list.appendChild(node);
    });
  }

  function renderTopics() {
    var wrap = document.getElementById("list-topics");
    if (!wrap) return;
    wrap.innerHTML = "";
    data.categories.forEach(function (cat) {
      wrap.appendChild(groupBlock(cat, cat.topics || [], function (topic) {
        return {
          label: localizedTitle(topic.title) + (topic.ai_generated ? " · AI" : ""),
          meta: topic.query || topic.id,
          remove: { category_id: cat.id, topic_id: topic.id }
        };
      }, "/api/topics"));
    });
  }

  function renderSources() {
    var wrap = document.getElementById("list-sources");
    if (!wrap) return;
    wrap.innerHTML = "";
    data.categories.forEach(function (cat) {
      wrap.appendChild(groupBlock(cat, cat.sources || [], function (source) {
        return {
          label: source.type,
          meta: (source.query || source.url || "") + (source.limit ? " · limit " + source.limit : ""),
          remove: { category: cat.id, index: source.index }
        };
      }, "/api/sources", true));
    });
  }

  function groupBlock(cat, entries, describe, endpoint, deleteIsSource) {
    var block = document.createElement("div");
    block.className = "manage-group";
    var head = document.createElement("h3");
    head.className = "manage-group-title";
    head.textContent = localizedTitle(cat.title) + " (" + entries.length + ")";
    block.appendChild(head);

    if (!entries.length) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = t("none");
      block.appendChild(empty);
      return block;
    }

    entries.forEach(function (entry) {
      var view = describe(entry);
      var row = document.createElement("div");
      row.className = "manage-row";
      row.innerHTML =
        '<div class="manage-row-main">' +
        '<span class="result-title">' + escapeHtml(view.label) + "</span>" +
        (view.meta ? '<div class="result-meta"><span>' + escapeHtml(view.meta) + "</span></div>" : "") +
        "</div>" +
        '<button type="button" class="btn btn-sm btn-ghost" data-remove ' + tip("tip_remove") + ">" + escapeHtml(t("remove")) + "</button>";
      row.querySelector("[data-remove]").addEventListener("click", function (event) {
        removeItem(event.target, endpoint, view.remove);
      });
      block.appendChild(row);
    });
    return block;
  }

  function renderChangelog() {
    var list = document.getElementById("list-changelog");
    if (!list) return;
    if (!data.changelog.length) {
      list.innerHTML = '<p class="empty">' + escapeHtml(t("nothing_here")) + "</p>";
      return;
    }
    list.innerHTML = "";
    data.changelog.slice().reverse().forEach(function (entry) {
      var node = document.createElement("div");
      node.className = "manage-row";
      var detail = "";
      if (entry.action === "add_topics" && entry.topics && entry.topics.length) {
        detail = entry.topics.map(function (topic) {
          return (topic.title && (topic.title.en || topic.title.nl)) || topic.topic_id || "";
        }).join(", ");
      } else if (entry.source) {
        detail = entry.source.type + " · " + (entry.source.query || entry.source.url || "");
      } else if (entry.reference) {
        detail = entry.reference.title || entry.reference.url || "";
      }
      node.innerHTML =
        '<div class="manage-row-main">' +
        '<span class="result-title">' + escapeHtml(entry.action || "") + "</span>" +
        '<div class="result-meta"><span>' + escapeHtml(entry.actor || "") + "</span>" +
        "<span>" + escapeHtml(String(entry.ts || "").slice(0, 16).replace("T", " ")) + "</span>" +
        (detail ? "<span>" + escapeHtml(detail) + "</span>" : "") +
        "</div></div>";
      list.appendChild(node);
    });
  }

  /* ---------- acties ---------- */
  function reload() {
    return request("GET", "/api/manage").then(function (payload) {
      data.categories = payload.categories || [];
      data.references = payload.references || [];
      data.changelog = payload.changelog || [];
      render();
    }).catch(function (err) {
      setStatus(errorMessage(err), true);
    });
  }

  function removeItem(button, endpoint, payload) {
    button.disabled = true;
    request("DELETE", endpoint, payload)
      .then(function () {
        setStatus(t("removed_ok"));
        return reload();
      })
      .catch(function (err) {
        button.disabled = false;
        setStatus(errorMessage(err), true);
      });
  }

  function formPayload(form) {
    var payload = {};
    var fields = form.querySelectorAll("input, select, textarea");
    for (var i = 0; i < fields.length; i++) {
      var field = fields[i];
      if (!field.name) continue;
      if (field.type === "number") {
        payload[field.name] = field.value === "" ? undefined : Number(field.value);
      } else {
        payload[field.name] = field.value.trim();
      }
    }
    return payload;
  }

  function wireForm(id, endpoint, successKey) {
    var form = document.getElementById(id);
    if (!form) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var payload = formPayload(form);
      request("POST", endpoint, payload)
        .then(function () {
          setStatus(t(successKey));
          form.reset();
          return reload();
        })
        .catch(function (err) {
          setStatus(errorMessage(err), true);
        });
    });
  }

  wireForm("form-reference", "/api/references", "saved_reference");
  wireForm("form-topic", "/api/topics", "saved_topic");
  wireForm("form-source", "/api/sources", "added_ok");

  var crawlButton = document.getElementById("btn-crawl");
  if (crawlButton) {
    crawlButton.addEventListener("click", function () {
      var statusEl = document.getElementById("crawl-status");
      crawlButton.disabled = true;
      if (statusEl) statusEl.textContent = t("rating_loading");
      request("POST", "/api/rebuild", {})
        .then(function () {
          if (statusEl) statusEl.textContent = t("crawling");
          crawlButton.disabled = false;
        })
        .catch(function (err) {
          if (statusEl) statusEl.textContent = errorMessage(err);
          crawlButton.disabled = false;
        });
    });
  }

  document.addEventListener("langchange", render);
  reload();
})();
