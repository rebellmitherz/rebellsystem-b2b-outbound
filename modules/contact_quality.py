"""Central contact quality guardrails for safe personalization.

The deterministic, offline-only contact-quality logic stays as before.
Optional opt-in: ``LINKEDIN_SERP_RESOLVE`` triggers a DuckDuckGo SERP lookup
via :mod:`modules.linkedin_resolver` when no public LinkedIn URL is known yet.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Modul-lokaler Counter für SERP-Calls pro Prozess-Lauf (Rate-Limit-Schutz)
_LINKEDIN_SERP_CALLS_THIS_RUN = 0


def _linkedin_serp_resolve_enabled() -> bool:
    raw = (os.environ.get("LINKEDIN_SERP_RESOLVE") or "1").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _linkedin_serp_max_per_run() -> int:
    try:
        return max(0, int((os.environ.get("LINKEDIN_SERP_MAX_PER_RUN") or "60").strip()))
    except ValueError:
        return 60


def reset_linkedin_serp_counter() -> None:
    """Reset des SERP-Call-Counters (z.B. zu Beginn eines neuen Mining-Runs)."""
    global _LINKEDIN_SERP_CALLS_THIS_RUN
    _LINKEDIN_SERP_CALLS_THIS_RUN = 0

LINKEDIN_RESOLUTION_STATUSES = frozenset({
    "not_checked",
    "search_link_only",
    "verified_company",
    "likely_person",
    "review",
    "rejected",
})

_TITLE_PREFIXES = frozenset({"dr", "prof", "dipl", "ing", "mba", "msc", "bsc", "herr", "frau"})

_STOP_WORDS = (
    "handelsregister",
    "registergericht",
    "amtsgericht",
    "umsatzsteuer",
    "steuernummer",
    "ust-id",
    "ust id",
    "telefon",
    "tel.",
    "tel ",
    "e-mail",
    "email",
    "datenschutz",
    "impressum",
    "haftung",
    "vertretungsberechtigt",
    "verantwortlich",
)

_BAD_CONTACT_TOKENS = frozenset({
    "ihre",
    "ihr",
    "sein",
    "seine",
    "unser",
    "unsere",
    "der",
    "die",
    "das",
    "wir",
    "sie",
    "tel",
    "telefon",
    "fax",
    "mail",
    "email",
    "e-mail",
    "info",
    "kontakt",
    "office",
    "team",
    "support",
    "service",
    "sekretariat",
    "zentrale",
    "handelsregister",
    "gesellschafter",
    "register",
    "registergericht",
    "amtsgericht",
    "umsatzsteuer",
    "steuernummer",
    "geschaeftsfuehrer",
    "geschaeftsfuehrung",
    "inh",
    "ust",
    "ust-id",
    "impressum",
    "datenschutz",
    "haftung",
    "copyright",
    "geschäftsführer",
    "geschaeftsfuehrer",
    "geschäftsführung",
    "geschaeftsfuehrung",
    "inhaber",
    "inhaberin",
    "inhaltlich",
    "werbung",
    "deutschland",
    "ceo",
    "founder",
    "gründer",
    "gruender",
    "gmbh",
    "ug",
    "ag",
    "kg",
    "ohg",
    "gbr",
    "mbh",
    "str",
    "str.",
    "straße",
    "strasse",
    "luisenstr",
    "fohlenhofstr",
    "rudolfstr",
    "weg",
    "platz",
    "allee",
    "energiekosten",
    "energieverbrauch",
    "handelsregisternummer",
    "urheberrecht",
    "marketing",
    "redaktion",
    "rd",
    "avenue",
    "st",
})

_BAD_SUBSTRINGS = (
    "handelsreg",
    "register",
    "gericht",
    "inhalt",
    "luisenstr",
    "fohlenhofstr",
    "rudolfstr",
    "umsatzsteuer",
    "steuernummer",
    "datenschutz",
    "impressum",
    "energiekosten",
    "energieverbrauch",
    "telefon",
    "webseite",
    "website",
)

_LEGAL_ENTITY_RE = re.compile(
    r"\b(gmbh|ug(?:\s*\(haftungsbeschraenkt\))?|ag|kg|ohg|gbr|se|e\.?\s*k\.?)\b",
    re.I,
)

_ROLE_ONLY_RE = re.compile(
    r"^\s*(geschaeftsfuehrer|geschäftsführer|geschäftsführung|geschaeftsfuehrung|"
    r"inhaber|inhaberin|ceo|founder|gründer|gruender|owner|managing director)\s*$",
    re.I,
)

_FEMALE_FIRST_NAMES = frozenset({
    "anna", "anja", "julia", "sandra", "petra", "nicole", "melanie", "stefanie",
    "katharina", "sabine", "claudia", "lisa", "laura", "lena", "marie", "maria",
    "michaela", "nina", "sarah", "sara", "sophie", "tanja", "verena",
})

_MALE_FIRST_NAMES = frozenset({
    "alexander", "andreas", "benjamin", "bernd", "christian", "daniel", "david",
    "dennis", "dirk", "dominik", "fabian", "felix", "florian", "frank", "georg",
    "hans", "hendrik", "henrik", "jan", "joachim", "jochen", "johannes", "jonas",
    "joerg", "jörg", "julian", "juergen", "jürgen", "kai", "karl", "klaus",
    "lars", "lukas", "manfred", "marc", "marcel", "marco", "mario", "mark",
    "markus", "martin", "matthias", "maximilian", "michael", "moritz", "nico",
    "niklas", "norbert", "oliver", "patrick", "paul", "peter", "philipp",
    "rainer", "ralf", "rene", "robert", "roland", "rolf", "sebastian", "simon",
    "stefan", "steffen", "sven", "thomas", "tim", "timo", "tobias", "tom",
    "torsten", "thorsten", "uli", "ulrich", "uwe", "viktor", "werner", "wolfgang",
})


def _ascii_fold(s: str) -> str:
    return (
        (s or "")
        .replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
        .replace("ß", "ss")
    )


def _tokens(s: str) -> list[str]:
    return [t.strip(" ,.;:-_()[]{}") for t in re.split(r"\s+", s or "") if t.strip()]


def block_bad_contact_tokens(name: str, *, city_hints: set[str] | None = None) -> list[str]:
    """Return deterministic flags explaining why a contact string is unsafe."""
    raw = " ".join((name or "").split()).strip()
    flags: list[str] = []
    if not raw:
        return ["empty"]
    low = raw.casefold()
    folded = _ascii_fold(low)
    toks = _tokens(low)
    tokset = {t.rstrip(".") for t in toks}
    cityset = {c.casefold().strip() for c in (city_hints or set()) if c}

    if "@" in raw or "://" in raw:
        flags.append("contains_email_or_url")
    if any(ch.isdigit() for ch in raw):
        flags.append("contains_digit")
    if _ROLE_ONLY_RE.match(raw):
        flags.append("role_without_name")
    if _LEGAL_ENTITY_RE.search(raw):
        flags.append("legal_entity_token")
    bad_hits = sorted(t for t in tokset if t in _BAD_CONTACT_TOKENS)
    if bad_hits:
        flags.append("bad_token:" + ",".join(bad_hits[:5]))
    keyword_hits = sorted(
        term
        for term in (
            "geschaeftsfuehrer",
            "geschäftsführer",
            "geschaeftsfuehrung",
            "geschäftsführung",
            "inhaber",
            "inhaberin",
            "gesellschafter",
            "inhaltlich",
            "luisenstr",
            "fohlenhofstr",
            "rudolfstr",
            "werbung",
            "deutschland",
            "urheberrecht",
            "copyright",
            "datenschutz",
            "impressum",
            "redaktion",
            "marketing",
        )
        if term in folded
    )
    if keyword_hits:
        flags.append("bad_keyword:" + ",".join(keyword_hits[:5]))
    if any(b in folded for b in _BAD_SUBSTRINGS):
        flags.append("bad_substring")
    if cityset and tokset.intersection(cityset):
        flags.append("city_token_as_name")
    if len(raw) > 80:
        flags.append("too_long")
    return flags


def normalize_person_name(name: str) -> str:
    """Clean a raw person candidate without making unsafe guesses."""
    s = " ".join((name or "").replace("\xa0", " ").split()).strip(" ,.;:-")
    if not s:
        return ""
    low = s.casefold()
    cut_at = len(s)
    for stop in _STOP_WORDS:
        m = re.search(r"(?<![a-zäöüß])" + re.escape(stop.casefold()), low)
        if m:
            cut_at = min(cut_at, m.start())
    s = s[:cut_at].strip(" ,.;:-")
    parts = _tokens(s)
    while parts and parts[0].rstrip(".").casefold() in _TITLE_PREFIXES:
        parts = parts[1:]
    return " ".join(parts[:4]).strip()


def is_valid_person_name(name: str, *, city_hints: set[str] | None = None) -> bool:
    """True only for a plausible natural person safe for personalization."""
    clean = normalize_person_name(name)
    if not clean:
        return False
    if block_bad_contact_tokens(clean, city_hints=city_hints):
        return False
    parts = _tokens(clean)
    if len(parts) < 2 or len(parts) > 4:
        return False
    for part in parts:
        low = part.rstrip(".").casefold()
        if low in {"von", "van", "de", "der", "di", "del"}:
            continue
        # allow apostrophes in names (e.g. O'Brien)
        letters_only = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", part)
        if len(letters_only) < 2:
            return False
        if not part[0].isupper():
            return False
    return True


def classify_contact_quality(lead: dict[str, Any] | None) -> dict[str, Any]:
    """Classify person safety and return additive fields for leads/exports."""
    lead = lead or {}
    city_hints = _city_hints_from_lead(lead)
    candidates = [
        lead.get("contact_person_clean"),
        lead.get("contact_full_name"),
        lead.get("managing_director"),
        lead.get("contact_person"),
        lead.get("owner_name"),
    ]
    raw_used = ""
    flags: list[str] = []
    for raw in candidates:
        raw_text = str(raw or "")
        raw_flags = block_bad_contact_tokens(raw_text, city_hints=city_hints)
        cand = normalize_person_name(raw_text)
        if not cand:
            continue
        cand_flags = raw_flags or block_bad_contact_tokens(cand, city_hints=city_hints)
        if is_valid_person_name(cand, city_hints=city_hints):
            if raw_flags:
                raw_used = raw_used or cand
                flags.extend(raw_flags)
                continue
            return {
                "person_quality": "valid",
                "contact_quality_reason": "valid_person_name",
                "safe_contact_person": cand,
                "safe_salutation": _safe_salutation_for(cand),
                "bad_contact_flags": "",
            }
        raw_used = raw_used or cand
        flags.extend(cand_flags or ["invalid_shape"])

    reason = "missing_person" if not raw_used else "invalid_person_name"
    return {
        "person_quality": "missing" if not raw_used else "invalid",
        "contact_quality_reason": reason,
        "safe_contact_person": "",
        "safe_salutation": "Guten Tag,",
        "bad_contact_flags": ";".join(sorted(set(flags))),
    }


def should_use_personal_salutation(lead_or_name: dict[str, Any] | str | None) -> bool:
    if isinstance(lead_or_name, dict):
        return bool(classify_contact_quality(lead_or_name).get("safe_contact_person"))
    return is_valid_person_name(str(lead_or_name or ""))


def safe_salutation_for(lead_or_name: dict[str, Any] | str | None) -> str:
    if isinstance(lead_or_name, dict):
        return str(classify_contact_quality(lead_or_name).get("safe_salutation") or "Guten Tag,")
    clean = normalize_person_name(str(lead_or_name or ""))
    return _safe_salutation_for(clean) if is_valid_person_name(clean) else "Guten Tag,"


def apply_contact_quality_fields(lead: dict[str, Any]) -> dict[str, Any]:
    """Mutate a lead with additive safety fields and neutralize unsafe person fields."""
    cq = classify_contact_quality(lead)
    lead.update({
        "person_quality": cq["person_quality"],
        "contact_quality_reason": cq["contact_quality_reason"],
        "safe_salutation": cq["safe_salutation"],
        "bad_contact_flags": cq["bad_contact_flags"],
    })
    safe_person = str(cq.get("safe_contact_person") or "")
    if safe_person:
        lead.setdefault("contact_person_clean", safe_person)
    else:
        for field in ("managing_director", "contact_full_name", "contact_person"):
            raw = str(lead.get(field) or "").strip()
            if raw and not is_valid_person_name(raw, city_hints=_city_hints_from_lead(lead)):
                lead[field] = ""
        if (lead.get("contact_person_quality") or "") not in ("valid", "good"):
            lead["contact_person_quality"] = "missing"
    lead["contact_artifact_risk"] = _classify_contact_artifact_risk(lead, cq)
    lead.update(build_linkedin_resolution_fields(lead))
    return lead


# Marker fuer harte CMS-/UI-/Rolle-/Impressums-Fragmente in bad_contact_flags.
# Praezise: nur wenn klar ein Artefakt ist, geben wir "high" zurueck — sonst
# bleibt der Lead in der Pipeline und wird per Decision-Layer ggf. zurueckgestuft.
_HIGH_ARTIFACT_FLAG_MARKERS = (
    "role_without_name",
    "legal_entity_token",
    "bad_keyword:",
    "bad_substring",
    "contains_email_or_url",
    "contains_digit",
    "city_token_as_name",
)


def _classify_contact_artifact_risk(lead: dict[str, Any], cq: dict[str, Any]) -> str:
    """Liefert low/medium/high — wird vom Decision-Layer ausgelesen.

    high   = klare Artefakt-Spur (Rolle/Adresse/UI/Register-Fragment); niemals
             personalisiert mailen.
    medium = Person fehlt oder formal ungueltig, kein eindeutiges Artefakt.
    low    = saubere Person erkannt.
    """
    if (cq.get("person_quality") or "") == "valid" and (cq.get("safe_contact_person") or "").strip():
        return "low"
    flags = str(cq.get("bad_contact_flags") or "")
    if any(m in flags for m in _HIGH_ARTIFACT_FLAG_MARKERS):
        return "high"
    if (cq.get("person_quality") or "") == "invalid":
        return "medium"
    if any((lead.get(f) or "").strip() for f in ("managing_director", "contact_full_name", "contact_person")):
        return "medium"
    return "low"


def build_linkedin_resolution_fields(lead: dict[str, Any] | None) -> dict[str, Any]:
    """Prepare LinkedIn resolver fields from existing public links/search links only."""
    lead = lead or {}
    company_url = _linkedin_company_url(
        lead.get("linkedin_company_url_verified") or lead.get("linkedin_company_url") or ""
    )
    person_url = _linkedin_person_url(lead.get("linkedin_person_url") or "")
    search_url = str(lead.get("linkedin_search_url") or lead.get("google_linkedin_url") or "").strip()
    has_person = bool(classify_contact_quality(lead).get("safe_contact_person"))

    status = "not_checked"
    try:
        confidence = float(lead.get("linkedin_match_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    reason = "no_linkedin_lookup_performed"
    if person_url:
        status = "likely_person"
        confidence = max(confidence, 0.8)
        reason = str(lead.get("linkedin_match_reason") or "existing_public_person_url")
    elif company_url:
        status = "verified_company"
        confidence = max(confidence, 0.75)
        reason = str(lead.get("linkedin_match_reason") or "existing_public_company_url")
    elif search_url:
        status = "search_link_only"
        confidence = 0.35 if has_person else 0.2
        reason = "search_url_prepared_no_serp_resolution"

    # Optionaler SERP-Lookup: nur wenn aktiviert UND noch keine echte URL vorhanden.
    # Per Default an, kann via LINKEDIN_SERP_RESOLVE=0 abgeschaltet werden.
    if (
        status in ("search_link_only", "not_checked")
        and not person_url
        and not company_url
        and _linkedin_serp_resolve_enabled()
    ):
        global _LINKEDIN_SERP_CALLS_THIS_RUN
        if _LINKEDIN_SERP_CALLS_THIS_RUN < _linkedin_serp_max_per_run():
            company_name = str(lead.get("company_name_clean") or lead.get("company_name") or "").strip()
            person_name = str(lead.get("contact_person_clean") or lead.get("contact_full_name") or "").strip()
            website = str(lead.get("website") or "").strip()
            city = str(lead.get("city") or "").strip()
            if company_name or website:
                _LINKEDIN_SERP_CALLS_THIS_RUN += 1
                try:
                    from modules.linkedin_resolver import resolve_linkedin_via_ddg

                    serp = resolve_linkedin_via_ddg(
                        company_name=company_name,
                        person_name=person_name,
                        website=website,
                        city=city,
                    )
                    serp_fields = serp.as_fields()
                    serp_status = str(serp_fields.get("linkedin_resolution_status") or "").strip()
                    serp_person = str(serp_fields.get("linkedin_person_url") or "").strip()
                    serp_company = str(serp_fields.get("linkedin_company_url_verified") or "").strip()
                    if serp_status in ("likely_person", "verified_company") and (serp_person or serp_company):
                        person_url = serp_person or person_url
                        company_url = serp_company or company_url
                        status = serp_status
                        try:
                            confidence = max(confidence, float(serp_fields.get("linkedin_match_confidence") or 0.0))
                        except (TypeError, ValueError):
                            pass
                        reason = str(serp_fields.get("linkedin_match_reason") or reason) + "_via_ddg"
                    elif serp_status == "review":
                        reason = str(serp_fields.get("linkedin_match_reason") or reason) + "_review_via_ddg"
                except Exception as exc:  # noqa: BLE001 - resolver darf Pipeline nie killen
                    logger.warning("[contact_quality] linkedin SERP lookup failed: %s", exc)
        else:
            reason = "serp_skipped_max_per_run_reached"

    return {
        "linkedin_person_url": person_url,
        "linkedin_company_url_verified": company_url,
        "linkedin_match_confidence": confidence,
        "linkedin_match_reason": reason,
        "linkedin_resolution_status": status,
    }


def _safe_salutation_for(clean_name: str) -> str:
    parts = _tokens(clean_name)
    if len(parts) < 2:
        return "Guten Tag,"
    first = parts[0].rstrip(".")
    last = parts[-1].rstrip(".")
    first_low = _ascii_fold(first.casefold())
    if first_low in _FEMALE_FIRST_NAMES:
        return f"Guten Tag Frau {last},"
    if first_low in _MALE_FIRST_NAMES:
        return f"Guten Tag Herr {last},"
    # Fallback: use last name without gender prefix
    return f"Guten Tag {last},"


def gender_known_for_first_name(first_name: str) -> bool:
    """True nur, wenn Vorname eindeutig einer Anrede zugeordnet werden kann."""
    if not first_name:
        return False
    first_low = _ascii_fold(str(first_name).strip().rstrip(".").casefold())
    return first_low in _FEMALE_FIRST_NAMES or first_low in _MALE_FIRST_NAMES


def _city_hints_from_lead(lead: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("city", "city_detected", "city_display", "city_raw", "search_city", "target_city"):
        raw = str(lead.get(key) or "").casefold()
        for part in re.split(r"[\s\-/,()]+", raw):
            part = part.strip()
            if len(part) >= 3:
                out.add(part)
    return out


def _linkedin_company_url(url: str) -> str:
    u = str(url or "").strip().split("?")[0].split("#")[0].rstrip("/")
    if not u:
        return ""
    try:
        host = (urlparse(u).netloc or "").casefold()
    except ValueError:
        return ""
    if "linkedin.com" in host and "/company/" in u.casefold():
        return u
    return ""


def _linkedin_person_url(url: str) -> str:
    u = str(url or "").strip().split("?")[0].split("#")[0].rstrip("/")
    if not u:
        return ""
    try:
        host = (urlparse(u).netloc or "").casefold()
    except ValueError:
        return ""
    if "linkedin.com" in host and "/in/" in u.casefold():
        return u
    return ""
