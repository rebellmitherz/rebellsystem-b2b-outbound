from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from modules.intent_portal_detail_resolver import resolve_portal_job_detail_preview

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "output" / "latest" / "intent_target_dedup_funnel.json"
OUTPUT_FILE = ROOT / "output" / "latest" / "intent_target_job_detail_fetch.json"
MAX_URLS = 3


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _next_action_for(result: dict) -> str:
    if result.get("company_name_valid") is True:
        return "fit_check_company"
    if result.get("resolver_status") in {"extracted_but_rejected", "unresolved"}:
        return "review"
    return "discard"


def run() -> dict:
    payload = _safe_read_json(INPUT_FILE)
    selected = list((payload or {}).get("selected_candidates") or [])[:MAX_URLS]

    results = []
    for candidate in selected:
        resolved = resolve_portal_job_detail_preview(candidate)
        results.append({
            "original_title": str(resolved.get("original_title") or candidate.get("title") or ""),
            "original_url": str(resolved.get("original_url") or candidate.get("url") or ""),
            "fetched": bool(resolved.get("fetched")),
            "http_status": resolved.get("http_status"),
            "company_name_extracted_raw": str(resolved.get("company_name_extracted_raw") or ""),
            "company_name_extracted": str(resolved.get("company_name_extracted") or ""),
            "company_name_valid": bool(resolved.get("company_name_valid")),
            "company_name_reject_reason": str(resolved.get("company_name_reject_reason") or ""),
            "extraction_method": str(resolved.get("extraction_method") or ""),
            "extraction_confidence": float(resolved.get("extraction_confidence") or 0),
            "resolver_status": str(resolved.get("resolver_status") or ""),
            "next_action": _next_action_for(resolved),
            "error": str(resolved.get("error") or ""),
        })

    fetched_count = len([r for r in results if r.get("fetched")])
    resolved_valid_count = len([r for r in results if r.get("resolver_status") == "resolved_valid"])
    rejected_count = len([r for r in results if r.get("resolver_status") == "extracted_but_rejected"])
    unresolved_count = len([r for r in results if r.get("resolver_status") == "unresolved"])
    failed_count = len([r for r in results if r.get("resolver_status") == "error"])

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_file": str(INPUT_FILE),
        "total_selected": len(selected),
        "fetched_count": fetched_count,
        "resolved_valid_count": resolved_valid_count,
        "rejected_count": rejected_count,
        "unresolved_count": unresolved_count,
        "failed_count": failed_count,
        "results": results,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"total_selected: {len(selected)}")
    print(f"fetched_count: {fetched_count}")
    print(f"resolved_valid_count: {resolved_valid_count}")
    print(f"rejected_count: {rejected_count}")
    print(f"unresolved_count: {unresolved_count}")
    print(f"failed_count: {failed_count}")
    for item in results:
        print(
            f"- {item['original_title']} | {item['company_name_extracted'] or item['company_name_extracted_raw'] or '-'} | "
            f"{item['company_name_valid']} | {item['extraction_method'] or '-'} | {item['extraction_confidence']:.2f} | {item['next_action']}"
        )
    print("SMOKE_OK")

    return output


if __name__ == "__main__":
    run()
