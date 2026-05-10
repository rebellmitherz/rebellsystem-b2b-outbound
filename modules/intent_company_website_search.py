#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from modules.intent_search_provider import search_intent_queries

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
LATEST = OUT / "latest"

INPUT_FILE = LATEST / "intent_company_website_resolution_preview.json"
OUTPUT_FILE = LATEST / "intent_company_website_search.json"

_PROVIDER = "serper"
_MAX_COMPANIES = 3
_MAX_QUERIES_PER_COMPANY = 2
_MAX_RESULTS_PER_QUERY = 3

_BLOCKED_DOMAINS = {
    "linkedin.com", "xing.com", "facebook.com", "instagram.com",
    "stepstone.de", "indeed.com", "indeed.de", "glassdoor.com",
    "wikipedia.org", "kununu.com", "gelbeseiten.de", "dasoertliche.de",
    "meinestadt.de", "business-on.de", "northdata.de", "crunchbase.com",
    "handelsregister.de", "youtube.com", "tiktok.com", "reddit.com",
}
_BLOCKED_DOMAIN_PARTS = [
    "stepstone", "indeed", "glassdoor", "linkedin", "xing", "facebook",
    "instagram", "wikipedia", "gelbeseiten", "dasoertliche", "meinestadt",
    "news", "presse", "blogspot", "wordpress", "kununu",
]


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _domain_from_url(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower().strip()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _company_tokens(company_name: str) -> list[str]:
    legal_forms = {
        "gmbh", "ug", "ag", "kg", "ohg", "se", "ltd", "inc", "gbr",
        "corp", "llc", "llp", "sa", "sl", "bv", "nv", "sarl", "sas",
    }
    words = []
    for raw in company_name.lower().replace("&", " ").replace("-", " ").split():
        token = "".join(ch for ch in raw if ch.isalnum())
        if len(token) < 3:
            continue
        if token in legal_forms:
            continue
        words.append(token)
    return words


def _score_candidate(company_name: str, title: str, url: str, snippet: str) -> dict:
    domain = _domain_from_url(url)
    domain_low = domain.lower()
    title_low = (title or "").lower()
    snippet_low = (snippet or "").lower()
    tokens = _company_tokens(company_name)

    if not domain:
        return {
            "domain": "",
            "is_official_candidate": False,
            "domain_confidence": 0.0,
            "reject_reason": "missing_domain",
        }

    for blocked in _BLOCKED_DOMAINS:
        if domain_low == blocked or domain_low.endswith("." + blocked):
            return {
                "domain": domain,
                "is_official_candidate": False,
                "domain_confidence": 0.0,
                "reject_reason": f"blocked_domain:{blocked}",
            }
    for blocked_part in _BLOCKED_DOMAIN_PARTS:
        if blocked_part in domain_low:
            return {
                "domain": domain,
                "is_official_candidate": False,
                "domain_confidence": 0.0,
                "reject_reason": f"blocked_domain_part:{blocked_part}",
            }

    score = 0.0
    matched_tokens = 0
    for token in tokens:
        if token in domain_low:
            score += 0.4
            matched_tokens += 1
        elif token in title_low:
            score += 0.15
        elif token in snippet_low:
            score += 0.05

    if matched_tokens >= 1:
        score += 0.2
    if len(tokens) >= 2 and matched_tokens >= 2:
        score += 0.15
    if domain_low.endswith(".de") or domain_low.endswith(".com"):
        score += 0.05

    score = min(score, 1.0)
    is_official = score >= 0.45
    reject_reason = "" if is_official else "low_brand_match"

    return {
        "domain": domain,
        "is_official_candidate": is_official,
        "domain_confidence": round(score, 3),
        "reject_reason": reject_reason,
    }


def run() -> dict:
    payload = _safe_read_json(INPUT_FILE)
    companies = list((payload or {}).get("companies") or [])
    eligible = [
        c for c in companies
        if c.get("company_name_valid") is True and c.get("next_action") == "search_official_website"
    ][:_MAX_COMPANIES]

    results = []
    searched_companies = 0
    official_candidates_count = 0

    for company in eligible:
        company_name = str(company.get("company_name") or "").strip()
        fit_status = str(company.get("fit_status") or "unknown")
        fit_score = float(company.get("fit_score") or 0)
        queries_used = list((company.get("resolution_queries") or [])[:_MAX_QUERIES_PER_COMPANY])

        company_result = {
            "company_name": company_name,
            "fit_status": fit_status,
            "fit_score": fit_score,
            "queries_used": queries_used,
            "candidates": [],
            "best_official_domain": "",
            "best_official_url": "",
            "best_confidence": 0.0,
            "website_resolution_status": "error",
            "next_action": "review",
        }

        if not queries_used:
            company_result["website_resolution_status"] = "needs_review"
            company_result["next_action"] = "review"
            results.append(company_result)
            continue

        try:
            search_batches = search_intent_queries(
                queries_used,
                provider=_PROVIDER,
                max_results_per_query=_MAX_RESULTS_PER_QUERY,
            )
            searched_companies += 1
        except Exception as exc:
            company_result["website_resolution_status"] = "error"
            company_result["next_action"] = "review"
            company_result["error"] = str(exc)
            results.append(company_result)
            continue

        best = None
        for batch in search_batches:
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
                    "domain": scored["domain"],
                    "is_official_candidate": scored["is_official_candidate"],
                    "domain_confidence": scored["domain_confidence"],
                    "reject_reason": scored["reject_reason"],
                }
                company_result["candidates"].append(candidate)
                if candidate["is_official_candidate"]:
                    if best is None or candidate["domain_confidence"] > best["domain_confidence"]:
                        best = candidate

        if best:
            company_result["best_official_domain"] = best["domain"]
            company_result["best_official_url"] = best["url"]
            company_result["best_confidence"] = best["domain_confidence"]
            company_result["website_resolution_status"] = "official_candidate_found"
            company_result["next_action"] = "verify_website"
            official_candidates_count += 1
        else:
            company_result["website_resolution_status"] = "no_official_candidate"
            company_result["next_action"] = "review"

        results.append(company_result)

    final = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": _PROVIDER,
        "total_companies": len(companies),
        "searched_companies": searched_companies,
        "official_candidates_count": official_candidates_count,
        "results": results,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    official_found = sum(1 for r in results if r["website_resolution_status"] == "official_candidate_found")
    needs_review = sum(1 for r in results if r["website_resolution_status"] in {"needs_review", "no_official_candidate", "error"})

    print("\n=== Phase 3.10: Company Website Search ===")
    print(f"  total_companies:          {len(companies)}")
    print(f"  searched_companies:       {searched_companies}")
    print(f"  official_candidate_found: {official_found}")
    print(f"  needs_review:             {needs_review}")
    for r in results:
        print(f"  {r['company_name']} | {r['best_official_domain'] or '-'} | {r['best_confidence']:.3f} | {r['website_resolution_status']} | {r['next_action']}")
    print()

    return final


if __name__ == "__main__":
    run()
