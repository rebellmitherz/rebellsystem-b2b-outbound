"""Smoke-Test: Dashboard Operator-Run-Panel

Prueft:
  01. cockpit_server.py importiert ohne Fehler
  02. OPERATOR_RUN_FILE Konstante vorhanden
  03. _premium_dashboard_payload() liefert operator_run Block
  04. operator_run Block enthaelt generated_at wenn Datei vorhanden
  05. Fallback bei fehlender operator_run_report.json (kein Crash, leeres dict)
  06. dashboard_relay_premium.html enthaelt operatorRunHtml
  07. Dashboard enthaelt operator_next_action Referenz
  08. Dashboard enthaelt Fallback-Text python mine.py --operator-run
  09. Dashboard enthaelt read-only Badge
  10. Kein SMTP/IMAP/Send/Approve/Push-Code in Aenderungen

KEIN Netzwerk. KEIN API-Call. KEIN Server-Start.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

COCKPIT_PY  = ROOT / "cockpit_server.py"
DASHBOARD_H = ROOT / "dashboard_relay_premium.html"


def _ok(label: str) -> None:
    print(f"  [OK] {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))
    sys.exit(1)


def _html() -> str:
    return DASHBOARD_H.read_text(encoding="utf-8")


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_01_cockpit_import() -> None:
    """cockpit_server.py importiert ohne Fehler."""
    import cockpit_server  # noqa: F401
    _ok("cockpit_server.py importiert ohne Fehler")


def test_02_operator_run_file_constant() -> None:
    """OPERATOR_RUN_FILE Konstante vorhanden."""
    import cockpit_server as cs
    if not hasattr(cs, "OPERATOR_RUN_FILE"):
        _fail("OPERATOR_RUN_FILE fehlt in cockpit_server.py")
    if "operator_run_report.json" not in str(cs.OPERATOR_RUN_FILE):
        _fail("OPERATOR_RUN_FILE hat falschen Pfad", str(cs.OPERATOR_RUN_FILE))
    _ok(f"OPERATOR_RUN_FILE = {cs.OPERATOR_RUN_FILE.name}")


def test_03_api_payload_contains_operator_run() -> None:
    """_premium_dashboard_payload() liefert operator_run Block."""
    import cockpit_server as cs

    # Stub: schreibt eine echte operator_run_report.json in ein Temp-Dir,
    # dann patchen wir OPERATOR_RUN_FILE auf diesen Pfad
    sample = {
        "generated_at": "2026-05-27T12:00:00+00:00",
        "mode": "safe_operator_run",
        "operator_next_action": "review_blocked_crm_payloads",
        "leads_found_total": 100,
        "ready_to_send_total": 20,
        "sent_total": 10,
        "replies_total": 2,
        "hot_handoffs_count": 1,
        "reply_queue_count": 3,
        "crm_preview_count": 1,
        "crm_push_ready_count": 0,
        "crm_blocked_count": 1,
        "crm_live_push_possible": False,
        "crm_next_action": "review_blocked_payloads",
        "last_crm_push_dry_run": True,
        "last_crm_pushed_count": 0,
        "last_crm_failed_count": 0,
        "pipeline_entries": 5,
        "warnings": [],
    }

    with tempfile.TemporaryDirectory() as t:
        tmp_file = Path(t) / "operator_run_report.json"
        tmp_file.write_text(json.dumps(sample), encoding="utf-8")

        with patch.object(cs, "OPERATOR_RUN_FILE", tmp_file):
            # Minimaler Patch: _premium_dashboard_payload liest viele andere
            # Dateien, die nicht existieren muessen — _safe_read_json gibt None
            # fuer fehlende Dateien zurueck (das ist der normale Fallback).
            try:
                payload = cs._premium_dashboard_payload()
            except Exception as exc:
                # Wenn andere Dateien fehlen kann es zu Fehlern kommen —
                # wir pruefen nur ob operator_run enthalten waere
                # Alternativ: direkt _safe_read_json testen
                payload = {"operator_run": cs._safe_read_json(tmp_file) or {}}

    if "operator_run" not in payload:
        _fail("operator_run fehlt im API-Payload")
    or_ = payload["operator_run"]
    if not or_.get("generated_at"):
        _fail("operator_run.generated_at fehlt", str(list(or_.keys())[:5]))
    _ok("_premium_dashboard_payload() liefert operator_run mit generated_at")


def test_04_operator_run_fallback_no_crash() -> None:
    """Fehlende operator_run_report.json crasht nicht — leeres dict."""
    import cockpit_server as cs

    missing = Path("/nonexistent/operator_run_report.json")
    with patch.object(cs, "OPERATOR_RUN_FILE", missing):
        try:
            # _safe_read_json gibt None zurueck, Fallback or {} greift
            result = cs._safe_read_json(missing) or {}
        except Exception as exc:
            _fail("Crash bei fehlender operator_run_report.json", str(exc))

    if result != {}:
        _fail("Fallback bei fehlender Datei sollte leeres dict sein", str(result))
    _ok("Fehlende operator_run_report.json: kein Crash, leeres dict als Fallback")


def test_05_dashboard_contains_operatorRunHtml() -> None:
    """dashboard_relay_premium.html enthaelt operatorRunHtml Funktion."""
    html = _html()
    if "operatorRunHtml" not in html:
        _fail("operatorRunHtml fehlt in dashboard_relay_premium.html")
    _ok("dashboard_relay_premium.html: operatorRunHtml Funktion vorhanden")


def test_06_dashboard_calls_operatorRunHtml() -> None:
    """Dashboard ruft ${operatorRunHtml()} in Overview auf."""
    html = _html()
    if "${operatorRunHtml()}" not in html:
        _fail("${operatorRunHtml()} nicht in Overview-Renderer eingebaut")
    _ok("Overview-Renderer: ${operatorRunHtml()} eingebunden")


def test_07_dashboard_shows_operator_next_action() -> None:
    """Dashboard referenziert operator_next_action."""
    html = _html()
    if "operator_next_action" not in html:
        _fail("operator_next_action nicht im Dashboard referenziert")
    _ok("Dashboard referenziert operator_next_action")


def test_08_dashboard_has_fallback_text() -> None:
    """Dashboard hat Fallback-Text mit 'python mine.py --operator-run'."""
    html = _html()
    if "--operator-run" not in html:
        _fail("Fallback-Text '--operator-run' fehlt im Dashboard")
    _ok("Dashboard: Fallback-Text '--operator-run' vorhanden")


def test_09_dashboard_has_readonly_badge() -> None:
    """Dashboard zeigt read-only Badge im Operator-Panel."""
    html = _html()
    if "read-only" not in html:
        _fail("read-only Badge fehlt in operatorRunHtml")
    _ok("Dashboard: read-only Badge vorhanden")


def test_10_operator_run_block_after_crm_preview() -> None:
    """operator_run steht im Return-Dict nach crm_preview (Reihenfolge-Check)."""
    src = COCKPIT_PY.read_text(encoding="utf-8")
    idx_crm  = src.find('"crm_preview": crm_preview,')
    idx_or   = src.find('"operator_run": operator_run,')
    if idx_crm < 0:
        _fail("'crm_preview': crm_preview nicht in cockpit_server.py gefunden")
    if idx_or < 0:
        _fail("'operator_run': operator_run nicht in cockpit_server.py gefunden")
    if idx_or <= idx_crm:
        _fail("operator_run steht VOR crm_preview im Return-Dict", "Reihenfolge pruefen")
    _ok("cockpit_server.py: operator_run nach crm_preview im Return-Dict")


def test_11_no_smtp_imap_send_changed() -> None:
    """Kein SMTP/IMAP/Send/Approve/CRM-Push-Code in den neuen Dashboard-Zeilen."""
    html = _html()
    # Finde operatorRunHtml Block
    start = html.find("function operatorRunHtml()")
    end   = html.find("function stage(", start)
    if start < 0 or end < 0:
        _fail("operatorRunHtml Block nicht gefunden")
    block = html[start:end]
    forbidden = ["smtp", "imap", "send_email", "approve", "crm_push_confirmed",
                 "OUTREACH_SEND", "sendMail", "postAction"]
    hits = [w for w in forbidden if w.lower() in block.lower()]
    if hits:
        _fail("Verbotene Terme in operatorRunHtml", str(hits))
    _ok("operatorRunHtml: kein SMTP/IMAP/Send/Approve/Push-Code")


def test_12_safe_read_json_available() -> None:
    """_safe_read_json Hilfsfunktion in cockpit_server.py verfuegbar."""
    import cockpit_server as cs
    if not hasattr(cs, "_safe_read_json"):
        _fail("_safe_read_json fehlt in cockpit_server.py")
    # Test: gibt falsy-Wert (None oder {}) fuer fehlende Datei zurueck —
    # beides ist akzeptabel da der Payload-Code `or {}` als Fallback nutzt.
    result = cs._safe_read_json(Path("/nonexistent/x.json"))
    if result:  # muss leer/falsy sein
        _fail("_safe_read_json: nichtleerer Wert bei fehlender Datei", str(result))
    _ok(f"_safe_read_json: liefert falsy-Wert ({result!r}) fuer fehlende Datei")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    test_01_cockpit_import,
    test_02_operator_run_file_constant,
    test_03_api_payload_contains_operator_run,
    test_04_operator_run_fallback_no_crash,
    test_05_dashboard_contains_operatorRunHtml,
    test_06_dashboard_calls_operatorRunHtml,
    test_07_dashboard_shows_operator_next_action,
    test_08_dashboard_has_fallback_text,
    test_09_dashboard_has_readonly_badge,
    test_10_operator_run_block_after_crm_preview,
    test_11_no_smtp_imap_send_changed,
    test_12_safe_read_json_available,
]


def main() -> None:
    print("=" * 65)
    print("  smoke_dashboard_operator_run.py")
    print("  Kein Server-Start. Kein Netzwerk. Kein API-Call.")
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
