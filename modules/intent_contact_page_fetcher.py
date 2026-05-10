#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
LATEST = OUT / "latest"

INPUT_FILE = LATEST / "intent_contact_preview.json"
OUTPUT_FILE = LATEST / "intent_contact_page_fetch_preview.json"

_MAX_COMPANIES = 1
_MAX_CONTACT_URLS = 2
_MAX_IMPRESSUM_URLS = 2
_TIMEOUT_SECONDS = 8
_USER_AGENT = "Mozilla/5.0 (compatible; IntentContactPageFetcher/1.0)"

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s./-]?)?(?:\(0\))?(?:\d[\s./-]?){6,15}\d")
_PERSON_RE = re.compile(
    r"\b(?:Geschäftsführer|Geschaeftsfuehrer|Ansprechpartner|Team|Kontakt)\b.{0,80}?\b([A-ZÄÖÜ][a-zäöüß]+(?:\s+[A-ZÄÖÜ][a-zäöüß]+){1,2})",
    re.IGNORECASE | re.DOTALL,
)

_GENERIC_PREFIXES = ("info@", "kontakt@", "office@", "hello@", "service@", "support@")


def _safe_read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return unescape(m.group(1)).strip() if m else ""


def _strip_html(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _classify_page_type(url: str, title: str, text: str) -> str:
    blob = f"{url} {title} {text}".lower()
    if "impressum" in blob or "imprint" in blob:
        return "impressum"
    if "kontakt" in blob or "contact" in blob or "ansprechpartner" in blob:
        return "contact"
    return "unknown"


def _extract_emails(text: str) -> list[dict]:
    found = []
    seen = set()
    for email in _EMAIL_RE.findall(text or ""):
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append({
            "email": email,
            "is_generic": key.startswith(_GENERIC_PREFIXES),
            "is_personal": not key.startswith(_GENERIC_PREFIXES),
        })
    return found


def _extract_phones(text: str) -> list[str]:
    out = []
    seen = set()
    for match in _PHONE_RE.findall(text or ""):
        phone = re.sub(r"\s+", " ", match).strip(" .,-")
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 7:
            continue
        if phone in seen:
            continue
        seen.add(phone)
        out.append(phone)
    return out


def _extract_persons(text: str) -> list[str]:
    out = []
    seen = set()
    for match in _PERSON_RE.findall(text or ""):
        name = re.sub(r"\s+", " ", match).strip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return out


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}, method="GET")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        final_url = resp.geturl()
        status = getattr(resp, "status", None) or resp.getcode()
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read(300000)
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


def _extract_from_url(url: str) -> dict:
    result = {
        "url": url,
        "fetched": False,
        "http_status": 0,
        "final_url": "",
        "page_title": "",
        "page_type": "unknown",
        "emails_found": [],
        "phones_found": [],
        "contact_persons_found": [],
        "extraction_confidence": 0.0,
        "error": "",
    }
    try:
        fetched = _fetch(url)
        html = fetched["html"]
        text = _strip_html(html)
        title = _extract_title(html)
        emails = _extract_emails(text)
        phones = _extract_phones(text)
        persons = _extract_persons(text)
        page_type = _classify_page_type(url, title, text)

        confidence = 0.0
        if fetched["http_status"] and 200 <= fetched["http_status"] < 400:
            confidence += 0.25
        if page_type in {"contact", "impressum"}:
            confidence += 0.25
        if emails:
            confidence += 0.25
        if phones or persons:
            confidence += 0.25
        confidence = round(min(confidence, 1.0), 3)

        result.update({
            "fetched": True,
            "http_status": fetched["http_status"],
            "final_url": fetched["final_url"],
            "page_title": title,
            "page_type": page_type,
            "emails_found": emails,
            "phones_found": phones,
            "contact_persons_found": persons,
            "extraction_confidence": confidence,
        })
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _pick_best_email(contact_results: list[dict], impressum_results: list[dict]) -> str:
    all_emails = []
    for bucket in (contact_results, impressum_results):
        for item in bucket:
            for email in item.get("emails_found", []):
                all_emails.append(email)
    personal = [e["email"] for e in all_emails if e.get("is_personal")]
    generic = [e["email"] for e in all_emails if e.get("is_generic")]
    return personal[0] if personal else (generic[0] if generic else "")


def _pick_best_phone(contact_results: list[dict], impressum_results: list[dict]) -> str:
    for bucket in (contact_results, impressum_results):
        for item in bucket:
            phones = item.get("phones_found", [])
            if phones:
                return phones[0]
    return ""


def run() -> dict:
    payload = _safe_read_json(INPUT_FILE)
    companies = list((payload or {}).get("companies") or [])
    eligible = [c for c in companies if c.get("next_action") == "fetch_contact_pages_preview"][:_MAX_COMPANIES]

    results = []
    urls_attempted = 0
    emails_found_count = 0
    phones_found_count = 0

    for company in eligible:
        contact_urls = list((company.get("contact_candidate_urls") or [])[:_MAX_CONTACT_URLS])
        impressum_urls = list((company.get("impressum_candidate_urls") or [])[:_MAX_IMPRESSUM_URLS])

        contact_results = []
        for url in contact_urls:
            urls_attempted += 1
            res = _extract_from_url(url)
            emails_found_count += len(res.get("emails_found", []))
            phones_found_count += len(res.get("phones_found", []))
            contact_results.append(res)

        impressum_results = []
        for url in impressum_urls:
            urls_attempted += 1
            res = _extract_from_url(url)
            emails_found_count += len(res.get("emails_found", []))
            phones_found_count += len(res.get("phones_found", []))
            impressum_results.append(res)

        best_email = _pick_best_email(contact_results, impressum_results)
        best_phone = _pick_best_phone(contact_results, impressum_results)

        if best_email and best_phone:
            status = "contact_data_found"
            next_action = "build_intent_lead_preview"
        elif best_email or best_phone:
            status = "partial_contact_data"
            next_action = "review"
        elif any(r.get("fetched") for r in (contact_results + impressum_results)):
            status = "no_contact_data"
            next_action = "review"
        else:
            status = "failed"
            next_action = "discard"

        results.append({
            "company_name": str(company.get("company_name") or ""),
            "verified_domain": str(company.get("verified_domain") or ""),
            "verified_url": str(company.get("verified_url") or ""),
            "contact_results": contact_results,
            "impressum_results": impressum_results,
            "best_email": best_email,
            "best_phone": best_phone,
            "contact_enrichment_status": status,
            "next_action": next_action,
        })

    final = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_file": str(INPUT_FILE),
        "total_companies": len(companies),
        "fetched_companies": len(results),
        "urls_attempted": urls_attempted,
        "emails_found_count": emails_found_count,
        "phones_found_count": phones_found_count,
        "results": results,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Phase 3.13: Contact/Impressum Fetch Preview ===")
    print(f"  total_companies:   {len(companies)}")
    print(f"  fetched_companies: {len(results)}")
    print(f"  urls_attempted:    {urls_attempted}")
    print(f"  emails_found:      {emails_found_count}")
    print(f"  phones_found:      {phones_found_count}")
    for r in results:
        print(f"  {r['company_name']} | {r['verified_domain']} | {r['contact_enrichment_status']} | email={bool(r['best_email'])} phone={bool(r['best_phone'])} | {r['next_action']}")
    print()

    return final


if __name__ == "__main__":
    run()
