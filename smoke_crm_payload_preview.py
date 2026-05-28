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
    "crm_push_ready", "crm_push_block_reason", "crm_push_mode",
]

RESULT_REQUIRED_FIELDS = [
    "dry_run", "provider", "generated_at", "count", "payloads", "warnings",
    "push_ready_count", "blocked_count", "blocked_reasons",
    "excluded_count", "excluded_reasons", "excluded_payloads",
]


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


# ── Test 4b: _has_rejection_phrase ───────────────────────────────────────────
print("\n[4b] _has_rejection_phrase")
check("'aktuell keinen Bedarf' erkannt", crm._has_rejection_phrase("Wir haben aktuell keinen Bedarf daran."))
check("'kein bedarf' erkannt",           crm._has_rejection_phrase("Es besteht kein Bedarf."))
check("'nicht interessiert' erkannt",    crm._has_rejection_phrase("Wir sind nicht interessiert."))
check("'kein interesse' erkannt",        crm._has_rejection_phrase("Kein Interesse, danke."))
check("'behalten Sie gerne im Hinterkopf' erkannt",
      crm._has_rejection_phrase("Behalten Sie gerne im Hinterkopf fuer spaeter."))
check("Leerer Text -> False",            not crm._has_rejection_phrase(""))
check("Positiver Text -> False",         not crm._has_rejection_phrase("Ich bin sehr interessiert!"))
check("case-insensitiv: 'Kein Bedarf'",  crm._has_rejection_phrase("Kein Bedarf"))


# ── Test 4c: Quality-Guard / Exclusion in build_crm_preview ──────────────────
print("\n[4c] Quality-Guard — harte Ablehnungen in excluded_payloads")

import json as _json

# Rejection phrase: geht in excluded_payloads, NICHT in active payloads
rejection_hh = {
    "email": "test@rejection.de",
    "company_name": "Firma GmbH",
    "contact_name": "Max Muster",
    "appointment_ready": True,
    "inbound_class": "positive",
    "last_inbound_snippet": "Vielen Dank, aber wir haben aktuell keinen Bedarf an Ihrer Leistung.",
    "source": "outreach_pipeline",
}

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "latest").mkdir()
    hh_file = tmp_path / "latest" / "hot_handoffs.json"
    hh_file.write_text(_json.dumps([rejection_hh]), encoding="utf-8")
    result_rej = crm.build_crm_preview(output_dir=tmp_path)

check("Ablehnung -> active count == 0 (excluded)",  result_rej.get("count") == 0)
check("Ablehnung -> excluded_count == 1",            result_rej.get("excluded_count") == 1)
check("Ablehnung -> payloads leer",                  result_rej.get("payloads") == [])
rej_excl = result_rej.get("excluded_payloads", [{}])[0]
check("Ablehnung -> exclusion_reason == 'rejection_phrase_detected'",
      rej_excl.get("exclusion_reason") == "rejection_phrase_detected",
      f"bekam '{rej_excl.get('exclusion_reason')}'")
check("Ablehnung -> Warnung vorhanden",              len(result_rej.get("warnings", [])) > 0)

# sent_log_only without company_name: ebenfalls excluded
sentlog_hh = {
    "email": "test@sentlog.de",
    "company_name": "",
    "contact_name": "",
    "appointment_ready": True,
    "inbound_class": "positive",
    "source": "sent_log_only",
    "last_inbound_snippet": "Danke fuer Ihre Nachricht.",
}

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "latest").mkdir()
    hh_file = tmp_path / "latest" / "hot_handoffs.json"
    hh_file.write_text(_json.dumps([sentlog_hh]), encoding="utf-8")
    result_sl = crm.build_crm_preview(output_dir=tmp_path)

check("sent_log_only no company -> active count == 0",   result_sl.get("count") == 0)
check("sent_log_only no company -> excluded_count == 1", result_sl.get("excluded_count") == 1)
sl_excl = result_sl.get("excluded_payloads", [{}])[0]
check("sent_log_only -> proposed_stage == 'review_required' in excluded",
      sl_excl.get("proposed_stage") == "review_required",
      f"bekam '{sl_excl.get('proposed_stage')}'")
check("sent_log_only -> exclusion_reason korrekt",
      sl_excl.get("exclusion_reason") in (
          "sent_log_only_review_required_zero_value",
          "sent_log_only_no_company_zero_value",
      ),
      f"bekam '{sl_excl.get('exclusion_reason')}'")

# Clean lead — bleibt in active payloads (nicht excluded)
clean_hh = {
    "email": "test@clean.de",
    "company_name": "Saubere GmbH",
    "contact_name": "Anna Rein",
    "appointment_ready": True,
    "inbound_class": "positive",
    "source": "outreach_pipeline",
    "last_inbound_snippet": "Ja, wann haben Sie Zeit fuer ein kurzes Gespraech?",
}

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "latest").mkdir()
    hh_file = tmp_path / "latest" / "hot_handoffs.json"
    hh_file.write_text(_json.dumps([clean_hh]), encoding="utf-8")
    result_clean = crm.build_crm_preview(output_dir=tmp_path)

check("Sauberer Lead -> active count == 1",    result_clean.get("count") == 1)
check("Sauberer Lead -> excluded_count == 0",  result_clean.get("excluded_count") == 0)
clean_payload = result_clean["payloads"][0] if result_clean.get("payloads") else {}
check("Sauberer Lead -> proposed_stage == 'appointment_ready'",
      clean_payload.get("proposed_stage") == "appointment_ready",
      f"bekam '{clean_payload.get('proposed_stage')}'")
check("Sauberer Lead -> estimated_value_eur == 5000",
      clean_payload.get("estimated_value_eur") == 5000)
check("Sauberer Lead -> dry_run == True", clean_payload.get("dry_run") is True)


# ── Test 4d: crm_push_readiness ──────────────────────────────────────────────
print("\n[4d] crm_push_readiness — _crm_push_readiness()")

ready, reason = crm._crm_push_readiness("appointment_ready", 5000, "Firma GmbH", "a@b.de", "Ja bitte!", "outreach_pipeline")
check("Sauberer Lead -> push_ready True",  ready is True)
check("Sauberer Lead -> block_reason leer", reason == "")

ready, reason = crm._crm_push_readiness("review_required", 5000, "Firma GmbH", "a@b.de", "Ja bitte!", "outreach_pipeline")
check("review_required -> push_ready False", ready is False)
check("review_required -> block_reason 'review_required'", reason == "review_required")

ready, reason = crm._crm_push_readiness("appointment_ready", 0, "Firma GmbH", "a@b.de", "Ja bitte!", "outreach_pipeline")
check("value 0 -> push_ready False", ready is False)
check("value 0 -> block_reason 'estimated_value_zero'", reason == "estimated_value_zero")

ready, reason = crm._crm_push_readiness("appointment_ready", 5000, "", "a@b.de", "Ja bitte!", "outreach_pipeline")
check("company_name leer -> push_ready False", ready is False)
check("company_name leer -> block_reason 'company_name_missing'", reason == "company_name_missing")

ready, reason = crm._crm_push_readiness("appointment_ready", 5000, "Firma GmbH", "a@b.de", "kein Bedarf leider", "outreach_pipeline")
check("Ablehnung im Snippet -> push_ready False", ready is False)
check("Ablehnung im Snippet -> block_reason 'rejection_phrase_detected'", reason == "rejection_phrase_detected")

ready, reason = crm._crm_push_readiness("hot_lead", 5000, "Firma GmbH", "a@b.de", "Sehr interessiert!", "sent_log_only")
check("sent_log_only -> push_ready False", ready is False)
check("sent_log_only -> block_reason 'sent_log_only_unresolved'", reason == "sent_log_only_unresolved")

ready, reason = crm._crm_push_readiness("hot_lead", 5000, "Firma GmbH", "a@b.de", "Ja sehr gerne!", "outreach_pipeline")
check("hot_lead mit Daten -> push_ready True", ready is True)


# ── Test 4e: Push-Readiness im build_crm_preview Ergebnis ────────────────────
print("\n[4e] Push-Readiness Top-Level Summary")

# Rejection lead -> EXCLUDED (kein blocked, kein active)
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "latest").mkdir()
    (tmp_path / "latest" / "hot_handoffs.json").write_text(
        _json.dumps([rejection_hh]), encoding="utf-8"
    )
    r_rej = crm.build_crm_preview(output_dir=tmp_path)

check("Ablehnung -> push_ready_count == 0",                    r_rej.get("push_ready_count") == 0)
check("Ablehnung -> blocked_count == 0 (excluded, nicht blocked)", r_rej.get("blocked_count") == 0)
check("Ablehnung -> excluded_count == 1",                      r_rej.get("excluded_count") == 1)
check("Ablehnung -> blocked_reasons leer",                     r_rej.get("blocked_reasons") == [])
check("Ablehnung -> payloads leer (entry in excluded_payloads)", r_rej.get("payloads") == [])

# Clean lead -> push_ready
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "latest").mkdir()
    (tmp_path / "latest" / "hot_handoffs.json").write_text(
        _json.dumps([clean_hh]), encoding="utf-8"
    )
    r_clean = crm.build_crm_preview(output_dir=tmp_path)

check("Sauberer Lead -> push_ready_count == 1", r_clean.get("push_ready_count") == 1)
check("Sauberer Lead -> blocked_count == 0",    r_clean.get("blocked_count") == 0)
check("Sauberer Lead -> blocked_reasons leer",  r_clean.get("blocked_reasons") == [])
clean_p = r_clean["payloads"][0] if r_clean.get("payloads") else {}
check("Sauberer Lead Payload -> crm_push_ready True",  clean_p.get("crm_push_ready") is True)
check("Sauberer Lead Payload -> crm_push_block_reason leer", clean_p.get("crm_push_block_reason") == "")
check("Sauberer Lead Payload -> crm_push_mode == 'dry_run_only'", clean_p.get("crm_push_mode") == "dry_run_only")


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
        check("  crm_push_ready ist bool", isinstance(payload.get("crm_push_ready"), bool))
        check("  crm_push_mode == 'dry_run_only'", payload.get("crm_push_mode") == "dry_run_only")
        if not payload.get("crm_push_ready"):
            check("  blocked -> crm_push_block_reason nicht leer", bool(payload.get("crm_push_block_reason")))

    check("push_ready_count + blocked_count == count",
          result.get("push_ready_count", -1) + result.get("blocked_count", -1) == result.get("count", -2))
    check("excluded_count >= 0",
          isinstance(result.get("excluded_count"), int) and result.get("excluded_count", -1) >= 0)
    check("excluded_payloads ist liste",
          isinstance(result.get("excluded_payloads"), list))
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
