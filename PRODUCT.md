# B2B Client Acquisition Engine
**CAE v3 — Precision Outreach Infrastructure for B2B Service Providers**

---

## What It Is

CAE is a self-hosted B2B lead acquisition engine that finds decision-maker contacts in target verticals, scores them for outreach readiness, generates personalized email sequences, and manages replies end-to-end — all with human-in-the-loop approval gates before any email leaves the building.

**One sentence:** CAE replaces 40+ hours/month of manual prospecting with an autonomous, approval-gated pipeline that goes from zero to inbox-ready outreach in under 5 minutes per vertical.

---

## Positioning

> *The outreach infrastructure that agencies and B2B service providers use to fill their pipeline without a sales team — or to power their clients' pipelines at scale.*

CAE is not a lead database subscription. It is not a spray-and-pray email blaster. It is a research + qualification + outreach engine that treats each contact as a lead worth a human's attention — and automates everything except the decision to send.

---

## Who It's For

**Primary ICP:** B2B service providers and agencies with average deal sizes of €5,000–€50,000 that need 5–15 qualified discovery calls per month and lack a dedicated prospecting team.

| Buyer Segment | Typical Need | CAE Outcome |
|---|---|---|
| Marketing & Digital Agencies | Fill retainer pipeline | 15–40 qualified agency contacts per run, personalized first-touch ready |
| IT Consultancies & MSPs | Enterprise pipeline | Decision-maker contacts with legal form + buy signal scoring |
| Management Consultants | Partner/mandate acquisition | Named GF/CEO contacts with impressum-verified email |
| Recruiting Firms | Employer mandate pipeline | Verified HR/GF contacts at growing companies |
| B2B SaaS / Tech Vendors | Outbound in new verticals | Vertical-specific scoring profiles + message variants |
| Outreach Agencies | Client campaign delivery | Multi-industry, multi-city batch runs with per-client report bundles |

**Secondary ICP:** Outreach agencies running campaigns on behalf of multiple B2B clients — CAE's batch mode, per-run reporting, and messaging assist bundles map directly to client deliverables.

---

## What It Delivers (Outcomes, Not Features)

| Deliverable | Description |
|---|---|
| Qualified contact list | 15–200 scored companies with contact name, verified email, phone, legal form |
| Personalized outreach bundle | Per-lead first-touch email + 2 follow-up variants, subject line options, CTA selection |
| Reply intelligence | Every inbound reply classified into 7 categories with suggested responses, no manual triage |
| Hot lead handoffs | Named contacts with reply excerpts + recommended next step, ready for sales handoff |
| Campaign brief | Markdown report: top prospects, action plan, quality warnings, run statistics |
| Messaging assist bundle | 5 JSON files per run: first-touch, follow-up sequences, reply templates, objection handling, meeting conversion |

---

## Core Mechanics

```
Search → Scrape → Score → Generate → Approve → Send → Classify → Report
```

1. **Search** — DuckDuckGo + LinkedIn URL builder finds companies matching vertical + location query
2. **Scrape** — Website + impressum parsing extracts: contact person, email, phone, legal form, team page signals, buy signals
3. **Score** — Rule-based 0–100 contact quality score using 12+ weighted signals (email domain match, contact name, legal form, impressum, buy signals, negative signals)
4. **Generate** — Per-lead message variants: lead type inference (ICP-hot / gatekeeper / neutral), tone selection (b2b_formal / beratung_warm / consultant), 3–5 subject line options
5. **Approve** — Preview → human approval required before any email is queued for sending
6. **Send** — SMTP with rate limiting, MX validation, deduplication, per-contact retry logic, sent-folder IMAP sync
7. **Classify** — IMAP reply polling classifies every inbound into: interested / positive / later / no_need / wrong_person / send_materials / not_interested / unclear; ambiguous replies go to human review queue
8. **Report** — Timestamped run directory with all outputs: JSON, CSV, markdown campaign brief, messaging assist bundle

---

## Integration Requirements

| Component | Requirement | Notes |
|---|---|---|
| Runtime | Python 3.8+ | Tested on 3.10, 3.11 |
| Email sending | SMTP (IONOS default) | Any SMTP provider works via .env config |
| Reply polling | IMAP (IONOS default) | Any IMAP provider works |
| AI messaging (optional) | Anthropic Claude API | Template fallback works without API key |
| Reply suggestions (optional) | OpenAI API (gpt-4o-mini) | Template fallback works without API key |
| Disk space | ~500 MB | For output/runs/ persistence across campaigns |
| No database required | File-based state | All state in JSON (output/); no external DB needed |

---

## Supported Verticals (v3)

15 production-ready vertical profiles with custom scoring bonuses, query variants, buy signal patterns, and negative signal filters:

`agenturen` · `it_dienstleister` · `beratungen` · `immobilienmakler` · `handwerker` · `pflegeanbieter` · `coaches` · `steuerberater` · `recruiting` · `pv_solar` · `sicherheitsdienst` · `fitnessstudios` · `gastronomie` · `arztpraxen` · `reinigung`

Any industry can be run via free-text input with default scoring. Custom vertical profiles can be added in `config/industry_profiles.py` in under 30 minutes.

---

## Safety & Compliance Design

CAE was built around explicit approval gates at every outreach step:

- No email sends without `preview → approve` workflow
- MX record DNS validation before every outbound
- Enterprise company blocklist (blocks Siemens, SAP, etc. without bypass flag)
- Generic email blocking (no info@, support@, noreply@)
- `do_not_resend` flag on negative replies — permanently blocks follow-ups
- GDPR-compatible by design: no auto-sending, dry-run modes, explicit human oversight
- All outreach state persisted with audit trail (sent timestamps, reply events log)

---

## Output Structure

Each run produces a timestamped directory:

```
output/runs/{YYYY-MM-DD_HH-MM-SS}_{type}_{city}/
├── leads.json                    # Scored lead objects
├── hot_leads.json                # Premium leads (score ≥60)
├── campaign_brief.md             # Human-readable executive summary
├── hotlist.md                    # Top prospects with contact + context
├── action_plan.md                # Prioritized next steps
├── run_report.json               # Full metadata + statistics
├── quality_warnings.json         # Negative signals, domain issues
└── messaging_assist/
    ├── first_touch_messages.json
    ├── followup_sequences.json
    ├── reply_templates.json
    ├── objection_handling.json
    ├── meeting_conversion_texts.json
    └── HOW_TO_USE.md
```

---

## Version History

| Version | Milestone |
|---|---|
| v1 | Basic search + scrape + export |
| v2 | Scoring engine + outreach pipeline + reply classification |
| v3 (current) | Messaging assist bundles + batch mode + extensions architecture + quality gates |
| v4 (roadmap) | CRM bridge + multi-sender + analytics dashboard |
