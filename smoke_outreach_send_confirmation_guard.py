"""Offline-Smoke fuer OUTREACH_SEND_CONFIRMED Gate in run_first_sends + run_followups.

Pruefziel: Direkter Erstmail-/Follow-up-Live-Versand erfordert die bewusste
Bestaetigung OUTREACH_SEND_CONFIRMED ∈ {true,1,yes,on}. Composite full-auto /
send-reply-drafts setzen die Variable nach OUTREACH_FULL_AUTO_CONFIRMED-Check
selbst. Reply-Auto-Send-Gate (REPLY_AUTO_SEND_CONFIRMED) bleibt unveraendert.

Kein SMTP. Kein IMAP. Kein mine.py. Keine Outreach-Befehle. Keine echten
Mails. Keine output-Mutation. Send-Helper werden monkey-gepatched, sodass
jeder versehentliche SMTP-Call den Test SOFORT failt.

Aufruf:
    python smoke_outreach_send_confirmation_guard.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Sicherheits-Defaults — wirklich nichts darf je live laufen
os.environ.setdefault("REPLY_AUTO_SEND", "false")
os.environ.setdefault("REPLY_DRY_RUN", "true")
os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)
os.environ.pop("OUTREACH_SEND_CONFIRMED", None)

from modules import outreach_pipeline as op  # noqa: E402

OUTREACH_PIPELINE_PY = ROOT / "modules" / "outreach_pipeline.py"
CAE_CLI_APP_PY = ROOT / "cae" / "cli" / "app.py"


# ── Send-Helper Monkeypatch: jeder Aufruf failt den Test sofort ─────────────
class SmtpUsedError(AssertionError):
    pass


def _no_smtp(*args, **kwargs):
    raise SmtpUsedError("FORBIDDEN: SMTP send helper was called during guard smoke")


# Sicherstellen, dass nichts versehentlich Live geht:
op._send_email_subprocess = _no_smtp  # type: ignore[assignment]
op.send_via_script = _no_smtp  # type: ignore[assignment]


def _set_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _sendable_entry() -> dict[str, Any]:
    return {
        "entry_key": "k_test",
        "company_name": "TestCo",
        "contact_name": "Anna",
        "email": "anna@test.invalid",
        "outreach_stage": "ready",
        "ready_to_send": "yes",
        "approved_for_send": True,
        "do_not_resend": False,
        "first_email_subject": "Kurze Frage",
        "first_email_body": "Hallo Anna",
    }


def _followup_entry() -> dict[str, Any]:
    from datetime import datetime, timedelta
    fs = (datetime.now() - timedelta(days=14)).replace(microsecond=0).isoformat()
    return {
        "entry_key": "k_fu",
        "company_name": "FuCo",
        "contact_name": "Bob",
        "email": "bob@fu.invalid",
        "outreach_stage": "sent",
        "reply_status": "none",
        "do_not_resend": False,
        "first_sent_at": fs,
        "last_contacted_at": fs,
        "first_email_subject": "Kurze Frage",
        "first_email_body": "Hallo Bob",
        "followup_1_text": "Kurze Nachfrage Bob",
    }


def main() -> int:
    # ── A) Helper-Truthy-Matrix ────────────────────────────────────────────
    falsy = [None, "", "false", "FALSE", "0", "no", "off", "bla", "  "]
    for v in falsy:
        _set_env("OUTREACH_SEND_CONFIRMED", v)
        assert op._outreach_send_confirmed() is False, f"A: must be False for {v!r}"
    print(f"PASS A: {len(falsy)} falsy/missing -> _outreach_send_confirmed=False")

    truthy_values = ["true", "1", "yes", "on", "TRUE", "Yes", " on  ", "  TRUE"]
    for v in truthy_values:
        _set_env("OUTREACH_SEND_CONFIRMED", v)
        assert op._outreach_send_confirmed() is True, f"A: must be True for {v!r}"
    _set_env("OUTREACH_SEND_CONFIRMED", None)
    print(f"PASS A: {len(truthy_values)} truthy (case/whitespace-tolerant) -> True")

    # ── B) run_first_sends ohne Confirmation ───────────────────────────────
    state_b = {"entries": [_sendable_entry()]}
    state_b_snapshot_keys = {k: v for k, v in state_b["entries"][0].items()}
    res = op.run_first_sends(state_b, Path("send_email.py"), 5)
    assert res.get("sent") == 0, f"B: sent must be 0, got {res.get('sent')!r}"
    assert res.get("blocked_reason") == "outreach_send_blocked_missing_confirmation", \
        f"B: blocked_reason wrong: {res!r}"
    # State darf nicht mutiert sein (kein last_error, kein stage-Wechsel)
    e = state_b["entries"][0]
    assert e["outreach_stage"] == state_b_snapshot_keys["outreach_stage"], "B: stage mutiert"
    assert "first_sent_at" not in e, "B: first_sent_at darf nicht gesetzt sein"
    assert e.get("last_error", "") == state_b_snapshot_keys.get("last_error", ""), "B: last_error mutiert"
    print("PASS B: run_first_sends ohne Confirmation -> sent=0, kein SMTP, kein State-Write")

    # ── C) run_followups ohne Confirmation ─────────────────────────────────
    state_c = {"entries": [_followup_entry()]}
    snap_c = dict(state_c["entries"][0])
    res = op.run_followups(state_c, Path("send_email.py"), 5)
    assert res.get("sent") == 0, f"C: sent must be 0, got {res!r}"
    assert res.get("blocked_reason") == "outreach_send_blocked_missing_confirmation", \
        f"C: blocked_reason wrong: {res!r}"
    e = state_c["entries"][0]
    assert e["outreach_stage"] == snap_c["outreach_stage"], "C: stage mutiert"
    assert e.get("last_error", "") == snap_c.get("last_error", ""), "C: last_error mutiert"
    assert "sent_message_id" not in e or e["sent_message_id"] == snap_c.get("sent_message_id", ""), \
        "C: sent_message_id mutiert"
    print("PASS C: run_followups ohne Confirmation -> sent=0, kein SMTP, kein State-Write")

    # ── D) Mit OUTREACH_SEND_CONFIRMED=true: Guard blockt nicht mehr ───────
    # Aber kein echter Send: bestehende Gates greifen (approved_for_send=False).
    _set_env("OUTREACH_SEND_CONFIRMED", "true")
    blocked_entry = _sendable_entry()
    blocked_entry["approved_for_send"] = False  # per-Entry-Gate blockiert
    state_d = {"entries": [blocked_entry]}
    res = op.run_first_sends(state_d, Path("send_email.py"), 5)
    # Wichtig: kein blocked_reason (Confirmation gegeben), aber sent=0 wegen Per-Entry-Gate
    assert "blocked_reason" not in res, f"D: confirmation present, no early-block expected: {res!r}"
    assert res.get("sent") == 0, f"D: per-entry gate must still hold, got sent={res.get('sent')!r}"
    assert res.get("skipped_unapproved", 0) >= 1, f"D: skipped_unapproved expected: {res!r}"
    # Per-Entry-Gate setzt last_error
    assert "not_approved_for_send" in (state_d["entries"][0].get("last_error") or ""), \
        f"D: last_error expected: {state_d['entries'][0]!r}"
    print("PASS D: mit Confirmation -> Per-Entry-Gate greift, kein SMTP, blocked_reason fehlt")

    # ── D2) Mit Confirmation: run_followups laeuft durch Per-Entry-Skip ────
    blocked_fu = _followup_entry()
    blocked_fu["do_not_resend"] = True  # per-Entry-Gate blockiert
    state_d2 = {"entries": [blocked_fu]}
    res = op.run_followups(state_d2, Path("send_email.py"), 5)
    assert "blocked_reason" not in res, f"D2: no early-block expected: {res!r}"
    assert res.get("sent") == 0, f"D2: do_not_resend must skip, got {res!r}"
    print("PASS D2: run_followups mit Confirmation + do_not_resend -> sent=0, kein SMTP")

    _set_env("OUTREACH_SEND_CONFIRMED", None)

    # ── E) Composite-Code-Check ────────────────────────────────────────────
    cae_src = CAE_CLI_APP_PY.read_text(encoding="utf-8")
    op_src = OUTREACH_PIPELINE_PY.read_text(encoding="utf-8")
    assert "OUTREACH_FULL_AUTO_CONFIRMED" in cae_src, "E: OUTREACH_FULL_AUTO_CONFIRMED entfernt"
    assert 'os.environ["OUTREACH_SEND_CONFIRMED"] = "true"' in cae_src, \
        "E: Composite setzt OUTREACH_SEND_CONFIRMED nicht"
    assert "prev_send_confirmed" in cae_src, "E: prev_send_confirmed Restore fehlt"
    assert "_outreach_send_confirmed" in op_src, "E: Helper fehlt in outreach_pipeline.py"
    assert "OUTREACH_SEND_CONFIRMED" in op_src, "E: Env-Name fehlt"
    assert "outreach_send_blocked_missing_confirmation" in op_src, "E: blocked_reason marker fehlt"
    print("PASS E: Composite setzt OUTREACH_SEND_CONFIRMED + restored im finally")

    # ── F) Regression: REPLY_AUTO_SEND_CONFIRMED Logik unveraendert ────────
    for marker in (
        "REPLY_AUTO_SEND_CONFIRMED",
        "_reply_auto_send_confirmed",
        "smtp_ok = bool(allow_auto and reply_may_auto_send and not dry and auto_send_confirmed)",
        "OUTREACH_FULL_AUTO_CONFIRMED",
        "auto_send_clear_replies",
        "REPLY_DRY_RUN",
        "export_hot_handoffs_files",
        "operator_reply_suggestion",
        "_build_operator_reply_suggestion",
    ):
        assert marker in op_src, f"F: marker entfernt: {marker!r}"
    # Helper funktional unveraendert
    os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)
    assert op._reply_auto_send_confirmed() is False, "F: reply helper default broken"
    os.environ["REPLY_AUTO_SEND_CONFIRMED"] = "true"
    assert op._reply_auto_send_confirmed() is True, "F: reply helper truthy broken"
    os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)
    print("PASS F: REPLY_AUTO_SEND_CONFIRMED + Hot-Handoff-Logik unveraendert")

    # ── G) Defense-in-depth: leeres state["entries"] auch ohne Conf -> Block
    res = op.run_first_sends({"entries": []}, Path("send_email.py"), 5)
    assert res.get("blocked_reason") == "outreach_send_blocked_missing_confirmation"
    res = op.run_followups({"entries": []}, Path("send_email.py"), 5)
    assert res.get("blocked_reason") == "outreach_send_blocked_missing_confirmation"
    print("PASS G: leerer State -> early-block bleibt erhalten (kein Loop)")

    print("ALL_TESTS_PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
