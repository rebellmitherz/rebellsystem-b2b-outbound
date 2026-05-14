#!/usr/bin/env python3
"""
Smoke-Test: Dashboard Results Refresh
Prueft ob nach Bot-Aktionen Ergebnisse korrekt aus output/latest/ geladen
und im Dashboard angezeigt werden.

Checks:
  1.  Dateien vorhanden
  2.  Neue Payload-Felder in _premium_dashboard_payload()
  3.  Payload counts enthalten leads_found, ready, review_leads, reply_items, linkedin
  4.  run_status / run_funnel Felder im Payload
  5.  HTML: neue Hilfsfunktionen vorhanden (normalizeCsvLead, readyLeadsTable, etc.)
  6.  HTML: renderEnrichment faellt auf leads_found zurueck
  7.  HTML: renderReview faellt auf review_leads zurueck
  8.  HTML: renderPipeline zeigt ready_leads + preview + linkedin Panels
  9.  HTML: renderReplies faellt auf reply_queue.items zurueck
  10. HTML: runStatusHtml() vorhanden
  11. HTML: Leere Zustaende in allen Sektionen
  12. HTML: loadAll() nach jeder Aktion aufgerufen (Auto-Refresh)
  13. HTML: Aktualisieren-Button ruft loadAll() auf
  14. HTML: Nav-Counts nutzen Fallback-Felder
  15. Keine gefaehrlichen Send-Routen im HTML
  16. HTTP-Tests gegen laufenden Server (Port 8765, falls erreichbar)

Ausfuehren: python smoke_dashboard_results_refresh.py
Optional: python cockpit_server.py starten fuer HTTP-Tests (Sektion 16)
"""
from __future__ import annotations

import http.client
import json
import py_compile
import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELAY_HTML = ROOT / "dashboard_relay_premium.html"
COCKPIT = ROOT / "cockpit_server.py"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"
INFO = "\033[96mINFO\033[0m"

failures: list[str] = []
passes: int = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passes
    tag = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{tag}] {label}{suffix}")
    if condition:
        passes += 1
    else:
        failures.append(label + (f" — {detail}" if detail else ""))


def skip(label: str, reason: str = "") -> None:
    print(f"  [{SKIP}] {label}" + (f"  ({reason})" if reason else ""))


def info(msg: str) -> None:
    print(f"  [{INFO}] {msg}")


def _port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_get(host: str, port: int, path: str, timeout: int = 5,
             max_bytes: int = 524288) -> tuple[int, str, str]:
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read(max_bytes).decode("utf-8", errors="replace")
        loc = resp.getheader("Location", "")
        conn.close()
        return resp.status, loc, body
    except Exception as exc:
        return -1, "", str(exc)[:80]


def main() -> int:
    print("\n=== Smoke-Test: Dashboard Results Refresh ===\n")

    # ── 1. Dateien vorhanden ─────────────────────────────────────────────────
    print("[1] Dateien vorhanden")
    check("dashboard_relay_premium.html existiert", RELAY_HTML.is_file())
    check("cockpit_server.py existiert", COCKPIT.is_file())
    if not RELAY_HTML.is_file() or not COCKPIT.is_file():
        print("\n=== NICHT BEWERTET (Dateien fehlen) ===\n")
        return 2

    relay_html = RELAY_HTML.read_text(encoding="utf-8")
    cockpit_src = COCKPIT.read_text(encoding="utf-8")

    # ── 2. Syntaxpruefung ────────────────────────────────────────────────────
    print("\n[2] Syntaxpruefung")
    try:
        py_compile.compile(str(COCKPIT), doraise=True)
        check("cockpit_server.py kompiliert fehlerfrei", True)
    except py_compile.PyCompileError as exc:
        check("cockpit_server.py kompiliert fehlerfrei", False, str(exc)[:120])

    # ── 3. Neue Payload-Felder in _premium_dashboard_payload() ──────────────
    print("\n[3] Neue output/latest Felder in _premium_dashboard_payload()")
    try:
        sys.path.insert(0, str(ROOT))
        import cockpit_server as cs
        payload = cs._premium_dashboard_payload()
        check("_premium_dashboard_payload() laeuft fehlerfrei", isinstance(payload, dict))

        # neue Daten-Sektionen
        new_sections = [
            "run_status", "run_funnel", "search_diag", "quality_warnings",
            "leads_found", "ready_leads", "review_leads",
            "outreach_preview_rows", "linkedin_results",
        ]
        for key in new_sections:
            check(f"  payload['{key}'] vorhanden", key in payload,
                  "fehlt — output/latest wird nicht gelesen")

        # Sicherheits-Flags unveraendert
        check("  safety.read_only == True",
              payload.get("safety", {}).get("read_only") is True)
        check("  safety.no_send == True",
              payload.get("safety", {}).get("no_send") is True)

    except Exception as exc:
        check("_premium_dashboard_payload() laeuft fehlerfrei", False, str(exc)[:160])
        payload = {}

    # ── 4. Payload counts enthalten neue Felder ──────────────────────────────
    print("\n[4] Payload counts — neue Felder")
    counts = payload.get("counts", {}) if isinstance(payload, dict) else {}
    for field in ["leads_found", "ready", "review_leads", "reply_items", "linkedin"]:
        check(f"  counts['{field}'] vorhanden", field in counts,
              "fehlt — Dashboard kann count nicht anzeigen")

    # ── 5. run_status / run_funnel Felder ────────────────────────────────────
    print("\n[5] run_status / run_funnel Felder im Payload")
    rs = payload.get("run_status") if isinstance(payload, dict) else None
    rf = payload.get("run_funnel") if isinstance(payload, dict) else None
    check("  run_status ist dict oder None",
          rs is None or isinstance(rs, dict))
    check("  run_funnel ist dict oder None",
          rf is None or isinstance(rf, dict))
    # Wenn vorhanden: Schluessel-Felder
    if isinstance(rs, dict):
        info(f"  run_status.run_status = {rs.get('run_status')!r}")
    if isinstance(rf, dict):
        info(f"  run_funnel.qualified_leads_found = {rf.get('qualified_leads_found')!r}")

    # ── 6. HTML: neue Hilfsfunktionen ────────────────────────────────────────
    print("\n[6] HTML — neue Hilfsfunktionen")
    helpers = [
        ("normalizeCsvLead",  r"function normalizeCsvLead\("),
        ("readyLeadsTable",   r"function readyLeadsTable\("),
        ("previewTable",      r"function previewTable\("),
        ("linkedinTable",     r"function linkedinTable\("),
        ("runStatusHtml",     r"function runStatusHtml\("),
    ]
    for name, pattern in helpers:
        check(f"  {name}() vorhanden", bool(re.search(pattern, relay_html)),
              "fehlt — zugehoerige Sektion wird nicht gerendert")

    # ── 7. HTML: renderEnrichment faellt auf leads_found zurueck ─────────────
    print("\n[7] HTML — renderEnrichment Fallback auf leads_found")
    check(
        "renderEnrichment() liest enriched_leads",
        bool(re.search(r"function renderEnrichment.*?enriched_leads", relay_html, re.DOTALL)),
    )
    check(
        "renderEnrichment() faellt auf leads_found (CSV) zurueck",
        bool(re.search(r"function renderEnrichment.*?leads_found", relay_html, re.DOTALL)),
        "fehlt — nach mine.py-Suche keine Leads sichtbar",
    )
    check(
        "renderEnrichment() nutzt normalizeCsvLead()",
        bool(re.search(r"function renderEnrichment.*?normalizeCsvLead", relay_html, re.DOTALL)),
        "fehlt — CSV-Feldnamen werden nicht normalisiert",
    )

    # ── 8. HTML: renderReview faellt auf review_leads zurueck ────────────────
    print("\n[8] HTML — renderReview Fallback auf review_leads")
    check(
        "renderReview() liest email_review",
        bool(re.search(r"function renderReview.*?email_review", relay_html, re.DOTALL)),
    )
    check(
        "renderReview() faellt auf review_leads (CSV) zurueck",
        bool(re.search(r"function renderReview.*?review_leads", relay_html, re.DOTALL)),
        "fehlt — review_before_send.csv wird nicht angezeigt",
    )

    # ── 9. HTML: renderPipeline zeigt ready_leads / preview / linkedin ────────
    print("\n[9] HTML — renderPipeline zeigt alle Sub-Panels")
    check(
        "renderPipeline() zeigt ready_leads (Versandbereit-Panel)",
        bool(re.search(r"function renderPipeline.*?ready_leads", relay_html, re.DOTALL)),
        "fehlt — ready_to_send.csv wird nicht angezeigt",
    )
    check(
        "renderPipeline() zeigt outreach_preview_rows",
        bool(re.search(r"function renderPipeline.*?outreach_preview_rows", relay_html, re.DOTALL)),
        "fehlt — outreach_preview.json wird nicht angezeigt",
    )
    check(
        "renderPipeline() zeigt linkedin_results",
        bool(re.search(r"function renderPipeline.*?linkedin_results", relay_html, re.DOTALL)),
        "fehlt — linkedin_outreach.csv wird nicht angezeigt",
    )
    check(
        "renderPipeline() nutzt readyLeadsTable()",
        bool(re.search(r"function renderPipeline.*?readyLeadsTable\(", relay_html, re.DOTALL)),
    )
    check(
        "renderPipeline() nutzt previewTable()",
        bool(re.search(r"function renderPipeline.*?previewTable\(", relay_html, re.DOTALL)),
    )
    check(
        "renderPipeline() nutzt linkedinTable()",
        bool(re.search(r"function renderPipeline.*?linkedinTable\(", relay_html, re.DOTALL)),
    )

    # ── 10. HTML: renderReplies faellt auf reply_queue.items zurueck ──────────
    print("\n[10] HTML — renderReplies Fallback auf reply_queue.items")
    check(
        "renderReplies() liest state.data.replies",
        bool(re.search(r"function renderReplies.*?data\.replies", relay_html, re.DOTALL)),
    )
    check(
        "renderReplies() faellt auf reply_queue.items zurueck",
        bool(re.search(r"function renderReplies.*?reply_queue.*?items", relay_html, re.DOTALL)),
        "fehlt — nach Sync ohne mapping keine Antworten sichtbar",
    )

    # ── 11. HTML: runStatusHtml() zeigt Letzter-Run-Panel ────────────────────
    print("\n[11] HTML — runStatusHtml() Letzter-Run-Panel")
    check(
        "runStatusHtml() liest run_status",
        bool(re.search(r"function runStatusHtml.*?run_status", relay_html, re.DOTALL)),
    )
    check(
        "runStatusHtml() liest run_funnel",
        bool(re.search(r"function runStatusHtml.*?run_funnel", relay_html, re.DOTALL)),
    )
    check(
        "runStatusHtml() zeigt Leerzustand wenn kein Run-Status",
        bool(re.search(r"runStatusHtml.*?Noch kein Lauf-Status", relay_html, re.DOTALL)),
        "fehlt — Leerzustand bei fehlendem run_status.json",
    )
    check(
        "renderOverview() ruft runStatusHtml() auf",
        bool(re.search(r"function renderOverview.*?runStatusHtml\(\)", relay_html, re.DOTALL)),
        "fehlt — Letzter-Run-Block erscheint nicht in Uebersicht",
    )

    # ── 12. HTML: Leere Zustaende in allen Sektionen ─────────────────────────
    print("\n[12] HTML — Leere Zustaende (nie blank)")
    empty_states = [
        ("Keine Enrichment-Daten",    "Keine Enrichment-Daten vorhanden"),
        ("Keine versandbereiten Leads", "Keine versandbereiten Leads"),
        ("Keine Vorschau-Daten",       "Keine Vorschau-Daten"),
        ("Keine LinkedIn-Daten",       "Keine LinkedIn-Daten"),
        ("Keine Replies",              "Keine Antworten vorhanden"),
        ("Keine Review Items",         "Keine Review Items vorhanden"),
        ("Keine Pipeline-Daten",       "Keine Pipeline-Daten vorhanden"),
        ("Keine Signale",              "Keine Signale vorhanden"),
    ]
    for label, text in empty_states:
        check(f"  Leerzustand '{label}' vorhanden", text in relay_html,
              "fehlt — Sektion bleibt bei leeren Daten komplett leer")

    # ── 13. HTML: Auto-Refresh — loadAll() nach jeder Aktion ─────────────────
    print("\n[13] HTML — Auto-Refresh nach Aktionen")
    # postAction ruft loadAll() auf
    check(
        "postAction() ruft loadAll() nach Abschluss",
        bool(re.search(r"function postAction.*?await loadAll\(\)", relay_html, re.DOTALL)),
        "fehlt — Dashboard aktualisiert sich nicht nach Aktionen",
    )
    # trackJob ruft loadAll() auf
    check(
        "trackJob() ruft loadAll() nach Job-Ende",
        bool(re.search(r"function trackJob.*?await loadAll\(\)", relay_html, re.DOTALL)),
        "fehlt — Hintergrundjobs aktualisieren Dashboard nicht",
    )
    # decide() ruft loadAll() auf
    check(
        "decide() ruft loadAll() nach Entscheidung",
        bool(re.search(r"function decide.*?await loadAll\(\)", relay_html, re.DOTALL)),
        "fehlt — Review-Entscheidungen aktualisieren Dashboard nicht",
    )
    # loadAll() beim Seitenstart
    check(
        "loadAll() beim Seitenstart aufgerufen",
        bool(re.search(r"loadAll\(\)\.catch\(", relay_html)),
        "fehlt — Dashboard laedt beim Start keine Daten",
    )

    # ── 14. HTML: Aktualisieren-Button ruft loadAll() auf ────────────────────
    print("\n[14] HTML — Aktualisieren-Button")
    check(
        "Aktualisieren-Button hat onclick=loadAll()",
        bool(re.search(r'onclick=["\']loadAll\(\)["\']', relay_html)),
        "fehlt — Manueller Refresh funktioniert nicht",
    )

    # ── 15. HTML: Nav-Counts nutzen Fallback-Felder ───────────────────────────
    print("\n[15] HTML — Nav-Counts mit Fallback-Feldern")
    check(
        "nav-enrichment nutzt leads_found als Fallback",
        bool(re.search(r'nav-enrichment.*?leads_found', relay_html, re.DOTALL)) or
        bool(re.search(r'leads_found.*?nav-enrichment', relay_html, re.DOTALL)),
        "fehlt — nav zeigt 0 nach mine.py-Suche",
    )
    check(
        "nav-review addiert review_leads",
        bool(re.search(r'nav-review.*?review_leads', relay_html, re.DOTALL)) or
        bool(re.search(r'review_leads.*?nav-review', relay_html, re.DOTALL)),
        "fehlt — nav-review ignoriert CSV-Review-Leads",
    )
    check(
        "nav-replies nutzt reply_items als Fallback",
        bool(re.search(r'nav-replies.*?reply_items', relay_html, re.DOTALL)) or
        bool(re.search(r'reply_items.*?nav-replies', relay_html, re.DOTALL)),
        "fehlt — nav zeigt 0 wenn reply_queue noch nicht gemappt",
    )

    # ── 16. Gefaehrliche Routen NICHT im HTML ────────────────────────────────
    print("\n[16] Gefaehrliche Send/Approve-Routen NICHT in Relay-HTML")
    for route in ["/api/send-batch", "/api/send-followups", "/api/full-auto",
                  "/api/approve-all", "/api/lead/send", "/api/lead/approve"]:
        check(f"  '{route}' NICHT in Relay-HTML", route not in relay_html,
              "EXPONIERT GEFAEHRLICHE ROUTE!")

    # ── 17. HTTP-Tests gegen laufenden Server ────────────────────────────────
    print("\n[17] HTTP-Tests gegen laufenden Server (Port 8765, falls erreichbar)")
    server_running = _port_open(DEFAULT_HOST, DEFAULT_PORT)

    if not server_running:
        skip("HTTP-Tests uebersprungen",
             f"kein Server auf {DEFAULT_HOST}:{DEFAULT_PORT} — starte python cockpit_server.py")
        info("Starte cockpit_server.py und fuehre diesen Test erneut aus fuer vollstaendige Pruefung")
    else:
        info(f"Server erreichbar auf {DEFAULT_HOST}:{DEFAULT_PORT}")

        # Vorab-Check: aktueller Server? Probe /api/premium-dashboard fuer neue Felder
        _root_status, _root_loc, _ = http_get(DEFAULT_HOST, DEFAULT_PORT, "/")
        routing_stale = _root_loc != "/relay" and "/relay" not in _root_loc
        if routing_stale:
            info(f"STALER SERVER: GET / leitet auf '{_root_loc}' (erwartet: /relay)")
            info("Bitte cockpit_server.py neu starten — danach alle HTTP-Tests gruen")
            skip("HTTP-Tests", "Server laeuft noch mit alter Code-Version (Routing)")
        else:
            # /api/premium-dashboard → 200 + neue Felder
            status, _, body = http_get(DEFAULT_HOST, DEFAULT_PORT, "/api/premium-dashboard")
            check(f"GET /api/premium-dashboard liefert 200 (erhalten: {status})",
                  status == 200, body[:80] if status != 200 else "")

            if status != 200:
                skip("HTTP Payload-Tests", f"kein 200 von /api/premium-dashboard (status={status})")
                data = None
            else:
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    data = None

                # Stale-Server-Erkennung: fehlen neue Felder → Server wurde nicht neu gestartet
                payload_stale = data is None or "run_status" not in data
                if payload_stale:
                    info("STALER SERVER: /api/premium-dashboard enthaelt keine neuen Felder (run_status fehlt)")
                    info("Bitte cockpit_server.py neu starten — danach alle HTTP-Payload-Tests gruen")
                    skip("HTTP Payload-Tests (neue Felder)", "Server laeuft noch mit alter Code-Version (Payload)")
                    data = None  # verhindert weitere Payload-Checks

                if data is not None:
                    http_new_sections = [
                        "run_status", "run_funnel", "leads_found",
                        "ready_leads", "review_leads", "outreach_preview_rows",
                        "linkedin_results",
                    ]
                    print("\n  Neue Sektionen im HTTP-Payload:")
                    for key in http_new_sections:
                        val = data.get(key)
                        present = key in data
                        check(f"  payload['{key}'] vorhanden", present,
                              "fehlt — output/latest wird nicht gelesen")
                        if present:
                            if isinstance(val, list):
                                info(f"    {key}: {len(val)} Eintraege")
                            elif isinstance(val, dict):
                                info(f"    {key}: dict mit {len(val)} Schluessel")
                            else:
                                info(f"    {key}: {val!r}")

                    print("\n  Counts im HTTP-Payload:")
                    http_counts = data.get("counts", {})
                    for field in ["leads_found", "ready", "review_leads", "reply_items", "linkedin"]:
                        check(f"  counts['{field}'] vorhanden", field in http_counts,
                              "fehlt — Nav-Count wird als 0 angezeigt")
                        if field in http_counts:
                            info(f"    counts.{field} = {http_counts[field]!r}")

    # ── Zusammenfassung ───────────────────────────────────────────────────────
    total = passes + len(failures)
    print(f"\n=== ERGEBNIS: {passes}/{total} Tests bestanden ===")
    if failures:
        print("Fehlgeschlagen:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Alle Tests bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
