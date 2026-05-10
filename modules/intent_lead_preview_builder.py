#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LATEST = ROOT / "output" / "latest"

INPUTS = {
    "intent_candidates_quality": LATEST / "intent_candidates_quality.json",
    "intent_job_detail_relevance": LATEST / "intent_job_detail_relevance.json",
    "intent_relevance_gated_detail_fetch": LATEST / "intent_relevance_gated_detail_fetch.json",
    "intent_resolved_company_fit": LATEST / "intent_resolved_company_fit.json",
    "intent_company_website_resolution_preview": LATEST / "intent_company_website_resolution_preview.json",
    "intent_company_website_search": LATEST / "intent_company_website_search.json",
    "intent_website_verification": LATEST / "intent_website_verification.json",
    "intent_contact_page_fetch_preview": LATEST / "intent_contact_page_fetch_preview.json",
}
OUTPUT_FILE = LATEST / "intent_lead_preview.json"


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _count_candidates_quality(payload: dict) -> int:
    for key in ("total_candidates", "candidate_count"):
        if isinstance(payload, dict) and key in payload:
            try:
                return int(payload.get(key) or 0)
            except Exception:
                return 0
    items = (payload or {}).get("results") or (payload or {}).get("candidates") or []
    return len(items) if isinstance(items, list) else 0


def _count_job_detail_pages(payload: dict) -> int:
    results = (payload or {}).get("results") or []
    return len(results) if isinstance(results, list) else 0


def _count_relevance_fetch_candidates(payload: dict) -> int:
    counts = (payload or {}).get("counts") or {}
    if "fetch_detail" in counts:
        try:
            return int(counts.get("fetch_detail") or 0)
        except Exception:
            return 0
    results = (payload or {}).get("results") or []
    if isinstance(results, list):
        return len([r for r in results if r.get("recommended_next_action") == "fetch_detail"])
    return 0


def _count_resolved_companies(payload: dict) -> int:
    companies = (payload or {}).get("companies") or []
    if isinstance(companies, list) and companies:
        return len(companies)
    results = (payload or {}).get("results") or []
    return len(results) if isinstance(results, list) else 0


def _count_verified_websites(payload: dict) -> int:
    results = (payload or {}).get("results") or []
    return len([
        r for r in results
        if r.get("verification_status") in {"verified", "likely_verified"}
    ]) if isinstance(results, list) else 0


def _build_candidate_maps() -> tuple[dict, dict, dict, dict, dict]:
    fit_payload = _safe_read_json(INPUTS["intent_resolved_company_fit"])
    resolution_payload = _safe_read_json(INPUTS["intent_company_website_resolution_preview"])
    search_payload = _safe_read_json(INPUTS["intent_company_website_search"])
    verify_payload = _safe_read_json(INPUTS["intent_website_verification"])
    contact_payload = _safe_read_json(INPUTS["intent_contact_page_fetch_preview"])

    fit_map = {str(x.get("company_name") or "").strip(): x for x in ((fit_payload or {}).get("companies") or (fit_payload or {}).get("results") or []) if isinstance(x, dict)}
    resolution_map = {str(x.get("company_name") or "").strip(): x for x in ((resolution_payload or {}).get("companies") or []) if isinstance(x, dict)}
    search_map = {str(x.get("company_name") or "").strip(): x for x in ((search_payload or {}).get("results") or []) if isinstance(x, dict)}
    verify_map = {str(x.get("company_name") or "").strip(): x for x in ((verify_payload or {}).get("results") or []) if isinstance(x, dict)}
    contact_map = {str(x.get("company_name") or "").strip(): x for x in ((contact_payload or {}).get("results") or []) if isinstance(x, dict)}
    return fit_map, resolution_map, search_map, verify_map, contact_map


def _reason_for_candidate(company_name: str, fit_map: dict, resolution_map: dict, verify_map: dict, contact_map: dict) -> tuple[str, str]:
    fit = fit_map.get(company_name) or {}
    resolution = resolution_map.get(company_name) or {}
    verify = verify_map.get(company_name) or {}
    contact = contact_map.get(company_name) or {}

    fit_status = str(fit.get("fit_status") or resolution.get("fit_status") or "")
    if fit_status in {"not_fit", "irrelevant"}:
        return "not_fit", "discard"
    if resolution.get("company_name_valid") is False:
        return "generic_company_name", "review"
    verification_status = str(verify.get("verification_status") or "")
    if verification_status in {"failed", "needs_review"}:
        return "failed", "review"

    contact_status = str(contact.get("contact_enrichment_status") or "")
    best_email = str(contact.get("best_email") or "")
    best_phone = str(contact.get("best_phone") or "")

    if contact_status == "failed":
        return "failed_contact_fetch", "discard"
    if contact_status == "no_contact_data":
        return "no_contact_data", "discard"
    if fit_status == "maybe_fit":
        if not best_email and not best_phone:
            return "weak_fit", "review"
    if not best_email and not best_phone:
        return "no_contact_data", "discard"
    if not best_email:
        return "no_email", "review"
    if not best_phone:
        return "no_phone", "review"
    return "", "build_intent_lead_preview"


def run() -> dict:
    candidates_quality = _safe_read_json(INPUTS["intent_candidates_quality"])
    job_detail_relevance = _safe_read_json(INPUTS["intent_job_detail_relevance"])
    relevance_gated = _safe_read_json(INPUTS["intent_relevance_gated_detail_fetch"])
    resolved_companies = _safe_read_json(INPUTS["intent_resolved_company_fit"])
    verified_websites = _safe_read_json(INPUTS["intent_website_verification"])
    contact_preview = _safe_read_json(INPUTS["intent_contact_page_fetch_preview"])

    fit_map, resolution_map, search_map, verify_map, contact_map = _build_candidate_maps()

    lead_candidates = []
    discarded_candidates = []
    review_candidates = []

    company_names = []
    for source_map in (fit_map, resolution_map, search_map, verify_map, contact_map):
        for name in source_map.keys():
            if name and name not in company_names:
                company_names.append(name)

    for company_name in company_names:
        verify = verify_map.get(company_name) or {}
        contact = contact_map.get(company_name) or {}
        verified_url = str(verify.get("final_url") or verify.get("candidate_url") or "")
        verified_domain = str(verify.get("candidate_domain") or "")
        best_email = str(contact.get("best_email") or "")
        best_phone = str(contact.get("best_phone") or "")
        contact_status = str(contact.get("contact_enrichment_status") or "")

        base = {
            "company_name": company_name,
            "verified_domain": verified_domain,
            "verified_url": verified_url,
            "best_email": best_email,
            "best_phone": best_phone,
            "fit_status": str((fit_map.get(company_name) or {}).get("fit_status") or (resolution_map.get(company_name) or {}).get("fit_status") or ""),
            "verification_status": str(verify.get("verification_status") or ""),
            "contact_enrichment_status": contact_status,
        }

        is_lead = bool(
            company_name
            and verified_url
            and verified_domain
            and (best_email or best_phone)
            and contact_status in {"contact_data_found", "partial_contact_data"}
        )

        if is_lead:
            candidate = dict(base)
            candidate["next_action"] = "build_intent_lead_preview"
            lead_candidates.append(candidate)
            continue

        reason, next_action = _reason_for_candidate(company_name, fit_map, resolution_map, verify_map, contact_map)
        candidate = dict(base)
        candidate["reason"] = reason
        candidate["next_action"] = next_action
        if next_action == "discard":
            discarded_candidates.append(candidate)
        else:
            review_candidates.append(candidate)

    total_contact_enriched = len([
        r for r in ((contact_preview or {}).get("results") or [])
        if r.get("contact_enrichment_status") in {"contact_data_found", "partial_contact_data"}
    ])

    total_lead_candidates = len(lead_candidates)
    discarded_count = len(discarded_candidates)
    review_count = len(review_candidates)

    if total_lead_candidates == 0:
        pipeline_status = "no_lead_created"
        summary = "Intent-Kette technisch erfolgreich, aber kein verwertbarer Lead aus dem aktuellen Kandidaten entstanden."
    else:
        pipeline_status = "lead_candidates_ready"
        summary = f"Intent-Kette hat {total_lead_candidates} verwertbare Lead-Kandidaten erzeugt."

    final = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_status": pipeline_status,
        "total_signal_candidates": _count_candidates_quality(candidates_quality),
        "total_job_detail_pages": _count_job_detail_pages(job_detail_relevance),
        "total_relevance_fetch_candidates": _count_relevance_fetch_candidates(relevance_gated),
        "total_resolved_companies": _count_resolved_companies(resolved_companies),
        "total_verified_websites": _count_verified_websites(verified_websites),
        "total_contact_enriched": total_contact_enriched,
        "total_lead_candidates": total_lead_candidates,
        "discarded_count": discarded_count,
        "review_count": review_count,
        "lead_candidates": lead_candidates,
        "discarded_candidates": discarded_candidates,
        "review_candidates": review_candidates,
        "summary": summary,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Phase 3.14: Intent Lead Preview Builder ===")
    print(f"  pipeline_status:       {pipeline_status}")
    print(f"  total_lead_candidates: {total_lead_candidates}")
    print(f"  discarded_count:       {discarded_count}")
    print(f"  review_count:          {review_count}")
    for item in lead_candidates:
        print(f"  {item['company_name']} | lead_candidate | ok | {item['next_action']}")
    for item in discarded_candidates:
        print(f"  {item['company_name']} | discard | {item['reason']} | {item['next_action']}")
    for item in review_candidates:
        print(f"  {item['company_name']} | review | {item['reason']} | {item['next_action']}")
    print()

    return final


if __name__ == "__main__":
    run()
