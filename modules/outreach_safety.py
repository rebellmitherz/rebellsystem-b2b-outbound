"""
Outreach-Personalisierung: Safety-First — keine Falschanreden, keine Legal-/Impressum-Namen.

Message-Modes:
  generic_safe   — keine sichere Person, keine sichere Firmenzeile → voll neutral
  company_aware  — Firma plausibel, Person unsicher
  person_aware   — Vor- und Nachname plausibel, nicht auf Blockliste
"""

from __future__ import annotations

import re
from typing import Any

# ── Person: niemals als „Herr/Frau …“ nutzen ───────────────────────────────

_PERSON_FORBIDDEN_RE = re.compile(
    r"\b("
    r"handelsregister|unternehmensregister|amtsgericht|registergericht|"
    r"umsatzsteuer|ust[-\s]?id|steuer[-\s]?nr|steuernummer|w[-\s]?id|"
    r"vertreten\s+durch|vertretungsberechtigt|geschäftsführ|geschaeftsfuehr|"
    r"postfach|pf\.?\s*\d|"
    r"\bhra\b|\bhrb\b|\bgmbh\b|\bag\b|\bug\b|\bkg\b|\bohg\b|"
    r"datenschutz|impressum|kontaktformular|"
    r"\bdritter\b|\bzweiter\b|\berster\b|\bvierter\b|"
    r"straße|strasse|str\.|weg\b|platz\b|allee\b|\bnr\.?\s*\d|"
    r"telefon|tel\.|fax|e-?mail|info@|"
    r"team\b|office\b|sekretariat|zentral\b|webseite[n]?\b|"
    r"verkaufsauftrag|inhalte\s+umgehend|umgehend\s+entfernen|"
    r"copyright|all\s+rights|alle\s+rechte|haftung|disclaimer"
    r")\b",
    re.I,
)

_PERSON_JUNK_TOKENS = frozenset({
    "handelsregister", "register", "amtsgericht", "registergericht", "umsatzsteuer",
    "ust", "id", "steuer", "steuernummer", "vertreten", "dritter", "zweiter", "erster",
    "str", "str.", "nr", "nr.", "no", "tel", "fax", "mail", "email", "e-mail",
    "postfach", "impressum", "datenschutz", "kontakt", "team", "office", "sekretariat",
    "herr", "frau", "dr", "prof", "ing", "mba",
    "geschäftsführer", "geschaeftsfuehrer", "inhaber", "gesellschafter", "vorstand",
    "third", "second", "first", "street", "st",
    # Geschäftstyp-Begriffe (kein Personenname)
    "autohaus", "autowerkstatt", "kanzlei", "praxis", "werkstatt",
    "büro", "buero", "studio", "atelier", "agentur", "beratung",
    "webseite", "webseiten", "website", "homepage", "internet",
    "verkaufsauftrag", "kaufauftrag", "mietauftrag",
    "haftung", "haftpflicht", "copyright",
    "auftrag", "auftraggeber",
    # Deutsche Pronomen / Artikel als "Vorname" → kein Personenname
    "ihre", "ihr", "sein", "seine", "seiner", "seinen",
    "unser", "unsere", "euer", "eure",
    "wir", "sie", "er", "es",
    # Abstrakte Nomen die kein Personenname sind
    "energiekosten", "energieverbrauch", "energiepreis",
    "personalanliegen", "mitarbeiteranliegen",
    "dienstleistungen", "anforderungen", "voraussetzungen",
    "leistungen", "angebote", "infos", "neuigkeiten",
    "registerei",
    # Mengenangaben / Adjektive als Vornamen
    "mehr", "alle", "viele", "weitere", "beste", "neue",
    # Grafik / Bildwörter
    "grafiken", "bilder", "fotos", "illustrationen",
    # Rechtliches
    "eintrag", "eingetragen",
})

# Suffixe, die fast ausschließlich Ortsnamen markieren (Saarland/D-Süd/D-West).
# Wird nur als Zusatz-Heuristik benutzt, NICHT als alleiniger Treffer für Eigennamen
# (es gibt seltene Familiennamen mit ähnlichen Endungen) — kombiniert mit Stadtbezug.
_PLACE_LIKE_SUFFIXES = (
    "wies", "weiler", "hausen", "kirchen", "ingen", "heim", "bach", "burg",
    "stadt", "felden", "felde", "feld", "tal", "bruck", "brücken", "bruecken",
    "hofen", "rode", "büttel", "buettel", "stein",
    # Straßen-/Wegnamen als letztes Token sind kein Familienname
    "weg", "gasse", "allee", "mühle", "muehle",
)

_BAD_SINGLE_TOKEN_NAME = frozenset({
    "inhaber", "geschäftsführer", "geschaeftsfuehrer", "vertretung", "kontakt",
})

_TITLE_PREFIXES = frozenset({
    "dr", "prof", "med", "dipl", "ing", "mba", "msc", "bsc", "mr", "mrs", "ms",
})

_FEMALE_FIRST = frozenset({
    "anna", "alice", "alexandra", "andrea", "angela", "anja", "julia", "sandra",
    "petra", "nicole", "melanie", "stefanie", "katharina", "katja", "sabine",
    "claudia", "birgit", "heike", "martina", "monika", "susanne", "vera",
    "helene", "helena", "lisa", "laura", "lena", "marie", "maria", "marion",
    "kerstin", "ute", "ulrike", "barbara", "christina", "christine", "elke",
    "eva", "franziska", "gabriele", "gabi", "iris", "jana", "janine", "jasmin",
    "jennifer", "jessica", "johanna", "karin", "kathrin", "lara", "leonie",
    "lina", "luisa", "manuela", "michaela", "miriam", "nadine", "natalie",
    "nina", "patricia", "rebecca", "regina", "renate", "ricarda", "rita",
    "sabrina", "sara", "sarah", "silke", "silvia", "simone", "sophia", "sophie",
    "stefanie", "steffi", "tanja", "tina", "ursula", "verena", "vivian", "yvonne",
})


def _looks_like_place_token(tok: str, city_hints: set[str] | None = None) -> bool:
    """Heuristik: token ist (vermutlich) ein Ortsname, nicht ein Familienname."""
    t = tok.strip(" ,.-–—").lower().rstrip(".")
    if not t or len(t) < 4:
        return False
    if city_hints and t in city_hints:
        return True
    # explizite Ortssuffixe (vorsichtig — nur als Zusatzcheck)
    return any(t.endswith(suf) for suf in _PLACE_LIKE_SUFFIXES)


def _person_tokens(s: str) -> list[str]:
    return [t.strip(" ,.;:-–—") for t in re.split(r"\s+", s) if t.strip()]


_TOKEN_SUBSTRING_BAN = (
    # Registertext-Fragmente — auch als Abkürzungen/Trunkierungen
    "register", "gericht", "umsatzsteuer", "ust-id", "ustid", "steuer",
    "handelsreg", "handelsregi", "handelsregisternr",  # fängt "Handelsreg" als Abkürzung
    "ddg", "agb", "verkaufsauftrag", "kaufauftrag",
    "haftung", "datenschutz", "impressum", "vertretungsbe",
    "rechtsform", "anschrift", "rufnummer", "telefonnr", "telefon",
    "webseite", "website", "homepage",
    "rechtsanwalt", "geschaeftsfuehrer", "geschäftsführer",
    # Service-/Marketing-Wörter die kein Personenname sind
    "energiekosten", "energiepreis", "energieverb",  # "Energiekosten", "Energieverbrauch"
    "personalanliegen", "personalbetr", "mitarbeiteranliegen",
    "kundenbetreu", "kundenbetreuung", "kundendienst",  # "Kundenbetreuung"
    "dienstleistung", "beratungsleistung",
    "anforderungen", "voraussetzungen",
    # Registerei / Register-Varianten
    "registerei", "registernr", "registernum",
    # Eingetragen in / Registered in
    "eingetragen", "eingetragene", "eintrag", "eintr",
    # Straßen-Fragmente als Token-Substring
    "landstr", "bundesstr", "hauptstr",
)


def _token_has_banned_substring(tok: str) -> bool:
    t = tok.lower().rstrip(".")
    return any(b in t for b in _TOKEN_SUBSTRING_BAN)


def outreach_person_is_safe(
    raw_name: str,
    *,
    city_hints: set[str] | None = None,
) -> tuple[bool, str]:
    """
    True nur bei plausibler natürlicher Person (2+ Tokens, kein Legal-Müll, kein Orts-Surname).

    city_hints: zusätzliche Stadtnamen aus Lead-Kontext (z. B. {'saarlouis','bous'}),
    die dann nie Vor-/Nachnamen sein dürfen.
    """
    s = re.sub(r"\s+", " ", (raw_name or "").strip())
    if not s:
        return False, "leer"
    if _PERSON_FORBIDDEN_RE.search(s):
        return False, "rechtlicher/impressums-artiger Text"
    if "@" in s:
        return False, "E-Mail im Namensfeld"
    if len(s) > 80:
        return False, "zu lang für Personenname"
    toks = _person_tokens(s)
    if len(toks) < 2:
        return False, "kein Vor- und Nachname"
    low_toks = {x.lower().rstrip(".") for x in toks}
    if _PERSON_JUNK_TOKENS.intersection(low_toks):
        return False, "Token wie Register/Straße/Steuer"
    # Vorname darf kein Pronomen/Artikel sein — auch wenn großgeschrieben
    _PRONOUN_FIRST_WORDS = frozenset({
        "ihre", "ihr", "sein", "seine", "unser", "unsere",
        "wir", "sie", "er", "es", "euer", "eure", "der", "die", "das",
    })
    if toks[0].lower().rstrip(".") in _PRONOUN_FIRST_WORDS:
        return False, "Vorname ist Pronomen/Artikel"
    # Substring-Ban (z. B. "Registerangaben" enthält "register")
    if any(_token_has_banned_substring(t) for t in toks):
        return False, "Token enthält Register-/Steuer-/Impressum-Substring"
    # Trunkierte Rechtsform-Suffixe ("Gmb" für GmbH, "Ko" für KG …)
    truncated_forms = {"gmb", "ohg", "kg", "ag", "ug", "se", "gesell"}
    if any(t.lower().rstrip(".") in truncated_forms for t in toks):
        return False, "Token wie trunkierte Rechtsform"
    if any(len(t) == 1 and t.isalpha() for t in toks):
        return False, "Initialenfragment"
    if toks[-1].lower().rstrip(".") in _BAD_SINGLE_TOKEN_NAME:
        return False, "Nachname wirkt wie Rolle/Funktion"
    if any(re.search(r"\d", t) for t in toks):
        return False, "Ziffer im Namen"
    vor, nach = toks[0], toks[-1]
    if len(vor) < 2 or len(nach) < 2:
        return False, "Vor- oder Nachname zu kurz"
    if len(nach) < 3:
        return False, "Nachname zu kurz (<3) — wahrscheinlich Fragment"
    if not vor[0].isalpha() or not nach[0].isalpha():
        return False, "kein alphabetischer Name"
    if vor.lower() in _TITLE_PREFIXES:
        return False, "nur Titelfragment"
    # Bei 3+ Tokens: das letzte Token darf nicht offensichtlich ein Ortsname sein
    if len(toks) >= 3 and _looks_like_place_token(nach, city_hints):
        return False, f"Letztes Token wirkt wie Ortsname ({nach})"
    if city_hints and nach.lower().rstrip(".") in city_hints:
        return False, f"Nachname identisch mit Stadt ({nach})"
    if city_hints and vor.lower().rstrip(".") in city_hints:
        return False, f"Vorname identisch mit Stadt ({vor})"
    return True, ""


def _city_hints_from_lead(lead: dict[str, Any] | None) -> set[str]:
    if not lead:
        return set()
    out: set[str] = set()
    for k in ("city", "city_detected", "search_city", "target_city"):
        v = (lead.get(k) or "").strip().lower()
        if v:
            # auch erste Wortbestandteile (z. B. „Überherrn-Felsberg“ → 'überherrn','felsberg')
            for part in re.split(r"[\s\-/,]+", v):
                if part:
                    out.add(part)
    return out


def resolve_safe_contact_name(entry: dict[str, Any], lead: dict[str, Any] | None) -> str:
    """Pipeline-Entry + Lead: erster sicherer Personenname (mit Stadt-Kontext)."""
    hints = _city_hints_from_lead(lead) | _city_hints_from_lead(entry)
    if lead:
        s = safe_contact_name_for_personalization(lead)
        if s and outreach_person_is_safe(s, city_hints=hints)[0]:
            return s
    for src in (entry, lead):
        if not src:
            continue
        for key in (
            "contact_person_clean",
            "contact_full_name",
            "managing_director",
            "contact_name",
        ):
            raw = (src.get(key) or "").strip()
            if raw and outreach_person_is_safe(raw, city_hints=hints)[0]:
                return raw
    return ""


def classify_mode_from_sources(entry: dict[str, Any], lead: dict[str, Any] | None) -> str:
    """Modus unter Nutzung von resolve_safe_contact + Firmenfeldern aus entry/lead."""
    if resolve_safe_contact_name(entry, lead):
        return MESSAGE_MODE_PERSON
    base: dict[str, Any] = dict(entry or {})
    if lead:
        base.update(lead)
    return classify_outreach_message_mode(base if base else None)


def safe_contact_name_for_personalization(lead: dict[str, Any]) -> str:
    """
    Bevorzugt contact_person_clean nur wenn valid und ortskontext-sicher; sonst leer.
    """
    if (lead.get("contact_person_quality") or "").strip() != "valid":
        return ""
    cand = (lead.get("contact_person_clean") or "").strip()
    hints = _city_hints_from_lead(lead)
    ok, _ = outreach_person_is_safe(cand, city_hints=hints)
    return cand if ok else ""


def safe_contact_first_name(lead: dict[str, Any]) -> str:
    """Reiner Vorname wenn safe, sonst leer."""
    full = safe_contact_name_for_personalization(lead)
    if not full:
        return ""
    parts = _person_tokens(full)
    while parts and parts[0].rstrip(".").lower() in _TITLE_PREFIXES:
        parts = parts[1:]
    if not parts:
        return ""
    fn = parts[0].strip(".")
    if len(fn) < 3 or not fn.isalpha():
        return ""
    return fn


_COMPANY_BAD_PHRASES = (
    "inhalte umgehend entfernen", "umgehend entfernen", "verkaufsauftrag",
    "all rights reserved", "alle rechte vorbehalten", "copyright",
    "lorem ipsum", "wird geladen", "page not found", "nicht gefunden",
    "cookies akzeptieren", "datenschutzerklärung", "datenschutzerklaerung",
    "wartungsmodus", "in wartung",
)

_COMPANY_SEO_PREFIX_RE = re.compile(
    r"^(ihr|euer|euer\s+kompetenter|der|die|das|unser|unsere|ihre|kompetente[r]?)\s+"
    r"(immobilienmakler|makler|partner|berater|spezialist|experte|dienstleister|"
    r"agentur|softwarehaus|systemhaus|it[-\s]?dienstleister)",
    re.I,
)


def company_anchor_is_safe(display: str) -> tuple[bool, str]:
    d = re.sub(r"\s+", " ", (display or "").strip())
    if not d or len(d) < 3:
        return False, "leer"
    low = d.lower()
    if _PERSON_FORBIDDEN_RE.search(d) and "gmbh" not in low and " ag" not in low:
        return False, "Firmenzeile wirkt wie Legal-Text"
    junk = {
        "startseite", "homepage", "willkommen", "impressum", "datenschutz",
        "kontakt", "ihr unternehmen", "unser unternehmen", "blog", "news",
    }
    if low in junk:
        return False, "generische Seitenbezeichnung"
    # Suchergebnis-Listentitel: "Die 10 Besten für ..." / "Top 5 ..."
    if re.match(r"^(die|der|das)\s+\d+\b", low):
        return False, "Suchergebnis-Listentitel (Die N ...)"
    if re.match(r"^top\s+\d+\b", low, re.I):
        return False, "Suchergebnis-Listentitel (Top N ...)"
    if "besten für" in low or "beste für" in low:
        return False, "Suchergebnis-Phrase (Besten für ...)"
    if any(p in low for p in _COMPANY_BAD_PHRASES):
        return False, "Firmenzeile enthält Rechts-/Müll-Phrase"
    if len(d) > 90:
        return False, "Firmenname zu lang/SEO"
    # Mehrere Bindestriche oder „&“ + Branchenkeyword + Stadtnamen → SEO-Title
    seo_keywords = ("immobilienmakler", "makler", "agentur", "dienstleister", "saarland", "saarlouis")
    if d.count(" -") >= 1 and sum(1 for kw in seo_keywords if kw in low) >= 2:
        return False, "Firmenzeile sieht aus wie SEO-Title (mehrere Branchen-/Ortskeywords)"
    return True, ""


def _strip_seo_prefix(s: str) -> str:
    return _COMPANY_SEO_PREFIX_RE.sub("", s).strip(" -–—,;:")


def safe_company_anchor_for_personalization(lead: dict[str, Any] | None) -> str:
    """
    Liefert eine sendefähige, kurze Firmenzeile — oder leeren String, wenn das
    company_name-Feld nur SEO/Legal/Müll ist.

    Reihenfolge: company_name_clean (wenn 'valid'/'good'), dann company_name (gefiltert).
    """
    if not lead:
        return ""
    cq = (lead.get("company_name_quality") or "").strip().lower()
    if cq == "weak":
        return ""
    candidates = [
        (lead.get("company_name_clean") or "").strip(),
        (lead.get("company_name") or "").strip(),
    ]
    # SEO-Bullet-Zeichen: "Titel ᐅ Untertitel" → nur Titel
    _SEO_BULLETS_RE = re.compile(r"\s*[ᐅ›»•►▶|]\s*")

    for raw in candidates:
        if not raw:
            continue
        s = re.sub(r"\s+", " ", raw)
        # offensichtlicher Müll: Satzpunkte mehrfach, Großbuchstaben-Sätze
        if "." in s and len(s.split()) > 6:
            continue
        if re.search(r"[!?]", s):
            continue
        # SEO-Bullet-Zeichen (ᐅ, ›, », •, |) → nur Teil vor dem Bullet verwenden
        bullet_m = _SEO_BULLETS_RE.search(s)
        if bullet_m:
            s = s[: bullet_m.start()].strip(" ,;:-")
            if not s or len(s) < 3:
                continue
        ok, _ = company_anchor_is_safe(s)
        if not ok:
            continue
        if len(s) > 65:
            continue
        # SEO-Auftakte abstreifen ("Ihr Immobilienmakler in ..." → "")
        cleaned = _strip_seo_prefix(s)
        if not cleaned or len(cleaned) < 3:
            continue
        # Colon-Subtitel kürzen: "Keyword Stadt: Professionelle Services für X" → "Keyword Stadt"
        if ":" in cleaned:
            pre, _, post = cleaned.partition(":")
            pre = pre.strip()
            if len(post.split()) >= 3 and len(pre) >= 3:
                cleaned = pre
        # Bindestrich-Subtitel kürzen: "B2B-IT GmbH - Ihr Systemhaus in ..." → "B2B-IT GmbH"
        dash_m = re.search(r"\s+-\s+[A-ZÜÖÄ]", cleaned)
        if dash_m:
            pre = cleaned[: dash_m.start()].strip()
            if len(pre) >= 3:
                cleaned = pre
        # Nach dem Strippen: Anker darf nicht mit Präposition beginnen
        _LEADING_PREP = re.compile(r"^(für|in|bei|an|zu|von|nach|um|über|aus)\b", re.I)
        if _LEADING_PREP.match(cleaned):
            continue
        # Wenn nach dem Strippen nur noch Branchen-Generikum übrig bleibt: skip
        generic_only = {
            "saarlouis", "in saarlouis", "saarland",
            "immobilienmakler", "agentur", "marketingagentur",
            "it-dienstleister", "it dienstleister",
        }
        if cleaned.lower().strip() in generic_only:
            continue
        return cleaned
    return ""


# ── Branchenerkennung (für Hooks/Subjects) ─────────────────────────────────

INDUSTRY_IT = "it_services"
INDUSTRY_AGENCY = "marketing_agency"
INDUSTRY_REALTOR = "real_estate"
INDUSTRY_GENERIC = "generic_b2b"


def detect_industry_bucket(lead: dict[str, Any] | None) -> str:
    if not lead:
        return INDUSTRY_GENERIC
    raw = " ".join(
        str(lead.get(k) or "") for k in (
            "industry", "industry_group", "target_client_type",
            "company_name", "company_name_clean", "search_query",
        )
    ).lower()
    if any(k in raw for k in ("immobilien", "makler", "real estate", "realtor")):
        return INDUSTRY_REALTOR
    if any(k in raw for k in ("marketingagentur", "marketing-agentur", "werbeagentur",
                              "social-media-agentur", "performance agentur",
                              "agentur ", " agentur", "agency")):
        return INDUSTRY_AGENCY
    if any(k in raw for k in ("it-dienst", "it dienst", "softwarehaus", "systemhaus",
                              "managed service", "msp", "saas", "software ", "it ",
                              " it-", "it-beratung")) or re.search(r"(^|[^a-z])it([^a-z]|$)", raw):
        return INDUSTRY_IT
    return INDUSTRY_GENERIC


def industry_hook_one_liner(industry_bucket: str) -> str:
    """Ein Satz, der unabhängig von Person/Firma trägt — Pain-First, B2B-konkret."""
    if industry_bucket == INDUSTRY_IT:
        return (
            "Bei IT-Häusern ist Pipeline für MSP-/Projekt-Geschäft selten das Problem der Technik, "
            "sondern der Vorqualifizierung — wir filtern Erstgespräche so vor, dass IT-Leitung/Einkauf "
            "fachlich schon andocken können."
        )
    if industry_bucket == INDUSTRY_AGENCY:
        return (
            "Agenturen brauchen planbar Pitches und Retainer — nicht „mehr Leads“, sondern "
            "Erstgespräche mit Entscheidern, deren Budget und Reifegrad zur Positionierung passt."
        )
    if industry_bucket == INDUSTRY_REALTOR:
        return (
            "Im Maklergeschäft entscheidet die Eigentümer-Pipeline — Bewertungstermine mit "
            "verkaufsbereiten Eigentümern, nicht weitere Plattform-Leads."
        )
    return (
        "Im B2B sind die meisten Akquise-Probleme keine Lead-Probleme, sondern "
        "Erstgesprächs-Qualität: zu viele Anbieter, zu wenig Klarheit, wer wirklich liefert."
    )


def industry_subject_options(industry_bucket: str, anchor: str) -> list[str]:
    """Subject-Linien — anchor ist entweder safe Firmenname ODER leer."""
    a = anchor.strip().rstrip(":–-—")
    # Doppel-Doppelpunkte vermeiden, falls Firmenstring selbst Doppelpunkt hat
    a = a.split(":", 1)[0].strip() if ":" in a else a
    if industry_bucket == INDUSTRY_IT:
        opts = [
            "Kurz: passende Erstgespräche mit IT-Entscheidern",
            "10 Min — Vorqualifizierung für IT-Pipeline",
        ]
        if a:
            opts.insert(0, f"Kurz zu {a}: Erstgespräche mit IT-Entscheidern")
    elif industry_bucket == INDUSTRY_AGENCY:
        opts = [
            "Kurz: planbare Pitches statt Lead-Volumen",
            "10 Min — Erstgespräche, die zur Positionierung passen",
        ]
        if a:
            opts.insert(0, f"Kurz zu {a}: Pitches mit Budget-Fit")
    elif industry_bucket == INDUSTRY_REALTOR:
        opts = [
            "Kurz: Eigentümer-Termine statt Portal-Leads",
            "10 Min — verkaufsbereite Eigentümer ansprechen",
        ]
        if a:
            opts.insert(0, f"Kurz zu {a}: planbare Bewertungstermine")
    else:
        opts = [
            "Kurz: passende B2B-Erstgespräche",
            "10 Min — sortierte Akquise statt Spam",
        ]
        if a:
            opts.insert(0, f"Kurz zu {a}: passende Erstgespräche")
    return opts[:3]


MESSAGE_MODE_GENERIC = "generic_safe"
MESSAGE_MODE_COMPANY = "company_aware"
MESSAGE_MODE_PERSON = "person_aware"


def classify_outreach_message_mode(lead: dict[str, Any] | None) -> str:
    """generic_safe | company_aware | person_aware"""
    if not lead:
        return MESSAGE_MODE_GENERIC
    person = safe_contact_name_for_personalization(lead)
    if person:
        return MESSAGE_MODE_PERSON
    if safe_company_anchor_for_personalization(lead):
        return MESSAGE_MODE_COMPANY
    return MESSAGE_MODE_GENERIC


def salutation_line_for_mode(mode: str, contact_name_safe: str, company_safe: str) -> str:
    """Anredezeile für E-Mail-Body (immer sendefähig)."""
    if mode == MESSAGE_MODE_PERSON and contact_name_safe:
        return salutation_herr_frau_line(contact_name_safe)
    if mode == MESSAGE_MODE_COMPANY and company_safe:
        return team_greeting_with_company(company_safe)
    return "Guten Tag,"


def salutation_herr_frau_line(contact_name_safe: str) -> str:
    """Nur aufrufen wenn outreach_person_is_safe True."""
    raw = (contact_name_safe or "").strip()
    toks = [t.strip(" ,.-–—") for t in re.split(r"\s+", raw) if t.strip()]
    while toks and toks[0].rstrip(".").lower() in _TITLE_PREFIXES:
        toks = toks[1:]
    if len(toks) < 2:
        return "Guten Tag,"
    vor, nach = toks[0], toks[-1]
    hf = "Frau" if vor.rstrip(".").lower() in _FEMALE_FIRST else "Herr"
    return f"Guten Tag {hf} {nach},"


def team_greeting_with_company(company_safe: str) -> str:
    c = (company_safe or "").strip()
    if not c:
        return "Guten Tag,"
    return f"Guten Tag an das Team von {c},"


def build_industry_context_paragraph(lead: dict[str, Any] | None) -> str:
    """
    1–2 Sätze Branchenbezug — nur wenn Zielgruppe erkennbar; sonst neutraler B2B-Satz.
    Konkreter, mit echten Pains der Branche.
    """
    bucket = detect_industry_bucket(lead)
    if bucket == INDUSTRY_IT:
        return (
            "Bei IT-Häusern bremst nicht die Liefer-Kapazität, sondern die Erstgesprächs-Qualität: "
            "RFP-Spam, IT-Leiter mit zu wenig Zeit, Einkauf, der vorab schon priorisiert. "
            "Wer hier nicht selektiert, verbrennt Pre-Sales-Zeit ohne Pipeline-Effekt.\n\n"
        )
    if bucket == INDUSTRY_AGENCY:
        _agency_words = ("agentur", "agency", "werbeagentur", "marketingagentur", "digitalagentur")
        _company_name_raw = ""
        for _k in ("company_safe", "company_name_clean", "company_name", "outreach_display_company"):
            _v = str((lead or {}).get(_k) or "").strip()
            if _v:
                _company_name_raw = _v
                break
        _company_lower = _company_name_raw.lower()
        if any(w in _company_lower for w in _agency_words):
            return (
                "Agenturen leben von Pitches mit Budget-Fit, nicht von Lead-Volumen. "
                "Generische Kaltmails verwässern die Positionierung, ziehen die falschen Anfragen an "
                "und kosten Strategen-Stunden, die in der Auslastungsplanung fehlen.\n\n"
            )
        return (
            "Im B2B scheitert Akquise selten an reiner Lead-Menge, sondern an der Frage, ob Angebot, "
            "Entscheider und Zeitpunkt zusammenpassen. Genau dort wird Vorqualifizierung wichtiger "
            "als zusätzlicher Kontakt-Output.\n\n"
        )
    if bucket == INDUSTRY_REALTOR:
        return (
            "Im Maklergeschäft hängt der Umsatz an der Eigentümer-Pipeline: "
            "verkaufsbereite Eigentümer, planbare Bewertungstermine, klare Vorqualifizierung — "
            "nicht weitere Portal-Leads, die im Schnitt nichts bringen.\n\n"
        )
    return (
        "Im B2B sind die meisten Akquise-Probleme keine Lead-Probleme, sondern "
        "Erstgesprächs-Probleme: zu viele Anbieter, zu wenig Klarheit, wer wirklich liefert.\n\n"
    )


def safe_company_display_for_short_texts(
    canonical_display: str,
    lead: dict[str, Any] | None,
) -> str:
    """Für WhatsApp/Kurztext: nur kurze, geprüfte Firmenzeile (nach Strict-Anchor) oder leer."""
    # Strikter Anker zuerst — schließt SEO-Titel/Müll wirklich aus.
    strict = safe_company_anchor_for_personalization(lead)
    if strict and len(strict) <= 55:
        return strict
    for cand in (
        (canonical_display or "").strip(),
        (lead.get("company_name_clean") or "").strip() if lead else "",
        (lead.get("company_name") or "").strip() if lead else "",
    ):
        if not cand:
            continue
        ok, _ = company_anchor_is_safe(cand)
        if ok and len(cand) <= 55:
            stripped = _strip_seo_prefix(cand)
            if stripped and len(stripped) >= 3:
                return stripped
    return ""


def build_whatsapp_short_safe(lead: dict[str, Any], canonical_company: str) -> str:
    """Kurznachricht ohne kaputte Anker. Branche fließt subtil ein, kein Offer-Dump."""
    mode = classify_outreach_message_mode(lead)
    co = safe_company_display_for_short_texts(canonical_company, lead)
    fn = safe_contact_first_name(lead) if lead else ""
    bucket = detect_industry_bucket(lead)
    pitch = {
        INDUSTRY_IT: "wie wir Erstgespräche für IT-Häuser vorsortieren (kein Tool-Spam)",
        INDUSTRY_AGENCY: "wie wir Pitches mit Budget-Fit vorsortieren (statt Lead-Volumen)",
        INDUSTRY_REALTOR: "wie wir Eigentümer-Termine vorqualifizieren (statt Portal-Leads)",
        INDUSTRY_GENERIC: "wie wir passende Akquise-Setups vorsortieren",
    }[bucket]
    if mode == MESSAGE_MODE_PERSON and fn:
        return (
            f"Hallo {fn}, kurze sachliche Frage: "
            f"Darf ich Ihnen in einem Satz skizzieren, {pitch}? "
            "Antwort genügt – falls kein Thema, hake ich nicht nach."
        )
    if mode == MESSAGE_MODE_COMPANY and co:
        return (
            f"Guten Tag, kurze Frage zu {co}: "
            f"Darf ich kurz skizzieren, {pitch}? "
            "Falls gerade kein Thema – kurz Bescheid genügt."
        )
    return (
        "Guten Tag, kurze sachliche Frage: "
        f"Darf ich kurz skizzieren, {pitch}? "
        "Falls gerade kein Thema – kurz Bescheid genügt."
    )


def compose_hardened_email_body(
    *,
    mode: str,
    salutation: str,
    industry_block: str,
    company_safe: str,
    personalization_hook: str = "",
) -> str:
    """Hauptteil Erstmail — connector positioning, mode-abhängige Einleitung."""
    team_sal = salutation.lower().startswith("guten tag an das team")
    if team_sal:
        opener = "ich hätte eine kurze fachliche Frage. "
    elif mode == MESSAGE_MODE_COMPANY and company_safe:
        opener = f"ich schreibe Ihnen bezüglich {company_safe}. "
    elif mode == MESSAGE_MODE_PERSON:
        opener = "ich hätte eine kurze fachliche Frage. "
    else:
        opener = "ich hätte eine kurze fachliche Frage. "

    core = (
        f"{opener}\n"
        f"{(personalization_hook.strip() + chr(10) + chr(10)) if personalization_hook.strip() else ''}"
        f"{industry_block}"
        "Wie viele Akquise-Angebote bekommen Sie aktuell — und wie viele davon würden Sie wirklich testen?\n\n"
        "Viele Unternehmen sprechen mit mir genau deshalb: zu viele Anbieter, zu wenig Klarheit, wer tatsächlich liefert.\n\n"
        "Ich arbeite mit ausgewählten Akquise-Teams, die nachweislich Termine bringen — "
        "und filtere, wer davon zu wem passt.\n\n"
        "Soll ich Ihnen kurz sagen, welches Setup bei Ihnen aktuell am ehesten funktionieren würde?\n\n"
        "Viele Grüße"
    )
    return f"{salutation}\n\n{core}".strip()


def outreach_mode_label_de(mode: str) -> str:
    return {
        MESSAGE_MODE_GENERIC: "neutral (keine Person, eingeschränkte Firmenzeile)",
        MESSAGE_MODE_COMPANY: "firmenbezogen (Person unsicher)",
        MESSAGE_MODE_PERSON: "personenbezogen (Name geprüft)",
    }.get(mode, mode)
