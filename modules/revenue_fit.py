"""
Umsatz-/Offer-Fit (3000-EUR-Einstiegs-Logik) und ready_to_send — regelbasiert, kein Versand, keine API.

ready_to_send breit: echte B2B-Firma = E-Mail + belastbare Website, ohne Ueberfilterung (Geo, Freemail, ICP).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_LMH = ("low", "medium", "high")
_READY = ("yes", "review", "no",)

_PLACEHOLDER_EMAIL_DOMAINS = (
    "example.com", "example.org", "example.net", "example.invalid", "test.com", "invalid", "localhost",
)
_DISPOSABLE_HOST_HINTS = (
    "yopmail", "mailinator", "guerrillamail", "trbvm", "10minutemail", "throwaway",
    "tempmail", "temp-mail", "dropmail", "mohmal", "sharklasers",
)
_SOCIAL_OR_JUNK_HOSTS = (
    "facebook.com", "instagram.com", "twitter.com", "tiktok.com", "m.facebook.com",
)
_DEMO_TOKENS_IN_NAME = (
    "testfirma", "test-firma", "platzhalter", "muster gmbh", "demo gmbh", "demo ag",
    "beispiel gmbh", "beispielfirma", "fiktive",
)
_OFFICIAL_EMAIL_SOURCE_TYPES = frozenset({"official_website", "impressum", "contact_page", "privacy_page", "footer"})
_PORTAL_SOURCE_HINTS = (
    "stepstone", "indeed", "join.com", "stellenanzeigen", "linkedin.com", "xing.com",
    "kununu", "glassdoor", "jobware", "workwise", "kimeta", "meinestadt",
)


def _lmh_from_score(n: int) -> str:
    if n >= 67:
        return "high"
    if n >= 40:
        return "medium"
    return "low"


def _ext_email(email: str) -> bool:
    e = (email or "").lower()
    for d in (
        "gmail.com", "googlemail.com", "gmx.de", "gmx.net", "web.de", "yahoo.",
        "hotmail.com", "outlook.com", "live.de", "proton",
    ):
        if d in e or e.endswith("@" + d):
            return True
    return False


def _solo_signals(lead: dict) -> bool:
    lf = (lead.get("legal_form") or "").lower()
    if "e.k" in lf or "einzel" in lf or "freiberuf" in lf or "gbr" in lf:
        return True
    name = f"{lead.get('company_name', '')} {lead.get('company_name_clean', '')}".lower()
    if any(x in name for x in ("freelance", "freiberuf", "Einzelunternehmer")):
        return True
    return False


def _inbound_funnel_strong(lead: dict) -> bool:
    t = " ".join([
        lead.get("description", "") or "",
        " ".join(lead.get("website_quality_signals", []) or []),
        lead.get("notes", "") or "",
    ]).lower()
    if any(
        w in t
        for w in ("calendly", "termin online buchen", "demo buchen", "hubspot", "salesforce", "kostenlose beratung buchen")
    ):
        return True
    return False


def _host_from_url(url: str) -> str:
    u = (url or "").strip()
    if u and "://" not in u:
        u = "https://" + u
    try:
        h = (urlparse(u).netloc or "").lower()
        if h.endswith(":443") or h.endswith(":80"):
            h = h.rsplit(":", 1)[0]
        return h.replace("www.", "")
    except Exception:
        return ""


def _registrable_domain(host: str) -> str:
    parts = [part for part in (host or "").lower().split(".") if part]
    if len(parts) < 2:
        return (host or "").lower().strip()
    return ".".join(parts[-2:])


def _email_domain(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e:
        return ""
    return e.rsplit("@", 1)[1].strip()


def _source_type_from_lead(lead: dict) -> str:
    existing = str(lead.get("email_source_type") or "").strip().lower()
    if existing:
        return existing
    source_url = str(lead.get("email_source_url") or lead.get("contact_source_url") or "").strip().lower()
    if not source_url:
        return "unknown"
    if any(hint in source_url for hint in _PORTAL_SOURCE_HINTS):
        return "portal"
    if "google." in source_url or "duckduckgo." in source_url or "bing." in source_url:
        return "search_snippet"
    if "impressum" in source_url or "imprint" in source_url:
        return "impressum"
    if "kontakt" in source_url or "contact" in source_url:
        return "contact_page"
    if "privacy" in source_url or "datenschutz" in source_url:
        return "privacy_page"
    host = _host_from_url(source_url)
    website_host = _host_from_url(str(lead.get("website") or ""))
    if host and website_host and _registrable_domain(host) == _registrable_domain(website_host):
        path = (urlparse(source_url).path or "").strip().strip("/").lower()
        if not path or path in {"index.html", "index.php", "home"}:
            return "official_website"
        return "official_website"
    return "unknown"


def _source_verified(lead: dict, source_type: str) -> bool:
    explicit = lead.get("email_source_verified")
    if explicit in (True, False):
        return bool(explicit)
    source_url = str(lead.get("email_source_url") or lead.get("contact_source_url") or "").strip()
    website = str(lead.get("website") or "").strip()
    if not source_url or not website:
        return False
    if source_type not in _OFFICIAL_EMAIL_SOURCE_TYPES:
        return False
    return _registrable_domain(_host_from_url(source_url)) == _registrable_domain(_host_from_url(website))


def _ready_to_send_source_ok(source_type: str, source_verified: bool) -> bool:
    return bool(source_verified and source_type in {"official_website", "impressum", "contact_page"})


def evaluate_email_quality_gate(lead: dict) -> dict[str, Any]:
    email = str(lead.get("email") or "").strip()
    company_domain = _host_from_url(str(lead.get("website") or ""))
    email_domain = str(lead.get("email_domain") or _email_domain(email)).strip().lower()
    is_freemail = bool(lead.get("external_provider_email")) or _ext_email(email)
    source_url = str(lead.get("email_source_url") or lead.get("contact_source_url") or "").strip()
    source_type = _source_type_from_lead(lead)
    source_verified = _source_verified(lead, source_type)
    email_domain_match = bool(lead.get("email_domain_match"))
    email_type = str(lead.get("email_type") or "").strip()
    localpart = email.split("@", 1)[0].strip().lower() if "@" in email else ""
    role_localparts = {
        "info", "kontakt", "anfrage", "redaktion", "erfolg", "office", "mail",
        "hello", "team", "service", "support", "sales", "vertrieb", "marketing",
        "presse", "jobs", "karriere", "hr", "buchhaltung", "rechnung", "accounting",
    }
    has_name = bool((lead.get("contact_full_name") or lead.get("managing_director") or lead.get("decision_maker_name") or "").strip())
    has_phone = bool((lead.get("phone") or "").strip())
    firm_ok = bool(str(lead.get("website") or "").strip() and str(lead.get("company_name") or "").strip())
    suspicious = bool(lead.get("suspicious_email_domain"))
    fake_or_invalid = (
        _is_disposable_or_placeholder_email(email)
        or str(lead.get("email_invalid") or "").strip().lower() in {"yes", "true", "1", "ja"}
        or "mock" in email.lower()
        or "test" in email.lower()
        or "dummy" in email.lower()
    )
    generic = email_type == "generische E-Mail" or localpart in role_localparts
    reasons: list[str] = []

    if not email:
        rank = "D"
        reasons.append("missing_email")
    elif fake_or_invalid or suspicious:
        rank = "D"
        reasons.append("invalid_or_risky_email")
    elif generic:
        rank = "C"
        reasons.append("generic_mailbox")
    elif is_freemail and _ready_to_send_source_ok(source_type, source_verified) and firm_ok and (has_phone or has_name):
        rank = "B+"
        reasons.append("verified_freemail_on_official_source")
    elif is_freemail:
        rank = "C"
        reasons.append("freemail_without_official_source")
    elif (not is_freemail) and email_domain_match and has_name and not suspicious:
        rank = "A"
        reasons.append("personal_domain_matched_email")
    elif firm_ok and (source_verified or email_domain_match or has_phone):
        rank = "B"
        reasons.append("plausible_email_with_partial_verification")
    else:
        rank = "C"
        reasons.append("email_requires_manual_review")

    if is_freemail:
        reasons.append("freemail")
    if source_verified:
        reasons.append(f"source:{source_type}")
    elif source_url:
        reasons.append(f"source_unverified:{source_type}")

    block_reason = ""
    if rank == "D":
        block_reason = reasons[0] if reasons else "invalid_email_quality"
    elif is_freemail and not _ready_to_send_source_ok(source_type, source_verified):
        block_reason = "unverified_freemail_source"
    elif rank not in {"A", "B+"}:
        block_reason = "email_quality_review_required"

    return {
        "email_domain": email_domain,
        "company_domain": company_domain,
        "email_source_url": source_url,
        "email_source_type": source_type,
        "email_source_verified": bool(source_verified),
        "is_freemail": bool(is_freemail and email),
        "email_quality_rank": rank,
        "email_quality_reason": "; ".join(dict.fromkeys(reasons))[:500],
        "ready_to_send_block_reason": block_reason,
        "verified_freemail": bool(is_freemail and _ready_to_send_source_ok(source_type, source_verified)),
    }


def _is_disposable_or_placeholder_email(email: str) -> bool:
    e = (email or "").strip().lower()
    if "@" not in e:
        return not e
    dom = e.split("@")[-1]
    for d in _PLACEHOLDER_EMAIL_DOMAINS:
        if dom == d or dom.endswith("." + d):
            return True
    for h in _DISPOSABLE_HOST_HINTS:
        if h in dom:
            return True
    return False


def _is_placeholder_corporate_website(website: str) -> bool:
    """Nicht die Firmenwebseite (Sozial-only, example.localhost, example.com-Hosts)."""
    h = _host_from_url(website)
    if not h:
        return True
    if h in ("localhost", "127.0.0.1") or h.startswith("127."):
        return True
    if (
        "example.com" in h or h.endswith(".example.com") or "example.org" in h
        or "example.invalid" in h or h.endswith(".example.invalid") or "test.invalid" in h
    ):
        return True
    for s in _SOCIAL_OR_JUNK_HOSTS:
        if h == s or h.endswith("." + s):
            return True
    return False


def _is_demo_synthetic_lead(lead: dict) -> bool:
    """Harte Demo-Platzhalter, keine echten Kunden (Generator/Fixtures)."""
    name = f"{lead.get('company_name', '')} {lead.get('company_name_clean', '')}"
    notes = f"{lead.get('notes', '')} {lead.get('description', '')}"
    wq = " ".join(lead.get("website_quality_signals", []) or [])
    nl, notes_l = name.lower(), notes.lower()
    if (lead.get("industry_group") or "").strip().lower() == "demo":
        return True
    est = (lead.get("estimated_company_size") or "").lower()
    if "demo" in est and "nicht real" in est:
        return True
    if "[demo]" in nl or "(demo)" in nl:
        return True
    for tok in _DEMO_TOKENS_IN_NAME:
        if tok in nl:
            return True
    if (lead.get("lead_status", "") or "").strip() in ("Demo",):
        return True
    if "demo datensatz" in notes_l or "demo-datensatz" in notes_l:
        return True
    if "fiktive firma" in notes_l or ("keine echten" in notes_l and "personen" in notes_l):
        return True
    if "DEMO-DATENSATZ" in (lead.get("notes", "") or "") or "DEMO-DATENSATZ" in notes:
        return True
    if "demo" in wq.lower() and "datensatz" in notes_l:
        return True
    if "demo-datensatz" in wq.lower():
        return True
    return False


def _hard_scrap_or_directory(lead: dict) -> bool:
    """Harte Ausschluesse: Verzeichnis-/Stellen-Rest, Platzhalter — nicht: nur Freemail oder schwaches Geo.
    'verzeichnis' nur in klaren Muster, nicht in beliebiger Erwaehnung (kein Wort-False-Positive in normalen Begruendungen)."""
    w = (lead.get("weak_lead_reason") or "") + " " + (lead.get("disqualification_reason") or "")
    w = w.lower()
    if any(
        x in w
        for x in (
            "branchenportal",
            "gelbe seiten",
            "reiner suchtreffer",
            "rein verzeichnis",
            "nur stellenanzeig",
            "nur stellenanzeigen",
            "reines branchenverzeichnis",
            "reiner verzeichnis",
        )
    ):
        return True
    if "reiner" in w and "suchtreffer" in w:
        return True
    if re.search(
        r"\b(yellow|local)\s*map|kostenlos\.\s*eintrag|yelp-",
        w,
    ):
        return True
    n = (lead.get("company_name", "") or "").lower()
    if re.search(
        r"\b(demo|beispiel|test)[-_]?firma\b|\bplatzhalter-?firma\b",
        n,
    ):
        return True
    if "example.com" in (lead.get("email") or "").lower():
        return True
    if _is_demo_synthetic_lead(lead):
        return True
    if _is_disposable_or_placeholder_email(lead.get("email") or ""):
        return True
    return False


def _directory_or_garbage(lead: dict) -> bool:
    """Nur harte Muelleimer-Signale (fuer Score-Penalty und harte Sperre)."""
    if _is_placeholder_corporate_website(lead.get("website") or ""):
        return True
    return _hard_scrap_or_directory(lead)


def compute_revenue_outreach_extras(lead: dict, meta: dict) -> dict[str, Any]:
    """
    Setzt revenue_fit_*, ready_to_send*, money/hotlist-Hilfsfelder, Outreach-Stubs.
    Voraussetzung: Lead ist bereits angereichert (Kontakt, Geo, outreach_readiness, ...).
    """
    out: dict[str, Any] = {
        "revenue_fit_score": 0,
        "revenue_fit_reason": "",
        "retainer_potential": "low",
        "outbound_service_fit": "low",
        "decision_maker_reachability": "low",
        "likely_to_buy_outbound": "low",
        "ready_to_send": "no",
        "ready_to_send_reason": "",
        "ready_to_send_block_reason": "",
        "money_reason": "",
        "why_this_is_a_good_client_now": "",
        "recommended_sales_angle": "",
        "estimated_close_potential": "low",
        "outreach_stage": lead.get("outreach_stage") or "new",
        "last_contacted_at": lead.get("last_contacted_at") or "",
        "next_followup_at": lead.get("next_followup_at") or "",
        "reply_status": lead.get("reply_status") or "none",
        "lead_temperature": lead.get("lead_temperature") or "cold",
    }

    oread = (lead.get("outreach_readiness") or "").strip()
    c_status = (lead.get("customer_status") or "").strip()
    score = int(lead.get("contact_quality_score", 0) or 0)
    cp = int(lead.get("client_potential_score", 0) or 0)
    buy = int(lead.get("buying_signal_score", 0) or 0)
    outb = int(lead.get("outbound_need_score", 0) or 0)
    st = (lead.get("lead_strength") or "").strip().lower()
    has_email = bool((lead.get("email") or "").strip())
    has_phone = bool((lead.get("phone") or "").strip())
    has_dm = bool((lead.get("managing_director") or lead.get("contact_full_name") or "").strip())
    gmc = (lead.get("geo_match_type") or "").strip()
    is_main = bool(lead.get("is_main_domain"))
    is_prem = bool(lead.get("is_premium_url"))
    susp = bool(lead.get("suspicious_email_domain")) or _ext_email(lead.get("email") or "")
    is_generic_email = (lead.get("email_type") or "") == "generische E-Mail"
    email_quality = evaluate_email_quality_gate(lead)
    out.update(email_quality)
    email_quality_rank = str(email_quality.get("email_quality_rank") or "")
    email_source_type = str(email_quality.get("email_source_type") or "unknown")
    email_source_verified = bool(email_quality.get("email_source_verified"))
    freemail_verified = bool(email_quality.get("verified_freemail"))

    # Store computed flags for downstream visibility
    out["has_phone"] = has_phone
    out["is_generic_email"] = is_generic_email

    pos = 18
    neg = 0
    rparts: list[str] = []

    if lead.get("industry") and lead.get("industry") != "Allgemein":
        pos += 8
        rparts.append("Branche im Profil")
    if has_dm:
        pos += 14
        rparts.append("Entscheider/Ansprechpartner sichtbar")
    if has_email and has_phone:
        pos += 10
        rparts.append("E-Mail und Telefon")
    elif has_email:
        pos += 5
    if is_main and is_prem:
        pos += 8
        rparts.append("Echte Firmenwebsite (Domain)")
    if lead.get("legal_form") in ("GmbH", "AG", "GmbH & Co. KG", "UG (haftungsbeschränkt)", "UG"):
        pos += 8
        rparts.append("Rechtsform Mittelstand-tauglich")
    for sig in (lead.get("sales_opportunity_signals") or []) + (lead.get("industry_buy_signals") or []):
        if any(
            w in (sig or "").lower()
            for w in ("neu", "wachs", "kund", "vertrieb", "akqui", "termin", "berat", "b2b")
        ):
            pos += 2
    if _solo_signals(lead):
        neg += 2
        rparts.append("eher Einzelunternehmer/Kleinstruktur (kein harter No)")
    if _inbound_funnel_strong(lead) and outb < 45:
        neg += 2
        rparts.append("starker Inbound/Terminflow sichtbar (leichter ICP-Hinweis)")
    if _directory_or_garbage(lead):
        neg += 22
    if gmc == "search_result_only":
        neg += 1
    if st == "reject":
        neg += 40
    elif st in ("weak", "review"):
        neg += 3

    rfs = max(0, min(100, pos - neg + (buy + outb) // 15))
    out["revenue_fit_score"] = rfs

    out["retainer_potential"] = _lmh_from_score(
        rfs // 2 + (10 if lead.get("legal_form") in ("GmbH", "AG") else 0) + min(buy, 30) // 3
    )
    nfor = int(lead.get("need_for_outbound_score", 0) or 0)
    out["outbound_service_fit"] = _lmh_from_score((outb + nfor) // 2)

    dmr = 20
    if has_dm:
        dmr += 50
    if has_email and not susp:
        dmr += 20
    if has_phone:
        dmr += 15
    if (lead.get("email_type") or "") == "generische E-Mail":
        dmr -= 8
    out["decision_maker_reachability"] = _lmh_from_score(dmr)

    _opp = 25 if oread in ("ready_now", "use_forwarding", "call_first") else 0
    if not _opp and has_email and st not in ("reject",):
        _opp = 10
    buy_like = (buy * 2 + rfs + _opp) // 3
    out["likely_to_buy_outbound"] = _lmh_from_score(buy_like)

    out["revenue_fit_reason"] = (
        f"regelbasiert {rfs}/100. " + ("; ".join(rparts) if rparts else "Standard-B2B-Signale")
    )[:500]

    # --- ready_to_send: NUR persoenliche E-Mail + Telefon + echte Website ---
    disq = (lead.get("disqualification_reason") or "").strip()
    hard_no = (
        oread == "do_not_contact"
        or c_status == "Nicht kontaktieren"
        or st == "reject"
        or disq
        or not has_email
        or not has_phone
        or not _firm_ok(lead)
        or _hard_scrap_or_directory(lead)
        or email_quality_rank == "D"
    )
    if hard_no:
        out["ready_to_send"] = "no"
        if oread == "do_not_contact":
            out["ready_to_send_reason"] = (lead.get("readiness_reason") or "Kein verwertbarer Kanal / Ausschluss.")[:500]
        elif not has_email:
            out["ready_to_send_reason"] = "Keine E-Mail fuer Erstkontakt."
        elif not has_phone:
            out["ready_to_send_reason"] = "Keine Telefonnummer gefunden."
        elif email_quality_rank == "D":
            out["ready_to_send_reason"] = "E-Mail-Qualitaet D: fake/mock/junk/ungueltig oder harter Vertrauenskonflikt."
            out["ready_to_send_block_reason"] = str(email_quality.get("ready_to_send_block_reason") or "email_quality_d")
        elif not _firm_ok(lead):
            out["ready_to_send_reason"] = "Keine belastbare Firmen-Website (keine reine Sozial-URL, kein Platzhalter-Host)."
        elif disq:
            out["ready_to_send_reason"] = (f"Regelbasierte Disqualifikation: {disq}"[:500])
        else:
            out["ready_to_send_reason"] = "Harter Ausschluss (MueLL/Demo/Verzeichnis) — nicht geeignet."
    elif email_quality_rank in ("A", "B+"):
        out["ready_to_send"] = "yes"
        if email_quality_rank == "B+" and freemail_verified:
            out["ready_to_send_reason"] = (
                f"Verified Freemail: persoenliche Freemail auf offizieller Quelle ({email_source_type}) + Telefon + Website — fuer Outreach-Sequenz freigegeben."
            )[:500]
        else:
            out["ready_to_send_reason"] = (
                "Echtfirma: persoenliche E-Mail + Telefon + Website — fuer Outreach-Sequenz freigegeben."
            )[:500]
        out["ready_to_send_block_reason"] = ""
    else:
        out["ready_to_send"] = "review"
        if is_generic_email:
            out["ready_to_send_reason"] = "Generische Mail vorhanden: erst pruefen oder telefonisch anbinden, dann ggf. Follow-up."
            out["ready_to_send_block_reason"] = "generic_mailbox_review"
        elif bool(email_quality.get("is_freemail")) and not email_source_verified:
            out["ready_to_send_reason"] = "Freemail ohne verifizierte offizielle Quelle: vor Versand Quelle/Firmenbezug pruefen."
            out["ready_to_send_block_reason"] = "unverified_freemail_source"
        else:
            out["ready_to_send_reason"] = "E-Mail plausibel, aber nicht stark genug fuer automatische Freigabe; vor Versand pruefen."
            out["ready_to_send_block_reason"] = str(email_quality.get("ready_to_send_block_reason") or "email_quality_review_required")

    # Sales-Hilfsfelder
    offer = (lead.get("passendes_kaltakquise_angebot") or lead.get("recommended_offer") or "")[:200]
    out["why_this_is_a_good_client_now"] = (
        f"{(lead.get('wahrscheinlicher_bedarf') or lead.get('strongest_signal') or '')[:220]} "
        f"Fit {rfs}/100, Client-Potential {cp}."
    ).strip()[:500]
    out["money_reason"] = (
        f"Kaltakquise/Terminierung: B2B-Signale, Erreichbarkeit, Offer-Passung ({rfs}/100). "
        f"Kontakt: {'E-Mail' if has_email else ''}{' + Tel' if has_phone else ''}."
    )[:400]
    out["recommended_sales_angle"] = (offer or lead.get("empfohlener_pitch", "") or "")[:400] or "Kurztest mit planbaren qualifizierten Erstgespraechen."
    ecp = "high" if rfs >= 62 and out["likely_to_buy_outbound"] == "high" else (
        "medium" if rfs >= 45 else "low"
    )
    out["estimated_close_potential"] = ecp

    return out


def _firm_ok(lead: dict) -> bool:
    w = (lead.get("website") or "").strip()
    if not w or "linkedin.com" in w.lower():
        return False
    if _is_placeholder_corporate_website(w):
        return False
    return True


def classify_pipeline_ingest(lead: dict) -> str | None:
    """
    Sync-Filter: None = Lead darf in die Outreach-Pipeline gemerged werden.
    Sonst Kurz-Code fuer Statistik/CSV (kein Merge dieses Datensatzes).
    Reihenfolge: struktureller Muell (Demo/Platzhalter) vor ready_to_send,
    damit Demo-Runden als fake_demo gezaehlt werden, nicht nur not_ready.
    """
    em = (lead.get("email") or "").strip()
    if not em:
        return "no_email"
    el = em.lower()
    if "@" not in el or el.count("@") != 1:
        return "invalid_email"

    if _is_disposable_or_placeholder_email(em):
        return "disposable_email"

    if _is_demo_synthetic_lead(lead):
        return "fake_demo"

    w = (lead.get("website") or "").strip()
    if not w or _is_placeholder_corporate_website(w) or not _firm_ok(lead):
        return "placeholder_website"

    if _hard_scrap_or_directory(lead):
        return "fake_demo"

    rts = (lead.get("ready_to_send") or "").strip().lower()
    if rts == "no":
        return "not_ready"
    if rts not in ("yes", "review"):
        return "not_ready"

    return None


def pipeline_entry_as_lead(entry: dict) -> dict:
    """Minimaler Lead-Dict aus Pipeline-Zeile fuer classify_pipeline_ingest / Cleanup."""
    return {
        "email": entry.get("email", ""),
        "website": entry.get("website", ""),
        "company_name": entry.get("company_name", ""),
        "company_name_clean": entry.get("company_name_clean", ""),
        "ready_to_send": entry.get("ready_to_send", ""),
        "notes": entry.get("notes", ""),
        "description": entry.get("description", ""),
        "website_quality_signals": entry.get("website_quality_signals") or [],
        "lead_status": entry.get("lead_status", ""),
        "industry_group": entry.get("industry_group", ""),
        "estimated_company_size": entry.get("estimated_company_size", ""),
        "weak_lead_reason": entry.get("weak_lead_reason", ""),
        "disqualification_reason": entry.get("disqualification_reason", ""),
    }


def money_leads_sort_key(lead: dict) -> tuple:
    """Fuer Top-10 JSON und Hotlist-Sort: Umsatz zuerst."""
    rfs = int(lead.get("revenue_fit_score", 0) or 0)
    cp = int(lead.get("client_potential_score", 0) or 0)
    rts = lead.get("ready_to_send", "")
    r_bonus = 30 if rts == "yes" else (10 if rts == "review" else 0)
    return (rfs + r_bonus, cp, int(lead.get("contact_quality_score", 0) or 0))
