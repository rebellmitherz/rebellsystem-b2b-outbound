"""Smoke-Test: modules/reply_action_plan.py

Prueft:
  01. Import ohne SMTP/IMAP/Send
  02. Fehlende Dateien crashen nicht, total_replies=0
  03. Pflichtfelder vorhanden
  04. Auto-Reply (generisch) -> ignore_auto_reply
  05. Auto-Reply mit OOO-Hinweis -> wait_until_back
  06. Negative Antwort / Ablehnungsphrase -> mark_negative_no_followup
  07. Duplikat (gleicher entry_key) -> deduplicate_reply
  08. Duplikat (gleiche E-Mail + Betreff) -> deduplicate_reply
  09. appointment_ready + keine Ablehnung -> promote_to_hot_handoff
  10. Positiv ohne Termin -> create_followup_draft
  11. Zaehler korrekt (auto+negative+dup+followup+hot+manual == total)
  12. next_best_action-Logik: hot_handoff > followup > manual > auto > none
  13. Output-Datei wird geschrieben
  14. python mine.py --help enthaelt --reply-action-plan
  15. Kein SMTP/IMAP/Send/Approve/CRM-Push in reply_action_plan.py

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
    "total_replies", "auto_reply_count", "negative_count", "duplicate_count",
    "manual_review_count", "followup_candidate_count", "hot_handoff_candidate_count",
    "next_best_action", "actions", "warnings",
]

ACTION_FIELDS = [
    "email", "subject", "reply_class", "appointment_ready", "is_auto_reply",
    "entry_key", "recommended_action", "reason", "priority", "snippet",
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


def _item_auto_generic(email: str = "auto@test.de") -> dict:
    return {
        "entry_key":          "key_auto_generic",
        "from_email_actual":  email,
        "inbound_subject":    "AW: Anfrage",
        "inbound_snippet":    "Wir haben Ihre Nachricht erhalten und werden uns melden.",
        "inbound_class":      "neutral",
        "confidence":         0.82,
        "is_auto_reply":      True,
        "auto_reply_reason":  "auto_submitted:auto-generated",
        "appointment_ready":  False,
    }


def _item_auto_ooo(email: str = "ooo@test.de") -> dict:
    return {
        "entry_key":          "key_auto_ooo",
        "from_email_actual":  email,
        "inbound_subject":    "Abwesenheitsnotiz: Anfrage",
        "inbound_snippet":    "Ich bin zurzeit nicht im Buero. Ab dem 15.06. bin ich wieder erreichbar.",
        "inbound_class":      "neutral",
        "confidence":         0.82,
        "is_auto_reply":      True,
        "auto_reply_reason":  "auto_submitted:auto-replied",
        "appointment_ready":  False,
    }


def _item_negative(email: str = "neg@test.de") -> dict:
    return {
        "entry_key":          "key_negative",
        "from_email_actual":  email,
        "inbound_subject":    "Re: Anfrage",
        "inbound_snippet":    "Vielen Dank, aber wir haben aktuell keinen Bedarf an Ihrer Leistung.",
        "inbound_class":      "negative",
        "confidence":         0.88,
        "is_auto_reply":      False,
        "auto_reply_reason":  "",
        "appointment_ready":  False,
    }


def _item_appointment(email: str = "appt@test.de") -> dict:
    return {
        "entry_key":          "key_appt",
        "from_email_actual":  email,
        "inbound_subject":    "Re: Anfrage",
        "inbound_snippet":    "Ja, gerne. Wann haben Sie naechste Woche Zeit fuer ein kurzes Zoom-Call?",
        "inbound_class":      "positive",
        "confidence":         0.91,
        "is_auto_reply":      False,
        "auto_reply_reason":  "",
        "appointment_ready":  True,
    }


def _item_positive_no_appt(email: str = "pos@test.de") -> dict:
    return {
        "entry_key":          "key_pos",
        "from_email_actual":  email,
        "inbound_subject":    "Re: Anfrage",
        "inbound_snippet":    "Interessant, ich wuerde gerne mehr erfahren.",
        "inbound_class":      "positive",
        "confidence":         0.75,
        "is_auto_reply":      False,
        "auto_reply_reason":  "",
        "appointment_ready":  False,
    }


def _item_dup_by_key(email: str = "dup@test.de") -> dict:
    """Duplikat: gleicher entry_key wie ein anderer Eintrag."""
    return {
        "entry_key":          "key_auto_ooo",   # gleich wie _item_auto_ooo
        "from_email_actual":  email,
        "inbound_subject":    "Abwesenheitsnotiz: Anfrage",
        "inbound_snippet":    "Ich bin im Urlaub.",
        "inbound_class":      "neutral",
        "confidence":         0.82,
        "is_auto_reply":      True,
        "auto_reply_reason":  "auto_submitted:auto-replied",
        "appointment_ready":  False,
    }


def _item_dup_by_email_subject(email: str = "dup2@test.de") -> dict:
    """Erstes Vorkommen — wird als Original behandelt."""
    return {
        "entry_key":          "key_dup2a",
        "from_email_actual":  email,
        "inbound_subject":    "Re: Anfrage Sonderfall",
        "inbound_snippet":    "Interessant, wir melden uns.",
        "inbound_class":      "positive",
        "confidence":         0.7,
        "is_auto_reply":      False,
        "auto_reply_reason":  "",
        "appointment_ready":  False,
    }


def _item_dup_same_email_subj(email: str = "dup2@test.de") -> dict:
    """Zweites Vorkommen — gleiche E-Mail + gleicher normalisierter Betreff."""
    return {
        "entry_key":          "key_dup2b",
        "from_email_actual":  email,
        "inbound_subject":    "AW: Anfrage Sonderfall",   # AW: wird normalisiert
        "inbound_snippet":    "Interessant, wir melden uns.",
        "inbound_class":      "positive",
        "confidence":         0.7,
        "is_auto_reply":      False,
        "auto_reply_reason":  "",
        "appointment_ready":  False,
    }


def _run_plan(
    tmp: Path,
    queue_items: list[dict] | None = None,
) -> dict:
    import modules.reply_action_plan as rap

    if queue_items is not None:
        _wj(tmp / "reply_queue.json", _make_queue(queue_items))

    return rap.build_reply_action_plan(
        reply_queue_file  = tmp / "reply_queue.json",
        reclassify_file   = tmp / "reply_reclassify_preview.json",
        quality_file      = tmp / "reply_quality_report.json",
        hot_handoffs_file = tmp / "hot_handoffs.json",
        crm_preview_file  = tmp / "crm_payload_preview.json",
        report_file       = tmp / "reply_action_plan.json",
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_01_import_clean() -> None:
    import modules.reply_action_plan  # noqa: F401
    _ok("Import: modules.reply_action_plan ohne Seiteneffekte")


def test_02_missing_files_no_crash() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(Path(t))
    if report.get("total_replies") != 0:
        _fail("total_replies sollte 0 sein bei fehlenden Dateien", str(report.get("total_replies")))
    _ok("Fehlende Dateien: kein Crash, total_replies=0")


def test_03_required_fields() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(Path(t))
    missing = [f for f in REQUIRED_FIELDS if f not in report]
    if missing:
        _fail("Pflichtfelder fehlen", str(missing))
    # Jede Action muss alle Felder haben
    for a in report.get("actions", []):
        missing_a = [f for f in ACTION_FIELDS if f not in a]
        if missing_a:
            _fail("Action-Felder fehlen", str(missing_a))
    _ok(f"Alle {len(REQUIRED_FIELDS)} Pflichtfelder + Action-Felder vorhanden")


def test_04_auto_reply_generic() -> None:
    """Generische Auto-Antwort -> ignore_auto_reply."""
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(Path(t), queue_items=[_item_auto_generic()])
    a = report["actions"][0]
    if a["recommended_action"] != "ignore_auto_reply":
        _fail("Auto-Reply (generisch) sollte ignore_auto_reply sein", a["recommended_action"])
    if a["priority"] != "low":
        _fail("Auto-Reply sollte priority=low haben", a["priority"])
    _ok("Auto-Reply generisch -> ignore_auto_reply [low]")


def test_05_auto_reply_ooo() -> None:
    """OOO-Auto-Antwort -> wait_until_back."""
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(Path(t), queue_items=[_item_auto_ooo()])
    a = report["actions"][0]
    if a["recommended_action"] != "wait_until_back":
        _fail("OOO-Auto-Reply sollte wait_until_back sein", a["recommended_action"])
    if a["priority"] != "low":
        _fail("OOO sollte priority=low haben", a["priority"])
    _ok("Auto-Reply OOO -> wait_until_back [low]")


def test_06_negative_reply() -> None:
    """Ablehnungsphrase -> mark_negative_no_followup."""
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(Path(t), queue_items=[_item_negative()])
    a = report["actions"][0]
    if a["recommended_action"] != "mark_negative_no_followup":
        _fail("Negative Antwort sollte mark_negative_no_followup sein", a["recommended_action"])
    _ok("Negative Antwort / Ablehnung -> mark_negative_no_followup")


def test_07_deduplicate_by_entry_key() -> None:
    """Zweiter Eintrag mit gleichem entry_key -> deduplicate_reply."""
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(
            Path(t),
            queue_items=[_item_auto_ooo(), _item_dup_by_key()],
        )
    if report["duplicate_count"] != 1:
        _fail("duplicate_count sollte 1 sein", str(report["duplicate_count"]))
    actions_map = {a["entry_key"]: a for a in report["actions"]}
    # Erster Eintrag: kein Duplikat
    first = report["actions"][0]
    if first["recommended_action"] == "deduplicate_reply":
        _fail("Erster Eintrag sollte NICHT deduplicate_reply sein")
    # Zweiter Eintrag: Duplikat
    second = report["actions"][1]
    if second["recommended_action"] != "deduplicate_reply":
        _fail("Zweiter Eintrag mit gleichem entry_key sollte deduplicate_reply sein",
              second["recommended_action"])
    _ok("Duplikat per entry_key erkannt -> deduplicate_reply")


def test_08_deduplicate_by_email_subject() -> None:
    """Zweiter Eintrag mit gleicher E-Mail + Betreff (nach Re:/AW: Strip) -> deduplicate_reply."""
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(
            Path(t),
            queue_items=[_item_dup_by_email_subject(), _item_dup_same_email_subj()],
        )
    if report["duplicate_count"] != 1:
        _fail("duplicate_count sollte 1 sein", str(report["duplicate_count"]))
    # Zweiter (AW: ...) soll Duplikat sein
    second = report["actions"][1]
    if second["recommended_action"] != "deduplicate_reply":
        _fail("Zweiter Eintrag (AW: gleicher Betreff) sollte deduplicate_reply sein",
              second["recommended_action"])
    _ok("Duplikat per E-Mail + normalisiertem Betreff erkannt -> deduplicate_reply")


def test_09_promote_to_hot_handoff() -> None:
    """appointment_ready + keine Ablehnung -> promote_to_hot_handoff."""
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(Path(t), queue_items=[_item_appointment()])
    a = report["actions"][0]
    if a["recommended_action"] != "promote_to_hot_handoff":
        _fail("Termin-Wunsch sollte promote_to_hot_handoff sein", a["recommended_action"])
    if a["priority"] != "high":
        _fail("promote_to_hot_handoff sollte priority=high haben", a["priority"])
    if report["hot_handoff_candidate_count"] != 1:
        _fail("hot_handoff_candidate_count sollte 1 sein", str(report["hot_handoff_candidate_count"]))
    _ok("appointment_ready ohne Ablehnung -> promote_to_hot_handoff [high]")


def test_10_create_followup_draft() -> None:
    """Positive Antwort ohne Termin -> create_followup_draft."""
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(Path(t), queue_items=[_item_positive_no_appt()])
    a = report["actions"][0]
    if a["recommended_action"] != "create_followup_draft":
        _fail("Positiv ohne Termin sollte create_followup_draft sein", a["recommended_action"])
    if a["priority"] != "high":
        _fail("create_followup_draft sollte priority=high haben", a["priority"])
    if report["followup_candidate_count"] != 1:
        _fail("followup_candidate_count sollte 1 sein", str(report["followup_candidate_count"]))
    _ok("Positive Antwort ohne Termin -> create_followup_draft [high]")


def test_11_counters_consistent() -> None:
    """Alle Zaehler zusammen == total_replies."""
    items = [
        _item_auto_generic("a1@t.de"),
        _item_auto_ooo("a2@t.de"),
        _item_negative("n@t.de"),
        _item_appointment("ap@t.de"),
        _item_positive_no_appt("po@t.de"),
    ]
    with tempfile.TemporaryDirectory() as t:
        report = _run_plan(Path(t), queue_items=items)
    total = report["total_replies"]
    if total != 5:
        _fail("total_replies sollte 5 sein", str(total))
    summed = (
        report["auto_reply_count"]
        + report["negative_count"]
        + report["duplicate_count"]
        + report["followup_candidate_count"]
        + report["hot_handoff_candidate_count"]
        + report["manual_review_count"]
    )
    if summed != total:
        _fail(
            f"Zaehler-Summe ({summed}) != total_replies ({total})",
            str(report),
        )
    _ok(f"Zaehler-Summe == total_replies == {total}")


def test_12_next_best_action_priority() -> None:
    """next_best_action folgt der definierten Prioritaet."""
    # Hot Handoff schlaegt alles
    with tempfile.TemporaryDirectory() as t:
        r = _run_plan(Path(t), queue_items=[_item_appointment()])
    if r["next_best_action"] != "review_hot_handoff_candidates":
        _fail("Mit hot_handoff: next_best_action != review_hot_handoff_candidates",
              r["next_best_action"])
    # Nur Followup (kein Hot)
    with tempfile.TemporaryDirectory() as t:
        r = _run_plan(Path(t), queue_items=[_item_positive_no_appt()])
    if r["next_best_action"] != "create_followup_drafts":
        _fail("Nur followup: next_best_action != create_followup_drafts", r["next_best_action"])
    # Nur Auto-Reply
    with tempfile.TemporaryDirectory() as t:
        r = _run_plan(Path(t), queue_items=[_item_auto_generic()])
    if r["next_best_action"] != "wait_for_auto_replies":
        _fail("Nur auto: next_best_action != wait_for_auto_replies", r["next_best_action"])
    # Leer
    with tempfile.TemporaryDirectory() as t:
        r = _run_plan(Path(t), queue_items=[])
    if r["next_best_action"] != "no_action":
        _fail("Leer: next_best_action != no_action", r["next_best_action"])
    _ok("next_best_action Prioritaet korrekt (hot > followup > auto > none)")


def test_13_output_file_written() -> None:
    """reply_action_plan.json wird geschrieben."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _run_plan(tmp, queue_items=[_item_negative()])
        out = tmp / "reply_action_plan.json"
        if not out.is_file():
            _fail("reply_action_plan.json nicht geschrieben")
        data = json.loads(out.read_text(encoding="utf-8"))
        if data.get("mode") != "reply_action_plan":
            _fail("mode in Ausgabedatei falsch", str(data.get("mode")))
    _ok("reply_action_plan.json korrekt geschrieben")


def test_14_mine_help_contains_flag() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "mine.py"), "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(ROOT),
    )
    if "--reply-action-plan" not in result.stdout:
        _fail("--reply-action-plan fehlt in mine.py --help", result.stdout[:300])
    _ok("mine.py --help: --reply-action-plan vorhanden")


def test_15_no_smtp_imap_in_module() -> None:
    import modules.reply_action_plan as rap
    src = Path(rap.__file__).read_text(encoding="utf-8")
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
        _fail("Verbotene Terme in reply_action_plan.py", str(hits))
    _ok("reply_action_plan.py: kein SMTP/IMAP/Send/Approve/CRM-Push-Code")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_01_import_clean,
    test_02_missing_files_no_crash,
    test_03_required_fields,
    test_04_auto_reply_generic,
    test_05_auto_reply_ooo,
    test_06_negative_reply,
    test_07_deduplicate_by_entry_key,
    test_08_deduplicate_by_email_subject,
    test_09_promote_to_hot_handoff,
    test_10_create_followup_draft,
    test_11_counters_consistent,
    test_12_next_best_action_priority,
    test_13_output_file_written,
    test_14_mine_help_contains_flag,
    test_15_no_smtp_imap_in_module,
]


def main() -> None:
    print("=" * 65)
    print("  smoke_reply_action_plan.py")
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
