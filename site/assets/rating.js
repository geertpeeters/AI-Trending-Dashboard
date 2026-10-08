/* 5-sterren beoordeling: GET/POST /api/rating. */
(function () {
  "use strict";

  var mount = document.querySelector(".rating");
  if (!mount) return;

  var slug = mount.getAttribute("data-slug");
  var stars = Array.prototype.slice.call(mount.querySelectorAll(".star"));
  var statusEl = mount.querySelector("[data-rating-status]");
  var avgEl = mount.querySelector("[data-rating-avg]");
  var countEl = mount.querySelector("[data-rating-count]");
  var histEl = mount.querySelector("[data-rating-hist]");
  var myScore = 0;

  function t(key) { return window.I18N ? window.I18N.t(key) : key; }

  function paint(value) {
    stars.forEach(function (star) {
      var v = parseInt(star.getAttribute("data-value"), 10);
      star.classList.toggle("is-on", v <= value);
      star.classList.toggle("is-hover", v <= value && value > myScore);
    });
  }

  function renderStats(data) {
    var avg = data.average;
    avgEl.textContent = avg === null || avg === undefined ? "—" : Number(avg).toFixed(1);
    countEl.textContent = (data.count || 0) + " " + t("rating_votes");
    if (data.my_score) {
      myScore = data.my_score;
      statusEl.textContent = t("rating_yours") + ": " + myScore + "/5";
    } else {
      statusEl.textContent = data.count ? "" : t("rating_none");
    }
    paint(myScore);

    histEl.innerHTML = "";
    for (var i = 5; i >= 1; i--) {
      var value = (data.histogram && data.histogram[String(i)]) || 0;
      var total = data.count || 0;
      var pct = total ? Math.round((value / total) * 100) : 0;
      var row = document.createElement("div");
      row.className = "hist-row";
      row.innerHTML =
        "<span>" + i + " ★</span>" +
        '<span class="hist-bar"><span style="width:' + pct + '%"></span></span>' +
        '<span class="hist-count">' + value + "</span>";
      histEl.appendChild(row);
    }
  }

  stars.forEach(function (star) {
    var value = parseInt(star.getAttribute("data-value"), 10);
    star.addEventListener("mouseenter", function () { paint(value); });
    star.addEventListener("mouseleave", function () { paint(myScore); });
    star.addEventListener("focus", function () { paint(value); });
    star.addEventListener("blur", function () { paint(myScore); });
    star.addEventListener("click", function () { submit(value); });
  });

  function submit(value) {
    statusEl.textContent = t("rating_loading");
    fetch("/api/rating", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: slug, score: value })
    })
      .then(function (res) { return res.json().then(function (body) { return { ok: res.ok, body: body }; }); })
      .then(function (out) {
        if (!out.ok) {
          statusEl.textContent = t("rating_error") + (out.body && out.body.error ? ": " + out.body.error : "");
          return;
        }
        myScore = out.body.my_score;
        renderStats(out.body);
        statusEl.textContent = t("rating_saved") + " · " + t("rating_yours") + ": " + myScore + "/5";
      })
      .catch(function () { statusEl.textContent = t("rating_error"); });
  }

  fetch("/api/rating?slug=" + encodeURIComponent(slug))
    .then(function (res) { return res.json(); })
    .then(renderStats)
    .catch(function () { statusEl.textContent = t("error_load"); });
})();
