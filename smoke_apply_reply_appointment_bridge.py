"""Offline-Smoke für den Appointment-Bridge-Patch in apply_reply_to_entry.

Nur In-Memory-Dicts. Kein IMAP. Kein SMTP. Keine Pipeline-Dateien werden gelesen
oder geschrieben. Keine Outreach-Befehle. Importiert lediglich apply_reply_to_entry
und prueft das Stage/Status-Verhalten auf synthetischen Entries.

Aufruf:
    python smoke_apply_reply_appointment_bridge.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Sicherheits-Defaults: keine echte Auto-Send-Logik soll laufen koennen, auch wenn
# spaeter im Modul etwas getriggert wuerde.
os.environ.setdefault("REPLY_AUTO_SEND", "false")
os.environ.setdefault("REPLY_DRY_RUN", "true")

from modules.outreach_pipeline import apply_reply_to_entry  # noqa: E402


def _base_entry(**overrides):
    e = {
        "entry_key": "test-entry-001",
        "company_name": "TestCo GmbH",
        "contact_name": "Anna Beispiel",
        "email": "anna@testco.de",
        "outreach_stage": "sent",
        "first_sent_at": "2026-05-10T10:00:00",
        "next_followup_at": "2026-05-13T10:00:00",
        "appointment_ready": False,
        "appointment_reason": "",
        "inbound_class": "",
        "inbound_confidence": 0.0,
        "reply_status": "none",
        "do_not_resend": False,
        "reply_needs_human_approval": False,
    }
    e.update(overrides)
    return e


def case_1_interested_with_appointment_promotes_to_hot():
    e = _base_entry(
        outreach_stage="sent",
        appointment_ready=True,
        appointment_reason="meeting_intent",
        inbound_class="interested",
        inbound_confidence=0.72,
    )
    apply_reply_to_entry(e, "interested")
    assert e["outreach_stage"] == "hot", f"expected hot, got {e['outreach_stage']!r}"
    assert e["lead_temperature"] == "hot"
    assert e["reply_status"] == "interested", f"reply_status should stay interested, got {e['reply_status']!r}"
    assert e["next_followup_at"] == ""
    assert e.get("handoff_next_action"), "handoff_next_action must be filled"
    assert e.get("handoff_summary"), "handoff_summary must be filled"
    assert e.get("why_hot"), "why_hot must be filled"
    assert e.get("termin_suggestion"), "termin_suggestion must be filled"
    assert e["conversation_status"] == "interested"
    assert e.get("do_not_resend") is False, "do_not_resend must NOT be set for hot leads"
    assert e["inbound_class"] == "interested", "existing inbound_class must be preserved"
    print("PASS: case_1 interested+appointment_ready -> hot")


def case_2_positive_with_appointment_unchanged():
    e = _base_entry(
        outreach_stage="sent",
        appointment_ready=True,
        appointment_reason="positive_reply",
        inbound_class="positive",
        inbound_confidence=0.85,
    )
    apply_reply_to_entry(e, "positive")
    assert e["outreach_stage"] == "hot"
    assert e["lead_temperature"] == "hot"
    assert e["reply_status"] == "positive"
    assert e["next_followup_at"] == ""
    assert e.get("handoff_next_action")
    assert e["inbound_class"] == "positive"
    print("PASS: case_2 positive+appointment_ready unchanged hot path")


def case_3_interested_without_appointment_stays_warm():
    e = _base_entry(
        outreach_stage="sent",
        appointment_ready=False,
        inbound_class="interested",
        inbound_confidence=0.42,
    )
    apply_reply_to_entry(e, "interested")
    assert e["outreach_stage"] == "warm", f"expected warm, got {e['outreach_stage']!r}"
    assert e["lead_temperature"] == "warm"
    assert e["reply_status"] == "interested"
    assert e["next_followup_at"] == ""
    assert e["conversation_status"] == "interested"
    print("PASS: case_3 interested without appointment_ready -> warm (unchanged)")


def case_4_negative_with_appointment_stays_lost():
    e = _base_entry(
        outreach_stage="sent",
        appointment_ready=True,  # sticky aus frueherem Reply
        inbound_class="negative",
        inbound_confidence=0.9,
    )
    apply_reply_to_entry(e, "negative")
    assert e["outreach_stage"] == "lost", f"expected lost, got {e['outreach_stage']!r}"
    assert e.get("do_not_resend") is True, "negative must set do_not_resend"
    assert e["lead_temperature"] == "cold"
    assert e["reply_status"] == "negative"
    assert e["conversation_status"] == "not_interested"
    print("PASS: case_4 negative dominates sticky appointment_ready")


def case_5_unclear_with_appointment_not_promoted():
    e = _base_entry(
        outreach_stage="sent",
        appointment_ready=True,
        inbound_class="unclear",
        inbound_confidence=0.3,
    )
    apply_reply_to_entry(e, "unclear")
    assert e["outreach_stage"] == "warm", f"expected warm, got {e['outreach_stage']!r}"
    assert e["lead_temperature"] == "warm"
    assert e["reply_status"] == "unclear"
    assert e.get("reply_needs_human_approval") is True
    assert e["conversation_status"] == "replied"
    print("PASS: case_5 unclear is not promoted by sticky appointment_ready")


def case_6_later_with_appointment_not_promoted():
    e = _base_entry(
        outreach_stage="sent",
        appointment_ready=True,  # sticky aus frueherem Reply
        inbound_class="later",
        inbound_confidence=0.6,
    )
    apply_reply_to_entry(e, "later")
    assert e["outreach_stage"] == "sent", f"later must not touch outreach_stage, got {e['outreach_stage']!r}"
    assert e["reply_status"] == "later"
    assert e["next_followup_at"], "later must set next_followup_at to a future timestamp"
    assert e["conversation_status"] == "replied"
    print("PASS: case_6 later is not promoted by sticky appointment_ready")


if __name__ == "__main__":
    case_1_interested_with_appointment_promotes_to_hot()
    case_2_positive_with_appointment_unchanged()
    case_3_interested_without_appointment_stays_warm()
    case_4_negative_with_appointment_stays_lost()
    case_5_unclear_with_appointment_not_promoted()
    case_6_later_with_appointment_not_promoted()
    print("ALL_TESTS_PASSED")
