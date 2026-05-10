#!/usr/bin/env python3
"""
Phase 3.9 – Company Website Resolution Preview.

Erzeugt isoliert eine Vorschau, welche Website-Queries spaeter genutzt werden.
Keine externen Requests. Nur Preview.

Input (bevorzugt):
  output/latest/intent_resolved_company_fit.json

Input (Fallback):
  output/latest/intent_relevance_gated_detail_fetch.json

Output:
  output/latest/intent_company_website_resolution_preview.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # Bot-Root (ein Verzeichnis ueber modules/)
OUT = ROOT / "output"
LATEST = OUT / "latest"

INPUT_PRIMARY = LATEST / "intent_resolved_company_fit.json"
INPUT_FALLBACK = LATEST / "intent_relevance_gated_detail_fetch.json"
OUTPUT_FILE = LATEST / "intent_company_website_resolution_preview.json"


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_company_from_title(title: str) -> str:
    """Extrahiere Firmennamen aus typischen Stepstone-Titeln."""
    patterns = [
        r"(?:Job bei der|bei der)\s+Firma\s+(.+?)(?:\s+[-–—]|\s*$)",
        r"Firma\s+(.+?)(?:\s+[-–—]|\s*$)",
    ]
    for pat in patterns:
        m = re.search(pat, title, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".")
    # Fallback: Text vor erstem Bindestrich
    parts = re.split(r"\s+[-–—]\s+", title.strip(), maxsplit=1)
    return parts[0].strip()


_LEGAL_FORMS = re.compile(
    r'\b(GmbH|UG|AG|KG|OHG|SE|Ltd|Inc|GbR|e\.K\.|e\.V\.|Stiftung|AöR|KGaA|Limited|Corporation|Corp|LLC|LLP|S\.A\.|S\.L\.|B\.V\.|N\.V\.|SARL|SAS|Sp\.\s*z\s*o\.\s*o\.|s\.\s*r\.\s*o\.)\b',
    re.IGNORECASE
)

_GENERIC_BLOCKLIST = [
    "marketing und e-commerce",
    "marketing und e commerce",
    "marketing jobs",
    "marketing & sales",
    "senior account manager",
    "sales manager",
    "account manager",
    "business development",
    "business development manager",
    "junior sales manager",
    "promoter",
    "kauffrau",
    "kaufmann",
    "neukundenakquise",
    "vertrieb",
    "karriere",
    "stellenangebote",
    "stellenangebot",
    "job bei der firma",
    "jobs",
    "karriereseite",
]


def is_likely_real_company_name(company_name: str) -> tuple:
    """Prueft ob ein String ein echter Firmenname ist.

    Returns (is_valid, reject_reason).
    """
    name = company_name.strip()
    if not name or len(name) < 3:
        return False, "too_short"

    name_lower = name.lower()

    if _LEGAL_FORMS.search(name):
        return True, ""

    for blocked in _GENERIC_BLOCKLIST:
        if blocked in name_lower:
            return False, f"contains_generic_term:{blocked}"

    job_title_keywords = {
        "manager", "sales", "account", "business", "development",
        "promoter", "kauffrau", "kaufmann", "senior", "junior",
        "assistant", "director", "consultant", "specialist",
        "analyst", "coordinator", "executive", "officer",
        "representative", "agent", "advisor", "head", "lead",
    }
    words = re.findall(r'[A-Za-zÄÖÜäöüß]+', name_lower)
    if len(words) < 2:
        return False, "single_word_no_legal_form"

    job_word_count = sum(1 for w in words if w in job_title_keywords)
    if job_word_count >= len(words) * 0.5:
        return False, "job_title_keywords_dominant"

    return True, ""


def _is_company_candidate(item: dict) -> bool:
    """Prueft ob dieses Item ein Kandidat fuer Website-Resolution ist."""
    if item.get("recommended_next_action") == "resolve_company_website":
        return True
    if item.get("company_name_valid") is True:
        return True
    return False


def _build_queries(company_name: str, city: str = "") -> list[str]:
    """Erzeuge Website-Resolution-Queries fuer eine Firma."""
    name = company_name.strip()
    if not name:
        return []
    city_suffix = f" {city}" if city else ""
    queries = [
        f"{name} offizielle Website",
        f"{name} Impressum",
    ]
    if city:
        queries.append(f"{name} {city} Website")
    queries.extend([
        f"{name} Kontakt",
        f"{name} LinkedIn",
    ])
    # dedup while preserving order
    seen = set()
    result = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            result.append(q)
    return result


def run(industry: str = "", city: str = "") -> dict:
    """Fuehre Website-Resolution-Preview durch.
    
    Returns dict mit Preview-Daten.
    Schreibt Output nach OUTPUT_FILE.
    """
    # Input laden
    primary = _safe_read_json(INPUT_PRIMARY)
    fallback = _safe_read_json(INPUT_FALLBACK)

    # Datenquelle waehlen
    raw_list = None
    source_file = ""
    if isinstance(primary, list):
        raw_list = primary
        source_file = str(INPUT_PRIMARY.name)
    elif isinstance(primary, dict) and primary.get("results"):
        raw_list = primary["results"]
        source_file = str(INPUT_PRIMARY.name)
    elif isinstance(fallback, list):
        raw_list = fallback
        source_file = str(INPUT_FALLBACK.name)
    elif isinstance(fallback, dict) and fallback.get("results"):
        raw_list = fallback["results"]
        source_file = str(INPUT_FALLBACK.name)

    if not raw_list:
        result = {
            "phase": "3.9",
            "status": "no_input",
            "message": "Keine Input-Daten gefunden. intent_resolved_company_fit.json oder intent_relevance_gated_detail_fetch.json benoetigt.",
            "total_companies": 0,
            "preview_ready": 0,
            "needs_review": 0,
            "industry": industry,
            "city": city,
            "source_file": None,
            "api_used": False,
            "companies": [],
        }
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    # Firmen filtern
    candidates = []
    seen_companies = set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        if not _is_company_candidate(item):
            continue
        # Company-Name: explizit oder aus Title extrahieren
        company_name = str(item.get("company_name") or "").strip()
        if not company_name:
            company_name = _extract_company_from_title(str(item.get("title") or ""))
        if not company_name or len(company_name) < 2:
            continue
        if company_name.lower() in seen_companies:
            continue
        seen_companies.add(company_name.lower())

        fit_status = str(item.get("fit_status") or item.get("relevance_status") or "unknown")
        fit_score = float(item.get("fit_score") or item.get("relevance_score") or 0)

        name_valid, name_reject = is_likely_real_company_name(company_name)
        if name_valid:
            queries = _build_queries(company_name, city or item.get("city", ""))
            next_action = "search_official_website"
        else:
            queries = []
            next_action = "review_company_name"

        candidates.append({
            "company_name": company_name,
            "company_name_valid": name_valid,
            "company_name_reject_reason": name_reject,
            "fit_status": fit_status,
            "fit_score": fit_score,
            "original_url": str(item.get("url") or ""),
            "resolution_queries": queries,
            "resolution_status": "preview_only",
            "api_used": False,
            "next_action": next_action,
        })

    preview_ready = len([c for c in candidates if c["company_name_valid"] and c["resolution_queries"]])
    needs_review = len([c for c in candidates if not c["company_name_valid"]])

    result = {
        "phase": "3.9",
        "status": "preview_ready",
        "message": f"{preview_ready} Firmen mit Queries, {needs_review} ohne Queries.",
        "total_companies": len(candidates),
        "preview_ready": preview_ready,
        "needs_review": needs_review,
        "industry": industry,
        "city": city,
        "source_file": source_file,
        "api_used": False,
        "companies": candidates,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # Terminal-Ausgabe
    print(f"\n=== Phase 3.9: Company Website Resolution Preview ===")
    print(f"Input: {source_file or 'keine'}")
    print(f"  total_companies: {len(candidates)}")
    print(f"  preview_ready:   {preview_ready}")
    print(f"  needs_review:    {needs_review}")
    for c in candidates:
        qc = len(c["resolution_queries"])
        print(f"  {c['company_name']} | {c['fit_status']} | queries={qc} | {c['next_action']}")
    print()

    return result


if __name__ == "__main__":
    run()
