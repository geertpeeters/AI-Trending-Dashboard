/* Taalkeuze (NL/EN) + dictionary voor JS-gegenereerde strings. */
(function () {
  var KEY = "ai-trending-lang";
  var DICT = {
    en: {
      search: "search",
      rating_saved: "rating saved",
      rating_error: "could not save rating",
      rating_none: "no ratings yet",
      rating_votes: "votes",
      rating_yours: "your score",
      rating_loading: "loading…",
      results: "results",
      results_one: "result",
      no_results: "no matches",
      search_placeholder: "search models, frameworks, standards…",
      type_to_search: "type to search",
      error_load: "could not load the index",
      updated: "updated",
      items: "items",
      discover: "discover live",
      discover_desc: "Search live sources and save results as a reference or as a topic for a category.",
      discover_placeholder: "search HN, GitHub, arXiv… (empty = trending now)",
      discover_search: "search",
      as_reference: "save as reference",
      as_topic: "save as topic",
      saved: "saved",
      save_failed: "could not save",
      forbidden: "writes are only allowed from your home network",
      not_json: "server did not respond with JSON",
      saved_reference: "reference saved — visible after the next crawl",
      saved_topic: "topic saved — active after the next crawl",
      save_in_category: "save in category",
      manage: "manage",
      manage_title: "Manage",
      manage_desc: "Add your own references, topics and sources. Changes apply at the next crawl.",
      references: "references",
      topics: "topics",
      sources: "sources",
      recent_changes: "recent changes",
      add: "add",
      remove: "remove",
      url: "url",
      title: "title",
      note: "note",
      category: "category",
      type: "type",
      query: "query or feed url",
      limit: "limit",
      keywords: "keywords (comma separated)",
      crawl_now: "crawl now",
      crawling: "crawl started — refresh in a few minutes",
      crawl_failed: "crawl could not start",
      load_error: "could not load data",
      none: "none",
      open: "open",
      no_query_placeholder: "leave empty for trending topics",
      added_ok: "added",
      removed_ok: "removed",
      lang_switch: "language",
      nothing_here: "nothing here yet",
      tip_ref: "Save this result as a reference in the selected category — visible after the next crawl (writes only from your home network)",
      tip_topic: "Save this title as an AI search-topic in the selected category — coverage grows from the next crawl (writes only from your home network)",
      tip_open: "Open the original source in a new tab",
      tip_remove: "Remove this entry from the configuration — it disappears at the next crawl",
      tip_discover_go: "Search the live sources above (empty query = what is trending right now)",
      tip_src_toggle: "Include this source in the live search",
      tip_save_cat: "Category where saved results end up"
    },
    nl: {
      search: "zoek",
      rating_saved: "beoordeling opgeslagen",
      rating_error: "beoordeling kon niet worden opgeslagen",
      rating_none: "nog geen beoordelingen",
      rating_votes: "stemmen",
      rating_yours: "jouw score",
      rating_loading: "laden…",
      results: "resultaten",
      results_one: "resultaat",
      no_results: "geen treffers",
      search_placeholder: "zoek modellen, frameworks, standaarden…",
      type_to_search: "typ om te zoeken",
      error_load: "index kon niet worden geladen",
      updated: "bijgewerkt",
      items: "items",
      discover: "live ontdekken",
      discover_desc: "Zoek in live bronnen en bewaar resultaten als referentie of als topic voor een categorie.",
      discover_placeholder: "zoek HN, GitHub, arXiv… (leeg = trending nu)",
      discover_search: "zoeken",
      as_reference: "als referentie opslaan",
      as_topic: "als topic opslaan",
      saved: "opgeslagen",
      save_failed: "kon niet opslaan",
      forbidden: "schrijven alleen vanaf je thuisnetwerk",
      not_json: "server antwoordde niet met JSON",
      saved_reference: "referentie opgeslagen — zichtbaar na de volgende crawl",
      saved_topic: "topic opgeslagen — actief na de volgende crawl",
      save_in_category: "opslaan in categorie",
      manage: "beheren",
      manage_title: "Beheren",
      manage_desc: "Voeg eigen referenties, topics en bronnen toe. Wijzigingen gelden bij de volgende crawl.",
      references: "referenties",
      topics: "topics",
      sources: "bronnen",
      recent_changes: "recente wijzigingen",
      add: "toevoegen",
      remove: "verwijderen",
      url: "url",
      title: "titel",
      note: "notitie",
      category: "categorie",
      type: "type",
      query: "query of feed-url",
      limit: "limiet",
      keywords: "sleutelwoorden (komma-gescheiden)",
      crawl_now: "nu crawlen",
      crawling: "crawl gestart — ververs over enkele minuten",
      crawl_failed: "crawl kon niet starten",
      load_error: "gegevens konden niet worden geladen",
      none: "geen",
      open: "openen",
      no_query_placeholder: "leeg laten voor trending topics",
      added_ok: "toegevoegd",
      removed_ok: "verwijderd",
      lang_switch: "taal",
      nothing_here: "nog niets",
      tip_ref: "Bewaar dit resultaat als referentie in de geselecteerde categorie — zichtbaar na de volgende crawl (schrijven alleen vanaf je thuisnetwerk)",
      tip_topic: "Bewaar deze titel als AI-zoektopic in de geselecteerde categorie — dekking groeit vanaf de volgende crawl (schrijven alleen vanaf je thuisnetwerk)",
      tip_open: "Opent de originele bron in een nieuw tabblad",
      tip_remove: "Verwijder dit uit de configuratie — verdwijnt bij de volgende crawl",
      tip_discover_go: "Doorzoek de live bronnen bovenaan (lege zoekopdracht = nu trending)",
      tip_src_toggle: "Neem deze bron mee in de live zoekopdracht",
      tip_save_cat: "Categorie waarin opgeslagen resultaten terechtkomen"
    }
  };

  function getLang() {
    var stored = null;
    try { stored = localStorage.getItem(KEY); } catch (e) { /* ignore */ }
    return stored === "nl" || stored === "en" ? stored : "en";
  }

  function setLang(lang) {
    if (lang !== "nl" && lang !== "en") return;
    try { localStorage.setItem(KEY, lang); } catch (e) { /* ignore */ }
    document.documentElement.lang = lang;
    var buttons = document.querySelectorAll(".lang-btn");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.toggle("is-active", buttons[i].getAttribute("data-lang") === lang);
    }
    document.dispatchEvent(new CustomEvent("langchange", { detail: { lang: lang } }));
  }

  function t(key) {
    var lang = getLang();
    return (DICT[lang] && DICT[lang][key]) || (DICT.en[key] || key);
  }

  /* data-tip-en/data-tip-nl attributen voor JS-gegenereerde elementen.
     De tooltip-engine in app.js leest beide talen en kiest op taal. */
  function tipAttr(key) {
    var en = (DICT.en[key] || key).replace(/"/g, "&quot;");
    var nl = (DICT.nl[key] || en).replace(/"/g, "&quot;");
    return 'data-tip-en="' + en + '" data-tip-nl="' + nl + '"';
  }

  window.I18N = { getLang: getLang, setLang: setLang, t: t, tipAttr: tipAttr, dict: DICT };

  document.documentElement.lang = getLang();
})();
