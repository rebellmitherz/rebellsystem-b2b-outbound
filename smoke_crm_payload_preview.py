"""Smoke test for modules/crm_payload_preview.py.

Prueft:
- laeuft offline/read-only (keine Netzwerk-/API-Calls)
- crm_payload_preview.json wird erzeugt
- kein requests/httpx/smtplib/imaplib importiert
- fehlende hot_handoffs.json crasht nicht
- Pflichtfelder in jedem Payload vorhanden
- dry_run ist immer True
- provider ist "generic"
- proposed_stage wird korrekt gemappt
- kein Send/SMTP/IMAP/Approve-Code in den geaenderten Stellen
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

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


PAYLOAD_REQUIRED_FIELDS = [
    "dry_run", "provider", "crm_object_type",
    "company_name", "contact_name", "email", "phone", "website",
    "subject", "reply_snippet", "source", "reply_class", "confidence",
    "proposed_stage", "proposed_action", "estimated_value_eur",
    "next_step", "owner_note", "created_at",
]

RESULT_REQUIRED_FIELDS = ["dry_run", "provider", "generated_at", "count", "payloads", "warnings"]


# ── Test 1: Import ohne Netzwerk/SMTP/IMAP ────────────────────────────────────
print("\n[1] Import-Check (keine Netz/SMTP/IMAP-Module)")
try:
    import modules.crm_payload_preview as crm
    check("modules.crm_payload_preview importiert", True)
except Exception as e:
    check("modules.crm_payload_preview importiert", False, str(e))
    sys.exit(1)

FORBIDDEN_MODULES = ("smtplib", "imaplib", "requests", "httpx", "urllib3",
                     "http.client", "socket")
for mod in FORBIDDEN_MODULES:
    in_sys = mod in sys.modules
    check(f"'{mod}' NICHT durch crm_payload_preview importiert", not in_sys)


# ── Test 2: leeres Verzeichnis — kein Crash ───────────────────────────────────
print("\n[2] Leeres Output-Verzeichnis — darf nicht crashen")
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    try:
        result = crm.build_crm_preview(output_dir=tmp_path)
        check("build_crm_preview laeuft ohne Fehler", True)
    except Exception as e:
        check("build_crm_preview laeuft ohne Fehler", False, str(e))
        result = {}

    for f in RESULT_REQUIRED_FIELDS:
        check(f"Ergebnis-Feld '{f}' vorhanden", f in result)

    check("payloads ist leere Liste bei fehlendem input", result.get("payloads") == [])
    check("count == 0 bei fehlendem input", result.get("count") == 0)
    check("dry_run == True", result.get("dry_run") is True)
    check("provider == 'generic'", result.get("provider") == "generic")
    check("Warnungen vorhanden", len(result.get("warnings", [])) > 0)


# ── Test 3: proposed_stage Logik ─────────────────────────────────────────────
print("\n[3] proposed_stage Mapping")

stage_cases = [
    ({"appointment_ready": True, "inbound_class": "positive"}, "appointment_ready"),
    ({"appointment_ready": False, "inbound_class": "interested"}, "hot_lead"),
    ({"appointment_ready": False, "inbound_class": "positive"}, "qualified_interest"),
    ({"appointment_ready": False, "inbound_class": "neutral"}, "review_required"),
    ({"appointment_ready": False, "inbound_class": "negative"}, "review_required"),
    ({"appointment_ready": False, "inbound_class": "", "why_hot": "heisser lead hot"}, "hot_lead"),
]

for hh, expected in stage_cases:
    result_stage = crm._proposed_stage(hh)
    check(
        f"Stage fuer {hh} == '{expected}'",
        result_stage == expected,
        f"bekam '{result_stage}'",
    )


# ── Test 4: estimated_value_eur ───────────────────────────────────────────────
print("\n[4] estimated_value_eur")
check("positive -> 5000",    crm._estimated_value({"inbound_class": "positive"}) == 5000)
check("interested -> 5000",  crm._estimated_value({"inbound_class": "interested"}) == 5000)
check("negative -> 0",       crm._estimated_value({"inbound_class": "negative"}) == 0)
check("leer -> 5000",        crm._estimated_value({}) == 5000)


# ── Test 5: echter Output (falls hot_handoffs.json vorhanden) ─────────────────
print("\n[5] Echter Output")
real_output = ROOT / "output"
if (real_output / "latest" / "hot_handoffs.json").is_file():
    try:
        result = crm.build_crm_preview(output_dir=real_output)
        check("build_crm_preview auf echtem Output laeuft", True)
    except Exception as e:
        check("build_crm_preview auf echtem Output laeuft", False, str(e))
        result = {}

    check("count >= 0", result.get("count", -1) >= 0)
    check("dry_run == True", result.get("dry_run") is True)
    check("provider == 'generic'", result.get("provider") == "generic")

    for payload in result.get("payloads", []):
        for f in PAYLOAD_REQUIRED_FIELDS:
            check(f"  Payload-Feld '{f}' vorhanden", f in payload)
        check("  dry_run == True in Payload", payload.get("dry_run") is True)
        check("  provider == 'generic' in Payload", payload.get("provider") == "generic")
        check(
            "  proposed_stage ist gueltiger Wert",
            payload.get("proposed_stage") in (
                "appointment_ready", "hot_lead", "qualified_interest", "review_required"
            ),
        )
        check(
            "  estimated_value_eur ist int",
            isinstance(payload.get("estimated_value_eur"), int),
        )
else:
    print("  SKIP — output/latest/hot_handoffs.json fehlt")


# ── Test 6: crm_payload_preview.json wird gespeichert ────────────────────────
print("\n[6] Datei wird geschrieben")
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "latest").mkdir()
    crm.run_crm_preview_cli(output_dir=tmp_path)
    out_file = tmp_path / "latest" / "crm_payload_preview.json"
    check("crm_payload_preview.json existiert", out_file.is_file())
    if out_file.is_file():
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
            check("crm_payload_preview.json ist valides JSON", True)
            check("dry_run == True in Datei", data.get("dry_run") is True)
            for f in RESULT_REQUIRED_FIELDS:
                check(f"  Datei enthaelt '{f}'", f in data)
        except json.JSONDecodeError as e:
            check("crm_payload_preview.json ist valides JSON", False, str(e))


# ── Test 7: kein Send/SMTP/IMAP/Approve/Netzwerk im Quellcode ────────────────
print("\n[7] Sicherheits-Check Quellcode")
import inspect

src = inspect.getsource(crm)
forbidden_src = [
    "smtplib", "imaplib", "requests.get", "requests.post",
    "httpx", "http.client", "socket.connect",
    "run_outreach_action", "send_email", "SMTP(", "IMAP4(",
    "approved_for_send =", "do_not_resend =",
]
for fs in forbidden_src:
    check(f"'{fs}' nicht im Quellcode", fs not in src)


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{'=' * 50}")
if failures:
    print(f"{FAIL}  {len(failures)} Test(s) fehlgeschlagen:")
    for f in failures:
        print(f"    - {f}")
    sys.exit(1)
else:
    print(f"{PASS}  Alle Checks bestanden.")
    print("       CRM-Preview ist read-only, offline, crasht nicht bei fehlenden Dateien.")
    print("       dry_run=True. Kein CRM-Push. Kein Send. Kein SMTP. Kein IMAP.")
