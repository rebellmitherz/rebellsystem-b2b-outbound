from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from modules.intent_search_provider import search_intent_queries
from modules.intent_company_website_search import _score_candidate
from modules.intent_website_verifier import _verify_company
from modules.intent_contact_preview import _build_candidates, _CONTACT_PATHS, _IMPRESSUM_PATHS
from modules.intent_contact_page_fetcher import _extract_from_url

ROOT = Path(__file__).resolve().parent
LATEST = ROOT / "output" / "latest"
INPUT_FILE = LATEST / "intent_target_preview_report.json"
OUTPUT_JSON = LATEST / "intent_outreach_preview.json"
OUTPUT_CSV = LATEST / "intent_outreach_preview.csv"
OUTPUT_MD = LATEST / "intent_outreach_preview.md"

MAX_COMPANIES = 3
MAX_QUERIES_PER_COMPANY = 2
MAX_RESULTS_PER_QUERY = 3
MAX_CONTACT_URLS = 2
MAX_IMPRESSUM_URLS = 2
PROVIDER = "serper"


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _company_queries(company: str) -> list[str]:
    return [
        f'"{company}" offizielle Website Kontakt Impressum',
        f'"{company}" Marketingagentur Muenchen Kontakt',
    ][:MAX_QUERIES_PER_COMPANY]


def _pick_best_domain(company_name: str, batches: list[dict]) -> dict:
    best = None
    for batch in batches:
        for hit in batch.get("results") or []:
            scored = _score_candidate(
                company_name,
                str(hit.get("title") or ""),
                str(hit.get("url") or ""),
                str(hit.get("snippet") or ""),
            )
            candidate = {
                "title": str(hit.get("title") or ""),
                "url": str(hit.get("url") or ""),
                "snippet": str(hit.get("snippet") or ""),
                **scored,
            }
            if candidate["is_official_candidate"]:
                if best is None or candidate["domain_confidence"] > best["domain_confidence"]:
                    best = candidate
    return best or {}


def _best_email_type(email: str) -> str:
    if not email:
        return ""
    low = email.lower()
    if low.startswith(("info@", "kontakt@", "office@", "hello@", "service@", "support@")):
        return "generic_email"
    return "personal_email"


def _contact_quality(contact_name: str, email: str, phone: str) -> str:
    if email and _best_email_type(email) == "personal_email" and phone and contact_name:
        return "strong"
    if email and phone:
        return "good"
    if email or phone or contact_name:
        return "partial"
    return "weak"


def _recommended_first_line(company: str, title: str) -> str:
    return f"mir ist aufgefallen, dass {company} aktuell im Bereich Vertrieb/Marketing wächst bzw. eine passende Rolle ausgeschrieben hat ({title})."


def _outreach_angle(fit_status: str) -> str:
    if fit_status == "target_fit":
        return "sales_growth_signal"
    if fit_status == "maybe_fit":
        return "marketing_growth_signal"
    return "manual_review_needed"


def _subject(company: str) -> str:
    return f"Kurze Frage zu planbaren B2B-Erstgesprächen für {company}"


def _body(contact_name: str, company: str, title: str) -> str:
    greeting_name = contact_name or ""
    greeting = f"Hallo {greeting_name}," if greeting_name else f"Hallo {company}-Team,"
    return (
        f"{greeting}\n\n"
        f"mir ist aufgefallen, dass Sie aktuell im Bereich Vertrieb/Marketing wachsen bzw. eine passende Rolle ausgeschrieben haben ({title}).\n\n"
        f"Ich unterstütze B2B-Unternehmen dabei, planbar qualifizierte Erstgespräche mit passenden Geschäftskunden aufzubauen, ohne dass intern sofort ein kompletter Akquise-Apparat entstehen muss.\n\n"
        f"Wäre es sinnvoll, kurz zu prüfen, ob das für {company} aktuell relevant ist?"
    )


def _write_csv(rows: list[dict]) -> None:
    fields = [
        "company_name", "website", "source_signal_title", "source_signal_url", "fit_status", "fit_score",
        "contact_name", "email", "email_type", "phone", "contact_quality", "recommended_first_line",
        "outreach_angle", "email_subject", "email_body", "ready_for_approval", "missing_fields", "next_action",
    ]
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["missing_fields"] = ", ".join(row.get("missing_fields") or [])
            writer.writerow({k: out.get(k, "") for k in fields})


def _write_md(payload: dict) -> None:
    lines = [
        "# Intent Outreach Preview",
        "",
        f"Generated: {payload.get('generated_at','-')}",
        "",
        "## Summary",
        "",
        f"- companies_loaded: {payload.get('companies_loaded',0)}",
        f"- companies_processed: {payload.get('companies_processed',0)}",
        f"- websites_found: {payload.get('websites_found',0)}",
        f"- emails_found: {payload.get('emails_found',0)}",
        f"- phones_found: {payload.get('phones_found',0)}",
        f"- ready_for_approval: {payload.get('ready_for_approval',0)}",
        f"- needs_manual_enrichment: {payload.get('needs_manual_enrichment',0)}",
        "",
        "## Candidates",
        "",
        "| Company | Email | Phone | Quality | Next Action |",
        "|---------|-------|-------|---------|-------------|",
    ]
    for r in payload.get("results") or []:
        lines.append(f"| {r.get('company_name','-')} | {r.get('email','-')} | {r.get('phone','-')} | {r.get('contact_quality','-')} | {r.get('next_action','-')} |")
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def run() -> dict:
    payload = _safe_read_json(INPUT_FILE)
    candidates = [
        c for c in (payload.get("results") or [])
        if c.get("next_action") == "candidate_for_manual_followup"
    ][:MAX_COMPANIES]

    results = []
    websites_found = 0
    emails_found = 0
    phones_found = 0
    ready_for_approval = 0
    needs_manual_enrichment = 0

    for candidate in candidates:
        company = str(candidate.get("company_name") or "").strip()
        title = str(candidate.get("title") or "")
        source_url = str(candidate.get("url") or "")
        fit_status = str(candidate.get("fit_status") or "")
        fit_score = float(candidate.get("fit_score") or 0)

        row = {
            "company_name": company,
            "website": "",
            "source_signal_title": title,
            "source_signal_url": source_url,
            "fit_status": fit_status,
            "fit_score": fit_score,
            "contact_name": "",
            "email": "",
            "email_type": "",
            "phone": "",
            "contact_quality": "weak",
            "recommended_first_line": _recommended_first_line(company, title),
            "outreach_angle": _outreach_angle(fit_status),
            "email_subject": _subject(company),
            "email_body": "",
            "ready_for_approval": False,
            "missing_fields": [],
            "next_action": "discard",
        }

        try:
            queries = _company_queries(company)
            batches = search_intent_queries(queries, provider=PROVIDER, max_results_per_query=MAX_RESULTS_PER_QUERY)
            best = _pick_best_domain(company, batches)
            if best:
                verification = _verify_company(company, best.get("domain", ""), best.get("url", ""))
                if verification.get("verification_status") in {"verified", "likely_verified"}:
                    website = str(verification.get("final_url") or verification.get("candidate_url") or best.get("url") or "")
                    row["website"] = website
                    websites_found += 1

                    contact_urls = _build_candidates(website, _CONTACT_PATHS)[:MAX_CONTACT_URLS]
                    impressum_urls = _build_candidates(website, _IMPRESSUM_PATHS)[:MAX_IMPRESSUM_URLS]

                    contact_results = []
                    for url in contact_urls:
                        contact_results.append(_extract_from_url(url))
                    impressum_results = []
                    for url in impressum_urls:
                        impressum_results.append(_extract_from_url(url))

                    all_emails = []
                    all_phones = []
                    all_persons = []
                    for bucket in (contact_results, impressum_results):
                        for item in bucket:
                            all_emails.extend(item.get("emails_found") or [])
                            all_phones.extend(item.get("phones_found") or [])
                            all_persons.extend(item.get("contact_persons_found") or [])

                    personal = [e.get("email", "") for e in all_emails if e.get("is_personal")]
                    generic = [e.get("email", "") for e in all_emails if e.get("is_generic")]
                    best_email = personal[0] if personal else (generic[0] if generic else "")
                    best_phone = all_phones[0] if all_phones else ""
                    best_person = all_persons[0] if all_persons else ""

                    row["contact_name"] = best_person
                    row["email"] = best_email
                    row["email_type"] = _best_email_type(best_email)
                    row["phone"] = best_phone
                    row["contact_quality"] = _contact_quality(best_person, best_email, best_phone)
                    row["email_body"] = _body(best_person, company, title)

                    if best_email:
                        emails_found += 1
                    if best_phone:
                        phones_found += 1

                    missing = []
                    if not best_email:
                        missing.append("email")
                    if not best_phone:
                        missing.append("phone")
                    if not best_person:
                        missing.append("contact_name")
                    row["missing_fields"] = missing

                    if best_email:
                        row["ready_for_approval"] = True
                        row["next_action"] = "approve_for_send"
                        ready_for_approval += 1
                    elif best_phone or best_person:
                        row["next_action"] = "enrich_manually"
                        needs_manual_enrichment += 1
                    else:
                        row["next_action"] = "discard"
                else:
                    row["missing_fields"] = ["website_verification"]
                    row["next_action"] = "discard"
            else:
                row["missing_fields"] = ["website"]
                row["next_action"] = "discard"
        except Exception as exc:
            row["missing_fields"] = [f"error:{exc}"]
            row["next_action"] = "discard"
            row["email_body"] = _body("", company, title)

        if row["next_action"] == "enrich_manually":
            row["ready_for_approval"] = False
        results.append(row)

    final = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_file": str(INPUT_FILE),
        "companies_loaded": len(candidates),
        "companies_processed": len(results),
        "websites_found": websites_found,
        "emails_found": emails_found,
        "phones_found": phones_found,
        "ready_for_approval": ready_for_approval,
        "needs_manual_enrichment": needs_manual_enrichment,
        "results": results,
    }

    OUTPUT_JSON.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(results)
    _write_md(final)

    print(f"companies_loaded: {len(candidates)}")
    print(f"companies_processed: {len(results)}")
    print(f"websites_found: {websites_found}")
    print(f"emails_found: {emails_found}")
    print(f"phones_found: {phones_found}")
    print(f"ready_for_approval: {ready_for_approval}")
    print(f"needs_manual_enrichment: {needs_manual_enrichment}")
    for r in results:
        print(f"{r['company_name']} | {r['email'] or '-'} | {r['phone'] or '-'} | {r['contact_quality']} | {r['next_action']}")
    print("RUN_OK")
    return final


if __name__ == "__main__":
    run()
