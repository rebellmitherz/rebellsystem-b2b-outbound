"""Smoke-Test: modules/crm_push.py

Prüft:
  1. Import ohne SMTP/IMAP/Send
  2. Guard: kein Push ohne CRM_PUSH_CONFIRMED=1
  3. Guard: kein Push ohne PIPEDRIVE_API_TOKEN
  4. Guard: kein Push ohne CRM_PROVIDER=pipedrive
  5. crm_push_ready=false → immer übersprungen (blocked_not_push_ready)
  6. review_required → nie gepusht
  7. crm_push_ready=true → Pipedrive-Abfolge: Org → Person → Deal → Note (gemockt)
  8. org_id / person_id / deal_id im Ergebnis
  9. crm_push_log.json wird in Temp-Dir geschrieben
 10. python mine.py --help enthält --crm-push

KEIN echter API-Call. KEIN SMTP. KEIN IMAP. KEIN Token.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

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


def _make_preview(payloads: list[dict], tmp: Path) -> Path:
    """Schreibt crm_payload_preview.json in tmp-Verzeichnis."""
    data = {
        "dry_run": True,
        "provider": "generic",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "count": len(payloads),
        "payloads": payloads,
        "warnings": [],
    }
    f = tmp / "crm_payload_preview.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return f


def _blocked_payload(email: str = "blocked@test.de") -> dict:
    return {
        "email": email,
        "company_name": "Testfirma GmbH",
        "contact_name": "Max Muster",
        "phone": "",
        "website": "https://testfirma.de",
        "subject": "KI-gestützte Terminierung",
        "reply_snippet": "",
        "source": "outreach_pipeline",
        "reply_class": "review_required",
        "confidence": None,
        "proposed_stage": "review_required",
        "proposed_action": "Manuell pruefen",
        "estimated_value_eur": 0,
        "next_step": "Manuelle Pruefung",
        "owner_note": "Testnotiz",
        "crm_push_ready": False,
        "crm_push_block_reason": "review_required",
        "crm_push_mode": "dry_run_only",
    }


def _push_ready_payload(email: str = "ready@testfirma.de") -> dict:
    return {
        "email": email,
        "company_name": "Qualifizierte GmbH",
        "contact_name": "Erika Muster",
        "phone": "+49 89 12345",
        "website": "https://qualifizierte-gmbh.de",
        "subject": "Terminierung fuer B2B-Vertrieb",
        "reply_snippet": "Ja, gerne Termin vereinbaren.",
        "source": "outreach_pipeline",
        "reply_class": "hot",
        "confidence": 0.95,
        "proposed_stage": "appointment_ready",
        "proposed_action": "Termin bestaetigen und Kalender-Einladung senden",
        "estimated_value_eur": 5000,
        "next_step": "Kalendereinladung senden",
        "owner_note": "Sehr qualifizierter Lead — schnell handeln.",
        "crm_push_ready": True,
        "crm_push_block_reason": "",
        "crm_push_mode": "live",
    }


# Pipedrive-Mock: gibt je nach endpoint unterschiedliche IDs zurück
_ORG_ID    = 1001
_PERSON_ID = 2001
_DEAL_ID   = 3001
_NOTE_ID   = 4001

def _mock_pipedrive_post(endpoint: str, token: str, body: dict) -> dict:
    """Simuliert Pipedrive-API ohne Netzwerk-Request."""
    id_map = {
        "organizations": _ORG_ID,
        "persons":       _PERSON_ID,
        "deals":         _DEAL_ID,
        "notes":         _NOTE_ID,
    }
    ep_key = endpoint.split("?")[0].rstrip("/")
    return {"success": True, "data": {"id": id_map.get(ep_key, 9999)}}


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_01_import_clean() -> None:
    """Import darf kein SMTP/IMAP/Send auslösen."""
    import modules.crm_push as crm  # noqa: F401
    _ok("Import: modules.crm_push lädt ohne Seiteneffekte")


def test_02_guard_no_confirmed() -> None:
    """Ohne CRM_PUSH_CONFIRMED=1 → kein Push."""
    import modules.crm_push as crm
    env_backup = {k: os.environ.pop(k, None) for k in
                  ("CRM_PUSH_CONFIRMED", "PIPEDRIVE_API_TOKEN", "CRM_PROVIDER")}
    try:
        allowed, reason = crm._check_push_allowed()
        if allowed:
            _fail("Guard: erlaubt ohne CRM_PUSH_CONFIRMED")
        if "CRM_PUSH_CONFIRMED" not in reason:
            _fail("Guard: Fehlermeldung nennt CRM_PUSH_CONFIRMED nicht", reason)
        _ok("Guard: kein Push ohne CRM_PUSH_CONFIRMED=1")
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


def test_03_guard_no_token() -> None:
    """Ohne PIPEDRIVE_API_TOKEN → kein Push."""
    import modules.crm_push as crm
    env_backup = {k: os.environ.pop(k, None) for k in
                  ("CRM_PUSH_CONFIRMED", "PIPEDRIVE_API_TOKEN", "CRM_PROVIDER")}
    os.environ["CRM_PUSH_CONFIRMED"] = "1"
    try:
        allowed, reason = crm._check_push_allowed()
        if allowed:
            _fail("Guard: erlaubt ohne PIPEDRIVE_API_TOKEN")
        if "PIPEDRIVE_API_TOKEN" not in reason:
            _fail("Guard: Fehlermeldung nennt PIPEDRIVE_API_TOKEN nicht", reason)
        _ok("Guard: kein Push ohne PIPEDRIVE_API_TOKEN")
    finally:
        os.environ.pop("CRM_PUSH_CONFIRMED", None)
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


def test_04_guard_wrong_provider() -> None:
    """Ohne CRM_PROVIDER=pipedrive → kein Push."""
    import modules.crm_push as crm
    env_backup = {k: os.environ.pop(k, None) for k in
                  ("CRM_PUSH_CONFIRMED", "PIPEDRIVE_API_TOKEN", "CRM_PROVIDER")}
    os.environ["CRM_PUSH_CONFIRMED"] = "1"
    os.environ["PIPEDRIVE_API_TOKEN"] = "dummy-token"
    os.environ["CRM_PROVIDER"] = "hubspot"  # falsch
    try:
        allowed, reason = crm._check_push_allowed()
        if allowed:
            _fail("Guard: erlaubt mit CRM_PROVIDER=hubspot")
        if "CRM_PROVIDER" not in reason:
            _fail("Guard: Fehlermeldung nennt CRM_PROVIDER nicht", reason)
        _ok("Guard: kein Push mit falschem CRM_PROVIDER")
    finally:
        for k in ("CRM_PUSH_CONFIRMED", "PIPEDRIVE_API_TOKEN", "CRM_PROVIDER"):
            os.environ.pop(k, None)
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


def test_05_blocked_payload_skipped() -> None:
    """crm_push_ready=false → status=blocked_not_push_ready, kein API-Call."""
    import modules.crm_push as crm

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        preview_file = _make_preview([_blocked_payload()], tmp)
        push_log_file = tmp / "crm_push_log.json"

        with patch.object(crm, "_pipedrive_post", side_effect=AssertionError("Netzwerk-Call im Dry-Run!")):
            result = crm.run_crm_push(
                preview_file=preview_file,
                push_log_file=push_log_file,
                force_dry_run=True,
            )

        results = result.get("results", [])
        if len(results) != 1:
            _fail("Blocked Payload: falsche Anzahl Ergebnisse", str(len(results)))
        r = results[0]
        if r.get("status") != "blocked_not_push_ready":
            _fail("Blocked Payload: falscher Status", r.get("status", "?"))
        _ok("Blocked Payload: crm_push_ready=false → blocked_not_push_ready, kein API-Call")


def test_06_review_required_never_pushed() -> None:
    """review_required-Stage → niemals gepusht (crm_push_ready=false)."""
    import modules.crm_push as crm

    review_payload = _blocked_payload("review@test.de")
    review_payload["proposed_stage"] = "review_required"
    review_payload["crm_push_ready"] = False
    review_payload["crm_push_block_reason"] = "review_required"

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        preview_file = _make_preview([review_payload], tmp)
        push_log_file = tmp / "crm_push_log.json"

        with patch.object(crm, "_pipedrive_post", side_effect=AssertionError("Netzwerk-Call verboten!")):
            result = crm.run_crm_push(
                preview_file=preview_file,
                push_log_file=push_log_file,
                force_dry_run=True,
            )

        r = result["results"][0]
        if r.get("status") != "blocked_not_push_ready":
            _fail("review_required: nicht korrekt blockiert", r.get("status", "?"))
        if r.get("block_reason") != "review_required":
            _fail("review_required: falscher block_reason", r.get("block_reason", "?"))
        _ok("review_required: niemals gepusht")


def test_07_push_ready_mock_full_flow() -> None:
    """crm_push_ready=true → Org → Person → Deal → Note (alle vier API-Calls gemockt)."""
    import modules.crm_push as crm

    # Env-Vars für echten Push setzen (nur für diesen Test)
    env_backup = {k: os.environ.pop(k, None) for k in
                  ("CRM_PUSH_CONFIRMED", "PIPEDRIVE_API_TOKEN", "CRM_PROVIDER")}
    os.environ["CRM_PUSH_CONFIRMED"] = "1"
    os.environ["PIPEDRIVE_API_TOKEN"] = "smoke-test-token"
    os.environ["CRM_PROVIDER"] = "pipedrive"

    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = _make_preview([_push_ready_payload()], tmp)
            push_log_file = tmp / "crm_push_log.json"

            calls_recorded: list[tuple[str, dict]] = []

            def _mock_post(endpoint: str, token: str, body: dict) -> dict:
                calls_recorded.append((endpoint, body))
                return _mock_pipedrive_post(endpoint, token, body)

            with patch.object(crm, "_pipedrive_post", side_effect=_mock_post):
                result = crm.run_crm_push(
                    preview_file=preview_file,
                    push_log_file=push_log_file,
                )

        # ── Ergebnis prüfen ───────────────────────────────────────────────────
        summary = result.get("summary", {})
        if summary.get("dry_run"):
            _fail("Mock-Push: dry_run=True obwohl alle Guards erfüllt", str(summary))

        results = result.get("results", [])
        if not results:
            _fail("Mock-Push: keine Ergebnisse")

        r = results[0]
        if r.get("status") != "success":
            _fail("Mock-Push: status != success", str(r))

        # ── org_id, person_id, deal_id prüfen ─────────────────────────────────
        if r.get("org_id") != _ORG_ID:
            _fail(f"Mock-Push: org_id erwartet {_ORG_ID}", str(r.get("org_id")))
        if r.get("person_id") != _PERSON_ID:
            _fail(f"Mock-Push: person_id erwartet {_PERSON_ID}", str(r.get("person_id")))
        if r.get("deal_id") != _DEAL_ID:
            _fail(f"Mock-Push: deal_id erwartet {_DEAL_ID}", str(r.get("deal_id")))

        # ── API-Call-Reihenfolge prüfen: Org → Person → Deal → Note ──────────
        endpoints_called = [ep for ep, _ in calls_recorded]
        expected_order = ["organizations", "persons", "deals", "notes"]
        if endpoints_called != expected_order:
            _fail(
                "Mock-Push: falsche API-Call-Reihenfolge",
                f"erwartet={expected_order}, erhalten={endpoints_called}",
            )

        # ── org_id in Person-Body ─────────────────────────────────────────────
        person_body = calls_recorded[1][1]
        if person_body.get("org_id") != _ORG_ID:
            _fail("Mock-Push: org_id fehlt im Person-Body", str(person_body))

        # ── person_id + org_id in Deal-Body ───────────────────────────────────
        deal_body = calls_recorded[2][1]
        if deal_body.get("person_id") != _PERSON_ID:
            _fail("Mock-Push: person_id fehlt im Deal-Body", str(deal_body))
        if deal_body.get("org_id") != _ORG_ID:
            _fail("Mock-Push: org_id fehlt im Deal-Body", str(deal_body))

        # ── deal_id in Note-Body ──────────────────────────────────────────────
        note_body = calls_recorded[3][1]
        if note_body.get("deal_id") != _DEAL_ID:
            _fail("Mock-Push: deal_id fehlt im Note-Body", str(note_body))

        _ok(f"Mock-Push: Org({_ORG_ID}) → Person({_PERSON_ID}) → Deal({_DEAL_ID}) → Note OK")

    finally:
        for k in ("CRM_PUSH_CONFIRMED", "PIPEDRIVE_API_TOKEN", "CRM_PROVIDER"):
            os.environ.pop(k, None)
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


def test_08_no_network_without_company_name() -> None:
    """Ohne company_name: Organization-Call wird nicht gemacht, aber Person + Deal + Note laufen."""
    import modules.crm_push as crm

    payload_no_company = _push_ready_payload("nocompany@test.de")
    payload_no_company["company_name"] = ""  # Firma leer

    env_backup = {k: os.environ.pop(k, None) for k in
                  ("CRM_PUSH_CONFIRMED", "PIPEDRIVE_API_TOKEN", "CRM_PROVIDER")}
    os.environ["CRM_PUSH_CONFIRMED"] = "1"
    os.environ["PIPEDRIVE_API_TOKEN"] = "smoke-test-token"
    os.environ["CRM_PROVIDER"] = "pipedrive"

    try:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            preview_file = _make_preview([payload_no_company], tmp)
            push_log_file = tmp / "crm_push_log.json"

            calls_recorded: list[str] = []

            def _mock_post(endpoint: str, token: str, body: dict) -> dict:
                calls_recorded.append(endpoint)
                return _mock_pipedrive_post(endpoint, token, body)

            with patch.object(crm, "_pipedrive_post", side_effect=_mock_post):
                result = crm.run_crm_push(
                    preview_file=preview_file,
                    push_log_file=push_log_file,
                )

        r = result["results"][0]
        if "organizations" in calls_recorded:
            _fail("Ohne company_name: organizations-Call trotzdem gemacht", str(calls_recorded))
        if r.get("org_id") is not None:
            _fail("Ohne company_name: org_id sollte None sein", str(r.get("org_id")))
        if r.get("person_id") != _PERSON_ID:
            _fail("Ohne company_name: person_id fehlt", str(r.get("person_id")))
        if r.get("deal_id") != _DEAL_ID:
            _fail("Ohne company_name: deal_id fehlt", str(r.get("deal_id")))
        _ok("Ohne company_name: kein Org-Call, Person + Deal + Note OK")

    finally:
        for k in ("CRM_PUSH_CONFIRMED", "PIPEDRIVE_API_TOKEN", "CRM_PROVIDER"):
            os.environ.pop(k, None)
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v


def test_09_push_log_in_temp() -> None:
    """crm_push_log.json wird nur im angegebenen Temp-Dir geschrieben."""
    import modules.crm_push as crm

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        preview_file = _make_preview([_blocked_payload()], tmp)
        push_log_file = tmp / "crm_push_log.json"

        crm.run_crm_push(
            preview_file=preview_file,
            push_log_file=push_log_file,
            force_dry_run=True,
        )

        if not push_log_file.is_file():
            _fail("Push-Log: crm_push_log.json nicht geschrieben")
        log = json.loads(push_log_file.read_text(encoding="utf-8"))
        if "summary" not in log or "results" not in log:
            _fail("Push-Log: fehlendes summary/results", str(log.keys()))

    # Datei ist nach TemporaryDirectory-Kontext weg — produktive Dirs unberührt
    _ok("Push-Log: crm_push_log.json nur in Temp-Dir geschrieben")


def test_10_mine_help_contains_crm_push() -> None:
    """python mine.py --help muss --crm-push enthalten."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "mine.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(ROOT),
    )
    if "--crm-push" not in result.stdout:
        _fail(
            "mine.py --help: --crm-push fehlt",
            f"stdout[:500]={result.stdout[:500]}",
        )
    _ok("mine.py --help: --crm-push vorhanden")


def test_11_force_dry_run_blocks_all_network() -> None:
    """force_dry_run=True → push_ready-Payload wird dry_run_skipped, kein API-Call."""
    import modules.crm_push as crm

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        preview_file = _make_preview([_push_ready_payload()], tmp)
        push_log_file = tmp / "crm_push_log.json"

        with patch.object(crm, "_pipedrive_post", side_effect=AssertionError("Netzwerk verboten!")):
            result = crm.run_crm_push(
                preview_file=preview_file,
                push_log_file=push_log_file,
                force_dry_run=True,
            )

        r = result["results"][0]
        if r.get("status") != "dry_run_skipped":
            _fail("force_dry_run: push_ready-Payload nicht als dry_run_skipped", r.get("status"))
        if result["summary"].get("pushed") != 0:
            _fail("force_dry_run: pushed != 0", str(result["summary"]))
        _ok("force_dry_run=True: push_ready-Payload → dry_run_skipped, kein Netzwerk")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_01_import_clean,
    test_02_guard_no_confirmed,
    test_03_guard_no_token,
    test_04_guard_wrong_provider,
    test_05_blocked_payload_skipped,
    test_06_review_required_never_pushed,
    test_07_push_ready_mock_full_flow,
    test_08_no_network_without_company_name,
    test_09_push_log_in_temp,
    test_10_mine_help_contains_crm_push,
    test_11_force_dry_run_blocks_all_network,
]


def main() -> None:
    print("=" * 65)
    print("  smoke_crm_push.py — CRM Push v1 Smoke-Test")
    print("  Kein echter API-Call. Kein SMTP. Kein IMAP.")
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
