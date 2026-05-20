"""Offline-Smoke fuer outreach_readiness_report() / --outreach readiness.

Pruefziel: rein read-only Betriebsampel.
- Kein SMTP, kein IMAP, kein mine.py, keine Outreach-Befehle.
- Keine Secrets im Output (PASS/TOKEN/API_KEY-Werte erscheinen nie).
- Keine echten output/**-Mutation: Backup/Restore von relevanten Files.

Aufruf:
    python smoke_outreach_readiness.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Sicherheits-Defaults — niemals Live triggern
os.environ.setdefault("REPLY_AUTO_SEND", "false")
os.environ.setdefault("REPLY_DRY_RUN", "true")
for k in (
    "OUTREACH_FULL_AUTO_CONFIRMED",
    "OUTREACH_SEND_CONFIRMED",
    "REPLY_AUTO_SEND_CONFIRMED",
):
    os.environ.pop(k, None)

from modules import outreach_pipeline as op  # noqa: E402
from config import OUTPUT_DIR  # noqa: E402

OUTPUT_DIR_P = Path(OUTPUT_DIR)
HH_JSON = OUTPUT_DIR_P / "hot_handoffs.json"
RQ_JSON = OUTPUT_DIR_P / "reply_queue.json"
PIPELINE_JSON = op.OUTREACH_PIPELINE_JSON
SENT_LOG_JSON = op.OUTREACH_SENT_LOG_JSON
OUTREACH_PIPELINE_PY = ROOT / "modules" / "outreach_pipeline.py"
CAE_CLI_APP_PY = ROOT / "cae" / "cli" / "app.py"


SECRET_VALUES = [
    "topsecret-abc123",
    "ionos-smtp-pw-XYZ",
    "ionos-imap-pw-XYZ",
    "api-key-9999",
    "totally-not-a-token-42",
]


def _snapshot(paths: list[Path]) -> dict[str, bytes | None]:
    return {str(p): (p.read_bytes() if p.is_file() else None) for p in paths}


def _restore(snap: dict[str, bytes | None]) -> None:
    for path_str, content in snap.items():
        p = Path(path_str)
        if content is None:
            if p.is_file():
                p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)


def _pop_test_env() -> None:
    for k in [
        "OUTREACH_FULL_AUTO_CONFIRMED", "OUTREACH_SEND_CONFIRMED",
        "REPLY_AUTO_SEND_CONFIRMED",
        "OUTREACH_SENDER_1_USER", "OUTREACH_SENDER_1_PASS",
        "OUTREACH_SENDER_1_SMTP_HOST", "OUTREACH_SENDER_1_SMTP_PORT",
        "OUTREACH_SENDER_1_IMAP_HOST", "OUTREACH_SENDER_1_IMAP_PORT",
        "OUTREACH_SENDER_1_DAILY_LIMIT",
        "IONOS_SMTP_USER", "IONOS_SMTP_PASS",
        "IONOS_IMAP_USER", "IONOS_IMAP_PASS",
        "IONOS_DRAFTS_FOLDER",
        "SOME_API_KEY", "REPLY_AUTO_SEND_ROUTES",
    ]:
        os.environ.pop(k, None)


def _assert_no_secret(report_json_text: str) -> None:
    for v in SECRET_VALUES:
        assert v not in report_json_text, f"SECRET LEAK: value {v!r} appeared in JSON"


def main() -> int:
    snap = _snapshot([HH_JSON, RQ_JSON, PIPELINE_JSON, SENT_LOG_JSON])
    try:
        # ── A) Leere Env ────────────────────────────────────────────────────
        _pop_test_env()
        os.environ["REPLY_DRY_RUN"] = "true"
        rep = op.outreach_readiness_report()
        g = rep["confirmation_gates"]
        assert rep["live_ready"] is False, "A: live_ready muss False sein"
        assert g["OUTREACH_FULL_AUTO_CONFIRMED"] is False
        assert g["OUTREACH_SEND_CONFIRMED"] is False
        assert g["REPLY_AUTO_SEND_CONFIRMED"] is False
        assert rep["would_full_auto_block_now"] is True
        assert rep["would_direct_send_block_now"] is True
        assert rep["would_reply_auto_send_block_now"] is True
        for m in ("OUTREACH_FULL_AUTO_CONFIRMED", "OUTREACH_SEND_CONFIRMED", "REPLY_AUTO_SEND_CONFIRMED"):
            assert m in rep["missing_required_confirmations"], f"A: missing {m}"
        print("PASS A: leere Env -> alle Gates blocken, live_ready=False")

        # ── B) Env mit Gates ────────────────────────────────────────────────
        os.environ["OUTREACH_FULL_AUTO_CONFIRMED"] = "true"
        os.environ["OUTREACH_SEND_CONFIRMED"] = "true"
        os.environ["REPLY_AUTO_SEND_CONFIRMED"] = "true"
        os.environ.pop("REPLY_DRY_RUN", None)
        rep = op.outreach_readiness_report()
        g = rep["confirmation_gates"]
        assert g["OUTREACH_FULL_AUTO_CONFIRMED"] is True
        assert g["OUTREACH_SEND_CONFIRMED"] is True
        assert g["REPLY_AUTO_SEND_CONFIRMED"] is True
        assert rep["would_full_auto_block_now"] is False
        assert rep["would_direct_send_block_now"] is False
        # Secrets-Schutz: keine Secret-Werte im JSON (auch wenn noch keine gesetzt sind)
        _assert_no_secret(json.dumps(rep))
        print("PASS B: alle 3 Gates truthy -> would_*_block=False")

        # ── C) Fake Sender Slot + Secret-Schutz ─────────────────────────────
        os.environ["OUTREACH_SENDER_1_USER"] = "test@example.com"
        os.environ["OUTREACH_SENDER_1_PASS"] = "topsecret-abc123"
        os.environ["OUTREACH_SENDER_1_SMTP_HOST"] = "smtp.example.com"
        os.environ["OUTREACH_SENDER_1_SMTP_PORT"] = "587"
        os.environ["OUTREACH_SENDER_1_IMAP_HOST"] = "imap.example.com"
        os.environ["OUTREACH_SENDER_1_IMAP_PORT"] = "993"
        os.environ["OUTREACH_SENDER_1_DAILY_LIMIT"] = "5"
        rep = op.outreach_readiness_report()
        c = rep["credentials_presence"]
        assert c["outreach_sender_slots_configured"] >= 1, f"C: slot count: {c}"
        assert c["smtp_config_present"] is True, "C: smtp_config_present"
        assert c["imap_config_present"] is True, "C: imap_config_present"
        body = json.dumps(rep)
        _assert_no_secret(body)
        assert "topsecret-abc123" not in body, "C: PASS value leaked"
        # User/Host gehoeren ebenfalls nicht in den Report
        assert "test@example.com" not in body, "C: USER value leaked"
        assert "smtp.example.com" not in body, "C: SMTP host leaked"
        assert "imap.example.com" not in body, "C: IMAP host leaked"
        print("PASS C: Sender-Slot erkannt, keine Werte/Secrets im Output")

        # ── D) Mehrere Fake-Secrets ─────────────────────────────────────────
        os.environ["IONOS_SMTP_USER"] = "ionos-user"
        os.environ["IONOS_SMTP_PASS"] = "ionos-smtp-pw-XYZ"
        os.environ["IONOS_IMAP_USER"] = "ionos-imap-user"
        os.environ["IONOS_IMAP_PASS"] = "ionos-imap-pw-XYZ"
        os.environ["SOME_API_KEY"] = "api-key-9999"
        os.environ["IONOS_DRAFTS_FOLDER"] = "Drafts"
        rep = op.outreach_readiness_report()
        c = rep["credentials_presence"]
        assert c["single_sender_fallback_configured"] is True, "D: ionos_smtp_present"
        assert c["drafts_folder_config_present"] is True, "D: drafts folder"
        body = json.dumps(rep)
        _assert_no_secret(body)
        for leak in ("ionos-user", "ionos-imap-user"):
            assert leak not in body, f"D: USER leaked {leak}"
        print("PASS D: mehrere Fake-Secrets, kein Wert im JSON")

        # ── E) Fehlende Dateien ─────────────────────────────────────────────
        for p in (HH_JSON, RQ_JSON, PIPELINE_JSON, SENT_LOG_JSON):
            if p.is_file():
                p.unlink()
        rep = op.outreach_readiness_report()
        rp = rep["reports_presence"]
        assert rp["outreach_pipeline_exists"] is False, "E: pipeline must be missing"
        assert rp["reply_queue_exists"] is False, "E: queue must be missing"
        assert rp["hot_handoffs_exists"] is False, "E: hot_handoffs must be missing"
        assert rp["sent_log_exists"] is False, "E: sent_log must be missing"
        c = rep["pipeline_counts"]
        assert c["total_entries"] == 0 and c["hot_handoffs_count"] == 0 and c["reply_queue_count"] == 0
        # Kein Crash
        assert rep["ok"] is True
        print("PASS E: fehlende Dateien -> counts=0, exists=False, kein Crash")

        # ── F) Pipeline-Counts ──────────────────────────────────────────────
        from datetime import datetime, timedelta
        old_fs = (datetime.now() - timedelta(days=14)).replace(microsecond=0).isoformat()
        pipeline = {"version": 1, "entries": [
            {"ready_to_send": "yes", "approved_for_send": True,
             "outreach_stage": "ready", "review_status": "send_ready"},
            {"ready_to_send": "yes", "approved_for_send": False,
             "outreach_stage": "new", "review_status": "review"},
            {"ready_to_send": "no", "outreach_stage": "sent",
             "review_status": "send_ready", "first_sent_at": old_fs, "reply_status": "none"},
            {"outreach_stage": "lost", "do_not_resend": True,
             "review_status": "reject"},
        ]}
        PIPELINE_JSON.parent.mkdir(parents=True, exist_ok=True)
        PIPELINE_JSON.write_text(json.dumps(pipeline), encoding="utf-8")
        rep = op.outreach_readiness_report()
        c = rep["pipeline_counts"]
        assert c["total_entries"] == 4, f"F: total_entries={c['total_entries']}"
        assert c["ready_to_send"] == 2, f"F: ready_to_send={c['ready_to_send']}"
        assert c["approved_for_send"] == 1, f"F: approved={c['approved_for_send']}"
        assert c["review"] == 1, f"F: review={c['review']}"
        assert c["reject"] == 1, f"F: reject={c['reject']}"
        assert c["sent"] == 1, f"F: sent={c['sent']}"
        assert c["do_not_resend"] == 1, f"F: dnr={c['do_not_resend']}"
        assert c["followup_due"] >= 1, f"F: followup_due={c['followup_due']}"
        print("PASS F: Pipeline-Counts korrekt aus synthetischem State")

        # ── G) Freshness: reply_queue neuer als hot_handoffs ────────────────
        HH_JSON.write_text(json.dumps({"count": 0, "handoffs": []}), encoding="utf-8")
        time.sleep(1.1)  # ensure mtime differs
        RQ_JSON.write_text(json.dumps({"items": [{"x": 1}]}), encoding="utf-8")
        rep = op.outreach_readiness_report()
        fr = rep["freshness"]
        assert fr["hot_handoffs_stale_vs_reply_queue"] is True, f"G: stale flag: {fr}"
        assert rep["reports_presence"]["hot_handoffs_exists"] is True
        assert rep["reports_presence"]["reply_queue_exists"] is True
        assert any("older than reply_queue" in w for w in rep["warnings"]), f"G: warning missing: {rep['warnings']}"
        # Umkehr
        time.sleep(1.1)
        HH_JSON.write_text(json.dumps({"count": 0, "handoffs": []}), encoding="utf-8")
        rep = op.outreach_readiness_report()
        assert rep["freshness"]["hot_handoffs_stale_vs_reply_queue"] is False, "G: reverse"
        print("PASS G: Freshness-Vergleich reply_queue vs hot_handoffs korrekt")

        # ── H) Code-Regression ──────────────────────────────────────────────
        src = OUTREACH_PIPELINE_PY.read_text(encoding="utf-8")
        for marker in (
            "OUTREACH_SEND_CONFIRMED",
            "REPLY_AUTO_SEND_CONFIRMED",
            "_outreach_send_confirmed",
            "_reply_auto_send_confirmed",
            "outreach_send_blocked_missing_confirmation",
            "reply_auto_send_blocked_missing_confirmation",
            "smtp_ok = bool(allow_auto and reply_may_auto_send and not dry and auto_send_confirmed)",
            "export_hot_handoffs_files",
            "_build_operator_reply_suggestion",
        ):
            assert marker in src, f"H: marker entfernt: {marker!r}"
        # Readiness darf KEIN smtplib/imaplib einfuehren
        assert "import smtplib" not in src, "H: smtplib added"
        # Readiness-Funktion existiert
        assert "def outreach_readiness_report" in src, "H: readiness function missing"
        assert 'if action == "readiness"' in src, "H: action branch missing"
        cae = CAE_CLI_APP_PY.read_text(encoding="utf-8")
        assert "OUTREACH_FULL_AUTO_CONFIRMED" in cae, "H: composite guard removed"
        assert '"readiness"' in cae, "H: readiness choice missing"
        # Composite-Set bleibt unveraendert
        assert 'os.environ["OUTREACH_SEND_CONFIRMED"] = "true"' in cae, "H: composite send set removed"
        print("PASS H: alle Confirmation-/Auto-Send-Gates + Helper unveraendert")

        # ── I) Helper-Funktional-Regression ─────────────────────────────────
        os.environ.pop("OUTREACH_SEND_CONFIRMED", None)
        assert op._outreach_send_confirmed() is False
        os.environ["OUTREACH_SEND_CONFIRMED"] = "true"
        assert op._outreach_send_confirmed() is True
        os.environ.pop("OUTREACH_SEND_CONFIRMED", None)
        os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)
        assert op._reply_auto_send_confirmed() is False
        os.environ["REPLY_AUTO_SEND_CONFIRMED"] = "true"
        assert op._reply_auto_send_confirmed() is True
        os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)
        print("PASS I: _outreach_send_confirmed + _reply_auto_send_confirmed funktional unveraendert")

        # ── J) Schema-Vollstaendigkeit ──────────────────────────────────────
        rep = op.outreach_readiness_report()
        for key in (
            "ok", "mode", "live_ready",
            "confirmation_gates", "credentials_presence", "pipeline_counts",
            "reports_presence", "freshness",
            "would_full_auto_block_now", "would_direct_send_block_now",
            "would_reply_auto_send_block_now",
            "missing_required_confirmations", "warnings", "next_safe_step",
        ):
            assert key in rep, f"J: missing key {key!r}"
        assert rep["mode"] == "readiness"
        assert isinstance(rep["next_safe_step"], str) and rep["next_safe_step"], "J: next_safe_step"
        print("PASS J: vollstaendiges Schema, mode='readiness'")

        print("ALL_TESTS_PASSED")
        return 0
    finally:
        _pop_test_env()
        _restore(snap)


if __name__ == "__main__":
    sys.exit(main())
