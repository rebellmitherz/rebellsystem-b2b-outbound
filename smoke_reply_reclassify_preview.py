"""Smoke-Test: modules/reply_reclassify_preview.py

Prueft:
  01. Import ohne SMTP/IMAP/Send
  02. Fehlende reply_queue.json crasht nicht
  03. Pflichtfelder vorhanden (generated_at, total_replies, changed_count, ...)
  04. "aktuell keinen Bedarf" (bisher positive) -> als Aenderung erkannt
  05. alt positive/appointment_ready=True -> neu negative/False
  06. Echte Terminanfrage bleibt appointment_ready=True (keine Aenderung)
  07. Auto-Reply bleibt neutral / appointment_ready=False (keine Aenderung)
  08. changed_count + unchanged_count == total_replies
  09. Ausgabedatei in Temp-Verzeichnis geschrieben
  10. python mine.py --help enthaelt --reply-reclassify-preview
  11. Kein SMTP/IMAP/Send/Approve/CRM-Push in reply_reclassify_preview.py
  12. reply_queue.json wird NICHT veraendert (read-only)

Kein Netzwerk. Kein SMTP. Kein IMAP. Kein Send. Kein CRM-Push.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

REQUIRED_FIELDS = [
    "generated_at", "mode",
    "total_replies", "changed_count", "unchanged_count",
    "changes", "warnings",
]


def _ok(label: str) -> None:
    print(f"  [OK] {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))
    sys.exit(1)


def _wj(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _make_queue(items: list[dict]) -> dict:
    return {"items": items, "total": len(items), "updated_at": "2026-01-01T00:00:00Z"}


def _item_rejection(email: str) -> dict:
    """Reply die bisher als positive/appointment_ready gespeichert wurde,
    aber Ablehnungsphrasen enthaelt — klassisch: artundweise.de-Fall."""
    return {
        "from_email_actual": email,
        "inbound_subject": "Re: Anfrage",
        "inbound_snippet": (
            "Fuer die Terminfindung setzen wir inhouse auf unser eigenes Team. "
            "Daher haben wir aktuell keinen Bedarf."
        ),
        "inbound_class": "positive",      # altes, fehlerhaftes Ergebnis
        "confidence": 1.0,
        "is_auto_reply": False,
        "auto_reply_reason": "",
        "appointment_ready": True,         # altes, fehlerhaftes Ergebnis
    }


def _item_genuine(email: str) -> dict:
    """Echte Terminanfrage ohne Ablehnungsphrase."""
    return {
        "from_email_actual": email,
        "inbound_subject": "Re: Anfrage",
        "inbound_snippet": "Ja, gerne. Koennen wir naechste Woche einen Zoom-Termin vereinbaren?",
        "inbound_class": "positive",
        "confidence": 0.88,
        "is_auto_reply": False,
        "auto_reply_reason": "",
        "appointment_ready": True,
    }


def _item_auto(email: str) -> dict:
    """Auto-Reply / OOO-Antwort."""
    return {
        "from_email_actual": email,
        "inbound_subject": "Automatische Antwort: Test",
        "inbound_snippet": "Ich bin im Urlaub und nicht erreichbar.",
        "inbound_class": "neutral",
        "confidence": 0.82,
        "is_auto_reply": True,
        "auto_reply_reason": "auto_submitted:auto-generated",
        "appointment_ready": False,
    }


def _run(tmp: Path, queue_items: list[dict] | None = None) -> dict:
    import modules.reply_reclassify_preview as rp

    if queue_items is not None:
        _wj(tmp / "reply_queue.json", _make_queue(queue_items))

    out = tmp / "reply_reclassify_preview.json"
    return rp.build_reclassify_preview(
        reply_queue_file=tmp / "reply_queue.json",
        preview_file=out,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_01_import_clean() -> None:
    import modules.reply_reclassify_preview  # noqa: F401
    _ok("Import: modules.reply_reclassify_preview ohne Seiteneffekte")


def test_02_missing_queue_no_crash() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t))   # keine reply_queue.json
    if report.get("mode") != "reply_reclassify_preview":
        _fail("mode falsch", str(report.get("mode")))
    if report.get("total_replies") != 0:
        _fail("total_replies sollte 0 sein bei fehlender Datei")
    _ok("Fehlende reply_queue.json: kein Crash, total_replies=0")


def test_03_required_fields() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t))
    missing = [f for f in REQUIRED_FIELDS if f not in report]
    if missing:
        _fail("Pflichtfelder fehlen", str(missing))
    _ok(f"Alle {len(REQUIRED_FIELDS)} Pflichtfelder vorhanden")


def test_04_rejection_detected_as_change() -> None:
    """'aktuell keinen Bedarf' war als positive gespeichert -> muss als Aenderung erkannt werden."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[_item_rejection("reject@test.de")])
    if report["changed_count"] != 1:
        _fail("changed_count != 1", str(report["changed_count"]))
    c = report["changes"][0]
    if c["old_reply_class"] != "positive":
        _fail("old_reply_class sollte 'positive' sein", str(c["old_reply_class"]))
    if c["new_reply_class"] not in ("negative", "neutral"):
        _fail("new_reply_class sollte 'negative' sein", str(c["new_reply_class"]))
    _ok("'aktuell keinen Bedarf' korrekt als Aenderung erkannt")


def test_05_old_positive_becomes_negative_and_no_appt() -> None:
    """Ablehnungsphrase: alt positive/appointment_ready=True -> neu negative/False."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[_item_rejection("reject@test.de")])
    if not report["changes"]:
        _fail("Keine Aenderungen gefunden")
    c = report["changes"][0]
    if c["old_appointment_ready"] is not True:
        _fail("old_appointment_ready sollte True sein", str(c["old_appointment_ready"]))
    if c["new_appointment_ready"] is not False:
        _fail("new_appointment_ready sollte False sein", str(c["new_appointment_ready"]))
    if c["changed"] is not True:
        _fail("changed-Flag sollte True sein")
    _ok("Ablehnungsphrase: old=positive/apt=True -> new=negative/apt=False")


def test_06_genuine_appointment_unchanged() -> None:
    """Echte Terminanfrage ohne Ablehnungsphrase -> keine Aenderung."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[_item_genuine("genuine@test.de")])
    if report["changed_count"] != 0:
        _fail(
            "Echte Terminanfrage sollte keine Aenderung ergeben",
            str(report["changes"]),
        )
    u = report["unchanged"][0]
    if u["new_appointment_ready"] is not True:
        _fail("Echte Terminanfrage: new_appointment_ready sollte True sein")
    _ok("Echte Terminanfrage: bleibt appointment_ready=True (keine Aenderung)")


def test_07_auto_reply_unchanged() -> None:
    """Auto-Reply war neutral/apt=False -> bleibt neutral/False (keine Aenderung)."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[_item_auto("auto@test.de")])
    if report["changed_count"] != 0:
        _fail("Auto-Reply sollte keine Aenderung ergeben", str(report["changes"]))
    _ok("Auto-Reply: bleibt neutral/apt=False (keine Aenderung)")


def test_08_counts_consistent() -> None:
    """changed_count + unchanged_count == total_replies."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[
            _item_rejection("r@test.de"),
            _item_genuine("g@test.de"),
            _item_auto("a@test.de"),
        ])
    total = report["total_replies"]
    if total != 3:
        _fail("total_replies != 3", str(total))
    if report["changed_count"] + report["unchanged_count"] != total:
        _fail(
            "changed_count + unchanged_count != total_replies",
            f"{report['changed_count']} + {report['unchanged_count']} != {total}",
        )
    _ok("changed_count + unchanged_count == total_replies")


def test_09_output_file_written() -> None:
    """Preview-Datei wird in Temp-Verzeichnis geschrieben."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _run(tmp, queue_items=[_item_rejection("x@test.de")])
        out = tmp / "reply_reclassify_preview.json"
        if not out.is_file():
            _fail("reply_reclassify_preview.json nicht geschrieben")
        data = json.loads(out.read_text(encoding="utf-8"))
        if data.get("mode") != "reply_reclassify_preview":
            _fail("mode in Ausgabedatei falsch", str(data.get("mode")))
    _ok("reply_reclassify_preview.json korrekt in Temp-Verzeichnis geschrieben")


def test_10_mine_help_contains_flag() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "mine.py"), "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    if "--reply-reclassify-preview" not in result.stdout:
        _fail("--reply-reclassify-preview fehlt in mine.py --help", result.stdout[:300])
    _ok("mine.py --help: --reply-reclassify-preview vorhanden")


def test_11_no_smtp_imap_in_module() -> None:
    """Keine SMTP/IMAP/Send/Approve/CRM-Push-Funktionen in reply_reclassify_preview.py."""
    import modules.reply_reclassify_preview as rp
    src = Path(rp.__file__).read_text(encoding="utf-8")
    forbidden = [
        "send_email(",
        "smtplib.SMTP(",
        "imaplib.IMAP",
        "approve(",
        "crm_push_confirmed",
        "CRM_PUSH_CONFIRMED",
    ]
    hits = [w for w in forbidden if w in src]
    if hits:
        _fail("Verbotene Terme in reply_reclassify_preview.py", str(hits))
    _ok("reply_reclassify_preview.py: kein SMTP/IMAP/Send/Approve/Push-Code")


def test_12_reply_queue_not_modified() -> None:
    """Die originale reply_queue.json wird nicht veraendert (read-only)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        queue_path = tmp / "reply_queue.json"
        original_content = json.dumps(_make_queue([_item_rejection("x@test.de")]))
        _wj(queue_path, _make_queue([_item_rejection("x@test.de")]))

        import modules.reply_reclassify_preview as rp
        rp.build_reclassify_preview(
            reply_queue_file=queue_path,
            preview_file=tmp / "reply_reclassify_preview.json",
        )

        after_content = queue_path.read_text(encoding="utf-8")
        if after_content != original_content:
            _fail("reply_queue.json wurde veraendert!")
    _ok("reply_queue.json wurde nicht veraendert (read-only bestaetigt)")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_01_import_clean,
    test_02_missing_queue_no_crash,
    test_03_required_fields,
    test_04_rejection_detected_as_change,
    test_05_old_positive_becomes_negative_and_no_appt,
    test_06_genuine_appointment_unchanged,
    test_07_auto_reply_unchanged,
    test_08_counts_consistent,
    test_09_output_file_written,
    test_10_mine_help_contains_flag,
    test_11_no_smtp_imap_in_module,
    test_12_reply_queue_not_modified,
]


def main() -> None:
    print("=" * 65)
    print("  smoke_reply_reclassify_preview.py")
    print("  Kein Netzwerk. Kein SMTP. Kein IMAP. Kein Send. Kein Push.")
    print("=" * 65)
    passed = 0
    for test in TESTS:
        try:
            test()
            passed += 1
        except SystemExit:
            raise
        except Exception as exc:
            _fail(test.__name__, str(exc))
    print("=" * 65)
    print(f"  Ergebnis: {passed}/{len(TESTS)} Tests bestanden")
    print("=" * 65)


if __name__ == "__main__":
    main()
