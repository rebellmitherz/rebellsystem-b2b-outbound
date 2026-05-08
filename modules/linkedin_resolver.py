"""Offline-safe LinkedIn/SERP resolver helpers.

No LinkedIn login, no browser automation, no external paid API. The networked
entrypoint uses the already configured DuckDuckGo dependency and only reads SERP
metadata/URLs.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException

logger = logging.getLogger(__name__)

SearchFn = Callable[[str, int, int, str], list[dict[str, Any]]]
MAX_LINKEDIN_RESOLVER_QUERIES = 5


@dataclass(frozen=True)
class LinkedInResolution:
    linkedin_person_url: str = ""
    linkedin_company_url_verified: str = ""
    linkedin_match_confidence: float = 0.0
    linkedin_match_reason: str = "no_linkedin_lookup_performed"
    linkedin_resolution_status: str = "not_checked"

    def as_fields(self) -> dict[str, Any]:
        return {
            "linkedin_person_url": self.linkedin_person_url,
            "linkedin_company_url_verified": self.linkedin_company_url_verified,
            "linkedin_match_confidence": round(float(self.linkedin_match_confidence), 2),
            "linkedin_match_reason": self.linkedin_match_reason,
            "linkedin_resolution_status": self.linkedin_resolution_status,
        }


def normalize_linkedin_url(url: str, *, kind: str = "") -> str:
    """Return canonical public linkedin.com/in or /company URL."""
    u = (url or "").strip()
    if not u:
        return ""
    u = u.split("?")[0].split("#")[0].rstrip("/")
    if not u.startswith(("http://", "https://")):
        return ""
    low = u.lower()
    if "linkedin.com/" not in low:
        return ""
    if kind == "person" and "/in/" not in low:
        return ""
    if kind == "company" and "/company/" not in low:
        return ""
    if "/in/" not in low and "/company/" not in low:
        return ""
    return u


def resolve_linkedin_from_hits(
    hits: Iterable[dict[str, Any]],
    *,
    company_name: str,
    person_name: str = "",
    website: str = "",
    city: str = "",
) -> LinkedInResolution:
    """Rank SERP hits and return the best LinkedIn match."""
    company_tokens = _important_tokens(company_name) or _important_tokens(_host_label(website))
    person_tokens = _important_tokens(person_name)
    city_tokens = _important_tokens(city)

    best_person: tuple[int, str, str] = (0, "", "")
    best_company: tuple[int, str, str] = (0, "", "")

    for hit in hits:
        url = str(hit.get("href") or hit.get("url") or "").strip()
        title = str(hit.get("title") or "")
        body = str(hit.get("body") or hit.get("snippet") or "")
        blob = f"{title} {body} {url}"
        person_url = normalize_linkedin_url(url, kind="person")
        company_url = normalize_linkedin_url(url, kind="company")
        if person_url:
            score, reason = _score_person_hit(blob, person_tokens, company_tokens, city_tokens)
            if score > best_person[0]:
                best_person = (score, person_url, reason)
        if company_url:
            score, reason = _score_company_hit(blob, company_tokens, city_tokens)
            if score > best_company[0]:
                best_company = (score, company_url, reason)

    if best_person[1] and best_person[0] >= 85:
        return LinkedInResolution(
            linkedin_person_url=best_person[1],
            linkedin_company_url_verified=best_company[1] if best_company[0] >= 70 else "",
            linkedin_match_confidence=min(0.98, best_person[0] / 100),
            linkedin_match_reason=best_person[2],
            linkedin_resolution_status="likely_person",
        )
    if best_company[1] and best_company[0] >= 70:
        return LinkedInResolution(
            linkedin_company_url_verified=best_company[1],
            linkedin_match_confidence=min(0.9, best_company[0] / 100),
            linkedin_match_reason=best_company[2],
            linkedin_resolution_status="verified_company",
        )
    if best_person[1] or best_company[1]:
        return LinkedInResolution(
            linkedin_person_url=best_person[1],
            linkedin_company_url_verified=best_company[1],
            linkedin_match_confidence=max(best_person[0], best_company[0]) / 100,
            linkedin_match_reason="linkedin_hit_found_but_low_confidence",
            linkedin_resolution_status="review",
        )
    return LinkedInResolution(
        linkedin_match_reason="no_linkedin_serp_hit",
        linkedin_resolution_status="rejected",
    )


def resolve_linkedin_via_ddg(
    *,
    company_name: str,
    person_name: str = "",
    website: str = "",
    city: str = "",
    region: str = "de-de",
    max_results: int = 12,
    search_fn: SearchFn | None = None,
) -> LinkedInResolution:
    """Resolve public LinkedIn URLs via SERP only."""
    queries = build_linkedin_queries(
        company_name=company_name,
        person_name=person_name,
        website=website,
        city=city,
    )
    if not queries:
        return LinkedInResolution(linkedin_match_reason="insufficient_query_context")
    fn = search_fn or _ddg_search_with_retry
    all_hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries[:MAX_LINKEDIN_RESOLVER_QUERIES]:
        try:
            hits = fn(query, max_results, 1, region)
        except Exception as exc:  # noqa: BLE001 - resolver must never break caller
            logger.warning("[linkedin_resolver] search skipped: %s", exc)
            # Do not abort – try next query instead
            continue
        for hit in hits:
            url = str(hit.get("href") or hit.get("url") or "").strip().split("?")[0].rstrip("/")
            if not url or url in seen:
                continue
            seen.add(url)
            all_hits.append(hit)
        # don't break early – collect all queries to get best match
    if not all_hits:
        return LinkedInResolution(linkedin_match_reason="serp_empty_or_unavailable", linkedin_resolution_status="search_link_only")
    return resolve_linkedin_from_hits(
        all_hits,
        company_name=company_name,
        person_name=person_name,
        website=website,
        city=city,
    )


def build_linkedin_queries(
    *,
    company_name: str,
    person_name: str = "",
    website: str = "",
    city: str = "",
) -> list[str]:
    company = _clean_query(company_name)
    person = _clean_query(person_name)
    host_label = _host_label(website)
    city_q = _clean_query(city)
    anchor = company or host_label
    queries: list[str] = []
    if person and anchor:
        queries.append(f'"{person}" "{anchor}" site:linkedin.com/in')
    if person and host_label and host_label.casefold() != anchor.casefold():
        queries.append(f'"{person}" "{host_label}" site:linkedin.com/in')
    if anchor:
        if city_q:
            queries.append(f'"{anchor}" "{city_q}" site:linkedin.com/company')
        queries.append(f'"{anchor}" site:linkedin.com/company')
        queries.append(f'"{anchor}" Geschäftsführer site:linkedin.com/in')
        queries.append(f'"{anchor}" Inhaber site:linkedin.com/in')
        if city_q:
            queries.append(f'"{anchor}" "{city_q}" site:linkedin.com/company')
        queries.append(f'"{anchor}" site:linkedin.com/company')
    if host_label and host_label.casefold() != anchor.casefold():
        queries.append(f'"{host_label}" site:linkedin.com/company')
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        q = " ".join(q.split())
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:MAX_LINKEDIN_RESOLVER_QUERIES]


def _ddg_search_with_retry(query: str, per_query: int, attempts: int, region: str) -> list[dict[str, Any]]:
    """Backend-Fallback-Kette identisch zu modules.search._search_with_retry.

    LinkedIn-Suchen sind oft `site:linkedin.com/...` — solche Queries indexieren
    Yandex/Mojeek schlecht. Wir versuchen sie trotzdem, geben aber nach kurzer
    Schleife auf, statt minutenlang zu retryen.
    """
    backends = ("auto", "yandex", "mojeek")
    for backend in backends:
        try:
            with DDGS() as ddgs:
                hits = list(ddgs.text(
                    query,
                    region=region,
                    safesearch="off",
                    max_results=max(1, per_query),
                    backend=backend,
                ))
            if hits:
                return hits
        except (DDGSException, RatelimitException, TimeoutError, ConnectionError):
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("[linkedin_resolver] SERP %s error: %s", backend, exc)
            break
    return []


def _score_person_hit(blob: str, person_tokens: set[str], company_tokens: set[str], city_tokens: set[str]) -> tuple[int, str]:
    b = _norm(blob)
    score = 20
    reasons = ["person_linkedin_url"]
    if person_tokens:
        hits = sum(1 for t in person_tokens if re.search(r'\b' + re.escape(t) + r'\b', b))
        if hits >= 2:
            score += min(35, hits * 18)
            reasons.append(f"person_tokens={hits}/{len(person_tokens)}")
        else:
            reasons.append("person_tokens_insufficient")
    if company_tokens:
        hits = sum(1 for t in company_tokens if re.search(r'\b' + re.escape(t) + r'\b', b))
        if hits >= 2:
            score += min(22, hits * 8)
            reasons.append(f"company_tokens={hits}/{len(company_tokens)}")
        else:
            reasons.append("company_tokens_insufficient")
    if city_tokens and any(re.search(r'\b' + re.escape(t) + r'\b', b) for t in city_tokens):
        score += 6
        reasons.append("city_match")
    return min(100, score), ";".join(reasons)


def _score_company_hit(blob: str, company_tokens: set[str], city_tokens: set[str]) -> tuple[int, str]:
    b = _norm(blob)
    score = 35
    reasons = ["company_linkedin_url"]
    if company_tokens:
        hits = sum(1 for t in company_tokens if re.search(r'\b' + re.escape(t) + r'\b', b))
        if hits >= 2:
            score += min(42, hits * 14)
            reasons.append(f"company_tokens={hits}/{len(company_tokens)}")
        else:
            reasons.append("company_tokens_insufficient")
    if city_tokens and any(re.search(r'\b' + re.escape(t) + r'\b', b) for t in city_tokens):
        score += 6
        reasons.append("city_match")
    return min(100, score), ";".join(reasons)


def _important_tokens(value: str) -> set[str]:
    stop = {
        "gmbh", "ug", "ag", "kg", "ohg", "gbr", "und", "co", "company",
        "marketingagentur", "agentur", "dienstleister", "deutschland",
    }
    return {
        t for t in re.split(r"[^a-z0-9äöüß]+", _norm(value))
        if len(t) >= 3 and t not in stop
    }


def _clean_query(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().replace('"', " "))[:120]


def _host_label(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    try:
        host = (urlparse(u).netloc or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    label = host.split(".")[0] if host else ""
    return label.replace("-", " ").replace("_", " ")


def _norm(value: str) -> str:
    t = (value or "").casefold()
    return (
        t.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )
