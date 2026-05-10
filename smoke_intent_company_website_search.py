from __future__ import annotations

import json
from pathlib import Path

from modules.intent_search_provider import _read_serper_key
from modules.intent_company_website_search import (
    INPUT_FILE,
    OUTPUT_FILE,
    _domain_from_url,
    _score_candidate,
    run,
)


if __name__ == "__main__":
    assert _domain_from_url("https://www.ipsos.com/de-de") == "ipsos.com"
    assert _domain_from_url("https://linkedin.com/company/ipsos") == "linkedin.com"

    good = _score_candidate(
        "Ipsos Operations GmbH",
        "Ipsos | Marktforschung und Beratung",
        "https://www.ipsos.com/de-de",
        "Offizielle Website von Ipsos in Deutschland",
    )
    assert good["is_official_candidate"] is True, good
    assert good["domain"] == "ipsos.com", good
    assert good["domain_confidence"] >= 0.45, good

    bad = _score_candidate(
        "Ipsos Operations GmbH",
        "Ipsos Jobs bei Stepstone",
        "https://www.stepstone.de/jobs/ipsos",
        "Stellenangebote von Ipsos",
    )
    assert bad["is_official_candidate"] is False, bad
    assert "blocked" in bad["reject_reason"], bad

    social = _score_candidate(
        "Ipsos Operations GmbH",
        "Ipsos LinkedIn",
        "https://www.linkedin.com/company/ipsos/",
        "LinkedIn Profil von Ipsos",
    )
    assert social["is_official_candidate"] is False, social

    preview = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    invalid_names = [c["company_name"] for c in preview.get("companies", []) if not c.get("company_name_valid")]
    assert "Marketing und E-Commerce" in invalid_names, invalid_names

    if not _read_serper_key():
        print("SKIPPED_NO_KEY")
    else:
        result = run()
        assert isinstance(result, dict)
        assert OUTPUT_FILE.exists(), f"Missing output: {OUTPUT_FILE}"
        written = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        assert written["provider"] == "serper", written
        assert "results" in written, written

        searched_names = [r["company_name"] for r in written.get("results", [])]
        assert "Marketing und E-Commerce" not in searched_names, searched_names
        assert len(searched_names) <= 3, searched_names

        for r in written.get("results", []):
            assert len(r.get("queries_used", [])) <= 2, r
            for cand in r.get("candidates", []):
                assert "title" in cand and "url" in cand and "snippet" in cand and "domain" in cand, cand
                assert "is_official_candidate" in cand and "domain_confidence" in cand and "reject_reason" in cand, cand

        print("SMOKE_OK")
