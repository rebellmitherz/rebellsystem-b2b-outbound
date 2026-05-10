from __future__ import annotations

import json
from pathlib import Path

import cockpit_server as cs


class _DummyHandler:
    def __init__(self, path: str):
        self.path = path
        self.status = None
        self.payload = None
        self.body = None

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

    # Existing files should load without crash
    payload = cs._intent_preview_payload()
    assert isinstance(payload, dict), "intent preview payload is not dict"
    assert "available" in payload, "payload missing available"
    if payload.get("available"):
        assert "recommended_default_focus" in payload, "payload missing recommended_default_focus"
        assert "job_detail_summary" in payload, "payload missing job_detail_summary"
        assert "relevance_summary" in payload, "payload missing relevance_summary"
        assert "relevance_fetch_candidates" in payload, "payload missing relevance_fetch_candidates"
        rel_summary = payload.get("relevance_summary")
        if rel_summary:
            assert "total_job_detail_pages" in rel_summary, "relevance_summary missing total_job_detail_pages"
            assert "relevant" in rel_summary, "relevance_summary missing relevant"
            assert "maybe_relevant" in rel_summary, "relevance_summary missing maybe_relevant"
            assert "needs_review" in rel_summary, "relevance_summary missing needs_review"
            assert "irrelevant" in rel_summary, "relevance_summary missing irrelevant"
            assert "fetch_detail_count" in rel_summary, "relevance_summary missing fetch_detail_count"
            assert "review_count" in rel_summary, "relevance_summary missing review_count"
            assert "discard_count" in rel_summary, "relevance_summary missing discard_count"
        for cand in (payload.get("relevance_fetch_candidates") or []):
            assert cand.get("recommended_next_action") == "fetch_detail", "fetch_candidate must have fetch_detail action"
            assert "title" in cand, "fetch_candidate missing title"
            assert "url" in cand, "fetch_candidate missing url"
            assert "relevance_score" in cand, "fetch_candidate missing relevance_score"
            assert "relevance_status" in cand, "fetch_candidate missing relevance_status"
            assert "relevance_reasons" in cand, "fetch_candidate missing relevance_reasons"
            assert "rejection_reasons" in cand, "fetch_candidate missing rejection_reasons"

    # Missing-file case must not crash
    old_decision = cs.INTENT_FOCUS_DECISION_FILE
    old_live = cs.INTENT_JOB_DETAIL_LIVE_FILE
    old_relevance = cs.INTENT_JOB_DETAIL_RELEVANCE_FILE
    try:
        cs.INTENT_FOCUS_DECISION_FILE = Path("__definitely_missing_decision__.json")
        cs.INTENT_JOB_DETAIL_LIVE_FILE = Path("__definitely_missing_live__.json")
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = Path("__definitely_missing_relevance__.json")
        missing_payload = cs._intent_preview_payload()
        assert missing_payload.get("available") is False, "missing payload should be unavailable"
        assert "Intent Preview noch nicht erzeugt" in str(missing_payload.get("message") or ""), "missing payload message wrong"
        assert missing_payload.get("relevance_summary") is None, "missing relevance should be None"
        assert missing_payload.get("relevance_fetch_candidates") == [], "missing candidates should be empty"

        for path in ("/api/intent-preview", "/api/intent-preview/"):
            handler = _DummyHandler(path)
            cs.Handler.do_GET(handler)
            assert handler.status == 200, f"GET {path} should return 200"
            assert isinstance(handler.payload, dict), f"GET {path} should return dict payload"
            assert handler.payload.get("error") != "unknown", f"GET {path} returned unknown error"
            assert handler.payload.get("available") is False, f"GET {path} should return unavailable payload for missing files"
            assert "Intent Preview noch nicht erzeugt" in str(handler.payload.get("message") or ""), f"GET {path} wrong missing-files message"

        html_handler = _DummyHandler("/")
        cs.Handler.do_GET(html_handler)
        html = (html_handler.body or b"").decode("utf-8", errors="replace")
        assert html_handler.status == 200, "GET / should return 200"
        assert "Intent Discovery Preview" in html, "HTML response missing Intent Discovery Preview"
    finally:
        cs.INTENT_FOCUS_DECISION_FILE = old_decision
        cs.INTENT_JOB_DETAIL_LIVE_FILE = old_live
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = old_relevance

    # Only-live-file (no relevance) must not crash and show relevance_summary=None
    try:
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = Path("__definitely_missing_relevance__.json")
        payload_no_rel = cs._intent_preview_payload()
        assert payload_no_rel.get("relevance_summary") is None, "missing relevance file => None summary"
        assert payload_no_rel.get("relevance_fetch_candidates") == [], "missing relevance file => empty candidates"
    finally:
        cs.INTENT_JOB_DETAIL_RELEVANCE_FILE = old_relevance

    print("SMOKE_OK")
