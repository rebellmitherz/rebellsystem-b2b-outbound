"""Offline-Smoke fuer Auto-Refresh + Latest-Mirror der hot_handoffs.* Dateien.

Pruefziel: nach dem Patch wird `export_hot_handoffs_files(state)` automatisch am
Ende von `run_process_replies` aufgerufen, und die erzeugten Dateien werden in
denselben Mirror-Loop nach `output/latest/` kopiert wie reply_events/reply_queue.

Dieser Smoke ruft `run_process_replies` NICHT auf (würde IMAP/SMTP triggern).
Stattdessen wird die Auto-Refresh-Logik isoliert nachgestellt:
  1. synthetischer In-Memory-State mit 4 Entries
  2. `apply_reply_to_entry` pro Entry mit passender Klasse
  3. `export_hot_handoffs_files(state)` direkt
  4. Mirror-Copy gemaess Patch (output/ -> output/latest/)
  5. Assertions auf JSON/CSV-Inhalt
  6. Idempotenz-Check (zweiter Lauf -> identische handoffs-Set)

WICHTIG: Vor Test-Beginn wird ein Backup der echten Output-Files erstellt; nach
Test (im finally-Block) wird der Originalzustand wiederhergestellt. Keine echte
Pipeline-State-Modifikation (`save_pipeline_state` wird NICHT aufgerufen), keine
IMAP/SMTP, kein Mailversand.

Aufruf:
    python smoke_hot_handoff_autorefresh.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Sicherheits-Defaults — nichts wird live ausgeloest
os.environ.setdefault("REPLY_AUTO_SEND", "false")
os.environ.setdefault("REPLY_DRY_RUN", "true")

from modules.outreach_pipeline import (  # noqa: E402
    apply_reply_to_entry,
    export_hot_handoffs_files,
)
from config import OUTPUT_DIR  # noqa: E402

OUTPUT_DIR_P = Path(OUTPUT_DIR)
LATEST_DIR = OUTPUT_DIR_P / "latest"
HH_FILES = ("hot_handoffs.json", "hot_handoffs.csv")


def _snapshot(paths: list[Path]) -> dict[str, bytes | None]:
    snap: dict[str, bytes | None] = {}
    for p in paths:
        snap[str(p)] = p.read_bytes() if p.is_file() else None
    return snap


def _restore(snap: dict[str, bytes | None]) -> None:
    for path_str, content in snap.items():
        p = Path(path_str)
        if content is None:
            if p.is_file():
                p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)


def _mirror_to_latest() -> None:
    """Stellt den Mirror-Loop aus dem Patch nach (output/ -> output/latest/)."""
    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    for name in HH_FILES:
        src = OUTPUT_DIR_P / name
        if src.is_file():
            shutil.copy2(src, LATEST_DIR / name)


def _build_state() -> dict[str, Any]:
    return {
        "entries": [
            {
                "entry_key": "k_positive",
                "company_name": "PositiveCo GmbH",
                "contact_name": "Anna Test",
                "email": "anna@positiveco.invalid",
                "phone": "",
                "website": "positiveco.invalid",
                "outreach_stage": "sent",
                "reply_status": "none",
                "appointment_ready": False,
                "first_sent_at": "2026-05-15T10:00:00",
                "last_contacted_at": "2026-05-15T10:00:00",
                "next_followup_at": "2026-05-18T10:00:00",
                "first_email_subject": "Kurze Frage",
                "first_email_body": "Hallo Anna, ...",
                "inbound_class": "positive",
                "inbound_confidence": 0.86,
            },
            {
                "entry_key": "k_appt",
                "company_name": "AppointmentCo GmbH",
                "contact_name": "Bob Termin",
                "email": "bob@apptco.invalid",
                "phone": "",
                "website": "apptco.invalid",
                "outreach_stage": "sent",
                "reply_status": "none",
                "appointment_ready": True,
                "appointment_reason": "meeting_intent",
                "first_sent_at": "2026-05-15T11:00:00",
                "last_contacted_at": "2026-05-15T11:00:00",
                "next_followup_at": "2026-05-18T11:00:00",
                "first_email_subject": "Kurze Frage",
                "first_email_body": "Hallo Bob, ...",
                "inbound_class": "interested",
                "inbound_confidence": 0.72,
            },
            {
                "entry_key": "k_warm",
                "company_name": "WarmCo GmbH",
                "contact_name": "Carla Warm",
                "email": "carla@warmco.invalid",
                "phone": "",
                "website": "warmco.invalid",
                "outreach_stage": "sent",
                "reply_status": "none",
                "appointment_ready": False,
                "first_sent_at": "2026-05-15T12:00:00",
                "last_contacted_at": "2026-05-15T12:00:00",
                "next_followup_at": "2026-05-18T12:00:00",
                "first_email_subject": "Kurze Frage",
                "first_email_body": "Hallo Carla, ...",
                "inbound_class": "interested",
                "inbound_confidence": 0.42,  # low conf < 0.52 -> nicht im Handoff
            },
            {
                "entry_key": "k_negative",
                "company_name": "NegativeCo GmbH",
                "contact_name": "Dirk Nope",
                "email": "dirk@negativeco.invalid",
                "phone": "",
                "website": "negativeco.invalid",
                "outreach_stage": "sent",
                "reply_status": "none",
                "appointment_ready": False,
                "first_sent_at": "2026-05-15T13:00:00",
                "last_contacted_at": "2026-05-15T13:00:00",
                "next_followup_at": "2026-05-18T13:00:00",
                "first_email_subject": "Kurze Frage",
                "first_email_body": "Hallo Dirk, ...",
                "inbound_class": "negative",
                "inbound_confidence": 0.85,
            },
        ]
    }


def _read_handoffs_json() -> dict[str, Any]:
    return json.loads((OUTPUT_DIR_P / "hot_handoffs.json").read_text(encoding="utf-8"))


def _read_handoffs_csv() -> list[dict[str, str]]:
    raw = (OUTPUT_DIR_P / "hot_handoffs.csv").read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw)))


def main() -> int:
    target_files = [
        OUTPUT_DIR_P / "hot_handoffs.json",
        OUTPUT_DIR_P / "hot_handoffs.csv",
        LATEST_DIR / "hot_handoffs.json",
        LATEST_DIR / "hot_handoffs.csv",
    ]
    snap = _snapshot(target_files)
    try:
        # ── Setup: synthetischer State + apply_reply_to_entry pro Entry ─────────
        state = _build_state()
        e_pos, e_appt, e_warm, e_neg = state["entries"]
        apply_reply_to_entry(e_pos, "positive")
        apply_reply_to_entry(e_appt, "interested")  # a96c9bf-Bridge -> hot
        apply_reply_to_entry(e_warm, "interested")  # ohne appt -> warm
        apply_reply_to_entry(e_neg, "negative")

        assert e_pos["outreach_stage"] == "hot", f"pre: positive stage {e_pos['outreach_stage']!r}"
        assert e_appt["outreach_stage"] == "hot", f"pre: appt stage {e_appt['outreach_stage']!r}"
        assert e_warm["outreach_stage"] == "warm", f"pre: warm stage {e_warm['outreach_stage']!r}"
        assert e_neg["outreach_stage"] == "lost", f"pre: neg stage {e_neg['outreach_stage']!r}"

        # ── A) export + Mirror ──────────────────────────────────────────────────
        export_hot_handoffs_files(state)
        _mirror_to_latest()

        # B) JSON-Inhalt
        hh = _read_handoffs_json()
        emails = {row.get("email", "") for row in hh.get("handoffs", [])}

        assert "anna@positiveco.invalid" in emails, "A1 FAIL: positive lead missing in hot_handoffs.json"
        assert "bob@apptco.invalid" in emails, "A2 FAIL: interested+appt lead missing"
        assert "carla@warmco.invalid" not in emails, "A3 FAIL: warm/low-conf should NOT be in handoff"
        assert "dirk@negativeco.invalid" not in emails, "A4 FAIL: negative lead must NOT appear"
        assert hh.get("count") == len([r for r in hh.get("handoffs", []) if r]), "count mismatch"
        assert hh.get("count") >= 2, f"expected count>=2, got {hh.get('count')}"
        print(f"PASS A: positive + interested+appt promoted; warm/low-conf + negative excluded "
              f"(count={hh.get('count')})")

        # ── B) Mirror nach latest/ ──────────────────────────────────────────────
        assert (LATEST_DIR / "hot_handoffs.json").is_file(), "B1 FAIL: latest hot_handoffs.json missing"
        assert (LATEST_DIR / "hot_handoffs.csv").is_file(), "B2 FAIL: latest hot_handoffs.csv missing"
        hh_latest = json.loads((LATEST_DIR / "hot_handoffs.json").read_text(encoding="utf-8"))
        # generated_at darf gleich sein (Copy), Inhalt der handoffs-Liste muss identisch sein
        main_set = sorted(r.get("email", "") for r in hh.get("handoffs", []))
        lat_set = sorted(r.get("email", "") for r in hh_latest.get("handoffs", []))
        assert main_set == lat_set, f"B3 FAIL: latest mirror differs: {main_set} != {lat_set}"
        print(f"PASS B: output/latest/hot_handoffs.{{json,csv}} mirror identical "
              f"({len(lat_set)} emails)")

        # ── C) Idempotenz ───────────────────────────────────────────────────────
        export_hot_handoffs_files(state)
        _mirror_to_latest()
        hh2 = _read_handoffs_json()
        emails2 = sorted(r.get("email", "") for r in hh2.get("handoffs", []))
        assert sorted(emails) == sorted(emails2), (
            f"C FAIL: second export produced different emails-set"
        )
        assert hh2.get("count") == hh.get("count"), "C FAIL: count differs on second export"
        print(f"PASS C: idempotenter zweiter Export -> identische handoffs-Liste (count={hh2.get('count')})")

        # ── D) Pflichtfelder pro Row ────────────────────────────────────────────
        for row in hh.get("handoffs", []):
            assert (row.get("company_name") or row.get("contact_name")), \
                f"D FAIL: row ohne Identitaet: {row}"
            assert row.get("email"), f"D FAIL: row ohne email: {row}"
            assert "reply_status" in row, f"D FAIL: reply_status fehlt"
            assert "outreach_stage" in row, f"D FAIL: outreach_stage fehlt"
            assert (row.get("recommended_next_action") or row.get("handoff_next_action")
                    or row.get("termin_next_step")), f"D FAIL: kein next_action: {row}"
            assert (row.get("handoff_summary") or row.get("why_hot")), \
                f"D FAIL: weder handoff_summary noch why_hot: {row}"
        print(f"PASS D: alle {hh.get('count')} Rows haben company/email/status/next-action/handoff-context")

        # ── E) CSV ist parsebar und enthält dieselben Emails ────────────────────
        csv_rows = _read_handoffs_csv()
        csv_emails = sorted(r.get("email", "") for r in csv_rows)
        assert csv_emails == sorted(emails), f"E FAIL: CSV emails differ: {csv_emails} vs {sorted(emails)}"
        print(f"PASS E: hot_handoffs.csv parsebar, {len(csv_rows)} Rows, gleiche Emails")

        print("ALL_TESTS_PASSED")
        return 0
    finally:
        _restore(snap)


if __name__ == "__main__":
    sys.exit(main())
