from __future__ import annotations

from typing import Iterable

_ALLOWED_RELEVANCE_FOCUS = {"broad", "target_industry"}

_SALES_HIRING_TEMPLATES = [
    'site:stepstone.de/stellenangebote-- {base} "{role}" "m/w/d"',
    'site:stepstone.de/jobs {base} "{role}" "m/w/d"',
    'site:indeed.com/viewjob {base} "{role}"',
    'site:de.indeed.com/viewjob {base} "{role}"',
    'site:jobs.lever.co {base} Sales',
    'site:greenhouse.io {base} Sales',
    'site:join.com {base} Sales Manager',
    'site:personio.de {base} Vertrieb',
    'site:arbeitnow.com {base} Sales Manager',
    'site:talent.com/jobs {base} "{role}"',
    '{base} "{role}" Karriere Bewerbung -"Jobs & Stellenangebote" -"Stellenangebote in" -"Finden Sie" -"alle Jobs" -"Suchergebnisse"',
    '{base} "{role}" "m/w/d" Karriere -"Jobs & Stellenangebote" -"Stellenangebote in" -"Suchergebnisse"',
    '{base} "{role}" gesucht "m/w/d" -stepstone.de/stellenangebote-- -indeed.com',
]

_GROWTH_TEMPLATES = [
    '{base} "sucht Verstärkung" Karriere -"Jobs & Stellenangebote" -"Stellenangebote in" -"Suchergebnisse"',
    '{base} "Team wächst" Karriere -"Jobs & Stellenangebote" -"Suchergebnisse"',
    '{base} "wir suchen" Karriere -"Jobs & Stellenangebote" -"Stellenangebote in"',
    '{base} "München" "Karriere" -"Jobs & Stellenangebote" -"Suchergebnisse"',
    'site:personio.de {base}',
    'site:join.com {base}',
    'site:jobs.lever.co {base}',
    'site:greenhouse.io {base}',
    '{base} "Karriere" "Bewerbung" -"Jobs & Stellenangebote" -"Stellenangebote in" -"Suchergebnisse"',
    '{base} "sucht" "Karriere" Mitarbeiter -"Jobs & Stellenangebote" -"Stellenangebote in"',
]

_ROLES_PER_SIGNAL: dict[str, list[str]] = {
    "sales_hiring": [
        "Sales Manager",
        "Business Development",
        "Account Manager",
        "Vertrieb",
        "Neukundenakquise",
    ],
    "growth_expansion": [],
}

_TARGET_INDUSTRY_VARIANTS = [
    "Marketingagentur",
    "Werbeagentur",
    "Online Marketing Agentur",
    "Performance Marketing Agentur",
    "SEO Agentur",
    "Social Media Agentur",
]

_NEGATIVE_KEYWORDS = [
    "-Bosch",
    "-Diageo",
    "-Hamburg",
    "-Berlin",
    "-Ingolstadt",
    "-Remote",
    "-Deutschlandweit",
]

_TARGET_SALES_HIRING_TEMPLATES = [
    'site:stepstone.de/stellenangebote-- "{agentur}" "{city}" "{role}" "m/w/d"',
    'site:stepstone.de/jobs "{agentur}" "{city}" "{role}"',
    'site:personio.de "{agentur}" "{city}" "{role}"',
    'site:join.com "{agentur}" "{city}" "{role}"',
    'site:indeed.com/viewjob "{agentur}" "{city}" "{role}"',
    'site:de.indeed.com/viewjob "{agentur}" "{city}" "{role}"',
    'site:jobs.lever.co "{agentur}" "{city}" Sales',
    'site:greenhouse.io "{agentur}" "{city}" Sales',
    '"{agentur}" "{city}" "{role}" "m/w/d" Karriere Bewerbung',
    '"{agentur}" "{city}" "{role}" gesucht',
    '"{agentur}" "{city}" "{role}" Karriere',
    '"{agentur}" "{city}" "Vertrieb" Karriere',
]

_TARGET_GROWTH_TEMPLATES = [
    '"{agentur}" "{city}" "sucht Verstärkung"',
    '"{agentur}" "{city}" "Team wächst"',
    '"{agentur}" "{city}" "wir suchen" Karriere',
    'site:personio.de "{agentur}" "{city}"',
    'site:join.com "{agentur}" "{city}"',
    '"{agentur}" "{city}" Karriere Bewerbung',
    '"{agentur}" "{city}" "sucht" Mitarbeiter Karriere',
]


def build_job_detail_queries(
    industry: str,
    city: str,
    signal_type: str,
    relevance_focus: str = "broad",
) -> list[str]:
    st = (signal_type or "").strip().lower()
    if st not in ("sales_hiring", "growth_expansion"):
        raise ValueError(f"Unsupported signal_type: {signal_type!r}. Use 'sales_hiring' or 'growth_expansion'")
    rf = (relevance_focus or "broad").strip().lower()
    if rf not in _ALLOWED_RELEVANCE_FOCUS:
        raise ValueError(f"Unknown relevance_focus: {relevance_focus!r}. Allowed: {sorted(_ALLOWED_RELEVANCE_FOCUS)}")

    industry = " ".join((industry or "").strip().split())
    city = " ".join((city or "").strip().split())
    base = " ".join(part for part in [industry, city] if part).strip()
    if not base:
        raise ValueError("industry and city cannot both be empty")

    if rf == "target_industry":
        return _build_target_industry_queries(industry, city, st)
    return _build_broad_queries(industry, city, st)


def _build_broad_queries(industry: str, city: str, signal_type: str) -> list[str]:
    base = " ".join(part for part in [industry, city] if part).strip()
    queries: list[str] = []

    if signal_type == "sales_hiring":
        templates = _SALES_HIRING_TEMPLATES
        roles = _ROLES_PER_SIGNAL[signal_type]
        for template in templates:
            if "{role}" in template:
                for role in roles:
                    q = template.format(base=base, role=role).strip()
                    if q:
                        queries.append(q)
            else:
                q = template.format(base=base).strip()
                if q:
                    queries.append(q)
    else:
        templates = _GROWTH_TEMPLATES
        for template in templates:
            q = template.format(base=base).strip()
            if q:
                queries.append(q)
    return _dedupe(queries)


def _build_target_industry_queries(industry: str, city: str, signal_type: str) -> list[str]:
    neg = " ".join(_NEGATIVE_KEYWORDS)
    queries: list[str] = []

    if signal_type == "sales_hiring":
        templates = _TARGET_SALES_HIRING_TEMPLATES
        roles = _ROLES_PER_SIGNAL[signal_type]
        for agentur in _TARGET_INDUSTRY_VARIANTS:
            for template in templates:
                if "{role}" in template:
                    for role in roles:
                        q = template.format(agentur=agentur, city=city, role=role).strip()
                        if q:
                            q = f"{q} {neg}".strip()
                            queries.append(q)
                else:
                    q = template.format(agentur=agentur, city=city).strip()
                    if q:
                        q = f"{q} {neg}".strip()
                        queries.append(q)
    else:
        for agentur in _TARGET_INDUSTRY_VARIANTS:
            for template in _TARGET_GROWTH_TEMPLATES:
                q = template.format(agentur=agentur, city=city).strip()
                if q:
                    q = f"{q} {neg}".strip()
                    queries.append(q)

    return _dedupe(queries)


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.casefold().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out
