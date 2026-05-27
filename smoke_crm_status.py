"""Smoke-Test: modules/crm_status.py

Prueft:
  01. Import ohne Netzwerk / ohne SMTP / ohne IMAP
  02. Fehlende Preview crasht nicht -> next_action=run_crm_preview
  03. missing_confirm_flag wird erkannt
  04. missing_token wird erkannt
  05. wrong_provider wird erkannt
  06. no_push_ready_payloads wird erkannt
  07. live_push_possible nur true wenn ALLE Bedingungen erfuellt
  08. crm_status_report.json in Temp-Dir geschrieben
  09. push_log gelesen wenn vorhanden (last_pushed_count)
  10. python mine.py --help enthaelt --crm-status

KEIN Netzwerk. KEIN API-Call. KEIN SMTP. KEIN IMAP. KEIN Token.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def _ok(label: str) -> None:
    print(f"  [OK] {label}")


def _fail(label: str, detail: str = "") -> None:
    msg = f"  [FAIL] {label}"
    if detail:
        msg += f": {detail}"
    print(msg)
    sys.exit(1)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _minimal_preview(payloads: list[dict]) -> dict:
    return {
        "dry_run": True,
        "provider": "generic",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "count": len(payloads),
        "payloads": payloads,
        "warnings": [],
    }


def _blocked_payload(email: str = "blocked@test.de") -> dict:
    return {
        "email": email,
        "company_name": "Testfirma GmbH",
        "crm_push_ready": False,
        "crm_push_block_reason": "review_required",
        "proposed_stage": "review_required",
        "estimated_value_eur": 0,
    }


def _push_ready_payload(email: str = "ready@testfirma.de") -> dict:
    return {
        "email": email,
        "company_name": "Qualifizierte GmbH",
        "crm_push_ready": True,
        "crm_push_block_reason": "",
        "proposed_stage": "appointment_ready",
        "estimated_value_eur": 5000,
    }


def _strip_env(*keys: str) -> dict:
    """Entfernt Keys aus os.environ, gibt Original-Werte zurueck."""
    return {k: os.environ.pop(k, None) for k in keys}


def _restore_env(backup: dict) -> None:
    for k, v in backup.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


CRM_KEYS = ("CRM_PUSH_CONFIRMED", "PIPEDRIVE_API_TOKEN", "CRM_PROVIDER")


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_01_import_clean() -> None:
    """Import ohne Netzwerk / SMTP / IMAP."""
    import modules.crm_status as cs  # noqa: F401
    _ok("Import: modules.crm_status ohne Seiteneffekte")


def test_02_missing_preview_no_crash() -> None:
    """Fehlende Preview crasht nicht, next_action=run_crm_preview."""
    import modules.crm_status as cs

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        report = cs.build_crm_status(
            preview_file=tmp / "nonexistent.json",
            push_log_file=tmp / "no_log.json",
            status_out_file=tmp / "crm_status_report.json",
        )

    if report.get("next_action") != "run_crm_preview":
        _fail("Fehlende Preview: next_action != run_crm_preview", report.get("next_action", "?"))
    if report.get("preview_exists") is not False:
        _fail("Fehlende Preview: preview_exists sollte False sein")
    if report.get("live_push_possible") is not False:
        _fail("Fehlende Preview: live_push_possible sollte False sein")
    if report.get("live_push_block_reason") != "missing_preview":
        _fail("Fehlende Preview: block_reason != missing_preview", report.get("live_push_block_reason"))
    _ok("Fehlende Preview: kein Crash, block=missing_preview, next_action=run_crm_preview")


def test_03_missing_confirm_flag() -> None:
    """Ohne CRM_PUSH_CONFIRMED=1 -> block=missing_confirm_flag."""
    import modules.crm_status as cs

    backup = _strip_env(*CRM_KEYS)
    os.environ["CRM_PROVIDER"] = "pipedrive"
    os.environ["PIPEDRIVE_API_TOKEN"] = "dummy"
    # CRM_PUSH_CONFIRMED absichtlich NICHT gesetzt

    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = tmp / "crm_payload_preview.json"
            _write_json(preview_file, _minimal_preview([_push_ready_payload()]))

            report = cs.build_crm_status(
                preview_file=preview_file,
                push_log_file=tmp / "no_log.json",
                status_out_file=tmp / "crm_status_report.json",
            )

        if report.get("live_push_block_reason") != "missing_confirm_flag":
            _fail("missing_confirm_flag nicht erkannt", report.get("live_push_block_reason", "?"))
        if report.get("live_push_possible") is not False:
            _fail("live_push_possible sollte False sein")
        _ok("missing_confirm_flag korrekt erkannt")
    finally:
        _restore_env(backup)
        for k in ("CRM_PROVIDER", "PIPEDRIVE_API_TOKEN"):
            os.environ.pop(k, None)


def test_04_missing_token() -> None:
    """Ohne PIPEDRIVE_API_TOKEN -> block=missing_token."""
    import modules.crm_status as cs

    backup = _strip_env(*CRM_KEYS)
    os.environ["CRM_PROVIDER"] = "pipedrive"
    os.environ["CRM_PUSH_CONFIRMED"] = "1"
    # PIPEDRIVE_API_TOKEN absichtlich NICHT gesetzt

    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = tmp / "crm_payload_preview.json"
            _write_json(preview_file, _minimal_preview([_push_ready_payload()]))

            report = cs.build_crm_status(
                preview_file=preview_file,
                push_log_file=tmp / "no_log.json",
                status_out_file=tmp / "crm_status_report.json",
            )

        if report.get("live_push_block_reason") != "missing_token":
            _fail("missing_token nicht erkannt", report.get("live_push_block_reason", "?"))
        if report.get("token_present") is not False:
            _fail("token_present sollte False sein")
        _ok("missing_token korrekt erkannt")
    finally:
        _restore_env(backup)
        for k in ("CRM_PROVIDER", "CRM_PUSH_CONFIRMED"):
            os.environ.pop(k, None)


def test_05_wrong_provider() -> None:
    """Falscher CRM_PROVIDER -> block=wrong_provider."""
    import modules.crm_status as cs

    backup = _strip_env(*CRM_KEYS)
    os.environ["CRM_PROVIDER"] = "hubspot"
    os.environ["CRM_PUSH_CONFIRMED"] = "1"
    os.environ["PIPEDRIVE_API_TOKEN"] = "dummy"

    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = tmp / "crm_payload_preview.json"
            _write_json(preview_file, _minimal_preview([_push_ready_payload()]))

            report = cs.build_crm_status(
                preview_file=preview_file,
                push_log_file=tmp / "no_log.json",
                status_out_file=tmp / "crm_status_report.json",
            )

        if report.get("live_push_block_reason") != "wrong_provider":
            _fail("wrong_provider nicht erkannt", report.get("live_push_block_reason", "?"))
        _ok("wrong_provider korrekt erkannt")
    finally:
        _restore_env(backup)
        for k in CRM_KEYS:
            os.environ.pop(k, None)


def test_06_no_push_ready_payloads() -> None:
    """Alle Payloads blockiert -> block=no_push_ready_payloads."""
    import modules.crm_status as cs

    backup = _strip_env(*CRM_KEYS)
    os.environ["CRM_PROVIDER"] = "pipedrive"
    os.environ["CRM_PUSH_CONFIRMED"] = "1"
    os.environ["PIPEDRIVE_API_TOKEN"] = "dummy"

    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = tmp / "crm_payload_preview.json"
            _write_json(preview_file, _minimal_preview([_blocked_payload()]))

            report = cs.build_crm_status(
                preview_file=preview_file,
                push_log_file=tmp / "no_log.json",
                status_out_file=tmp / "crm_status_report.json",
            )

        if report.get("live_push_block_reason") != "no_push_ready_payloads":
            _fail("no_push_ready_payloads nicht erkannt", report.get("live_push_block_reason", "?"))
        if report.get("push_ready_count") != 0:
            _fail("push_ready_count sollte 0 sein", str(report.get("push_ready_count")))
        if report.get("blocked_count") != 1:
            _fail("blocked_count sollte 1 sein", str(report.get("blocked_count")))
        _ok("no_push_ready_payloads korrekt erkannt")
    finally:
        _restore_env(backup)
        for k in CRM_KEYS:
            os.environ.pop(k, None)


def test_07_live_push_possible_only_when_all_conditions_met() -> None:
    """live_push_possible=True nur wenn ALLE Bedingungen erfuellt."""
    import modules.crm_status as cs

    backup = _strip_env(*CRM_KEYS)
    os.environ["CRM_PROVIDER"] = "pipedrive"
    os.environ["CRM_PUSH_CONFIRMED"] = "1"
    os.environ["PIPEDRIVE_API_TOKEN"] = "real-token"

    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = tmp / "crm_payload_preview.json"
            _write_json(preview_file, _minimal_preview([_push_ready_payload()]))

            report = cs.build_crm_status(
                preview_file=preview_file,
                push_log_file=tmp / "no_log.json",
                status_out_file=tmp / "crm_status_report.json",
            )

        if not report.get("live_push_possible"):
            _fail(
                "live_push_possible sollte True sein (alle Guards erfuellt)",
                f"block={report.get('live_push_block_reason')} report={report}",
            )
        if report.get("next_action") != "ready_for_guarded_crm_push":
            _fail("next_action != ready_for_guarded_crm_push", report.get("next_action", "?"))
        _ok("live_push_possible=True bei vollstaendigen Guards + push_ready Payload")
    finally:
        _restore_env(backup)
        for k in CRM_KEYS:
            os.environ.pop(k, None)


def test_08_report_written_to_temp() -> None:
    """crm_status_report.json nur im Temp-Dir geschrieben."""
    import modules.crm_status as cs

    backup = _strip_env(*CRM_KEYS)
    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = tmp / "crm_payload_preview.json"
            _write_json(preview_file, _minimal_preview([_blocked_payload()]))
            status_out = tmp / "crm_status_report.json"

            cs.build_crm_status(
                preview_file=preview_file,
                push_log_file=tmp / "no_log.json",
                status_out_file=status_out,
            )

            if not status_out.is_file():
                _fail("crm_status_report.json nicht geschrieben")
            report = json.loads(status_out.read_text(encoding="utf-8"))
            required_fields = [
                "generated_at", "crm_provider", "crm_push_confirmed",
                "token_present", "live_push_possible", "live_push_block_reason",
                "preview_exists", "preview_count", "push_ready_count",
                "blocked_count", "blocked_reasons", "last_push_log_exists",
                "last_push_dry_run", "last_pushed_count", "last_failed_count",
                "next_action", "warnings",
            ]
            missing = [f for f in required_fields if f not in report]
            if missing:
                _fail("Pflichtfelder fehlen in crm_status_report.json", str(missing))

        _ok("crm_status_report.json in Temp-Dir, alle Pflichtfelder vorhanden")
    finally:
        _restore_env(backup)


def test_09_push_log_read_when_present() -> None:
    """Push-Log wird gelesen wenn vorhanden; last_pushed_count korrekt."""
    import modules.crm_status as cs

    backup = _strip_env(*CRM_KEYS)
    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = tmp / "crm_payload_preview.json"
            _write_json(preview_file, _minimal_preview([_blocked_payload()]))

            push_log_file = tmp / "crm_push_log.json"
            _write_json(push_log_file, {
                "summary": {
                    "dry_run": False,
                    "pushed": 3,
                    "failed": 1,
                    "provider": "pipedrive",
                },
                "results": [],
            })

            report = cs.build_crm_status(
                preview_file=preview_file,
                push_log_file=push_log_file,
                status_out_file=tmp / "crm_status_report.json",
            )

        if not report.get("last_push_log_exists"):
            _fail("last_push_log_exists sollte True sein")
        if report.get("last_pushed_count") != 3:
            _fail("last_pushed_count != 3", str(report.get("last_pushed_count")))
        if report.get("last_failed_count") != 1:
            _fail("last_failed_count != 1", str(report.get("last_failed_count")))
        if report.get("last_push_dry_run") is not False:
            _fail("last_push_dry_run sollte False sein")
        _ok("Push-Log korrekt gelesen: last_pushed_count=3, last_failed_count=1")
    finally:
        _restore_env(backup)


def test_10_mine_help_contains_crm_status() -> None:
    """python mine.py --help muss --crm-status enthalten."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "mine.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(ROOT),
    )
    if "--crm-status" not in result.stdout:
        _fail(
            "mine.py --help: --crm-status fehlt",
            f"stdout[:500]={result.stdout[:500]}",
        )
    _ok("mine.py --help: --crm-status vorhanden")


def test_11_next_action_configure_env() -> None:
    """push_ready Payload vorhanden aber ENV unvollstaendig -> configure_crm_env."""
    import modules.crm_status as cs

    backup = _strip_env(*CRM_KEYS)
    os.environ["CRM_PROVIDER"] = "pipedrive"
    # CRM_PUSH_CONFIRMED fehlt absichtlich

    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = tmp / "crm_payload_preview.json"
            _write_json(preview_file, _minimal_preview([_push_ready_payload()]))

            report = cs.build_crm_status(
                preview_file=preview_file,
                push_log_file=tmp / "no_log.json",
                status_out_file=tmp / "crm_status_report.json",
            )

        if report.get("next_action") != "configure_crm_env":
            _fail(
                "next_action != configure_crm_env",
                f"got={report.get('next_action')}, push_ready={report.get('push_ready_count')}",
            )
        _ok("next_action=configure_crm_env bei push_ready + unvollstaendiger ENV")
    finally:
        _restore_env(backup)
        os.environ.pop("CRM_PROVIDER", None)


def test_12_review_required_in_blocked_reasons() -> None:
    """review_required erscheint in blocked_reasons."""
    import modules.crm_status as cs

    backup = _strip_env(*CRM_KEYS)
    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = tmp / "crm_payload_preview.json"
            _write_json(preview_file, _minimal_preview([
                _blocked_payload("a@test.de"),
                _blocked_payload("b@test.de"),
            ]))

            report = cs.build_crm_status(
                preview_file=preview_file,
                push_log_file=tmp / "no_log.json",
                status_out_file=tmp / "crm_status_report.json",
            )

        reasons = report.get("blocked_reasons", [])
        if "review_required" not in reasons:
            _fail("review_required nicht in blocked_reasons", str(reasons))
        # Keine Duplikate
        if len(reasons) != len(set(reasons)):
            _fail("blocked_reasons enthaelt Duplikate", str(reasons))
        _ok("review_required in blocked_reasons, keine Duplikate")
    finally:
        _restore_env(backup)


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_01_import_clean,
    test_02_missing_preview_no_crash,
    test_03_missing_confirm_flag,
    test_04_missing_token,
    test_05_wrong_provider,
    test_06_no_push_ready_payloads,
    test_07_live_push_possible_only_when_all_conditions_met,
    test_08_report_written_to_temp,
    test_09_push_log_read_when_present,
    test_10_mine_help_contains_crm_status,
    test_11_next_action_configure_env,
    test_12_review_required_in_blocked_reasons,
]


def main() -> None:
    print("=" * 65)
    print("  smoke_crm_status.py -- CRM Status Preflight Smoke-Test")
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
