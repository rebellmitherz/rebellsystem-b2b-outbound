"""Offline-Smoke fuer parametrisierte Stadtlogik in intent_relevance_filter
und intent_resolved_company_fit.

Pruefziel: city_match greift jetzt fuer Koeln, IT (kein city), Hamburg usw.
statt nur fuer Muenchen. Kein SMTP, kein IMAP, kein Versand, keine Dateien
geschrieben.

Aufruf:
    python smoke_city_relevance_filter.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from modules.intent_relevance_filter import (  # noqa: E402
    _city_match_terms,
    _city_mismatch_terms,
    classify_job_detail_relevance,
)
from modules.intent_resolved_company_fit import (  # noqa: E402
    _city_match_terms as rcf_city_match,
    _city_mismatch_terms as rcf_city_mismatch,
    fit_check_resolved_companies,
)

# ─── Hilfsfunktionen ────────────────────────────────────────────────────────

def _job_result(text: str, portal_type: str = "job_detail_page") -> dict:
    return {
        "portal_url_type": portal_type,
        "title": text,
        "url": f"https://example.de/{text[:20].replace(' ', '-')}",
        "snippet": text,
    }


def _resolved(text: str) -> dict:
    return {
        "company_name_valid": True,
        "original_title": text,
        "original_snippet": text,
        "original_url": "https://example.de",
        "company_name_extracted": "TestFirma GmbH",
        "extraction_confidence": 0.95,
    }


# ─── Tests ──────────────────────────────────────────────────────────────────

def main() -> int:

    # ── A) Helper: _city_match_terms ────────────────────────────────────────
    # Muenchen (Fallback + Alias)
    assert "münchen" in _city_match_terms("München"), "A: münchen fehlt"
    assert "muenchen" in _city_match_terms("München"), "A: muenchen fehlt"
    assert "munich" in _city_match_terms("München"), "A: munich fehlt"
    assert "münchen" in _city_match_terms("muenchen"), "A: alias muenchen"

    # Koeln
    koeln_terms = _city_match_terms("Köln")
    assert "köln" in koeln_terms, f"A: köln fehlt: {koeln_terms}"
    assert "koeln" in koeln_terms, f"A: koeln fehlt: {koeln_terms}"
    assert "cologne" in koeln_terms, f"A: cologne fehlt: {koeln_terms}"

    # Hamburg (kein Alias nötig, nur Lowercased)
    assert "hamburg" in _city_match_terms("Hamburg"), "A: hamburg"

    # Leerer city-Parameter -> München-Fallback
    fb = _city_match_terms("")
    assert "münchen" in fb, f"A: fallback fehlt münchen: {fb}"

    print("PASS A: _city_match_terms liefert korrekte Terme")

    # ── B) Helper: _city_mismatch_terms ─────────────────────────────────────
    # Zielstadt wird aus Mismatch-Liste entfernt
    mm_koeln = _city_mismatch_terms("Köln")
    assert "köln" not in mm_koeln, f"B: köln in eigenem mismatch: {mm_koeln}"
    assert "koeln" not in mm_koeln, f"B: koeln in eigenem mismatch: {mm_koeln}"
    assert "hamburg" in mm_koeln, f"B: hamburg fehlt im mismatch"
    assert "berlin" in mm_koeln, f"B: berlin fehlt im mismatch"

    mm_hamburg = _city_mismatch_terms("Hamburg")
    assert "hamburg" not in mm_hamburg, f"B: hamburg im eigenen mismatch: {mm_hamburg}"
    assert "berlin" in mm_hamburg, "B: berlin fehlt"

    # Muenchen selbst: nicht in seiner eigenen Mismatch-Liste
    mm_muc = _city_mismatch_terms("München")
    assert "münchen" not in mm_muc, f"B: münchen im eigenen mismatch: {mm_muc}"
    assert "muenchen" not in mm_muc, f"B: muenchen im eigenen mismatch"

    print("PASS B: _city_mismatch_terms entfernt Zielstadt korrekt")

    # ── C) classify_job_detail_relevance — früher München-only, jetzt Köln ──

    # C1) Köln-Snippet -> city_match=True
    r_koeln = classify_job_detail_relevance(
        [_job_result("Sales Manager Köln - Marketing Agentur")],
        industry="Marketingagentur",
        city="Köln",
    )
    entry = r_koeln[0]
    assert entry.get("city_match") is True, f"C1: city_match fehlt für Köln: {entry}"
    assert entry.get("relevance_status") in ("relevant", "maybe_relevant"), \
        f"C1: status falsch: {entry.get('relevance_status')}"
    print(f"PASS C1: Köln -> city_match=True, status={entry['relevance_status']}")

    # C2) Snippet mit Berlin-Begriff bei Köln-Ziel -> city_mismatch=True
    r_wrong = classify_job_detail_relevance(
        [_job_result("Sales Manager Berlin - Marketing Agentur")],
        industry="Marketingagentur",
        city="Köln",
    )
    entry2 = r_wrong[0]
    assert entry2.get("city_match") is False, f"C2: city_match falsch positiv: {entry2}"
    assert entry2.get("city_mismatch") is True or "wrong_city" in entry2.get("rejection_reasons", []), \
        f"C2: wrong_city nicht erkannt: {entry2}"
    print(f"PASS C2: Berlin-Snippet bei Köln-Ziel -> city_match=False")

    # C3) München-Snippet bei München-Ziel -> weiterhin korrekt (Regression)
    r_muc = classify_job_detail_relevance(
        [_job_result("Business Development München - Agentur")],
        industry="Marketingagentur",
        city="München",
    )
    e3 = r_muc[0]
    assert e3.get("city_match") is True, f"C3: München-Regression: {e3}"
    print(f"PASS C3: München weiterhin korrekt (Regression)")

    # C4) IT-Lauf ohne city -> Fallback München, kein Crash
    r_it = classify_job_detail_relevance(
        [_job_result("IT-Dienstleister sucht Account Manager")],
        industry="IT",
        city="",
    )
    assert isinstance(r_it[0].get("city_match"), bool), f"C4: kein bool: {r_it[0]}"
    print(f"PASS C4: Leerer city-Parameter -> kein Crash, city_match={r_it[0]['city_match']}")

    # C5) Hamburg-Snippet bei Hamburg-Ziel -> city_match=True
    r_hh = classify_job_detail_relevance(
        [_job_result("Sales Executive Hamburg Agentur")],
        industry="Marketingagentur",
        city="Hamburg",
    )
    assert r_hh[0].get("city_match") is True, f"C5: Hamburg city_match fehlt: {r_hh[0]}"
    print(f"PASS C5: Hamburg -> city_match=True")

    # C6) Köln-Snippet bei München-Ziel -> city_mismatch (Köln ist im mismatch für München)
    r_koeln_wrong = classify_job_detail_relevance(
        [_job_result("Account Manager Köln - Agentur")],
        industry="Marketingagentur",
        city="München",
    )
    e6 = r_koeln_wrong[0]
    assert e6.get("city_match") is False, f"C6: city_match false positiv: {e6}"
    print(f"PASS C6: Köln-Snippet bei München-Ziel -> city_match=False")

    # ── D) fit_check_resolved_companies — rcf_city_match/mismatch ───────────

    # D1) Köln-Alias-Helpers
    assert "köln" in rcf_city_match("Köln"), "D1: köln fehlt"
    assert "cologne" in rcf_city_match("Köln"), "D1: cologne fehlt"
    assert "köln" not in rcf_city_mismatch("Köln"), "D1: köln im eigenen mismatch"

    # D2) fit_check mit Köln-Daten
    results_d = fit_check_resolved_companies(
        [_resolved("Account Manager Köln Marketingagentur Sales")],
        target_industry="Marketingagentur",
        target_city="Köln",
    )
    rd = results_d[0]
    assert rd.get("city_fit") is True, f"D2: city_fit fehlt für Köln: {rd}"
    assert rd.get("fit_status") in ("target_fit", "maybe_fit"), \
        f"D2: fit_status falsch: {rd.get('fit_status')}"
    print(f"PASS D2: fit_check Köln -> city_fit=True, fit_status={rd['fit_status']}")

    # D3) München-Regression in fit_check
    results_muc = fit_check_resolved_companies(
        [_resolved("Account Manager München Marketingagentur")],
        target_industry="Marketingagentur",
        target_city="München",
    )
    assert results_muc[0].get("city_fit") is True, f"D3: München-Regression: {results_muc[0]}"
    print("PASS D3: fit_check München weiterhin korrekt (Regression)")

    # D4) Hamburg-Ziel, Hamburg-Snippet
    results_hh = fit_check_resolved_companies(
        [_resolved("Sales Manager Hamburg Werbeagentur Business Development")],
        target_industry="Werbeagentur",
        target_city="Hamburg",
    )
    assert results_hh[0].get("city_fit") is True, f"D4: Hamburg city_fit fehlt: {results_hh[0]}"
    assert "hamburg" not in rcf_city_mismatch("Hamburg"), "D4: hamburg im eigenen mismatch"
    print("PASS D4: fit_check Hamburg -> city_fit=True, nicht im mismatch")

    # D5) Leeres target_city -> kein Crash, bool-Rückgabe
    results_empty = fit_check_resolved_companies(
        [_resolved("Sales Manager Agentur")],
        target_industry="IT",
        target_city="",
    )
    assert isinstance(results_empty[0].get("city_fit"), bool), "D5: kein bool"
    print("PASS D5: leeres target_city -> kein Crash")

    # ── E) Determinismus ─────────────────────────────────────────────────────
    assert _city_match_terms("Köln") == _city_match_terms("Köln"), "E: nicht deterministisch"
    assert _city_mismatch_terms("Hamburg") == _city_mismatch_terms("Hamburg"), "E: mismatch nicht deterministisch"
    print("PASS E: beide Helpers deterministisch")

    print("ALL_TESTS_PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
