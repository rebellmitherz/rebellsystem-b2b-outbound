"""Offline-Smoke fuer operator_reply_suggestion in export_hot_handoffs_files.

Pruefziel: Jede Hot-Handoff-Row erhaelt ein deterministisches deutsches
operator_reply_suggestion-Feld (Termin-CTA fuer hot Faelle, manueller
Pruefhinweis fuer sent_log_only). Negative/later/unclear ohne Appointment
erscheinen nicht im Handoff (Filter-Regression).

Kein IMAP. Kein SMTP. Kein mine.py. Keine Outreach-Befehle. Keine echten
Mails. Backup/Restore der echten output/hot_handoffs.* und
output/reply_queue.json im finally-Block. Auto-Send-/Auto-Reply-Faehigkeit
wird NICHT veraendert — Test verifiziert das per Code-Inspection.

Aufruf:
    python smoke_hot_handoff_reply_suggestion.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Sicherheits-Defaults — niemals Auto-Send triggern
os.environ.setdefault("REPLY_AUTO_SEND", "false")
os.environ.setdefault("REPLY_DRY_RUN", "true")
os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)

from modules.outreach_pipeline import (  # noqa: E402
    export_hot_handoffs_files,
    _build_operator_reply_suggestion,
    _reply_auto_send_confirmed,
)
from config import OUTPUT_DIR  # noqa: E402

OUTPUT_DIR_P = Path(OUTPUT_DIR)
HH_JSON = OUTPUT_DIR_P / "hot_handoffs.json"
HH_CSV = OUTPUT_DIR_P / "hot_handoffs.csv"
RQ_JSON = OUTPUT_DIR_P / "reply_queue.json"

OUTREACH_PIPELINE_PY = ROOT / "modules" / "outreach_pipeline.py"


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


def _write_queue(items: list[dict[str, Any]]) -> None:
    RQ_JSON.parent.mkdir(parents=True, exist_ok=True)
    RQ_JSON.write_text(
        json.dumps({"version": 1, "updated_at": "2026-05-20T10:00:00", "items": items},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_hh() -> dict[str, Any]:
    return json.loads(HH_JSON.read_text(encoding="utf-8"))


def _read_hh_csv() -> list[dict[str, str]]:
    raw = HH_CSV.read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw)))


def _entry(email: str, **over: Any) -> dict[str, Any]:
    e = {
        "entry_key": f"k_{email}",
        "company_name": f"Co_{email}",
        "contact_name": f"Name_{email}",
        "email": email,
        "outreach_stage": "hot",
        "reply_status": "positive",
        "inbound_class": "positive",
        "inbound_confidence": 0.9,
        "appointment_ready": False,
        "first_email_subject": "Kurze Frage",
        "first_email_body": "Hallo",
    }
    e.update(over)
    return e


def _q_item(reason: str, email: str, cls: str, conf: float,
            appt: bool = False) -> dict[str, Any]:
    return {
        "message_id": f"<{email}-{cls}>",
        "from_email": email,
        "inbound_subject": "Re: Kurze Frage",
        "inbound_snippet": f"Snippet fuer {email}",
        "inbound_class": cls,
        "confidence": conf,
        "appointment_ready": appt,
        "reason": reason,
        "needs_approval": True,
    }


REASON_PRIMARY = "sent_log_match_without_pipeline_entry"


def main() -> int:
    snap = _snapshot([HH_JSON, HH_CSV, RQ_JSON])
    try:
        # ── A) Pipeline appointment_ready -> Termin-CTA ─────────────────────
        _write_queue([])
        state_a = {"entries": [_entry(
            "alice@a.invalid", appointment_ready=True,
            inbound_class="interested", inbound_confidence=0.55,
            reply_status="interested",
        )]}
        export_hot_handoffs_files(state_a)
        hh = _read_hh()
        assert hh["count"] == 1, f"A count: {hh['count']}"
        row = hh["handoffs"][0]
        ors = row["operator_reply_suggestion"]
        assert "10 Minuten" in ors, f"A no termin-CTA: {ors!r}"
        assert ors.startswith("Passt, danke"), f"A wrong template start: {ors[:40]!r}"
        assert "Manuell prüfen" not in ors, "A: sent_log prefix must NOT appear on pipeline-entry"
        print("PASS A: pipeline appointment_ready -> Termin-CTA (10 Minuten)")

        # ── B) Pipeline positive ohne appointment_ready -> Abgleich-CTA ─────
        _write_queue([])
        state_b = {"entries": [_entry("bob@b.invalid", appointment_ready=False)]}
        export_hot_handoffs_files(state_b)
        row = _read_hh()["handoffs"][0]
        ors = row["operator_reply_suggestion"]
        assert "10 Minuten" in ors, f"B no 10-Minuten-CTA: {ors!r}"
        assert "Abgleich" in ors, f"B no Abgleich-CTA: {ors!r}"
        assert "Danke für die Rückmeldung" in ors, f"B wrong template: {ors[:50]!r}"
        print("PASS B: pipeline positive -> Abgleich/10-Minuten-CTA")

        # ── C) sent_log_only positive -> Prüfhinweis + Termin-CTA ───────────
        _write_queue([_q_item(REASON_PRIMARY, "carol@c.invalid", "positive", 0.80)])
        export_hot_handoffs_files({"entries": []})
        row = _read_hh()["handoffs"][0]
        ors = row["operator_reply_suggestion"]
        assert row["source"] == "sent_log_only", "C source"
        assert "Manuell prüfen" in ors, f"C no manual prefix: {ors!r}"
        assert "verifizieren" in ors, f"C no verification hint: {ors!r}"
        assert "10 Minuten" in ors, f"C no termin-CTA: {ors!r}"
        # Keine Behauptung "Kontakt ist korrekt"
        assert "Kontakt ist korrekt" not in ors, "C false claim"
        assert "sicher korrekt" not in ors, "C false claim"
        print("PASS C: sent_log_only positive -> Pruefhinweis + Termin-CTA")

        # ── D) sent_log_only appointment_ready -> Prüfhinweis + 10 Minuten ──
        _write_queue([_q_item(REASON_PRIMARY, "dave@d.invalid", "interested", 0.45, appt=True)])
        export_hot_handoffs_files({"entries": []})
        row = _read_hh()["handoffs"][0]
        ors = row["operator_reply_suggestion"]
        assert "Manuell prüfen" in ors, "D no manual prefix"
        assert "10 Minuten" in ors, f"D no 10-Minuten-CTA: {ors!r}"
        assert ors.find("Manuell") < ors.find("10 Minuten"), "D ordering"
        print("PASS D: sent_log_only appointment_ready -> Pruefhinweis + 10 Minuten")

        # ── E) Negative: keine Hot-Handoff-Row (Filter-Regression) ──────────
        _write_queue([_q_item(REASON_PRIMARY, "frank@f.invalid", "negative", 0.95)])
        state_e = {"entries": [_entry(
            "eve@e.invalid", outreach_stage="lost",
            reply_status="negative", inbound_class="negative",
        )]}
        export_hot_handoffs_files(state_e)
        hh = _read_hh()
        emails = {r["email"] for r in hh["handoffs"]}
        assert "eve@e.invalid" not in emails, "E negative pipeline must not appear"
        assert "frank@f.invalid" not in emails, "E negative sent_log_only must not appear"
        print("PASS E: negative -> kein Hot-Handoff-Eintrag, kein Vorschlag")

        # ── F) later/unclear ohne appointment_ready: nicht hot ──────────────
        _write_queue([
            _q_item(REASON_PRIMARY, "g@g.invalid", "later", 0.7),
            _q_item(REASON_PRIMARY, "h@h.invalid", "unclear", 0.4),
        ])
        state_f = {"entries": [
            _entry("i@i.invalid", outreach_stage="sent",
                   reply_status="later", inbound_class="later",
                   inbound_confidence=0.7, appointment_ready=False),
        ]}
        export_hot_handoffs_files(state_f)
        emails = {r["email"] for r in _read_hh()["handoffs"]}
        assert "g@g.invalid" not in emails, "F later sent_log_only must not appear"
        assert "h@h.invalid" not in emails, "F unclear sent_log_only must not appear"
        assert "i@i.invalid" not in emails, "F later pipeline entry must not appear"
        print("PASS F: later/unclear ohne appt -> nicht im Handoff")

        # ── G) CSV enthaelt Spalte operator_reply_suggestion ────────────────
        _write_queue([_q_item(REASON_PRIMARY, "carol@c.invalid", "positive", 0.80)])
        export_hot_handoffs_files({"entries": [_entry("alice@a.invalid")]})
        csv_rows = _read_hh_csv()
        assert len(csv_rows) == 2, f"G csv rows: {len(csv_rows)}"
        for r in csv_rows:
            assert "operator_reply_suggestion" in r, f"G csv missing col in {list(r.keys())}"
            assert r["operator_reply_suggestion"], f"G empty suggestion for {r['email']}"
        print(f"PASS G: CSV enthaelt operator_reply_suggestion ({len(csv_rows)} Rows)")

        # ── H) Idempotenz ───────────────────────────────────────────────────
        hh1 = _read_hh()
        snapshot1 = sorted((r["email"], r["operator_reply_suggestion"]) for r in hh1["handoffs"])
        export_hot_handoffs_files({"entries": [_entry("alice@a.invalid")]})
        hh2 = _read_hh()
        snapshot2 = sorted((r["email"], r["operator_reply_suggestion"]) for r in hh2["handoffs"])
        assert snapshot1 == snapshot2, "H idempotency mismatch"
        assert hh2["count"] == hh1["count"], "H count drift"
        print("PASS H: zweiter Export -> identisch")

        # ── I) Auto-Send-Faehigkeit unangetastet (Code-Inspection) ──────────
        # I1) Helper _reply_auto_send_confirmed bleibt funktional
        assert callable(_reply_auto_send_confirmed), "I1 helper missing"
        os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)
        assert _reply_auto_send_confirmed() is False, "I1 default must be False"
        os.environ["REPLY_AUTO_SEND_CONFIRMED"] = "true"
        assert _reply_auto_send_confirmed() is True, "I1 truthy must work"
        os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)
        # I2) Sourcecode unveraendert um Send-Gates: alle Schluesselstellen praesent
        src = OUTREACH_PIPELINE_PY.read_text(encoding="utf-8")
        for marker in (
            "REPLY_AUTO_SEND_CONFIRMED",
            "OUTREACH_FULL_AUTO_CONFIRMED",
            "smtp_ok = bool(allow_auto and reply_may_auto_send and not dry and auto_send_confirmed)",
            "auto_send_clear_replies",
            "REPLY_DRY_RUN",
        ):
            assert marker in src, f"I2 missing send-gate marker: {marker!r}"
        # I3) Keine neuen smtplib/imaplib-Importe hinzugefuegt
        assert "import smtplib" not in src, "I3 unexpected smtplib import"
        assert "imaplib.IMAP4_SSL" not in src, "I3 unexpected imaplib usage"
        print("PASS I: Auto-Send-Gates + Helper unveraendert (Code-Inspection)")

        # ── J) Direkter Helper-Test fuer Determinismus ──────────────────────
        s_appt = _build_operator_reply_suggestion(
            source="pipeline_entry", inbound_class="interested",
            reply_status="interested", appointment_ready=True,
        )
        s_pos = _build_operator_reply_suggestion(
            source="pipeline_entry", inbound_class="positive",
            reply_status="positive", appointment_ready=False,
        )
        s_neutral = _build_operator_reply_suggestion(
            source="pipeline_entry", inbound_class="", reply_status="",
            appointment_ready=False,
        )
        s_slo = _build_operator_reply_suggestion(
            source="sent_log_only", inbound_class="positive",
            reply_status="", appointment_ready=False,
        )
        # Determinismus: zweimal gleich
        assert s_appt == _build_operator_reply_suggestion(
            source="pipeline_entry", inbound_class="interested",
            reply_status="interested", appointment_ready=True,
        ), "J determinism"
        assert "10 Minuten" in s_appt and "Manuell" not in s_appt
        assert "10 Minuten" in s_pos and "Manuell" not in s_pos
        assert "vor Antwort" in s_neutral and "Manuell prüfen" not in s_neutral
        assert s_slo.startswith("Manuell prüfen")
        print("PASS J: Helper deterministisch, deutsche Templates, keine falschen Behauptungen")

        print("ALL_TESTS_PASSED")
        return 0
    finally:
        _restore(snap)
        os.environ.pop("REPLY_AUTO_SEND_CONFIRMED", None)


if __name__ == "__main__":
    sys.exit(main())
