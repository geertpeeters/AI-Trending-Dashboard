"""Jinja2 -> statische HTML (dashboard, categoriepagina's, item-pagina's, search-index)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from jinja2 import DictLoader, Environment, select_autoescape

from pipeline.config_loader import localized
from pipeline.scoring import build_sparkline, trend_series
from pipeline.util import atomic_write_json, atomic_write_text, ensure_dir, now_iso

NEW_WINDOW_DAYS = 3
HOT_TREND = 70

LAYOUT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ page_title }}</title>
<meta name="description" content="{{ page_desc }}">
<link rel="stylesheet" href="/assets/app.css">
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
</head>
<body data-page="{{ page }}">
<header class="site-header">
  <a class="brand" href="/index.html" data-tip-en="Back to the dashboard" data-tip-nl="Terug naar het dashboard">AI&nbsp;TRENDING<span class="cursor" aria-hidden="true">_</span></a>
  <div class="header-meta">
    <span class="meta-key">{{ m.i18n('updated', 'bijgewerkt') }}:</span>
    <span class="meta-val">{{ generated_at }}</span>
    <span class="meta-sep">|</span>
    <span class="meta-key">{{ m.i18n('items', 'items') }}:</span>
    <span class="meta-val">{{ item_count }}</span>
  </div>
  <nav class="header-actions">
    <a class="head-link" href="/search.html" data-tip-en="Search titles, tags, summaries and categories" data-tip-nl="Zoek in titels, tags, samenvattingen en categorieën">{{ m.i18n('search', 'zoek') }}</a>
    <a class="head-link" href="/manage.html" data-tip-en="Add your own references, topics and sources — writes only from your home network" data-tip-nl="Voeg eigen referenties, topics en bronnen toe — schrijven alleen vanaf je thuisnetwerk">{{ m.i18n('manage', 'beheren') }}</a>
    <a class="head-link" href="/help.html" data-tip-en="Help: how this dashboard runs and how to operate it" data-tip-nl="Help: zo draait en bedien je dit dashboard">{{ m.i18n('help', 'help') }}</a>
    <div class="lang-switch" role="group" aria-label="language">
      <button type="button" class="lang-btn" data-lang="nl" data-tip-en="Switch the site to Dutch" data-tip-nl="Zet de site op Nederlands">NL</button>
      <button type="button" class="lang-btn" data-lang="en" data-tip-en="Switch the site to English" data-tip-nl="Zet de site op Engels">EN</button>
    </div>
  </nav>
</header>

<nav class="tabs-wrap" aria-label="categories">
  <div class="tabs-fade tabs-fade--left" aria-hidden="true"></div>
  <div class="tabs" id="tabs">
    <a class="tab {{ 'is-active' if active_tab == 'all' else '' }}" data-cat="all" href="/index.html" data-tip-en="Show every category" data-tip-nl="Toon alle categorieën">{{ m.i18n('all', 'alles') }}</a>
    {% for cat in categories %}
    <a class="tab {{ 'is-active' if active_tab == cat.id else '' }}" data-cat="{{ cat.id }}" href="/categories/{{ cat.id }}.html" data-tip-en="Show this category: {{ cat.title.get('en') }}" data-tip-nl="Toon deze categorie: {{ cat.title.get('nl') }}">{{ m.loc(cat.title) }}</a>
    {% endfor %}
  </div>
  <div class="tabs-fade tabs-fade--right" aria-hidden="true"></div>
</nav>

<main id="main" class="{{ main_class }}">
{% block content %}{% endblock %}
</main>

<footer class="site-footer">
  <span>{{ m.i18n('static build', 'bouw') }}: {{ generated_at }}</span>
  <span class="meta-sep">|</span>
  <span>{{ m.i18n('sources', 'bronnen') }}: HN Algolia · GitHub · OpenRouter · arXiv · RSS</span>
  <span class="meta-sep">|</span>
  <span>{{ m.i18n('daily AI review at 06:00 UTC', 'dagelijkse AI-review om 06:00 UTC') }}</span>
</footer>

<script src="/assets/i18n.js" defer></script>
<script src="/assets/app.js" defer></script>
{% block scripts %}{% endblock %}
</body>
</html>
"""

INDEX = """{% extends "layout.html" %}
{% block content %}
<section class="intro">
  <h1>{{ m.i18n('AI trending dashboard', 'Trenddashboard AI') }}</h1>
  <p class="intro-sub">{{ m.i18n('Rebalanced daily: HN points, GitHub star velocity, recency and AI relevance.', 'Dagelijks herwogen: HN-sterren, GitHub-sterrendelta, publicatiedatum en AI-relevantie.') }}</p>
</section>

<section class="cards" id="overview">
{% for cat in cats %}
  {% set items = cat['items'] %}
  <article class="cat-card" id="cat-{{ cat.id }}" data-cat="{{ cat.id }}">
    <header class="cat-head">
      <div class="cat-title-row">
        <h2 class="cat-title"><a href="/categories/{{ cat.id }}.html" data-tip-en="Open the full category list" data-tip-nl="Open de volledige categorie-lijst">{{ m.loc(cat.title) }}</a></h2>
        <span class="badge">{{ cat.count }} {{ m.i18n('items', 'items') }}</span>
      </div>
      <p class="cat-desc">{{ m.loc(cat.description) }}</p>
    </header>
    <div class="spark-row">
      <span class="spark-label">{{ m.i18n('14d', '14d') }}</span>
      <span class="spark" aria-hidden="true">{{ cat.sparkline or '·' }}</span>
      <span class="spark-delta {{ cat.delta_class }}">{{ cat.delta_arrow }}{{ cat.delta }}</span>
    </div>
    <ol class="top-list">
    {% for item in items[:3] %}
      <li class="top-item">
        <span class="rank">{{ loop.index }}</span>
        <a class="top-title" href="/items/{{ item.slug }}.html">{{ item.title }}</a>
        <span class="badges">
          {% if item.is_new %}<span class="badge badge-inv">NEW</span>{% endif %}
          {% if item.is_hot %}<span class="badge badge-inv">HOT</span>{% endif %}
        </span>
        <span class="top-meta">
          <span class="spark spark-sm" aria-hidden="true">{{ item.sparkline or '·' }}</span>
          <span class="delta {{ item.delta_class }}">{{ item.delta_arrow }}{{ item.delta }}</span>
          <span class="metric">{{ item.trend|int }}</span>
        </span>
      </li>
    {% else %}
      <li class="top-item top-item--empty">{{ m.i18n('no items in this run', 'geen items in deze run') }}</li>
    {% endfor %}
    </ol>
    <footer class="cat-foot">
      <a class="cat-more" href="/categories/{{ cat.id }}.html" data-tip-en="Browse every item in this category" data-tip-nl="Bekijk alle items in deze categorie">{{ m.i18n('open category', 'categorie openen') }} →</a>
    </footer>
  </article>
{% endfor %}
</section>
{% endblock %}
"""

CATEGORY = """{% extends "layout.html" %}
{% block content %}
<section class="page-head">
  <h1 class="page-title">{{ m.loc(cat.title) }}</h1>
  <p class="page-desc">{{ m.loc(cat.description) }}</p>
  <div class="chip-row">
    {% for topic in cat.topics %}
    <span class="chip">{{ m.loc(topic.title) }}</span>
    {% endfor %}
    <span class="chip chip-meta">{{ items|length }} / {{ cat.count }} {{ m.i18n('items', 'items') }}</span>
    <span class="chip chip-meta">{{ m.i18n('sorted by trend', 'gesorteerd op trend') }}</span>
  </div>
</section>

<section class="item-table">
  <div class="row row-head">
    <span class="col-rank">#</span>
    <span class="col-main">{{ m.i18n('item', 'item') }}</span>
    <span class="col-spark">{{ m.i18n('14d', '14d') }}</span>
    <span class="col-num">{{ m.i18n('stars', 'stars') }}</span>
    <span class="col-num">HN</span>
    <span class="col-num">{{ m.i18n('rel', 'rel') }}</span>
    <span class="col-num">{{ m.i18n('trend', 'trend') }}</span>
  </div>
  {% for item in items %}
  <a class="row row-item" href="/items/{{ item.slug }}.html" data-cat="{{ cat.id }}">
    <span class="col-rank">{{ "%02d"|format(loop.index) }}</span>
    <span class="col-main">
      <span class="row-title">{{ item.title }}</span>
      {% if item.purpose.en or item.purpose.nl %}
      <span class="row-purpose">{{ m.loc(item.purpose) }}</span>
      {% endif %}
      <span class="row-sub">
        <span class="src">{% if item.source == 'manual' %}{{ m.i18n('manual', 'handmatig') }}{% else %}{{ item.source }}{% endif %}</span>
        {% if item.is_new %}<span class="badge badge-inv">NEW</span>{% endif %}
        {% if item.is_hot %}<span class="badge badge-inv">HOT</span>{% endif %}
        <span class="delta {{ item.delta_class }}">{{ item.delta_arrow }}{{ item.delta }}</span>
      </span>
    </span>
    <span class="col-spark spark" aria-hidden="true">{{ item.sparkline or '·' }}</span>
    <span class="col-num">{{ item.stars|int if item.stars else '—' }}</span>
    <span class="col-num">{{ item.hn_points|int if item.hn_points else '—' }}</span>
    <span class="col-num">{{ item.relevance }}/10</span>
    <span class="col-num col-trend">{{ item.trend|int }}</span>
  </a>
  {% else %}
  <p class="empty">{{ m.i18n('no items in this run', 'geen items in deze run') }}</p>
  {% endfor %}
</section>
{% endblock %}
"""

ITEM = """{% extends "layout.html" %}
{% block content %}
<article class="item-page">
  <div class="crumbs">
    <a href="/index.html">{{ m.i18n('all', 'alles') }}</a>
    <span>→</span>
    <a href="/categories/{{ item.category }}.html">{{ m.loc(cat_title) }}</a>
  </div>

  <h1 class="item-title">{{ item.title }}{% if item.kind %} <span class="kind-badge">{{ item.kind }}</span>{% endif %}</h1>

  <div class="item-meta">
    <span class="m">stars:</span> <span class="v">{{ item.stars|int if item.stars else 0 }}</span>
    <span class="v {{ item.delta_class }}">{{ item.delta_arrow }}{{ item.delta }}/d</span>
    <span class="m">hn:</span> <span class="v">{{ item.hn_points|int }}</span>
    <span class="m">{{ m.i18n('relevance', 'relevantie') }}:</span> <span class="v">{{ item.relevance }}/10</span>
    <span class="m">trend:</span> <span class="v">{{ item.trend|int }}</span>
    <span class="spark" aria-hidden="true">{{ item.sparkline or '·' }}</span>
  </div>

  <div class="item-links">
    <a class="btn" href="{{ item.url }}" rel="noopener noreferrer" target="_blank" data-tip-en="Open the original source in a new tab" data-tip-nl="Opent de originele bron in een nieuw tabblad">{{ m.i18n('open source', 'bron openen') }} ↗</a>
    {% if item.discussion_url and item.discussion_url != item.url %}
    <a class="btn btn-ghost" href="{{ item.discussion_url }}" rel="noopener noreferrer" target="_blank" data-tip-en="Open the discussion (HN/GitHub/…) in a new tab" data-tip-nl="Opent de discussie (HN/GitHub/…) in een nieuw tabblad">{{ m.i18n('discussion', 'discussie') }} ↗</a>
    {% endif %}
  </div>

  {% if item.purpose.en or item.purpose.nl %}
  <div class="item-purpose">
    <span class="purpose-label">{{ m.i18n("what it's for", "waar het voor is") }}</span>
    {{ m.loc(item.purpose) }}
  </div>
  {% endif %}

  <div class="i18n-block">
    <span class="i18n" lang="en">{{ item.summary.en or item.description or 'No summary yet.' }}</span>
    <span class="i18n" lang="nl">{{ item.summary.nl or item.description or 'Nog geen samenvatting.' }}</span>
  </div>

  {% if item.tags %}
  <div class="chip-row">
    {% for tag in item.tags %}<span class="chip">{{ tag }}</span>{% endfor %}
  </div>
  {% endif %}

  {% if item.manual_note %}
  <div class="manual-note">
    <span class="k">{{ m.i18n('note', 'notitie') }}</span>
    <span>{{ item.manual_note }}</span>
  </div>
  {% endif %}

  <div class="meta-grid">
    <div><span class="k">{{ m.i18n('source', 'bron') }}</span><span class="v">{% if item.source == 'manual' %}{{ m.i18n('manual', 'handmatig toegevoegd') }}{% else %}{{ item.source }}{% endif %}</span></div>
    <div><span class="k">{{ m.i18n('first seen', 'voor het eerst') }}</span><span class="v">{{ item.first_seen[:10] }}</span></div>
    <div><span class="k">{{ m.i18n('published', 'gepubliceerd') }}</span><span class="v">{{ (item.created_at or '—')[:10] }}</span></div>
    <div><span class="k">{{ m.i18n('stars/day', 'sterren/dag') }}</span><span class="v">+{{ item.star_delta }}</span></div>
    {% if item.context_length %}
    <div><span class="k">context</span><span class="v">{{ item.context_length }}</span></div>
    {% endif %}
    {% if item.free %}
    <div><span class="k">{{ m.i18n('price', 'prijs') }}</span><span class="v">FREE</span></div>
    {% endif %}
  </div>

  <section class="rating" data-slug="{{ item.slug }}">
    <h2 class="section-title">{{ m.i18n('your rating', 'jouw beoordeling') }}</h2>
    <div class="rating-stars" role="radiogroup" aria-label="rating">
      {% for star in range(1, 6) %}
      <button type="button" class="star" data-value="{{ star }}" aria-label="{{ star }} stars"
        data-tip-en="Rate {{ star }} of 5 — anonymous, counts towards the ranking"
        data-tip-nl="Geef {{ star }} van 5 sterren — anoniem, telt mee in de ranking">★</button>
      {% endfor %}
    </div>
    <p class="rating-status" data-rating-status></p>
    <div class="rating-stats">
      <span class="rating-avg" data-rating-avg>—</span>
      <span class="rating-count" data-rating-count></span>
    </div>
    <div class="histogram" data-rating-hist></div>
  </section>

  {% if related %}
  <section class="related">
    <h2 class="section-title">{{ m.i18n('related', 'gerelateerd') }}</h2>
    <ul class="related-list">
      {% for rel in related %}
      <li>
        <a href="/items/{{ rel.slug }}.html">{{ rel.title }}</a>
        <span class="delta {{ rel.delta_class }}">{{ rel.delta_arrow }}{{ rel.delta }}</span>
        <span class="metric">{{ rel.trend|int }}</span>
      </li>
      {% endfor %}
    </ul>
  </section>
  {% endif %}
</article>
<script src="/assets/rating.js" defer></script>
{% endblock %}
"""

HELP = """{% extends "layout.html" %}
{% block content %}
<section class="page-head">
  <h1 class="page-title">{{ m.i18n('Help · operating the dashboard', 'Help · het dashboard bedienen') }}</h1>
  <p class="page-desc">{{ m.i18n(
    'Operational manual: how the site gets its data, what runs when, and how to change things safely.',
    'Operationele handleiding: waar de site zijn data vandaan haalt, wat wanneer draait en hoe je veilig dingen wijzigt.') }}</p>
</section>

<div class="help">
  <section class="help-section">
    <h2>1 · {{ m.i18n('Daily rhythm', 'Dagelijks ritme') }}</h2>
    <p>{{ m.i18n(
      'The site is a static build. Every day at 06:00 UTC the scheduler container runs a crawl: fetch sources, score items, AI-review, then re-render every page. The timestamp in the header shows when the current build finished.',
      "De site is een statische build. Elke dag om 06:00 UTC draait de scheduler-container een crawl: bronnen ophalen, items scoren, AI-review en daarna alle pagina's opnieuw renderen. De tijd in de header is het moment waarop de huidige build klaar kwam.") }}</p>
    <p>{{ m.i18n(
      'Between runs you can trigger a crawl yourself: Manage → crawl now (writes only from your home network). Changes you make on the Manage page are applied at the next crawl.',
      'Tussen runs door kun je zelf een crawl starten: Beheren → nu crawlen (schrijven alleen vanaf je thuisnetwerk). Wijzigingen op de Beheerpagina gelden bij de volgende crawl.') }}</p>
  </section>

  <section class="help-section">
    <h2>2 · {{ m.i18n('Commands', "Commando's") }}</h2>
    <ul>
      <li><code>docker compose up -d --build</code> — {{ m.i18n('build and start web + scheduler (run after every code or .env change)', 'bouwt en start web + scheduler (draai na elke code- of .env-wijziging)') }}</li>
      <li><code>ops/dev.sh</code> — {{ m.i18n('local development: crawl (optional flags) + server on port 8184', 'lokale ontwikkeling: crawl (met flags) + server op poort 8184') }}</li>
      <li><code>python3 pipeline/crawl.py</code> — {{ m.i18n('one crawl with AI-review', 'een crawl met AI-review') }}</li>
      <li><code>python3 pipeline/crawl.py --no-ai</code> — {{ m.i18n('fetch + score + render, skip AI', 'ophalen + scoren + renderen, zonder AI') }}</li>
      <li><code>python3 pipeline/crawl.py --categories models,token-providers</code> — {{ m.i18n('only these categories', 'alleen deze categorieën') }}</li>
      <li><code>python3 pipeline/crawl.py --resummarize</code> — {{ m.i18n('re-review every item (backfill after a prompt change)', 'alle items opnieuw beoordelen (backfill na promptwijziging)') }}</li>
      <li><code>python3 server/server.py</code> — {{ m.i18n('run the server + API standalone (no crawl)', 'draai alleen de server + API (geen crawl)') }}</li>
      <li><code>python3 -m unittest discover -s tests -v</code> — {{ m.i18n('run the test suite', 'draai de tests') }}</li>
    </ul>
    <p>{{ m.i18n(
      'Logs: docker logs ai-trending-web, or data/logs/ (every web-triggered crawl writes web-trigger-*.log there).',
      'Logs: docker logs ai-trending-web, of data/logs/ (elke via de web gestarte crawl schrijft daar web-trigger-*.log).') }}</p>
  </section>

  <section class="help-section">
    <h2>3 · {{ m.i18n('Settings (.env)', 'Instellingen (.env)') }}</h2>
    <ul>
      <li><code>OMNIROUTE_URL</code> / <code>OMNIROUTE_API_KEY</code> — {{ m.i18n('OpenAI-compatible gateway the AI-review runs on (URL from inside the gateway network).', 'OpenAI-compatibele gateway waarop de AI-review draait (URL vanuit het gateway-netwerk).') }}</li>
      <li><code>OMNIROUTE_MODEL</code> — {{ m.i18n('model preference; auto/best-fast is the fastest for 8-item review batches.', 'modelvoorkeur; auto/best-fast is het snelste voor 8-items review-batches.') }}</li>
      <li><code>WRITE_NETWORKS</code> — {{ m.i18n('networks allowed to write via the website (references, topics, sources, crawl). Empty = deny all. The 403 message shows your IP and the allowed ranges; 172.16.0.0/12 covers docker, so opening the site from the server itself also works.', 'netwerken die mogen schrijven via de website (referenties, topics, bronnen, crawl). Leeg = alles geweigerd. De 403-melding toont jouw IP en de toegestane ranges; 172.16.0.0/12 dekt docker, dus de site openen vanaf de server zelf werkt ook.') }}</li>
      <li><code>RATING_SALT</code> — {{ m.i18n('salt for anonymising rating IPs (change it in production)', "zout om rating-IP's te anonimiseren (wijzig dit in productie)") }}</li>
      <li><code>MAX_WRITES_PER_HOUR</code> / <code>MAX_RATINGS_PER_HOUR</code> — {{ m.i18n('rate limits per IP', 'rate-limits per IP') }}</li>
      <li><code>GITHUB_TOKEN</code> — {{ m.i18n('optional: higher GitHub search rate', 'optioneel: hoger GitHub-zoekratio') }}</li>
      <li><code>PORT</code> — {{ m.i18n('server port inside the container (default 8184)', 'serverpoort in de container (standaard 8184)') }}</li>
    </ul>
    <p>{{ m.i18n('After changing .env or server code: docker compose up -d --build (containers are recreated, so the new settings apply).', 'Na wijzigen van .env of servercode: docker compose up -d --build (containers worden opnieuw aangemaakt, de nieuwe instellingen gelden dan).') }}</p>
  </section>

  <section class="help-section">
    <h2>4 · {{ m.i18n('Web management', 'Web-beheer') }}</h2>
    <ul>
      <li>{{ m.i18n('References: a curated URL per category; injected into the index at the next crawl and never dropped.', 'Referenties: een handmatige URL per categorie; wordt bij de volgende crawl in de index gezet en nooit gedropt.') }}</li>
      <li>{{ m.i18n('Topics: search queries that drive source fetches and AI keyword matching. AI-generated topics are marked with · AI.', 'Topics: zoekopdrachten die het ophalen van bronnen en de AI-sleutelwoordmatch sturen. AI-gegenereerde topics zijn gemarkeerd met · AI.') }}</li>
      <li>{{ m.i18n('Sources: extra feeds/searches per category (hn_search, github_search, arxiv, rss, openrouter).', 'Bronnen: extra feeds/zoekopdrachten per categorie (hn_search, github_search, arxiv, rss, openrouter).') }}</li>
      <li>{{ m.i18n('Crawl now: starts a full crawl in the background; refresh the page in a few minutes.', 'Nu crawlen: start een volledige crawl op de achtergrond; ververs de pagina over enkele minuten.') }}</li>
      <li>{{ m.i18n('Every change is logged in the changelog on this page and applied at the next crawl. Writes are limited to WRITE_NETWORKS and to a maximum of 60 per hour per IP.', 'Elke wijziging staat in het changelog op deze pagina en geldt bij de volgende crawl. Schrijven is beperkt tot WRITE_NETWORKS en maximaal 60 keer per uur per IP.') }}</li>
    </ul>
  </section>

  <section class="help-section">
    <h2>5 · {{ m.i18n('How the AI review works', 'Zo werkt de AI-review') }}</h2>
    <p>{{ m.i18n(
      'Each batch of up to 8 items is scored (relevance 0-10, keep/drop), tagged, and given a summary (600-800 chars), a one-line purpose and a kind (tool/model/paper/…). Results land in data/items.json: summary, purpose, kind, relevance, tags, ai_verdict and ai_reviewed_at.',
      'Elke batch van maximaal 8 items wordt gescoord (relevantie 0-10, keep/drop), getagd en krijgt een samenvatting (600-800 tekens), een éénregelige purpose en een soort (tool/model/paper/…). De resultaten staan in data/items.json: summary, purpose, kind, relevance, tags, ai_verdict en ai_reviewed_at.') }}</p>
    <p>{{ m.i18n(
      'Self-healing: an item without a purpose is picked up again by the next run, even inside the 7-day review window. So if a batch failed (gateway timeout), the next crawl simply retries it — nothing is lost.',
      'Zelfherstel: een item zonder purpose wordt door de volgende run weer opgepakt, zelfs binnen het 7-dagen review-venster. Als een batch dus faalde (gateway-timeout), probeert de volgende crawl het gewoon opnieuw — er gaat niets verloren.') }}</p>
    <p>{{ m.i18n(
      'Failing batches are logged and retried with backoff. For a full backfill after a prompt change, run: python3 pipeline/crawl.py --resummarize.',
      'Gefaalde batches worden gelogd en met backoff opnieuw geprobeerd. Voor een volledige backfill na een promptwijziging: python3 pipeline/crawl.py --resummarize.') }}</p>
  </section>

  <section class="help-section">
    <h2>6 · {{ m.i18n('Data and files', 'Data en bestanden') }}</h2>
    <ul>
      <li><code>data/items.json</code> — {{ m.i18n('the index: every item with scores, AI fields and history flags', 'de index: elk item met scores, AI-velden en history-flags') }}</li>
      <li><code>data/ratings.json</code> — {{ m.i18n('5-star ratings (hashed per IP + salt)', '5-sterrenbeoordelingen (gehasht per IP + zout)') }}</li>
      <li><code>data/references.json</code> — {{ m.i18n('your manual references', 'jouw handmatige referenties') }}</li>
      <li><code>data/history/</code> — {{ m.i18n('daily trend snapshots (drives sparklines and deltas)', 'dagelijkse trend-snapshots (voedt sparklines en deltas)') }}</li>
      <li><code>data/config-changelog.jsonl</code> — {{ m.i18n('audit trail of configuration changes', 'audittrail van configuratiewijzigingen') }}</li>
      <li><code>config/categories.yaml</code> — {{ m.i18n('categories, sources and topics (the single source of truth)', 'categorieën, bronnen en topics (de single source of truth)') }}</li>
      <li><code>data/search-index.json</code>, <code>data/categories.json</code> — {{ m.i18n('consumed by the client-side search and the tab bar', 'gebruikt door de client-side search en de tab-balk') }}</li>
      <li><code>data/.crawl.lock</code> — {{ m.i18n('mutex between cron and web-triggered crawls; auto-released when the holder dies', 'mutex tussen cron- en web-crawls; wordt vrijgegeven als de houder sterft') }}</li>
      <li><code>site/</code> — {{ m.i18n('generated HTML; do not edit by hand', 'gegenereerde HTML; niet handmatig bewerken') }}</li>
    </ul>
  </section>

  <section class="help-section">
    <h2>7 · {{ m.i18n('Troubleshooting', 'Problemen oplossen') }}</h2>
    <ul>
      <li><b>403 on saving</b> — {{ m.i18n('your IP is not in WRITE_NETWORKS; the error message shows the IP the server saw and the allowed ranges.', 'jouw IP staat niet in WRITE_NETWORKS; de foutmelding toont het IP dat de server zag en de toegestane ranges.') }}</li>
      <li><b>HTML instead of JSON</b> — {{ m.i18n('a proxy or server error page reached the browser; the UI now reports this as "server did not respond with JSON (HTTP …)". Check docker logs ai-trending-web.', 'een proxy- of serverfoutpagina bereikte de browser; de UI meldt dit nu als "server antwoordde niet met JSON (HTTP …)". Kijk in docker logs ai-trending-web.') }}</li>
      <li><b>Crawl will not start</b> — {{ m.i18n('data/.crawl.lock exists and is fresh: another crawl is running. It is stale after 30 minutes or when the holding pid is dead.', 'data/.crawl.lock bestaat en is recent: er draait al een crawl. Hij is verouderd na 30 minuten of als de pid dood is.') }}</li>
      <li><b>AI review slow or failing</b> — {{ m.i18n('gateway peaks last 20-60 minutes; batches time out and are retried next run. Check data/logs/ and the gateway itself.', 'gateway-pieken duren 20-60 minuten; batches lopen vast en worden de volgende run opnieuw geprobeerd. Kijk in data/logs/ en naar de gateway zelf.') }}</li>
      <li><b>Site not updated</b> — {{ m.i18n('look in data/logs/web-trigger-*.log or docker logs ai-trending-scheduler; /healthz must return {"status":"ok"}.', 'kijk in data/logs/web-trigger-*.log of docker logs ai-trending-scheduler; /healthz moet {"status":"ok"} teruggeven.') }}</li>
      <li><b>Item vanished</b> — {{ m.i18n('items are pruned 30 days after their last sighting by any source; manual references are re-injected every crawl.', 'items verdwijnen 30 dagen na hun laatste waarneming door een bron; handmatige referenties worden elke crawl opnieuw geïnjecteerd.') }}</li>
    </ul>
  </section>

  <section class="help-section">
    <h2>8 · {{ m.i18n('Privacy and security', 'Privacy en beveiliging') }}</h2>
    <ul>
      <li>{{ m.i18n('Ratings are stored as a hash of your IP + RATING_SALT, never as a raw address.', 'Beoordelingen worden bewaard als hash van je IP + RATING_SALT, nooit als ruw adres.') }}</li>
      <li>{{ m.i18n('All writes (references, topics, sources, crawl) are restricted to WRITE_NETWORKS and rate-limited per IP.', 'Alle schrijfacties (referenties, topics, bronnen, crawl) zijn beperkt tot WRITE_NETWORKS en rate-limited per IP.') }}</li>
      <li>{{ m.i18n('The site is fully static; there are no third-party trackers or analytics.', 'De site is volledig statisch; geen third-party trackers of analytics.') }}</li>
    </ul>
  </section>
</div>
{% endblock %}
"""

MACROS = """{% macro i18n(en, nl) -%}
<span class="i18n" lang="en">{{ en }}</span><span class="i18n" lang="nl">{{ nl }}</span>
{%- endmacro %}
{%- macro loc(value) -%}
{{ i18n(value.get('en') if value is mapping else value, value.get('nl') if value is mapping else value) }}
{%- endmacro -%}
"""


def _env() -> Environment:
    env = Environment(
        loader=DictLoader({
            "layout.html": LAYOUT,
            "index.html": INDEX,
            "category.html": CATEGORY,
            "item.html": ITEM,
            "help.html": HELP,
            "_macros.html": MACROS,
        }),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["m"] = env.get_template("_macros.html").module
    env.filters["num"] = lambda value: f"{int(value):,}"
    return env


def _format_delta(value: float) -> tuple[str, str, str]:
    """Geeft (arrow, tekst, css-klasse)."""
    value = float(value or 0)
    if value > 0.05:
        return "▲", f"+{value:.1f}", "up"
    if value < -0.05:
        return "▼", f"{value:.1f}", "down"
    return "•", "0.0", "flat"


def _enrich(item: dict, history) -> dict:
    item = dict(item)
    item["sparkline"] = build_sparkline(history, item["slug"])
    series = trend_series(history, item["slug"])
    delta = (series[-1] - series[-2]) if len(series) >= 2 else 0.0
    arrow, text, css = _format_delta(delta)
    item["delta_arrow"] = arrow
    item["delta"] = text
    item["delta_class"] = css
    first_seen = str(item.get("first_seen") or "")[:10]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    item["is_new"] = bool(first_seen) and _days_between(first_seen, today) <= NEW_WINDOW_DAYS
    item["is_hot"] = float(item.get("trend", 0)) >= HOT_TREND or (
        int(item.get("relevance", 0)) >= 9 and float(item.get("trend", 0)) >= 45)
    item.setdefault("summary", {"en": "", "nl": ""})
    item.setdefault("purpose", {"en": "", "nl": ""})
    item.setdefault("kind", "")
    item.setdefault("tags", [])
    return item


def _days_between(a: str, b: str) -> int:
    try:
        da = datetime.strptime(a, "%Y-%m-%d")
        db = datetime.strptime(b, "%Y-%m-%d")
        return abs((db - da).days)
    except ValueError:
        return 9999


def build_context(config: dict, items: dict, history) -> dict:
    by_category: dict[str, list[dict]] = {cat["id"]: [] for cat in config["categories"]}
    enriched_all: dict[str, dict] = {}
    for slug, raw in items.items():
        enriched = _enrich(raw, history)
        enriched_all[slug] = enriched
        for cid in enriched.get("categories") or [enriched.get("category")]:
            if cid in by_category:
                by_category[cid].append(enriched)

    cats = []
    max_items = int(config.get("defaults", {}).get("max_items_per_category", 20))
    for cat in config["categories"]:
        total = sorted(by_category.get(cat["id"], []), key=lambda i: i.get("trend", 0), reverse=True)
        cat_items = total[:max_items]
        # handmatig toegevoegde referenties blijven altijd zichtbaar (trend 0 op dag 1)
        cat_items += [i for i in total[max_items:] if i.get("manual")]
        series: list[float] = []
        for _, snapshot in history:
            values = [snapshot[s]["trend"] for s in (i["slug"] for i in cat_items)
                      if isinstance(snapshot.get(s), dict)]
            series.append(sum(values) / len(values) if values else 0.0)
        if len(series) >= 2:
            delta = series[-1] - series[-2]
        else:
            delta = 0.0
        arrow, text, css = _format_delta(delta)
        cats.append({
            **cat,
            "items": cat_items,
            "sparkline": "".join(
                "▁▂▃▄▅▆▇█"[int(round(((v - min(series)) / ((max(series) - min(series)) or 1.0)) * 7))]
                for v in series) if series else "",
            "delta": text,
            "delta_arrow": arrow,
            "delta_class": css,
            "count": len(total),
        })

    flat = sorted(enriched_all.values(), key=lambda i: i.get("trend", 0), reverse=True)
    return {
        "config": config,
        "categories": cats,
        "items": flat,
        "item_count": len(items),
        "generated_at": now_iso(),
    }


def render_site(config: dict, items: dict, history, site_dir: str, data_dir: str) -> dict:
    env = _env()
    context = build_context(config, items, history)
    ensure_dir(site_dir)

    index_html = env.get_template("index.html").render(
        page="index",
        page_title="AI Trending · dashboard",
        page_desc="Daily AI industry trending dashboard.",
        main_class="page-index",
        active_tab="all",
        cats=context["categories"],
        categories=context["categories"],
        generated_at=context["generated_at"],
        item_count=context["item_count"],
    )
    atomic_write_text(os.path.join(site_dir, "index.html"), index_html)
    written = {"index.html": len(index_html)}

    help_html = env.get_template("help.html").render(
        page="help",
        page_title="Help · AI Trending",
        page_desc="Operational manual for the dashboard.",
        main_class="page-help",
        active_tab="",
        categories=context["categories"],
        generated_at=context["generated_at"],
        item_count=context["item_count"],
    )
    atomic_write_text(os.path.join(site_dir, "help.html"), help_html)
    written["help.html"] = len(help_html)
    for cat in context["categories"]:
        html = env.get_template("category.html").render(
            page="category",
            page_title=f"{localized(cat['title'], 'en')} · AI Trending",
            page_desc=localized(cat["description"], "en"),
            main_class="page-category",
            active_tab=cat["id"],
            cat=cat,
            items=cat["items"],
            categories=context["categories"],
            generated_at=context["generated_at"],
            item_count=context["item_count"],
        )
        path = os.path.join(site_dir, "categories", f"{cat['id']}.html")
        ensure_dir(os.path.dirname(path))
        atomic_write_text(path, html)
        written[f"categories/{cat['id']}.html"] = len(html)

    items_dir = os.path.join(site_dir, "items")
    ensure_dir(items_dir)
    cat_titles = {c["id"]: c["title"] for c in context["categories"]}
    for item in context["items"]:
        related = [i for i in context["items"]
                   if i["slug"] != item["slug"] and item["category"] in (i.get("categories") or [])][:6]
        html = env.get_template("item.html").render(
            page="item",
            page_title=f"{item['title']} · AI Trending",
            page_desc=(item.get("summary") or {}).get("en") or item.get("description", ""),
            main_class="page-item",
            active_tab=item["category"],
            item=item,
            cat_title=cat_titles.get(item["category"], {"en": item["category"], "nl": item["category"]}),
            related=related,
            categories=context["categories"],
            generated_at=context["generated_at"],
            item_count=context["item_count"],
        )
        atomic_write_text(os.path.join(items_dir, f"{item['slug']}.html"), html)
        written[f"items/{item['slug']}.html"] = len(html)

    current = {f"{item['slug']}.html" for item in context["items"]}
    for name in os.listdir(items_dir):
        if name.endswith(".html") and name not in current:
            os.remove(os.path.join(items_dir, name))

    write_search_index(context["items"], data_dir)
    write_categories_json(context, data_dir)
    return written


def write_search_index(items: list[dict], data_dir: str) -> str:
    index = [{
        "slug": item["slug"],
        "title": item["title"],
        "url": item.get("url", ""),
        "category": item.get("category", ""),
        "categories": item.get("categories") or [],
        "tags": item.get("tags") or [],
        "source": item.get("source", ""),
        "trend": item.get("trend", 0),
        "relevance": item.get("relevance", 0),
        "stars": item.get("stars", 0),
        "summary_en": (item.get("summary") or {}).get("en", ""),
        "summary_nl": (item.get("summary") or {}).get("nl", ""),
        "purpose_en": (item.get("purpose") or {}).get("en", ""),
        "purpose_nl": (item.get("purpose") or {}).get("nl", ""),
        "kind": item.get("kind", ""),
    } for item in items]
    path = os.path.join(data_dir, "search-index.json")
    atomic_write_json(path, index)
    return path


def write_categories_json(context: dict, data_dir: str) -> str:
    payload = {
        "generated_at": context["generated_at"],
        "item_count": context["item_count"],
        "categories": [{
            "id": cat["id"],
            "title": cat["title"],
            "description": cat.get("description", {}),
            "count": cat["count"],
            "trend": round(sum(i.get("trend", 0) for i in cat["items"]) / len(cat["items"]), 1) if cat["items"] else 0,
        } for cat in context["categories"]],
    }
    path = os.path.join(data_dir, "categories.json")
    atomic_write_json(path, payload)
    return path
