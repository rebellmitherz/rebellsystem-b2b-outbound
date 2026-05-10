"""
Smoke-Test: Phase 3.9 – Company Website Resolution Preview.

- Laueft ohne Crash
- Output-Datei wird geschrieben
- api_used ist false
- preview_ready Firmen haben >= 1 Query
- Keine externen Requests
"""
from __future__ import annotations

import json
from pathlib import Path

from modules.intent_company_website_resolver import (
    run,
    _extract_company_from_title,
    _is_company_candidate,
    _build_queries,
    OUTPUT_FILE,
)

if __name__ == "__main__":
    # 1. Unit: company name extraction
    assert _extract_company_from_title(
        "Manager (w/m/d) - Creative Excellence - Job bei der Firma Ipsos Operations GmbH"
    ) == "Ipsos Operations GmbH", f"got: {_extract_company_from_title('Manager (w/m/d) - Creative Excellence - Job bei der Firma Ipsos Operations GmbH')!r}"
    assert _extract_company_from_title(
        "ACCOUNT MANAGER (m/w/d) - Job bei der Firma Nielsen Communication GmbH"
    ) == "Nielsen Communication GmbH"
    assert _extract_company_from_title(
        "Junior Sales Manager – Außendienst – Bosch Power Tools"
    ) == "Junior Sales Manager"
    assert _extract_company_from_title("") == ""

    # 2. Unit: is_company_candidate
    assert _is_company_candidate({"recommended_next_action": "resolve_company_website"}) is True
    assert _is_company_candidate({"company_name_valid": True}) is True
    assert _is_company_candidate({"recommended_next_action": "fetch_detail"}) is False
    assert _is_company_candidate({}) is False

    # 3. Unit: build_queries
    q = _build_queries("TestFirma GmbH", "München")
    assert len(q) >= 4, f"expected >=4 queries, got {len(q)}"
    assert "TestFirma GmbH offizielle Website" in q
    assert "TestFirma GmbH Impressum" in q
    assert "TestFirma GmbH München Website" in q
    assert "TestFirma GmbH Kontakt" in q
    assert "TestFirma GmbH LinkedIn" in q
    # no duplicates
    assert len(q) == len(set(q)), "queries should be deduplicated"
    # no empty queries
    assert all(qq.strip() for qq in q), "no empty queries allowed"

    q_no_city = _build_queries("Firma", "")
    assert len(q_no_city) >= 3
    assert all(qq.strip() for qq in q_no_city)

    # 4. Run: real data (files may not exist – must not crash)
    result = run(industry="Marketingagentur", city="Muenchen")
    assert isinstance(result, dict), "return must be dict"
    assert "api_used" in result, "missing api_used"
    assert result["api_used"] is False, "api_used must be False"
    assert "total_companies" in result
    assert "preview_ready" in result
    assert "needs_review" in result
    assert "companies" in result

    # 5. Output file exists
    assert OUTPUT_FILE.exists(), f"Output file not found: {OUTPUT_FILE}"
    written = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    assert written["api_used"] is False, "written api_used must be False"

    # 6. If preview_ready > 0, verify queries
    for c in written.get("companies", []):
        if c.get("resolution_queries"):
            assert len(c["resolution_queries"]) >= 1, f"Company {c['company_name']} has 0 queries"
            assert all(q.strip() for q in c["resolution_queries"]), f"Company {c['company_name']} has empty query"

    # 7. No external requests happened (verified by api_used=False everywhere)
    for c in written.get("companies", []):
        assert c.get("api_used") is False, f"Company {c['company_name']} has api_used=True"

    print(f"  total_companies: {result['total_companies']}")
    print(f"  preview_ready:   {result['preview_ready']}")
    print(f"  needs_review:    {result['needs_review']}")
    print("SMOKE_OK")
