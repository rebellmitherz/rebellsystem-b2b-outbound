"""
Persistente Outreach-Pipeline, Versand nur bei echtem SMTP-Erfolg, Dedup, Follow-ups.

- send_email.py wird per importlib geladen (mehrzeilige Bodies); Erfolg nur bei result['ok'] is True.
- Kein Status 'sent' ohne erfolgreichen Versand.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import logging
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from config import (
    AUTOPILOT_REPLY_CONFIG_JSON,
    FOLLOWUP_DAYS_1,
    FOLLOWUP_DAYS_2,
    OUTREACH_ENTERPRISE_MIN_EMPLOYEES,
    OUTREACH_MAX_SEND_DELAY_SEC,
    OUTREACH_MAX_SENDS_PER_RUN,
    OUTREACH_MIN_SEND_DELAY_SEC,
    OUTREACH_PIPELINE_JSON,
    OUTREACH_ROTATION_JSON,
    OUTREACH_SENT_LOG_JSON,
    OUTPUT_DIR,
    REPLY_EVENTS_JSON,
    REPLY_QUEUE_JSON,
    SEND_EMAIL_SCRIPT_DEFAULT,
    get_industry_profile,
)
from modules.revenue_fit import classify_pipeline_ingest, pipeline_entry_as_lead, evaluate_email_quality_gate
import modules.reply_intelligence as reply_intel
import modules.reply_processor as reply_proc
from modules import expose_generator

logger = logging.getLogger(__name__)

PIPE_VERSION = 1
STAGES = frozenset({
    "new", "drafted", "ready", "sent", "followup_1", "followup_2", "replied", "won", "lost",
    "warm", "hot",
})

# Bereits kontaktierte Stufen nicht per Cleanup loeschen (Historie / keine Zerstoerung echter Sends)
_PROTECTED_OUTREACH_STAGES = frozenset({"sent", "followup_1", "followup_2", "replied", "won", "hot", "warm"})


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _norm_company(s: str) -> str:
    t = (s or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s*[|:\-–—].*$", "", t)
    for prefix in ("it systemhaus ", "seo agentur ", "marketing agentur ", "b2b marketing agentur "):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
    return t[:200]


# Preview/Versand-Vorbereitung: generische Postfächer (kein persönlicher Ansprechpartner)
_BLOCKED_EMAIL_LOCAL_PARTS = frozenset({
    "shop", "store", "vertrieb-shop", "ecommerce", "e-commerce",
    "support", "helpdesk", "help", "kundenservice", "service",
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster",
    "newsletter", "news", "mailing", "marketing-allgemein", "presse", "pr", "jobs", "career", "bewerbung",
    "abuse", "security", "datenschutz", "privacy",
})
# info@ nur als letzte Option — in der Preview nicht anzeigen (Qualität vor Masse)
_INFO_FALLBACK_LOCAL_PARTS = frozenset({"info", "information", "infodesk", "office", "zentrale"})

_ENTERPRISE_NAME_HINTS = (
    "datagroup", "hays", "adecco", "randstad", "manpower", "michael page", "robert half",
    "telekom", "t-systems", "siemens", "sap", "allianz", "deutsche bank", "adesso",
    "mckinsey", "bcg", "boston consulting", "accenture", "capgemini", "bearingpoint",
    "atos", "cognizant", "infosys", "wipro", "ibm", "amazon", "microsoft", "google",
    "ernst young", "ey ", " pwc", "pwc ", "deloitte", "kpmg",
)

_PORTAL_OR_MAGAZINE_RE = re.compile(
    r"(branchenbuch|gelbe\s*seiten|das\s+örtliche|11880|\byelp\b|cylex|"
    r"hotfrog|herold\.at|wlw\.|wer-liefert-was|europages|kompass\.com|"
    r"jobportal|stellenmarkt|only\s*jobs|stepstone\.de/|xing\.com/jobs|"
    r"online-?magazin|fachmagazin|verlagsgruppe|mediengruppe|"
    r"immobilien.*(magazin|portal|nachrichten)|"
    r"\|\s*news\b|\|\s*ratgeber|ratgeber\s*\||"
    r"chip\.de|focus\.de|stern\.de|spiegel\.de|welt\.de|zeit\.de|handelsblatt|faz\.net|sueddeutsche)",
    re.I,
)


def _email_local_lower(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e:
        return ""
    return e.split("@", 1)[0]


def _is_blocked_generic_email_local(email: str) -> bool:
    loc = _email_local_lower(email)
    if not loc:
        return True
    if loc in _BLOCKED_EMAIL_LOCAL_PARTS:
        return True
    if loc in _INFO_FALLBACK_LOCAL_PARTS:
        return True
    if loc.startswith("no-reply") or loc.startswith("noreply") or loc.startswith("donotreply"):
        return True
    return False


def _preview_block_enterprise_or_portal(company: str, website: str) -> bool:
    c = _norm_company(company)
    if any(h in c for h in _ENTERPRISE_NAME_HINTS):
        return True
    hay = f"{company} {website}".strip()
    if hay and _PORTAL_OR_MAGAZINE_RE.search(hay):
        return True
    return False


def _enterprise_name_matches_send_block(company: str, website: str) -> bool:
    """Bekannte Konzern-/Enterprise-Namen (Firma oder Host, keine Portale)."""
    c = _norm_company(company)
    if any(h in c for h in _ENTERPRISE_NAME_HINTS):
        return True
    host = _domain_from_url(website).lower()
    if host:
        host_compact = host.replace("-", "")
        for h in _ENTERPRISE_NAME_HINTS:
            hs = h.replace(" ", "")
            if len(hs) >= 4 and (hs in host_compact or h in host):
                return True
    return False


def _size_label_implies_over_employee_threshold(label: str, threshold: int) -> bool:
    """Leitet aus estimated_company_size o.ä. ab, ob eher >threshold MA."""
    t = (label or "").strip().lower()
    if not t:
        return False
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", t)
    if m:
        hi = max(int(m.group(1)), int(m.group(2)))
        return hi > threshold
    m = re.search(r"(\d+)\s*\+", t)
    if m:
        return int(m.group(1)) > threshold
    m = re.search(r"(?:>|≥)\s*(\d+)", t)
    if m:
        return int(m.group(1)) >= threshold
    m = re.search(r"(\d+)\s*(?:ma|mitarbeiter|angestellte|employees?)\b", t)
    if m:
        return int(m.group(1)) > threshold
    return False


def _entry_enterprise_send_blocked(entry: dict, lead: dict | None) -> tuple[bool, str]:
    if entry.get("outreach_send_bypass_filters"):
        return False, ""
    if _enterprise_name_matches_send_block(entry.get("company_name") or "", entry.get("website") or ""):
        return True, "enterprise_filtered"
    sz = (
        (entry.get("estimated_company_size") or "").strip()
        or ((lead.get("estimated_company_size") or "").strip() if lead else "")
    )
    if _size_label_implies_over_employee_threshold(sz, OUTREACH_ENTERPRISE_MIN_EMPLOYEES):
        return True, "enterprise_filtered"
    return False, ""


def _email_domain_for_mx(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e:
        return ""
    return e.rsplit("@", 1)[-1].strip().rstrip(".")


def _domain_has_inbound_mail_dns(domain: str) -> bool:
    """
    MX mit auflösbarem Ziel, sonst A/AAAA der Domain (RFC-5321-Fallback).
    Reduziert 5xx wegen nicht existierender Mail-Domains.
    """
    d = (domain or "").strip().lower().rstrip(".")
    if not d or "." not in d:
        return False
    try:
        import dns.resolver
    except ImportError:
        try:
            socket.getaddrinfo(d, None, type=socket.SOCK_STREAM)
            return True
        except OSError:
            return False

    res = dns.resolver.Resolver()
    res.timeout = 2.5
    res.lifetime = 6.0
    try:
        answers = res.resolve(d, "MX")
        for r in answers:
            host = str(r.exchange).rstrip(".").lower()
            if not host or host == ".":
                continue
            for qt in ("A", "AAAA"):
                try:
                    res.resolve(host, qt)
                    return True
                except Exception:
                    continue
    except Exception:
        pass
    for qt in ("A", "AAAA"):
        try:
            res.resolve(d, qt)
            return True
        except Exception:
            continue
    return False


def _recipient_domain_mail_ready(email: str) -> tuple[bool, str]:
    dom = _email_domain_for_mx(email)
    if not dom:
        return False, "invalid_domain"
    if not _domain_has_inbound_mail_dns(dom):
        return False, "invalid_domain"
    return True, ""


def _pause_between_outreach_sends() -> None:
    lo = min(OUTREACH_MIN_SEND_DELAY_SEC, OUTREACH_MAX_SEND_DELAY_SEC)
    hi = max(OUTREACH_MIN_SEND_DELAY_SEC, OUTREACH_MAX_SEND_DELAY_SEC)
    time.sleep(random.uniform(lo, hi))


def _effective_outreach_send_cap(requested: int) -> int:
    if requested <= 0:
        return OUTREACH_MAX_SENDS_PER_RUN
    return min(requested, OUTREACH_MAX_SENDS_PER_RUN)


def _domain_brand_name(website: str) -> str:
    h = _domain_from_url(website)
    if not h:
        return ""
    part = h.split(".")[0]
    if part in ("www", "mail", "smtp"):
        return ""
    return re.sub(r"[-_]+", " ", part).strip().title() if part else ""


_COMPANY_NAME_JUNK_TOKENS = frozenset({
    "offizielle", "impressum", "kontakt", "willkommen", "startseite", "homepage",
    "datenschutz", "agb", "blog", "news", "karriere", "über", "uns", "ueber",
    "training", "marktführer", "marktfuehrer",
})

# Nur Kontakt-/Anrede-Logik (nicht auf Firmennamen anwenden — z. B. „…-Beratung GmbH“).
_CONTACT_NAME_JUNK_TOKENS = _COMPANY_NAME_JUNK_TOKENS | frozenset({"beratung"})


def _impressum_company_plausible(name: str) -> bool:
    if len(name) < 5 or len(name) > 120:
        return False
    low = name.lower().strip()
    if low in _COMPANY_NAME_JUNK_TOKENS or low.split()[0] in _COMPANY_NAME_JUNK_TOKENS:
        return False
    if re.search(r"(GmbH|AG|KG|UG|SE|gGmbH|e\.?\s*K\.?\.?)", name, re.I):
        return True
    parts = name.split()
    if len(parts) >= 2:
        return not low.startswith("http")
    return 4 <= len(name) <= 42 and name[0].isalnum()


def _company_from_impressum_blob(text: str) -> str:
    if not text or len(text) < 30:
        return ""
    patterns = (
        r"wird von\s+([A-Za-zÀ-ž0-9\.\-\s&'’]+?(?:GmbH|AG|KG|UG|SE|gGmbH))\s+betrieben",
        r"§\s*5\s*TMG\s*:\s*([A-Za-zÀ-ž0-9\.\-\s&'’]+?(?:GmbH|AG|KG|UG|SE|e\.?\s*K\.?\.?|gGmbH))",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            name = re.sub(r"\s+", " ", m.group(1).strip())
            if _impressum_company_plausible(name):
                return name
    return ""


def _strip_seo_company_title(name: str) -> str:
    t = re.sub(r"\s+", " ", (name or "").strip())
    if " / " in t and len(t) > 35:
        t = t.split(" / ")[0].strip()
    t = re.sub(r"\s*[|–—]\s*.+$", "", t)
    t = re.sub(r"\s+-\s+.+$", "", t)
    if len(t) > 35 and ":" in t:
        t = t.split(":", 1)[0].strip()
    t = re.sub(r"\s+#\s+.+$", "", t)
    t = t.strip(" -–—|")
    t = re.sub(r"\.\.\.\s*$", "", t).strip()
    return t


def _trim_display_company(name: str) -> str:
    t = (name or "").strip()
    if len(t) > 80:
        t = t[:80].rsplit(" ", 1)[0] + "…"
    return t


def canonical_firma_for_outreach(entry: dict, lead: dict | None) -> str:
    """
    Echter Firmenname: Impressum (TMG) vor Legal/Clean/Domain — kein SEO-Titel.
    """
    for src in (lead, entry):
        if not src:
            continue
        ic = _company_from_impressum_blob((src.get("impressum_info") or "").strip())
        if ic:
            return _trim_display_company(ic)
    for src in (lead, entry):
        if not src:
            continue
        for k in ("legal_name", "registered_company_name", "handelsname"):
            v = (src.get(k) or "").strip()
            if v:
                t = _strip_seo_company_title(v)
                if t and _impressum_company_plausible(t):
                    return _trim_display_company(t)
    for src in (lead, entry):
        if not src:
            continue
        cc = (src.get("company_name_clean") or "").strip()
        if cc:
            t = _strip_seo_company_title(cc)
            toks = {x.lower().rstrip("-–—") for x in t.split()}
            if t and not _COMPANY_NAME_JUNK_TOKENS.intersection(toks):
                if _impressum_company_plausible(t) or (" " in t and 6 <= len(t) <= 75):
                    return _trim_display_company(t)
                if 4 <= len(t) <= 22 and t[0].isalnum():
                    return _trim_display_company(t)
    for src in (entry, lead):
        if not src:
            continue
        cn = _strip_seo_company_title((src.get("company_name") or "").strip())
        if not cn:
            continue
        if _impressum_company_plausible(cn):
            return _trim_display_company(cn)
        if "gmbh" in cn.lower() or " ag" in cn.lower() or cn.endswith(" AG"):
            return _trim_display_company(cn)
    web = (entry.get("website") or "") or ((lead.get("website") or "") if lead else "")
    dom = _domain_brand_name(web)
    if dom:
        return _trim_display_company(dom)
    raw = _strip_seo_company_title(
        (entry.get("company_name") or (lead.get("company_name") if lead else "") or ""),
    )
    return _trim_display_company(raw) if raw else "Ihr Unternehmen"


def _nachname_aus_kontakt(contact_name: str) -> str:
    raw = (contact_name or "").strip()
    if not raw:
        return ""
    toks = [t.strip(" ,.-–—") for t in re.split(r"\s+", raw) if t.strip()]
    if not toks:
        return ""
    if _CONTACT_NAME_JUNK_TOKENS.intersection(x.lower().rstrip("-") for x in toks):
        return ""
    if len(toks) >= 2:
        return toks[-1].rstrip("-–—")
    return ""


_TITLE_PREFIXES = frozenset({
    "dr", "prof", "med", "dipl", "ing", "mba", "msc", "bsc", "mr", "mrs", "ms",
})


def _looks_like_usable_first_name(tok: str) -> bool:
    t = (tok or "").strip()
    if not t:
        return False
    base = t.rstrip(".").strip()
    if len(base) <= 1:
        return False
    if len(base) == 1 and t.endswith("."):
        return False
    if len(base) == 2 and t.endswith("."):
        return False
    low = base.lower()
    if low in _TITLE_PREFIXES:
        return False
    if not base[0].isalpha():
        return False
    return True


_FEMALE_FIRST_HINT = frozenset({
    "anna", "alice", "alexandra", "andrea", "angela", "anja", "annette", "antje", "astrid",
    "barbara", "beate", "birgit", "brigitte", "britta", "carolin", "caroline", "catherine",
    "christine", "claudia", "cornelia", "dagmar", "daniela", "doris", "eva", "franziska",
    "gabriele", "gisela", "heike", "helena", "helga", "ilse", "ingrid", "iris", "isabel",
    "jana", "jasmin", "jennifer", "jessica", "julia", "karen", "karin", "katharina", "katja",
    "kerstin", "kirsten", "laura", "lea", "lena", "lisa", "louise", "magdalena", "manuela",
    "margarete", "maria", "marie", "marion", "martina", "melanie", "monika", "nadine",
    "nicole", "nina", "petra", "renate", "rita", "sabine", "sandra", "sarah", "sibylle",
    "silke", "silvia", "simone", "sonja", "stefanie", "stephanie", "susanne", "svenja",
    "tanja", "theresa", "ulla", "ursula", "vera", "veronika", "waltraud", "yvonne",
})


def _herr_oder_frau(vorname: str) -> str:
    """Liefert 'Herr' / 'Frau' / '' (leer = unbekannt -> neutralisieren)."""
    v = (vorname or "").strip().rstrip(".").lower()
    if v in _FEMALE_FIRST_HINT:
        return "Frau"
    try:
        from modules.contact_quality import _MALE_FIRST_NAMES
    except Exception:
        _MALE_FIRST_NAMES = frozenset()
    if v in _MALE_FIRST_NAMES:
        return "Herr"
    return ""


def _salutation_line(contact_name: str) -> str:
    """Mit erkennbarem Vornamen: Guten Tag Herr/Frau Nachname — sonst neutral Guten Tag."""
    raw = (contact_name or "").strip()
    if not raw:
        return "Guten Tag,"
    toks = [t.strip(" ,.-–—") for t in re.split(r"\s+", raw) if t.strip()]
    if not toks:
        return "Guten Tag,"
    while toks:
        head = toks[0].rstrip(".").lower()
        if head in _TITLE_PREFIXES:
            toks = toks[1:]
            continue
        break
    if not toks:
        return "Guten Tag,"
    if _CONTACT_NAME_JUNK_TOKENS.intersection(x.lower().rstrip("-") for x in toks):
        return "Guten Tag,"
    if len(toks) < 2:
        return "Guten Tag,"
    vorname, nachname = toks[0], toks[-1]
    if not _looks_like_usable_first_name(vorname) or not nachname:
        return "Guten Tag,"
    hf = _herr_oder_frau(vorname)
    if not hf:
        return "Guten Tag,"
    return f"Guten Tag {hf} {nachname},"


def _industry_mentions_it_sector(low: str) -> bool:
    """True nur bei IT als Wort/Abkürzung — nicht Teilstrings wie in „mit“."""
    if "it-dienst" in low or "it system" in low or "it-system" in low:
        return True
    return bool(re.search(r"(^|[^a-zäöüß0-9])it($|[^a-zäöüß0-9])", low, re.I))


def _branche_phrase(lead: dict | None) -> str:
    if not lead:
        return "B2B-Unternehmen"
    ind = (lead.get("industry") or "").strip()
    low = ind.lower()
    if "agentur" in low:
        return "Agenturen"
    if "beratung" in low:
        return "Beratungsunternehmen"
    if "software" in low or "saas" in low:
        return "Softwareanbietern"
    if "personal" in low or "recruit" in low:
        return "Personalberatern"
    if _industry_mentions_it_sector(low) or "dienstleister" in low:
        return "IT-Dienstleistern"
    if ind:
        return ind
    ig = (lead.get("industry_group") or "").replace("_", " ").strip()
    return ig or "B2B-Unternehmen"


def _branche_phrase_nominative(lead: dict | None) -> str:
    """Subjekt für „Die meisten … sprechen“ (Nominativ/Plural)."""
    if not lead:
        return "B2B-Anbieter"
    ind = (lead.get("industry") or "").strip()
    low = ind.lower()
    if "agentur" in low:
        return "Agenturen"
    if "beratung" in low:
        return "Beratungsunternehmen"
    if "software" in low or "saas" in low:
        return "Softwareanbieter"
    if "personal" in low or "recruit" in low:
        return "Personalberater"
    if _industry_mentions_it_sector(low) or "dienstleister" in low:
        return "IT-Dienstleister"
    if ind:
        parts = ind.split()
        if len(parts) > 4 or len(ind) > 48:
            return "B2B-Anbieter"
        return ind
    ig = (lead.get("industry_group") or "").replace("_", " ").strip()
    if ig:
        parts = ig.split()
        if len(parts) > 4 or len(ig) > 48:
            return "B2B-Anbieter"
        return ig
    return "B2B-Unternehmen"


OUTREACH_FIRST_SUBJECT = "Kurze Frage"

# Nur bei bekannter Branchenzuordnung (config INDUSTRY_PROFILES) — kein Freitext aus SEO/Description.
_OUTREACH_SERVICE_PHRASE_BY_GROUP: dict[str, str] = {
    "agenturen": "Agenturleistungen",
    "it_dienstleister": "IT-Dienstleistungen",
    "beratungen": "Beratungsleistungen",
    "immobilienmakler": "Immobilien-Dienstleistungen",
    "handwerker": "Handwerksleistungen",
    "pflegeanbieter": "Pflegeleistungen",
    "coaches": "Coaching und Beratung",
    "steuerberater": "Steuerberatung",
    "recruiting": "Personalvermittlung und Recruiting",
    "pv_solar": "PV- und Solarlösungen",
    "sicherheitsdienst": "Sicherheitsdienste",
    "fitnessstudios": "Fitness- und Trainingsangebote",
    "gastronomie": "Gastronomie",
    "arztpraxen": "Praxisleistungen",
    "reinigung": "Reinigungs- und Facility-Services",
}


def _outreach_personalization_phrase(merged: dict, lead: dict | None) -> str:
    """Nur der Leistungstext; leer wenn Branche nicht sicher zuordenbar."""
    ig = ((lead.get("industry_group") if lead else None) or merged.get("industry_group") or "").strip().lower()
    industry = ((lead.get("industry") if lead else None) or merged.get("industry") or "").strip()
    group_key = ""
    if ig in _OUTREACH_SERVICE_PHRASE_BY_GROUP:
        group_key = ig
    elif industry:
        prof = get_industry_profile(industry)
        g = (prof.get("group") if prof else "") or ""
        if g in _OUTREACH_SERVICE_PHRASE_BY_GROUP:
            group_key = g
    if not group_key:
        return ""
    return _OUTREACH_SERVICE_PHRASE_BY_GROUP[group_key]


def _outreach_personalization_block(merged: dict, lead: dict | None, *, short: bool = False) -> str:
    """Nur bei sicher erkanntem Profil; short=kompakter Satz bei Wortlimit."""
    phrase = _outreach_personalization_phrase(merged, lead)
    if not phrase:
        return ""
    if short:
        return f"Ich habe gesehen, dass Sie {phrase} anbieten.\n\n"
    return (
        f"Ich habe gesehen, dass Sie {phrase} anbieten – "
        "genau da sehen wir aktuell oft unregelmäßige Anfragen.\n\n"
    )

_LEGACY_OUTREACH_MARKERS = (
    "B2B-Recherche",
    "Viele Gruesse",
    "Kundengespraeche",
    "gestossen",
    "erklaerungsbeduerftige",
    "grundsaetzlich",
    "naechste Woche",
    "waere der Ansatz",
    "Kurze Frage zu neuen B2B-Terminen",
    "gewinnen Sie aktuell planbar",
    "Kaltakquise-Chaos",
    "wie ein kleiner Test für",
    "ist das Thema für",
    "Wenn der Zeitpunkt nicht passt",
    "kurze Nachfrage zu meiner E-Mail",
    "letzte, kurze Nachfrage",
    # Vorheriges Hardened-Template (Anbieter-/Test-Setup) — auf Filter/Connector-Text migrieren
    "Wir bauen gerade Tests",
    "ohne Streuverlust",
    "hätten Sie aktuell überhaupt Kapazität",
    "Wenn morgen 2–3 passende B2B-Anfragen",
    "Soll ich Ihnen ein konkretes Beispiel schicken",
)


def _outreach_copy_is_legacy(
    body: str,
    subject: str,
    fu1: str = "",
    fu2: str = "",
) -> bool:
    blob = f"{body or ''}\n{subject or ''}\n{fu1 or ''}\n{fu2 or ''}"
    return any(m in blob for m in _LEGACY_OUTREACH_MARKERS)


def build_hardened_outreach_messages(merged: dict, lead: dict | None) -> dict[str, str]:
    """
    Erstmail + Follow-ups: modulare, safety-first Outreach-Texte.
    """
    from modules import outreach_safety as osafe
    from modules.contact_quality import classify_contact_quality

    source = lead or merged
    company_display = canonical_firma_for_outreach(merged, lead)
    company_safe = osafe.safe_company_display_for_short_texts(company_display, source)
    contact_safe = osafe.resolve_safe_contact_name(merged, lead)
    cq = classify_contact_quality({**(lead or {}), **(merged or {}), "contact_person": contact_safe})
    if not cq.get("safe_contact_person"):
        contact_safe = ""
    mode = osafe.classify_mode_from_sources(merged, lead)
    if mode == osafe.MESSAGE_MODE_PERSON and not contact_safe:
        mode = osafe.MESSAGE_MODE_GENERIC
    if mode == osafe.MESSAGE_MODE_COMPANY and not company_safe:
        mode = osafe.MESSAGE_MODE_GENERIC

    sal_line = osafe.salutation_line_for_mode(mode, contact_safe, company_safe)
    industry_block = osafe.build_industry_context_paragraph(source)
    body = osafe.compose_hardened_email_body(
        mode=mode,
        salutation=sal_line,
        industry_block=industry_block,
        company_safe=company_safe,
    )

    if mode == osafe.MESSAGE_MODE_PERSON:
        fu1 = "Kurze Nachfrage:\nSoll ich Ihnen 1–2 konkrete Setups nennen, die zu Ihrem Haus passen?"
        fu2 = "Alles gut, wenn es gerade kein Thema ist.\nDann hake ich nicht weiter nach."
    elif mode == osafe.MESSAGE_MODE_COMPANY:
        fu1 = f"Kurze Nachfrage:\nSoll ich Ihnen 1–2 konkrete Setups für {company_safe} nennen?"
        fu2 = "Alles gut, wenn es gerade kein Thema ist.\nDann hake ich nicht weiter nach."
    else:
        fu1 = "Kurze Nachfrage:\nSoll ich Ihnen 1–2 konkrete Setups nennen, die bei ähnlichen Unternehmen funktionieren?"
        fu2 = "Alles gut, wenn es gerade kein Thema ist.\nDann hake ich nicht weiter nach."

    return {
        "first_email_subject": OUTREACH_FIRST_SUBJECT,
        "first_email_body": body,
        "followup_1_text": fu1,
        "followup_2_text": fu2,
        "company_display": company_display,
        "outreach_message_mode": mode,
        "outreach_company_safe": company_safe,
        "outreach_contact_safe": contact_safe,
    }


def build_hardened_outreach_messages_for_lead(lead: dict) -> dict[str, str]:
    """Für Mining/Export: voller Lead als Kontext."""
    pseudo = {
        "contact_name": (lead.get("managing_director") or lead.get("contact_full_name") or "").strip(),
        "company_name": (lead.get("company_name") or "").strip(),
        "website": (lead.get("website") or "").strip(),
    }
    return build_hardened_outreach_messages(pseudo, lead)


def _display_company_for_preview(entry: dict, lead: dict | None) -> str:
    return canonical_firma_for_outreach(entry, lead)


def _domain_from_url(url: str) -> str:
    try:
        u = (url or "").strip()
        if u and "://" not in u:
            u = "https://" + u
        h = urlparse(u).netloc.replace("www.", "").lower()
        if ":" in h and h.rsplit(":", 1)[-1].isdigit():
            h = h.rsplit(":", 1)[0]
        return h[:200]
    except Exception:
        return ""


def _duplicate_group_id_for_domain(website_domain: str) -> str:
    d = (website_domain or "").strip().lower() or "_"
    return hashlib.sha256(d.encode("utf-8")).hexdigest()[:16]


def _stage_rank(stage: str) -> int:
    return {
        "new": 0, "drafted": 0, "ready": 0,
        "sent": 10, "followup_1": 20, "followup_2": 30,
        "warm": 35, "replied": 40, "hot": 43, "won": 50, "lost": 50,
    }.get((stage or "new").strip(), 0)


def _find_key_by_email(by_key: dict[str, dict], em: str) -> Optional[str]:
    for k, e in by_key.items():
        if _norm_email(e.get("email", "")) == em:
            return k
    return None


def _collapse_duplicate_email_keys(by_key: dict[str, dict]) -> dict[str, dict]:
    """Legt pro E-Mail hoechstens eine Zeile an; verhindert Doppelzeilen bei Namens-/Key-Aenderungen zwischen Laeufen."""
    by_email: dict[str, list[str]] = {}
    singles: dict[str, dict] = {}
    for k, e in by_key.items():
        em = _norm_email(e.get("email", ""))
        if not em:
            singles[k] = e
            continue
        by_email.setdefault(em, []).append(k)
    out: dict[str, dict] = {**singles}
    for em, keys in by_email.items():
        if len(keys) == 1:
            k0 = keys[0]
            out[k0] = by_key[k0]
            continue
        keys_sorted = sorted(keys, key=lambda kk: -_stage_rank(by_key[kk].get("outreach_stage")))
        keep = keys_sorted[0]
        merged = by_key[keep]
        for drop in keys_sorted[1:]:
            merged = _merge_entry(merged, by_key[drop])
        out[keep] = merged
    return out


def _annotate_duplication_metadata(state: dict[str, Any]) -> None:
    """Gruppiert nach Website-Domain: gleiche Firma, mehrere Kontakte erlaubt, kein Doppel-Spam pro E-Mail-Key."""
    entries = [e for e in (state.get("entries") or []) if e.get("entry_key")]
    by_domain: dict[str, list[dict]] = {}
    for e in entries:
        d = (e.get("website_domain") or "").strip().lower() or "_no_domain_"
        by_domain.setdefault(d, []).append(e)
    for d, group in by_domain.items():
        gid = _duplicate_group_id_for_domain(d)
        n = len(group)
        for e in group:
            e["duplicate_group_id"] = gid
            e["is_same_company"] = n > 1
            e["is_same_contact"] = True
            e["same_domain_lead_count"] = n


def entry_key(lead: dict) -> str:
    """Stabiler Schluessel: E-Mail > Domain+Name."""
    em = _norm_email(lead.get("email") or "")
    dom = _domain_from_url(lead.get("website") or "")
    co = _norm_company(lead.get("company_name") or lead.get("company_name_clean") or "")
    raw = f"{em}|{dom}|{co}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _parse_dt(s: str) -> Optional[datetime]:
    if not (s or "").strip():
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").split("+")[0])
    except Exception:
        return None


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("[outreach] kann %s nicht lesen: %s", path, exc)
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _latest_leads_path() -> Path:
    p = Path(OUTPUT_DIR) / "latest" / "leads.json"
    if p.is_file():
        return p
    return Path(OUTPUT_DIR) / "latest" / "leads_full.json"


def load_pipeline_state() -> dict[str, Any]:
    data = _load_json(OUTREACH_PIPELINE_JSON, {})
    if not data:
        return {"version": PIPE_VERSION, "updated_at": "", "entries": []}
    if "entries" not in data:
        data["entries"] = []
    data["version"] = PIPE_VERSION
    return data


def save_pipeline_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    state["version"] = PIPE_VERSION
    _annotate_duplication_metadata(state)
    _save_json(OUTREACH_PIPELINE_JSON, state)
    if not OUTREACH_SENT_LOG_JSON.is_file():
        _save_json(OUTREACH_SENT_LOG_JSON, {"version": 1, "events": []})
    _export_pipeline_csv(state)
    _export_followup_due(state)
    export_hot_handoffs_files(state)
    _copy_to_output_latest()


def _copy_to_output_latest() -> None:
    latest = Path(OUTPUT_DIR) / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    for name in (
        "outreach_pipeline.json",
        "outreach_pipeline.csv",
        "sent_log.json",
        "followup_due.csv",
        "skipped_duplicates.csv",
        "skipped_invalid.csv",
        "outreach_preview.json",
        "outreach_preview.csv",
        "hot_handoffs.json",
        "hot_handoffs.csv",
        "reply_templates_preview.json",
        "autopilot_reply_config.json",
        "reply_events.json",
        "reply_queue.json",
        "expose_handoff.jsonl",
    ):
        src = Path(OUTPUT_DIR) / name
        if src.is_file():
            shutil.copy2(src, latest / name)


def _export_pipeline_csv(state: dict[str, Any]) -> None:
    path = Path(OUTPUT_DIR) / "outreach_pipeline.csv"
    rows = state.get("entries") or []
    fieldnames = [
        "entry_key", "email", "company_name", "website", "website_domain",
        "duplicate_group_id", "is_same_company", "is_same_contact", "same_domain_lead_count",
        "ready_to_send",
        "approved_for_send", "approved_at", "approved_by",
        "outreach_stage", "reply_status", "lead_temperature", "first_sent_at", "last_contacted_at",
        "next_followup_at", "sent_message_id", "do_not_resend", "last_error",
    ]
    if not rows:
        path.write_text(",".join(fieldnames) + "\n", encoding="utf-8-sig")
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for e in rows:
            w.writerow(e)


def _export_followup_due(state: dict[str, Any]) -> None:
    now = datetime.now()
    due: list[dict] = []
    for e in state.get("entries") or []:
        stg = (e.get("outreach_stage") or "").strip()
        nxt = _parse_dt(e.get("next_followup_at") or "")
        rpl = (e.get("reply_status") or "none").strip()
        if rpl != "none":
            continue
        if (e.get("inbound_last_text") or "").strip() or e.get("processed_inbound_ids"):
            continue
        if stg in ("replied", "won", "lost", "hot", "warm") or e.get("do_not_resend"):
            continue
        if nxt and nxt <= now and stg in ("sent", "followup_1"):
            due.append(e)
    p = Path(OUTPUT_DIR) / "followup_due.csv"
    fn = [
        "entry_key", "email", "company_name", "outreach_stage", "next_followup_at",
        "first_sent_at", "ready_to_send",
    ]
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fn, extrasaction="ignore")
        w.writeheader()
        for e in due:
            w.writerow({k: e.get(k, "") for k in fn})


def _append_event(event: dict[str, Any]) -> None:
    data = _load_json(OUTREACH_SENT_LOG_JSON, {"version": 1, "events": []})
    data.setdefault("events", [])
    data["events"].append(event)
    _save_json(OUTREACH_SENT_LOG_JSON, data)


def lead_to_entry(lead: dict) -> dict[str, Any]:
    ek = entry_key(lead)
    rts = (lead.get("ready_to_send") or "").strip()
    st = (lead.get("outreach_stage") or "new").strip()
    if st not in STAGES:
        st = "new"
    wd = _domain_from_url(lead.get("website") or "")
    contact = (lead.get("managing_director") or lead.get("contact_full_name") or "").strip()
    msgs = build_hardened_outreach_messages_for_lead(lead)
    return {
        "entry_key": ek,
        "email": (lead.get("email") or "").strip(),
        "company_name": (lead.get("company_name") or "").strip(),
        "contact_name": contact,
        "phone": (lead.get("phone") or "").strip(),
        "website": (lead.get("website") or "").strip(),
        "website_domain": wd,
        # Sortier-Anker: wann der Lead in die Pipeline gekommen ist.
        # Wird beim ersten Anlegen gesetzt; bei _merge_entry erhalten.
        "added_at": (lead.get("added_at") or _now_iso()),
        "duplicate_group_id": lead.get("duplicate_group_id") or _duplicate_group_id_for_domain(wd),
        "is_same_company": lead.get("is_same_company", False),
        "is_same_contact": lead.get("is_same_contact", True),
        "same_domain_lead_count": int(lead.get("same_domain_lead_count") or 1),
        "ready_to_send": rts,
        "ready_to_send_reason": (lead.get("ready_to_send_reason") or "").strip()[:800],
        "estimated_close_potential": (lead.get("estimated_close_potential") or "").strip() or "",
        "recommended_sales_angle": (lead.get("recommended_sales_angle") or "").strip()[:800],
        "money_reason": (lead.get("money_reason") or "").strip()[:800],
        "outreach_stage": st,
        "reply_status": (lead.get("reply_status") or "none").strip() or "none",
        "lead_temperature": (lead.get("lead_temperature") or "cold").strip() or "cold",
        "first_sent_at": lead.get("first_sent_at") or "",
        "last_contacted_at": lead.get("last_contacted_at") or "",
        "next_followup_at": lead.get("next_followup_at") or "",
        "sent_message_id": lead.get("sent_message_id") or "",
        "do_not_resend": bool(lead.get("do_not_resend", False)),
        "first_email_subject": msgs["first_email_subject"],
        "first_email_body": msgs["first_email_body"],
        "followup_1_text": msgs["followup_1_text"],
        "followup_2_text": msgs["followup_2_text"],
        "impressum_info": (lead.get("impressum_info") or "")[:8000],
        "company_name_clean": (lead.get("company_name_clean") or "").strip(),
        "industry": (lead.get("industry") or "").strip(),
        "industry_group": (lead.get("industry_group") or "").strip(),
        "estimated_company_size": (lead.get("estimated_company_size") or "").strip(),
        "outreach_send_bypass_filters": bool(lead.get("outreach_send_bypass_filters", False)),
        "outreach_sender_email": (lead.get("outreach_sender_email") or "").strip(),
        "last_error": (lead.get("last_error") or "").strip(),
        "approved_for_send": bool(lead.get("approved_for_send", False)),
        "approved_at": (lead.get("approved_at") or "").strip(),
        "approved_by": (lead.get("approved_by") or "").strip(),
        "approval_campaign_id": (lead.get("approval_campaign_id") or "").strip(),
        "handoff_summary": (lead.get("handoff_summary") or "").strip()[:2000],
        "handoff_next_action": (lead.get("handoff_next_action") or "").strip()[:800],
        "why_hot": (lead.get("why_hot") or "").strip()[:800],
        "handed_off_at": (lead.get("handed_off_at") or "").strip(),
        "conversation_status": (lead.get("conversation_status") or reply_proc.CONVERSATION_NEW).strip()
        or reply_proc.CONVERSATION_NEW,
        "source": (lead.get("source") or "search"),
    }


def _accum_skip(stats: dict[str, int], reason: str) -> None:
    stats["sync_skipped"] = stats.get("sync_skipped", 0) + 1
    key = f"sync_skipped_{reason}"
    stats[key] = stats.get(key, 0) + 1
    if reason in ("fake_demo", "disposable_email"):
        stats["sync_skipped_fake"] = stats.get("sync_skipped_fake", 0) + 1


def prune_pipeline_junk(state: dict[str, Any]) -> dict[str, int]:
    """
    Entfernt Demo-/Fake-/Platzhalter- und not-ready Eintraege, die noch nie versendet wurden.
    Gesendete Eintraege bleiben unangetastet.
    """
    removed = 0
    soft = 0
    kept: list[dict] = []
    for e in state.get("entries") or []:
        st = (e.get("outreach_stage") or "new").strip()
        if st in _PROTECTED_OUTREACH_STAGES:
            kept.append(e)
            continue
        reason = classify_pipeline_ingest(pipeline_entry_as_lead(e))
        if reason is None:
            kept.append(e)
            continue
        harsh = reason in (
            "fake_demo", "disposable_email", "placeholder_website",
            "no_email", "invalid_email",
        )
        if harsh or reason == "not_ready":
            removed += 1
            continue
        soft += 1
        e2 = {**e, "do_not_resend": True, "outreach_stage": "lost", "last_error": "filtered_cleanup"}
        kept.append(e2)
    state["entries"] = kept
    return {"pipeline_pruned_removed": removed, "pipeline_pruned_soft_lost": soft}


def _entry_send_precheck_ok(e: dict) -> tuple[bool, str]:
    """Zusaetzliche Sicherheit vor SMTP: keine Demo/example-Reste trotz veralteter Flags."""
    reason = classify_pipeline_ingest(pipeline_entry_as_lead(e))
    if reason is not None:
        return False, reason
    return True, ""


def _merge_entry(existing: dict, incoming: dict) -> dict:
    out = {**existing}
    st = (out.get("outreach_stage") or "new").strip()
    # Keine Ueberschreibung operativer Felder nach Versand / Handoff
    if st in ("sent", "followup_1", "followup_2", "replied", "won", "lost", "hot", "warm"):
        return out
    # Inhalte aus neuem Lauf (Freigabe-Felder bleiben aus `existing`, bis explizit approve-CLI)
    for k in (
        "company_name", "website", "website_domain", "ready_to_send",
        "ready_to_send_reason", "estimated_close_potential", "recommended_sales_angle", "money_reason",
        "contact_name", "phone",
        "impressum_info", "company_name_clean", "industry", "industry_group",
        "estimated_company_size",
        "first_email_subject", "first_email_body", "followup_1_text", "followup_2_text",
    ):
        v = incoming.get(k)
        if v:
            out[k] = v
    if not out.get("email") and incoming.get("email"):
        out["email"] = incoming["email"]
    if incoming.get("outreach_stage") in ("new", "drafted", "ready") and st == "new":
        out["outreach_stage"] = "new"
    if "outreach_send_bypass_filters" in incoming:
        out["outreach_send_bypass_filters"] = bool(incoming.get("outreach_send_bypass_filters"))
    if (incoming.get("outreach_sender_email") or "").strip():
        out["outreach_sender_email"] = str(incoming.get("outreach_sender_email") or "").strip()
    if incoming.get("source") and incoming["source"] != out.get("source"):
        out["source"] = incoming["source"]
    return out


def _leads_by_email_norm() -> dict[str, dict]:
    path = _latest_leads_path()
    leads = _load_json(path, [])
    if not isinstance(leads, list):
        return {}
    out: dict[str, dict] = {}
    for lead in leads:
        em = _norm_email(lead.get("email", ""))
        if em:
            out[em] = lead
    return out


def _enrich_entry_from_lead(entry: dict, lead: dict | None) -> dict:
    if not lead:
        return entry
    e = {**entry}
    for k in ("ready_to_send_reason", "estimated_close_potential", "recommended_sales_angle", "money_reason"):
        if not (e.get(k) or "").strip() and (lead.get(k) or "").strip():
            e[k] = str(lead.get(k) or "").strip()
    if not (e.get("contact_name") or "").strip():
        e["contact_name"] = (lead.get("managing_director") or lead.get("contact_full_name") or "").strip()
    if not (e.get("phone") or "").strip():
        e["phone"] = (lead.get("phone") or "").strip()
    if not (e.get("estimated_company_size") or "").strip() and (lead.get("estimated_company_size") or "").strip():
        e["estimated_company_size"] = str(lead.get("estimated_company_size") or "").strip()
    return e


def get_state_campaign_id(state: dict) -> str | None:
    """Liefert die aktuelle campaign_id aus dem Pipeline-State (None = legacy/unconfigured)."""
    cid = (state or {}).get("campaign_id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()
    return None


def derive_default_campaign_id(state: dict | None = None) -> str:
    """Stabiler Default, wenn keine campaign_id existiert. Bricht nicht."""
    upd = (state or {}).get("updated_at") if isinstance(state, dict) else None
    if isinstance(upd, str) and upd.strip():
        return f"campaign-{upd.strip()[:10]}"
    return f"campaign-{datetime.now().strftime('%Y-%m-%d')}"


def _entry_was_contacted_in_campaign(entry: dict, current_campaign_id: str | None) -> bool:
    """True nur, wenn dieser Eintrag in derselben campaign_id schon kontaktiert wurde.

    - current_campaign_id is None  -> legacy-Verhalten: jeder sent/followup zaehlt als kontaktiert
    - last_campaign_id fehlt       -> als 'fremde Kampagne' werten -> nicht blockieren
    - last_campaign_id == aktuell  -> blockieren
    """
    stg = (entry.get("outreach_stage") or "new").strip()
    if stg in ("new", "drafted", "ready"):
        return False
    if current_campaign_id is None:
        return True
    last_cid = (entry.get("last_campaign_id") or "").strip()
    contacted_in = entry.get("contacted_in_campaigns") or []
    if isinstance(contacted_in, list) and current_campaign_id in contacted_in:
        return True
    if last_cid and last_cid == current_campaign_id:
        return True
    return False


# Role-based / generic email prefixes that must never appear in outreach preview.
# Only affects _preview_exclusion_reason() — scoring, sync and pipeline history are untouched.
_BLOCKED_EMAIL_PREFIXES: frozenset[str] = frozenset({
    "impressum", "shop", "info", "hello", "hallo", "office", "kontakt",
    "support", "mail", "service", "noreply", "no-reply", "post", "webmaster",
    "sales", "contact", "team", "news", "newsletter", "anfrage", "bestellung",
    "feedback", "hilfe", "buchung", "reservierung", "vermietung",
})


def _blocked_email_prefix(email_lower: str) -> bool:
    """True wenn der Local-Part ein bekannter generischer/role-based Prefix ist."""
    local = email_lower.split("@")[0]
    # Normalisiere: Trennzeichen und abschliessende Ziffern entfernen
    normalized = local.replace(".", "").replace("-", "").replace("_", "").rstrip("0123456789")
    return normalized in _BLOCKED_EMAIL_PREFIXES


def _preview_exclusion_reason(e: dict, current_campaign_id: str | None = None) -> str:
    if e.get("do_not_resend"):
        return "already_contacted"
    if _entry_was_contacted_in_campaign(e, current_campaign_id):
        return "already_contacted"
    rts = (e.get("ready_to_send") or "").strip().lower()
    if rts != "yes":
        return "ready_to_send_not_yes"
    if not (e.get("company_name") or "").strip() or not (e.get("website") or "").strip():
        return "missing_required_fields"
    em = (e.get("email") or "").strip().lower()
    if not em:
        return "missing_email"
    if "@" not in em or em.count("@") != 1:
        return "invalid_email"
    if _blocked_email_prefix(em):
        return "generic_email_prefix"
    ing = classify_pipeline_ingest(pipeline_entry_as_lead(e))
    if ing == "no_email":
        return "missing_email"
    if ing in ("invalid_email", "disposable_email"):
        return "invalid_email"
    if ing in ("placeholder_website", "fake_demo"):
        return "hygiene_failed"
    if _preview_block_enterprise_or_portal(e.get("company_name") or "", e.get("website") or ""):
        return "other"
    if e.get("domain_blocked") or e.get("suspicious_email_domain"):
        return "blocked_domain"
    return ""


def _preview_eligible_entry(e: dict, current_campaign_id: str | None = None) -> bool:
    return not _preview_exclusion_reason(e, current_campaign_id)


_GENERIC_COMPANY_TOKENS = frozenset({
    "marketing", "marketingagentur", "marketingagenturen", "agentur", "agenturen",
    "online-marketing", "onlinemarketing", "performance-marketing",
    "neukundengewinnung", "neukunden", "leadgenerierung", "lead-generierung",
    "consulting", "beratung",
})

_GENERIC_COMPANY_PHRASES = (
    "neukunden durch online-marketing",
    "online-marketing gewinnen",
    "b2b marketing agentur",
    "b2b-marketing agentur",
    "marketing agentur",
    "online marketing agentur",
)


def _normalize_company_for_genericity(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\b(gmbh|ug|ag|kg|ohg|gbr|se|mbh|e\.?\s*k\.?)\b\.?", " ", s)
    s = re.sub(r"[^\wäöüß\-/&\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_generic_company_name(name: str) -> bool:
    """True wenn Firmenname zu generisch / werblich klingt (Review noetig)."""
    s = _normalize_company_for_genericity(name)
    if not s:
        return False
    if any(p in s for p in _GENERIC_COMPANY_PHRASES):
        return True
    toks = [t for t in re.split(r"[\s\-/&]+", s) if t]
    nontrivial = [t for t in toks if len(t) > 2]
    if nontrivial and all(t in _GENERIC_COMPANY_TOKENS for t in nontrivial):
        return True
    return False


def _campaign_approval_status(entry: dict, current_campaign_id: str | None) -> str:
    if not entry.get("approved_for_send"):
        return "not_approved"
    appr_cid = (entry.get("approval_campaign_id") or "").strip()
    if current_campaign_id and appr_cid and appr_cid == current_campaign_id:
        return "approved_for_this_campaign"
    if not appr_cid:
        return "approved_old_campaign"
    if current_campaign_id and appr_cid != current_campaign_id:
        return "approved_old_campaign"
    return "approved_for_this_campaign"


def _salutation_quality_status(entry: dict) -> str:
    """ok / neutralized / review."""
    sal = (entry.get("safe_salutation") or "").strip()
    contact = (entry.get("contact_name") or entry.get("safe_contact_person") or "").strip()
    body = (entry.get("first_email_body") or "")
    if not contact:
        return "neutralized" if "Guten Tag," in body else "review"
    parts = [p for p in re.split(r"\s+", contact) if p]
    if len(parts) < 2:
        return "review" if "Herr " in body or "Frau " in body else "neutralized"
    first = parts[0].rstrip(".")
    try:
        from modules.contact_quality import gender_known_for_first_name
    except Exception:
        gender_known_for_first_name = lambda _x: False  # noqa: E731
    if gender_known_for_first_name(first):
        if sal.startswith("Guten Tag Herr ") or sal.startswith("Guten Tag Frau "):
            return "ok"
        return "neutralized"
    if "Herr " in body or "Frau " in body:
        return "review"
    return "neutralized"


def _text_industry_review_reason(entry: dict, lead: dict | None) -> str:
    """Liefert review-code, wenn der Mailtext Branchen-Spezialformulierungen
    enthaelt, die zur Lead-Branche nicht passen. Sonst leerer String."""
    body = (entry.get("first_email_body") or "") + "\n" + (entry.get("followup_1_text") or "")
    blow = body.lower()
    industry = ((entry.get("industry") or (lead or {}).get("industry") or "")).strip().lower()
    industry_group = ((entry.get("industry_group") or (lead or {}).get("industry_group") or "")).strip().lower()
    is_it = "it-dienst" in industry or "it system" in industry or industry_group == "it_dienstleister"
    is_marketing = "agentur" in industry or "marketing" in industry or industry_group == "agenturen"
    has_it_phrase = (
        "it-haus" in blow or "it-häuser" in blow or "it-haeuser" in blow
        or "it-dienstleist" in blow or "it-system" in blow
    )
    if has_it_phrase and (is_marketing or not is_it):
        return "industry_text_mismatch"
    if not industry and not industry_group:
        return "industry_unknown"
    return ""


def compute_review_gate(entry: dict, lead: dict | None, current_campaign_id: str | None) -> dict:
    """Reine Review-/Sichtbarkeitslogik. Beruehrt KEINE Versandlogik."""
    reasons: list[str] = []
    review_status = "send_ready"

    if entry.get("do_not_resend"):
        return {
            "review_status": "reject",
            "review_reason": "do_not_resend",
            "campaign_approval_status": _campaign_approval_status(entry, current_campaign_id),
            "text_quality_status": "review",
            "salutation_quality_status": "review",
        }

    cas = _campaign_approval_status(entry, current_campaign_id)
    if cas == "approved_old_campaign":
        reasons.append("approval_from_other_campaign")

    stg = (entry.get("outreach_stage") or "new").strip()
    if stg in ("sent", "followup_1", "followup_2", "replied", "won", "lost"):
        reasons.append(f"old_stage:{stg}")

    sq = _salutation_quality_status(entry)
    if sq == "review":
        reasons.append("salutation_review")
    elif sq == "neutralized":
        reasons.append("salutation_neutralized")

    person_quality = (entry.get("person_quality") or "").strip().lower()
    bad_flags = (entry.get("bad_contact_flags") or "").strip()
    if person_quality in ("invalid", "missing") or bad_flags:
        reasons.append("contact_quality")

    if is_generic_company_name(entry.get("company_name") or ""):
        reasons.append("generic_company")

    text_reason = _text_industry_review_reason(entry, lead)
    text_quality_status = "review" if text_reason else "ok"
    if text_reason:
        reasons.append(f"text:{text_reason}")

    if reasons:
        review_status = "review"

    return {
        "review_status": review_status,
        "review_reason": ",".join(reasons),
        "campaign_approval_status": cas,
        "text_quality_status": text_quality_status,
        "salutation_quality_status": sq,
    }


def _suggested_action_for_preview(e: dict, gate: dict | None = None) -> str:
    if gate and gate.get("review_status") != "send_ready":
        cas = gate.get("campaign_approval_status", "not_approved")
        if cas == "approved_old_campaign":
            return "Review erforderlich (alte Freigabe — neu pruefen, dann erneut approven)."
        if "old_stage" in (gate.get("review_reason") or ""):
            return "Review erforderlich (alter Versand-Status sichtbar — keine automatische Freigabe)."
        return "Review erforderlich — siehe review_reason."
    if not e.get("approved_for_send"):
        return "Freigabe: `python mine.py --outreach approve` (optional `--approve-keys <entry_key>`)."
    return "Freigegeben — Versand: `python mine.py --outreach send --outreach-limit N`."


def _apply_hardened_outreach_copy(entry: dict, lead: dict | None) -> str:
    """
    Schreibt dieselben Betreff-/Body-/Follow-up-Texte wie die Preview in die Pipeline (in-place).
    Gibt company_display zurück (Anzeigename in Preview-Export).
    """
    merged = _enrich_entry_from_lead(dict(entry), lead)
    msgs = build_hardened_outreach_messages(merged, lead)
    entry["first_email_subject"] = msgs["first_email_subject"]
    entry["first_email_body"] = msgs["first_email_body"]
    entry["followup_1_text"] = msgs["followup_1_text"]
    entry["followup_2_text"] = msgs["followup_2_text"]
    entry["outreach_display_company"] = msgs["company_display"]
    return msgs["company_display"]


def _load_current_run_emails() -> set[str]:
    """Laedt E-Mails aus output/latest/ready_to_send.csv (aktueller Mine-Lauf).
    Gibt leeres Set zurueck wenn die Datei nicht existiert — dann kein Filter aktiv."""
    rts_csv = Path(OUTPUT_DIR) / "latest" / "ready_to_send.csv"
    if not rts_csv.is_file():
        return set()
    try:
        with open(rts_csv, newline="", encoding="utf-8-sig") as f:
            return {_norm_email(row.get("email") or "") for row in csv.DictReader(f) if row.get("email")}
    except Exception:
        return set()


def _load_current_run_map() -> dict[str, dict]:
    """Laedt ready_to_send.csv als email->row-Mapping (aktueller Mine-Lauf).
    Gibt leere Map zurueck wenn Datei nicht existiert — dann kein Mapping aktiv."""
    rts_csv = Path(OUTPUT_DIR) / "latest" / "ready_to_send.csv"
    if not rts_csv.is_file():
        return {}
    try:
        result: dict[str, dict] = {}
        with open(rts_csv, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                em = _norm_email(row.get("email") or "")
                if em:
                    result[em] = dict(row)
        return result
    except Exception:
        return {}


def run_preview(state: dict[str, Any], limit: int) -> dict[str, Any]:
    by_lead = _leads_by_email_norm()
    rows: list[dict[str, Any]] = []
    exclusion_counts: dict[str, int] = {}
    cap = limit if limit > 0 else 10**9
    current_cid = get_state_campaign_id(state)
    review_counts = {"send_ready": 0, "review": 0, "reject": 0}

    # Current-run filter + Mapping: wenn ready_to_send.csv vorhanden, nur aktuelle Leads zeigen
    # und deren Firma/Kontakt-Felder aus CSV erzwingen (Pipeline kann veraltete Daten enthalten).
    # Historische Pipeline-Eintraege werden NICHT geloescht — nur aus der Preview ausgefiltert.
    current_run_map = _load_current_run_map()
    use_current_run_filter = bool(current_run_map)

    for e in state.get("entries") or []:
        em_check = _norm_email(e.get("email", ""))
        if use_current_run_filter and em_check not in current_run_map:
            exclusion_counts["historical_not_current_run"] = (
                exclusion_counts.get("historical_not_current_run", 0) + 1
            )
            continue
        reason = _preview_exclusion_reason(e, current_cid)
        if reason:
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
            continue
        em = _norm_email(e.get("email", ""))
        crr = current_run_map.get(em) if use_current_run_filter else None  # CSV-Zeile aktueller Lauf
        lead = by_lead.get(em)
        e.update(_enrich_entry_from_lead(dict(e), lead))
        company_display = _apply_hardened_outreach_copy(e, lead)
        if len(rows) >= cap:
            continue
        enriched = _enrich_entry_from_lead(dict(e), lead)
        gate = compute_review_gate(enriched, lead, current_cid)
        review_counts[gate["review_status"]] = review_counts.get(gate["review_status"], 0) + 1
        # Firma/Kontakt aus ready_to_send.csv erzwingen — Pipeline-Eintrag kann veraltete Daten tragen.
        # Pipeline-Felder (outreach_stage, added_at, status) bleiben erhalten.
        def _crr(k: str) -> str:
            return (crr.get(k) or "").strip() if crr else ""
        try:
            score_val = int(crr.get("score") or 0) if crr and crr.get("score") else int(enriched.get("score") or 0)
        except (TypeError, ValueError):
            score_val = int(enriched.get("score") or 0)
        # Email-Quality Safety Gate: alte ready_to_send=yes-Werte aus Pipeline dürfen nicht
        # in Preview gelangen wenn aktuelle E-Mail-Qualität nur C/D ist.
        # gate_lead mit aktuellen CSV-Feldern befüllen damit persönliche Firmenmails (r.schmitt@)
        # nicht wegen fehlender Kontextfelder fälschlich abgewertet werden.
        gate_lead = dict(enriched)
        gate_lead["email"] = _crr("email") or gate_lead.get("email", "")
        gate_lead["company_name"] = _crr("company_name") or gate_lead.get("company_name", "")
        gate_lead["contact_full_name"] = (
            _crr("contact_name")
            or gate_lead.get("contact_full_name", "")
            or gate_lead.get("managing_director", "")
            or gate_lead.get("decision_maker_name", "")
        )
        gate_lead["phone"] = _crr("phone") or gate_lead.get("phone", "")
        gate_lead["website"] = _crr("website") or gate_lead.get("website", "")
        # email_domain_match ableiten wenn noch nicht gesetzt: E-Mail-Domain == Website-Domain?
        if not gate_lead.get("email_domain_match"):
            _gl_em_d = gate_lead["email"].rsplit("@", 1)[1].strip().lower() if "@" in gate_lead["email"] else ""
            _gl_site = gate_lead["website"].lower().replace("https://", "").replace("http://", "")
            if _gl_site.startswith("www."):
                _gl_site = _gl_site[4:]
            _gl_site = _gl_site.split("/")[0]
            if _gl_em_d and _gl_site:
                gate_lead["email_domain_match"] = _gl_em_d == _gl_site or _gl_site.endswith("." + _gl_em_d)
        eq_result = evaluate_email_quality_gate(gate_lead)
        eq_rank = eq_result.get("email_quality_rank", "")
        eq_block = eq_result.get("ready_to_send_block_reason") or "email_quality_review_required"
        eq_reason = eq_result.get("email_quality_reason") or eq_rank or "unbekannt"
        eq_downgrade = eq_rank not in {"A", "B+"}
        if eq_downgrade:
            preview_rts = "review"
            preview_rts_reason = f"Preview-Safety: E-Mail nicht premium-sendfähig ({eq_reason}); manuelle Prüfung erforderlich."
            preview_rts_block = eq_block
            final_review_status = gate["review_status"] if gate["review_status"] in {"review", "reject"} else "review"
            final_review_reason = gate["review_reason"] or "email_quality_review_required"
        else:
            preview_rts = enriched.get("ready_to_send", "")
            preview_rts_reason = enriched.get("ready_to_send_reason", "")
            preview_rts_block = enriched.get("ready_to_send_block_reason", "")
            final_review_status = gate["review_status"]
            final_review_reason = gate["review_reason"]
        # Contact-Safety: offensichtlich unplausible/prominente Ansprechpartner → review
        _suspicious_contact_names: frozenset[str] = frozenset({
            "pep guardiola",
        })
        _contact_raw = (
            _crr("contact_name")
            or enriched.get("contact_name", "")
            or enriched.get("contact_full_name", "")
            or enriched.get("managing_director", "")
        )
        _contact_norm = _contact_raw.strip().lower()
        if _contact_norm in _suspicious_contact_names:
            preview_rts = "review"
            preview_rts_reason = "Preview-Safety: Ansprechpartner wirkt unplausibel/prominent; manuelle Prüfung erforderlich."
            preview_rts_block = "suspicious_contact_name"
            if final_review_status != "reject":
                final_review_status = "review"
            if final_review_status != "reject":
                final_review_reason = "suspicious_contact_name"
        rows.append({
            "entry_key": enriched.get("entry_key", ""),
            "company_name": _crr("company_name") or company_display,
            "contact_name": _crr("contact_name") or enriched.get("contact_name", ""),
            "email": _crr("email") or enriched.get("email", ""),
            "phone": _crr("phone") or enriched.get("phone", ""),
            "website": _crr("website") or enriched.get("website", ""),
            "score": score_val,
            "ready_to_send": preview_rts,
            "ready_to_send_reason": preview_rts_reason,
            "ready_to_send_block_reason": preview_rts_block,
            "first_email_subject": _crr("first_email_subject") or (e.get("first_email_subject") or "").strip(),
            "first_email_body": _crr("first_email_body") or (e.get("first_email_body") or "").strip(),
            "followup_1": (e.get("followup_1_text") or "").strip(),
            "followup_2": (e.get("followup_2_text") or "").strip(),
            "estimated_close_potential": enriched.get("estimated_close_potential", ""),
            "recommended_sales_angle": _crr("recommended_sales_angle") or enriched.get("recommended_sales_angle", ""),
            "money_reason": enriched.get("money_reason", ""),
            "approved_for_send": bool(enriched.get("approved_for_send")),
            "approved_at": enriched.get("approved_at", ""),
            "approval_campaign_id": (enriched.get("approval_campaign_id") or "").strip(),
            "outreach_stage": enriched.get("outreach_stage", ""),
            "review_status": final_review_status,
            "review_reason": final_review_reason,
            "campaign_approval_status": gate["campaign_approval_status"],
            "text_quality_status": gate["text_quality_status"],
            "salutation_quality_status": gate["salutation_quality_status"],
            "suggested_action": _suggested_action_for_preview(enriched, gate),
            "added_at": (e.get("added_at") or "")[:10],
            "is_current_run": True,
        })
    for e in state.get("entries") or []:
        if not _outreach_copy_is_legacy(
            e.get("first_email_body", ""),
            e.get("first_email_subject", ""),
            e.get("followup_1_text", ""),
            e.get("followup_2_text", ""),
        ):
            continue
        em = _norm_email(e.get("email", ""))
        lead = by_lead.get(em) if em else None
        e.update(_enrich_entry_from_lead(dict(e), lead))
        _apply_hardened_outreach_copy(e, lead)
    save_pipeline_state(state)
    payload = {
        "generated_at": _now_iso(),
        "count": len(rows),
        "rows": rows,
        "diagnostics": {
            "total_entries": len(state.get("entries") or []),
            "preview_count": len(rows),
            "excluded_by_reason": exclusion_counts,
            "campaign_id": current_cid,
            "review_counts": review_counts,
        },
    }
    pj = Path(OUTPUT_DIR) / "outreach_preview.json"
    _save_json(pj, payload)
    pc = Path(OUTPUT_DIR) / "outreach_preview.csv"
    fn = [
        "entry_key", "company_name", "contact_name", "email", "phone", "website",
        "ready_to_send", "ready_to_send_reason",
        "first_email_subject", "first_email_body", "followup_1", "followup_2",
        "estimated_close_potential", "recommended_sales_angle", "money_reason",
        "approved_for_send", "approved_at", "approval_campaign_id", "outreach_stage",
        "review_status", "review_reason",
        "campaign_approval_status", "text_quality_status", "salutation_quality_status",
        "suggested_action",
    ]
    with open(pc, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fn, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    latest = Path(OUTPUT_DIR) / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pj, latest / "outreach_preview.json")
    shutil.copy2(pc, latest / "outreach_preview.csv")
    return {
        "ok": True,
        "preview_count": len(rows),
        "excluded_by_reason": exclusion_counts,
        "review_counts": review_counts,
        "campaign_id": current_cid,
        "files": [str(pj), str(pc)],
    }


def run_approve(
    state: dict[str, Any],
    *,
    limit: int,
    approve_keys_csv: str,
    bypass_filters: bool = False,
) -> dict[str, Any]:
    """Freigabe respektiert das zentrale Decision-Layer.

    - next_action='ready_now'         → automatische Freigabe möglich
    - next_action='call_first'        → nur via expliziten approve-keys
    - next_action='use_forwarding'    → nur via expliziten approve-keys
    - next_action='review'            → nur via expliziten approve-keys
    - next_action='do_not_contact'    → niemals freigeben (block)

    Wenn keine Decision-Felder vorhanden sind (Legacy-Leads), gilt das
    bisherige Verhalten (preview_eligible-Check).
    """
    keys = {k.strip() for k in (approve_keys_csv or "").split(",") if k.strip()}
    who = (os.environ.get("USERNAME") or os.environ.get("USER") or "cli").strip()
    by_lead = _leads_by_email_norm()
    n = 0
    blocked_dnc = 0
    blocked_human_ok = 0
    cap = limit if limit > 0 else 10**9
    for e in state.get("entries") or []:
        if n >= cap:
            break
        if not _preview_eligible_entry(e):
            continue
        ek = (e.get("entry_key") or "").strip()
        if keys and ek not in keys:
            continue

        # Decision-Layer aus dem zugeordneten Lead lesen
        em = _norm_email(e.get("email", ""))
        lead = by_lead.get(em) or {}
        next_action = (lead.get("next_action") or "").lower()

        # do_not_contact: niemals freigeben
        if next_action == "do_not_contact":
            blocked_dnc += 1
            continue

        # Aktionen mit human_ok: nur mit expliziten Keys
        # (verhindert versehentliche Massen-Freigabe von wackeligen Leads)
        if next_action in ("call_first", "use_forwarding", "review") and not keys:
            blocked_human_ok += 1
            continue

        e.update(_enrich_entry_from_lead(dict(e), lead))
        _apply_hardened_outreach_copy(e, lead)
        subj = (e.get("first_email_subject") or "").strip()
        body = (e.get("first_email_body") or "").strip()
        if not subj or not body:
            continue
        e["approved_for_send"] = True
        e["approved_at"] = _now_iso()
        e["approved_by"] = who
        e["approval_campaign_id"] = get_state_campaign_id(state) or ""
        # Decision-Layer-Spuren auf Pipeline-Eintrag übernehmen für CRM-Sicht
        if next_action:
            e["decision_next_action"] = next_action
            e["decision_action_label"] = lead.get("next_action_label", "")
        if bypass_filters:
            e["outreach_send_bypass_filters"] = True
        n += 1
    save_pipeline_state(state)
    return {
        "ok": True,
        "approved": n,
        "approve_keys_filter": sorted(keys) if keys else "all_eligible",
        "bypass_filters": bool(bypass_filters),
        "blocked_do_not_contact": blocked_dnc,
        "blocked_needs_human_ok": blocked_human_ok,
    }


def _build_handoff_summary(e: dict) -> str:
    co = (e.get("company_name") or "").strip()
    cn = (e.get("contact_name") or "").strip()
    em = (e.get("email") or "").strip()
    ang = (e.get("recommended_sales_angle") or "").strip()
    parts = [
        f"Firma: {co}" if co else "",
        f"Kontakt: {cn}" if cn else "",
        f"E-Mail: {em}" if em else "",
        f"Status: {e.get('reply_status', '')}, Stufe: {e.get('outreach_stage', '')}",
        f"Sales-Winkel: {ang[:400]}" if ang else "",
    ]
    return " | ".join(p for p in parts if p).strip()[:2000]


def apply_reply_to_entry(e: dict, reply_status: str) -> None:
    r = (reply_status or "").strip().lower()
    if r == "neutral":
        r = "interested"
    if r not in ("positive", "interested", "later", "negative", "unclear"):
        return
    e["reply_status"] = r
    e["reply_needs_human_approval"] = False
    if r == "positive":
        e["outreach_stage"] = "hot"
        e["lead_temperature"] = "hot"
        e["inbound_class"] = e.get("inbound_class") or "positive"
        e["why_hot"] = (e.get("why_hot") or "").strip() or "Positive Antwort — Interesse oder Termin offen."
        e["handoff_next_action"] = (e.get("handoff_next_action") or "").strip() or (
            "Kurz vorqualifizieren (Passt ein Partner-Setup?); dann Termin oder Übergabe vorbereiten."
        )
        e["handoff_summary"] = _build_handoff_summary(e)
        e["next_followup_at"] = ""
        e["termin_suggestion"] = (e.get("termin_suggestion") or "").strip() or reply_intel._slots_hint()
        e["conversation_status"] = reply_proc.CONVERSATION_INTERESTED
    elif r == "interested":
        e["outreach_stage"] = "warm"
        e["lead_temperature"] = "warm"
        e["inbound_class"] = e.get("inbound_class") or "interested"
        e["handoff_summary"] = _build_handoff_summary(e)
        e["handoff_next_action"] = (e.get("handoff_next_action") or "").strip() or (
            "Rückfrage: passt grundsätzlich ein externes Akquise-Setup — dann passendes Team vorstellen."
        )
        e["next_followup_at"] = ""
        e["conversation_status"] = reply_proc.CONVERSATION_INTERESTED
    elif r == "negative":
        e["outreach_stage"] = "lost"
        e["do_not_resend"] = True
        e["next_followup_at"] = ""
        e["lead_temperature"] = "cold"
        e["conversation_status"] = reply_proc.CONVERSATION_NOT_INTERESTED
    elif r == "later":
        e["reply_status"] = "later"
        later = datetime.now() + timedelta(days=14)
        e["next_followup_at"] = later.replace(microsecond=0).isoformat()
        e["conversation_status"] = reply_proc.CONVERSATION_REPLIED
    elif r == "unclear":
        e["outreach_stage"] = "warm"
        e["lead_temperature"] = "warm"
        e["reply_needs_human_approval"] = True
        e["next_followup_at"] = ""
        e["conversation_status"] = reply_proc.CONVERSATION_REPLIED


def run_reply_update(state: dict[str, Any], entry_key: str, reply_status: str) -> dict[str, Any]:
    ek = (entry_key or "").strip()
    if not ek:
        return {"ok": False, "error": "entry_key fehlt"}
    found = False
    for e in state.get("entries") or []:
        if (e.get("entry_key") or "").strip() != ek:
            continue
        found = True
        apply_reply_to_entry(e, reply_status)
        break
    if not found:
        return {"ok": False, "error": "entry_key nicht gefunden"}
    save_pipeline_state(state)
    return {"ok": True, "entry_key": ek, "reply_status": reply_status}


def export_hot_handoffs_files(state: dict[str, Any]) -> None:
    rows: list[dict[str, Any]] = []
    for e in state.get("entries") or []:
        st = (e.get("outreach_stage") or "").strip()
        rpl = (e.get("reply_status") or "").strip()
        icl = (e.get("inbound_class") or "").strip()
        try:
            icf = float(e.get("inbound_confidence") or 0)
        except (TypeError, ValueError):
            icf = 0.0
        handoff = (
            st == "hot"
            or rpl == "positive"
            or icl == "positive"
            or (rpl == "interested" and icf >= 0.52)
            or (icl == "interested" and icf >= 0.52)
        )
        if not handoff:
            continue
        exp = expose_generator.build_expose(
            e,
            inbound_snippet=(e.get("inbound_last_text") or "")[:800],
            sentiment=reply_proc.map_intel_class_to_sentiment(icl or ""),
            intel_class=icl,
        )
        rows.append({
            "company_name": e.get("company_name", ""),
            "contact_name": e.get("contact_name", ""),
            "email": e.get("email", ""),
            "phone": e.get("phone", ""),
            "website": e.get("website", ""),
            "reply_status": rpl,
            "outreach_stage": st,
            "conversation_status": e.get("conversation_status", ""),
            "inbound_class": icl,
            "inbound_confidence": icf,
            "why_hot": e.get("why_hot", ""),
            "last_inbound_snippet": (e.get("inbound_last_text") or "")[:800],
            "recommended_outbound_reply": (e.get("last_suggested_outbound_reply") or "")[:2000],
            "handoff_summary": e.get("handoff_summary", "") or _build_handoff_summary(e),
            "recommended_next_action": e.get("handoff_next_action", ""),
            "termin_suggestion": e.get("termin_suggestion", "") or reply_intel._slots_hint(),
            "termin_next_step": (
                "Termin vorschlagen (2–3 konkrete Slots); Outlook/Kalender prüfen; Kurz-Agenda senden."
            ),
            "entry_key": e.get("entry_key", ""),
            "expose": exp,
        })
    payload = {"generated_at": _now_iso(), "count": len(rows), "handoffs": rows}
    hj = Path(OUTPUT_DIR) / "hot_handoffs.json"
    _save_json(hj, payload)
    hc = Path(OUTPUT_DIR) / "hot_handoffs.csv"
    fn = list(rows[0].keys()) if rows else [
        "company_name", "contact_name", "email", "phone", "website",
        "reply_status", "outreach_stage", "conversation_status", "inbound_class", "inbound_confidence",
        "why_hot", "last_inbound_snippet", "recommended_outbound_reply",
        "handoff_summary",
        "recommended_next_action", "termin_suggestion", "termin_next_step", "entry_key", "expose",
    ]
    with open(hc, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fn, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {k: v for k, v in r.items() if k != "expose"}
            if r.get("expose"):
                flat["expose"] = json.dumps(r["expose"], ensure_ascii=False)
            w.writerow(flat)


def _append_reply_event(event: dict[str, Any]) -> None:
    data = _load_json(REPLY_EVENTS_JSON, {"version": 1, "events": []})
    data.setdefault("events", [])
    ev = {**event, "ts": event.get("ts") or _now_iso()}
    data["events"].append(ev)
    _save_json(REPLY_EVENTS_JSON, data)


def _append_reply_queue_item(item: dict[str, Any]) -> None:
    data = _load_json(REPLY_QUEUE_JSON, {"version": 1, "updated_at": "", "items": []})
    data.setdefault("items", [])
    mid = (item.get("message_id") or "").strip()
    if mid:
        for x in data["items"]:
            if (x.get("message_id") or "").strip() != mid:
                continue
            if item.get("is_auto_reply") and not x.get("is_auto_reply"):
                x.update(item)
                data["updated_at"] = _now_iso()
                _save_json(REPLY_QUEUE_JSON, data)
            return
    data["items"].append(item)
    data["updated_at"] = _now_iso()
    _save_json(REPLY_QUEUE_JSON, data)


def load_autopilot_reply_config() -> dict[str, Any]:
    return _load_json(
        AUTOPILOT_REPLY_CONFIG_JSON,
        {"reply_templates_approved": False, "auto_send_clear_replies": False},
    )


def save_autopilot_reply_config(cfg: dict[str, Any]) -> None:
    cfg["updated_at"] = _now_iso()
    _save_json(AUTOPILOT_REPLY_CONFIG_JSON, cfg)
    latest = Path(OUTPUT_DIR) / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(AUTOPILOT_REPLY_CONFIG_JSON, latest / "autopilot_reply_config.json")


def run_preview_templates(state: dict[str, Any]) -> dict[str, Any]:
    """Nur Vorlagen (Erstmail/Follow-up/Reply-Klassen) — kein Versand."""
    sample: dict[str, Any] = {}
    for e in state.get("entries") or []:
        if (e.get("first_email_subject") or "").strip():
            sample = dict(e)
            break
    if not sample:
        sample = {
            "contact_name": "Max Mustermann",
            "company_name": "Beispiel GmbH",
            "first_email_subject": OUTREACH_FIRST_SUBJECT,
        }
    ctx = {
        "contact_name": sample.get("contact_name", ""),
        "company_name": sample.get("company_name", ""),
        "first_email_subject": sample.get("first_email_subject", ""),
    }
    reply_samples: dict[str, Any] = {}
    for cls in sorted(reply_intel.REPLY_CLASSES):
        subj, body = reply_intel.build_template_reply(cls, ctx)
        reply_samples[cls] = {"subject": subj, "body": body}
    connector_ctx = ctx
    connector_replies = {
        "positive": reply_proc.generate_reply(reply_proc.SENTIMENT_POSITIVE, connector_ctx),
        "neutral": reply_proc.generate_reply(reply_proc.SENTIMENT_NEUTRAL, connector_ctx),
        "negative": reply_proc.generate_reply(reply_proc.SENTIMENT_NEGATIVE, connector_ctx),
    }
    demo_inbound = (
        "Hallo, klingt interessant — können wir nächste Woche kurz telefonieren? "
        "Dienstag vormittags wäre gut."
    )
    d_cls, d_conf = reply_intel.classify_inbound(demo_inbound)
    d_route = reply_intel.decide_route(d_cls, d_conf, demo_inbound)
    payload = {
        "generated_at": _now_iso(),
        "first_outreach_example": {
            "first_email_subject": sample.get("first_email_subject", ""),
            "first_email_body": (sample.get("first_email_body") or "")[:2000],
            "followup_1": (sample.get("followup_1_text") or "")[:1500],
            "followup_2": (sample.get("followup_2_text") or "")[:1500],
        },
        "reply_templates_by_class": reply_samples,
        "connector_replies_autopilot": connector_replies,
        "classification_demo": {
            "sample_inbound": demo_inbound,
            "class": d_cls,
            "confidence": round(d_conf, 3),
            "route": d_route,
            "auto_send_routes_env": (os.environ.get("REPLY_AUTO_SEND_ROUTES") or "template"),
        },
        "notes": (
            "Nach Anpassung der Mining-/Export-Vorlagen: einmal `approve-templates`. "
            "Autopilot-Antworten nur mit Freigabe + IMAP-Zugang (IONOS_IMAP_USER, IONOS_SMTP_PASS)."
        ),
    }
    try:
        from cae.messaging.exporter import write_messaging_preview

        payload["messaging_assist_paths"] = write_messaging_preview(sample, Path(OUTPUT_DIR))
    except Exception as exc:
        payload["messaging_assist_paths_error"] = str(exc)[:500]
    p = Path(OUTPUT_DIR) / "reply_templates_preview.json"
    _save_json(p, payload)
    latest = Path(OUTPUT_DIR) / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, latest / "reply_templates_preview.json")
    return {"ok": True, "file": str(p)}


def run_approve_templates() -> dict[str, Any]:
    who = (os.environ.get("USERNAME") or os.environ.get("USER") or "cli").strip()
    cfg = load_autopilot_reply_config()
    cfg["reply_templates_approved"] = True
    cfg["approved_at"] = _now_iso()
    cfg["approved_by"] = who
    cfg["auto_send_clear_replies"] = True
    save_autopilot_reply_config(cfg)
    return {"ok": True, "autopilot_reply_config": str(AUTOPILOT_REPLY_CONFIG_JSON)}


def _entry_was_contacted(e: dict) -> bool:
    return (e.get("outreach_stage") or "").strip() in (
        "sent", "followup_1", "followup_2",
    )


def _sent_log_contacted_emails() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ev in _sent_log_events_list():
        if not isinstance(ev, dict) or ev.get("ok") is not True:
            continue
        kind = (ev.get("kind") or "").strip()
        if kind not in ("first_send", "followup_1", "followup_2"):
            continue
        em = _norm_email(ev.get("to", ""))
        if not em:
            continue
        out.setdefault(em, {
            "entry_key": ev.get("entry_key", ""),
            "sender_email": ev.get("sender_email", ""),
            "sent_log_id": ev.get("sent_log_id", ""),
            "ts": ev.get("ts", ""),
        })
    return out


def process_replies(
    state: dict[str, Any] | None = None,
    script: Path | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    """Öffentlicher Einstieg: Antworten laden → klassifizieren → Connector-Antworten erzeugen/speichern."""
    st = state if state is not None else load_pipeline_state()
    sc = script if script is not None else SEND_EMAIL_SCRIPT_DEFAULT
    return run_process_replies(st, Path(sc), limit)


def run_process_replies(
    state: dict[str, Any],
    script: Path,
    limit: int,
) -> dict[str, Any]:
    """
    IMAP lesen, klassifizieren, Connector-Template-Antworten optional auto-senden,
    heikel/unklar an reply_queue. Hartes Nein: kein Versand, Status not_interested.
    """
    cfg = load_autopilot_reply_config()
    if not cfg.get("reply_templates_approved"):
        return {
            "ok": False,
            "error": "reply_templates nicht freigegeben - zuerst: python mine.py --outreach approve-templates",
        }
    cap = limit if limit > 0 else 10**9
    pipeline_contacted = {
        _norm_email(e.get("email", ""))
        for e in state.get("entries") or []
        if _entry_was_contacted(e) and (e.get("email") or "").strip()
    }
    sent_log_contacted = _sent_log_contacted_emails()
    contacted = set(pipeline_contacted) | set(sent_log_contacted)
    msgs = reply_intel.fetch_inbound_messages(candidate_from_emails=contacted, max_fetch=80)
    _append_reply_event({
        "kind": "poll",
        "fetched": len(msgs),
        "contacted_leads": len(pipeline_contacted),
        "sent_log_contacted": len(sent_log_contacted),
    })
    auto_sent = 0
    queued = 0
    skipped = 0
    dry = (os.environ.get("REPLY_DRY_RUN") or "").lower() in ("1", "true", "yes")
    processed = 0
    for msg in msgs:
        if processed >= cap:
            break
        em = (msg.get("from_email") or "").strip().lower()
        mid = (msg.get("message_id") or "").strip()
        body = (msg.get("body_text") or "").strip()
        actual_from = (msg.get("from_email_actual") or em or "").strip().lower()
        received_account = (msg.get("received_account") or "").strip()
        is_auto_reply = bool(msg.get("is_auto_reply"))
        auto_reply_reason = (msg.get("auto_reply_reason") or "").strip()
        entry: Optional[dict] = None
        for e in state.get("entries") or []:
            if _norm_email(e.get("email", "")) == em:
                entry = e
                break
        if not entry:
            cls, conf = reply_intel.classify_inbound(body)
            if is_auto_reply:
                cls, conf = "neutral", max(float(conf or 0), 0.82)
            route = reply_intel.decide_route(cls, conf, body)
            if is_auto_reply:
                route = "template"
            sentiment = reply_proc.map_intel_class_to_sentiment(cls)
            sent_meta = sent_log_contacted.get(em, {})
            reason = "sent_log_match_without_pipeline_entry"
            if is_auto_reply:
                reason = "sent_log_auto_reply_without_pipeline_entry"
            _append_reply_queue_item({
                "message_id": mid,
                "entry_key": sent_meta.get("entry_key", ""),
                "from_email": em,
                "from_email_actual": actual_from,
                "received_account": received_account,
                "is_auto_reply": is_auto_reply,
                "auto_reply_reason": auto_reply_reason,
                "inbound_subject": msg.get("subject", ""),
                "inbound_snippet": body[:1200],
                "inbound_class": cls,
                "sentiment": sentiment,
                "confidence": conf,
                "route": route,
                "action": reply_proc.ACTION_REVIEW,
                "suggested_subject": "",
                "suggested_body": "",
                "reason": reason,
                "needs_approval": True,
                "appointment_ready": False if is_auto_reply else reply_intel.detect_appointment_intent(body, cls, conf).get("appointment_ready", False),
                "appointment_reason": "" if is_auto_reply else reply_intel.detect_appointment_intent(body, cls, conf).get("appointment_reason", ""),
                "sent_log_id": sent_meta.get("sent_log_id", ""),
                "sent_at": sent_meta.get("ts", ""),
            })
            _append_reply_event({
                "kind": "queued_sent_log_only",
                "from_email": em,
                "from_email_actual": actual_from,
                "received_account": received_account,
                "message_id": mid,
                "inbound_class": cls,
                "confidence": conf,
                "route": route,
                "is_auto_reply": is_auto_reply,
                "auto_reply_reason": auto_reply_reason,
                "reason": reason,
            })
            queued += 1
            processed += 1
            continue
        seen = list(entry.get("processed_inbound_ids") or [])
        if mid and mid in seen:
            skipped += 1
            continue
        if not _entry_was_contacted(entry):
            skipped += 1
            continue
        cls, conf = reply_intel.classify_inbound(body)
        if is_auto_reply:
            cls, conf = "neutral", max(float(conf or 0), 0.82)
        route = reply_intel.decide_route(cls, conf, body)
        if is_auto_reply:
            route = "template"
        sentiment = reply_proc.map_intel_class_to_sentiment(cls)
        ctx = {
            "contact_name": entry.get("contact_name", ""),
            "company_name": entry.get("company_name", ""),
            "first_email_subject": entry.get("first_email_subject", ""),
        }
        entry["inbound_last_text"] = body[:8000]
        entry["inbound_class"] = cls
        entry["inbound_confidence"] = round(conf, 4)
        entry["last_inbound_auto_reply"] = is_auto_reply
        entry["last_inbound_auto_reply_reason"] = auto_reply_reason
        entry["last_reply_route"] = route
        entry["conversation_status"] = reply_proc.CONVERSATION_REPLIED

        # ── Terminbereitschaft (additiv, kein auto-send) ──────────────────────
        appt_info = reply_intel.detect_appointment_intent(body, cls, conf)
        if appt_info["appointment_ready"]:
            # Einmal gesetzt bleibt es (auch wenn spätere Replies schwächer sind)
            entry["appointment_ready"] = True
            entry["appointment_reason"] = appt_info["appointment_reason"]
        elif not entry.get("appointment_ready"):
            entry["appointment_ready"] = False
            if not entry.get("appointment_reason"):
                entry["appointment_reason"] = appt_info.get("appointment_reason", "")

        human_reason = ""
        if route == "human":
            human_reason = "routing_human_escalation"
        if reply_intel.must_escalate_human(body):
            human_reason = "sensitive_or_legal"
        if reply_intel.negative_with_potential(body):
            human_reason = "negative_with_potential_objection"

        if sentiment == reply_proc.SENTIMENT_NEGATIVE and not human_reason:
            entry["last_suggested_outbound_reply"] = ""
            apply_reply_to_entry(entry, "negative")
            _append_reply_queue_item({
                "message_id": mid,
                "entry_key": entry.get("entry_key", ""),
                "from_email": em,
                "from_email_actual": actual_from,
                "received_account": received_account,
                "is_auto_reply": is_auto_reply,
                "auto_reply_reason": auto_reply_reason,
                "inbound_subject": msg.get("subject", ""),
                "inbound_snippet": body[:1200],
                "inbound_class": cls,
                "sentiment": sentiment,
                "confidence": conf,
                "route": route,
                "reply_action": "ignore",
                "action": reply_proc.ACTION_IGNORE,
                "suggested_subject": "",
                "suggested_body": "",
                "reason": "negative_no_outbound",
                "needs_approval": False,
                "appointment_ready": entry.get("appointment_ready", False),
                "appointment_reason": entry.get("appointment_reason", ""),
            })
            _append_reply_event({
                "kind": "inbound_negative_ignored",
                "entry_key": entry.get("entry_key"),
                "from_email": em,
                "from_email_actual": actual_from,
                "received_account": received_account,
                "is_auto_reply": is_auto_reply,
                "auto_reply_reason": auto_reply_reason,
                "message_id": mid,
                "inbound_class": cls,
                "confidence": conf,
            })
            if mid:
                seen.append(mid)
                entry["processed_inbound_ids"] = seen[-40:]
            processed += 1
            continue

        if human_reason:
            gen = (
                reply_proc.generate_reply_with_assist_layer(
                    reply_proc.SENTIMENT_POSITIVE,
                    ctx,
                    intel_class=cls,
                    inbound_text=body,
                )
                if sentiment == reply_proc.SENTIMENT_POSITIVE
                else reply_proc.generate_reply_with_assist_layer(
                    reply_proc.SENTIMENT_NEUTRAL,
                    ctx,
                    intel_class=cls,
                    inbound_text=body,
                )
            )
        else:
            gen = reply_proc.generate_reply_with_assist_layer(
                sentiment, ctx, intel_class=cls, inbound_text=body,
            )
        subj, draft_body = gen["subject"], gen["body"]
        reply_sales_category = (gen.get("reply_category") or "").strip()
        meeting_angle_meta = (gen.get("meeting_angle") or "").strip()
        if (os.environ.get("REPLY_USE_LLM_REFINE") or "").lower() in ("1", "true", "yes"):
            draft_body = reply_intel.maybe_llm_refine(cls, route, body, draft_body, ctx)
        entry["last_suggested_outbound_reply"] = draft_body[:4000]
        allow_auto = bool(cfg.get("auto_send_clear_replies")) and reply_intel.auto_send_allowed(route)
        reply_may_auto_send = gen.get("action") == reply_proc.ACTION_SEND
        smtp_ok = bool(allow_auto and reply_may_auto_send and not dry)

        if human_reason or route == "human":
            _append_reply_queue_item({
                "message_id": mid,
                "entry_key": entry.get("entry_key", ""),
                "from_email": em,
                "from_email_actual": actual_from,
                "received_account": received_account,
                "is_auto_reply": is_auto_reply,
                "auto_reply_reason": auto_reply_reason,
                "inbound_subject": msg.get("subject", ""),
                "inbound_snippet": body[:1200],
                "inbound_class": cls,
                "sentiment": sentiment,
                "confidence": conf,
                "route": route,
                "action": gen["action"],
                "suggested_subject": subj,
                "suggested_body": draft_body,
                "reason": human_reason or "human_review",
                "needs_approval": True,
                "appointment_ready": entry.get("appointment_ready", False),
                "appointment_reason": entry.get("appointment_reason", ""),
                "reply_sales_category": reply_sales_category,
                "meeting_angle": meeting_angle_meta,
            })
            _append_reply_event({
                "kind": "queued_human",
                "entry_key": entry.get("entry_key"),
                "from_email": em,
                "from_email_actual": actual_from,
                "received_account": received_account,
                "is_auto_reply": is_auto_reply,
                "auto_reply_reason": auto_reply_reason,
                "message_id": mid,
                "inbound_class": cls,
                "confidence": conf,
                "route": route,
            })
            apply_reply_to_entry(entry, "unclear")
            entry["reply_needs_human_approval"] = True
            queued += 1
            if mid:
                seen.append(mid)
                entry["processed_inbound_ids"] = seen[-40:]
            processed += 1
            continue

        if not smtp_ok:
            if dry:
                qreason = "dry_run"
            elif not reply_may_auto_send:
                qreason = "reply_action_review_recommended"
            elif not allow_auto:
                qreason = "auto_send_disabled_for_route"
            else:
                qreason = "no_auto_smtp"
            _append_reply_queue_item({
                "message_id": mid,
                "entry_key": entry.get("entry_key", ""),
                "from_email": em,
                "from_email_actual": actual_from,
                "received_account": received_account,
                "is_auto_reply": is_auto_reply,
                "auto_reply_reason": auto_reply_reason,
                "inbound_subject": msg.get("subject", ""),
                "inbound_snippet": body[:1200],
                "inbound_class": cls,
                "sentiment": sentiment,
                "confidence": conf,
                "route": route,
                "action": gen["action"],
                "suggested_subject": subj,
                "suggested_body": draft_body,
                "reason": qreason,
                "needs_approval": True,
                "appointment_ready": entry.get("appointment_ready", False),
                "appointment_reason": entry.get("appointment_reason", ""),
                "reply_sales_category": reply_sales_category,
                "meeting_angle": meeting_angle_meta,
            })
            _append_reply_event({
                "kind": "queued_preview",
                "entry_key": entry.get("entry_key"),
                "route": route,
                "dry_run": dry,
            })
            queued += 1
            if mid:
                seen.append(mid)
                entry["processed_inbound_ids"] = seen[-40:]
            processed += 1
            continue

        try:
            res = send_via_script(script, em, subj, draft_body)
        except Exception as exc:
            _append_reply_event({
                "kind": "auto_reply_error",
                "entry_key": entry.get("entry_key"),
                "error": str(exc)[:500],
            })
            err = str(exc)
            entry["last_error"] = f"auto_reply:{err}"[:800]
            processed += 1
            continue
        if res.get("ok") is not True:
            _append_reply_queue_item({
                "message_id": mid,
                "entry_key": entry.get("entry_key", ""),
                "from_email": em,
                "from_email_actual": actual_from,
                "received_account": received_account,
                "action": gen["action"],
                "suggested_subject": subj,
                "suggested_body": draft_body,
                "reason": f"smtp_failed:{res.get('error', '')}"[:300],
                "needs_approval": True,
                "reply_sales_category": reply_sales_category,
                "meeting_angle": meeting_angle_meta,
            })
            _append_reply_event({
                "kind": "auto_reply_smtp_nok",
                "entry_key": entry.get("entry_key"),
                "error": res.get("error", ""),
            })
            queued += 1
            processed += 1
            continue

        ts = _now_iso()
        entry["last_auto_reply_at"] = ts
        entry["last_contacted_at"] = ts
        apply_reply_to_entry(entry, cls if cls in ("positive", "interested", "later", "negative") else "unclear")
        if sentiment == reply_proc.SENTIMENT_POSITIVE:
            entry["conversation_status"] = reply_proc.CONVERSATION_MEETING_PENDING
            expose_generator.append_expose_jsonl(
                Path(OUTPUT_DIR) / "expose_handoff.jsonl",
                expose_generator.build_expose(
                    entry, inbound_snippet=body[:500], sentiment=sentiment, intel_class=cls,
                ),
                ts=ts,
            )
        elif sentiment == reply_proc.SENTIMENT_NEUTRAL:
            entry["conversation_status"] = reply_proc.CONVERSATION_REPLIED
        if cls == "positive":
            entry["termin_suggestion"] = reply_intel._slots_hint()
        mid_fu = _new_sent_id("reply", em)
        entry["sent_message_id"] = (entry.get("sent_message_id") or "") + f",{mid_fu}"
        reply_from = (res.get("from") or os.environ.get("IONOS_SMTP_USER") or "").strip()
        _append_event({
            "ts": ts, "kind": "auto_reply", "to": em, "ok": True,
            "sent_log_id": mid_fu, "error": "", "entry_key": entry.get("entry_key"),
            "inbound_class": cls, "route": route,
            "sender_email": reply_from,
        })
        _append_reply_event({
            "kind": "auto_reply_sent",
            "entry_key": entry.get("entry_key"),
            "inbound_class": cls,
            "confidence": conf,
            "route": route,
            "smtp_meta": {k: res.get(k) for k in ("sent_folder_sync",) if k in res},
        })
        auto_sent += 1
        if mid:
            seen.append(mid)
            entry["processed_inbound_ids"] = seen[-40:]
        processed += 1

    save_pipeline_state(state)
    latest = Path(OUTPUT_DIR) / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    for name in ("reply_events.json", "reply_queue.json"):
        src = Path(OUTPUT_DIR) / name
        if src.is_file():
            shutil.copy2(src, latest / name)
    return {
        "ok": True,
        "fetched": len(msgs),
        "processed": processed,
        "auto_sent": auto_sent,
        "queued_human_or_preview": queued,
        "skipped": skipped,
        "dry_run": dry,
    }


def sync_from_latest_run() -> dict[str, Any]:
    """Laedt output/latest/leads.json und vereinheitlicht mit Pipeline."""
    path = _latest_leads_path()
    if not path.is_file():
        return {"ok": False, "error": f"Keine {path.name} — zuerst einen Mining-Lauf (mine.py) ausfuehren."}
    leads = _load_json(path, [])
    if not isinstance(leads, list):
        return {"ok": False, "error": "Ungueltiges leads.json Format"}

    state = load_pipeline_state()
    prune_stats = prune_pipeline_junk(state)
    stats: dict[str, int] = {
        "sync_skipped": 0,
        "sync_skipped_fake": 0,
        "sync_skipped_no_email": 0,
        "sync_skipped_placeholder": 0,
        "sync_skipped_not_ready": 0,
    }
    by_key: dict[str, dict] = {e["entry_key"]: e for e in state.get("entries", []) if e.get("entry_key")}
    skipped: list[dict] = []
    ingested = 0
    for lead in leads:
        skip_reason = classify_pipeline_ingest(lead)
        if skip_reason is not None:
            _accum_skip(stats, skip_reason)
            skipped.append({
                "email": (lead.get("email") or "").strip(),
                "company_name": (lead.get("company_name") or "").strip(),
                "website": (lead.get("website") or "").strip(),
                "reason": skip_reason,
            })
            continue
        inc = lead_to_entry(lead)
        em = _norm_email(inc.get("email", ""))
        k_em = _find_key_by_email(by_key, em)
        if k_em is not None:
            by_key[k_em] = _merge_entry(by_key[k_em], inc)
            ingested += 1
            continue
        ek = inc["entry_key"]
        if ek in by_key:
            by_key[ek] = _merge_entry(by_key[ek], inc)
        else:
            by_key[ek] = inc
        ingested += 1

    by_key = _collapse_duplicate_email_keys(by_key)
    state["entries"] = list(by_key.values())
    save_pipeline_state(state)
    sp = Path(OUTPUT_DIR) / "skipped_duplicates.csv"
    with open(sp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["email", "reason", "existing_key"], extrasaction="ignore")
        w.writeheader()
    inv = Path(OUTPUT_DIR) / "skipped_invalid.csv"
    with open(inv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["email", "company_name", "website", "reason"], extrasaction="ignore")
        w.writeheader()
        for s in skipped:
            w.writerow({k: s.get(k, "") for k in ["email", "company_name", "website", "reason"]})
    return {
        "ok": True,
        "leads_in_file": len(leads),
        "merged": ingested,
        "pipeline_entries": len(state["entries"]),
        "sync_skipped": stats["sync_skipped"],
        "sync_skipped_fake": stats.get("sync_skipped_fake", 0),
        "sync_skipped_no_email": stats.get("sync_skipped_no_email", 0),
        "sync_skipped_placeholder": stats.get("sync_skipped_placeholder", 0),
        "sync_skipped_not_ready": stats.get("sync_skipped_not_ready", 0),
        "sync_skipped_invalid_email": stats.get("sync_skipped_invalid_email", 0),
        "sync_skipped_disposable_email": stats.get("sync_skipped_disposable_email", 0),
        "pipeline_pruned_removed": prune_stats["pipeline_pruned_removed"],
        "pipeline_pruned_soft_lost": prune_stats["pipeline_pruned_soft_lost"],
    }


OUTREACH_SEND_LOG_KINDS = frozenset({"first_send", "followup_1", "followup_2"})


def _local_today_prefix() -> str:
    return datetime.now().date().isoformat()


def _event_ts_date_prefix(ts: str) -> str:
    s = (ts or "").strip()
    if len(s) >= 10:
        return s[:10]
    return ""


def _sent_log_events_list() -> list[dict[str, Any]]:
    data = _load_json(OUTREACH_SENT_LOG_JSON, {"version": 1, "events": []})
    ev = data.get("events") or []
    return ev if isinstance(ev, list) else []


def _outreach_sender_max_slots() -> int:
    raw = (os.environ.get("OUTREACH_SENDER_MAX_SLOTS") or "3").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3
    return max(1, min(20, n))


def _outreach_sender_slots_from_env() -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    max_slots = _outreach_sender_max_slots()
    for i in range(1, max_slots + 1):
        u = (os.environ.get(f"OUTREACH_SENDER_{i}_USER") or "").strip()
        p = (os.environ.get(f"OUTREACH_SENDER_{i}_PASS") or "").strip()
        if not u or not p:
            continue
        try:
            lim = max(0, int(os.environ.get(f"OUTREACH_SENDER_{i}_DAILY_LIMIT", "5")))
        except ValueError:
            lim = 5
        raw_w = os.environ.get(f"OUTREACH_SENDER_{i}_WEIGHT")
        if raw_w is None or str(raw_w).strip() == "":
            weight = 1
        else:
            try:
                weight = int(str(raw_w).strip())
            except ValueError:
                weight = 1
        weight = max(1, min(20, weight))
        sh = os.environ.get(f"OUTREACH_SENDER_{i}_SMTP_HOST")
        sport = os.environ.get(f"OUTREACH_SENDER_{i}_SMTP_PORT")
        ih = os.environ.get(f"OUTREACH_SENDER_{i}_IMAP_HOST")
        iport = os.environ.get(f"OUTREACH_SENDER_{i}_IMAP_PORT")
        slots.append({
            "user": u,
            "pass": p,
            "daily_limit": lim,
            "slot": i,
            "smtp_host": (str(sh).strip() if sh is not None else ""),
            "smtp_port": (str(sport).strip() if sport is not None else ""),
            "imap_host": (str(ih).strip() if ih is not None else ""),
            "imap_port": (str(iport).strip() if iport is not None else ""),
            "sent_folder": (os.environ.get(f"OUTREACH_SENDER_{i}_SENT_FOLDER") or "").strip(),
            "sync_sent": (os.environ.get(f"OUTREACH_SENDER_{i}_SYNC_SENT") or "").strip(),
            "weight": weight,
        })
    return slots


def _load_outreach_rr_index(n_senders: int) -> int:
    if n_senders <= 0:
        return 0
    raw = _load_json(OUTREACH_ROTATION_JSON, {})
    try:
        return int(raw.get("next_sender_idx", 0)) % n_senders
    except (TypeError, ValueError):
        return 0


def _save_outreach_rr_index(idx: int, n_senders: int) -> None:
    if n_senders <= 0:
        return
    normalized = int(idx) % n_senders
    _save_json(OUTREACH_ROTATION_JSON, {"next_sender_idx": normalized, "updated_at": _now_iso()})


def outreach_today_send_stats() -> dict[str, Any]:
    """
    sent_log: heute (lokal), nur first_send/followup_* mit ok=True.
    """
    today = _local_today_prefix()
    slots = _outreach_sender_slots_from_env()
    by_sender: dict[str, int] = {s["user"]: 0 for s in slots}
    total = 0
    for ev in _sent_log_events_list():
        if ev.get("kind") not in OUTREACH_SEND_LOG_KINDS:
            continue
        if ev.get("ok") is not True:
            continue
        if _event_ts_date_prefix(str(ev.get("ts") or "")) != today:
            continue
        total += 1
        se = (ev.get("sender_email") or "").strip()
        if se and se in by_sender:
            by_sender[se] += 1
        elif se:
            by_sender[se] = by_sender.get(se, 0) + 1
    remaining: dict[str, int] = {}
    for s in slots:
        u = s["user"]
        lim = int(s["daily_limit"])
        remaining[u] = max(0, lim - by_sender.get(u, 0))
    return {
        "sent_today_total": total,
        "sent_today_by_sender": dict(sorted(by_sender.items())),
        "remaining_today_by_sender": {k: remaining[k] for k in sorted(remaining.keys())},
    }


def _outreach_recipients_sent_ok_ever() -> set[str]:
    out: set[str] = set()
    for ev in _sent_log_events_list():
        if ev.get("kind") not in OUTREACH_SEND_LOG_KINDS:
            continue
        if ev.get("ok") is not True:
            continue
        to = _norm_email(str(ev.get("to") or ""))
        if to:
            out.add(to)
    return out


def _outreach_domain_owner_today() -> dict[str, str]:
    """
    Lead-Domain (Empfänger-Domain) -> Sender, der heute zuerst/diesbezüglich aus Outbound geschrieben hat.
    Nur Events mit sender_email; Legacy-Events ohne Feld werden ignoriert (kein False-Positive).
    """
    today = _local_today_prefix()
    owners: dict[str, str] = {}
    for ev in _sent_log_events_list():
        if ev.get("kind") not in OUTREACH_SEND_LOG_KINDS:
            continue
        if ev.get("ok") is not True:
            continue
        if _event_ts_date_prefix(str(ev.get("ts") or "")) != today:
            continue
        sender = (ev.get("sender_email") or "").strip()
        if not sender:
            continue
        to = str(ev.get("to") or "").strip()
        dom = _email_domain_for_mx(to)
        if not dom:
            continue
        d = dom.lower()
        owners.setdefault(d, sender)
    return owners


def _outreach_weighted_sender_sequence(senders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Slot-Reihenfolge; jeder Sender erscheint weight-mal (Warm-up / Gmail höher gewichten)."""
    seq: list[dict[str, Any]] = []
    for s in senders:
        try:
            w = int(s.get("weight", 1))
        except (TypeError, ValueError):
            w = 1
        w = max(1, min(20, w))
        seq.extend([s] * w)
    return seq


def _outreach_weighted_rotation_length(senders: list[dict[str, Any]]) -> int:
    if not senders:
        return 0
    total = 0
    for s in senders:
        try:
            w = int(s.get("weight", 1))
        except (TypeError, ValueError):
            w = 1
        total += max(1, min(20, w))
    return max(total, 1)


def _pick_outreach_sender_round_robin(
    *,
    senders: list[dict[str, Any]],
    rr_start: int,
    recipient_domain: str,
    counts_today: dict[str, int],
    domain_owner_today: dict[str, str],
) -> tuple[Optional[dict[str, Any]], int, str]:
    """
    Gewichtete Round-Robin-Rotation: (sender_dict|None, rr_position_in_weighted_seq, skip_reason).
    """
    n = len(senders)
    if n == 0:
        return None, 0, "no_senders_configured"
    seq = _outreach_weighted_sender_sequence(senders)
    ell = len(seq)
    if ell == 0:
        return None, 0, "no_senders_configured"
    dom = (recipient_domain or "").strip().lower()
    try_order = [(rr_start + i) % ell for i in range(ell)]
    incumbent = domain_owner_today.get(dom, "") if dom else ""
    for pos in try_order:
        s = seq[pos]
        u = s["user"]
        lim = int(s["daily_limit"])
        if counts_today.get(u, 0) >= lim:
            continue
        if incumbent and _norm_email(incumbent) != _norm_email(u):
            continue
        return s, pos, ""
    if dom and incumbent:
        return None, 0, "domain_locked_other_sender_today"
    caps = all(counts_today.get(senders[j]["user"], 0) >= int(senders[j]["daily_limit"]) for j in range(n))
    if caps:
        return None, 0, "all_senders_at_daily_cap"
    return None, 0, "no_sender_available"


def _send_email_subprocess(
    script: Path,
    to: str,
    subject: str,
    body: str,
    smtp_user: str,
    smtp_pass: str,
    sender_slot: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """send_email.py per subprocess; ENV nur im Child gesetzt (keine Passwörter loggen)."""
    if not script.is_file():
        return {"ok": False, "error": f"send_email.py nicht gefunden: {script}"}
    env = os.environ.copy()
    env["IONOS_SMTP_USER"] = smtp_user
    env["IONOS_SMTP_PASS"] = smtp_pass
    env["IONOS_IMAP_USER"] = smtp_user
    env["IONOS_IMAP_PASS"] = smtp_pass
    # CRITICAL: FROM-Adresse muss identisch mit SMTP-Auth sein.
    # Ohne diese Zeile liest send_email.py die alte IONOS_SMTP_USER aus der .env
    # → 550 "Sender address not allowed" wenn ein anderer Sender gewählt ist.
    env["MAIL_FROM"] = smtp_user
    env["MAIL_REPLY_TO"] = smtp_user

    slot = sender_slot or {}
    sh = (slot.get("smtp_host") or "").strip()
    if sh:
        env["IONOS_SMTP_HOST"] = sh
    sp = (slot.get("smtp_port") or "").strip()
    if sp:
        env["IONOS_SMTP_PORT"] = sp
    ih = (slot.get("imap_host") or "").strip()
    if ih:
        env["IONOS_IMAP_HOST"] = ih
    ip = (slot.get("imap_port") or "").strip()
    if ip:
        env["IONOS_IMAP_PORT"] = ip
    sf_slot = (slot.get("sent_folder") or "").strip()
    if sf_slot:
        env["IONOS_SENT_FOLDER"] = sf_slot
    else:
        sf = (os.environ.get("IONOS_SENT_FOLDER") or "").strip()
        if sf:
            env["IONOS_SENT_FOLDER"] = sf
    ss = (slot.get("sync_sent") or "").strip()
    if ss:
        env["IONOS_SYNC_SENT"] = ss
    try:
        proc = subprocess.run(
            [sys.executable, str(script), to, subject, body],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "subprocess timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    raw = (proc.stdout or "").strip()
    if not raw:
        err_snip = (proc.stderr or "")[:400]
        return {"ok": False, "error": f"send_email.py keine Ausgabe ({proc.returncode}): {err_snip}"}
    line = raw.splitlines()[-1]
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"ungültige JSON-Antwort: {line[:180]}"}


def _sender_slot_for_smtp_user(senders: list[dict[str, Any]], smtp_user: str) -> Optional[dict[str, Any]]:
    for s in senders:
        if s.get("user") == smtp_user:
            return s
    return None


def _credentials_for_outreach_sender(
    sender_email: str,
    senders: list[dict[str, Any]],
) -> Optional[tuple[str, str]]:
    n = _norm_email(sender_email)
    if not n:
        return None
    for s in senders:
        if _norm_email(s["user"]) == n:
            return (s["user"], s["pass"])
    return None


def _outreach_counts_live_today(senders: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {s["user"]: 0 for s in senders}
    today = _local_today_prefix()
    for ev in _sent_log_events_list():
        if ev.get("kind") not in OUTREACH_SEND_LOG_KINDS or ev.get("ok") is not True:
            continue
        if _event_ts_date_prefix(str(ev.get("ts") or "")) != today:
            continue
        su = (ev.get("sender_email") or "").strip()
        if su:
            counts[su] = counts.get(su, 0) + 1
    return counts


def _pick_followup_credentials(
    entry: dict,
    senders: list[dict[str, Any]],
    counts_live: dict[str, int],
) -> Optional[tuple[str, str, str]]:
    """(smtp_user, pass, sender_email) mit Tageslimit; None = alle voll."""
    pref = (entry.get("outreach_sender_email") or "").strip()
    ordered: list[tuple[str, str]] = []
    creds_pref = _credentials_for_outreach_sender(pref, senders)
    if creds_pref:
        ordered.append(creds_pref)
    for s in senders:
        t = (s["user"], s["pass"])
        if t not in ordered:
            ordered.append(t)
    for user, pw in ordered:
        lim = next((int(x["daily_limit"]) for x in senders if x["user"] == user), 5)
        if counts_live.get(user, 0) >= lim:
            continue
        return (user, pw, user)
    return None


def _get_send_function(script: Path):
    if not script.is_file():
        raise FileNotFoundError(f"send_email.py nicht gefunden: {script}")
    spec = importlib.util.spec_from_file_location("openclaw_send_email", script)
    if not spec or not spec.loader:
        raise RuntimeError("Modul-Spec ungueltig")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "send_email", None)
    if not callable(fn):
        raise RuntimeError("send_email.py enthaelt keine send_email(to, subject, body, attachments=None) Funktion")
    return fn


def send_via_script(script: Path, to: str, subject: str, body: str) -> dict[str, Any]:
    """Laedt send_email.py und ruft send_email() — Erfolg nur bei exakt result.get('ok') is True."""
    try:
        send_fn = _get_send_function(script)
        return send_fn(to, subject, body, None)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _new_sent_id(kind: str, email: str) -> str:
    return f"{kind}_{_now_iso().replace(':', '')}_{hashlib.md5(email.encode()).hexdigest()[:8]}"


def run_first_sends(
    state: dict[str, Any],
    script: Path,
    limit: int,
) -> dict[str, Any]:
    cap = _effective_outreach_send_cap(limit)
    sent_n = 0
    err_n = 0
    skip_unapproved = 0
    skip_enterprise = 0
    skip_invalid_domain = 0
    skip_empty_recipient = 0
    skip_duplicate_recipient = 0
    skip_no_sender = 0
    by_lead = _leads_by_email_norm()
    senders = _outreach_sender_slots_from_env()
    use_mailboxes = len(senders) > 0
    recipients_ever = _outreach_recipients_sent_ok_ever()
    domain_owner_mutable = dict(_outreach_domain_owner_today())
    counts_live = _outreach_counts_live_today(senders)
    wlen = _outreach_weighted_rotation_length(senders) if senders else 0
    rr_cursor = _load_outreach_rr_index(wlen) if senders else 0

    for e in list(state.get("entries", [])):
        if sent_n >= cap:
            break
        if e.get("do_not_resend"):
            continue
        rts = (e.get("ready_to_send") or "").strip()
        if rts != "yes":
            continue
        stg = (e.get("outreach_stage") or "new").strip()
        if stg not in ("new", "drafted", "ready"):
            continue
        em = (e.get("email") or "").strip()
        if not em:
            skip_empty_recipient += 1
            e["last_error"] = "empty_recipient"
            continue
        if _norm_email(em) in recipients_ever:
            e["last_error"] = "duplicate_recipient_already_sent"
            skip_duplicate_recipient += 1
            continue
        if not e.get("approved_for_send"):
            e["last_error"] = "not_approved_for_send - zuerst `python mine.py --outreach approve`."
            skip_unapproved += 1
            continue
        lead = by_lead.get(_norm_email(em))
        e.update(_enrich_entry_from_lead(dict(e), lead))
        _apply_hardened_outreach_copy(e, lead)
        ent_block, ent_reason = _entry_enterprise_send_blocked(e, lead)
        if ent_block:
            e["last_error"] = ent_reason[:800]
            skip_enterprise += 1
            logger.warning("[outreach] Skip Versand (enterprise) %s: %s", em, ent_reason)
            continue
        ok_dom, dom_reason = _recipient_domain_mail_ready(em)
        if not ok_dom:
            e["last_error"] = dom_reason[:800]
            skip_invalid_domain += 1
            logger.warning("[outreach] Skip Versand (%s) %s", dom_reason, em)
            continue
        ok_pre, pre_reason = _entry_send_precheck_ok(e)
        if not ok_pre:
            e["last_error"] = f"precheck_blocked:{pre_reason}"[:800]
            logger.warning("[outreach] Skip Versand (precheck) %s: %s", em, pre_reason)
            err_n += 1
            continue
        subj = (e.get("first_email_subject") or "").strip()
        body = (e.get("first_email_body") or "").strip()
        if not subj or not body:
            e["last_error"] = "Betreff/Body leer (sync nach Mining)"
            err_n += 1
            continue

        rec_dom = _email_domain_for_mx(em)
        sender_mail_for_log = ""
        chosen_rr_pos = 0
        if use_mailboxes:
            chosen, chosen_rr_pos, skip_reason = _pick_outreach_sender_round_robin(
                senders=senders,
                rr_start=rr_cursor,
                recipient_domain=rec_dom,
                counts_today=counts_live,
                domain_owner_today=domain_owner_mutable,
            )
            if not chosen:
                e["last_error"] = skip_reason[:800]
                skip_no_sender += 1
                continue
            # Cursor IMMER weiterschalten — unabhängig von Erfolg/Fehler.
            # Sonst bleibt der Cursor bei Fehlern stehen und derselbe Sender
            # wird endlos wiederholt (Gmail kommt nie dran).
            rr_cursor = (chosen_rr_pos + 1) % wlen
            _save_outreach_rr_index(rr_cursor, wlen)
            try:
                res = _send_email_subprocess(
                    script, em, subj, body, chosen["user"], chosen["pass"], chosen,
                )
            except Exception as exc:  # noqa: BLE001
                e["last_error"] = f"Exception: {exc}"[:800]
                logger.exception("[outreach] Erstversand-Exception: %s", em)
                _append_event({
                    "ts": _now_iso(), "kind": "first_send", "to": em, "ok": False,
                    "sent_log_id": "", "error": str(exc)[:800], "entry_key": e.get("entry_key"),
                    "sender_email": chosen["user"],
                })
                err_n += 1
                _pause_between_outreach_sends()
                continue
            sender_mail_for_log = chosen["user"]
        else:
            try:
                res = send_via_script(script, em, subj, body)
            except Exception as exc:  # noqa: BLE001
                e["last_error"] = f"Exception: {exc}"[:800]
                logger.exception("[outreach] Erstversand-Exception: %s", em)
                sender_mail_for_log = (os.environ.get("IONOS_SMTP_USER") or "").strip()
                _append_event({
                    "ts": _now_iso(), "kind": "first_send", "to": em, "ok": False,
                    "sent_log_id": "", "error": str(exc)[:800], "entry_key": e.get("entry_key"),
                    "sender_email": sender_mail_for_log,
                })
                err_n += 1
                _pause_between_outreach_sends()
                continue
            sender_mail_for_log = (res.get("from") or os.environ.get("IONOS_SMTP_USER") or "").strip()

        if res.get("ok") is True:
            ts = _now_iso()
            e["outreach_stage"] = "sent"
            e["last_error"] = ""
            e["first_sent_at"] = ts
            e["last_contacted_at"] = ts
            e["conversation_status"] = reply_proc.CONVERSATION_CONTACTED
            fdt = datetime.now() + timedelta(days=FOLLOWUP_DAYS_1)
            e["next_followup_at"] = fdt.replace(microsecond=0).isoformat()
            mid = _new_sent_id("first", em)
            e["sent_message_id"] = mid
            if use_mailboxes:
                e["outreach_sender_email"] = sender_mail_for_log
                counts_live[sender_mail_for_log] = counts_live.get(sender_mail_for_log, 0) + 1
                dlow = (rec_dom or "").strip().lower()
                if dlow:
                    domain_owner_mutable.setdefault(dlow, sender_mail_for_log)
            elif not (e.get("outreach_sender_email") or "").strip():
                e["outreach_sender_email"] = sender_mail_for_log
            recipients_ever.add(_norm_email(em))
            _append_event({
                "ts": ts, "kind": "first_send", "to": em, "ok": True,
                "sent_log_id": mid, "error": "", "entry_key": e.get("entry_key"),
                "sender_email": sender_mail_for_log,
            })
            sent_n += 1
        else:
            err = (res.get("error") or res.get("raw") or "unknown")[:800]
            e["last_error"] = str(err)
            logger.warning("[outreach] Erstversand nok %s: %s", em, str(err)[:500])
            _append_event({
                "ts": _now_iso(), "kind": "first_send", "to": em, "ok": False,
                "sent_log_id": "", "error": str(err), "entry_key": e.get("entry_key"),
                "sender_email": sender_mail_for_log,
            })
            err_n += 1
        _pause_between_outreach_sends()
    return {
        "sent": sent_n,
        "errors": err_n,
        "skipped_unapproved": skip_unapproved,
        "skipped_enterprise": skip_enterprise,
        "skipped_invalid_domain": skip_invalid_domain,
        "skipped_empty_recipient": skip_empty_recipient,
        "skipped_duplicate_recipient": skip_duplicate_recipient,
        "skipped_no_sender": skip_no_sender,
        "send_cap": cap,
        "outreach_mailbox_mode": bool(use_mailboxes),
    }


def _followup_due_1(e: dict) -> bool:
    st = (e.get("outreach_stage") or "").strip()
    if st != "sent":
        return False
    fs = _parse_dt(e.get("first_sent_at") or "")
    if not fs:
        return False
    return datetime.now() >= fs + timedelta(days=FOLLOWUP_DAYS_1)


def _followup_due_2(e: dict) -> bool:
    st = (e.get("outreach_stage") or "").strip()
    if st != "followup_1":
        return False
    fs = _parse_dt(e.get("first_sent_at") or "")
    if not fs:
        return False
    return datetime.now() >= fs + timedelta(days=FOLLOWUP_DAYS_2)


def run_followups(
    state: dict[str, Any],
    script: Path,
    limit: int,
) -> dict[str, Any]:
    cap = _effective_outreach_send_cap(limit)
    sent_n = 0
    err_n = 0
    skip_enterprise = 0
    skip_invalid_domain = 0
    skip_empty_recipient = 0
    skip_followup_cap = 0
    by_lead = _leads_by_email_norm()
    senders = _outreach_sender_slots_from_env()
    use_mailboxes = len(senders) > 0
    counts_live = _outreach_counts_live_today(senders) if senders else {}

    for e in list(state.get("entries", [])):
        if sent_n >= cap:
            break
        rpl = (e.get("reply_status") or "none").strip()
        if rpl == "negative":
            continue
        if rpl != "none":
            continue
        if (e.get("inbound_last_text") or "").strip():
            continue
        if e.get("processed_inbound_ids"):
            continue
        if e.get("do_not_resend"):
            continue
        if _followup_due_1(e):
            em_fu = _norm_email((e.get("email") or "").strip())
            to_addr = (e.get("email") or "").strip()
            if not to_addr:
                skip_empty_recipient += 1
                e["last_error"] = "empty_recipient"
                continue
            ld_fu = by_lead.get(em_fu)
            e.update(_enrich_entry_from_lead(dict(e), ld_fu))
            _apply_hardened_outreach_copy(e, ld_fu)
            ent_block, ent_reason = _entry_enterprise_send_blocked(e, ld_fu)
            if ent_block:
                e["last_error"] = ent_reason[:800]
                skip_enterprise += 1
                logger.warning("[outreach] Skip Follow-up 1 (enterprise) %s", to_addr)
                continue
            ok_dom, dom_reason = _recipient_domain_mail_ready(to_addr)
            if not ok_dom:
                e["last_error"] = dom_reason[:800]
                skip_invalid_domain += 1
                logger.warning("[outreach] Skip Follow-up 1 (%s) %s", dom_reason, to_addr)
                continue
            subj = f"Re: {(e.get('first_email_subject') or '').strip()}"[:200]
            body = (e.get("followup_1_text") or "").strip()
            if not body:
                e["last_error"] = "followup_1 Text fehlt"
                err_n += 1
                continue

            sender_mail = ""
            if use_mailboxes:
                picked = _pick_followup_credentials(e, senders, counts_live)
                if not picked:
                    e["last_error"] = "followup_all_senders_at_daily_cap"
                    skip_followup_cap += 1
                    continue
                u, p, sender_mail = picked
                try:
                    slot_dict = _sender_slot_for_smtp_user(senders, u)
                    res = _send_email_subprocess(
                        script, to_addr, subj or "Kurze Nachfrage", body, u, p, slot_dict,
                    )
                except Exception as exc:  # noqa: BLE001
                    e["last_error"] = f"Exception: {exc}"[:500]
                    logger.exception("[outreach] Follow-up 1: %s", e.get("email"))
                    _append_event({
                        "ts": _now_iso(), "kind": "followup_1", "to": to_addr, "ok": False,
                        "sent_log_id": "", "error": str(exc)[:500], "entry_key": e.get("entry_key"),
                        "sender_email": sender_mail,
                    })
                    err_n += 1
                    _pause_between_outreach_sends()
                    continue
            else:
                try:
                    res = send_via_script(script, to_addr, subj or "Kurze Nachfrage", body)
                except Exception as exc:  # noqa: BLE001
                    e["last_error"] = f"Exception: {exc}"[:500]
                    logger.exception("[outreach] Follow-up 1: %s", e.get("email"))
                    sender_mail = (os.environ.get("IONOS_SMTP_USER") or "").strip()
                    _append_event({
                        "ts": _now_iso(), "kind": "followup_1", "to": to_addr, "ok": False,
                        "sent_log_id": "", "error": str(exc)[:500], "entry_key": e.get("entry_key"),
                        "sender_email": sender_mail,
                    })
                    err_n += 1
                    _pause_between_outreach_sends()
                    continue
                sender_mail = (res.get("from") or os.environ.get("IONOS_SMTP_USER") or "").strip()

            if res.get("ok") is True:
                ts = _now_iso()
                e["outreach_stage"] = "followup_1"
                e["last_contacted_at"] = ts
                fdt = _parse_dt(e.get("first_sent_at") or "") or datetime.now()
                e["next_followup_at"] = (fdt + timedelta(days=FOLLOWUP_DAYS_2)).replace(microsecond=0).isoformat()
                mid = _new_sent_id("fu1", e.get("email", ""))
                e["sent_message_id"] = e.get("sent_message_id", "") + f",{mid}"
                if use_mailboxes:
                    counts_live[sender_mail] = counts_live.get(sender_mail, 0) + 1
                _append_event({
                    "ts": ts, "kind": "followup_1", "to": to_addr, "ok": True,
                    "sent_log_id": mid, "error": "", "entry_key": e.get("entry_key"),
                    "sender_email": sender_mail,
                })
                sent_n += 1
            else:
                e["last_error"] = str(res.get("error", ""))[:500]
                _append_event({
                    "ts": _now_iso(), "kind": "followup_1", "to": to_addr, "ok": False,
                    "sent_log_id": "", "error": str(res.get("error", ""))[:500],
                    "entry_key": e.get("entry_key"), "sender_email": sender_mail,
                })
                err_n += 1
            _pause_between_outreach_sends()
        elif _followup_due_2(e):
            em_fu = _norm_email((e.get("email") or "").strip())
            to_addr = (e.get("email") or "").strip()
            if not to_addr:
                skip_empty_recipient += 1
                e["last_error"] = "empty_recipient"
                continue
            ld_fu = by_lead.get(em_fu)
            e.update(_enrich_entry_from_lead(dict(e), ld_fu))
            _apply_hardened_outreach_copy(e, ld_fu)
            ent_block, ent_reason = _entry_enterprise_send_blocked(e, ld_fu)
            if ent_block:
                e["last_error"] = ent_reason[:800]
                skip_enterprise += 1
                logger.warning("[outreach] Skip Follow-up 2 (enterprise) %s", to_addr)
                continue
            ok_dom, dom_reason = _recipient_domain_mail_ready(to_addr)
            if not ok_dom:
                e["last_error"] = dom_reason[:800]
                skip_invalid_domain += 1
                logger.warning("[outreach] Skip Follow-up 2 (%s) %s", dom_reason, to_addr)
                continue
            subj = f"Re: {(e.get('first_email_subject') or '').strip()}"[:200]
            body = (e.get("followup_2_text") or "").strip()
            if not body:
                e["last_error"] = "followup_2 Text fehlt"
                err_n += 1
                continue

            sender_mail = ""
            if use_mailboxes:
                picked = _pick_followup_credentials(e, senders, counts_live)
                if not picked:
                    e["last_error"] = "followup_all_senders_at_daily_cap"
                    skip_followup_cap += 1
                    continue
                u, p, sender_mail = picked
                try:
                    slot_dict = _sender_slot_for_smtp_user(senders, u)
                    res = _send_email_subprocess(
                        script, to_addr, subj or "Letzte kurze Nachfrage", body, u, p, slot_dict,
                    )
                except Exception as exc:  # noqa: BLE001
                    e["last_error"] = f"Exception: {exc}"[:500]
                    logger.exception("[outreach] Follow-up 2: %s", e.get("email"))
                    _append_event({
                        "ts": _now_iso(), "kind": "followup_2", "to": to_addr, "ok": False,
                        "sent_log_id": "", "error": str(exc)[:500], "entry_key": e.get("entry_key"),
                        "sender_email": sender_mail,
                    })
                    err_n += 1
                    _pause_between_outreach_sends()
                    continue
            else:
                try:
                    res = send_via_script(script, to_addr, subj or "Letzte kurze Nachfrage", body)
                except Exception as exc:  # noqa: BLE001
                    e["last_error"] = f"Exception: {exc}"[:500]
                    logger.exception("[outreach] Follow-up 2: %s", e.get("email"))
                    sender_mail = (os.environ.get("IONOS_SMTP_USER") or "").strip()
                    _append_event({
                        "ts": _now_iso(), "kind": "followup_2", "to": to_addr, "ok": False,
                        "sent_log_id": "", "error": str(exc)[:500], "entry_key": e.get("entry_key"),
                        "sender_email": sender_mail,
                    })
                    err_n += 1
                    _pause_between_outreach_sends()
                    continue
                sender_mail = (res.get("from") or os.environ.get("IONOS_SMTP_USER") or "").strip()

            if res.get("ok") is True:
                ts = _now_iso()
                e["outreach_stage"] = "followup_2"
                e["last_contacted_at"] = ts
                e["next_followup_at"] = ""
                mid = _new_sent_id("fu2", e.get("email", ""))
                e["sent_message_id"] = e.get("sent_message_id", "") + f",{mid}"
                if use_mailboxes:
                    counts_live[sender_mail] = counts_live.get(sender_mail, 0) + 1
                _append_event({
                    "ts": ts, "kind": "followup_2", "to": to_addr, "ok": True,
                    "sent_log_id": mid, "error": "", "entry_key": e.get("entry_key"),
                    "sender_email": sender_mail,
                })
                sent_n += 1
            else:
                e["last_error"] = str(res.get("error", ""))[:500]
                _append_event({
                    "ts": _now_iso(), "kind": "followup_2", "to": to_addr, "ok": False,
                    "sent_log_id": "", "error": str(res.get("error", ""))[:500],
                    "entry_key": e.get("entry_key"), "sender_email": sender_mail,
                })
                err_n += 1
            _pause_between_outreach_sends()
    return {
        "sent": sent_n,
        "errors": err_n,
        "skipped_enterprise": skip_enterprise,
        "skipped_invalid_domain": skip_invalid_domain,
        "skipped_empty_recipient": skip_empty_recipient,
        "skipped_followup_cap": skip_followup_cap,
        "send_cap": cap,
        "outreach_mailbox_mode": bool(use_mailboxes),
    }


def run_pipeline_cleanup_only() -> dict[str, Any]:
    """Nur Pipeline bereinigen (ohne leads.json)."""
    state = load_pipeline_state()
    prune_stats = prune_pipeline_junk(state)
    save_pipeline_state(state)
    return {
        "ok": True,
        "pipeline_entries": len(state.get("entries") or []),
        **prune_stats,
    }


def run_outreach_action(
    action: str,
    *,
    limit: int = 50,
    send_email_script: Optional[str] = None,
    approve_keys: str = "",
    reply_entry_key: str = "",
    reply_status: str = "",
    bypass_filters: bool = False,
) -> None:
    script = Path(send_email_script.strip()) if (send_email_script or "").strip() else SEND_EMAIL_SCRIPT_DEFAULT
    if action == "sync":
        r = sync_from_latest_run()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        if not r.get("ok"):
            sys.exit(2)
        return
    if action == "cleanup":
        r = run_pipeline_cleanup_only()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    if action == "preview":
        st = load_pipeline_state()
        r = run_preview(st, limit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    if action == "approve":
        st = load_pipeline_state()
        r = run_approve(st, limit=limit, approve_keys_csv=approve_keys, bypass_filters=bypass_filters)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    if action == "reply":
        if not (reply_entry_key or "").strip():
            print(json.dumps({"ok": False, "error": "--reply-entry-key fehlt"}, ensure_ascii=False))
            sys.exit(2)
        if not (reply_status or "").strip():
            print(json.dumps({"ok": False, "error": "--reply-status fehlt"}, ensure_ascii=False))
            sys.exit(2)
        st = load_pipeline_state()
        r = run_reply_update(st, reply_entry_key, reply_status)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        if not r.get("ok"):
            sys.exit(2)
        return
    if action == "handoffs":
        st = load_pipeline_state()
        export_hot_handoffs_files(st)
        p = Path(OUTPUT_DIR) / "hot_handoffs.json"
        data = _load_json(p, {})
        print(json.dumps({"ok": True, "file": str(p), "count": data.get("count", 0)}, ensure_ascii=False, indent=2))
        return
    if action == "preview-templates":
        st = load_pipeline_state()
        r = run_preview_templates(st)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    if action == "approve-templates":
        r = run_approve_templates()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    if action == "process-replies":
        st = load_pipeline_state()
        r = run_process_replies(st, script, limit)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        if not r.get("ok"):
            sys.exit(2)
        return
    if action == "reply-drafts":
        from modules.reply_mail_drafts import create_drafts_from_reply_queue

        r = create_drafts_from_reply_queue()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    if action == "status":
        st = load_pipeline_state()
        ent = st.get("entries") or []
        c = {x: 0 for x in STAGES}
        for e in ent:
            s = (e.get("outreach_stage") or "new").strip()
            c[s] = c.get(s, 0) + 1
        prev_elig = sum(1 for e in ent if _preview_eligible_entry(e))
        approved_ready = sum(
            1 for e in ent
            if e.get("approved_for_send") and (e.get("ready_to_send") or "").strip() == "yes"
            and (e.get("outreach_stage") or "").strip() in ("new", "drafted", "ready")
        )
        rq = _load_json(REPLY_QUEUE_JSON, {"items": []})
        qn = len(rq.get("items") or [])
        ap_cfg = load_autopilot_reply_config()
        vol = outreach_today_send_stats()
        print(json.dumps({
            "entries": len(ent),
            "by_stage": c,
            "workflow": {
                "preview_eligible": prev_elig,
                "approved_pending_send": approved_ready,
                "hot_or_positive": sum(
                    1 for e in ent
                    if (e.get("outreach_stage") or "").strip() == "hot"
                    or (e.get("reply_status") or "").strip() == "positive"
                ),
                "reply_queue_pending": qn,
                "reply_templates_approved": bool(ap_cfg.get("reply_templates_approved")),
            },
            "sent_today_total": vol["sent_today_total"],
            "sent_today_by_sender": vol["sent_today_by_sender"],
            "remaining_today_by_sender": vol["remaining_today_by_sender"],
            "pipeline_file": str(OUTREACH_PIPELINE_JSON),
        }, indent=2, ensure_ascii=False))
        return
    if action not in ("send", "followups"):
        print(json.dumps({"ok": False, "error": "unbekannte Aktion"}))
        sys.exit(2)
    st = load_pipeline_state()
    if action == "send":
        r = run_first_sends(st, script, limit)
    else:
        r = run_followups(st, script, limit)
    save_pipeline_state(st)
    r["ok"] = True
    r["script"] = str(script)
    print(json.dumps(r, ensure_ascii=False, indent=2))

