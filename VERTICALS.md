# CAE Vertical Expansion Playbook
**B2B Client Acquisition Engine — Vertical Configuration Guide**

---

## Overview

CAE's scoring and search engine is vertical-configurable. The vertical profile system (`config/industry_profiles.py`) allows any German-language B2B vertical to be onboarded in under 30 minutes without touching core pipeline code.

This document covers: current verticals, how to configure a new one, the vertical expansion roadmap, and the strategic playbook for geographic expansion.

---

## Current Production Verticals (v3)

15 production-ready vertical profiles, each with custom scoring bonuses, query variants, buy signal patterns, and negative signal filters.

### Tier 1 — High-Value B2B (score_bonus 6–8)

These verticals have the highest deal sizes, best intent signals, and respond best to personalized cold outreach.

| Vertical Key | Typical Deal Value | Best Use Case |
|---|---|---|
| `it_dienstleister` | €10k–€100k | Enterprise IT pipeline, MSP client acquisition |
| `beratungen` | €15k–€50k | Mandate acquisition for management/strategy consulting |
| `recruiting` | €5k–€20k/placement | Employer mandate outreach for recruiting firms |
| `pv_solar` | €8k–€40k | B2B solar installation pipeline, Gewerbe focus |

### Tier 2 — Proven B2B Verticals (score_bonus 4–6)

Strong fit for outreach agencies running client campaigns. Well-understood buyer psychology.

| Vertical Key | Typical Deal Value | Best Use Case |
|---|---|---|
| `agenturen` | €3k–€15k/month | Agency new business, retainer acquisition |
| `immobilienmakler` | €5k–€20k | Objektakquise pipeline, Eigentümer contacts |
| `steuerberater` | €3k–€10k/year | Mandate acquisition, Unternehmer contacts |
| `coaches` | €3k–€15k | High-ticket coaching, Erstgespräch pipeline |
| `sicherheitsdienst` | €5k–€25k | B2B Firmenkunden, Objektschutz |
| `handwerker` | €3k–€15k | Gewerbekunden, Sanierungsprojekte |

### Tier 3 — Local Service (score_bonus 3–4)

Workable but lower deal values; best for high-volume local campaigns.

| Vertical Key | Typical Deal Value | Best Use Case |
|---|---|---|
| `pflegeanbieter` | €2k–€8k | B2B contracts, facility/corporate partnerships |
| `fitnessstudios` | €2k–€5k | Firmenfitness contracts |
| `gastronomie` | €2k–€8k | Catering/Event contracts, B2B recurring |
| `arztpraxen` | €3k–€8k | Practice management, medical equipment |
| `reinigung` | €3k–€10k | Recurring Unterhaltsreinigung contracts |

---

## How to Add a New Vertical

Adding a new vertical requires editing one file: `config/industry_profiles.py`

### Step 1 — Research the Vertical

Before writing code, answer these questions:
1. What are 5–10 search terms for this industry in German?
2. What query patterns find these companies (GmbH search, Impressum-based, project-intent)?
3. What keywords on their website signal buying intent (Neugeschäft, Anfrage, Wachstum, etc.)?
4. What keywords signal low quality (Portal, Job, Karriere, Ratgeber, etc.)?
5. What's the outbound reason — why would this company want cold outreach?

### Step 2 — Add the Profile

Open `config/industry_profiles.py` and add your vertical to `INDUSTRY_PROFILES`:

```python
"your_vertical_key": {
    "terms": [
        "Primary Term",           # Most common industry name
        "Synonym 1",              # Alternative names
        "Synonym 2 GmbH",         # With legal form for precision
    ],
    "queries": [
        "{term} {loc} GmbH Kontakt",                    # Basic contact search
        "{term} {loc} Impressum Entscheider",           # Decision-maker focused
        "{term} {loc} Neukunden B2B Vertrieb",          # Intent-focused
        "{term} {loc} Angebot Beratung Kontakt",        # Consultation-intent
        "{term} {loc} wachsendes Unternehmen",          # Growth signals
    ],
    "buy_signals": [
        "Neugeschäft",        # New business signals
        "Neukunden",          # New customer intent
        "Wachstum",           # Growth language
        "Anfrage",            # Inquiry readiness
        "Expansion",          # Scaling intent
        # Add 5-15 industry-specific terms
    ],
    "negative_signals": [
        "Job",                # Job postings (not a business)
        "Karriere",           # Career pages
        "Portal",             # Directory/portal sites
        "Ratgeber",           # How-to content
        "Verein",             # Associations (not commercial)
        # Add industry-specific exclusions
    ],
    "outbound_reason": "One sentence: why this vertical needs cold outreach.",
    "score_bonus": 5,   # 3-8 depending on deal size + intent signal quality
}
```

### Step 3 — Test the Vertical

```bash
# Demo mode (no live search)
python mine.py --demo --industry "Your Vertical Term"

# Live test (small batch)
python mine.py -i "Your Vertical Term" -c "München" -n 10 --outreach none
```

Review `output/latest/leads.json` — check:
- Are results actually in the target vertical?
- Is the score distribution reasonable (>30% should be ≥60)?
- Are negative results filtered out?
- Is the contact data quality acceptable (email + name present on most)?

### Step 4 — Calibrate Scoring

If results are too noisy: add negative signals.
If results are too sparse: add broader terms or relax query specificity.
If scores are systematically low: increase `score_bonus` or add buy signal terms.

**Typical tuning cycle:** 2–3 test runs, 1–2 hours of calibration.

### Step 5 — Document the Profile

Add a row to the vertical table in this file and note:
- Typical deal value for the vertical's clients
- Best use case (who is running campaigns targeting this vertical)
- Any unusual scoring considerations

---

## Vertical Configuration Template

Copy this template when adding a new vertical:

```python
"VERTICAL_KEY": {
    "terms": [
        "TERM_1",
        "TERM_2",
        "TERM_3",
    ],
    "queries": [
        "{term} {loc} GmbH Kontakt",
        "{term} {loc} Impressum",
        "{term} {loc} B2B Anfrage Beratung",
        "{term} {loc} Neukunden Vertrieb",
        "{term} {loc} Wachstum Unternehmen",
    ],
    "buy_signals": [
        "SIGNAL_1",
        "SIGNAL_2",
        "SIGNAL_3",
    ],
    "negative_signals": [
        "Job",
        "Karriere",
        "Portal",
    ],
    "outbound_reason": "OUTBOUND_REASON",
    "score_bonus": 5,
}
```

---

## Geographic Expansion Playbook

### Current Scope: DACH (v3)

CAE is optimized for German-language markets:
- Germany (Deutschland) — primary
- Austria (Österreich) — impressum parsing compatible, query patterns work
- Switzerland (Schweiz/Suisse) — GmbH/AG recognition, .ch domains supported

### Phase 1: UK + Netherlands (Q3 2026)

**Why UK first:**
- Largest English B2B market in Europe
- Companies House provides structured contact data (equivalent to impressum)
- "Ltd" / "PLC" legal form detection is straightforward
- High deal values, strong outreach culture

**Required changes:**
- English message templates (first touch, follow-up, objection handling)
- Companies House scraping integration
- UK-specific negative signals (Companies House portal pages, etc.)
- `.co.uk` / `.com` domain scoring adjustments

**Netherlands:**
- Dutch is partially interpretable with German parsing (Impressum → KvK)
- Strong English-language B2B culture (Dutch companies respond to English outreach)
- High-value verticals: IT services, consultancies, agencies

### Phase 2: France + Poland (Q4 2026)

**France:** Requires French message templates + INFOGREFFE integration
**Poland:** Fast-growing B2B market, German-speaking decision-makers in many sectors

### Phase 3: EU Broadening (2027)

Target: Any EU market where the company registry provides structured contact data and the local B2B culture accepts cold email outreach.

---

## Vertical × Geography Matrix (Prioritized Expansion)

| Vertical | DE/AT/CH (now) | UK | NL | FR |
|---|---|---|---|---|
| IT Dienstleister | GA | High priority | High priority | Medium |
| Marketing Agencies | GA | High priority | High priority | Low |
| Management Consulting | GA | High priority | Medium | High priority |
| Recruiting | GA | High priority | High priority | Medium |
| Legal/Tax | GA | Medium | Low | Medium |
| PV/Solar | GA | Medium | Medium | Low |

Prioritization is based on: (1) deal size in target market, (2) cold email receptivity culture, (3) data availability from company registries.

---

## Custom Vertical Onboarding (Client-Facing)

For agency clients or enterprise buyers who need a custom vertical not in the production list:

**Included in setup:**
- 1-hour discovery call: understand target ICP, ideal company profile, signals
- Vertical profile configuration: terms, queries, signals
- 2 test runs with review and calibration
- Custom vertical added to client's CAE instance

**Typical timeline:** 1 business day from discovery to first live run.

**Verticals we've seen work well beyond the defaults:**
- Architektur & Ingenieurbüros
- Eventmanagement & MICE
- Messebau
- Logistik & Spedition
- Gerüstbau & Stahlbau
- Pharma Außendienst
- Versicherungsmakler
- Bauträger & Projektentwicklung
