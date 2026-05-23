"""
Resolved Company Fit Check — isolierter Zielgruppen-Fit für extrahierte Firmen.

Bewertet nur Einträge mit company_name_valid == true oder
resolver_status == "resolved_valid".

Keine externen Requests. Reine Textanalyse.

Verwendung:
  from modules.intent_resolved_company_fit import fit_check_resolved_companies
"""
from __future__ import annotations

from typing import Any

# Fallback wenn target_city leer (Legacy-Verhalten: München-Lauf)
_MISMATCH_CITIES = (
    "hamburg", "berlin", "köln", "frankfurt", "stuttgart",
    "düsseldorf", "ingolstadt", "wiesbaden", "potsdam", "oranienburg",
    "harburg", "deutschlandweit", "remote",
)

_CITY_MATCH_TERMS = ("münchen", "muenchen", "munich", "bayern")

# Umlaut-/Alias-Mapping (identisch zu intent_relevance_filter — bewusst dupliziert,
# kein gemeinsamer Import um Modul-Kopplung zu vermeiden)
_CITY_ALIASES: dict[str, list[str]] = {
    "münchen": ["münchen", "muenchen", "munich"],
    "muenchen": ["münchen", "muenchen", "munich"],
    "köln": ["köln", "koeln", "cologne"],
    "koeln": ["köln", "koeln", "cologne"],
    "düsseldorf": ["düsseldorf", "duesseldorf"],
    "duesseldorf": ["düsseldorf", "duesseldorf"],
    "nürnberg": ["nürnberg", "nuernberg"],
    "nuernberg": ["nürnberg", "nuernberg"],
}


def _city_match_terms(city: str) -> list[str]:
    """Gibt Suchterme für die Zielstadt zurück — mit Umlaut-Varianten.

    Fallback auf München-Liste wenn city leer (Legacy).
    """
    c = city.strip().lower()
    if not c:
        return list(_CITY_MATCH_TERMS)
    return _CITY_ALIASES.get(c, [c])


def _city_mismatch_terms(city: str) -> list[str]:
    """Mismatch-Liste ohne die Zielstadt selbst (verhindert Selbst-Bestrafung)."""
    match_set = set(_city_match_terms(city))
    return [t for t in _MISMATCH_CITIES if t not in match_set]

_INDUSTRY_FIT_TERMS = (
    "marketingagentur", "werbeagentur", "online marketing",
    "performance marketing", "social media", "seo", "agentur",
    "marketingkommunikation", "marktforschung",
    "research", "consulting", "beratung",
)

_SIGNAL_FIT_TERMS = (
    "sales manager", "account manager", "business development",
    "vertrieb", "neukundenakquise", "außendienst", "aussendienst",
    "sdr", "bdr", "new business",
    "sales executive", "account executive",
)


def _join_search_text(item: dict[str, Any]) -> str:
    parts = [
        str(item.get(k) or "") for k in (
            "original_title", "page_title", "company_name_extracted",
            "original_url",
        )
    ]
    # Snippet kann unter verschiedenen Keys liegen
    snippet = str(item.get("original_snippet") or item.get("snippet") or "")
    if snippet:
        parts.append(snippet)
    return " ".join(parts).lower()


def fit_check_resolved_companies(
    results: list[dict[str, Any]],
    target_industry: str,
    target_city: str,
) -> list[dict[str, Any]]:
    """Reichert resolved-valid Einträge um Zielgruppen-Fit-Felder an."""
    enriched: list[dict[str, Any]] = []
    for item in results:
        valid = (
            item.get("company_name_valid") is True
            or str(item.get("resolver_status") or "").strip() == "resolved_valid"
        )
        if not valid:
            enriched.append(item)
            continue

        entry = dict(item)
        text = _join_search_text(entry)
        company = str(entry.get("company_name_extracted") or "").strip()
        extraction_confidence = float(entry.get("extraction_confidence") or 0.0)

        industry_fit = any(t in text for t in _INDUSTRY_FIT_TERMS)
        city_fit = any(t in text for t in _city_match_terms(target_city))
        signal_fit = any(t in text for t in _SIGNAL_FIT_TERMS)
        wrong_city = any(t in text for t in _city_mismatch_terms(target_city))

        score = 0.0
        fit_reasons: list[str] = []
        risk_flags: list[str] = []

        if industry_fit:
            score += 0.4
            fit_reasons.append("industry_fit")
        else:
            risk_flags.append("no_industry_match")
        if city_fit:
            score += 0.3
            fit_reasons.append("city_fit")
        else:
            risk_flags.append("no_city_match")
        if signal_fit:
            score += 0.3
            fit_reasons.append("signal_fit")
        else:
            risk_flags.append("no_signal_match")
        if extraction_confidence >= 0.9:
            score += 0.2
            fit_reasons.append("high_extraction_confidence")
        if wrong_city and not city_fit:
            score -= 0.4
            risk_flags.append("wrong_city")
        elif wrong_city and city_fit:
            score -= 0.2
            risk_flags.append("mixed_city_signals")

        score = round(max(0.0, min(1.0, score)), 2)

        if score >= 0.75:
            fit_status = "target_fit"
        elif score >= 0.5:
            fit_status = "maybe_fit"
        elif score >= 0.3:
            fit_status = "weak_fit"
        else:
            fit_status = "not_fit"

        if fit_status in ("target_fit", "maybe_fit"):
            next_action = "resolve_company_website"
        elif fit_status == "weak_fit":
            next_action = "review"
        else:
            next_action = "discard"

        entry.update({
            "company_name": company,
            "target_industry": target_industry,
            "target_city": target_city,
            "fit_status": fit_status,
            "fit_score": score,
            "industry_fit": industry_fit,
            "city_fit": city_fit,
            "signal_fit": signal_fit,
            "fit_reasons": fit_reasons,
            "risk_flags": risk_flags,
            "recommended_next_action": next_action,
        })
        enriched.append(entry)

    return enriched
