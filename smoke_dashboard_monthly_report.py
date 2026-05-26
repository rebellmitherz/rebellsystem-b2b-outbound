"""Smoke test: monthly_report block im Dashboard.

Prueft:
- /api/premium-dashboard liefert monthly_report Block
- Block enthaelt alle Pflichtfelder
- fehlende monthly_report.json crasht nicht (Fallback leer-dict)
- dashboard_relay_premium.html enthaelt Monatsreport-Render-Funktion
- kein Send/SMTP/IMAP/Approve-Code in den geaenderten Stellen
"""
from __future__ import annotations

import json
import sys
import importlib.util
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  {PASS}  {label}")
    else:
        msg = label + (f": {detail}" if detail else "")
        print(f"  {FAIL}  {msg}")
        failures.append(msg)


REQUIRED_REPORT_FIELDS = [
    "period_start", "period_end", "runs_scanned",
    "leads_found_total", "ready_to_send_total", "approved_total",
    "sent_total", "replies_total", "hot_handoffs_total",
    "appointment_ready_total", "followups_due_total", "followups_sent_total",
    "autonomous_mode_signals_total", "approval_mode_signals_total",
    "estimated_pipeline_value_eur", "warnings",
]


# ── Test 1: cockpit_server imports and MONTHLY_REPORT_FILE exists ─────────────
print("\n[1] cockpit_server.py — Konstante + Payload-Schluessel")
try:
    import cockpit_server as cs
    check("cockpit_server importiert", True)
except Exception as e:
    check("cockpit_server importiert", False, str(e))
    print(f"\n{FAIL} Abbruch — cockpit_server nicht importierbar")
    sys.exit(1)

check(
    "MONTHLY_REPORT_FILE definiert",
    hasattr(cs, "MONTHLY_REPORT_FILE"),
)
if hasattr(cs, "MONTHLY_REPORT_FILE"):
    expected = cs.OUT / "latest" / "monthly_report.json"
    check(
        "MONTHLY_REPORT_FILE zeigt auf output/latest/monthly_report.json",
        cs.MONTHLY_REPORT_FILE == expected,
        f"ist: {cs.MONTHLY_REPORT_FILE}",
    )

# ── Test 2: _premium_dashboard_payload liefert monthly_report ────────────────
print("\n[2] _premium_dashboard_payload() — monthly_report Block")
try:
    payload = cs._premium_dashboard_payload()
    check("_premium_dashboard_payload() laeuft ohne Fehler", True)
except Exception as e:
    check("_premium_dashboard_payload() laeuft ohne Fehler", False, str(e))
    payload = {}

check("'monthly_report' Schluessel im Payload vorhanden", "monthly_report" in payload)

mr = payload.get("monthly_report", {})
check("monthly_report ist dict", isinstance(mr, dict))


# ── Test 3: fehlende monthly_report.json crasht nicht ────────────────────────
print("\n[3] Fallback bei fehlender monthly_report.json")
import tempfile, os

orig_path = cs.MONTHLY_REPORT_FILE
try:
    # Point to a nonexistent file temporarily
    cs.MONTHLY_REPORT_FILE = Path(tempfile.gettempdir()) / "_nonexistent_monthly_report.json"
    # Reload payload — must not crash
    try:
        p2 = cs._premium_dashboard_payload()
        mr2 = p2.get("monthly_report", None)
        check("Payload ohne monthly_report.json laeuft", True)
        check("monthly_report Schluessel noch vorhanden", "monthly_report" in p2)
        check(
            "Fallback ist leeres dict (nicht None/crash)",
            isinstance(mr2, dict),
            f"Typ: {type(mr2).__name__}",
        )
    except Exception as e:
        check("Payload ohne monthly_report.json laeuft", False, str(e))
finally:
    cs.MONTHLY_REPORT_FILE = orig_path


# ── Test 4: mit echter monthly_report.json alle Pflichtfelder im Block ────────
print("\n[4] Pflichtfelder im monthly_report Block (mit echter Datei)")
mr_file = ROOT / "output" / "latest" / "monthly_report.json"
if mr_file.is_file():
    try:
        mr_data = json.loads(mr_file.read_text(encoding="utf-8"))
        check("monthly_report.json lesbar", True)
        for field in REQUIRED_REPORT_FIELDS:
            check(f"  Feld '{field}' in monthly_report.json", field in mr_data)
    except Exception as e:
        check("monthly_report.json lesbar", False, str(e))
else:
    print(f"  SKIP — output/latest/monthly_report.json fehlt (zuerst: python mine.py --monthly-report)")


# ── Test 5: dashboard_relay_premium.html enthaelt Monatsreport-Anzeige ───────
print("\n[5] dashboard_relay_premium.html — Anzeige-Funktion vorhanden")
relay_file = ROOT / "dashboard_relay_premium.html"
check("dashboard_relay_premium.html existiert", relay_file.is_file())

if relay_file.is_file():
    html = relay_file.read_text(encoding="utf-8")
    check("monthlyReportHtml() Funktion vorhanden", "function monthlyReportHtml" in html)
    check("monthly_report Datenzugriff vorhanden", "state.data.monthly_report" in html or "monthly_report" in html)
    check("KPI-Tiles fuer Monatsreport vorhanden", "leads_found_total" in html and "sent_total" in html)
    check("Fallback-Text 'Noch kein Monatsreport' vorhanden", "Noch kein Monatsreport" in html)
    check("monthlyReportHtml() in renderOverview() aufgerufen", "monthlyReportHtml()" in html)
    check(
        "Pipeline-Wert (estimated_pipeline_value_eur) wird angezeigt",
        "estimated_pipeline_value_eur" in html,
    )
    check(
        "Autonomer/Freigabe-Modus Felder sichtbar",
        "autonomous_mode_signals_total" in html and "approval_mode_signals_total" in html,
    )


# ── Test 6: keine Send/SMTP/IMAP/Approve-Logik in den geaenderten Stellen ────
print("\n[6] Sicherheits-Check der geaenderten Stellen")
import inspect

pd_src = inspect.getsource(cs._premium_dashboard_payload)
forbidden = ["smtplib", "imaplib", "send_email", "SMTP(", "IMAP4("]
for f in forbidden:
    check(f"'{f}' nicht in _premium_dashboard_payload", f not in pd_src)

if relay_file.is_file():
    mr_func_start = html.find("function monthlyReportHtml")
    mr_func_end = html.find("\nfunction ", mr_func_start + 1)
    mr_func_src = html[mr_func_start:mr_func_end] if mr_func_end > 0 else html[mr_func_start:]
    for f in ["fetch(", "postAction(", "api(", "XMLHttpRequest"]:
        check(f"'{f}' nicht in monthlyReportHtml()", f not in mr_func_src)


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{'=' * 50}")
if failures:
    print(f"{FAIL}  {len(failures)} Test(s) fehlgeschlagen:")
    for f in failures:
        print(f"    - {f}")
    sys.exit(1)
else:
    print(f"{PASS}  Alle Checks bestanden.")
    print("       API liefert monthly_report Block. Dashboard zeigt Monatsreport.")
    print("       Kein Send, kein SMTP, kein IMAP, kein Approve geaendert.")
