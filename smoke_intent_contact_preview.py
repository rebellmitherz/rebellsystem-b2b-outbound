from __future__ import annotations

import json

from modules.intent_contact_preview import (
    INPUT_FILE,
    OUTPUT_FILE,
    _build_candidates,
    run,
)


if __name__ == "__main__":
    candidates = _build_candidates("https://www.ipsos.com/de-de", ["kontakt", "de/kontakt", "contact"])
    assert any("kontakt" in c for c in candidates), candidates
    assert any("/de/kontakt" in c for c in candidates), candidates
    assert len(candidates) == len(set(candidates)), "should be deduplicated"

    verif = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    eligible = [
        r for r in verif.get("results", [])
        if r.get("next_action") == "enrich_company_contact_preview"
        and r.get("verification_status") in {"verified", "likely_verified"}
    ]
    assert len(eligible) >= 1, f"expected at least 1 eligible company, got {len(eligible)}"

    result = run()
    assert isinstance(result, dict), result
    assert result["api_used"] is False, result
    assert result["http_used"] is False, result
    assert OUTPUT_FILE.exists(), OUTPUT_FILE

    written = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    assert written["api_used"] is False, written
    assert written["http_used"] is False, written

    for c in written.get("companies", []):
        assert c["api_used"] is False, c
        assert c["http_used"] is False, c
        assert c["enrichment_status"] == "preview_only", c
        assert len(c["contact_candidate_urls"]) >= 1, c
        assert len(c["impressum_candidate_urls"]) >= 1, c
        assert c["next_action"] == "fetch_contact_pages_preview", c

    print("SMOKE_OK")
