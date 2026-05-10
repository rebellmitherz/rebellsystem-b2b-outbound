from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import run_intent_auto_pipeline as pipeline

ROOT = Path(__file__).resolve().parent
LATEST = ROOT / "output" / "latest"

OUTREACH_PREVIEW_FILE = LATEST / "intent_outreach_preview.json"
REPORT_JSON = LATEST / "intent_auto_pipeline_report.json"
REPORT_MD = LATEST / "intent_auto_pipeline_report.md"
AUTO_SEND_CANDIDATES = LATEST / "intent_auto_send_candidates.json"
ALREADY_SENT_LOG = LATEST / "intent_auto_sent_log.json"
DO_NOT_CONTACT_FILE = LATEST / "intent_do_not_contact.json"

# Static fixture: 3 candidates, 1 fully eligible, 2 ineligible
FIXTURE = {
    "generated_at": "2026-05-10T17:00:00Z",
    "results": [
        {
            "company_name": "Seokratie GmbH",
            "website": "https://seokratie.de",
            "source_signal_title": "Content Marketing Manager (m/w/d)",
            "source_signal_url": "https://www.stepstone.de/some-job",
            "fit_status": "target_fit",
            "fit_score": 1.0,
            "contact_name": "",
            "email": "kontakt@seokratie.de",
            "email_type": "generic_email",
            "phone": "+49 89 219 09 84 11",
            "contact_quality": "good",
            "recommended_first_line": "...",
            "outreach_angle": "sales_growth_signal",
            "email_subject": "Kurze Frage zu B2B-Erstgesprächen",
            "email_body": "Hallo Seokratie-Team, ...",
            "ready_for_approval": True,
            "missing_fields": [],
            "next_action": "approve_for_send",
        },
        {
            "company_name": "Blessing Marketing GmbH",
            "website": "",
            "source_signal_title": "Vertriebsmitarbeiter",
            "source_signal_url": "https://www.stepstone.de/other-job",
            "fit_status": "maybe_fit",
            "fit_score": 0.6,
            "contact_name": "",
            "email": "",
            "email_type": "",
            "phone": "",
            "contact_quality": "weak",
            "recommended_first_line": "...",
            "outreach_angle": "marketing_growth_signal",
            "email_subject": "",
            "email_body": "",
            "ready_for_approval": False,
            "missing_fields": ["website", "email"],
            "next_action": "discard",
        },
        {
            "company_name": "THE MARKETER P.S.O. UG",
            "website": "https://example.com",
            "source_signal_title": "PR Manager",
            "source_signal_url": "https://www.stepstone.de/another-job",
            "fit_status": "target_fit",
            "fit_score": 0.9,
            "contact_name": "",
            "email": "info@example.com",
            "email_type": "generic_email",
            "phone": "",
            "contact_quality": "partial",
            "recommended_first_line": "...",
            "outreach_angle": "sales_growth_signal",
            "email_subject": "Kurze Frage",
            "email_body": "Hallo Team, ...",
            "ready_for_approval": False,
            "missing_fields": ["phone"],
            "next_action": "approve_for_send",
        },
    ],
}


def _seed_fixture() -> None:
    LATEST.mkdir(parents=True, exist_ok=True)
    OUTREACH_PREVIEW_FILE.write_text(json.dumps(FIXTURE, ensure_ascii=False, indent=2), encoding="utf-8")


def _patch_no_preview_chain() -> None:
    pipeline._run_preview_chain = lambda: {"target_preview": "skipped_smoke", "outreach_preview": "skipped_smoke"}


def _read_report() -> dict:
    return json.loads(REPORT_JSON.read_text(encoding="utf-8"))


def _read_candidates() -> dict:
    return json.loads(AUTO_SEND_CANDIDATES.read_text(encoding="utf-8"))


def _cleanup_outputs() -> None:
    for path in (REPORT_JSON, REPORT_MD, AUTO_SEND_CANDIDATES):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    _patch_no_preview_chain()
    _seed_fixture()

    # Make sure no stale env enables auto-send
    os.environ.pop("INTENT_AUTO_SEND", None)

    # Optional: ensure ALREADY_SENT_LOG empty for clean test
    backup_sent = None
    if ALREADY_SENT_LOG.exists():
        backup_sent = ALREADY_SENT_LOG.read_text(encoding="utf-8")
        ALREADY_SENT_LOG.unlink()

    backup_dnc = None
    if DO_NOT_CONTACT_FILE.exists():
        backup_dnc = DO_NOT_CONTACT_FILE.read_text(encoding="utf-8")
        DO_NOT_CONTACT_FILE.unlink()

    try:
        # 1. preview mode
        _cleanup_outputs()
        pipeline.run("preview")
        report = _read_report()
        assert report["mode"] == "preview", "preview mode wrong"
        assert report["auto_send_attempted"] == 0, "preview must not attempt sends"
        assert report["auto_sent"] == 0, "preview must not send"
        assert report["companies_processed"] <= 3, "preview must respect MAX_COMPANIES"
        assert REPORT_JSON.exists() and REPORT_MD.exists() and AUTO_SEND_CANDIDATES.exists(), "preview must write all outputs"
        cands = _read_candidates()
        assert cands["mode"] == "preview"
        for c in cands.get("candidates") or []:
            assert c["next_action"] == "ready_for_existing_outreach_pipeline"
        print("[OK] preview mode: no sends, all outputs written")

        # 2. approval mode
        _cleanup_outputs()
        pipeline.run("approval")
        report = _read_report()
        assert report["mode"] == "approval", "approval mode wrong"
        assert report["auto_send_attempted"] == 0, "approval must not attempt sends"
        assert report["auto_sent"] == 0, "approval must not send"
        assert report["companies_processed"] <= 3, "approval must respect MAX_COMPANIES"
        assert AUTO_SEND_CANDIDATES.exists(), "approval must write candidates"
        print("[OK] approval mode: no sends, candidates written")

        # 3. auto mode WITHOUT INTENT_AUTO_SEND => must NOT send
        _cleanup_outputs()
        pipeline.run("auto")
        report = _read_report()
        assert report["mode"] == "auto", "auto mode wrong"
        assert report["auto_send_attempted"] == 0, "auto must not attempt sends without INTENT_AUTO_SEND=true"
        assert report["auto_sent"] == 0, "auto must not send without INTENT_AUTO_SEND=true"
        assert report["skipped_reason"] == "SKIPPED_AUTO_SEND_DISABLED", \
            f"auto mode without env should set skipped_reason, got: {report['skipped_reason']}"
        print("[OK] auto mode without env: skipped, no send")

        # 4. auto_eligible enforcement: only Seokratie eligible (good quality, complete)
        eligible_in_results = [r for r in report["results"] if r.get("auto_eligible")]
        assert len(eligible_in_results) <= 1, f"smoke fixture should have <=1 eligible, got {len(eligible_in_results)}"
        if eligible_in_results:
            assert eligible_in_results[0]["company_name"] == "Seokratie GmbH", \
                "Seokratie should be the eligible candidate"

        # 5. Verify ineligible reasons are populated
        ineligible = [r for r in report["results"] if not r.get("auto_eligible")]
        for r in ineligible:
            assert r.get("auto_eligibility_reasons"), \
                f"{r['company_name']} ineligible but no reasons given"

        # 6. The MAX_AUTO_SEND_PER_RUN limit
        assert report["auto_eligible"] <= 1, "MAX_AUTO_SEND_PER_RUN should cap auto_eligible to 1"

        # 7. Verify file structure of auto_send_candidates
        cands = _read_candidates()
        for c in cands.get("candidates") or []:
            for field in ("company_name", "email", "website", "source_signal_url",
                          "email_subject", "email_body", "contact_quality", "next_action"):
                assert field in c, f"auto_send_candidates entry missing {field}"

        # 8. mine.py and outreach_pipeline.py untouched (no actual imports)
        with open(pipeline.__file__, "r", encoding="utf-8") as f:
            src_lines = f.read().splitlines()
        for line in src_lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("import outreach_pipeline") or stripped.startswith("from outreach_pipeline"):
                raise AssertionError("must not import outreach_pipeline")
            if stripped.startswith("import mine") or stripped.startswith("from mine"):
                raise AssertionError("must not import mine")
        print("[OK] no imports of mine/outreach_pipeline")

        print("SMOKE_OK")
    finally:
        if backup_sent is not None:
            ALREADY_SENT_LOG.write_text(backup_sent, encoding="utf-8")
        if backup_dnc is not None:
            DO_NOT_CONTACT_FILE.write_text(backup_dnc, encoding="utf-8")
