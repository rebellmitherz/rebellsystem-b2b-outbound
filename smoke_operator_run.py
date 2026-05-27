"""Smoke-Test: modules/operator_run.py

Prueft:
  01. Import ohne SMTP/IMAP/Send
  02. Fehlende Quelldateien crashen nicht
  03. operator_run_report.json wird erzeugt
  04. Alle Pflichtfelder vorhanden
  05. mode == "safe_operator_run"
  06. operator_next_action bei push_ready + kein live_push: configure_crm_env_or_keep_dry_run
  07. operator_next_action bei nur blocked: review_blocked_crm_payloads
  08. operator_next_action bei hot_handoffs=0: generate_more_hot_handoffs
  09. operator_next_action bei reply_queue > 0: review_replies
  10. operator_next_action bei ready_to_send > 0: review_ready_outreach
  11. operator_next_action sonst: no_action
  12. ready_to_send.csv Fallback wird benutzt wenn monthly_report fehlt
  13. reply_queue_count bevorzugt len(items) gegenueber total
  14. python mine.py --help enthaelt --operator-run

KEIN Netzwerk. KEIN API-Call. KEIN SMTP. KEIN IMAP. KEIN Token.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

REQUIRED_FIELDS = [
    "generated_at", "mode",
    "leads_found_total", "ready_to_send_total", "sent_total", "replies_total",
    "hot_handoffs_count", "reply_queue_count",
    "crm_preview_count", "crm_push_ready_count", "crm_blocked_count",
    "crm_live_push_possible", "crm_next_action",
    "operator_next_action", "warnings",
]


def _ok(label: str) -> None:
    print(f"  [OK] {label}")


def _fail(label: str, detail: str = "") -> None:
    msg = f"  [FAIL] {label}"
    if detail:
        msg += f": {detail}"
    print(msg)
    sys.exit(1)


def _wj(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _wcsv(path: Path, rows: int) -> None:
    """Schreibt einfache CSV mit N Datenzeilen."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["email,company_name,score"] + [f"lead{i}@test.de,Firma{i} GmbH,75" for i in range(rows)]
    path.write_text("\n".join(lines), encoding="utf-8")


def _make_preview(push_ready: int = 0, blocked: int = 0) -> dict:
    payloads = (
        [{"crm_push_ready": True,  "crm_push_block_reason": ""} for _ in range(push_ready)] +
        [{"crm_push_ready": False, "crm_push_block_reason": "review_required"} for _ in range(blocked)]
    )
    return {
        "count": push_ready + blocked,
        "push_ready_count": push_ready,
        "blocked_count": blocked,
        "payloads": payloads,
    }


def _make_crm_status(live_push: bool = False, next_action: str = "no_action") -> dict:
    return {
        "live_push_possible": live_push,
        "next_action": next_action,
        "push_ready_count": 0,
        "blocked_count": 0,
    }


def _make_reply_queue(items: int = 0, total: int = 0) -> dict:
    return {
        "total": total,
        "items": [{"email": f"r{i}@test.de"} for i in range(items)],
    }


def _make_hot_handoffs(count: int) -> dict:
    return {
        "count": count,
        "handoffs": [{"email": f"hh{i}@test.de"} for i in range(count)],
    }


def _make_monthly(
    leads: int = 0, ready: int = 0, sent: int = 0, replies: int = 0
) -> dict:
    return {
        "leads_found_total": leads,
        "ready_to_send_total": ready,
        "sent_total": sent,
        "replies_total": replies,
    }


def _run(
    tmp: Path,
    monthly: dict | None = None,
    preview: dict | None = None,
    crm_status: dict | None = None,
    reply_queue: dict | None = None,
    hot_handoffs: dict | None = None,
    ready_to_send_rows: int = 0,
) -> dict:
    """Hilfsfunktion: schreibt Quelldateien in tmp und ruft build_operator_run_report auf."""
    import modules.operator_run as op

    if monthly is not None:
        _wj(tmp / "monthly_report.json", monthly)
    if preview is not None:
        _wj(tmp / "crm_payload_preview.json", preview)
    if crm_status is not None:
        _wj(tmp / "crm_status_report.json", crm_status)
    if reply_queue is not None:
        _wj(tmp / "reply_queue.json", reply_queue)
    if hot_handoffs is not None:
        _wj(tmp / "hot_handoffs.json", hot_handoffs)
    if ready_to_send_rows > 0:
        _wcsv(tmp / "ready_to_send.csv", ready_to_send_rows)

    out_file = tmp / "operator_run_report.json"
    report = op.build_operator_run_report(latest=tmp, out_file=out_file)
    return report


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_01_import_clean() -> None:
    import modules.operator_run  # noqa: F401
    _ok("Import: modules.operator_run ohne Seiteneffekte")


def test_02_all_files_missing_no_crash() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t))  # alle Quelldateien fehlen
    if report.get("mode") != "safe_operator_run":
        _fail("Fehlende Dateien: mode falsch", report.get("mode", "?"))
    _ok("Alle Quelldateien fehlen: kein Crash")


def test_03_report_file_written() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _run(tmp, monthly=_make_monthly())
        out = tmp / "operator_run_report.json"
        if not out.is_file():
            _fail("operator_run_report.json nicht geschrieben")
    _ok("operator_run_report.json wird erzeugt")


def test_04_required_fields() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t))
    missing = [f for f in REQUIRED_FIELDS if f not in report]
    if missing:
        _fail("Pflichtfelder fehlen", str(missing))
    _ok(f"Alle {len(REQUIRED_FIELDS)} Pflichtfelder vorhanden")


def test_05_mode_field() -> None:
    with tempfile.TemporaryDirectory() as t:
        report = _run(Path(t))
    if report.get("mode") != "safe_operator_run":
        _fail("mode != safe_operator_run", report.get("mode", "?"))
    _ok("mode == 'safe_operator_run'")


def test_06_next_action_configure_env() -> None:
    """push_ready > 0 und live_push=false -> configure_crm_env_or_keep_dry_run."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            preview=_make_preview(push_ready=2, blocked=1),
            crm_status=_make_crm_status(live_push=False),
            hot_handoffs=_make_hot_handoffs(1),
            reply_queue=_make_reply_queue(items=0),
        )
    if report.get("operator_next_action") != "configure_crm_env_or_keep_dry_run":
        _fail(
            "operator_next_action falsch",
            f"got={report.get('operator_next_action')} push_ready={report.get('crm_push_ready_count')}",
        )
    _ok("operator_next_action=configure_crm_env_or_keep_dry_run (push_ready + kein live_push)")


def test_07_next_action_review_blocked() -> None:
    """Nur blocked, kein push_ready -> review_blocked_crm_payloads."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            preview=_make_preview(push_ready=0, blocked=3),
            crm_status=_make_crm_status(live_push=False),
            hot_handoffs=_make_hot_handoffs(1),
            reply_queue=_make_reply_queue(items=0),
        )
    if report.get("operator_next_action") != "review_blocked_crm_payloads":
        _fail("operator_next_action falsch", report.get("operator_next_action", "?"))
    _ok("operator_next_action=review_blocked_crm_payloads")


def test_08_next_action_generate_handoffs() -> None:
    """Keine Hot Handoffs -> generate_more_hot_handoffs."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            preview=_make_preview(push_ready=0, blocked=0),
            hot_handoffs=_make_hot_handoffs(0),
            reply_queue=_make_reply_queue(items=0),
        )
    if report.get("operator_next_action") != "generate_more_hot_handoffs":
        _fail("operator_next_action falsch", report.get("operator_next_action", "?"))
    _ok("operator_next_action=generate_more_hot_handoffs (hot_handoffs=0)")


def test_09_next_action_review_replies() -> None:
    """reply_queue > 0 -> review_replies (wenn keine CRM-Aktion priorisiert)."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            preview=_make_preview(push_ready=0, blocked=0),
            hot_handoffs=_make_hot_handoffs(1),
            reply_queue=_make_reply_queue(items=3),
        )
    if report.get("operator_next_action") != "review_replies":
        _fail("operator_next_action falsch", report.get("operator_next_action", "?"))
    if report.get("reply_queue_count") != 3:
        _fail("reply_queue_count != 3", str(report.get("reply_queue_count")))
    _ok("operator_next_action=review_replies (reply_queue=3)")


def test_10_next_action_review_outreach() -> None:
    """ready_to_send > 0 -> review_ready_outreach (wenn keine priorisierte Aktion)."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            monthly=_make_monthly(ready=5),
            preview=_make_preview(push_ready=0, blocked=0),
            hot_handoffs=_make_hot_handoffs(1),
            reply_queue=_make_reply_queue(items=0),
        )
    if report.get("operator_next_action") != "review_ready_outreach":
        _fail("operator_next_action falsch", report.get("operator_next_action", "?"))
    _ok("operator_next_action=review_ready_outreach (ready_to_send=5)")


def test_11_next_action_no_action() -> None:
    """Alles leer/null -> no_action."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            monthly=_make_monthly(leads=10, ready=0, sent=10, replies=0),
            preview=_make_preview(push_ready=0, blocked=0),
            hot_handoffs=_make_hot_handoffs(1),
            reply_queue=_make_reply_queue(items=0),
        )
    if report.get("operator_next_action") != "no_action":
        _fail("operator_next_action falsch", report.get("operator_next_action", "?"))
    _ok("operator_next_action=no_action")


def test_12_ready_to_send_csv_fallback() -> None:
    """CSV-Fallback wenn monthly_report.json fehlt oder ready=0."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        # monthly_report ohne ready_to_send
        report = _run(
            tmp,
            monthly=_make_monthly(leads=5, ready=0),
            hot_handoffs=_make_hot_handoffs(1),
            ready_to_send_rows=7,
        )
    if report.get("ready_to_send_total") != 7:
        _fail("CSV-Fallback: ready_to_send_total != 7", str(report.get("ready_to_send_total")))
    _ok("ready_to_send.csv Fallback: 7 Zeilen korrekt gezaehlt")


def test_13_reply_queue_prefers_items_len() -> None:
    """reply_queue_count bevorzugt len(items) wenn items > 0 (total kann abweichen)."""
    with tempfile.TemporaryDirectory() as t:
        report = _run(
            Path(t),
            reply_queue=_make_reply_queue(items=5, total=0),  # inkonsistente Daten
            hot_handoffs=_make_hot_handoffs(1),
        )
    if report.get("reply_queue_count") != 5:
        _fail("reply_queue_count sollte 5 sein (aus items)", str(report.get("reply_queue_count")))
    _ok("reply_queue_count bevorzugt len(items) gegenueber total")


def test_14_mine_help_contains_operator_run() -> None:
    """python mine.py --help muss --operator-run enthalten."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "mine.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(ROOT),
    )
    if "--operator-run" not in result.stdout:
        _fail(
            "mine.py --help: --operator-run fehlt",
            f"stdout[:500]={result.stdout[:500]}",
        )
    _ok("mine.py --help: --operator-run vorhanden")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_01_import_clean,
    test_02_all_files_missing_no_crash,
    test_03_report_file_written,
    test_04_required_fields,
    test_05_mode_field,
    test_06_next_action_configure_env,
    test_07_next_action_review_blocked,
    test_08_next_action_generate_handoffs,
    test_09_next_action_review_replies,
    test_10_next_action_review_outreach,
    test_11_next_action_no_action,
    test_12_ready_to_send_csv_fallback,
    test_13_reply_queue_prefers_items_len,
    test_14_mine_help_contains_operator_run,
]


def main() -> None:
    print("=" * 65)
    print("  smoke_operator_run.py -- Operator Run Smoke-Test")
    print("  Kein Netzwerk. Kein API-Call. Kein SMTP. Kein IMAP.")
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
