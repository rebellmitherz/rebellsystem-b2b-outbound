"""
Smoke-Test: Phase 3.9 – Company Website Resolution Preview.

- Laueft ohne Crash
- Output-Datei wird geschrieben
- api_used ist false
- preview_ready Firmen haben >= 1 Query
- Keine externen Requests
- is_likely_real_company_name filtert generische Begriffe
"""
from __future__ import annotations

import json
from pathlib import Path

from modules.intent_company_website_resolver import (
    run,
    is_likely_real_company_name,
    _extract_company_from_title,
    _is_company_candidate,
    _build_queries,
    OUTPUT_FILE,
)

if __name__ == "__main__":
    # 1. is_likely_real_company_name: valid cases
    assert is_likely_real_company_name("Ipsos Operations GmbH") == (True, ""), \
        f"Ipsos Operations GmbH should be valid: {is_likely_real_company_name('Ipsos Operations GmbH')}"
    assert is_likely_real_company_name("Nielsen Communication GmbH") == (True, "")
    assert is_likely_real_company_name("Visionary Minds GmbH") == (True, "")
    assert is_likely_real_company_name("Müller & Schmidt KG") == (True, "")
    assert is_likely_real_company_name("ACME Corp") == (True, "")
    assert is_likely_real_company_name("TestFirma AG") == (True, "")
    assert is_likely_real_company_name("Ipsos Ltd") == (True, "")

    # 2. is_likely_real_company_name: invalid cases
    valid, reason = is_likely_real_company_name("Marketing und E-Commerce")
    assert valid is False, f"'Marketing und E-Commerce' should be invalid, got valid={valid}"
    assert "generic" in reason, f"reason should mention generic: {reason}"

    valid, reason = is_likely_real_company_name("Sales Manager")
    assert valid is False, f"'Sales Manager' should be invalid"

    valid, reason = is_likely_real_company_name("Business Development")
    assert valid is False, f"'Business Development' should be invalid"

    valid, reason = is_likely_real_company_name("Senior Account Manager")
    assert valid is False, f"'Senior Account Manager' should be invalid"

    valid, reason = is_likely_real_company_name("Kauffrau für Marketing")
    assert valid is False, f"'Kauffrau für Marketing' should be invalid"

    valid, reason = is_likely_real_company_name("Marketing Jobs")
    assert valid is False, f"'Marketing Jobs' should be invalid"

    valid, reason = is_likely_real_company_name("AB")  # too short
    assert valid is False, f"'AB' should be invalid (too short)"

    valid, reason = is_likely_real_company_name("")
    assert valid is False, f"empty should be invalid"

    # 3. Unit: company name extraction
    assert _extract_company_from_title(
        "Manager (w/m/d) - Creative Excellence - Job bei der Firma Ipsos Operations GmbH"
    ) == "Ipsos Operations GmbH"
    assert _extract_company_from_title(
        "ACCOUNT MANAGER (m/w/d) - Job bei der Firma Nielsen Communication GmbH"
    ) == "Nielsen Communication GmbH"

    # 4. Unit: is_company_candidate
    assert _is_company_candidate({"recommended_next_action": "resolve_company_website"}) is True
    assert _is_company_candidate({"company_name_valid": True}) is True
    assert _is_company_candidate({"recommended_next_action": "fetch_detail"}) is False
    assert _is_company_candidate({}) is False

    # 5. Unit: build_queries
    q = _build_queries("TestFirma GmbH", "München")
    assert len(q) >= 4
    assert "TestFirma GmbH offizielle Website" in q
    assert "TestFirma GmbH Impressum" in q
    assert "TestFirma GmbH München Website" in q
    assert "TestFirma GmbH Kontakt" in q
    assert "TestFirma GmbH LinkedIn" in q
    assert len(q) == len(set(q)), "queries should be deduplicated"
    assert all(qq.strip() for qq in q), "no empty queries allowed"

    # 6. Run: real data (files may not exist – must not crash)
    result = run(industry="Marketingagentur", city="Muenchen")
    assert isinstance(result, dict), "return must be dict"
    assert "api_used" in result, "missing api_used"
    assert result["api_used"] is False, "api_used must be False"
    assert "total_companies" in result
    assert "preview_ready" in result
    assert "needs_review" in result
    assert "companies" in result

    # 7. Output file exists
    assert OUTPUT_FILE.exists(), f"Output file not found: {OUTPUT_FILE}"
    written = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    assert written["api_used"] is False, "written api_used must be False"

    # 8. Verify all companies have new fields
    for c in written.get("companies", []):
        assert "company_name_valid" in c, f"missing company_name_valid for {c.get('company_name')}"
        assert "company_name_reject_reason" in c, f"missing company_name_reject_reason for {c.get('company_name')}"
        if c["company_name_valid"]:
            assert len(c["resolution_queries"]) >= 1, f"valid company {c['company_name']} has 0 queries"
            assert all(q.strip() for q in c["resolution_queries"]), f"valid company {c['company_name']} has empty query"
            assert c["next_action"] == "search_official_website", \
                f"valid company {c['company_name']} should have search_official_website, got {c['next_action']}"
        else:
            assert c["next_action"] == "review_company_name", \
                f"invalid company {c['company_name']} should have review_company_name, got {c['next_action']}"
            assert c["company_name_reject_reason"], f"invalid company {c['company_name']} missing reject_reason"

    # 9. No external requests (verified by api_used=False everywhere)
    for c in written.get("companies", []):
        assert c.get("api_used") is False, f"Company {c['company_name']} has api_used=True"

    # 10. Counts must match
    valid_count = sum(1 for c in written["companies"] if c["company_name_valid"])
    invalid_count = sum(1 for c in written["companies"] if not c["company_name_valid"])
    assert written["preview_ready"] == valid_count, \
        f"preview_ready={written['preview_ready']} != valid_count={valid_count}"
    assert written["needs_review"] == invalid_count, \
        f"needs_review={written['needs_review']} != invalid_count={invalid_count}"

    print(f"  total_companies: {result['total_companies']}")
    print(f"  preview_ready:   {result['preview_ready']}")
    print(f"  needs_review:    {result['needs_review']}")
    for c in written["companies"]:
        status = "valid" if c["company_name_valid"] else f"REJECTED: {c['company_name_reject_reason']}"
        print(f"  {c['company_name']} | {c['fit_status']} | {status} | {c['next_action']}")
    print("SMOKE_OK")
