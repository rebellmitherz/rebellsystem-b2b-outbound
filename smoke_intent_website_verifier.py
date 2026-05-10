from __future__ import annotations

import json

from modules.intent_website_verifier import (
    INPUT_FILE,
    OUTPUT_FILE,
    _domain_from_url,
    _extract_meta_description,
    _extract_title,
    _has_contact_link,
    _has_impressum_link,
    _strip_html,
    run,
)


if __name__ == "__main__":
    assert _domain_from_url("https://www.ipsos.com/de-de") == "ipsos.com"
    assert _extract_title("<html><head><title>Ipsos Deutschland</title></head></html>") == "Ipsos Deutschland"
    assert _extract_meta_description('<meta name="description" content="Offizielle Website von Ipsos">') == "Offizielle Website von Ipsos"
    assert _has_contact_link('<a href="/kontakt">Kontakt</a>') is True
    assert _has_impressum_link('<a href="/impressum">Impressum</a>') is True
    assert _strip_html('<html><body><h1>Ipsos</h1><script>x</script></body></html>') == 'Ipsos'

    search_payload = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    verify_candidates = [
        r for r in search_payload.get("results", [])
        if r.get("next_action") == "verify_website"
        and r.get("website_resolution_status") == "official_candidate_found"
    ]
    assert len(verify_candidates) <= 3, verify_candidates
    assert all(r.get("company_name") != "Marketing und E-Commerce" for r in verify_candidates), verify_candidates

    result = run()
    assert isinstance(result, dict), result
    assert OUTPUT_FILE.exists(), OUTPUT_FILE

    written = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    assert "results" in written, written
    assert written["total_websites"] <= 3, written

    for item in written.get("results", []):
        assert item["company_name"], item
        assert item["candidate_url"], item
        assert item["candidate_domain"], item
        assert item["verification_status"] in {"verified", "likely_verified", "needs_review", "failed"}, item
        assert item["next_action"] in {"enrich_company_contact_preview", "review", "discard"}, item
        assert 0.0 <= float(item["verification_confidence"]) <= 1.0, item

    print("SMOKE_OK")
