"""Offline-Smoke fuer sent_log-only Erweiterung in export_hot_handoffs_files.

Pruefziel: reply_queue.json-Items mit reason="sent_log_match_without_pipeline_entry"
oder "sent_log_auto_reply_without_pipeline_entry" landen als zusaetzliche Rows
mit source="sent_log_only" im Hot-Handoff-Export, sofern sie qualifizieren
(positive / interested>=0.52 / appointment_ready). Negative, unclear, leere
Email und Pipeline-Duplikate werden NICHT exportiert.

Kein IMAP. Kein SMTP. Kein mine.py. Keine Outreach-Befehle. Keine echten Mails.
Backup/Restore der echten output-Dateien im finally-Block.

Aufruf:
    python smoke_hot_handoff_sent_log_only.py
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

# Sicherheits-Defaults — Auto-Send/Live darf nichts ausloesen
os.environ.setdefault("REPLY_AUTO_SEND", "false")
os.environ.setdefault("REPLY_DRY_RUN", "true")

from modules.outreach_pipeline import export_hot_handoffs_files  # noqa: E402
from config import OUTPUT_DIR  # noqa: E402

OUTPUT_DIR_P = Path(OUTPUT_DIR)
HH_JSON = OUTPUT_DIR_P / "hot_handoffs.json"
HH_CSV = OUTPUT_DIR_P / "hot_handoffs.csv"
RQ_JSON = OUTPUT_DIR_P / "reply_queue.json"


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


def _delete_queue() -> None:
    if RQ_JSON.is_file():
        RQ_JSON.unlink()


def _read_hh() -> dict[str, Any]:
    return json.loads(HH_JSON.read_text(encoding="utf-8"))


def _read_hh_csv() -> list[dict[str, str]]:
    raw = HH_CSV.read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw)))


def _pipeline_entry(email: str, **over: Any) -> dict[str, Any]:
    e = {
        "entry_key": f"k_{email}",
        "company_name": f"Co_{email}",
        "contact_name": f"Name_{email}",
        "email": email,
        "phone": "",
        "website": "",
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
            appt: bool = False, msg_id: str | None = None) -> dict[str, Any]:
    return {
        "message_id": msg_id or f"<{email}-{cls}-{int(conf*100)}>",
        "entry_key": "",
        "from_email": email,
        "inbound_subject": "Re: Kurze Frage",
        "inbound_snippet": f"Snippet fuer {email} ({cls})",
        "inbound_class": cls,
        "confidence": conf,
        "appointment_ready": appt,
        "reason": reason,
        "needs_approval": True,
    }


REASON_PRIMARY = "sent_log_match_without_pipeline_entry"
REASON_AUTO = "sent_log_auto_reply_without_pipeline_entry"


def main() -> int:
    snap = _snapshot([HH_JSON, HH_CSV, RQ_JSON])
    try:
        # ── A) Pipeline-Match weiterhin funktional + neue Felder vorhanden ────
        state_a = {"entries": [_pipeline_entry("alice@a.invalid")]}
        _write_queue([])
        export_hot_handoffs_files(state_a)
        hh = _read_hh()
        assert hh["count"] == 1, f"A count: {hh['count']}"
        row = hh["handoffs"][0]
        assert row["email"] == "alice@a.invalid", "A email"
        assert row["source"] == "pipeline_entry", f"A source: {row.get('source')!r}"
        assert row["entry_key"] == "k_alice@a.invalid", "A entry_key"
        print("PASS A: Pipeline-Match -> source=pipeline_entry, struktur intakt")

        # ── B) sent_log-only positive (conf 0.80) -> exportiert ──────────────
        _write_queue([_q_item(REASON_PRIMARY, "bob@b.invalid", "positive", 0.80)])
        export_hot_handoffs_files({"entries": []})
        hh = _read_hh()
        assert hh["count"] == 1, f"B count: {hh['count']}"
        row = hh["handoffs"][0]
        assert row["source"] == "sent_log_only", f"B source: {row.get('source')!r}"
        assert row["email"] == "bob@b.invalid", "B email"
        assert row["entry_key"] == "", "B entry_key must be empty"
        assert row["inbound_class"] == "positive", "B inbound_class"
        assert "manuell" in (row.get("handoff_summary") or "").lower(), "B handoff_summary"
        assert "manuell" in (row.get("recommended_next_action") or "").lower(), "B next_action"
        assert row.get("reason") == REASON_PRIMARY, "B reason"
        print("PASS B: sent_log-only positive (conf 0.80) -> exportiert")

        # ── C) sent_log-only interested + appointment_ready (conf 0.45) ──────
        _write_queue([_q_item(REASON_PRIMARY, "carol@c.invalid", "interested", 0.45, appt=True)])
        export_hot_handoffs_files({"entries": []})
        hh = _read_hh()
        assert hh["count"] == 1, f"C count: {hh['count']}"
        row = hh["handoffs"][0]
        assert row["source"] == "sent_log_only", "C source"
        assert row["email"] == "carol@c.invalid", "C email"
        assert row["appointment_ready"] is True, "C appointment_ready"
        print("PASS C: sent_log-only interested + appointment_ready -> exportiert")

        # ── D) sent_log-only interested conf 0.60, kein appt -> exportiert ───
        _write_queue([_q_item(REASON_PRIMARY, "dave@d.invalid", "interested", 0.60)])
        export_hot_handoffs_files({"entries": []})
        hh = _read_hh()
        assert hh["count"] == 1, f"D count: {hh['count']}"
        assert hh["handoffs"][0]["source"] == "sent_log_only", "D source"
        assert hh["handoffs"][0]["email"] == "dave@d.invalid", "D email"
        print("PASS D: sent_log-only interested conf 0.60 -> exportiert")

        # ── E) sent_log-only interested conf 0.40 ohne appt -> NICHT ─────────
        _write_queue([_q_item(REASON_PRIMARY, "ed@e.invalid", "interested", 0.40)])
        export_hot_handoffs_files({"entries": []})
        hh = _read_hh()
        assert hh["count"] == 0, f"E count must be 0: {hh['count']}"
        print("PASS E: interested conf 0.40 ohne appt -> NICHT exportiert")

        # ── F) sent_log-only negative -> NICHT ───────────────────────────────
        _write_queue([_q_item(REASON_PRIMARY, "frank@f.invalid", "negative", 0.90)])
        export_hot_handoffs_files({"entries": []})
        assert _read_hh()["count"] == 0, "F: negative must NOT appear"
        print("PASS F: negative -> NICHT exportiert")

        # ── G) sent_log-only unclear ohne appt -> NICHT ──────────────────────
        _write_queue([_q_item(REASON_PRIMARY, "gina@g.invalid", "unclear", 0.30)])
        export_hot_handoffs_files({"entries": []})
        assert _read_hh()["count"] == 0, "G: unclear must NOT appear"
        print("PASS G: unclear ohne appt -> NICHT exportiert")

        # ── H) Dedup: Pipeline-Entry + Queue gleiche Email -> nur Entry-Row ──
        _write_queue([
            _q_item(REASON_PRIMARY, "alice@a.invalid", "positive", 0.85),
            _q_item(REASON_AUTO, "bob@b.invalid", "positive", 0.80),
        ])
        export_hot_handoffs_files({"entries": [_pipeline_entry("alice@a.invalid")]})
        hh = _read_hh()
        sources = sorted((r["email"], r["source"]) for r in hh["handoffs"])
        assert hh["count"] == 2, f"H count: {hh['count']}"
        assert ("alice@a.invalid", "pipeline_entry") in sources, "H alice must be pipeline_entry"
        assert ("alice@a.invalid", "sent_log_only") not in sources, "H alice must NOT be duplicated"
        assert ("bob@b.invalid", "sent_log_only") in sources, "H bob must be sent_log_only"
        print("PASS H: Email-Dedup (Entry hat Vorrang), auto_reply_reason erkannt")

        # ── I) Idempotenz ────────────────────────────────────────────────────
        export_hot_handoffs_files({"entries": [_pipeline_entry("alice@a.invalid")]})
        hh2 = _read_hh()
        sources2 = sorted((r["email"], r["source"]) for r in hh2["handoffs"])
        assert sources == sources2, f"I idempotency mismatch: {sources} vs {sources2}"
        assert hh2["count"] == 2, f"I count: {hh2['count']}"
        print("PASS I: zweiter Export -> identische Row-Set, kein Duplikat")

        # ── J) reply_queue.json fehlt -> kein Crash, nur Entry-Rows ──────────
        _delete_queue()
        export_hot_handoffs_files({"entries": [_pipeline_entry("alice@a.invalid")]})
        hh = _read_hh()
        assert hh["count"] == 1, f"J count: {hh['count']}"
        assert hh["handoffs"][0]["source"] == "pipeline_entry", "J source"
        print("PASS J: fehlende reply_queue.json -> kein Crash, Entry-Rows intakt")

        # ── K) Items ohne Email werden ignoriert ─────────────────────────────
        bad = _q_item(REASON_PRIMARY, "", "positive", 0.9)
        bad["from_email"] = ""
        _write_queue([bad])
        export_hot_handoffs_files({"entries": []})
        assert _read_hh()["count"] == 0, "K: empty email must be ignored"
        print("PASS K: leere Email -> ignoriert")

        # ── L) Andere reason-Werte werden ignoriert ──────────────────────────
        _write_queue([_q_item("human_review", "ivy@i.invalid", "positive", 0.9)])
        export_hot_handoffs_files({"entries": []})
        assert _read_hh()["count"] == 0, "L: foreign reason must be ignored"
        print("PASS L: fremde reason -> ignoriert")

        # ── M) CSV parsebar, gleiche Emails wie JSON ─────────────────────────
        _write_queue([_q_item(REASON_PRIMARY, "bob@b.invalid", "positive", 0.80)])
        export_hot_handoffs_files({"entries": [_pipeline_entry("alice@a.invalid")]})
        hh = _read_hh()
        csv_rows = _read_hh_csv()
        json_emails = sorted(r["email"] for r in hh["handoffs"])
        csv_emails = sorted(r["email"] for r in csv_rows)
        assert json_emails == csv_emails, f"M emails mismatch: {json_emails} vs {csv_emails}"
        sources_csv = {r["source"] for r in csv_rows}
        assert sources_csv == {"pipeline_entry", "sent_log_only"}, f"M csv sources: {sources_csv}"
        print(f"PASS M: CSV parsebar, {len(csv_rows)} Rows, source-Spalte enthalten")

        print("ALL_TESTS_PASSED")
        return 0
    finally:
        _restore(snap)


if __name__ == "__main__":
    sys.exit(main())
