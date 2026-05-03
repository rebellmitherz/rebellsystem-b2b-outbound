# CAE Module Breakdown
**B2B Client Acquisition Engine v3 — Premium Module Structure**

---

## Module Architecture Overview

CAE is built as eight loosely coupled modules. Each module can be used standalone or chained through the pipeline. The architecture (`cae/layers/` + `cae/extensions/`) is designed for extension without modification of core modules.

```
┌─────────────────────────────────────────────────────┐
│  CORE TIER  (all plans)                             │
│  M1 Research Engine · M2 Scoring · M3 Outreach      │
│  M4 Reply Intelligence · M5 Reporting               │
├─────────────────────────────────────────────────────┤
│  PRO TIER                                           │
│  M6 Vertical Configurator · M7 Batch Operations     │
├─────────────────────────────────────────────────────┤
│  ENTERPRISE / ADD-ON (roadmap + partial beta)       │
│  M8 LinkedIn Intelligence · M9 CRM Bridge           │
└─────────────────────────────────────────────────────┘
```

---

## M1 — Research Engine

**What it does:** Discovers companies matching a vertical + geographic target, extracts contact data from open web sources.

**Location:** `modules/search.py` · `modules/scraper.py` · `cae/layers/research_engine/`

**Capabilities:**
- DuckDuckGo search with 5 vertical-optimized query variants per run
- LinkedIn company search URL generation for manual or semi-automated enrichment
- Website scraping: impressum parsing (German legal requirement), contact page detection, team page signals
- Email extraction with domain-match validation
- Phone number extraction (DE/AT/CH format)
- Legal form detection (GmbH, AG, GbR, Einzelunternehmen, etc.)
- Parallel impressum scanning for speed
- Domain blocklist: filters LinkedIn, XING, directories, portals, news sites

**Input:** industry string + city/region + lead count limit
**Output:** Raw lead objects with contact data + extraction metadata

**Status:** GA (General Availability)

---

## M2 — Lead Scoring & Qualification

**What it does:** Assigns a 0–100 contact quality score to every lead using 12+ weighted signals. Classifies leads as Premium (≥60), Warm (40–59), or Cold (<40).

**Location:** `modules/scorer.py` · `config/industry_profiles.py` · `config/signals.py`

**Scoring Model (rule-based, fully transparent):**

| Signal | Max Points | Notes |
|---|---|---|
| Verified email (domain match) | 22 | Penalizes generic domains |
| Named contact person | 18 | Director/GF name > generic "Team" |
| Phone number present | 12 | Increases multi-touch reach |
| Legal form (GmbH/AG) | 12 | Proxy for business maturity + deal size |
| Impressum found | 10 | Compliance indicator + data quality |
| Team page present | 8 | Signals organizational structure |
| Premium domain | 6 | .de / .at / .ch vs directory subdomains |
| Buy signal match | +variable | Industry profile bonus (4–8 pts) |
| Negative signal match | −variable | Penalizes inactive, closed, hobby projects |

**Industry Profiles:** 15 vertical profiles with custom scoring bonuses, query variants, and signal dictionaries. New profiles configurable in `config/industry_profiles.py` in <30 min.

**Status:** GA

---

## M3 — Personalized Outreach Engine

**What it does:** Generates personalized first-touch emails and 2-step follow-up sequences per lead. Manages the full send pipeline with explicit approval gates.

**Location:** `cae/messaging/` · `modules/outreach_pipeline.py` · `modules/outreach_safety.py`

**Messaging Architecture:**
- **Lead type inference:** ICP-hot / forwarding-gatekeeper / neutral contact
- **Tone selection:** b2b_formal / beratung_warm / consultant
- **Per-lead generation:** 3–5 subject line variants, email body with company/person anchors, CTA selection (meeting / soft inquiry)
- **Follow-up sequences:** Day 3 + Day 7+ variants with smart conditions (stops on reply, stops on negative classification)

**Safety Pipeline:**
```
search → preview → [HUMAN APPROVE] → send → IMAP sync → reply poll
```
- `preview` mode: generates all outreach, writes to file, no sending
- `approve`: batch approval gate — required before send
- `send`: SMTP dispatch with MX validation, deduplication, rate limiting, retry logic
- Enterprise company blocklist, generic email filter, GDPR-compatible dry-run mode

**Outreach Modes:**
| Mode | Description |
|---|---|
| `none` | Search + score only, no outreach |
| `preview` | Generate emails, no sending |
| `approve` | Approve batch for sending |
| `send` | Dispatch approved batch |
| `send-reply-drafts` | Send + create IMAP draft replies |
| `full-auto` | Search → approve → send → auto-reply (SMTP) |
| `process-replies` | IMAP poll + classify inbound |

**Status:** GA

---

## M4 — Reply Intelligence

**What it does:** Monitors the IMAP inbox for inbound replies, classifies every reply into an actionable category, and queues suggested responses for human review or auto-send.

**Location:** `modules/reply_intelligence.py` · `modules/reply_processor.py` · `cae/messaging/reply_taxonomy.py`

**Classification Categories:**

| Class | Description | Auto-Action |
|---|---|---|
| `interested` | Positive, wants to learn more | Draft meeting proposal |
| `positive` | Enthusiastic, explicit buy signal | Flag as hot lead, draft reply |
| `later` | Good timing mismatch, wants follow-up | Pause sequences, note date |
| `no_need` | Polite decline, current solution | Mark `do_not_resend` |
| `wrong_person` | Forwarded / wrong contact | Draft re-route request |
| `send_materials` | Wants case study / deck first | Draft materials send + follow-up |
| `not_interested` | Hard no | Mark `do_not_resend`, end sequence |
| `unclear` | Ambiguous, needs human judgment | Queue for human review |

**Reply Processing:**
- Continuous IMAP polling with Message-ID deduplication
- Persistent reply queue (`output/reply_queue.json`)
- Event log (`output/reply_events.json`) for audit trail
- Auto-reply options: template-based (no LLM) or gpt-4o-mini (OpenAI optional)
- Manual review queue for ambiguous or sensitive replies

**Objection Handling Library:** Pre-built responses for common B2B objections (cost, timing, incumbent vendor, decision-maker hierarchy, legal/procurement gatekeeping).

**Meeting Conversion Module:** Calendar-ready scheduling snippets, time slot proposals, soft close language variants.

**Status:** GA

---

## M5 — Reporting & Campaign Analytics

**What it does:** Generates per-run reports in multiple formats, from machine-readable JSON to sales-ready markdown briefs.

**Location:** `modules/exporter.py` · `modules/expose_generator.py` · `modules/customer_outputs.py`

**Report Types:**

| Report | Format | Audience |
|---|---|---|
| Campaign Brief | Markdown | Agency client deliverable |
| Hot Lead Hotlist | Markdown + CSV | Sales/SDR handoff |
| Action Plan | Markdown | Daily ops guide |
| Run Report | JSON | Pipeline integration / logging |
| Summary Statistics | JSON | Score distribution, quality metrics |
| Quality Warnings | JSON | Domain issues, negative signals |
| Messaging Assist Bundle | 5× JSON | SDR/copywriter working files |

**Messaging Assist Bundle Contents:**
- `first_touch_messages.json` — Per-lead subject + body variants
- `followup_sequences.json` — Multi-day library with conditions
- `reply_templates.json` — 7 categories with draft responses
- `objection_handling.json` — Common objections + counters
- `meeting_conversion_texts.json` — Scheduling snippets, time slots

**Status:** GA

---

## M6 — Vertical Configurator

**What it does:** Enables configuration of new industry verticals without code changes, using structured profile definitions.

**Location:** `config/industry_profiles.py` · `config/signals.py`

**Vertical Profile Schema:**

```python
{
    "terms": [...],          # Industry search terms
    "queries": [...],        # Query templates with {term} + {loc} slots
    "buy_signals": [...],    # Intent keywords that boost score
    "negative_signals": [...], # Exclusion keywords that reduce score
    "outbound_reason": "...", # Why this vertical needs outreach (messaging context)
    "score_bonus": int,       # Base score bonus for vertical match (0–10)
}
```

**Adding a new vertical:** Edit `config/industry_profiles.py`, add a new key to `INDUSTRY_PROFILES`. No other code changes needed. The pipeline auto-discovers profiles on load.

**Current production profiles:** 15 verticals (see PRODUCT.md)

**Status:** GA

---

## M7 — Batch Operations

**What it does:** Runs multi-industry × multi-city campaigns in a single invocation, producing separate output directories per combination.

**Location:** `cae/cli/app.py` · `cae/pipeline/core.py`

**Capabilities:**
- Comma-separated industries: `-i "Marketingagentur,IT Dienstleister"`
- Comma-separated cities: `-c "München,Berlin,Hamburg"`
- Produces M×N run directories, each with full reporting
- Configurable per-run lead limits
- Shared outreach pipeline across all batch combinations

**Example:** 3 industries × 5 cities = 15 separate scored lead lists + 15 campaign briefs in one run.

**Status:** GA

---

## M8 — LinkedIn Intelligence (Beta)

**What it does:** Enhances person-level decision-maker identification using LinkedIn search signal enrichment.

**Location:** `cae/extensions/linkedin/` · `linkedin_bot/`

**Current State (Beta):**
- LinkedIn company search URL generation
- Person disambiguation logic
- Decision-maker scoring (GF, CEO, Inhaber, Geschäftsführer signals)
- Web-based (no LinkedIn API — scraping-optional architecture)

**Roadmap:**
- Profile-level enrichment (title, seniority, tenure)
- Multi-person contact extraction per company
- LinkedIn message sequence generation (separate from email outreach)

**Status:** Beta — functional for URL generation + person signals; full profile enrichment is roadmap

---

## M9 — CRM Bridge (Roadmap)

**What it does:** Pushes lead status, pipeline stage, and reply events to external CRM systems or a local SQLite mirror.

**Location:** `cae/extensions/crm_bridge/` (planned) · `cae/layers/pipeline_crm/`

**Planned Capabilities:**
- Pipedrive native integration (deals + contacts)
- HubSpot CRM push (contacts + activity timeline)
- SQLite local mirror for multi-run analytics
- Webhook delivery on stage change (interested / positive / meeting_booked)

**Status:** Architecture in place (`cae/layers/pipeline_crm/`, `cae/extensions/contracts.py`); implementation roadmap Q3 2026

---

## Extension Architecture

CAE uses a layered extension model that allows new capabilities to be added without modifying core modules:

```
cae/
├── layers/          # Modular capability layers (touch points for extensions)
│   ├── research_engine/
│   ├── lead_factory/
│   ├── linkedin_intelligence/
│   ├── outreach/
│   ├── conversation/
│   └── pipeline_crm/
└── extensions/      # Pluggable add-ons
    ├── linkedin/
    ├── quality_gates/
    ├── contracts.py  # Protocol stubs for type-safe extension contracts
    └── README.md
```

New extensions implement the Protocol stubs in `contracts.py` and register with the relevant layer — no changes to `modules/` required.
