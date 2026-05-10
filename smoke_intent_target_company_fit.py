from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "output" / "latest" / "intent_target_job_detail_fetch.json"
OUTPUT_FILE = ROOT / "output" / "latest" / "intent_target_company_fit.json"

TARGET_INDUSTRY = "Marketingagentur"
TARGET_CITY = "Muenchen"

_INDUSTRY_TERMS = [
    "marketingagentur", "werbeagentur", "online marketing",
    "performance marketing", "seo", "social media", "agentur",
    "marketingkommunikation", "kanzleimarketing",
]

_CITY_TERMS = [
    "münchen", "muenchen", "munich",
]

_SIGNAL_TERMS = [
    "sales manager", "account manager", "business development",
    "vertrieb", "neukundenakquise", "pr / communications",
    "content marketing", "marketing manager",
]


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_text(company_name: str, title: str) -> str:
    return f"{company_name} {title}".lower()


def _fit_company(company_name: str, title: str, confidence: float) -> dict:
    combined = _build_text(company_name, title)

    industry_fit = any(t in combined for t in _INDUSTRY_TERMS)
    city_fit = any(t in combined for t in _CITY_TERMS)
    signal_fit = any(t in combined for t in _SIGNAL_TERMS)

    score = 0.0
    reasons: list[str] = []
    risk_flags: list[str] = []

    if industry_fit:
        score += 0.4
        reasons.append("industry_fit")
    else:
        risk_flags.append("no_industry_match")

    if city_fit:
        score += 0.3
        reasons.append("city_fit")
    else:
        risk_flags.append("no_city_match")

    if signal_fit:
        score += 0.3
        reasons.append("signal_fit")
    else:
        risk_flags.append("no_signal_match")

    if confidence >= 0.9:
        score += 0.2
        reasons.append("high_extraction_confidence")

    score = max(0.0, min(1.0, round(score, 3)))

    if score >= 0.75:
        fit_status = "target_fit"
        next_action = "search_company_website"
    elif score >= 0.5:
        fit_status = "maybe_fit"
        next_action = "search_company_website"
    elif score >= 0.3:
        fit_status = "weak_fit"
        next_action = "review"
    else:
        fit_status = "not_fit"
        next_action = "discard"

    return {
        "company_name": company_name,
        "original_title": title,
        "original_url": "",
        "extraction_method": "",
        "extraction_confidence": confidence,
        "target_industry": TARGET_INDUSTRY,
        "target_city": TARGET_CITY,
        "fit_status": fit_status,
        "fit_score": score,
        "industry_fit": industry_fit,
        "city_fit": city_fit,
        "signal_fit": signal_fit,
        "fit_reasons": reasons,
        "risk_flags": risk_flags,
        "next_action": next_action,
    }


def run() -> dict:
    payload = _safe_read_json(INPUT_FILE)
    results_raw = list((payload or {}).get("results") or [])

    fitted = []
    for item in results_raw:
        if not item.get("company_name_valid"):
            continue
        if item.get("next_action") != "fit_check_company":
            continue
        company_name = str(item.get("company_name_extracted") or item.get("company_name_extracted_raw") or "")
        title = str(item.get("original_title") or "")
        confidence = float(item.get("extraction_confidence") or 0)

        company = _fit_company(company_name, title, confidence)
        company["original_url"] = str(item.get("original_url") or "")
        company["extraction_method"] = str(item.get("extraction_method") or "")
        fitted.append(company)

    target_fit = len([f for f in fitted if f["fit_status"] == "target_fit"])
    maybe_fit = len([f for f in fitted if f["fit_status"] == "maybe_fit"])
    weak_fit = len([f for f in fitted if f["fit_status"] == "weak_fit"])
    not_fit = len([f for f in fitted if f["fit_status"] == "not_fit"])
    search_count = len([f for f in fitted if f["next_action"] == "search_company_website"])
    review_count = len([f for f in fitted if f["next_action"] == "review"])
    discard_count = len([f for f in fitted if f["next_action"] == "discard"])

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_file": str(INPUT_FILE),
        "total_companies": len(fitted),
        "target_fit": target_fit,
        "maybe_fit": maybe_fit,
        "weak_fit": weak_fit,
        "not_fit": not_fit,
        "search_company_website_count": search_count,
        "review_count": review_count,
        "discard_count": discard_count,
        "companies": fitted,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"total_companies: {len(fitted)}")
    print(f"target_fit: {target_fit}")
    print(f"maybe_fit: {maybe_fit}")
    print(f"weak_fit: {weak_fit}")
    print(f"not_fit: {not_fit}")
    print(f"search_company_website_count: {search_count}")
    print(f"review_count: {review_count}")
    print(f"discard_count: {discard_count}")
    for f in fitted:
        print(f"- {f['company_name']} | {f['fit_status']} | {f['fit_score']:.3f} | {f['next_action']} | {', '.join(f['fit_reasons']) or '--'}")
    print("SMOKE_OK")

    return output


if __name__ == "__main__":
    run()
