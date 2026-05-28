"""Smoke-Test: modules/reply_reclassify_apply.py

Prueft:
  01. Import ohne SMTP/IMAP/Send
  02. Fehlende Dateien crashen nicht
  03. Pflichtfelder vorhanden
  04. Dry-Run (kein REPLY_RECLASSIFY_CONFIRMED): reply_queue.json unveraendert
  05. Dry-Run: applied_count=0, dry_run=True
  06. Confirmed Apply: Backup-Datei erstellt
  07. Confirmed Apply: reply_queue.json wird aktualisiert
  08. Confirmed Apply: nur erlaubte Felder veraendert (kein unerlaubtes Ueberschreiben)
  09. artundweise-Fall: positive/apt=True -> negative/apt=False
  10. Bereits korrigierter Eintrag wird nicht nochmals ueberschrieben (kein Match)
  11. python mine.py --help enthaelt --reply-reclassify-apply
  12. Kein SMTP/IMAP/Send/Approve/CRM-Push in reply_reclassify_apply.py

Kein Netzwerk. Kein SMTP. Kein IMAP. Kein Send. Kein CRM-Push.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

REQUIRED_FIELDS = [
    "generated_at", "dry_run", "confirmed",
    "total_changes", "applied_count", "skipped_count",
    "backup_path", "changed_items", "warnings",
]


def _ok(label: str) -> None:
    print(f"  [OK] {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))
    sys.exit(1)


def _wj(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── Fixture-Hilfsfunktionen ────────────────────────────────────────────────────

def _make_queue(items: list[dict]) -> dict:
    return {"items": items, "total": len(items), "updated_at": "2026-01-01T00:00:00Z"}


def _artundweise_queue_item() -> dict:
    """Artundweise-Eintrag wie er in reply_queue.json gespeichert ist (alt/falsch)."""
    return {
        "from_email_actual": "we@artundweise.de",
        "from_email":        "we@artundweise.de",
        "inbound_subject":   "Re: Anfrage",
        "inbound_snippet":   (
            "Fuer die Terminfindung setzen wir inhouse auf unser eigenes Team. "
            "Daher haben wir aktuell keinen Bedarf."
        ),
        "inbound_class":     "positive",    # alt/falsch
        "confidence":        1.0,
        "appointment_ready": True,           # alt/falsch
        "appointment_reason": "positive_reply",
        "is_auto_reply":     False,
        "route":             "human",
        "action":            "review",
        "sentiment":         "positive",    # wird NICHT veraendert
        "needs_approval":    True,          # wird NICHT veraendert
    }


def _artundweise_preview_change() -> dict:
    """Passendes Change-Objekt wie es --reply-reclassify-preview erzeugen wuerde."""
    return {
        "email":               "we@artundweise.de",
        "subject":             "Re: Anfrage",
        "old_reply_class":     "positive",
        "new_reply_class":     "negative",
        "old_appointment_ready": True,
        "new_appointment_ready": False,
        "old_confidence":      1.0,
        "new_confidence":      0.88,
        "changed":             True,
        "is_auto_reply":       False,
        "reason":              "rejection_phrase_veto",
        "snippet":             "Fuer die Terminfindung setzen wir inhouse...",
    }


def _make_preview(changes: list[dict]) -> dict:
    return {
        "generated_at":  "2026-05-28T00:00:00+00:00",
        "mode":          "reply_reclassify_preview",
        "total_replies": 1,
        "changed_count": len(changes),
        "unchanged_count": 0,
        "changes":       changes,
        "unchanged":     [],
        "warnings":      [],
    }


def _run_apply(
    tmp: Path,
    queue_items: list[dict] | None = None,
    preview_changes: list[dict] | None = None,
    confirmed: bool = False,
) -> dict:
    import modules.reply_reclassify_apply as ra

    if queue_items is not None:
        _wj(tmp / "reply_queue.json", _make_queue(queue_items))
    if preview_changes is not None:
        _wj(tmp / "reply_reclassify_preview.json", _make_preview(preview_changes))

    env_patch = {"REPLY_RECLASSIFY_CONFIRMED": "1"} if confirmed else {}
    env_clean = {k: "" for k in ["REPLY_RECLASSIFY_CONFIRMED"]} if not confirmed else {}

    with patch.dict(os.environ, {**env_clean, **env_patch}):
        return ra.build_reclassify_apply(
            preview_file=tmp / "reply_reclassify_preview.json",
            reply_queue_file=tmp / "reply_queue.json",
            report_file=tmp / "reply_reclassify_apply_report.json",
        )


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_01_import_clean() -> None:
    import modules.reply_reclassify_apply  # noqa: F401
    _ok("Import: modules.reply_reclassify_apply ohne Seiteneffekte")


def test_02_missing_files_no_crash() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run_apply(Path(t))   # keine Dateien
    if report.get("total_changes") != 0:
        _fail("total_changes sollte 0 sein bei fehlenden Dateien")
    _ok("Fehlende Dateien: kein Crash, total_changes=0")


def test_03_required_fields() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run_apply(Path(t))
    missing = [f for f in REQUIRED_FIELDS if f not in report]
    if missing:
        _fail("Pflichtfelder fehlen", str(missing))
    _ok(f"Alle {len(REQUIRED_FIELDS)} Pflichtfelder vorhanden")


def test_04_dry_run_queue_unchanged() -> None:
    """Dry-Run (kein REPLY_RECLASSIFY_CONFIRMED): reply_queue.json darf sich nicht aendern."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        queue_path = tmp / "reply_queue.json"
        _wj(queue_path, _make_queue([_artundweise_queue_item()]))
        original = queue_path.read_text(encoding="utf-8")

        _run_apply(tmp,
                   queue_items=[_artundweise_queue_item()],
                   preview_changes=[_artundweise_preview_change()],
                   confirmed=False)

        after = queue_path.read_text(encoding="utf-8")
        if after != original:
            _fail("Dry-Run hat reply_queue.json veraendert!")
    _ok("Dry-Run: reply_queue.json unveraendert")


def test_05_dry_run_applied_count_zero() -> None:
    """Dry-Run: applied_count=0, dry_run=True."""
    with tempfile.TemporaryDirectory() as t:
        report = _run_apply(Path(t),
                            queue_items=[_artundweise_queue_item()],
                            preview_changes=[_artundweise_preview_change()],
                            confirmed=False)
    if report["applied_count"] != 0:
        _fail("Dry-Run: applied_count sollte 0 sein", str(report["applied_count"]))
    if report["dry_run"] is not True:
        _fail("Dry-Run: dry_run sollte True sein", str(report["dry_run"]))
    if report["total_changes"] != 1:
        _fail("Dry-Run: total_changes sollte 1 sein", str(report["total_changes"]))
    _ok("Dry-Run: applied_count=0, dry_run=True, total_changes=1")


def test_06_confirmed_creates_backup() -> None:
    """Confirmed Apply: Backup-Datei wird erstellt."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        report = _run_apply(tmp,
                            queue_items=[_artundweise_queue_item()],
                            preview_changes=[_artundweise_preview_change()],
                            confirmed=True)
        if not report["backup_path"]:
            _fail("backup_path leer nach echtem Apply")
        backup = Path(report["backup_path"])
        if not backup.is_file():
            _fail("Backup-Datei existiert nicht", report["backup_path"])
        # Backup enthaelt Original-Daten
        orig = json.loads(backup.read_text(encoding="utf-8"))
        items = orig.get("items", [])
        if not items or items[0].get("inbound_class") != "positive":
            _fail("Backup enthaelt nicht die Original-Daten")
    _ok("Confirmed Apply: Backup erstellt und enthaelt Original-Daten")


def test_07_confirmed_updates_queue() -> None:
    """Confirmed Apply: reply_queue.json wird aktualisiert."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        queue_path = tmp / "reply_queue.json"
        _wj(queue_path, _make_queue([_artundweise_queue_item()]))

        report = _run_apply(tmp,
                            preview_changes=[_artundweise_preview_change()],
                            confirmed=True)

        if report["applied_count"] != 1:
            _fail("applied_count sollte 1 sein", str(report["applied_count"]))
        updated = json.loads(queue_path.read_text(encoding="utf-8"))
        item = updated["items"][0]
        if item["inbound_class"] != "negative":
            _fail("inbound_class sollte 'negative' sein nach Apply", item["inbound_class"])
        if item["appointment_ready"] is not False:
            _fail("appointment_ready sollte False sein nach Apply")
    _ok("Confirmed Apply: reply_queue.json korrekt aktualisiert")


def test_08_only_allowed_fields_changed() -> None:
    """Confirmed Apply: nur erlaubte Felder veraendert, z.B. sentiment unveraendert."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        original_item = _artundweise_queue_item()
        _wj(tmp / "reply_queue.json", _make_queue([original_item]))
        _run_apply(tmp,
                   preview_changes=[_artundweise_preview_change()],
                   confirmed=True)
        updated = json.loads((tmp / "reply_queue.json").read_text(encoding="utf-8"))
        item = updated["items"][0]
        # Diese Felder MUSS sich aendern
        if item["inbound_class"] != "negative":
            _fail("inbound_class nicht auf 'negative' gesetzt")
        if item["appointment_ready"] is not False:
            _fail("appointment_ready nicht auf False gesetzt")
        # Diese Felder duerfen sich NICHT aendern
        if item.get("sentiment") != original_item["sentiment"]:
            _fail("'sentiment' wurde unveraendert ueberschrieben!", str(item.get("sentiment")))
        if item.get("needs_approval") != original_item["needs_approval"]:
            _fail("'needs_approval' wurde veraendert!")
        if item.get("route") != original_item["route"]:
            _fail("'route' wurde veraendert!")
    _ok("Confirmed Apply: nur erlaubte Felder veraendert")


def test_09_artundweise_full_correction() -> None:
    """artundweise-Fall: positive/apt=True -> negative/apt=False, reason korrekt."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _wj(tmp / "reply_queue.json", _make_queue([_artundweise_queue_item()]))
        report = _run_apply(tmp,
                            preview_changes=[_artundweise_preview_change()],
                            confirmed=True)
        ci = report["changed_items"][0]
        if ci["old_reply_class"] != "positive":
            _fail("old_reply_class sollte 'positive' sein", ci["old_reply_class"])
        if ci["new_reply_class"] != "negative":
            _fail("new_reply_class sollte 'negative' sein", ci["new_reply_class"])
        if ci["old_appointment_ready"] is not True:
            _fail("old_appointment_ready sollte True sein")
        if ci["new_appointment_ready"] is not False:
            _fail("new_appointment_ready sollte False sein")
        if ci.get("reason") != "rejection_phrase_veto":
            _fail("reason sollte 'rejection_phrase_veto' sein", str(ci.get("reason")))
        if not ci.get("applied"):
            _fail("applied-Flag sollte True sein")
    _ok("artundweise-Fall: positive/apt=True -> negative/apt=False korrekt korrigiert")


def test_10_already_corrected_not_overwritten() -> None:
    """Eintrag dessen inbound_class bereits korrigiert wurde, wird NICHT nochmals geaendert."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        # Queue-Eintrag ist bereits 'negative' (manuell korrigiert)
        already_fixed = dict(_artundweise_queue_item())
        already_fixed["inbound_class"] = "negative"
        already_fixed["appointment_ready"] = False
        _wj(tmp / "reply_queue.json", _make_queue([already_fixed]))
        # Preview sagt immer noch old_class=positive -> kein Match
        report = _run_apply(tmp,
                            preview_changes=[_artundweise_preview_change()],
                            confirmed=True)
        if report["applied_count"] != 0:
            _fail("Bereits korrigierter Eintrag wurde nochmals ueberschrieben!")
        if report["skipped_count"] != 1:
            _fail("skipped_count sollte 1 sein", str(report["skipped_count"]))
    _ok("Bereits korrigierter Eintrag: wird nicht nochmals ueberschrieben")


def test_11_mine_help_contains_flag() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "mine.py"), "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    if "--reply-reclassify-apply" not in result.stdout:
        _fail("--reply-reclassify-apply fehlt in mine.py --help", result.stdout[:300])
    _ok("mine.py --help: --reply-reclassify-apply vorhanden")


def test_12_no_smtp_imap_in_module() -> None:
    import modules.reply_reclassify_apply as ra
    src = Path(ra.__file__).read_text(encoding="utf-8")
    forbidden = [
        "send_email(",
        "smtplib.SMTP(",
        "imaplib.IMAP",
        "approve(",
        "crm_push_confirmed",
        "CRM_PUSH_CONFIRMED",
        "OUTREACH_SEND",
    ]
    hits = [w for w in forbidden if w in src]
    if hits:
        _fail("Verbotene Terme in reply_reclassify_apply.py", str(hits))
    _ok("reply_reclassify_apply.py: kein SMTP/IMAP/Send/Approve/CRM-Push-Code")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_01_import_clean,
    test_02_missing_files_no_crash,
    test_03_required_fields,
    test_04_dry_run_queue_unchanged,
    test_05_dry_run_applied_count_zero,
    test_06_confirmed_creates_backup,
    test_07_confirmed_updates_queue,
    test_08_only_allowed_fields_changed,
    test_09_artundweise_full_correction,
    test_10_already_corrected_not_overwritten,
    test_11_mine_help_contains_flag,
    test_12_no_smtp_imap_in_module,
]


def main() -> None:
    print("=" * 65)
    print("  smoke_reply_reclassify_apply.py")
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
