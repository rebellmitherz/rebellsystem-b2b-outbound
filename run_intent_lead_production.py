from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
LATEST = ROOT / "output" / "latest"

INPUT_FILE = LATEST / "intent_outreach_preview.json"
OUTPUT_JSON = LATEST / "intent_lead_production.json"
OUTPUT_CSV = LATEST / "intent_lead_production.csv"
OUTPUT_MD = LATEST / "intent_lead_production.md"

ALLOWED_MODES = ("preview", "approval", "auto")
DEFAULT_LIMIT = 10
HARD_MAX_LIMIT = 10

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
GENERIC_PREFIXES = ("info@", "kontakt@", "office@", "hello@", "service@",
                    "support@", "mail@", "contact@", "marketing@", "sales@")

LEAD_FIELDS = [
    "company_name", "website", "industry", "city_region",
    "intent_signal_type", "intent_signal_source_url", "intent_signal_title",
    "signal_reason",
    "decision_maker_name", "decision_maker_role",
    "email", "email_type", "phone", "linkedin_url",
    "contact_quality", "outreach_angle",
    "recommended_first_line",
    "email_subject", "email_body",
    "followup_1", "followup_2",
    "next_action", "status",
    "missing_fields",
]


def _safe_read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _classify_email_type(email: str) -> str:
    if not email:
        return ""
    low = email.lower().strip()
    if low.startswith(GENERIC_PREFIXES):
        return "generic"
    return "personal"


def _signal_reason(angle: str, signal_title: str) -> str:
    if angle == "sales_growth_signal":
        return f"Aktive Stellenausschreibung im Vertrieb/Sales: {signal_title}"
    if angle == "marketing_growth_signal":
        return f"Wachstumssignal im Marketing-/Account-Bereich: {signal_title}"
    if angle == "manual_review_needed":
        return f"Signal benötigt manuelle Sichtung: {signal_title}"
    return f"Bedarfssignal: {signal_title}"


def _intent_signal_type(angle: str) -> str:
    if angle == "sales_growth_signal":
        return "sales_hiring"
    if angle == "marketing_growth_signal":
        return "growth_expansion"
    return "manual_signal"


def _build_followups(company: str, contact_name: str) -> tuple[str, str]:
    greeting = f"Hallo {contact_name}," if contact_name else f"Hallo {company}-Team,"
    fu1 = (
        f"{greeting}\n\n"
        f"kurze Erinnerung an meine letzte Mail. Falls B2B-Erstgespräche aktuell kein Thema sind, kein Stress.\n"
        f"Falls doch: ein 15-Minuten-Slot reicht, um zu prüfen ob es passt.\n\n"
        f"Wäre nächste Woche eine kurze Abstimmung machbar?"
    )
    fu2 = (
        f"{greeting}\n\n"
        f"letzte Nachricht von meiner Seite zu diesem Thema, damit Sie nicht zugespammt werden.\n"
        f"Wenn B2B-Erstgespräche aktuell nicht relevant sind, schließe ich das Thema sauber ab.\n\n"
        f"Sonst freue ich mich über eine kurze Rückmeldung."
    )
    return fu1, fu2


def _domain_to_region(website: str) -> str:
    if not website:
        return ""
    try:
        netloc = urlparse(website).netloc.lower()
        if netloc.endswith(".de"):
            return "DE"
        if netloc.endswith(".at"):
            return "AT"
        if netloc.endswith(".ch"):
            return "CH"
        if netloc.endswith(".com") or netloc.endswith(".io") or netloc.endswith(".co"):
            return "INTL"
    except Exception:
        pass
    return ""


def _normalize(row: dict) -> dict:
    company = str(row.get("company_name") or "").strip()
    website = str(row.get("website") or "").strip()
    email = str(row.get("email") or "").strip()
    phone = str(row.get("phone") or "").strip()
    contact_name = str(row.get("contact_name") or "").strip()
    contact_quality = str(row.get("contact_quality") or "").strip().lower()
    angle = str(row.get("outreach_angle") or "").strip()
    signal_title = str(row.get("source_signal_title") or "").strip()
    signal_url = str(row.get("source_signal_url") or "").strip()
    subject = str(row.get("email_subject") or "").strip()
    body = str(row.get("email_body") or "").strip()
    first_line = str(row.get("recommended_first_line") or "").strip()

    fu1, fu2 = _build_followups(company, contact_name)
    email_type = _classify_email_type(email)
    intent_signal_type = _intent_signal_type(angle)
    signal_reason_text = _signal_reason(angle, signal_title)

    decision_maker_role = ""
    if signal_title:
        # rough role hint from signal title (no logic change to bot)
        for marker in ("Sales Manager", "Account Manager", "Business Development",
                       "Vertrieb", "Marketing Manager", "Content Manager"):
            if marker.lower() in signal_title.lower():
                decision_maker_role = marker
                break

    lead = {
        "company_name": company,
        "website": website,
        "industry": "Marketingagentur",
        "city_region": _domain_to_region(website) or "DE",
        "intent_signal_type": intent_signal_type,
        "intent_signal_source_url": signal_url,
        "intent_signal_title": signal_title,
        "signal_reason": signal_reason_text,
        "decision_maker_name": contact_name,
        "decision_maker_role": decision_maker_role,
        "email": email,
        "email_type": email_type,
        "phone": phone,
        "linkedin_url": "",
        "contact_quality": contact_quality,
        "outreach_angle": angle,
        "recommended_first_line": first_line,
        "email_subject": subject,
        "email_body": body,
        "followup_1": fu1,
        "followup_2": fu2,
        "next_action": "",
        "status": "",
        "missing_fields": [],
    }
    return lead


def _evaluate(lead: dict) -> dict:
    missing: list[str] = []
    if not lead.get("email"):
        missing.append("email")
    elif not EMAIL_RE.match(lead["email"]):
        missing.append("email_invalid")
    if not lead.get("website"):
        missing.append("website")
    if not lead.get("intent_signal_source_url"):
        missing.append("intent_signal_source_url")
    if not lead.get("email_body"):
        missing.append("email_body")

    soft_missing: list[str] = []
    if not lead.get("decision_maker_name"):
        soft_missing.append("decision_maker_name")
    if not lead.get("phone"):
        soft_missing.append("phone")

    if not missing:
        if not soft_missing:
            lead["status"] = "ready_for_approval"
            lead["next_action"] = "approve_for_send"
        else:
            lead["status"] = "ready_for_approval"
            lead["next_action"] = "approve_for_send"
    elif missing == ["email"] or missing == ["website"] or missing == ["decision_maker_name"]:
        lead["status"] = "needs_enrichment"
        lead["next_action"] = "enrich_manually"
    else:
        # If too many critical fields missing (e.g. no email and no website and no body)
        if len(missing) >= 3:
            lead["status"] = "discard"
            lead["next_action"] = "discard"
        else:
            lead["status"] = "needs_enrichment"
            lead["next_action"] = "enrich_manually"

    lead["missing_fields"] = missing + soft_missing
    return lead


def _write_outputs(report: dict, leads: list[dict]) -> None:
    OUTPUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LEAD_FIELDS)
        writer.writeheader()
        for lead in leads:
            row = dict(lead)
            row["missing_fields"] = ", ".join(lead.get("missing_fields") or [])
            writer.writerow({k: row.get(k, "") for k in LEAD_FIELDS})

    md = [
        "# Intent Lead Production Report",
        "",
        f"Generated: {report.get('generated_at','-')}",
        f"Mode: {report.get('mode','-')}",
        f"Limit: {report.get('limit',0)}",
        "",
        "## Summary",
        "",
        f"- loaded_candidates: {report.get('loaded_candidates',0)}",
        f"- normalized_leads: {report.get('normalized_leads',0)}",
        f"- ready_for_approval: {report.get('ready_for_approval',0)}",
        f"- needs_enrichment: {report.get('needs_enrichment',0)}",
        f"- discard: {report.get('discard',0)}",
        "",
        "## Leads",
        "",
        "| Company | Email | Phone | Quality | Status |",
        "|---------|-------|-------|---------|--------|",
    ]
    for lead in leads:
        md.append(
            f"| {lead.get('company_name','-')} | {lead.get('email','-') or '-'} | "
            f"{lead.get('phone','-') or '-'} | {lead.get('contact_quality','-')} | {lead.get('status','-')} |"
        )
    OUTPUT_MD.write_text("\n".join(md), encoding="utf-8")


def run(mode: str = "preview", limit: int = DEFAULT_LIMIT) -> dict:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"Unknown mode: {mode!r}. Allowed: {list(ALLOWED_MODES)}")
    limit = max(1, min(int(limit), HARD_MAX_LIMIT))

    LATEST.mkdir(parents=True, exist_ok=True)
    payload = _safe_read_json(INPUT_FILE)
    raw_rows = list(payload.get("results") or [])[:limit]
    loaded = len(raw_rows)

    leads = [_evaluate(_normalize(r)) for r in raw_rows]

    ready = sum(1 for l in leads if l["status"] == "ready_for_approval")
    needs = sum(1 for l in leads if l["status"] == "needs_enrichment")
    discarded = sum(1 for l in leads if l["status"] == "discard")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "limit": limit,
        "input_file": str(INPUT_FILE),
        "loaded_candidates": loaded,
        "normalized_leads": len(leads),
        "ready_for_approval": ready,
        "needs_enrichment": needs,
        "discard": discarded,
        "leads": leads,
    }

    _write_outputs(report, leads)

    print(f"mode: {mode}")
    print(f"limit: {limit}")
    print(f"loaded_candidates: {loaded}")
    print(f"normalized_leads: {len(leads)}")
    print(f"ready_for_approval: {ready}")
    print(f"needs_enrichment: {needs}")
    print(f"discard: {discarded}")
    for lead in leads:
        print(f"{lead.get('company_name','-')} | {lead.get('email','-') or '-'} | "
              f"{lead.get('phone','-') or '-'} | {lead.get('contact_quality','-')} | {lead.get('status','-')}")
    print("RUN_OK")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Intent Lead Production Runner")
    parser.add_argument("--mode", choices=ALLOWED_MODES, default="preview")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Max leads per run (hard cap {HARD_MAX_LIMIT})")
    args = parser.parse_args(argv)
    run(args.mode, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
