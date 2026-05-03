# B2B Client Acquisition Engine (CAE v3)

**Precision outreach infrastructure for B2B service providers and outreach agencies.**  
From zero to inbox-ready, scored, personalized outreach in under 5 minutes per vertical.

---

## What It Does

CAE finds decision-maker contacts at German-language B2B companies, scores each contact for outreach readiness, generates personalized first-touch emails and follow-up sequences, dispatches them through an approval-gated SMTP pipeline, and classifies every inbound reply — end to end.

This is not a lead database subscription. CAE scrapes fresh data from company websites and German impressums on every run, which means contacts are current and validated.

**Core pipeline:**
```
Search → Scrape → Score → Generate → Approve → Send → Classify → Report
```

---

## Product Documentation

| Document | Contents |
|---|---|
| [PRODUCT.md](PRODUCT.md) | Crisp product definition, ICP, mechanics, integration requirements |
| [MODULES.md](MODULES.md) | Premium module breakdown — what each module does and its status |
| [POSITIONING.md](POSITIONING.md) | Sales-facing positioning, ROI framing, objection handling |
| [ROADMAP.md](ROADMAP.md) | v3 through v5 — what's built, what's next, what unlocks each pricing tier |
| [VERTICALS.md](VERTICALS.md) | Vertical playbook — 15 production profiles + how to add new ones |
| [GAP_ANALYSIS.md](GAP_ANALYSIS.md) | Blunt assessment — what's already premium vs. what blocks €5k/month |
| [BETRIEB.md](BETRIEB.md) | Daily operations guide (German) — SMTP, approval flow, reply polling |

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env: add SMTP/IMAP credentials

# Search and score only (no outreach)
python mine.py -i "IT Dienstleister" -c "München" -n 25

# Batch: multiple industries × cities
python mine.py -i "Marketingagentur,IT Dienstleister" -c "München,Berlin" -n 50

# Demo mode (no live search, no credentials needed)
python mine.py --demo --industry "Marketingagentur"

# CSV import and enrichment
python mine.py --input-csv leads.csv --mode enrich
```

---

## Outreach Pipeline

```bash
# Step 1: Preview outreach (generates emails, does NOT send)
python mine.py --outreach preview

# Step 2: Approve batch for sending
python mine.py --outreach approve

# Step 3: Send approved batch (respects outreach-limit)
python mine.py --outreach send --outreach-limit 10

# Step 4: Poll for replies and classify
python mine.py --outreach process-replies

# View hot leads ready for handoff
python mine.py --outreach handoffs

# Campaign status summary
python mine.py --outreach status
```

**All outreach requires explicit approval before any email is dispatched.** No accidental sends.

---

## Supported Verticals (v3)

15 production-ready vertical profiles with custom scoring, query variants, and buy signal patterns:

`IT Dienstleister` · `Marketingagentur` · `Unternehmensberatung` · `Immobilienmakler` · `Steuerberater` · `Handwerker` · `Recruiter` · `PV/Solar` · `Coach/Berater` · `Sicherheitsdienst` · `Pflegeanbieter` · `Fitnessstudio` · `Gastronomie` · `Arztpraxis` · `Reinigungsfirma`

Any industry runs with free-text input. Custom vertical profiles take ~30 minutes to configure (see [VERTICALS.md](VERTICALS.md)).

---

## What Each Run Produces

```
output/runs/{timestamp}_{type}_{city}/
├── leads.json                    # Scored lead objects (all)
├── hot_leads.json                # Premium leads (score ≥60)
├── campaign_brief.md             # Executive summary + action plan
├── hotlist.md                    # Top prospects with contact + context
├── run_report.json               # Full metadata + statistics
├── quality_warnings.json         # Negative signals, domain issues
└── messaging_assist/
    ├── first_touch_messages.json  # Per-lead subject + body variants
    ├── followup_sequences.json    # Multi-day follow-up library
    ├── reply_templates.json       # 7 reply categories + drafts
    ├── objection_handling.json    # Common objections + counters
    ├── meeting_conversion_texts.json
    └── HOW_TO_USE.md
```

---

## Environment Configuration

Copy `.env.example` to `.env` and configure:

```env
# Email sending (SMTP — any provider)
IONOS_SMTP_HOST=smtp.ionos.de
IONOS_SMTP_PORT=587
IONOS_SMTP_USER=you@yourdomain.de
IONOS_SMTP_PASS=yourpassword

# Reply polling (IMAP)
IONOS_IMAP_HOST=imap.ionos.de
IONOS_IMAP_PORT=993
IONOS_IMAP_USER=you@yourdomain.de
IONOS_IMAP_PASS=yourpassword

# Optional: AI-enhanced messaging
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Safety defaults (leave these as-is to start)
REPLY_AUTO_SEND=false
REPLY_DRY_RUN=0
```

---

## Architecture

```
b2bbot/
├── cae/                 # Core acquisition engine
│   ├── cli/             # CLI orchestrator
│   ├── pipeline/        # Pipeline coordination
│   ├── layers/          # Modular capability layers
│   ├── messaging/       # Outreach generation + reply handling
│   └── extensions/      # Pluggable add-ons (LinkedIn, CRM bridge)
├── modules/             # Implementation modules
│   ├── search.py        # Search + LinkedIn URL builder
│   ├── scraper.py       # Website + impressum parsing
│   ├── scorer.py        # Rule-based 0–100 scoring
│   ├── outreach_pipeline.py  # SMTP send + IMAP sync
│   ├── reply_intelligence.py # Reply classification
│   └── ...
├── config/              # Vertical profiles, signals, defaults
└── output/              # Run outputs (JSON, CSV, markdown)
```

Extensions implement Protocol stubs in `cae/extensions/contracts.py` and register with the relevant layer — no modifications to `modules/` required.

---

## Lead Score Interpretation

| Score | Classification | Recommended Action |
|---|---|---|
| ≥60 | Premium | First-touch ready, prioritize |
| 40–59 | Warm | Second-priority, review before send |
| <40 | Cold | Manual review required before outreach |

Score signals: verified email (domain-matched), named contact person, legal form (GmbH/AG), impressum present, team page, buy signal matches, negative signal penalties.

---

## Safety & Compliance

- No sends without explicit `preview → approve` workflow
- MX record DNS validation before every outbound
- Enterprise blocklist prevents large-corp sends without explicit bypass
- Generic email addresses blocked (no info@, support@, noreply@)
- `do_not_resend` permanently blocks follow-ups on negative replies
- Dry-run mode for full audit without any email dispatch
- All state persisted with timestamps for audit trail

---

## Dependency Summary

| Package | Purpose |
|---|---|
| `ddgs` | DuckDuckGo search (no API key needed) |
| `requests` + `beautifulsoup4` | Website scraping |
| `dnspython` | MX record validation |
| `anthropic` | Claude API for AI-enhanced messaging (optional) |
| `python-dotenv` | Environment configuration |

No database required. All state is file-based (output/ directory).

---

## Related Documentation

- Daily operations and scheduling: [BETRIEB.md](BETRIEB.md)
- Extension architecture: [cae/extensions/README.md](cae/extensions/README.md)
- Vertical expansion: [VERTICALS.md](VERTICALS.md)
- What's next: [ROADMAP.md](ROADMAP.md)
- Sales conversations: [POSITIONING.md](POSITIONING.md)
