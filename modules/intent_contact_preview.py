#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
LATEST = OUT / "latest"

INPUT_FILE = LATEST / "intent_website_verification.json"
OUTPUT_FILE = LATEST / "intent_contact_preview.json"

_CONTACT_PATHS = [
    "kontakt", "kontakt.html", "contact", "contact.html",
    "de/kontakt", "de/contact", "en/contact",
    "ueber-uns/kontakt", "about/contact", "contact-us",
]
_IMPRESSUM_PATHS = [
    "impressum", "de/impressum", "legal/impressum",
    "imprint", "de/imprint",
]


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_candidates(base_url: str, paths: list[str]) -> list[str]:
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    seen = set()
    result = []
    for path in paths:
        full = urljoin(base, "/" + path.lstrip("/"))
        if full not in seen:
            seen.add(full)
            result.append(full)
    return result


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def run() -> dict:
    payload = _safe_read_json(INPUT_FILE)
    entries = list((payload or {}).get("results") or [])
    eligible = [
        r for r in entries
        if r.get("next_action") == "enrich_company_contact_preview"
        and r.get("verification_status") in {"verified", "likely_verified"}
    ]

    companies = []
    for item in eligible:
        verified_url = str(item.get("candidate_url") or item.get("final_url") or "")
        contact_candidates = _build_candidates(verified_url, _CONTACT_PATHS)
        impressum_candidates = _build_candidates(verified_url, _IMPRESSUM_PATHS)

        companies.append({
            "company_name": str(item.get("company_name") or ""),
            "verified_domain": str(item.get("candidate_domain") or ""),
            "verified_url": verified_url,
            "verification_confidence": float(item.get("verification_confidence") or 0),
            "contact_candidate_urls": _dedupe(contact_candidates),
            "impressum_candidate_urls": _dedupe(impressum_candidates),
            "enrichment_status": "preview_only",
            "api_used": False,
            "http_used": False,
            "next_action": "fetch_contact_pages_preview",
        })

    preview_ready = len([c for c in companies if c["contact_candidate_urls"]])
    needs_review = len(companies) - preview_ready

    final = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "3.12",
        "total_companies": len(companies),
        "preview_ready": preview_ready,
        "needs_review": needs_review,
        "api_used": False,
        "http_used": False,
        "companies": companies,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Phase 3.12: Company Contact Preview ===")
    print(f"  total_companies: {len(companies)}")
    print(f"  preview_ready:   {preview_ready}")
    print(f"  needs_review:    {needs_review}")
    for c in companies:
        print(f"  {c['company_name']} | {c['verified_domain']} | contact={len(c['contact_candidate_urls'])} imp={len(c['impressum_candidate_urls'])} | {c['next_action']}")
    print()

    return final


if __name__ == "__main__":
    run()
