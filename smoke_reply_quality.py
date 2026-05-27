"""Smoke-Test: modules/reply_quality.py

Prueft:
  01. Import ohne SMTP/IMAP/Send
  02. Fehlende Quelldateien crashen nicht
  03. reply_quality_report.json wird erzeugt
  04. Alle Pflichtfelder vorhanden
  05. auto_reply korrekt gezaehlt
  06. Duplikate (gleicher Snippet-Hash) erkannt
  07. Fehlklassifikation erkannt: positive class + Ablehnungsphrase
  08. no_pipeline_match korrekt
  09. root_causes nicht leer
  10. recommendations nicht leer
  11. per-reply block_reasons gesetzt
  12. sent_log leer -> Warnung
  13. pipeline reply_statuses aggregiert
  14. python mine.py --help enthaelt --reply-quality
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
    "total_replies", "auto_replies", "genuine_replies", "duplicates",
    "positive_classified", "appointment_ready_classified",
    "misclassified_replies", "in_hot_handoffs", "crm_blocked_replies",
    "no_pipeline_match", "rejection_phrases_detected",
    "pipeline_total", "pipeline_reply_statuses", "sent_log_entries",
    "reply_analyses", "root_causes", "recommendations", "warnings",
]


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def _ok(label: str) -> None:
    print(f"  [OK] {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))
    sys.exit(1)


def _wj(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _make_reply_queue(items: list[dict]) -> dict:
    return {"items": items, "total": len(items), "updated_at": "2026-01-01T00:00:00Z"}


def _auto_reply_item(email: str) -> dict:
    return {
        "from_email_actual": email,
        "inbound_subject": "Automatische Antwort: Test",
        "inbound_snippet": "Ich bin im Urlaub und nicht erreichbar.",
        "inbound_class": "neutral",
        "sentiment": "neutral",
        "confidence": 0.82,
        "is_auto_reply": True,
        "auto_reply_reason": "auto_submitted:auto-generated",
        "appointment_ready": False,
        "needs_approval": True,
        "route": "template",
        "action": "review",
    }


def _genuine_positive_item(email: str, snippet: str = "Ja, gerne Termin vereinbaren.") -> dict:
    return {
        "from_email_actual": email,
        "inbound_subject": "Re: Anfrage",
        "inbound_snippet": snippet,
        "inbound_class": "positive",
        "sentiment": "positive",
        "confidence": 0.95,
        "is_auto_reply": False,
        "auto_reply_reason": "",
        "appointment_ready": True,
        "needs_approval": True,
        "route": "human",
        "action": "review",
    }


def _rejection_item(email: str) -> dict:
    """Antwort die als 'positive' klassifiziert wurde aber Ablehnungsphrase enthaelt."""
    return {
        "from_email_actual": email,
        "inbound_subject": "Re: Anfrage",
        "inbound_snippet": "Danke fuer Ihre Nachricht. Wir haben aktuell keinen Bedarf.",
        "inbound_class": "positive",   # Fehlklassifikation!
        "sentiment": "positive",
        "confidence": 0.90,
        "is_auto_reply": False,
        "auto_reply_reason": "",
        "appointment_ready": True,     # Fehlklassifikation!
        "needs_approval": True,
        "route": "human",
        "action": "review",
    }


def _make_hot_handoffs(emails: list[str]) -> dict:
    return {
        "count": len(emails),
        "handoffs": [{"email": e, "inbound_class": "positive"} for e in emails],
    }


def _make_pipeline(emails: list[str], reply_status: str = "none") -> dict:
    return {
        "entries": [{"email": e, "status": None, "reply_status": reply_status} for e in emails]
    }


def _run(
    tmp: Path,
    queue_items: list[dict] | None = None,
    hh_emails: list[str] | None = None,
    pipeline_emails: list[str] | None = None,
    sent_emails: list[str] | None = None,
    crm_payloads: list[dict] | None = None,
) -> dict:
    import modules.reply_quality as rq

    if queue_items is not None:
        _wj(tmp / "reply_queue.json", _make_reply_queue(queue_items))
    if hh_emails is not None:
        _wj(tmp / "hot_handoffs.json", _make_hot_handoffs(hh_emails))
    if pipeline_emails is not None:
        _wj(tmp / "outreach_pipeline.json", _make_pipeline(pipeline_emails))
    if sent_emails is not None:
        _wj(tmp / "sent_log.json", [{"email": e} for e in sent_emails])
    if crm_payloads is not None:
        _wj(tmp / "crm_payload_preview.json", {"payloads": crm_payloads})

    out = tmp / "reply_quality_report.json"
    return rq.build_reply_quality_report(
        reply_queue_file=tmp / "reply_queue.json",
        hot_handoffs_file=tmp / "hot_handoffs.json",
        sent_log_file=tmp / "sent_log.json",
        pipeline_file=tmp / "outreach_pipeline.json",
        crm_preview_file=tmp / "crm_payload_preview.json",
        report_file=out,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_01_import_clean() -> None:
    import modules.reply_quality  # noqa: F401
    _ok("Import: modules.reply_quality ohne Seiteneffekte")


def test_02_missing_files_no_crash() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t))
    if report.get("mode") != "reply_quality_audit":
        _fail("mode falsch", report.get("mode", "?"))
    _ok("Alle Quelldateien fehlen: kein Crash")


def test_03_report_file_written() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _run(tmp, queue_items=[_auto_reply_item("x@test.de")])
        if not (tmp / "reply_quality_report.json").is_file():
            _fail("reply_quality_report.json nicht geschrieben")
    _ok("reply_quality_report.json wird erzeugt")


def test_04_required_fields() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t))
    missing = [f for f in REQUIRED_FIELDS if f not in report]
    if missing:
        _fail("Pflichtfelder fehlen", str(missing))
    _ok(f"Alle {len(REQUIRED_FIELDS)} Pflichtfelder vorhanden")


def test_05_auto_reply_counted() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[
            _auto_reply_item("auto1@test.de"),
            _auto_reply_item("auto2@test.de"),
            _genuine_positive_item("real@test.de"),
        ])
    if report["auto_replies"] != 2:
        _fail("auto_replies != 2", str(report["auto_replies"]))
    if report["genuine_replies"] != 1:
        _fail("genuine_replies != 1", str(report["genuine_replies"]))
    if report["total_replies"] != 3:
        _fail("total_replies != 3", str(report["total_replies"]))
    _ok("auto_reply korrekt gezaehlt: 2 auto, 1 genuine")


def test_06_duplicate_detection() -> None:
    """Zwei identische Snippets -> 1 Duplikat."""
    same_snip = "Identische Nachricht fuer Duplikat-Test."
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[
            _genuine_positive_item("a@test.de", snippet=same_snip),
            _genuine_positive_item("b@test.de", snippet=same_snip),
        ])
    if report["duplicates"] != 1:
        _fail("duplicates != 1", str(report["duplicates"]))
    # Duplikat-Eintrag hat 'duplicate' in block_reasons
    dup_entries = [a for a in report["reply_analyses"] if "duplicate" in a.get("block_reasons", [])]
    if not dup_entries:
        _fail("Kein Eintrag mit block_reason='duplicate'")
    _ok("Duplikat korrekt erkannt (gleicher Snippet-Hash)")


def test_07_misclassification_detected() -> None:
    """positive class + Ablehnungsphrase = misclassified."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[_rejection_item("reject@test.de")])
    if report["misclassified_replies"] != 1:
        _fail("misclassified_replies != 1", str(report["misclassified_replies"]))
    a = report["reply_analyses"][0]
    if not a["misclassified"]:
        _fail("misclassified=False obwohl Ablehnungsphrase vorhanden")
    if not a["rejection_phrases"]:
        _fail("rejection_phrases leer")
    br = a["block_reasons"]
    if not any("misclassification" in r for r in br):
        _fail("misclassification nicht in block_reasons", str(br))
    _ok("Fehlklassifikation erkannt: positive + Ablehnungsphrase")


def test_08_no_pipeline_match() -> None:
    """Antwort-Email nicht in Pipeline -> no_pipeline_match."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            queue_items=[_genuine_positive_item("outside@test.de")],
            pipeline_emails=["other@test.de"],
            sent_emails=[],
        )
    if report["no_pipeline_match"] != 1:
        _fail("no_pipeline_match != 1", str(report["no_pipeline_match"]))
    a = report["reply_analyses"][0]
    if a["in_pipeline"]:
        _fail("in_pipeline sollte False sein")
    if "no_pipeline_match" not in a["block_reasons"]:
        _fail("no_pipeline_match nicht in block_reasons", str(a["block_reasons"]))
    _ok("no_pipeline_match korrekt erkannt")


def test_09_pipeline_match_clears_block() -> None:
    """Email in Pipeline -> kein no_pipeline_match-Block."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            queue_items=[_genuine_positive_item("matched@test.de")],
            pipeline_emails=["matched@test.de"],
        )
    a = report["reply_analyses"][0]
    if not a["in_pipeline"]:
        _fail("in_pipeline sollte True sein")
    if "no_pipeline_match" in a["block_reasons"]:
        _fail("no_pipeline_match faelschlicherweise in block_reasons")
    _ok("Pipeline-Match: no_pipeline_match-Block entfernt")


def test_10_root_causes_not_empty() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[_auto_reply_item("x@test.de")])
    if not report["root_causes"]:
        _fail("root_causes leer")
    _ok("root_causes nicht leer")


def test_11_recommendations_not_empty() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t), queue_items=[_auto_reply_item("x@test.de")])
    if not report["recommendations"]:
        _fail("recommendations leer")
    _ok("recommendations nicht leer")


def test_12_empty_sent_log_triggers_warning() -> None:
    """sent_log leer -> Warnung im Report."""
    with tempfile.TemporaryDirectory() as t:
        # sent_log.json explizit als leere Liste schreiben
        _wj(Path(t) / "sent_log.json", [])
        report = _run(
            Path(t),
            queue_items=[_genuine_positive_item("real@test.de")],
            pipeline_emails=["other@test.de"],
        )
    warns = " ".join(report["warnings"]).lower()
    if "sent_log" not in warns:
        _fail("Keine Warnung fuer leeren sent_log", str(report["warnings"][:2]))
    _ok("Leerer sent_log: Warnung ausgeloest")


def test_13_pipeline_reply_statuses_aggregated() -> None:
    """pipeline_reply_statuses summiert reply_status-Werte."""
    import modules.reply_quality as rq

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _wj(tmp / "outreach_pipeline.json", {
            "entries": [
                {"email": "a@t.de", "reply_status": "none"},
                {"email": "b@t.de", "reply_status": "none"},
                {"email": "c@t.de", "reply_status": "positive"},
            ]
        })
        out = tmp / "reply_quality_report.json"
        report = rq.build_reply_quality_report(
            reply_queue_file=tmp / "missing.json",
            hot_handoffs_file=tmp / "missing.json",
            sent_log_file=tmp / "missing.json",
            pipeline_file=tmp / "outreach_pipeline.json",
            crm_preview_file=tmp / "missing.json",
            report_file=out,
        )
    statuses = report["pipeline_reply_statuses"]
    if statuses.get("none") != 2:
        _fail("pipeline_reply_statuses['none'] != 2", str(statuses))
    if statuses.get("positive") != 1:
        _fail("pipeline_reply_statuses['positive'] != 1", str(statuses))
    _ok("pipeline_reply_statuses korrekt aggregiert")


def test_14_mine_help_contains_reply_quality() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "mine.py"), "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    if "--reply-quality" not in result.stdout:
        _fail("--reply-quality fehlt in mine.py --help", result.stdout[:300])
    _ok("mine.py --help: --reply-quality vorhanden")


def test_15_hot_handoff_match_tracked() -> None:
    """Email in hot_handoffs -> in_hot_handoffs=True im Analyse-Eintrag."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            queue_items=[_genuine_positive_item("hot@test.de")],
            hh_emails=["hot@test.de"],
        )
    a = report["reply_analyses"][0]
    if not a["in_hot_handoffs"]:
        _fail("in_hot_handoffs sollte True sein fuer email in hot_handoffs")
    if report["in_hot_handoffs"] != 1:
        _fail("in_hot_handoffs count != 1", str(report["in_hot_handoffs"]))
    _ok("Hot-Handoff-Match korrekt getrackt")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_01_import_clean,
    test_02_missing_files_no_crash,
    test_03_report_file_written,
    test_04_required_fields,
    test_05_auto_reply_counted,
    test_06_duplicate_detection,
    test_07_misclassification_detected,
    test_08_no_pipeline_match,
    test_09_pipeline_match_clears_block,
    test_10_root_causes_not_empty,
    test_11_recommendations_not_empty,
    test_12_empty_sent_log_triggers_warning,
    test_13_pipeline_reply_statuses_aggregated,
    test_14_mine_help_contains_reply_quality,
    test_15_hot_handoff_match_tracked,
]


def main() -> None:
    print("=" * 65)
    print("  smoke_reply_quality.py -- Reply Quality Audit Smoke-Test")
    print("  Kein Send. Kein IMAP. Kein CRM-Push. Kein Netzwerk.")
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
