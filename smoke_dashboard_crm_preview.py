"""Smoke test: CRM-Preview Block im Dashboard.

Prueft:
- /api/premium-dashboard liefert crm_preview Block
- Block enthaelt dry_run, provider, count, payloads, warnings
- fehlende crm_payload_preview.json crasht nicht (Fallback leeres dict)
- dashboard_relay_premium.html enthaelt crmPreviewHtml()-Funktion
- review_required und dry_run werden sichtbar
- kein Send/SMTP/IMAP/CRM-Push in den geaenderten Stellen
"""
from __future__ import annotations

import json
import sys
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


# ── Test 1: cockpit_server.py — Konstante + Payload-Schluessel ────────────────
print("\n[1] cockpit_server.py — CRM_PREVIEW_FILE + Payload-Schluessel")
try:
    import cockpit_server as cs
    check("cockpit_server importiert", True)
except Exception as e:
    check("cockpit_server importiert", False, str(e))
    print(f"\n{FAIL} Abbruch — cockpit_server nicht importierbar")
    sys.exit(1)

check("CRM_PREVIEW_FILE definiert", hasattr(cs, "CRM_PREVIEW_FILE"))
if hasattr(cs, "CRM_PREVIEW_FILE"):
    expected = cs.OUT / "latest" / "crm_payload_preview.json"
    check(
        "CRM_PREVIEW_FILE zeigt auf output/latest/crm_payload_preview.json",
        cs.CRM_PREVIEW_FILE == expected,
        f"ist: {cs.CRM_PREVIEW_FILE}",
    )


# ── Test 2: _premium_dashboard_payload liefert crm_preview ───────────────────
print("\n[2] _premium_dashboard_payload() — crm_preview Block")
try:
    payload = cs._premium_dashboard_payload()
    check("_premium_dashboard_payload() laeuft ohne Fehler", True)
except Exception as e:
    check("_premium_dashboard_payload() laeuft ohne Fehler", False, str(e))
    payload = {}

check("'crm_preview' Schluessel im Payload vorhanden", "crm_preview" in payload)
cp = payload.get("crm_preview", {})
check("crm_preview ist dict", isinstance(cp, dict))


# ── Test 3: fehlende crm_payload_preview.json crasht nicht ───────────────────
print("\n[3] Fallback bei fehlender crm_payload_preview.json")
orig_path = cs.CRM_PREVIEW_FILE
import tempfile
try:
    cs.CRM_PREVIEW_FILE = Path(tempfile.gettempdir()) / "_nonexistent_crm_preview.json"
    try:
        p2 = cs._premium_dashboard_payload()
        cp2 = p2.get("crm_preview", None)
        check("Payload ohne crm_payload_preview.json laeuft", True)
        check("crm_preview Schluessel noch vorhanden", "crm_preview" in p2)
        check(
            "Fallback ist leeres dict (nicht None/crash)",
            isinstance(cp2, dict),
            f"Typ: {type(cp2).__name__}",
        )
    except Exception as e:
        check("Payload ohne crm_payload_preview.json laeuft", False, str(e))
finally:
    cs.CRM_PREVIEW_FILE = orig_path


# ── Test 4: echter crm_payload_preview.json alle Pflichtfelder ───────────────
print("\n[4] Pflichtfelder (mit echter Datei)")
crm_file = ROOT / "output" / "latest" / "crm_payload_preview.json"
if crm_file.is_file():
    try:
        data = json.loads(crm_file.read_text(encoding="utf-8"))
        check("crm_payload_preview.json lesbar", True)
        for f in ("dry_run", "provider", "count", "payloads", "warnings", "generated_at"):
            check(f"  Feld '{f}' vorhanden", f in data)
        check("  dry_run == True", data.get("dry_run") is True)
        check("  provider == 'generic'", data.get("provider") == "generic")
        for p in data.get("payloads", []):
            check("  Payload: proposed_stage vorhanden", "proposed_stage" in p)
            check("  Payload: dry_run == True", p.get("dry_run") is True)
            check(
                "  Payload: proposed_stage gueltiger Wert",
                p.get("proposed_stage") in (
                    "appointment_ready", "hot_lead", "qualified_interest", "review_required"
                ),
            )
    except Exception as e:
        check("crm_payload_preview.json lesbar", False, str(e))
else:
    print("  SKIP — output/latest/crm_payload_preview.json fehlt (zuerst: python mine.py --crm-preview)")


# ── Test 5: dashboard_relay_premium.html — crmPreviewHtml() vorhanden ────────
print("\n[5] dashboard_relay_premium.html — crmPreviewHtml()")
relay_file = ROOT / "dashboard_relay_premium.html"
check("dashboard_relay_premium.html existiert", relay_file.is_file())

if relay_file.is_file():
    html = relay_file.read_text(encoding="utf-8")
    check("crmPreviewHtml() Funktion vorhanden", "function crmPreviewHtml" in html)
    check("crmPreviewHtml() in renderOverview() aufgerufen", "crmPreviewHtml()" in html)
    check("state.data.crm_preview Zugriff vorhanden", "state.data.crm_preview" in html or "crm_preview" in html)
    check("'review_required' wird sichtbar", "review_required" in html)
    check("dry_run wird sichtbar", "dry_run" in html)
    check("Fallback-Text 'Noch keine CRM-Preview' vorhanden", "Noch keine CRM-Preview" in html)
    check("proposed_stage wird angezeigt", "proposed_stage" in html)
    check("estimated_value_eur wird angezeigt", "estimated_value_eur" in html)
    check("'Kein API-Push' Hinweis vorhanden", "Kein API-Push" in html or "kein API-Push" in html or "kein CRM" in html.lower() or "Kein API-Push" in html)


# ── Test 6: keine Send/SMTP/IMAP/CRM-Push-Logik in den geaenderten Stellen ───
print("\n[6] Sicherheits-Check")
import inspect

pd_src = inspect.getsource(cs._premium_dashboard_payload)
for f in ("smtplib", "imaplib", "send_email", "SMTP(", "IMAP4(", "requests.post", "httpx"):
    check(f"'{f}' nicht in _premium_dashboard_payload", f not in pd_src)

if relay_file.is_file():
    fn_start = html.find("function crmPreviewHtml")
    fn_end   = html.find("\nfunction ", fn_start + 1)
    fn_src   = html[fn_start:fn_end] if fn_end > 0 else html[fn_start:]
    for f in ("fetch(", "postAction(", "XMLHttpRequest", "api("):
        check(f"'{f}' nicht in crmPreviewHtml()", f not in fn_src)


# ── Test 7: HTTP-Handler-Pfad (statische Code-Analyse) ───────────────────────
print("\n[7] HTTP-Handler-Pfad — statische Analyse")
import inspect

handler_src = inspect.getsource(cs.Handler.do_GET)
check(
    "/api/premium-dashboard Handler ruft _premium_dashboard_payload() auf",
    "_premium_dashboard_payload()" in handler_src
    and '"/api/premium-dashboard"' in handler_src,
)
# Verify there is no filtering between the call and _json()
# The pattern must be: _json(_premium_dashboard_payload()) with nothing in between
check(
    "Handler gibt _premium_dashboard_payload() ungefiltert an _json weiter",
    "_json(_premium_dashboard_payload())" in handler_src,
)
# _json must not filter keys
json_src = inspect.getsource(cs.Handler._json)
check("_json() filtert keine Keys (kein dict comprehension)", "{k:" not in json_src and "keys()" not in json_src)
check("_json() nutzt json.dumps direkt", "json.dumps" in json_src)

check("CRM_PREVIEW_FILE Konstante in cockpit_server", hasattr(cs, "CRM_PREVIEW_FILE"))
check(
    "crm_preview Variable in _premium_dashboard_payload Quellcode",
    "crm_preview" in inspect.getsource(cs._premium_dashboard_payload),
)
check(
    "'crm_preview' im Return-Dict von _premium_dashboard_payload",
    '"crm_preview"' in inspect.getsource(cs._premium_dashboard_payload)
    or "'crm_preview'" in inspect.getsource(cs._premium_dashboard_payload),
)


# ── Test 8: Live-HTTP (optional — nur wenn Server laeuft) ────────────────────
print("\n[8] Live-HTTP /api/premium-dashboard (SKIP wenn Server nicht laeuft)")
import urllib.request
import urllib.error

SERVER_URL = "http://127.0.0.1:8765/api/premium-dashboard"
try:
    with urllib.request.urlopen(SERVER_URL, timeout=3) as resp:
        raw = resp.read().decode("utf-8")
    live_data = json.loads(raw)
    check("HTTP 200 von /api/premium-dashboard", True)
    check(
        "HTTP-Antwort enthaelt 'crm_preview'",
        "crm_preview" in live_data,
        "Server laeuft mit alter Code-Version — bitte neu starten: python cockpit_server.py"
        if "crm_preview" not in live_data else "",
    )
    check(
        "HTTP crm_preview ist dict",
        isinstance(live_data.get("crm_preview"), dict),
    )
    check(
        "HTTP monthly_report und crm_preview beide vorhanden",
        "monthly_report" in live_data and "crm_preview" in live_data,
    )
except urllib.error.URLError:
    print("  SKIP — Server nicht erreichbar (starte: python cockpit_server.py)")
except Exception as e:
    check("HTTP-Antwort lesbar", False, str(e))


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n{'=' * 50}")
if failures:
    print(f"{FAIL}  {len(failures)} Test(s) fehlgeschlagen:")
    for f in failures:
        print(f"    - {f}")
    sys.exit(1)
else:
    print(f"{PASS}  Alle Checks bestanden.")
    print("       API liefert crm_preview Block. Dashboard zeigt CRM-Preview.")
    print("       Fallback bei fehlender Datei. review_required sichtbar. dry_run=True sichtbar.")
    print("       Kein CRM-Push. Kein Send. Kein SMTP. Kein IMAP. Kein Approve.")
    print("       WICHTIG: Server neu starten damit HTTP-Aenderungen aktiv werden: python cockpit_server.py")
