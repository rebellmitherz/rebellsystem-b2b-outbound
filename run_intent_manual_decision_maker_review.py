from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
LATEST = ROOT / "output" / "latest"
OUTPUT_ROOT = ROOT / "output"

ENRICHED_FILE = LATEST / "intent_enriched_leads.json"
DM_FILE = LATEST / "intent_decision_maker_enrichment.json"
DM_DEBUG_FILE = LATEST / "intent_decision_maker_enrichment_debug.json"
POOL_INPUT_FILE = LATEST / "intent_pool_enrichment_input.json"
POOL_TO_ENRICHMENT_REPORT_FILE = LATEST / "intent_pool_to_enrichment_report.json"
OUTPUT_JSON = LATEST / "intent_manual_decision_maker_reviews.json"
OUTPUT_MD = LATEST / "intent_manual_decision_maker_reviews.md"

SENT_LOG_JSON = OUTPUT_ROOT / "sent_log.json"
PIPELINE_JSON = OUTPUT_ROOT / "outreach_pipeline.json"

# ── Fake-Personen / Nicht-Namen ──────────────────────────────────────────────
FAKE_PERSON_TOKENS = frozenset({
    "lernen sie", "ihre ansprechpartner", "ansprechpartner",
    "kontakt", "team", "datenschutz", "impressum", "jobs", "karriere",
    "finden sie", "telefon", "email", "mail", "service", "support",
    "info", "presse", "newsletter", "login", "registrieren",
    "agentur", "full-service",
})

FAKE_PERSON_PHRASES = frozenset({
    "ihre ansprechpartner", "lernen sie", "lernen sie uns kennen",
    "finden sie", "finden sie uns", "ihr ansprechpartner",
    "ansprechpartner für", "ansprechpartner fur",
    "kontakt aufnehmen", "kontaktformular",
    "zum impressum", "zur datenschutzerklärung", "zur datenschutzerklaerung",
    "unser team", "das team", "ihr team",
})

# ── Portal-Domains (dürfen Signalquelle sein, aber NICHT als Firma) ──────────
PORTAL_DOMAIN_PARTS = (
    "stepstone", "kimeta", "join.com", "workwise", "stellenanzeigen", "indeed",
    "xing", "omr", "linkedin", "glassdoor", "monster", "joblift", "meinestadt",
    "experteer", "truific", "absolventa", "stellenmarkt", "jobportal",
    "stellenonline", "jobware", "stellenangebote",
)

# ── Weltbekannte / extern wirkende Namen ─────────────────────────────────────
SUSPICIOUS_EXTERNAL_NAMES = frozenset({
    "bob iger", "tim cook", "sundar pichai", "satya nadella", "elon musk",
    "jeff bezos", "mark zuckerberg", "larry page", "sergey brin",
    "bill gates", "steve ballmer", "jack dorsey", "reed hastings",
    "brian chesky", "daniel ek", "evan spiegel", "kevin systrom",
    "susan wojcicki", "marissa mayer", "travis kalanick", "dara khosrowshahi",
    "andy jassy", "jensen huang", "lisa su", "patrick collison",
    "john collison", "drew houston", "aaron levie", "bret taylor",
})

# ── Minimale Plausibilität ───────────────────────────────────────────────────
NAME_RE = r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+){1,3}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _domain(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or parsed.netloc or parsed.path or "").split(":")[0].removeprefix("www.").strip("/")


def _company_key(company: str, website: str = "") -> str:
    domain = _domain(website)
    if domain:
        return f"domain:{domain}"
    return "company:" + re.sub(r"\s+", " ", (company or "").strip()).casefold()


def _valid_url(value: str) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.hostname or parsed.netloc or parsed.path
    return "." in (host or "") and " " not in (host or "")


def _load_manual_payload(output_json: Path = OUTPUT_JSON) -> dict:
    payload = _read_json(output_json)
    if not payload:
        payload = {
            "generated_at": _now(),
            "updated_at": "",
            "status": "ok",
            "reviews": [],
            "safety": {
                "no_send": True,
                "no_smtp": True,
                "no_pipeline_integration": True,
                "manual_only": True,
            },
        }
    payload.setdefault("reviews", [])
    payload.setdefault("safety", {
        "no_send": True,
        "no_smtp": True,
        "no_pipeline_integration": True,
        "manual_only": True,
    })
    return payload


def load_manual_reviews(output_json: Path = OUTPUT_JSON) -> list[dict]:
    return list(_load_manual_payload(output_json).get("reviews") or [])


def _manual_index(output_json: Path = OUTPUT_JSON) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in load_manual_reviews(output_json):
        key = _company_key(str(item.get("company_name") or ""), str(item.get("website") or ""))
        if key:
            out[key] = item
    return out


def _debug_index() -> dict[str, dict]:
    out: dict[str, dict] = {}
    payload = _read_json(DM_DEBUG_FILE)
    for item in payload.get("companies") or []:
        key = _company_key(str(item.get("company_name") or ""), str(item.get("website") or ""))
        if key:
            out[key] = item
    return out


def _dm_index() -> dict[str, dict]:
    out: dict[str, dict] = {}
    payload = _read_json(DM_FILE)
    for item in payload.get("companies") or []:
        key = _company_key(str(item.get("company_name") or ""), str(item.get("website") or ""))
        if key:
            out[key] = item
    return out


def _lead_rows() -> list[dict]:
    rows: list[dict] = []
    sources = [
        ("intent_enriched_leads", ENRICHED_FILE, "enriched_leads"),
        ("intent_pool_enrichment_input", POOL_INPUT_FILE, "leads"),
    ]
    for source, path, key in sources:
        payload = _read_json(path)
        for item in payload.get(key) or []:
            if isinstance(item, dict):
                row = dict(item)
                row["_input_source"] = source
                rows.append(row)
    for item in (_read_json(POOL_TO_ENRICHMENT_REPORT_FILE).get("processed_candidates") or []):
        if isinstance(item, dict):
            rows.append({
                "_input_source": "intent_pool_to_enrichment_report",
                "company_name": item.get("company_name", ""),
                "website": item.get("website", ""),
                "industry": item.get("industry", ""),
                "city_region": item.get("city", ""),
                "intent_signal_title": item.get("signal_title", ""),
                "intent_signal_source_url": item.get("signal_url", ""),
                "lead_quality_status": "needs_decision_maker_review",
                "next_action": "manual_decision_maker_review",
            })
    return rows


# ── Filter helpers ───────────────────────────────────────────────────────────

def _is_fake_person_name(name: str) -> bool:
    """True wenn der Name offensichtlich kein Personenname ist."""
    if not name or not (name.strip()):
        return True
    cleaned = re.sub(r"\s+", " ", name.strip())
    low = cleaned.casefold()
    if low in FAKE_PERSON_TOKENS:
        return True
    for phrase in FAKE_PERSON_PHRASES:
        if phrase in low:
            return True
    # Einzelne Tokens, keine Vor-/Nachnamen-Struktur
    parts = cleaned.split()
    if len(parts) < 2:
        return True
    # Alle Teile grossgeschrieben = kein normaler Name
    if all(p == p.upper() and len(p) > 1 for p in parts if len(p) > 1):
        return True
    # Beginnt ein Teil nicht mit Grossbuchstabe? (z.B. "von der Media GmbH")
    for p in parts:
        if p and not p[0].isupper():
            return True
    # Internet-typische Muster
    if any(sep in cleaned for sep in ("@", "http", "www.", "//")):
        return True
    if len(parts) > 4:
        return True
    return False


def _is_portal_as_company(company: str, website: str) -> tuple[bool, str]:
    """True wenn die Firma eigentlich ein Jobportal ist."""
    host = _domain(website)
    if host and any(part in host for part in PORTAL_DOMAIN_PARTS):
        return True, f"portal_domain:{host}"
    company_low = (company or "").casefold()
    portal_names = ("experteer", "stepstone", "indeed", "xing", "kimeta",
                    "monster", "glassdoor", "join.com", "joblift", "workwise",
                    "absolventa", "stellenmarkt", "jobware", "jobleads",
                    "truific")
    for pn in portal_names:
        if pn in company_low:
            return True, f"portal_company_name:{pn}"
    return False, ""


def _is_suspicious_external_person(name: str, company: str) -> tuple[bool, str]:
    """True wenn die Person welbekannt/fremd wirkt für diese Firma."""
    if not name or not company:
        return False, ""
    name_low = re.sub(r"\s+", " ", name.strip()).casefold()
    if name_low in SUSPICIOUS_EXTERNAL_NAMES:
        return True, f"suspicious_external_person:{name_low}"
    # Plausibilität: Wenn Name in bekannt-Fremder Datenbank, aber Firma passt nicht
    company_tokens = set(re.findall(r"[a-zäöüß]{3,}", company.casefold()))
    name_tokens = set(re.findall(r"[a-zäöüß]{3,}", name_low))
    # Wenn keinerlei Token-Überlappung und Name sehr spezifisch → suspicious
    return False, ""


def _load_sent_index() -> dict[str, set[str]]:
    """Lädt Sent-Log und baut Index {email_lower, domain}. """
    sent_emails: set[str] = set()
    sent_domains: set[str] = set()
    sent_log = _read_json(SENT_LOG_JSON)
    pipeline = _read_json(PIPELINE_JSON)

    for ev in sent_log.get("events") or []:
        if not isinstance(ev, dict):
            continue
        email = str(ev.get("to") or ev.get("email") or "").strip().lower()
        if email:
            sent_emails.add(email)
            if "@" in email:
                sent_domains.add(email.rsplit("@", 1)[-1])
        company_domain = _domain(str(ev.get("website") or ev.get("company_domain") or ""))
        if company_domain:
            sent_domains.add(company_domain)

    for entry in pipeline.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        email = str(entry.get("email") or "").strip().lower()
        if email:
            sent_emails.add(email)
            if "@" in email:
                sent_domains.add(email.rsplit("@", 1)[-1])
        website = str(entry.get("website") or "")
        if website:
            sent_domains.add(_domain(website))
        stage = str(entry.get("outreach_stage") or "").strip().lower()
        if stage in ("sent", "won", "lost", "followup_1", "followup_2"):
            company_name = str(entry.get("company_name") or "").strip()
            if company_name:
                sent_emails.add(f"__company:{company_name.casefold()}")

    return {"emails": sent_emails, "domains": sent_domains}


def _is_already_contacted_or_sent(company: str, website: str, email: str, sent_index: dict) -> tuple[bool, str]:
    """Prüft ob Firma bereits kontaktiert/gesendet/in Pipeline ist."""
    if email:
        email_low = email.strip().lower()
        if email_low in sent_index["emails"]:
            return True, "email_in_sent_log_or_pipeline"
        if "@" in email_low:
            domain = email_low.rsplit("@", 1)[-1]
            if domain in sent_index["domains"]:
                return True, "email_domain_in_sent_log"

    if website:
        domain = _domain(website)
        if domain and domain in sent_index["domains"]:
            return True, "website_domain_in_sent_log_or_pipeline"

    if company:
        company_key = f"__company:{company.casefold()}"
        if company_key in sent_index["emails"]:
            return True, "company_in_pipeline"

    return False, ""


def _is_valid_candidate_name(name: str) -> bool:
    """Checkt ob ein Kandidatenname als Person plausibel ist."""
    if not name or not (name.strip()):
        return False
    cleaned = re.sub(r"\s+", " ", name.strip())
    return not _is_fake_person_name(cleaned)


def _normalize_company(company: str) -> str:
    """Entfernt GmbH/AG/etc.-Suffix für Vergleich."""
    company = re.sub(r"\s+", " ", (company or "").strip())
    return re.sub(r"\b(GmbH|UG|AG|KG|OHG|GbR|SE|Ltd|Inc|LLC)\b", "", company, flags=re.I).strip()


# ── Core ─────────────────────────────────────────────────────────────────────

def load_review_items(output_json: Path = OUTPUT_JSON) -> dict:
    debug_by_key = _debug_index()
    dm_by_key = _dm_index()
    manual_by_key = _manual_index(output_json)
    sent_index = _load_sent_index()

    rows = []
    seen: set[str] = set()
    filtered_out: list[dict] = []
    rejected_candidates_log: list[dict] = []

    for lead in _lead_rows():
        company = str(lead.get("company_name") or "").strip()
        website = str(lead.get("website") or "").strip()
        email = str(lead.get("email") or "").strip()

        # ── Filter 1: company + website Pflicht ──
        if not company or not website:
            filtered_out.append({"company_name": company or "-", "reason": "missing_company_or_website", "source": "lead_rows"})
            continue

        key = _company_key(company, website)
        if key in seen:
            continue

        status = str(lead.get("lead_quality_status") or lead.get("status") or "").strip()
        next_action = str(lead.get("next_action") or "").strip()
        dm_name = str(lead.get("decision_maker_name") or "").strip()
        should_show = (
            status == "needs_decision_maker_review"
            or next_action == "manual_decision_maker_review"
            or (not dm_name and company and website)
        )
        if not should_show:
            filtered_out.append({"company_name": company, "website": website, "reason": f"status_not_review:{status}", "source": "lead_rows"})
            continue

        # ── Filter 2: portal as company blocken ──
        is_portal, portal_reason = _is_portal_as_company(company, website)
        if is_portal:
            filtered_out.append({
                "company_name": company,
                "website": website,
                "reason": portal_reason,
                "source": "portal_block",
                "status": "blocked_portal_as_company",
            })
            continue

        # ── Filter 3: already contacted / sent / pipeline check ──
        is_contacted, contact_reason = _is_already_contacted_or_sent(company, website, email, sent_index)
        if is_contacted:
            filtered_out.append({
                "company_name": company,
                "website": website,
                "reason": contact_reason,
                "source": "already_contacted_block",
                "status": "blocked_already_contacted",
            })
            continue

        # ── Filter 4: Duplicate aus already-contacted-Log verstecken ──
        if any("duplicate" in str(flag).lower() for flag in (lead.get("risk_flags") or [])):
            filtered_out.append({
                "company_name": company,
                "website": website,
                "reason": "duplicate_risk_flag",
                "source": "duplicate_block",
                "status": "blocked_duplicate",
            })
            continue

        seen.add(key)

        debug = debug_by_key.get(key, {})
        dm = dm_by_key.get(key, {})
        manual = manual_by_key.get(key)

        # ── Kandidaten prüfen ──
        existing_candidates = dm.get("decision_maker_candidates") or debug.get("candidates_found") or []
        valid_candidates: list[dict] = []
        rejected_candidates: list[dict] = []

        for cand in existing_candidates:
            cand_name = str(cand.get("name") or "").strip()
            if not cand_name:
                continue

            # Fake-Personen-Name Check
            if _is_fake_person_name(cand_name):
                rejected_candidates.append({
                    "rejected_name": cand_name,
                    "reason": "fake_person_name",
                    "original_candidate": dict(cand),
                })
                continue

            # Suspicious external person check
            is_susp, susp_reason = _is_suspicious_external_person(cand_name, company)
            if is_susp:
                rejected_candidates.append({
                    "rejected_name": cand_name,
                    "reason": susp_reason,
                    "original_candidate": dict(cand),
                })
                continue

            valid_candidates.append(cand)

        if rejected_candidates:
            for rc in rejected_candidates:
                rejected_candidates_log.append({
                    "company_name": company,
                    "website": website,
                    **rc,
                })

        # ── Queries / Pages Debug ──
        queries_tried = debug.get("queries_tried") or dm.get("queries_tried") or []
        pages_checked = debug.get("pages_checked") or dm.get("pages_checked") or []

        rows.append({
            "review_key": key,
            "company_name": company,
            "website": website,
            "industry": str(lead.get("industry") or "").strip(),
            "city_region": str(lead.get("city_region") or lead.get("city") or "").strip(),
            "signal_title": str(lead.get("intent_signal_title") or lead.get("signal_title") or "").strip(),
            "signal_url": str(lead.get("intent_signal_source_url") or lead.get("signal_url") or "").strip(),
            "lead_quality_status": status,
            "next_action": next_action,
            "debug_reason": str(debug.get("debug_reason") or dm.get("debug_reason") or "").strip(),
            "final_status": str(debug.get("final_status") or dm.get("enrichment_status") or status).strip(),
            "queries_tried": queries_tried,
            "pages_checked": pages_checked,
            "queries_tried_count": len(queries_tried),
            "pages_checked_count": len(pages_checked),
            "existing_candidates": valid_candidates,
            "rejected_candidates": rejected_candidates,
            "manual_review": manual,
            "input_source": str(lead.get("_input_source") or ""),
            "enterprise_blocked": bool(lead.get("enterprise_blocked") or False),
        })

    return {
        "available": True,
        "generated_at": _now(),
        "items": rows,
        "count": len(rows),
        "completed_count": sum(1 for row in rows if row.get("manual_review")),
        "filtered_out_count": len(filtered_out),
        "filtered_out": filtered_out,
        "rejected_candidates_log": rejected_candidates_log,
        "output_json": str(output_json),
        "output_md": str(OUTPUT_MD),
        "safety": {
            "no_send": True,
            "no_smtp": True,
            "no_pipeline_integration": True,
        },
    }


def validate_review(data: dict) -> tuple[bool, str]:
    required = ("company_name", "website", "decision_maker_name", "decision_maker_role", "decision_maker_source_url")
    for key in required:
        if not str(data.get(key) or "").strip():
            return False, f"{key}_required"
    if not _valid_url(str(data.get("website") or "")):
        return False, "website_invalid"
    if not _valid_url(str(data.get("decision_maker_source_url") or "")):
        return False, "source_url_invalid"
    try:
        confidence = float(data.get("decision_maker_confidence"))
    except Exception:
        return False, "confidence_invalid"
    if confidence < 0 or confidence > 1:
        return False, "confidence_out_of_range"
    return True, ""


def save_manual_review(data: dict, output_json: Path = OUTPUT_JSON, output_md: Path = OUTPUT_MD) -> dict:
    ok, error = validate_review(data)
    if not ok:
        return {"ok": False, "error": error}
    confidence = round(float(data.get("decision_maker_confidence")), 3)
    item = {
        "company_name": str(data.get("company_name") or "").strip(),
        "website": str(data.get("website") or "").strip(),
        "decision_maker_name": str(data.get("decision_maker_name") or "").strip(),
        "decision_maker_role": str(data.get("decision_maker_role") or "").strip(),
        "decision_maker_source_url": str(data.get("decision_maker_source_url") or "").strip(),
        "decision_maker_confidence": confidence,
        "decision_maker_source_type": "manual_dashboard_review",
        "decision_maker_evidence": "manual_operator_entry",
        "note": str(data.get("note") or "").strip(),
        "reviewed_by": "dashboard",
        "reviewed_at": _now(),
        "status": "manually_completed",
        "next_action": "enrich_email_for_decision_maker",
    }
    payload = _load_manual_payload(output_json)
    key = _company_key(item["company_name"], item["website"])
    reviews = [
        row for row in payload.get("reviews") or []
        if _company_key(str(row.get("company_name") or ""), str(row.get("website") or "")) != key
    ]
    reviews.append(item)
    payload["reviews"] = reviews
    payload["updated_at"] = item["reviewed_at"]
    payload["review_count"] = len(reviews)
    _write_json(output_json, payload)
    _write_md(payload, output_md)
    return {"ok": True, "review": item, "review_count": len(reviews)}


def _write_md(payload: dict, output_md: Path = OUTPUT_MD) -> None:
    lines = [
        "# Intent Manual Decision Maker Reviews",
        "",
        f"- generated_at: {payload.get('generated_at', '')}",
        f"- updated_at: {payload.get('updated_at', '')}",
        f"- review_count: {len(payload.get('reviews') or [])}",
        "",
        "| Company | Website | Decision Maker | Role | Confidence | Source |",
        "|---|---|---|---|---:|---|",
    ]
    for item in payload.get("reviews") or []:
        lines.append(
            f"| {item.get('company_name','')} | {item.get('website','')} | {item.get('decision_maker_name','')} | "
            f"{item.get('decision_maker_role','')} | {item.get('decision_maker_confidence',0)} | {item.get('decision_maker_source_url','')} |"
        )
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Save a manual intent decision-maker review")
    parser.add_argument("--company", required=True)
    parser.add_argument("--website", default="")
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--confidence", type=float, default=0.9)
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    result = save_manual_review({
        "company_name": args.company,
        "website": args.website,
        "decision_maker_name": args.name,
        "decision_maker_role": args.role,
        "decision_maker_source_url": args.source_url,
        "decision_maker_confidence": args.confidence,
        "note": args.note,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
