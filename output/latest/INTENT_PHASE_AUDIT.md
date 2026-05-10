# INTENT PHASE AUDIT — B2B Bot

**Erstellt:** 2026-05-10 16:10 CEST  
**Bot-Pfad:** `C:\Users\micha\Desktop\Neuer Ordner\bot_builder\generated_bots\b2bbot`  
**Zweck:** Bestandsaufnahme aller Intent-/Jobdetail-/Smoke-Artefakte. Keine Codeänderungen.

---

## 1. Intent-Module (`modules/intent_*.py`)

| Nr | Datei | Phase | Zweck | Status |
|----|-------|-------|-------|--------|
| 1 | `intent_search_provider.py` | Pre-3.x | Serper/Tavily-Suche | Bestand (existierte vorher) |
| 2 | `intent_portal_url_classifier.py` | Pre-3.x | URL-Typ-Klassifikation (Jobdetail/Listing/Search/Company) | Bestand |
| 3 | `intent_relevance_filter.py` | Pre-3.x | Relevanz-Scoring für Job-Detail-Treffer | Bestand |
| 4 | `intent_portal_detail_resolver.py` | Pre-3.x | HTTP-Fetch Jobdetailseiten, Firmennamen-Extraktion (JSON-LD) | Bestand |
| 5 | `intent_job_detail_query_builder.py` | 3.14b | Query-Generator mit `relevance_focus` (broad/target_industry) | **NEU** |
| 6 | `intent_company_website_resolver.py` | 3.9 | Prüft Firmenname auf Gültigkeit (Blocklist generischer Begriffe) | **NEU — Preview** |
| 7 | `intent_company_website_search.py` | 3.10 | Serper-Suche nach offizieller Firmen-Website | **NEU — Preview** |
| 8 | `intent_website_verifier.py` | 3.11 | HTTP-Verifikation der gefundenen Website | **NEU — Preview** |
| 9 | `intent_contact_preview.py` | 3.12 | Generiert Kontakt/Impressum-URL-Kandidaten (KEIN HTTP) | **NEU — Preview** |
| 10 | `intent_contact_page_fetcher.py` | 3.13 | HTTP-Fetch Kontakt/Impressum-Seiten, Extraktion Email/Telefon | **NEU — Preview** |
| 11 | `intent_lead_preview_builder.py` | 3.14 | Aggregiert Pipeline-Ergebnisse zu Lead-Kandidaten | **NEU — Preview** |

---

## 2. Smoke-Skripte (`smoke_*.py`)

| Nr | Datei | Testet | Letzter Status |
|----|-------|--------|----------------|
| 1 | `smoke_intent_job_detail_query_builder.py` | Query-Builder (broad + target_industry) | SMOKE_OK |
| 2 | `smoke_intent_job_detail_target_live_test.py` | 3 Live-Queries target_industry → Klassifikation + Relevance | SMOKE_OK |
| 3 | `smoke_intent_target_dedup_funnel.py` | Deduplizierung Jobdetail-Kandidaten | SMOKE_OK |
| 4 | `smoke_intent_target_job_detail_fetch.py` | Portal-Detail-Resolver auf 3 deduplizierte URLs | SMOKE_OK |
| 5 | `smoke_intent_target_company_fit.py` | Company-Fit-Scoring gegen Marketingagentur/München | SMOKE_OK |
| 6 | `smoke_dashboard_intent_preview.py` | Dashboard-Route /api/intent-preview | SMOKE_OK |
| 7 | `smoke_intent_company_website_resolver.py` | Phase 3.9 | SMOKE_OK |
| 8 | `smoke_intent_company_website_search.py` | Phase 3.10 | SMOKE_OK |
| 9 | `smoke_intent_website_verifier.py` | Phase 3.11 | SMOKE_OK |
| 10 | `smoke_intent_contact_preview.py` | Phase 3.12 | SMOKE_OK |
| 11 | `smoke_intent_contact_page_fetcher.py` | Phase 3.13 | SMOKE_OK |
| 12 | `smoke_intent_lead_preview_builder.py` | Phase 3.14 | SMOKE_OK |

---

## 3. Output-Dateien (`output/latest/intent_*.json`)

| Nr | Datei | Quelle |
|----|-------|--------|
| 1 | `intent_job_detail_relevance.json` | Relevance-Filter-Output (Pre-3.x) |
| 2 | `intent_company_website_resolution_preview.json` | Phase 3.9 |
| 3 | `intent_company_website_search.json` | Phase 3.10 |
| 4 | `intent_website_verification.json` | Phase 3.11 |
| 5 | `intent_contact_preview.json` | Phase 3.12 |
| 6 | `intent_contact_page_fetch_preview.json` | Phase 3.13 |
| 7 | `intent_lead_preview.json` | Phase 3.14 |
| 8 | `intent_job_detail_target_live_test.json` | Live-Test Smoke |
| 9 | `intent_target_dedup_funnel.json` | Dedup Smoke |
| 10 | `intent_target_job_detail_fetch.json` | Detail-Fetch Smoke |
| 11 | `intent_target_company_fit.json` | Company-Fit Smoke |

---

## 4. Experiment/Preview vs. Produktiv

### Nur Experiment/Preview (Phase 3.9–3.14):
- `intent_company_website_resolver.py` → testete Firmennamen-Validierung
- `intent_company_website_search.py` → testete Serper-Website-Suche
- `intent_website_verifier.py` → testete HTTP-Website-Verifikation
- `intent_contact_preview.py` → testete URL-Generierung ohne HTTP
- `intent_contact_page_fetcher.py` → testete Kontaktseiten-Extraktion
- `intent_lead_preview_builder.py` → testete Lead-Aggregation

Diese Module wurden mit nur 1–2 Firmen getestet (Ipsos, Marketing und E-Commerce). Sie sind funktional, aber nie im Produktivdurchlauf gelaufen. Ergebnis damals: `no_lead_created` — Ipsos hatte keine erreichbare Kontaktseite.

### Teilproduktiv (neuer Code mit realer Verbesserung):
- `intent_job_detail_query_builder.py` → `relevance_focus=target_industry` liefert 282 fokussierte Queries (vs. 45 broad). **Echte Verbesserung.**

### Produktiv (existierte vor den Phasen):
- `intent_search_provider.py`
- `intent_portal_url_classifier.py`
- `intent_relevance_filter.py`
- `intent_portal_detail_resolver.py`

---

## 5. Sicher isolierte Teile

**Alle neuen Module und Smokes sind isoliert:**
- Keines wird von `mine.py` importiert
- Keines wird von `outreach_pipeline.py` importiert
- Keines wird von `exporter.py` importiert
- Keines wird von `customer_outputs.py` importiert
- Die Module importieren sich nur untereinander (Preview-Chain) oder nutzen `modules/intent_search_provider.py`
- Smokes sind eigenständige `__main__`-Scripts ohne Import in andere Module

**Einzige Berührung mit dem Bestand:**
- `cockpit_server.py` → Route-Normalisierung für `/api/intent-preview` (reiner Dashboard-Fix, kein Bot-Eingriff)
- `modules/intent_company_website_search.py` und `intent_contact_page_fetcher.py` nutzen `intent_search_provider.py` (nur für Serper-Key)

---

## 6. Dateien, die NICHT in den alten Bot eingreifen

| Datei | Greift ein? | Wie? |
|-------|-------------|------|
| Alle `modules/intent_*` (neu) | **NEIN** | Werden nirgends importiert außer untereinander |
| Alle `smoke_intent_*` | **NEIN** | Nur `if __name__ == "__main__"` |
| `cockpit_server.py` | **Minimal** | Nur neue Route `/api/intent-preview` + Trailing-Slash-Normalisierung |
| `output/latest/intent_*` | **NEIN** | Nur JSON-Ausgaben |

---

## 7. Wurde `mine.py` verändert?

**NEIN.** `mine.py` wurde zu keinem Zeitpunkt angefasst.

---

## 8. Wurde `outreach_pipeline.py` verändert?

**NEIN.** `outreach_pipeline.py` wurde zu keinem Zeitpunkt angefasst.

---

## 9. Wurde Versandlogik verändert?

**NEIN.** `exporter.py`, `customer_outputs.py`, Mail-Texte, Telegram-Sends — nichts davon wurde verändert.

---

## 10. Fachlich sinnvolle Phasen

### Hoch sinnvoll:
- **Phase 3.14b (target_industry Query-Builder):** `relevance_focus=target_industry` ist die stärkste Verbesserung. Statt generischer "Sales Manager München"-Queries liefert es 282 gezielte Queries mit Agenturbezug, Stadtfilter und Negativ-Keywords. Die Live-Test-Ergebnisse (Seokratie, THE MARKETER, PWG) zeigen echte Marketingagenturen statt Bosch/Diageo. → **behalten + integrieren**

- **Smoke-Kette target_industry (Live-Test → Dedup → Fetch → Company Fit):** Zeigt den kompletten Flow von Query bis Company-Fit und beweist, dass der target_industry-Modus echte Treffer liefert. → **behalten als Test-Asset**

### Eingeschränkt sinnvoll:
- **Phase 3.9 (Company Name Resolution):** Die `is_likely_real_company_name()`-Funktion ist nützlich, aber aktuell nur eine Textheuristik ohne Abgleich mit echten Datenbanken. → **parken**

- **Phase 3.14 (Lead Preview Builder):** Die Aggregationslogik ist solide, aber die Schwachstelle liegt in Phase 3.13 (kaum erreichbare Kontaktseiten). → **parken bis Kontaktfindung verbessert**

---

## 11. Zu joblastig / zu riskant

### Riskant:
- **Phase 3.10–3.13 (Website-Suche → Kontakt-Fetch):** Diese Chain macht HTTP-Requests auf externe Seiten (Serper + direkte Webseiten-Fetches). Bei Massenläufen riskant wegen Rate-Limits, IP-Sperren, und rechtlichen Grauzonen (Impressum-Scraping). Aktuell auf 1–3 URLs limitiert — sicher, aber skaliert nicht. → **parken, nur mit explizitem Limit nutzen**

### Joblastig (zu viele Queries/Requests):
- **target_industry mit 282 Queries:** Viel zu viele für Live. Muss auf 5–10 gecappt werden. → **Limit im Integrationsfall zwingend**

---

## 12. Empfehlung

| Artefakt | Empfehlung | Begründung |
|----------|------------|------------|
| `intent_job_detail_query_builder.py` (target_industry) | **BEHALTEN + INTEGRIEREN** | Echte Verbesserung, isoliert, kein Bot-Eingriff |
| `smoke_intent_job_detail_query_builder.py` | **BEHALTEN** | Validiert beide Modi |
| Smoke-Kette (live_test → dedup → fetch → company_fit) | **BEHALTEN** | Beweist End-to-End target_industry |
| `intent_company_website_resolver.py` (3.9) | **PARKEN** | Nützlich, aber nur Textheuristik |
| `intent_company_website_search.py` (3.10) | **PARKEN** | Serper-Abhängigkeit, skaliert nicht |
| `intent_website_verifier.py` (3.11) | **PARKEN** | HTTP-Requests, Rate-Limit-Risiko |
| `intent_contact_preview.py` (3.12) | **PARKEN** | Kein HTTP, harmlos — aber ohne 3.13 nutzlos |
| `intent_contact_page_fetcher.py` (3.13) | **PARKEN** | Scraping-Risiko, kaum verwertbare Ergebnisse im Test |
| `intent_lead_preview_builder.py` (3.14) | **PARKEN** | Gute Logik, aber abhängig von 3.10–3.13 |
| Smoke-Skripte zu 3.9–3.14 | **PARKEN** | Mit Modulen parken |
| `intent_portal_detail_resolver.py` | **BEHALTEN** | Existierte vorher, funktioniert gut (JSON-LD) |
| `cockpit_server.py` Änderung | **BEHALTEN** | Reiner Bugfix, kein Bot-Eingriff |

---

## Zusammenfassung

- **11 Intent-Module** (4 Bestand, 7 neu)
- **12 Smoke-Skripte** (alle SMOKE_OK)
- **11 Output-JSONs** im `output/latest/`
- **0 Eingriffe** in `mine.py`, `outreach_pipeline.py`, Versandlogik
- **1 minimale Änderung** an `cockpit_server.py` (Route-Normalisierung)
- **1 echte Verbesserung:** `relevance_focus=target_industry` im Query-Builder
- **6 Preview-Module** (3.9–3.14) die nie produktiv liefen → parken
- **0 Datenverlust**, **0 Broken Builds**, alle Commits sauber

**Nächster sinnvoller Schritt:** target_industry Query-Builder in den echten Intent-Pipeline-Durchlauf integrieren (mit Query-Limit 5–10), alles andere parken.
