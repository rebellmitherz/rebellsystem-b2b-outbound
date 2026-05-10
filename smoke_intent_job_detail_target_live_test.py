from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from modules.intent_job_detail_query_builder import build_job_detail_queries
from modules.intent_portal_url_classifier import classify_portal_url
from modules.intent_relevance_filter import classify_job_detail_relevance
from modules.intent_search_provider import _read_serper_key, search_intent_queries

ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = ROOT / "output" / "latest" / "intent_job_detail_target_live_test.json"

INDUSTRY = "Marketingagentur"
CITY = "Muenchen"
SIGNAL_TYPE = "sales_hiring"
RELEVANCE_FOCUS = "target_industry"
MAX_QUERIES = 3
MAX_RESULTS_PER_QUERY = 3
PROVIDER = "serper"


def _write_output(payload: dict) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _count_types(results: list[dict]) -> dict[str, int]:
    counts = {
        "job_detail_page": 0,
        "listing_page": 0,
        "search_page": 0,
        "company_profile": 0,
        "unknown": 0,
    }
    for item in results:
        t = str(item.get("portal_url_type") or "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def _count_relevance(results: list[dict]) -> dict[str, int]:
    counts = {
        "relevant": 0,
        "maybe_relevant": 0,
        "needs_review": 0,
        "irrelevant": 0,
    }
    for item in results:
        status = item.get("relevance_status")
        if status in counts:
            counts[status] += 1
    return counts


def run() -> dict:
    queries = build_job_detail_queries(
        industry=INDUSTRY,
        city=CITY,
        signal_type=SIGNAL_TYPE,
        relevance_focus=RELEVANCE_FOCUS,
    )[:MAX_QUERIES]

    if not _read_serper_key():
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "skipped_no_key",
            "provider": PROVIDER,
            "query_count": len(queries),
            "raw_result_count": 0,
            "queries_used": queries,
            "results": [],
        }
        _write_output(payload)
        print("SKIPPED_NO_KEY")
        return payload

    batches = search_intent_queries(
        queries=queries,
        provider=PROVIDER,
        max_results_per_query=MAX_RESULTS_PER_QUERY,
    )

    raw_results: list[dict] = []
    for batch in batches:
        for item in batch.get("results") or []:
            raw_results.append({
                "query": batch.get("query", ""),
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("snippet") or ""),
                "provider": str(item.get("provider") or PROVIDER),
            })

    raw_results = raw_results[: MAX_QUERIES * MAX_RESULTS_PER_QUERY]

    classified = [
        {
            **item,
            **classify_portal_url({
                "url": item.get("url"),
                "title": item.get("title"),
                "snippet": item.get("snippet"),
            }),
        }
        for item in raw_results
    ]

    enriched = classify_job_detail_relevance(classified, industry=INDUSTRY, city=CITY)

    type_counts = _count_types(enriched)
    relevance_counts = _count_relevance(enriched)
    should_fetch_detail = len([x for x in enriched if x.get("should_fetch_detail") is True])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": PROVIDER,
        "industry": INDUSTRY,
        "city": CITY,
        "signal_type": SIGNAL_TYPE,
        "relevance_focus": RELEVANCE_FOCUS,
        "query_count": len(queries),
        "raw_result_count": len(enriched),
        "queries_used": queries,
        "counts": {
            **type_counts,
            "should_fetch_detail": should_fetch_detail,
            **relevance_counts,
        },
        "results": enriched,
    }
    _write_output(payload)

    print(f"query_count: {len(queries)}")
    print(f"raw_result_count: {len(enriched)}")
    print(f"job_detail_page: {type_counts['job_detail_page']}")
    print(f"listing_page: {type_counts['listing_page']}")
    print(f"search_page: {type_counts['search_page']}")
    print(f"company_profile: {type_counts['company_profile']}")
    print(f"unknown: {type_counts['unknown']}")
    print(f"should_fetch_detail: {should_fetch_detail}")
    print(f"relevant: {relevance_counts['relevant']}")
    print(f"maybe_relevant: {relevance_counts['maybe_relevant']}")
    print(f"needs_review: {relevance_counts['needs_review']}")
    print(f"irrelevant: {relevance_counts['irrelevant']}")
    for item in enriched[:9]:
        print(f"- {item.get('portal_url_type','unknown')} | {item.get('title','')[:90]} | {item.get('url','')}")
    print("SMOKE_OK")

    return payload


if __name__ == "__main__":
    run()
