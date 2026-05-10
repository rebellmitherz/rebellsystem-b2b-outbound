from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import run_intent_lead_production as production

ROOT = Path(__file__).resolve().parent

REAL = {
    "company_name": "Seokratie GmbH",
    "website": "https://seokratie.de",
    "source_signal_title": "Content Marketing Manager (m/w/d)",
    "source_signal_url": "https://www.stepstone.de/some-job",
    "fit_status": "target_fit",
    "fit_score": 1.0,
    "contact_name": "Sandra Beispielperson",
    "email": "kontakt@seokratie.de",
    "email_type": "generic_email",
    "phone": "+49 89 219 09 84 11",
    "contact_quality": "good",
    "recommended_first_line": "...",
    "outreach_angle": "sales_growth_signal",
    "email_subject": "Kurze Frage",
    "email_body": "Hallo Seokratie-Team, ...",
    "ready_for_approval": True,
    "missing_fields": [],
    "next_action": "approve_for_send",
}

FAKE_CASES = [
    # email contains 'example'
    {**REAL, "company_name": "FakeOne GmbH", "email": "info@example.com"},
    # email contains 'test'
    {**REAL, "company_name": "FakeTwo GmbH", "email": "kontakt@testfirma.de"},
    # email contains 'mock'
    {**REAL, "company_name": "FakeThree GmbH", "email": "mock@mockmail.io"},
    # email contains 'dummy'
    {**REAL, "company_name": "FakeFour GmbH", "email": "dummy@somefirm.de"},
    # email contains 'fixture'
    {**REAL, "company_name": "FakeFive GmbH", "email": "fixture@firma.de"},
    # email contains 'placeholder'
    {**REAL, "company_name": "FakeSix GmbH", "email": "placeholder@firma.de"},
    # email_source flagged
    {**REAL, "company_name": "FakeSeven GmbH", "email": "echtemail@firma.de", "email_source": "fixture"},
    # ends in .test.com domain
    {**REAL, "company_name": "FakeEight GmbH", "email": "info@sub.test.com"},
]


def _seed(rows: list[dict]) -> None:
    payload = {"generated_at": "2026-05-10T18:00:00Z", "results": rows}
    production.LATEST.mkdir(parents=True, exist_ok=True)
    production.INPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                     encoding="utf-8")


def _read_output() -> dict:
    return json.loads(production.OUTPUT_JSON.read_text(encoding="utf-8"))


if __name__ == "__main__":
    # Backup existing input file
    backup = None
    if production.INPUT_FILE.exists():
        backup = production.INPUT_FILE.read_text(encoding="utf-8")

    try:
        # 1. Real Seokratie data must remain ready_for_approval
        _seed([REAL])
        production.run("preview", limit=10)
        report = _read_output()
        leads = report["leads"]
        assert len(leads) == 1
        assert leads[0]["status"] == "ready_for_approval", \
            f"Real Seokratie should be ready_for_approval, got {leads[0]['status']}"
        assert leads[0]["contact_quality"] == "good"
        print("[OK] real Seokratie -> ready_for_approval")

        # 2. Each fake email must NEVER be ready_for_approval
        for fake in FAKE_CASES:
            _seed([fake])
            production.run("preview", limit=10)
            report = _read_output()
            leads = report["leads"]
            assert len(leads) == 1, f"expected 1 lead for {fake['company_name']}"
            lead = leads[0]
            assert lead["status"] != "ready_for_approval", \
                f"{fake['company_name']} ({fake['email']}) should NOT be ready_for_approval, got {lead['status']}"
            assert lead["status"] in ("needs_enrichment", "discard"), \
                f"{fake['company_name']} status invalid: {lead['status']}"
            assert lead["next_action"] in ("enrich_contact", "enrich_manually", "discard"), \
                f"{fake['company_name']} next_action invalid: {lead['next_action']}"
            assert lead["contact_quality"] == "invalid_or_mock", \
                f"{fake['company_name']} contact_quality should be invalid_or_mock, got {lead['contact_quality']}"
            assert "valid_real_email" in lead["missing_fields"], \
                f"{fake['company_name']} missing_fields should include valid_real_email, got {lead['missing_fields']}"
        print(f"[OK] all {len(FAKE_CASES)} fake email patterns rejected")

        # 3. Mixed batch: real + fake
        _seed([REAL] + FAKE_CASES[:2])
        production.run("preview", limit=10)
        report = _read_output()
        ready = [l for l in report["leads"] if l["status"] == "ready_for_approval"]
        assert len(ready) == 1, f"expected exactly 1 ready, got {len(ready)}"
        assert ready[0]["company_name"] == "Seokratie GmbH"
        print("[OK] mixed batch: only real lead ready_for_approval")

        # 4. Outputs exist
        assert production.OUTPUT_JSON.exists(), "JSON output missing"
        assert production.OUTPUT_CSV.exists(), "CSV output missing"
        assert production.OUTPUT_MD.exists(), "MD output missing"
        print("[OK] outputs JSON/CSV/MD written")

        # 5. Limit cap honored
        many = [REAL] * 25
        _seed(many)
        production.run("preview", limit=10)
        report = _read_output()
        assert report["normalized_leads"] <= 10, \
            f"limit cap broken: {report['normalized_leads']}"
        print("[OK] hard limit 10 enforced")

        # 6. No imports of mine/outreach_pipeline
        with open(production.__file__, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                assert not stripped.startswith("import outreach_pipeline"), \
                    "must not import outreach_pipeline"
                assert not stripped.startswith("from outreach_pipeline"), \
                    "must not import outreach_pipeline"
                assert not stripped.startswith("import mine"), "must not import mine"
                assert not stripped.startswith("from mine"), "must not import mine"
        print("[OK] no imports of mine/outreach_pipeline")

        print("SMOKE_OK")
    finally:
        if backup is not None:
            production.INPUT_FILE.write_text(backup, encoding="utf-8")
