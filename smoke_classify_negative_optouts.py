"""Offline-Smoke fuer die _HARD_NO-Erweiterung in modules/reply_intelligence.py.

Nur In-Memory-Dicts und reine Klassifikations-Funktionen. Kein IMAP. Kein SMTP.
Keine Pipeline-Dateien werden gelesen oder geschrieben. Keine Outreach-Befehle.

Pruefungen:
  A) Neue Opt-Out-Phrasen werden als negative klassifiziert
  B) Bestehende Hard-No-Patterns bleiben negative (Regression)
  C) Sensitive/legal bleibt unclear + human (Regression)
  D) Negative-with-potential bleibt erkannt (Regression)
  E) Positive Termin-Antwort bleibt nicht negative (Regression)
  F) "stop by my office" wird NICHT mehr negative (Verbesserung)
  G) End-to-End apply_reply_to_entry(negative) → Pipeline-Felder korrekt
  H) Appointment-Bridge (a96c9bf) bleibt unbeschädigt

Aufruf:
    python smoke_classify_negative_optouts.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Sicherheits-Defaults: niemals Auto-Reply / Live-Versand triggern, falls
# Module zur Importzeit irgendetwas streamen wuerden.
os.environ.setdefault("REPLY_AUTO_SEND", "false")
os.environ.setdefault("REPLY_DRY_RUN", "true")

from modules.reply_intelligence import (  # noqa: E402
    classify_inbound,
    must_escalate_human,
    negative_with_potential,
)
from modules.outreach_pipeline import apply_reply_to_entry  # noqa: E402


# ── A) Neue Opt-Out-Phrasen (inkl. standalone-stop) ──────────────────────────
A_NEW_HARD_NO = [
    "Please remove me from your mailing list.",
    "Take me off your list.",
    "Do not contact me again.",
    "Don't contact me again.",
    "Do not email me anymore.",
    "Don't email me anymore.",
    "Please remove this address.",
    "Remove me.",
    "Bitte keine weiteren Mails.",
    "Bitte keine Mails mehr.",
    "Wir möchten keine Werbung erhalten.",
    "Wir wünschen keine Werbung.",
    "Bitte aus dem Verteiler entfernen.",
    "Austragen bitte.",
    "Bitte austragen.",
    "Nicht weiter anschreiben bitte.",
    "Bitte nicht erneut anschreiben.",
    # Standalone stop — mit Satzzeichen und bare (SMS-Style)
    "Stop.",
    "STOP!",
    "stop",
    "Stop",
    "STOP",
    "stop?",
    "Just stop.",
]


def test_A_new_optouts_are_negative():
    for s in A_NEW_HARD_NO:
        cls, conf = classify_inbound(s)
        assert cls == "negative" and conf >= 0.80, f"A FAIL: {s!r} -> ({cls}, {conf})"
    print(f"PASS A: {len(A_NEW_HARD_NO)} neue Opt-Out-Phrasen -> negative >=0.80")


# ── B) Bestehende Hard-Nos (Regression) ──────────────────────────────────────
B_EXISTING = [
    "Unsubscribe please.",
    "Kein Interesse, danke.",
    "Nicht mehr kontaktieren bitte.",
    "Bitte löschen Sie meine Daten.",
    "Abmelden vom Newsletter.",
]


def test_B_existing_hard_nos_regression():
    for s in B_EXISTING:
        cls, conf = classify_inbound(s)
        assert cls == "negative" and conf >= 0.80, f"B FAIL: {s!r} -> ({cls}, {conf})"
    print(f"PASS B: {len(B_EXISTING)} bestehende Hard-Nos -> negative >=0.80")


# ── C) Sensitive bleibt unclear + human ──────────────────────────────────────
# Nach Edge-Patch matched _SENSITIVE_PATTERNS auch deklinierte Formen von
# "datenschutzbeauftrag" via \w*-Suffix (Datenschutzbeauftragter/-te/-ten).
C_SENSITIVE_PHRASES = [
    "Mein Anwalt prüft das.",
    "Bitte den Datenschutzbeauftragten kontaktieren.",
    "Mein Datenschutzbeauftragter prüft das.",
    "Datenschutzbeauftragte wird sich melden.",
]


def test_C_sensitive_routes_to_human():
    for s in C_SENSITIVE_PHRASES:
        cls, conf = classify_inbound(s)
        assert cls == "unclear" and conf >= 0.85, f"C cls FAIL: {s!r} -> ({cls}, {conf})"
        assert must_escalate_human(s) is True, f"C must_escalate_human FAIL: {s!r}"
    print(f"PASS C: {len(C_SENSITIVE_PHRASES)} sensitive Phrasen -> unclear + must_escalate_human=True")


# ── D) Negative with potential ───────────────────────────────────────────────
def test_D_negative_with_potential():
    s = "Kein Bedarf, aber vielleicht später nochmal besprechen?"
    assert negative_with_potential(s) is True, (
        f"D FAIL: negative_with_potential({s!r}) should be True"
    )
    print("PASS D: 'kein bedarf aber vielleicht später' -> negative_with_potential=True")


# ── E) Positive bleibt nicht negative ────────────────────────────────────────
def test_E_positive_stays_not_negative():
    s = "Sounds interesting — can we schedule a call next week?"
    cls, conf = classify_inbound(s)
    assert cls != "negative", f"E FAIL: positive missclassified -> ({cls}, {conf})"
    print(f"PASS E: positive Termin-Reply -> {cls} (nicht negative)")


# ── F) "stop by" Verbesserung — kein false-positive bei positivem "stop by" ──
F_STOP_BY_NOT_NEGATIVE = [
    "Stop by my office anytime.",
    "Can you stop by tomorrow?",
    "We should stop by later.",
]


def test_F_stop_by_is_not_negative():
    for s in F_STOP_BY_NOT_NEGATIVE:
        cls, conf = classify_inbound(s)
        assert cls != "negative", f"F FAIL: {s!r} -> ({cls}, {conf})"
    print(f"PASS F: {len(F_STOP_BY_NOT_NEGATIVE)} 'stop by'-Phrasen bleiben nicht-negative")


# ── G) End-to-End apply_reply_to_entry("negative") ───────────────────────────
def test_G_apply_reply_to_entry_negative():
    e = {
        "outreach_stage": "sent",
        "reply_status": "none",
        "appointment_ready": False,
        "next_followup_at": "2026-05-22T10:00:00",
    }
    apply_reply_to_entry(e, "negative")
    assert e["outreach_stage"] == "lost", f"G stage FAIL: {e['outreach_stage']!r}"
    assert e.get("do_not_resend") is True, "G do_not_resend FAIL"
    assert e["next_followup_at"] == "", "G next_followup_at FAIL"
    assert e["reply_status"] == "negative", "G reply_status FAIL"
    assert e["lead_temperature"] == "cold", "G lead_temperature FAIL"
    print("PASS G: apply_reply_to_entry(negative) -> lost + do_not_resend + cleared followup")


# ── H) Appointment-Bridge Regression (a96c9bf) ───────────────────────────────
def test_H_appointment_bridge_regression():
    e = {
        "outreach_stage": "sent",
        "reply_status": "none",
        "appointment_ready": True,
        "inbound_class": "interested",
        "inbound_confidence": 0.7,
        "next_followup_at": "2026-05-22T10:00:00",
    }
    apply_reply_to_entry(e, "interested")
    assert e["outreach_stage"] == "hot", f"H stage FAIL: {e['outreach_stage']!r}"
    assert e["lead_temperature"] == "hot", "H lead_temperature FAIL"
    assert e["reply_status"] == "interested", "H reply_status FAIL"
    assert e["next_followup_at"] == "", "H next_followup_at FAIL"
    assert e.get("handoff_next_action"), "H handoff_next_action FAIL"
    assert e.get("do_not_resend") in (False, None), "H must NOT set do_not_resend for hot"
    print("PASS H: appointment-bridge (interested+appointment_ready) -> hot, unbeschädigt")


if __name__ == "__main__":
    test_A_new_optouts_are_negative()
    test_B_existing_hard_nos_regression()
    test_C_sensitive_routes_to_human()
    test_D_negative_with_potential()
    test_E_positive_stays_not_negative()
    test_F_stop_by_is_not_negative()
    test_G_apply_reply_to_entry_negative()
    test_H_appointment_bridge_regression()
    print("ALL_TESTS_PASSED")
