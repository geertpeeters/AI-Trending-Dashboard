# AI Trending Dashboard

Statisch, monochroom (dark / Courier) dashboard met de trendingste technologieën in
AI-land, elke dag herwogen door een cron-pipeline met verplichte AI-review.

Categorieën (uitbreidbaar via `config/categories.yaml`):

`models` · `agentic-frameworks` · `skill-design` · `ai-standaarden` ·
`token-providers` · `top-20-free-models` · `token-compressors` · `decision-models`

## Architectuur

```
pipeline/crawl.py     cron-entrypoint: crawl -> score -> AI-review -> render
pipeline/sources/     hn.py, github.py, openrouter.py, arxiv.py, rss.py
pipeline/scoring.py   trend-score + dagelijkse history-snapshot (sparklines)
pipeline/ai.py        LLM-client: relevance, NL/EN summaries, nieuwe topics
pipeline/render.py    Jinja2 -> statische HTML
server/server.py      static server + API: ratings, referenties, topics, bronnen, discover
site/                 index.html (gegenereerd), categories/, items/, help.html, search.html, manage.html, assets/
config/categories.yaml  categorieen + topics + bronnen (AI-updaterbaar + web-beheer)
data/                 items.json, ratings.json, references.json, history/, logs/
```

## Lokaal draaien

```sh
apk add python3 py3-yaml py3-jinja2     # Alpine
python3 -m compileall pipeline server
python3 pipeline/crawl.py --no-ai       # eerste run tegen echte bronnen
python3 server/server.py                # http://localhost:8184
```

Of in één keer: `sh ops/dev.sh --no-ai`

Met AI (verplicht voor summaries/relevance/config-updates). Standaard loopt alles via
je eigen **omniroute-gateway** — dezelfde `OMNIROUTE_*`-variabelen die de container
toch al meekreeg:

```sh
cp .env.example .env    # OMNIROUTE_URL + OMNIROUTE_API_KEY invullen
set -a; . ./.env; set +a
python3 pipeline/crawl.py
```

- `OMNIROUTE_URL=http://omniroute:20128/v1` (vanuit de container: docker-DNS-naam op
  `infra-net`; `omniroute.test` resolveert níet inside docker)
- `OMNIROUTE_API_KEY=` — dezelfde sleutel die de gateway accepteert
- `OMNIROUTE_MODEL=` optioneel; leeg = eerste passende model uit `GET {url}/models`
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` hebben voorrang als je ze invult
  (bijv. direct naar `api.openai.com`)

Zonder sleutel, of met `--no-ai`, valt de pipeline terug op heuristieken;
de cron breekt nooit.

## Docker

```sh
cp .env.example .env     # OMNIROUTE_* + WRITE_NETWORKS invullen
docker compose up -d --build
```

- `web` (poort **8184** in het container-netwerk): serveert `site/` + `data/`
- `scheduler`: crond, dagelijks 06:00 → `python3 pipeline/crawl.py`

Handmatig draaien: `docker compose run --rm scheduler python3 pipeline/crawl.py`

Alle bestaande items in één keer opnieuw laten samenvatten (backfill na een
prompt-wijziging): `docker compose run --rm scheduler python3 pipeline/crawl.py --resummarize`
— slaat het 7-dagen-venster én de cap van 40/run over; gewone cron-runs houden die cap.

### Reverse proxy (Traefik / omniroute)

De `web`-service hangt aan het **externe** docker-netwerk `infra-net` (moet al bestaan:
`docker network inspect infra-net`), zodat traefik/omniroute de container ziet. Routing
loopt via labels in `docker-compose.yml`:

| label | waarde |
|---|---|
| `traefik.http.routers.ai-trending-dashboard.rule` | ``Host(`ai-trending-dashboard.test`)`` |
| `traefik.http.routers.ai-trending-dashboard.entrypoints` | `web` (alleen HTTP) |
| `traefik.http.services.ai-trending-dashboard.loadbalancer.server.port` | `8184` |
| `traefik.docker.network` | `infra-net` |

Er is géén `ports:`-mapping: alles loopt via traefik op het interne netwerk
(uniek poortnummer 8184, geen conflicten binnen `infra-net`).

`.env` bevat `OMNIROUTE_URL` + `OMNIROUTE_API_KEY`: die gaan als `env_file` de
container in én de AI-review gebruikt ze als LLM-endpoint (zie "Lokaal draaien").

Testen:

```sh
docker compose up -d --build
curl -s -o /dev/null -w '%{http_code}\n' -H "Host: ai-trending-dashboard.test" http://<proxy-ip>/
docker compose exec web python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8184/healthz').status)"  # lokaal
```

Zonder Docker-daemon in de build-omgeving: validatie is een YAML-parse; opbouwen
gebeurt op de host.

### DNS voor `*.test`

De browser moet `ai-trending-dashboard.test` (en `omniroute.test`) naar je proxy
resolven — anders: *DNS_PROBE_POSSIBLE* in plaats van een dashboard.

1. Proxy-IP zoeken:
   ```sh
   docker network inspect infra-net -f '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'
   ```
2. Op de machine waar je browser draait, in `/etc/hosts` (of een A-record in
   Pi-hole/AdGuard/UniFi):
   ```
   <proxy-ip>  ai-trending-dashboard.test omniroute.test
   ```
3. Zonder DNS testen of traefik de router al heeft:
   ```sh
   curl -s -o /dev/null -w '%{http_code}\n' -H "Host: ai-trending-dashboard.test" http://<proxy-ip>/
   ```
   - `200` → alleen DNS regelen
   - `404 page not found` → traefik-router/entrypoint/netwerk
   - `404 not found` → onze server: `site/index.html` ontbreekt in de container

### Eerste start

`web` start met `sh -c "test -f site/index.html || python3 pipeline/crawl.py; exec python3 server/server.py"`:
als de gegenereerde pagina's ontbreken (ze staan in `.gitignore`), draait de container
eerst één crawl en start daarna de server. Is `site/index.html` er wel, dan is dat een
no-op. Handmatig herhalen: `docker compose run --rm scheduler python3 pipeline/crawl.py`.

## Pipeline-stappen (dagelijks)

1. **Crawl** per category/topic: HN Algolia, GitHub Search, OpenRouter
   (`:free` + `pricing.prompt == "0"`), arXiv, RSS → normaliseren → dedup op URL/slug.
2. **Score**: `trend = 0.35·HN + 0.25·GH-sterrendelta + 0.20·recency + 0.20·AI-relevance`
   (0-100), snapshot in `data/history/YYYY-MM-DD.json` voor de 14-daagse sparklines.
3. **AI-review A**: relevance 0-10 + keep/drop + tags + samenvatting (NL+EN, 3–6 zinnen,
   ~600–800 tekens: wat het is / waar het voor is / waarom nu) + `purpose`-regel
   (*"waar het voor is"*, één losse zin) + `kind` (tool/model/paper/…) per item,
   plus keep/expand/drop per gehost topic.
4. **AI-review B**: nieuwe trends detecteren → `config/categories.yaml` bijwerken
   (backup `config/categories.prev.yaml`, log `data/config-changelog.jsonl`).
5. **Render**: item-pagina's, dashboard, categoriepagina's, `search-index.json`.

## API

| endpoint | methode | beschrijving |
|---|---|---|
| `/healthz` | GET | status |
| `/api/rating?slug=x` | GET | gemiddelde, histogram, eigen score |
| `/api/rating` | POST | `{"slug": "...", "score": 1..5}` (validatie, IP-throttle, atomaire write) |
| `/api/manage` | GET | categoriën + bronnen + topics + references + changelog |
| `/api/references` | GET/POST/DELETE | eigen links; `{"url","title","category","note"}` |
| `/api/topics` | POST/DELETE | topic toevoegen aan/verwijderen uit `categories.yaml` |
| `/api/sources` | POST/DELETE | bron toevoegen/verwijderen (`type`: `hn_search`, `github_search`, `arxiv`, `rss`, `openrouter`) |
| `/api/discover?q=&sources=` | GET | live zoeken in HN/GitHub/arXiv (+ OpenRouter-feed bij lege query), cache 10 min |
| `/api/rebuild` | POST | start een crawl in de achtergrond (202; 409 als er al een draait) |
| `/data/search-index.json` | GET | client-side search-index |

**Schrijfbeveiliging**: POST/DELETE op `references`/`topics`/`sources`/`rebuild` mogen
alleen vanaf `WRITE_NETWORKS` in `.env` (CIDR's of IP's, komma-gescheiden).
Leeg = alles geweigerd (403); ratings en lezen blijven open.
De 403-melding toont het IP dat de server ziet — zo zie je direct waarom een
poging werd geweigerd. `X-Forwarded-For` wordt rechts-uitgelezen (door Traefik
toegevoegd), zodat een gespoofde header de guard niet omzeilt. `172.16.0.0/12`
staat standaard in de lijst: wie de site vanaf de host zelf opent (127.0.0.1)
loopt via docker-proxy en verschijnt als `172.x.x.x`.

## Beheren via de website

`/manage.html` (link rechtsboven) en de **discover**-sectie op `/search.html`:

- **Referenties**: eigen links toevoegen (url, titel, categorie, notitie). Ze komen als
  `source: manual` items in de index: score/search/rating werken, de AI mag ze
  samenvatten maar **niet** droppen, en ze verdwijnen pas als je de referentie verwijdert.
- **Topics**: handmatig topic per categorie (titel EN/NL, query, sleutelwoorden) —
  zelfde veilige route als de AI: backup `config/categories.yaml.prev` + changelog.
- **Bronnen**: bronnenlijst per categorie beheren. Voor AI-portals zonder API volstaat
  `type: rss` met een feed-url, bijv.:
  `https://huggingface.co/blog/feed.xml`, `https://simonwillison.net/atom.xml`,
  `https://www.latent.space/feed`, `https://lmsys.org/rss.xml`.
- **Discover**: live zoeken (lege query = trending: HN-frontpage + OpenRouter) met knoppen
  *"als referentie"* / *"als topic"* per resultaat.
- **Nu crawlen**: `POST /api/rebuild`; cron en web-trigger vechten niet om dezelfde data
  (`data/.crawl.lock`, stale na 30 min).

Wijzigingen gelden pas na een crawl (cron 06:00 of de knop); logboek staat in
`data/config-changelog.jsonl`.

## UI

- **Kleuren**: warme donkere achtergrond (`#0b0a09`) met zachte pastelaccenten uit vier
  spectra — **groen** `#9cc4a6` (sparklines, opgaande deltas, succesmeldingen, `h1`),
  **blauw** `#9fb6d4` (NEW/HOT-badges, alle klikbare knoppen, actieve taalknop, `h2`),
  **geel** `#d6c98d` (sterren, trend-kolom, kind-badges, histogram, `h3`), **rood**
  `#d4a09b` (dalingen, fouten); links, brand en tabs blijven warm zand
  (`--accent #d8c4a6`). Geen felle kleuren; hover-verlichting is maximaal
  `brightness(1.15)`.
- Tweetalig **NL/EN**, switcher rechtsboven (keuze in `localStorage`).
- **Kaartenbak-tabs**: overlappende tab-kaarten, sticky onder de header, scrollbaar
  met fade-edges; op het dashboard filteren ze de categorie-kaarten.
- Item-pagina: `stars: 1,204  ▲+38.0/d  hn: 214  relevance: 9/10`, ★☆-widget (5 sterren),
  `kind`-badge naast de titel, **purpose-regel** (*"Waar het voor is:"*) en de volledige
  3–6-zinnen-samenvatting; handmatige referenties tonen bron "handmatig toegevoegd" +
  hun notitie. Categorieregels tonen de purpose-regel onder de titel; search-resultaten
  tonen purpose + een afgekapte summary.
- Ratings worden server-side geaggregeerd per gehashte IP.
- `/search.html`: index-search + live discover; `/manage.html`: referenties, topics,
  bronnen, changelog en de crawl-knop.
- **Tooltips**: elke knop, head-link, tab en bediening heeft een tweetalige hint
  (`data-tip-en`/`data-tip-nl`, engine in `assets/app.js`), zichtbaar bij hover én focus.
- `/help.html`: operationele handleiding (dagelijks ritme, commando's, `.env`,
  web-beheer, AI-review, data-bestanden, troubleshooting, privacy) — meegegenerate
  door de pipeline en linkbaar rechtsboven op elke pagina.
- **Favicon**: `assets/favicon.svg` (donker vlak, stijgende balken) in elke tab.

## Config bewerken

`config/categories.yaml` is het invoerbestand voor de pipeline. Gewijzigd wordt erdoor:

- de AI-run voegt alleen *nieuwe* topics toe (max `ai_review.max_new_topics_per_run`),
  met backup en changelog;
- de beheerpagina (`/manage.html`) voegt topics/bronnen toe en verwijdert ze, altijd met
  backup (`config/categories.yaml.prev`) en een changelog-regel (`actor: web`);
- `data/references.json` is de bron voor handmatige referenties (los van de yaml).

## Verificatie

1. `python3 -m compileall pipeline server tests && python3 -m pyflakes pipeline server tests`
2. `python3 -m unittest discover -s tests -v` → 73 tests: keep-alive-regressie (403 + body
   op dezelfde verbinding), XFF-guard, ratings, lock, AI-review-logica, config-changelog,
   render (help/favicon/tooltips)
3. `python3 pipeline/crawl.py --no-ai` → `data/items.json`, `data/search-index.json`, `site/` gevuld
4. `WRITE_NETWORKS=127.0.0.1 python3 server/server.py` → `/`, `/search.html`, `/manage.html`,
   `/help.html`, `/healthz`, `/api/rating?slug=...`, `/api/manage`, `/api/discover?q=llm&sources=hn`
5. Writes: `curl -X POST -H 'Content-Type: application/json' -d '{"url":"https://x.nl","category":"models"}' http://127.0.0.1:8184/api/references`
   → `201`; zonder `WRITE_NETWORKS` → `403` (melding toont het gezien IP + toegestane ranges)
6. Visuele check: tabs, sparklines, search, discover, manage, rating, NL/EN-toggle,
   tooltips, favicon, help-pagina, itempagina met purpose-regel + kind-badge
7. Met `OMNIROUTE_API_KEY` in `.env`: volledige AI-run (summaries, relevance, config-update);
   éénmalig daarna `--resummarize` draaien voor de oudere items
