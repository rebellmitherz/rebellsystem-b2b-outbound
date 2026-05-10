from __future__ import annotations

import json

from modules.intent_contact_page_fetcher import (
    INPUT_FILE,
    OUTPUT_FILE,
    _classify_page_type,
    _extract_emails,
    _extract_persons,
    _extract_phones,
    run,
)


if __name__ == "__main__":
    assert _classify_page_type("https://example.com/kontakt", "Kontakt", "Kontakt") == "contact"
    assert _classify_page_type("https://example.com/impressum", "Impressum", "Impressum") == "impressum"

    emails = _extract_emails("Kontakt: info@example.com und max.mustermann@example.com")
    assert len(emails) == 2, emails
    assert any(e["is_generic"] for e in emails), emails
    assert any(e["is_personal"] for e in emails), emails

    phones = _extract_phones("Rufen Sie uns an: +49 40 1234567 oder 040 / 7654321")
    assert len(phones) >= 1, phones

    persons = _extract_persons("Geschäftsführer Max Mustermann Ansprechpartner Erika Musterfrau")
    assert len(persons) >= 1, persons

    preview = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    eligible = [c for c in preview.get("companies", []) if c.get("next_action") == "fetch_contact_pages_preview"]
    assert len(eligible) >= 1, eligible

    result = run()
    assert isinstance(result, dict), result
    assert OUTPUT_FILE.exists(), OUTPUT_FILE

    written = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    assert written["fetched_companies"] <= 1, written
    assert written["urls_attempted"] <= 4, written

    for item in written.get("results", []):
        assert len(item.get("contact_results", [])) <= 2, item
        assert len(item.get("impressum_results", [])) <= 2, item
        assert item["next_action"] in {"build_intent_lead_preview", "review", "discard"}, item
        assert item["contact_enrichment_status"] in {"contact_data_found", "partial_contact_data", "no_contact_data", "failed"}, item

    print("SMOKE_OK")
