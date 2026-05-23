"""
Intent Relevance Filter — isolierter Relevance-Scorer für Job-Detail-Treffer.

Bewertet nur Ergebnisse mit portal_url_type == "job_detail_page".
Keine externen Requests. Reine Textanalyse.

Verwendung:
  from modules.intent_relevance_filter import classify_job_detail_relevance
"""
from __future__ import annotations

from typing import Any

# ── Keyword-Definitionen ─────────────────────────────────────────────────────

# Fallback wenn city-Parameter leer (Legacy-Verhalten: München-Lauf)
_CITY_MATCH_TERMS = [
    "münchen", "muenchen", "munich",
]

_CITY_MISMATCH_TERMS = [
    "hamburg", "ingolstadt", "berlin", "köln", "frankfurt",
    "stuttgart", "düsseldorf", "remote", "deutschlandweit",
    "potsdam", "oranienburg", "wiesbaden", "harburg",
]

# Umlaut-/Alias-Mapping für häufige deutsche Städte
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
    return [t for t in _CITY_MISMATCH_TERMS if t not in match_set]

_INDUSTRY_TERMS = [
    "marketingagentur", "werbeagentur", "online marketing",
    "performance marketing", "social media", "seo", "agentur",
    "marketingkommunikation", "marketing agentur",
]

_ROLE_SIGNAL_TERMS = [
    "sales manager", "account manager", "business development",
    "vertrieb", "neukundenakquise", "außendienst", "aussendienst",
    "sdr", "bdr", "new business", "kundenbetreuung",
    "sales executive", "account executive",
]


def _text_block(result: dict[str, Any]) -> str:
    title = str(result.get("title") or "")
    url = str(result.get("url") or "")
    snippet = str(result.get("snippet") or "")
    return f"{title} {url} {snippet}"


def classify_job_detail_relevance(
    results: list[dict[str, Any]],
    industry: str,
    city: str,
) -> list[dict[str, Any]]:
    """Reichert jedes job_detail_page-Ergebnis um Relevance-Felder an.

    Andere portal_url_type-Einträge werden unverändert durchgereicht.
    """
    enriched: list[dict[str, Any]] = []
    for item in results:
        if item.get("portal_url_type") != "job_detail_page":
            enriched.append(item)
            continue

        entry = dict(item)
        text = _text_block(entry).lower()

        city_match = any(t in text for t in _city_match_terms(city))
        city_mismatch = any(t in text for t in _city_mismatch_terms(city))
        industry_match = any(t in text for t in _INDUSTRY_TERMS)
        role_signal_match = any(t in text for t in _ROLE_SIGNAL_TERMS)

        score = 0.0
        relevance_reasons: list[str] = []
        rejection_reasons: list[str] = []

        if city_match:
            score += 0.4
            relevance_reasons.append("city_match")
        if industry_match:
            score += 0.3
            relevance_reasons.append("industry_match")
        if role_signal_match:
            score += 0.3
            relevance_reasons.append("role_signal_match")

        if city_mismatch and not city_match:
            score -= 0.4
            rejection_reasons.append("wrong_city")
        elif city_mismatch and city_match:
            # gemischte Stadt-Signale → leichte Abwertung
            score -= 0.2
            rejection_reasons.append("mixed_city_signals")

        if not relevance_reasons:
            rejection_reasons.append("no_positive_signal")

        score = max(0.0, min(1.0, score))

        if score >= 0.75:
            status = "relevant"
        elif score >= 0.45:
            status = "maybe_relevant"
        elif score < 0.25:
            status = "irrelevant"
        else:
            status = "needs_review"

        if status in ("relevant", "maybe_relevant"):
            next_action = "fetch_detail"
        elif status == "needs_review":
            next_action = "review"
        else:
            next_action = "discard"

        entry.update({
            "relevance_status": status,
            "relevance_score": score,
            "city_match": city_match,
            "industry_match": industry_match,
            "role_signal_match": role_signal_match,
            "relevance_reasons": relevance_reasons,
            "rejection_reasons": rejection_reasons,
            "recommended_next_action": next_action,
        })
        enriched.append(entry)

    return enriched
