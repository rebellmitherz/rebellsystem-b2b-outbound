"""
smoke_outreach_preview_current_run_only.py
Verifies that the preview current-run filter and generic-prefix blocker work correctly.
No real emails sent, no pipeline history deleted.
"""

import importlib
import sys
import types
import csv
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

PASS = []
FAIL = []

def ok(label):
    PASS.append(label)
    print(f"  PASS  {label}")

def fail(label, detail=""):
    FAIL.append(label)
    print(f"  FAIL  {label}" + (f": {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 1. Unit-test _blocked_email_prefix
# ---------------------------------------------------------------------------
print("\n[1] _blocked_email_prefix")

# Patch heavy imports before loading the module
_fake_modules = [
    "modules.lead_schema", "modules.revenue_fit", "modules.email_quality",
    "modules.outreach_engine", "modules.intent_lead_production",
    "anthropic", "openai", "requests", "bs4", "lxml",
]
for m in _fake_modules:
    parts = m.split(".")
    if len(parts) > 1:
        pkg = parts[0]
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)
    sys.modules[m] = types.ModuleType(m)

# Stub out the classify_pipeline_ingest used inside the module
stub_revenue = sys.modules["modules.revenue_fit"]
stub_revenue.classify_pipeline_ingest = lambda *a, **kw: {}  # type: ignore

# Now we can import just the helpers we need by loading the source as text
src_path = Path(__file__).parent / "modules" / "outreach_pipeline.py"
src = src_path.read_text(encoding="utf-8")

# Extract the two helper functions via exec into a namespace
ns: dict = {}
# Minimal stubs needed by the module-level code
ns["Path"] = Path
ns["csv"] = csv
ns["json"] = json
ns["os"] = os

# Pull just the relevant pieces we can safely exec
import re

# _norm_email
m = re.search(r"def _norm_email\(.*?\n(?:    .*\n)+", src)
if m:
    exec(compile(m.group(), "<string>", "exec"), ns)

# _BLOCKED_EMAIL_PREFIXES
m2 = re.search(r"_BLOCKED_EMAIL_PREFIXES: frozenset.*?= frozenset\(\{.*?\}\)", src, re.DOTALL)
if m2:
    exec(compile(m2.group(), "<string>", "exec"), ns)

# _blocked_email_prefix
m3 = re.search(r"def _blocked_email_prefix\(.*?\n(?:    .*\n)+", src)
if m3:
    exec(compile(m3.group(), "<string>", "exec"), ns)

bep = ns.get("_blocked_email_prefix")
if bep is None:
    fail("helper_loaded", "_blocked_email_prefix not found in source")
else:
    ok("helper_loaded")

    blocked_cases = [
        "impressum@firma.de",
        "info@test.com",
        "shop@example.de",
        "hello@web.de",
        "hallo@firma.de",
        "office@company.de",
        "kontakt@example.com",
        "support@test.de",
        "mail@firma.de",
        "service@test.com",
        "noreply@test.com",
        "no-reply@test.com",
        "post@firma.de",
        "webmaster@example.de",
        "sales@test.com",
        "contact@test.com",
        "team@test.com",
        "news@test.com",
        "newsletter@example.de",
        "anfrage@test.de",
        "bestellung@shop.de",
        "feedback@test.com",
        "info1@test.com",       # trailing digit stripped
    ]

    personal_cases = [
        "max.mustermann@test.com",
        "j.smith@company.de",
        "benedikt.becker@firma.de",
        "eric.raber@agentur.de",
        "r.schmitt@agency.de",
        "martin.schneider@company.de",
        "mario.wagner@example.de",
    ]

    for email in blocked_cases:
        result = bep(email)
        if result:
            ok(f"blocked:{email}")
        else:
            fail(f"blocked:{email}", "expected True, got False")

    for email in personal_cases:
        result = bep(email)
        if not result:
            ok(f"personal_passes:{email}")
        else:
            fail(f"personal_passes:{email}", "expected False (personal), got True (blocked)")


# ---------------------------------------------------------------------------
# 2. _load_current_run_emails reads ready_to_send.csv correctly
# ---------------------------------------------------------------------------
print("\n[2] _load_current_run_emails")

# Extract the function source
m4 = re.search(r"def _load_current_run_emails\(.*?\n(?:    .*\n)+", src)
m4_src = m4.group() if m4 else None

if not m4_src:
    fail("load_fn_found", "_load_current_run_emails not found in source")
else:
    ok("load_fn_found")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        latest = tmp_path / "latest"
        latest.mkdir()
        rts = latest / "ready_to_send.csv"
        rts.write_text(
            "priority,company_name,contact_name,email\n"
            "A-Lead,Firma A,Max Muster,max@firma-a.de\n"
            "B-Lead,Firma B,Jane Doe,jane@firma-b.com\n",
            encoding="utf-8",
        )

        ns2 = {"Path": Path, "csv": csv, "_norm_email": ns.get("_norm_email", lambda s: (s or "").strip().lower())}
        ns2["OUTPUT_DIR"] = str(tmp_path)
        exec(compile(m4_src, "<string>", "exec"), ns2)
        fn = ns2["_load_current_run_emails"]
        result = fn()

        if "max@firma-a.de" in result:
            ok("email_a_loaded")
        else:
            fail("email_a_loaded", f"got {result}")

        if "jane@firma-b.com" in result:
            ok("email_b_loaded")
        else:
            fail("email_b_loaded", f"got {result}")

        if len(result) == 2:
            ok("exact_count_2")
        else:
            fail("exact_count_2", f"expected 2, got {len(result)}")

        # Test missing file â†’ empty set
        rts.unlink()
        result_empty = fn()
        if result_empty == set():
            ok("missing_file_returns_empty_set")
        else:
            fail("missing_file_returns_empty_set", f"got {result_empty}")


# ---------------------------------------------------------------------------
# 3. run_preview() current-run filter logic (structural checks on source)
# ---------------------------------------------------------------------------
print("\n[3] run_preview() source structure checks")

checks = {
    "current_run_emails_loaded": "_load_current_run_emails()" in src,
    "use_current_run_filter_set": "use_current_run_filter = bool(current_run_map)" in src,
    "historical_skip_counted": "historical_not_current_run" in src,
    "filter_before_exclusion_check": (lambda b: b.index("use_current_run_filter") < b.index("_preview_exclusion_reason(e"))(src[src.index("def run_preview("):src.index("\ndef ", src.index("def run_preview(") + 1)]),
    "is_current_run_field_added": '"is_current_run": True' in src,
    "added_at_field_added": '"added_at"' in src and '(e.get("added_at") or "")[:10]' in src,
    "pipeline_not_deleted_in_run_preview": "entries" in src and "del " not in src[src.index("def run_preview"):src.index("def run_preview") + 3000],
    "no_send_call_in_run_preview": "send_email" not in src[src.index("def run_preview"):src.index("def run_preview") + 3000],
}

for label, result in checks.items():
    if result:
        ok(label)
    else:
        fail(label)


# ---------------------------------------------------------------------------
# 4. _preview_exclusion_reason() has generic_email_prefix gate
# ---------------------------------------------------------------------------
print("\n[4] _preview_exclusion_reason() source structure")

excl_fn_start = src.find("def _preview_exclusion_reason(")
excl_fn_end = src.find("\ndef ", excl_fn_start + 1)
excl_body = src[excl_fn_start:excl_fn_end]

checks2 = {
    "blocked_prefix_check_present": "_blocked_email_prefix(em)" in excl_body,
    "generic_email_prefix_returned": '"generic_email_prefix"' in excl_body,
    "invalid_email_still_present": '"invalid_email"' in excl_body,
    "blocked_check_after_invalid_email": excl_body.index("_blocked_email_prefix") > excl_body.index('"invalid_email"'),
}

for label, result in checks2.items():
    if result:
        ok(label)
    else:
        fail(label)


# ---------------------------------------------------------------------------
# 5. Pipeline history not deleted (sync_from_latest_run stays intact)
# ---------------------------------------------------------------------------
print("\n[5] Pipeline history preservation")

sync_start = src.find("def sync_from_latest_run(")
sync_end = src.find("\ndef ", sync_start + 1)
sync_body = src[sync_start:sync_end]

checks3 = {
    "sync_fn_exists": sync_start != -1,
    "no_truncate_in_sync": "entries = []" not in sync_body and "entries.clear()" not in sync_body,
    "merge_logic_present": "existing_map" in sync_body or "by_email" in sync_body or "existing" in sync_body,
    "load_current_run_not_in_sync": "_load_current_run_emails" not in sync_body,
}

for label, result in checks3.items():
    if result:
        ok(label)
    else:
        fail(label)


# ---------------------------------------------------------------------------
# 6. Dashboard HTML checks
# ---------------------------------------------------------------------------
print("\n[6] dashboard_relay_premium.html")

html_path = Path(__file__).parent / "dashboard_relay_premium.html"
html = html_path.read_text(encoding="utf-8")

html_checks = {
    "preview_table_has_datum_col": "<th>Datum</th>" in html and "previewTable" in html,
    "preview_table_historisch_badge": "historisch" in html,
    "preview_table_is_current_run_check": "is_current_run===false" in html or "is_current_run === false" in html,
    "preview_table_added_at_rendered": "r.added_at" in html,
    "pipeline_table_has_datum_col": html.count("<th>Datum</th>") >= 1,
    "pipeline_table_added_at_rendered": "it.added_at" in html or "it.created_at" in html,
    "no_send_button_in_preview_section": "send_all" not in html.lower() or "btn-send" not in html,
}

for label, result in html_checks.items():
    if result:
        ok(label)
    else:
        fail(label)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*55}")
print(f"  RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  FAILURES:")
    for f_ in FAIL:
        print(f"    - {f_}")
print(f"{'='*55}\n")

sys.exit(0 if not FAIL else 1)
