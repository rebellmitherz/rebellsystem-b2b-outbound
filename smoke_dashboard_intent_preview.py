from __future__ import annotations

import json
import tempfile
from pathlib import Path

import cockpit_server as cs


class _DummyHandler:
    def __init__(self, path: str):
        self.path = path
        self.status = None
        self.payload = None
        self.body = None

    def _read(self):
        return {}

    def _json(self, payload, status=200):
        self.status = status
        self.payload = payload
        return payload

    def _send(self, status, body, content_type="text/html; charset=utf-8"):
        self.status = status
        self.body = body
        return body


if __name__ == "__main__":
    assert "Intent Discovery Preview" in cs.PREMIUM_HTML, "HTML missing Intent Discovery Preview section"

    # 1. Target Preview Report file reference exists
    assert cs.INTENT_TARGET_PREVIEW_FILE is not None, "missing INTENT_TARGET_PREVIEW_FILE"
    assert cs.INTENT_TARGET_PREVIEW_SCRIPT is not None, "missing INTENT_TARGET_PREVIEW_SCRIPT"

    # 2. Button text present in HTML
    assert "Intent Preview starten" in cs.PREMIUM_HTML, "HTML missing button text"

    # 3. /api/intent-target-preview/run endpoint exists (test route recognition)
    handler = _DummyHandler("/api/intent-target-preview/run")
    cs.Handler.do_POST(handler)
    assert handler.status == 200, f"POST /api/intent-target-preview/run should return 200, got {handler.status}"
    assert isinstance(handler.payload, dict), "POST should return dict"
    assert "job_id" in handler.payload, "POST missing job_id"

    # 4. Existing files should load without crash (with target_preview_report)
    payload = cs._intent_preview_payload()
    assert isinstance(payload, dict), "intent preview payload is not dict"
    assert "available" in payload, "payload missing available"
    assert "target_preview_report" in payload, "payload missing target_preview_report"
    if payload.get("available"):
        tpr = payload.get("target_preview_report")
        if tpr and tpr.get("available"):
            assert "queries_used" in tpr, "target_preview missing queries_used"
            assert "raw_results" in tpr, "target_preview missing raw_results"
            assert "unique_job_detail_pages" in tpr, "target_preview missing unique_job_detail_pages"
            assert "fetched_details" in tpr, "target_preview missing fetched_details"
            assert "resolved_companies" in tpr, "target_preview missing resolved_companies"
            assert "target_fit" in tpr, "target_preview missing target_fit"
            assert "maybe_fit" in tpr, "target_preview missing maybe_fit"
            assert "discard" in tpr, "target_preview missing discard"
            assert "candidates" in tpr, "target_preview missing candidates"
            for cand in tpr.get("candidates") or []:
                assert "company" in cand, "candidate missing company"
                assert "fit_status" in cand, "candidate missing fit_status"
                assert "score" in cand, "candidate missing score"
                assert "next_action" in cand, "candidate missing next_action"
                assert "source_url" in cand, "candidate missing source_url"

        assert "recommended_default_focus" in payload, "payload missing recommended_default_focus"
        assert "job_detail_summary" in payload, "payload missing job_detail_summary"
        assert "relevance_summary" in payload, "payload missing relevance_summary"
        assert "relevance_fetch_candidates" in payload, "payload missing relevance_fetch_candidates"

    # 5. Missing-file case must not crash
    old_decision = cs.INTENT_FOCUS_DECISION_FILE
    old_live = cs.INTENT_JOB_DETAIL_LIVE_FILE
    old_relevance = cs.INTENT_JOB_DETAIL_RELEVANCE_FILE
    old_target = cs.INTENT_TARGET_PREVIEW_FILE
    try:
        cs.INTENT_FOCUS_DECISION_FILE = Path("__definitely_missing_decision__.json")
        cs.INTENT_JOB_DETAIL_LIVE_FILE = Path("__definitely_missing_live__.json")
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = Path("__definitely_missing_relevance__.json")
        cs.INTENT_TARGET_PREVIEW_FILE = Path("__definitely_missing_target_preview__.json")
        missing_payload = cs._intent_preview_payload()
        assert missing_payload.get("available") is False, "missing payload should be unavailable"
        assert missing_payload.get("target_preview_report") is None, "missing target_preview_report should be None"
        assert "Intent Preview noch nicht erzeugt" in str(missing_payload.get("message") or ""), "missing payload message wrong"

        for path in ("/api/intent-preview", "/api/intent-preview/"):
            handler = _DummyHandler(path)
            cs.Handler.do_GET(handler)
            assert handler.status == 200, f"GET {path} should return 200"
            assert isinstance(handler.payload, dict), f"GET {path} should return dict payload"
            assert handler.payload.get("available") is False, f"GET {path} should return unavailable payload"
            assert handler.payload.get("target_preview_report") is None, f"GET {path} target_preview should be None when missing"

        html_handler = _DummyHandler("/")
        cs.Handler.do_GET(html_handler)
        html = (html_handler.body or b"").decode("utf-8", errors="replace")
        assert html_handler.status == 200, "GET / should return 200"
        assert "Intent Discovery Preview" in html, "HTML response missing Intent Discovery Preview"
        assert "Intent Preview starten" in html, "HTML missing button text after missing-file case"
    finally:
        cs.INTENT_FOCUS_DECISION_FILE = old_decision
        cs.INTENT_JOB_DETAIL_LIVE_FILE = old_live
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = old_relevance
        cs.INTENT_TARGET_PREVIEW_FILE = old_target

    # 6. Nur target_preview_report vorhanden => available muss true sein
    tmpdir = Path(tempfile.mkdtemp(prefix="intent_target_preview_only_"))
    target_only_file = tmpdir / "intent_target_preview_report.json"
    target_only_file.write_text(json.dumps({
        "queries_used": 5,
        "raw_results": 9,
        "unique_job_detail_pages": 4,
        "fetched_details": 3,
        "resolved_companies": 3,
        "target_fit": 2,
        "maybe_fit": 1,
        "discard": 0,
        "results": [
            {
                "company_name": "Seokratie GmbH",
                "fit_status": "target_fit",
                "fit_score": 1.0,
                "next_action": "candidate_for_manual_followup",
                "url": "https://example.com/seokratie"
            }
        ]
    }, ensure_ascii=False), encoding="utf-8")
    try:
        cs.INTENT_FOCUS_DECISION_FILE = Path("__definitely_missing_decision__.json")
        cs.INTENT_JOB_DETAIL_LIVE_FILE = Path("__definitely_missing_live__.json")
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = Path("__definitely_missing_relevance__.json")
        cs.INTENT_TARGET_PREVIEW_FILE = target_only_file
        target_only_payload = cs._intent_preview_payload()
        assert target_only_payload.get("available") is True, "target-only payload should be available"
        assert str(target_only_payload.get("message") or "") == "", "target-only payload should not show missing message"
        tpr = target_only_payload.get("target_preview_report")
        assert isinstance(tpr, dict) and tpr.get("available") is True, "target-only report missing or unavailable"
        assert tpr.get("queries_used") == 5, "target-only queries_used wrong"
        assert tpr.get("resolved_companies") == 3, "target-only resolved_companies wrong"
        assert len(tpr.get("candidates") or []) == 1, "target-only candidates wrong"

        handler = _DummyHandler("/api/intent-preview")
        cs.Handler.do_GET(handler)
        assert handler.status == 200, "GET /api/intent-preview should return 200 in target-only case"
        assert handler.payload.get("available") is True, "GET /api/intent-preview should be available in target-only case"
        assert handler.payload.get("target_preview_report", {}).get("available") is True, "GET target report unavailable"

        html_handler = _DummyHandler("/")
        cs.Handler.do_GET(html_handler)
        html = (html_handler.body or b"").decode("utf-8", errors="replace")
        assert "Target-Industry Preview Report" in html, "HTML missing Target Intent Preview section"

        leads_handler = _DummyHandler("/api/leads")
        cs.Handler.do_GET(leads_handler)
        assert leads_handler.status == 200, "/api/leads should remain unchanged"
        assert isinstance(leads_handler.payload, dict), "/api/leads payload should be dict"
        assert "items" in leads_handler.payload, "/api/leads missing items"
    finally:
        cs.INTENT_FOCUS_DECISION_FILE = old_decision
        cs.INTENT_JOB_DETAIL_LIVE_FILE = old_live
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = old_relevance
        cs.INTENT_TARGET_PREVIEW_FILE = old_target

    # 7. Only-live-file (no relevance/target) must not crash
    try:
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = Path("__definitely_missing_relevance__.json")
        cs.INTENT_TARGET_PREVIEW_FILE = Path("__definitely_missing_target_preview__.json")
        payload_no_rel = cs._intent_preview_payload()
        assert payload_no_rel.get("relevance_summary") is None, "missing relevance file => None summary"
        assert payload_no_rel.get("relevance_fetch_candidates") == [], "missing relevance file => empty candidates"
        assert payload_no_rel.get("target_preview_report") is None, "missing target file => None report"
    finally:
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = old_relevance
        cs.INTENT_TARGET_PREVIEW_FILE = old_target

    print("SMOKE_OK")
