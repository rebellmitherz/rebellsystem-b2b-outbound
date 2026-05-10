from __future__ import annotations

import json

from modules.intent_lead_preview_builder import OUTPUT_FILE, run


if __name__ == "__main__":
    result = run()
    assert isinstance(result, dict), result
    assert OUTPUT_FILE.exists(), OUTPUT_FILE

    written = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    assert "pipeline_status" in written, written
    assert "lead_candidates" in written, written
    assert "discarded_candidates" in written, written
    assert "review_candidates" in written, written
    assert "summary" in written, written

    assert written["pipeline_status"] in {"no_lead_created", "lead_candidates_ready"}, written

    ipsos_in_leads = any(c.get("company_name") == "Ipsos Operations GmbH" for c in written.get("lead_candidates", []))
    assert ipsos_in_leads is False, written

    ipsos_discard_or_review = any(
        c.get("company_name") == "Ipsos Operations GmbH"
        for c in (written.get("discarded_candidates", []) + written.get("review_candidates", []))
    )
    assert ipsos_discard_or_review is True, written

    assert written["total_lead_candidates"] == len(written.get("lead_candidates", [])), written
    assert written["discarded_count"] == len(written.get("discarded_candidates", [])), written
    assert written["review_count"] == len(written.get("review_candidates", [])), written

    print("SMOKE_OK")
