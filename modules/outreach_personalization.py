from __future__ import annotations

import re
from typing import Any

_ROLE_HINT_RE = re.compile(r"\b(geschäftsführer|geschaeftsfuehrer|inhaber|ceo|founder)\b", re.I)
_AGENCY_HINT_RE = re.compile(r"\b(agentur|agency|marketingagentur|werbeagentur|digitalagentur)\b", re.I)
_CITY_CLEAN_RE = re.compile(r"\s+")
_INTENT_CLEAN_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _company_value(lead: dict[str, Any] | None) -> str:
    if not lead:
        return ""
    for key in ("company_safe", "company_name_clean", "company_name", "company_name_cleaned"):
        val = _text(lead.get(key))
        if val:
            return val
    return ""


def _role_value(lead: dict[str, Any] | None) -> str:
    if not lead:
        return ""
    for key in ("contact_role", "contact_title", "role", "job_title", "position"):
        val = _text(lead.get(key))
        if val:
            return val
    return ""


def _industry_value(lead: dict[str, Any] | None) -> str:
    if not lead:
        return ""
    for key in ("industry", "recommended_sales_angle", "why_this_is_a_good_client_now", "money_reason"):
        val = _text(lead.get(key))
        if val:
            return val
    return ""


def _city_value(lead: dict[str, Any] | None) -> str:
    if not lead:
        return ""
    for key in ("city", "company_city", "target_city", "search_city"):
        val = _text(lead.get(key))
        if val:
            return val
    return ""


def _intent_value(lead: dict[str, Any] | None) -> str:
    if not lead:
        return ""
    for key in ("intent_signal_text", "intent_signal_type"):
        val = _text(lead.get(key))
        if val:
            return val
    return ""


def _short_intent_excerpt(text: str, limit: int = 12) -> str:
    cleaned = _URL_RE.sub("", _text(text))
    cleaned = cleaned.replace("\n", " ").strip(" ,;:.-")
    if not cleaned:
        return ""
    parts = cleaned.split()
    if len(parts) > limit:
        cleaned = " ".join(parts[:limit]).rstrip(" ,;:.-")
    if len(cleaned) > 130:
        cleaned = cleaned[:130].rstrip(" ,;:.-")
    return cleaned


def build_personalization_context(lead: dict | None) -> dict:
    source = dict(lead or {})
    company = _company_value(source)
    role = _role_value(source)
    industry = _industry_value(source)
    city = _city_value(source)
    intent = _intent_value(source)
    website = _text(source.get("website"))
    website_domain = _text(source.get("website_domain"))
    contact_name = _text(source.get("contact_name") or source.get("contact_full_name"))
    contact_full_name = _text(source.get("contact_full_name") or source.get("contact_name"))
    company_domain = _text(source.get("company_domain") or website_domain)
    email_domain = _text(source.get("email_domain"))
    is_freemail = bool(_text(source.get("is_freemail")).lower() in {"1", "true", "yes", "y"})
    email_domain_match = bool(_text(source.get("email_domain_match")).lower() in {"1", "true", "yes", "y"})
    email_source_url = _text(source.get("contact_source_url"))
    email_source_type = _text(source.get("email_source_type")) or "unknown"
    email_source_verified = bool(_text(source.get("email_source_verified")).lower() in {"1", "true", "yes", "y"})
    company_safe = _text(source.get("company_safe"))

    return {
        "company_name": company,
        "company_name_clean": _text(source.get("company_name_clean")),
        "company_safe": company_safe,
        "contact_name": contact_name,
        "contact_full_name": contact_full_name,
        "contact_role": role,
        "city": city,
        "website": website,
        "website_domain": website_domain,
        "company_domain": company_domain,
        "email_domain": email_domain,
        "is_freemail": is_freemail,
        "email_domain_match": email_domain_match,
        "email_source_url": email_source_url,
        "email_source_type": email_source_type or "unknown",
        "email_source_verified": email_source_verified,
        "industry": industry,
        "recommended_sales_angle": _text(source.get("recommended_sales_angle")),
        "money_reason": _text(source.get("money_reason")),
        "why_this_is_a_good_client_now": _text(source.get("why_this_is_a_good_client_now")),
        "intent_signal_text": intent,
        "intent_signal_type": _text(source.get("intent_signal_type")),
        "linkedin_url": _text(source.get("linkedin_url")),
        "linkedin_profile_url": _text(source.get("linkedin_profile_url")),
    }


def build_personalization_hook(lead: dict | None) -> str:
    context = build_personalization_context(lead)
    parts: list[str] = []

    company = context.get("company_name") or context.get("company_safe") or ""
    industry = _lower(context.get("industry"))
    sales_angle = _lower(context.get("recommended_sales_angle"))
    money_reason = _lower(context.get("money_reason"))
    why_now = _lower(context.get("why_this_is_a_good_client_now"))
    company_is_agency = bool(company and _AGENCY_HINT_RE.search(company.lower()))
    if company_is_agency:
        parts.append(f"Ich habe gesehen, dass Sie mit {company} als Agentur sichtbar auftreten und B2B-Leistungen anbieten.")
    elif company and (sales_angle or money_reason):
        parts.append(f"Ich bin bei der Recherche auf {company} gestoßen und würde meine Frage deshalb bewusst konkret stellen.")

    role = context.get("contact_role") or ""
    if role and _ROLE_HINT_RE.search(role):
        parts.append(f"Da Sie als {role} sichtbar sind, wäre meine Frage eher strategisch als operativ.")

    city = context.get("city") or ""
    if company and city:
        parts.append(f"Mir ist aufgefallen, dass {company} lokal in {city} sichtbar ist.")

    intent = _short_intent_excerpt(context.get("intent_signal_text") or "")
    if intent:
        parts.append(f"Mir ist bei der öffentlichen Recherche ein mögliches Wachstumssignal aufgefallen: {intent}.")

    if not parts:
        return ""
    return " ".join(parts[:1])


def personalization_quality(context: dict) -> str:
    ctx = context or {}
    score = 0

    company = _text(ctx.get("company_name") or ctx.get("company_safe"))
    industry = _lower(ctx.get("industry"))
    role = _lower(ctx.get("contact_role"))
    city = _text(ctx.get("city"))
    intent = _text(ctx.get("intent_signal_text"))

    if company:
        score += 1
    if industry and _AGENCY_HINT_RE.search(industry):
        score += 1
    if role and _ROLE_HINT_RE.search(role):
        score += 1
    if city:
        score += 1
    if intent:
        score += 1

    if score >= 4:
        return "high"
    if score >= 2:
        return "medium"
    if score == 1:
        return "low"
    return "none"
