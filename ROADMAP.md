# CAE Product Roadmap
**B2B Client Acquisition Engine — v3 through v5**

---

## Roadmap Philosophy

CAE's roadmap is organized around three axes:
1. **Reliability** — making existing capabilities production-hardened for agency use
2. **Intelligence** — adding AI-powered layers that increase reply rates and reduce manual work
3. **Scale** — enabling multi-client, multi-campaign operations at 10× the current throughput

Features are sequenced by what unblocks the next pricing tier. The v3 → v4 jump (Q2–Q3 2026) closes the gap from €5k/month to a justifiable €8–10k/month offer for agencies. v5 targets enterprise/white-label territory.

---

## Now — v3 (Current, GA)

**Theme: Solid pipeline, German market depth, approval-gated outreach**

| Module | Status | Notes |
|---|---|---|
| Lead discovery (DuckDuckGo + impressum) | GA | 15+ DACH verticals |
| Rule-based scoring (0–100) | GA | 12+ weighted signals |
| Personalized outreach generation | GA | Lead type + tone inference |
| 2-step follow-up sequences | GA | Smart stop on reply |
| Reply classification (7 categories) | GA | Keyword + rule-based |
| Objection handling library | GA | Pre-built for DACH B2B |
| Meeting conversion module | GA | Calendar snippets |
| Batch multi-industry × multi-city | GA | M×N run directories |
| Campaign brief + hotlist reports | GA | Markdown + JSON |
| Messaging assist bundle | GA | 5 JSON files per run |
| IMAP reply polling + SMTP sending | GA | IONOS default, configurable |
| Quality gates (MX, enterprise blocklist) | GA | GDPR-compatible |
| 15 vertical profiles | GA | Configurable without code |
| Extension architecture (contracts.py) | GA | Protocol stubs for safe extension |
| LinkedIn URL generation | Beta | URL building, not API |
| CSV import + enrichment | GA | Deduplication, merge |

---

## Q2 2026 — v3.5 (Stability + Onboarding)

**Theme: Removing friction that blocks non-technical buyers**

### Deliverables

**Setup Wizard (High Priority)**
- Interactive `setup.py` that validates SMTP/IMAP credentials, writes `.env`, and runs a smoke test
- Removes the #1 support issue: misconfigured .env files
- Target: zero-error onboarding in under 15 minutes

**Configurable SMTP Provider**
- Abstract the IONOS-specific email config into a provider pattern
- Out-of-the-box support for: IONOS, Gmail (OAuth), SendGrid, Mailgun, AWS SES
- Unlocks buyers who don't use IONOS; eliminates "IONOS only?" objection

**Campaign Status Dashboard (CLI)**
- `python mine.py --status` produces a rich terminal summary:
  - Active campaigns, sent count, reply count, hot leads, pending follow-ups
  - Replaces manual inspection of output/ JSON files

**Deduplication Across Runs**
- Global contact registry prevents same email being contacted in two different runs
- Persistent across sessions (SQLite or JSON index)
- Addresses: "what if we've already contacted them?"

**Improved Error Handling + Logging**
- Structured error log with run-level context
- Distinguishes scraping failures (network) from scoring failures (data) from send failures (SMTP)
- Enables operators to restart interrupted runs cleanly

**Estimated effort:** 3–4 weeks engineering

---

## Q3 2026 — v4 (Intelligence Layer)

**Theme: AI-powered layers that 3× reply rate and reduce manual triage**

### Deliverables

**CRM Bridge — Pipedrive + HubSpot (High Priority)**
- Push leads, deals, and reply events to Pipedrive or HubSpot on stage change
- Webhooks on: `lead_qualified`, `email_sent`, `reply_classified`, `meeting_booked`
- Pull existing deals to prevent re-contacting existing customers/prospects
- Architecture: `cae/extensions/crm_bridge/` (stubs in place, implementation this quarter)
- **Why this unlocks 5k/month:** Without CRM sync, enterprise buyers have to manually transfer data — this is a hard blocker for Tier 3 buyers

**AI-Enhanced Personalization (Anthropic Claude)**
- Use Claude to generate truly personalized first-touch emails using scraped company context
- Input: company name, website content snippets, contact name, industry signals, buy signals
- Output: unique opening paragraph per lead (not just template fill-in)
- Quality gate: AI draft + human approval before send (same workflow, better content)
- Expected lift: +30–50% reply rate over pure template approach

**Multi-Sender Support**
- Configure 2–5 sending mailboxes per campaign
- Auto-rotate senders to protect domain reputation
- Per-sender send limits and cooldown configuration
- **Why this matters:** Single sender at 50+ emails/day risks domain blacklisting; multi-sender is table stakes for agency scale

**Reply Auto-Routing**
- `interested` / `positive` replies auto-routed to Slack/email notification (webhook)
- `send_materials` auto-triggers pre-approved case study attachment
- `wrong_person` triggers auto-forward request with decision-maker question
- Reduces manual triage from 2 hrs/day to 15 min/day for active campaigns

**A/B Subject Line Testing**
- Run 2–3 subject variants per batch, track open proxy (reply rate per variant)
- Report: winning variant per industry/city combination after 20+ sends
- Feeds back into messaging assist bundle for future runs

**Estimated effort:** 6–8 weeks engineering

---

## Q4 2026 — v4.5 (Scale + Agency Features)

**Theme: Multi-client operations, white-label deliverables, analytics**

### Deliverables

**Multi-Client Campaign Management**
- Named client workspaces: `campaigns/client_a/`, `campaigns/client_b/`
- Per-client SMTP credentials, industry profiles, and output directories
- Client-level reporting aggregation: total sends, replies, hot leads across all runs
- Enables agency use case: one CAE instance, N client campaigns running simultaneously

**White-Label Report Generation**
- Client branding in campaign brief: logo, color scheme, agency name
- PDF export for client-facing deliverables (currently markdown only)
- Custom email signature per campaign (no "Rebell Systems" in client deliverables)

**Analytics Dashboard (Web UI — Minimal)**
- Single-page web dashboard served locally: `python mine.py --dashboard`
- Shows: active campaigns, pipeline funnel (sent → replied → hot → meeting), per-vertical performance
- No external dependency: embedded Jinja2 + Chart.js, serves from localhost
- **Why:** CLI-only tools create a perception gap at 5k/month; buyers expect some kind of visual layer

**LinkedIn Outreach Sequences (Extension)**
- LinkedIn connection request + message sequence generation (separate from email)
- Decision-maker profile matching: finds GF/CEO LinkedIn URL from company search
- Multi-touch: LinkedIn connect + email + follow-up LinkedIn message
- Architecture: `cae/extensions/linkedin/` (stubs in place)

**Advanced Batch Scheduling**
- Cron-based recurring campaign runs: `python mine.py --schedule "0 8 * * 1"` (Monday 8am)
- Prevents re-contacting already-sent leads across scheduled runs
- Status digest email: weekly summary of all scheduled campaigns

**Estimated effort:** 8–10 weeks engineering

---

## 2027 — v5 (Enterprise + White-Label)

**Theme: Resellable product, enterprise compliance, team workflows**

### Vision Items (Not Committed)

| Feature | Description | Why It Matters |
|---|---|---|
| REST API | JSON API for all CAE actions | 3rd-party integrations, Zapier, custom UIs |
| Team collaboration | Multi-user approvals, audit trails | Enterprise sales team deployment |
| SSO + role-based access | Admin / operator / viewer roles | Compliance requirement for enterprise |
| GDPR compliance dashboard | Opt-out tracking, consent records, deletion workflow | Required for EU enterprise buyers |
| Phone enrichment | Phone validation + reverse lookup | Multi-touch campaigns via phone |
| Custom LLM fine-tuning | Fine-tune on client's best-performing emails | Continuous personalization improvement |
| International language support | English, French, Dutch, Polish message generation | EU market expansion beyond DACH |
| Data residency options | EU-hosted deployment, no US data transfer | Enterprise/government compliance |
| SLA + dedicated support | 99.9% uptime SLA, dedicated Slack channel | Justifies €15k+/month enterprise tier |

---

## What Unlocks Each Pricing Tier

| License Tier | Monthly Price | Gate Feature Required |
|---|---|---|
| Starter | €2,500 | v3 core (current) |
| Pro | €5,000 | v3 + SMTP flexibility + campaign status + dedup |
| Agency | €8,000 | v4 + CRM bridge + multi-sender + white-label reports |
| Enterprise | €15,000+ | v5 + REST API + SSO + GDPR dashboard + SLA |

The current v3 codebase supports **Starter** confidently and **Pro** with the Q2 2026 additions. **Agency** requires CRM bridge (the single highest-priority roadmap item).

---

## Immediate Next Steps (Prioritized)

1. **SMTP provider abstraction** — unblocks non-IONOS buyers, 1 week effort
2. **Setup wizard** — reduces onboarding friction from 2 hrs to 15 min, 3 days effort
3. **CRM bridge MVP** (Pipedrive) — single highest-value feature for upsell path, 3 weeks effort
4. **Campaign status CLI dashboard** — reduces daily ops friction, 3 days effort
5. **Multi-sender rotation** — required for agency-scale campaigns, 2 weeks effort
