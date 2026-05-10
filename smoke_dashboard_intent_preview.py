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

    # Missing-file case must not crash
    old_decision = cs.INTENT_FOCUS_DECISION_FILE
    old_live = cs.INTENT_JOB_DETAIL_LIVE_FILE
    try:
        cs.INTENT_FOCUS_DECISION_FILE = Path("__definitely_missing_decision__.json")
        cs.INTENT_JOB_DETAIL_LIVE_FILE = Path("__definitely_missing_live__.json")
        missing_payload = cs._intent_preview_payload()
        assert missing_payload.get("available") is False, "missing payload should be unavailable"
        assert "Intent Preview noch nicht erzeugt" in str(missing_payload.get("message") or ""), "missing payload message wrong"

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

    print("SMOKE_OK")
