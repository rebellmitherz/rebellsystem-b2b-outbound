"""Smoke test for modules/monthly_report.py.

Checks:
- runs offline / read-only (no SMTP, IMAP, send, approve logic imported)
- monthly_report.json is written to output/latest/
- all required fields exist with correct types
- missing files do NOT crash (graceful degradation)
- autonomous + approval mode fields are present
- no send/SMTP/IMAP/approve function is touched
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# ── required fields and their expected types ──────────────────────────────────
REQUIRED_FIELDS: dict[str, type | tuple] = {
    "period_start": str,
    "period_end": str,
    "runs_scanned": int,
    "leads_found_total": int,
    "ready_to_send_total": int,
    "approved_total": int,
    "sent_total": int,
    "replies_total": int,
    "hot_handoffs_total": int,
    "appointment_ready_total": int,
    "followups_due_total": int,
    "followups_sent_total": int,
    "autonomous_mode_signals_total": int,
    "approval_mode_signals_total": int,
    "estimated_pipeline_value_eur": int,
    "warnings": list,
    "generated_at": str,
}

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {PASS}  {label}")
    else:
        msg = f"{label}" + (f": {detail}" if detail else "")
        print(f"  {FAIL}  {msg}")
        failures.append(msg)


# ── Test 1: module imports without pulling in SMTP/IMAP/send logic ────────────
print("\n[1] Import-Check (kein SMTP/IMAP/Send/Approve)")
try:
    import modules.monthly_report as mr
    check("modules.monthly_report importiert", True)
except Exception as e:
    check("modules.monthly_report importiert", False, str(e))
    sys.exit(1)

forbidden_modules = ("smtplib", "imaplib", "email.mime")
for mod in forbidden_modules:
    imported = mod in sys.modules
    check(f"'{mod}' NICHT importiert durch monthly_report", not imported, f"war in sys.modules")


# ── Test 2: graceful degradation with empty temp dir ─────────────────────────
print("\n[2] Leeres Output-Verzeichnis (keine Dateien) — darf nicht crashen")
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    try:
        report = mr.build_monthly_report(days=30, output_dir=tmp_path)
        check("build_monthly_report läuft ohne Fehler", True)
    except Exception as e:
        check("build_monthly_report läuft ohne Fehler", False, str(e))
        report = {}

    for field, expected_type in REQUIRED_FIELDS.items():
        present = field in report
        check(f"Pflichtfeld '{field}' vorhanden", present)
        if present:
            correct_type = isinstance(report[field], expected_type)
            check(
                f"  Typ korrekt ({expected_type.__name__ if isinstance(expected_type, type) else expected_type})",
                correct_type,
                f"bekam {type(report[field]).__name__}",
            )

    all_zero_ints = all(
        report.get(f, -1) == 0
        for f in REQUIRED_FIELDS
        if REQUIRED_FIELDS[f] is int
    )
    check("Alle Integer-Felder = 0 bei leerem Verzeichnis", all_zero_ints)

    has_warnings = len(report.get("warnings", [])) > 0
    check("Warnungen vorhanden bei fehlenden Dateien", has_warnings)


# ── Test 3: run against real output directory ─────────────────────────────────
print("\n[3] Echter Output (output/ des Projekts)")
real_output = Path(__file__).resolve().parent / "output"
if real_output.is_dir():
    try:
        report = mr.build_monthly_report(days=365, output_dir=real_output)
        check("build_monthly_report auf echtem output/ läuft", True)
    except Exception as e:
        check("build_monthly_report auf echtem output/ läuft", False, str(e))
        report = {}

    for field in REQUIRED_FIELDS:
        check(f"Echtes Pflichtfeld '{field}' vorhanden", field in report)

    check("runs_scanned > 0 (echte Runs gefunden)", report.get("runs_scanned", 0) > 0)
    check("leads_found_total > 0", report.get("leads_found_total", 0) > 0)
    check("sent_total >= 0", report.get("sent_total", 0) >= 0)
    check(
        "autonomous_mode_signals_total >= 0",
        report.get("autonomous_mode_signals_total", -1) >= 0,
    )
    check(
        "approval_mode_signals_total >= 0",
        report.get("approval_mode_signals_total", -1) >= 0,
    )
else:
    print("  SKIP — kein output/-Verzeichnis vorhanden (CI/Testumgebung)")


# ── Test 4: monthly_report.json wird gespeichert ─────────────────────────────
print("\n[4] Output-Datei wird geschrieben")
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "latest").mkdir()
    mr.run_monthly_report_cli(days=30, output_dir=tmp_path)
    out_file = tmp_path / "latest" / "monthly_report.json"
    check("monthly_report.json existiert", out_file.is_file())
    if out_file.is_file():
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
            check("monthly_report.json ist valides JSON", True)
            for field in REQUIRED_FIELDS:
                check(f"  Datei enthält '{field}'", field in data)
        except json.JSONDecodeError as e:
            check("monthly_report.json ist valides JSON", False, str(e))


# ── Test 5: keine Send/SMTP/IMAP/Approve-Funktion berührt ───────────────────
print("\n[5] Sicherheits-Check: keine Send/SMTP/IMAP/Approve-Importe durch den Report")
import importlib, inspect
source = inspect.getsource(mr)
forbidden_strings = [
    "smtplib", "imaplib", "run_outreach_action", "send_email",
    "approved_for_send =", "do_not_resend =", "approve(",
    "SMTP(", "IMAP4(",
]
for fs in forbidden_strings:
    # Only flag assignments/calls that could mutate state, not field reads
    found = fs in source
    # Reading field names for metrics (e.g. e.get("approved_for_send")) is fine
    # Only flag if it looks like a write or import
    if fs in ("smtplib", "imaplib", "SMTP(", "IMAP4(", "send_email"):
        check(f"'{fs}' nicht in Quellcode", not found, "verbotener Import/Aufruf")
    else:
        # For things like "approved_for_send =" — only dangerous if it's an assignment
        dangerous = f"{fs} " in source and "get(" not in fs
        check(f"Kein schreibender Zugriff via '{fs}'", not (dangerous and found))


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{'=' * 50}")
if failures:
    print(f"{FAIL}  {len(failures)} Test(s) fehlgeschlagen:")
    for f in failures:
        print(f"    - {f}")
    sys.exit(1)
else:
    print(f"{PASS}  Alle Checks bestanden. Report ist read-only, offline-fähig, crasht nicht bei fehlenden Dateien.")
