#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
LATEST = OUT / "latest"

INPUT_FILE = LATEST / "intent_company_website_search.json"
OUTPUT_FILE = LATEST / "intent_website_verification.json"

_MAX_WEBSITES = 3
_TIMEOUT_SECONDS = 8
_USER_AGENT = "Mozilla/5.0 (compatible; IntentWebsiteVerifier/1.0)"


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _domain_from_url(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower().strip()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _company_tokens(company_name: str) -> list[str]:
    legal_forms = {
        "gmbh", "ug", "ag", "kg", "ohg", "se", "ltd", "inc", "gbr",
        "corp", "llc", "llp", "sa", "sl", "bv", "nv", "sarl", "sas",
        "operations", "holding", "group",
    }
    words = []
    for raw in company_name.lower().replace("&", " ").replace("-", " ").split():
        token = "".join(ch for ch in raw if ch.isalnum())
        if len(token) < 3:
            continue
        if token in legal_forms:
            continue
        words.append(token)
    return words


def _strip_html(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return unescape(m.group(1)).strip() if m else ""


def _extract_meta_description(html: str) -> str:
    m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return unescape(m.group(1)).strip() if m else ""


def _has_contact_link(html: str) -> bool:
    return bool(re.search(r'href=["\'][^"\']*["\'][^>]*>\s*(Kontakt|Contact)\s*<', html, flags=re.IGNORECASE))


def _has_impressum_link(html: str) -> bool:
    return bool(re.search(r'href=["\'][^"\']*["\'][^>]*>\s*(Impressum|Imprint)\s*<', html, flags=re.IGNORECASE))


def _fetch_homepage(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        final_url = resp.geturl()
        status = getattr(resp, "status", None) or resp.getcode()
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read(250000)
    charset = "utf-8"
    m = re.search(r"charset=([A-Za-z0-9_\-]+)", content_type, flags=re.IGNORECASE)
    if m:
        charset = m.group(1)
    html = raw.decode(charset, errors="replace")
    return {
        "final_url": final_url,
        "http_status": int(status or 0),
        "html": html,
    }


def _verify_company(company_name: str, candidate_domain: str, candidate_url: str) -> dict:
    result = {
        "company_name": company_name,
        "candidate_domain": candidate_domain,
        "candidate_url": candidate_url,
        "final_url": "",
        "http_status": 0,
        "page_title": "",
        "meta_description": "",
        "brand_match": False,
        "domain_match": False,
        "has_contact_link": False,
        "has_impressum_link": False,
        "verification_confidence": 0.0,
        "verification_status": "failed",
        "next_action": "discard",
    }

    try:
        fetched = _fetch_homepage(candidate_url)
        html = fetched["html"]
        text = _strip_html(html)[:12000]
        title = _extract_title(html)
        meta_description = _extract_meta_description(html)
        final_url = fetched["final_url"]
        http_status = fetched["http_status"]
        final_domain = _domain_from_url(final_url)
        tokens = _company_tokens(company_name)

        domain_match = any(token in final_domain for token in tokens)
        searchable = f"{title} {meta_description} {text}".lower()
        brand_match = any(token in searchable for token in tokens)
        has_contact_link = _has_contact_link(html)
        has_impressum_link = _has_impressum_link(html)

        confidence = 0.0
        if domain_match:
            confidence += 0.4
        if brand_match:
            confidence += 0.3
        if 200 <= http_status < 400:
            confidence += 0.1
        if has_contact_link:
            confidence += 0.1
        if has_impressum_link:
            confidence += 0.1
        confidence = max(0.0, min(1.0, round(confidence, 3)))

        if confidence >= 0.8:
            verification_status = "verified"
            next_action = "enrich_company_contact_preview"
        elif confidence >= 0.6:
            verification_status = "likely_verified"
            next_action = "enrich_company_contact_preview"
        elif confidence >= 0.35:
            verification_status = "needs_review"
            next_action = "review"
        else:
            verification_status = "failed"
            next_action = "discard"

        result.update({
            "final_url": final_url,
            "http_status": http_status,
            "page_title": title,
            "meta_description": meta_description,
            "brand_match": brand_match,
            "domain_match": domain_match,
            "has_contact_link": has_contact_link,
            "has_impressum_link": has_impressum_link,
            "verification_confidence": confidence,
            "verification_status": verification_status,
            "next_action": next_action,
        })
    except Exception as exc:
        result["error"] = str(exc)
        result["verification_status"] = "failed"
        result["next_action"] = "discard"

    return result


def run() -> dict:
    payload = _safe_read_json(INPUT_FILE)
    entries = list((payload or {}).get("results") or [])
    eligible = [
        r for r in entries
        if r.get("next_action") == "verify_website"
        and r.get("website_resolution_status") == "official_candidate_found"
    ][:_MAX_WEBSITES]

    results = []
    for item in eligible:
        results.append(
            _verify_company(
                str(item.get("company_name") or ""),
                str(item.get("best_official_domain") or ""),
                str(item.get("best_official_url") or ""),
            )
        )

    final = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_websites": len(eligible),
        "verified": sum(1 for r in results if r["verification_status"] == "verified"),
        "likely_verified": sum(1 for r in results if r["verification_status"] == "likely_verified"),
        "needs_review": sum(1 for r in results if r["verification_status"] == "needs_review"),
        "failed": sum(1 for r in results if r["verification_status"] == "failed"),
        "results": results,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Phase 3.11: Website Verification ===")
    print(f"  total_websites:  {final['total_websites']}")
    print(f"  verified:        {final['verified']}")
    print(f"  likely_verified: {final['likely_verified']}")
    print(f"  needs_review:    {final['needs_review']}")
    print(f"  failed:          {final['failed']}")
    for r in results:
        print(f"  {r['company_name']} | {r['candidate_domain']} | {r['verification_status']} | {r['verification_confidence']:.3f} | {r['next_action']}")
    print()

    return final


if __name__ == "__main__":
    run()
