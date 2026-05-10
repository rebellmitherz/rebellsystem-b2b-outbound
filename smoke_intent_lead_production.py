from __future__ import annotations

import json
from pathlib import Path

import run_intent_lead_production as production

ROOT = Path(__file__).resolve().parent

REAL_PERSONAL = {
    "company_name": "Seokratie GmbH",
    "website": "https://seokratie.de",
    "source_signal_title": "Sales Manager (m/w/d)",
    "source_signal_url": "https://www.stepstone.de/some-job",
    "fit_status": "target_fit",
    "fit_score": 1.0,
    "contact_name": "Sandra Beispielperson",
    "email": "sandra@seokratie.de",
    "email_type": "personal_email",
    "phone": "+49 89 219 09 84 11",
    "contact_quality": "good",
    "recommended_first_line": "...",
    "outreach_angle": "sales_growth_signal",
    "email_subject": "Kurze Frage",
    "email_body": "Hallo Sandra Beispielperson, ...",
    "ready_for_approval": True,
    "missing_fields": [],
    "next_action": "approve_for_send",
}

INVALID_NAME_GENERIC = {
    "company_name": "Seokratie GmbH",
    "website": "https://www.seokratie.de/impressum/",
    "source_signal_title": "Content Marketing Manager (m/w/d)",
    "source_signal_url": "https://www.stepstone.de/some-job",
    "fit_status": "target_fit",
    "fit_score": 1.0,
    "contact_name": "Wissen Blog SEO",
    "email": "kontakt@seokratie.de",
    "email_type": "generic_email",
    "phone": "+49 89 219 09 84 11",
    "contact_quality": "good",
    "recommended_first_line": "...",
    "outreach_angle": "sales_growth_signal",
    "email_subject": "Kurze Frage",
    "email_body": "Hallo Wissen Blog SEO, ...",
    "ready_for_approval": True,
    "missing_fields": [],
    "next_action": "approve_for_send",
}

FAKE_CASES = [
    {**REAL_PERSONAL, "company_name": "FakeOne GmbH", "email": "info@example.com"},
    {**REAL_PERSONAL, "company_name": "FakeTwo GmbH", "email": "kontakt@testfirma.de"},
    {**REAL_PERSONAL, "company_name": "FakeThree GmbH", "email": "mock@mockmail.io"},
    {**REAL_PERSONAL, "company_name": "FakeFour GmbH", "email": "dummy@somefirm.de"},
    {**REAL_PERSONAL, "company_name": "FakeFive GmbH", "email": "fixture@firma.de"},
    {**REAL_PERSONAL, "company_name": "FakeSix GmbH", "email": "placeholder@firma.de"},
    {**REAL_PERSONAL, "company_name": "FakeSeven GmbH", "email": "echtemail@firma.de", "email_source": "fixture"},
    {**REAL_PERSONAL, "company_name": "FakeEight GmbH", "email": "info@sub.test.com"},
]


def _seed(rows: list[dict]) -> None:
    payload = {"generated_at": "2026-05-10T18:00:00Z", "results": rows}
    production.LATEST.mkdir(parents=True, exist_ok=True)
    production.INPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_output() -> dict:
    return json.loads(production.OUTPUT_JSON.read_text(encoding="utf-8"))


if __name__ == "__main__":
    backup = None
    if production.INPUT_FILE.exists():
        backup = production.INPUT_FILE.read_text(encoding="utf-8")

    try:
        _seed([REAL_PERSONAL])
        production.run("preview", limit=10, skip_refresh=True)
        report = _read_output()
        lead = report["leads"][0]
        assert lead["status"] == "ready_for_approval", lead
        assert lead["decision_maker_name"] == "Sandra Beispielperson", lead
        assert lead["email"].startswith("sandra@"), lead
        print("[OK] personal email + valid decision maker -> ready_for_approval")

        _seed([INVALID_NAME_GENERIC])
        production.run("preview", limit=10, skip_refresh=True)
        report = _read_output()
        lead = report["leads"][0]
        assert lead["decision_maker_name"] == "", lead
        assert "valid_decision_maker_name" in lead["missing_fields"], lead
        assert lead["email_body"].startswith("Hallo Seokratie GmbH-Team,") or lead["email_body"].startswith("Hallo Seokratie-Team,"), lead
        assert lead["website"] == "https://www.seokratie.de", lead
        assert lead["contact_source_url"] == "https://www.seokratie.de/impressum/", lead
        assert lead["status"] == "needs_enrichment", lead
        assert lead["next_action"] == "enrich_contact", lead
        assert "weak_sales_signal" in lead["risk_flags"], lead
        print("[OK] invalid name + generic email -> needs_enrichment with team salutation and root website")

        for fake in FAKE_CASES:
            _seed([fake])
            production.run("preview", limit=10, skip_refresh=True)
            report = _read_output()
            lead = report["leads"][0]
            assert lead["status"] != "ready_for_approval", lead
            assert lead["contact_quality"] == "invalid_or_mock", lead
            assert "valid_real_email" in lead["missing_fields"], lead
        print(f"[OK] all {len(FAKE_CASES)} fake email patterns rejected")

        _seed([REAL_PERSONAL, INVALID_NAME_GENERIC])
        production.run("preview", limit=10, skip_refresh=True)
        report = _read_output()
        ready = [l for l in report["leads"] if l["status"] == "ready_for_approval"]
        assert len(ready) == 1, report
        assert ready[0]["company_name"] == "Seokratie GmbH", report
        print("[OK] mixed batch: only personal+valid lead ready_for_approval")

        assert production.OUTPUT_JSON.exists(), "JSON output missing"
        assert production.OUTPUT_CSV.exists(), "CSV output missing"
        assert production.OUTPUT_MD.exists(), "MD output missing"
        print("[OK] outputs JSON/CSV/MD written")

        many = [REAL_PERSONAL] * 25
        _seed(many)
        production.run("preview", limit=10, skip_refresh=True)
        report = _read_output()
        assert report["normalized_leads"] <= 10, report
        print("[OK] hard limit 10 enforced")

        with open(production.__file__, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                assert not stripped.startswith("import outreach_pipeline"), "must not import outreach_pipeline"
                assert not stripped.startswith("from outreach_pipeline"), "must not import outreach_pipeline"
                assert not stripped.startswith("import mine"), "must not import mine"
                assert not stripped.startswith("from mine"), "must not import mine"
        print("[OK] no imports of mine/outreach_pipeline")

        print("RUN_OK")
        print("SMOKE_OK")
    finally:
        if backup is not None:
            production.INPUT_FILE.write_text(backup, encoding="utf-8")
