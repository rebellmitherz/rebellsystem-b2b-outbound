from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "output" / "latest" / "intent_job_detail_target_live_test.json"
OUTPUT_FILE = ROOT / "output" / "latest" / "intent_target_dedup_funnel.json"
MAX_SELECTED = 3


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if not k.lower().startswith("utm_")]
    query = urlencode(query_pairs)
    return urlunparse((scheme, netloc, path, "", query, ""))


def _company_hint(title: str, url: str) -> str:
    text = f"{title} {url}"
    patterns = [
        r"-\s+([A-ZÄÖÜ0-9][A-Za-zÄÖÜäöüß0-9 .&\-/]+?)\s*--",
        r"bei der Firma\s+([A-ZÄÖÜ0-9][A-Za-zÄÖÜäöüß0-9 .&\-/]+)",
        r"in Muenchen\s+([A-ZÄÖÜ0-9][A-Za-zÄÖÜäöüß0-9 .&\-/]+)",
        r"in München\s+([A-ZÄÖÜ0-9][A-Za-zÄÖÜäöüß0-9 .&\-/]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip(" -")
    title_parts = [p.strip() for p in re.split(r"\s+-\s+", title) if p.strip()]
    if len(title_parts) >= 2:
        return title_parts[-1][:120]
    return ""


def run() -> dict:
    payload = _safe_read_json(INPUT_FILE)
    raw_results = list((payload or {}).get("results") or [])
    raw_result_count = len(raw_results)

    kept_by_url: dict[str, dict] = {}
    for item in raw_results:
        if item.get("portal_url_type") != "job_detail_page":
            continue
        relevance_status = str(item.get("relevance_status") or "")
        if relevance_status == "irrelevant":
            continue
        if relevance_status and relevance_status not in {"relevant", "maybe_relevant", "needs_review"}:
            continue

        normalized = _normalize_url(str(item.get("url") or ""))
        if not normalized:
            continue

        existing = kept_by_url.get(normalized)
        current_score = float(item.get("relevance_score") or 0)
        if existing is None or current_score > float(existing.get("relevance_score") or 0):
            kept_by_url[normalized] = {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "signal_type": str(item.get("signal_type") or (payload or {}).get("signal_type") or "sales_hiring"),
                "portal_url_type": str(item.get("portal_url_type") or ""),
                "relevance_status": relevance_status or "needs_review",
                "relevance_score": current_score,
                "recommended_next_action": str(item.get("recommended_next_action") or "review"),
                "company_hint_from_url_or_title": _company_hint(str(item.get("title") or ""), str(item.get("url") or "")),
                "next_action": "fetch_job_detail_preview",
                "normalized_url": normalized,
            }

    unique_candidates = list(kept_by_url.values())
    unique_candidates.sort(
        key=lambda x: (
            {"relevant": 3, "maybe_relevant": 2, "needs_review": 1}.get(x.get("relevance_status", ""), 0),
            float(x.get("relevance_score") or 0),
        ),
        reverse=True,
    )
    selected_candidates = unique_candidates[:MAX_SELECTED]

    unique_job_detail_count = len(unique_candidates)
    duplicates_removed_count = max(0, len([
        r for r in raw_results if r.get("portal_url_type") == "job_detail_page"
    ]) - unique_job_detail_count)
    selected_for_fetch_count = len(selected_candidates)

    for item in selected_candidates:
        item.pop("normalized_url", None)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_file": str(INPUT_FILE),
        "raw_result_count": raw_result_count,
        "unique_job_detail_count": unique_job_detail_count,
        "selected_for_fetch_count": selected_for_fetch_count,
        "duplicates_removed_count": duplicates_removed_count,
        "selected_candidates": selected_candidates,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"raw_result_count: {raw_result_count}")
    print(f"unique_job_detail_count: {unique_job_detail_count}")
    print(f"duplicates_removed_count: {duplicates_removed_count}")
    print(f"selected_for_fetch_count: {selected_for_fetch_count}")
    for item in selected_candidates:
        print(f"- {item['title']} | {item['relevance_status']} | {item['next_action']} | {item['url']}")
    print("SMOKE_OK")

    return output


if __name__ == "__main__":
    run()
