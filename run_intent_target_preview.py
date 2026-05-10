from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from modules.intent_job_detail_query_builder import build_job_detail_queries
from modules.intent_search_provider import _read_serper_key, search_intent_queries
from modules.intent_portal_url_classifier import classify_portal_url
from modules.intent_relevance_filter import classify_job_detail_relevance
from modules.intent_portal_detail_resolver import resolve_portal_job_detail_preview

# ── Konfiguration ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
OUTPUT_JSON = ROOT / "output" / "latest" / "intent_target_preview_report.json"
OUTPUT_MD = ROOT / "output" / "latest" / "intent_target_preview_report.md"

INDUSTRY = "Marketingagentur"
CITY = "Muenchen"
SIGNAL_TYPE = "sales_hiring"
RELEVANCE_FOCUS = "target_industry"
PROVIDER = "serper"

MAX_QUERIES = 5
MAX_RESULTS_PER_QUERY = 3
MAX_UNIQUE_JOB_DETAILS = 5
MAX_DETAIL_FETCHES = 3

# ── Fit-Scoring ──────────────────────────────────────────────────────────────
_INDUSTRY_TERMS = [
    "marketingagentur", "werbeagentur", "online marketing", "performance marketing",
    "seo", "social media", "agentur", "marketingkommunikation", "kanzleimarketing",
]
_CITY_TERMS = ["münchen", "muenchen", "munich"]
_SIGNAL_TERMS = [
    "sales manager", "account manager", "business development", "vertrieb",
    "neukundenakquise", "pr / communications", "content marketing", "marketing manager",
]


def _normalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    query = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")])
    return urlunparse((scheme, netloc, path, "", query, ""))


def _fit_company(name: str, title: str, confidence: float) -> dict:
    combined = f"{name} {title}".lower()
    ind = any(t in combined for t in _INDUSTRY_TERMS)
    cit = any(t in combined for t in _CITY_TERMS)
    sig = any(t in combined for t in _SIGNAL_TERMS)
    score = 0.0
    reasons = []
    if ind:
        score += 0.4
        reasons.append("industry_fit")
    if cit:
        score += 0.3
        reasons.append("city_fit")
    if sig:
        score += 0.3
        reasons.append("signal_fit")
    if confidence >= 0.9:
        score += 0.2
        reasons.append("high_confidence")
    score = round(max(0.0, min(1.0, score)), 3)
    if score >= 0.75:
        status = "target_fit"
    elif score >= 0.5:
        status = "maybe_fit"
    elif score >= 0.3:
        status = "weak_fit"
    else:
        status = "discard"
    return {
        "company_name": name,
        "fit_status": status,
        "fit_score": score,
        "reasons": reasons,
        "industry_fit": ind,
        "city_fit": cit,
        "signal_fit": sig,
    }


def _next_action(fit_status: str) -> str:
    if fit_status in ("target_fit", "maybe_fit"):
        return "candidate_for_manual_followup"
    if fit_status == "weak_fit":
        return "review_company"
    return "discard"


# ── Main ─────────────────────────────────────────────────────────────────────
def run() -> dict:
    queries = build_job_detail_queries(INDUSTRY, CITY, SIGNAL_TYPE, relevance_focus=RELEVANCE_FOCUS)
    queries = queries[:MAX_QUERIES]

    if not _read_serper_key():
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "SKIPPED_NO_KEY",
            "queries_used": len(queries),
            "raw_results": 0,
            "unique_job_detail_pages": 0,
            "fetched_details": 0,
            "resolved_companies": 0,
            "target_fit": 0,
            "maybe_fit": 0,
            "discard": 0,
            "results": [],
        }
        _write_outputs(payload)
        print("SKIPPED_NO_KEY")
        return payload

    # 1. Suche
    batches = search_intent_queries(queries, provider=PROVIDER, max_results_per_query=MAX_RESULTS_PER_QUERY)
    raw = []
    for b in batches:
        for item in b.get("results") or []:
            raw.append({
                "query": b.get("query", ""),
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("snippet") or ""),
            })
    raw = raw[:MAX_QUERIES * MAX_RESULTS_PER_QUERY]

    # 2. Klassifikation
    classified = []
    for item in raw:
        info = classify_portal_url({"url": item["url"], "title": item["title"], "snippet": item["snippet"]})
        classified.append({**item, **info})

    # 3. Relevance
    enriched = classify_job_detail_relevance(classified, industry=INDUSTRY, city=CITY)

    # 4. Deduplizieren — nur job_detail_page, nicht irrelevant
    by_url = {}
    for item in enriched:
        if item.get("portal_url_type") != "job_detail_page":
            continue
        if item.get("relevance_status") == "irrelevant":
            continue
        norm = _normalize_url(item.get("url") or "")
        if not norm:
            continue
        cur_score = float(item.get("relevance_score") or 0)
        if norm not in by_url or cur_score > float(by_url[norm].get("relevance_score") or 0):
            by_url[norm] = item

    unique = sorted(
        by_url.values(),
        key=lambda x: float(x.get("relevance_score") or 0),
        reverse=True,
    )
    unique = unique[:MAX_UNIQUE_JOB_DETAILS]

    # 5. Detail-Fetches
    fetches = []
    for candidate in unique[:MAX_DETAIL_FETCHES]:
        resolved = resolve_portal_job_detail_preview(candidate)
        fetches.append({
            "url": resolved.get("original_url", ""),
            "title": resolved.get("original_title", ""),
            "fetched": bool(resolved.get("fetched")),
            "http_status": resolved.get("http_status"),
            "extraction_method": resolved.get("extraction_method", "-"),
            "company_name": resolved.get("company_name_extracted", ""),
            "company_name_valid": bool(resolved.get("company_name_valid")),
        })

    # 6. Company-Fit
    resolved_companies = 0
    target_fit = 0
    maybe_fit = 0
    discard = 0
    results = []

    for f in fetches:
        if not f["company_name_valid"] or not f["company_name"]:
            results.append({
                "url": f["url"],
                "title": f["title"],
                "company_name": f["company_name"] or "-",
                "company_name_valid": f["company_name_valid"],
                "fit_status": "discard",
                "fit_score": 0.0,
                "fit_reasons": [],
                "next_action": "discard",
            })
            discard += 1
            continue

        fit = _fit_company(f["company_name"], f["title"], 0.95)
        resolved_companies += 1
        if fit["fit_status"] in ("target_fit", "maybe_fit"):
            if fit["fit_status"] == "target_fit":
                target_fit += 1
            else:
                maybe_fit += 1
        else:
            discard += 1

        results.append({
            "url": f["url"],
            "title": f["title"],
            "company_name": f["company_name"],
            "company_name_valid": f["company_name_valid"],
            **fit,
            "next_action": _next_action(fit["fit_status"]),
        })

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "industry": INDUSTRY,
        "city": CITY,
        "signal_type": SIGNAL_TYPE,
        "relevance_focus": RELEVANCE_FOCUS,
        "queries_used": len(queries),
        "raw_results": len(enriched),
        "unique_job_detail_pages": len(unique),
        "fetched_details": len(fetches),
        "resolved_companies": resolved_companies,
        "target_fit": target_fit,
        "maybe_fit": maybe_fit,
        "discard": discard,
        "results": results,
    }

    _write_outputs(payload)

    print(f"queries_used: {payload['queries_used']}")
    print(f"raw_results: {payload['raw_results']}")
    print(f"unique_job_detail_pages: {payload['unique_job_detail_pages']}")
    print(f"fetched_details: {payload['fetched_details']}")
    print(f"resolved_companies: {payload['resolved_companies']}")
    print(f"target_fit: {payload['target_fit']}")
    print(f"maybe_fit: {payload['maybe_fit']}")
    print(f"discard: {payload['discard']}")
    for r in results:
        print(f"- {r['company_name']} | {r['fit_status']} | {r['next_action']} | {r['url'][:100]}")
    print("RUN_OK")

    return payload


def _write_outputs(payload: dict) -> None:
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Intent Target Preview Report",
        f"",
        f"**Generated:** {payload.get('generated_at', '-')}",
        f"**Industry:** {payload.get('industry', '-')}",
        f"**City:** {payload.get('city', '-')}",
        f"**Signal:** {payload.get('signal_type', '-')}",
        f"**Focus:** {payload.get('relevance_focus', '-')}",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Queries Used | {payload.get('queries_used', 0)} |",
        f"| Raw Results | {payload.get('raw_results', 0)} |",
        f"| Unique Job Detail Pages | {payload.get('unique_job_detail_pages', 0)} |",
        f"| Fetched Details | {payload.get('fetched_details', 0)} |",
        f"| Resolved Companies | {payload.get('resolved_companies', 0)} |",
        f"| Target Fit | {payload.get('target_fit', 0)} |",
        f"| Maybe Fit | {payload.get('maybe_fit', 0)} |",
        f"| Discard | {payload.get('discard', 0)} |",
        f"",
        f"## Results",
        f"",
    ]

    results = payload.get("results") or []
    if results:
        lines.append("| Company | Fit Status | Score | Next Action |")
        lines.append("|---------|------------|-------|-------------|")
        for r in results:
            lines.append(
                f"| {r.get('company_name', '-')} | {r.get('fit_status', '-')} | "
                f"{r.get('fit_score', 0):.3f} | {r.get('next_action', '-')} |"
            )
    else:
        lines.append("_No results._")

    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    run()
