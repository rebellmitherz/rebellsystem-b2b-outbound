#!/usr/bin/env python3
"""
Smoke-Test: Dashboard Next-Actions Block (Post Lead-Anreicherung)
Prueft ob nach Lead-Anreicherung sichtbare naechste Aktionen ohne echten Versand
vorhanden sind.

Checks:
  1.  Dateien vorhanden
  2.  Syntaxpruefung cockpit_server.py
  3.  HTML: enrichFunnelGapHtml() vorhanden
  4.  HTML: enrichNextActionsHtml() vorhanden
  5.  HTML: previewAndOpen() vorhanden — ruft nur /api/preview, keinen Send
  6.  HTML: renderEnrichment() ruft enrichFunnelGapHtml() und enrichNextActionsHtml() auf
  7.  HTML: id="enrich-next-actions" Block vorhanden
  8.  HTML: Button "Outreach-Vorschau erzeugen" (id="btn-outreach-preview") vorhanden
  9.  HTML: Button triggert previewAndOpen() — kein direktes Send
  10. HTML: Button "Pipeline anzeigen" (id="btn-go-pipeline") ruft setView('pipeline')
  11. HTML: Button "Review oeffnen" (id="btn-go-review") ruft setView('review')
  12. HTML: Button "Daten aktualisieren" (id="btn-refresh-data") ruft loadAll()
  13. HTML: enrichFunnelGapHtml() zeigt target_gap_count
  14. HTML: enrichFunnelGapHtml() zeigt stop_reason
  15. HTML: enrichFunnelGapHtml() zeigt Ablehnungsgruende (rejected_count_by_reason)
  16. HTML: enrichFunnelGapHtml() zeigt Ziel-erfuellt-Banner wenn target_fulfilled
  17. Payload: run_funnel enthaelt target_gap_count, stop_reason, requested_count
  18. Keine gefaehrlichen Send-Routen in previewAndOpen() / naechste-Aktionen-Bereich
  19. HTML: Keine gefaehrlichen Send-Routen im gesamten HTML
  20. HTTP-Test gegen laufenden Server (Port 8765, falls erreichbar)

Ausfuehren:  python smoke_dashboard_next_actions.py
Optional:    python cockpit_server.py fuer HTTP-Tests (Sektion 20)
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
    print("\n=== Smoke-Test: Dashboard Next-Actions Block ===\n")

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

    # ── 3. HTML: enrichFunnelGapHtml() ───────────────────────────────────────
    print("\n[3] HTML — enrichFunnelGapHtml() Funktion vorhanden")
    check(
        "enrichFunnelGapHtml() definiert",
        bool(re.search(r"function enrichFunnelGapHtml\(", relay_html)),
        "fehlt — Funnel-Gap-Banner kann nicht angezeigt werden",
    )

    # ── 4. HTML: enrichNextActionsHtml() ─────────────────────────────────────
    print("\n[4] HTML — enrichNextActionsHtml() Funktion vorhanden")
    check(
        "enrichNextActionsHtml() definiert",
        bool(re.search(r"function enrichNextActionsHtml\(", relay_html)),
        "fehlt — Naechster-Schritt-Block kann nicht angezeigt werden",
    )

    # ── 5. HTML: previewAndOpen() — nur /api/preview, kein Send ──────────────
    print("\n[5] HTML — previewAndOpen() — nur Preview, kein Send")
    check(
        "previewAndOpen() definiert",
        bool(re.search(r"async function previewAndOpen\(", relay_html)),
        "fehlt — Outreach-Vorschau-Button hat keine Aktion",
    )
    check(
        "previewAndOpen() ruft /api/preview auf",
        bool(re.search(r"previewAndOpen.*?/api/preview", relay_html, re.DOTALL)),
        "fehlt — Button generiert keine Vorschau",
    )
    check(
        "previewAndOpen() ruft setView('pipeline') nach Preview",
        bool(re.search(r"previewAndOpen.*?setView\(['\"]pipeline['\"]", relay_html, re.DOTALL)),
        "fehlt — nach Preview-Generierung wird Pipeline nicht geoeffnet",
    )
    for dangerous in ["/api/send", "/api/approve", "/api/full-auto", "send-batch"]:
        check(
            f"previewAndOpen() enthaelt NICHT '{dangerous}'",
            dangerous not in relay_html[relay_html.find("previewAndOpen"):relay_html.find("previewAndOpen")+300]
            if "previewAndOpen" in relay_html else True,
            "GEFAEHRLICH: previewAndOpen() loest Versand aus!",
        )

    # ── 6. HTML: renderEnrichment() ruft beide Helfer auf ────────────────────
    print("\n[6] HTML — renderEnrichment() integriert beide Helfer")
    check(
        "renderEnrichment() ruft enrichFunnelGapHtml() auf",
        bool(re.search(r"function renderEnrichment.*?enrichFunnelGapHtml\(\)", relay_html, re.DOTALL)),
        "fehlt — Funnel-Gap wird nicht in Anreicherungs-Sektion angezeigt",
    )
    check(
        "renderEnrichment() ruft enrichNextActionsHtml() auf",
        bool(re.search(r"function renderEnrichment.*?enrichNextActionsHtml\(\)", relay_html, re.DOTALL)),
        "fehlt — Naechster-Schritt-Block erscheint nicht nach Lead-Tabelle",
    )

    # ── 7. HTML: id="enrich-next-actions" Block ──────────────────────────────
    print("\n[7] HTML — id='enrich-next-actions' Block vorhanden")
    check(
        "id=\"enrich-next-actions\" vorhanden",
        "enrich-next-actions" in relay_html,
        "fehlt — Block ist nicht auffindbar per ID",
    )

    # ── 8. HTML: Button "Outreach-Vorschau erzeugen" ─────────────────────────
    print("\n[8] HTML — Button 'Outreach-Vorschau erzeugen'")
    check(
        "id=\"btn-outreach-preview\" vorhanden",
        "btn-outreach-preview" in relay_html,
        "fehlt — Button nicht auffindbar",
    )
    check(
        "Buttontext 'Outreach-Vorschau erzeugen' vorhanden",
        "Outreach-Vorschau erzeugen" in relay_html,
        "fehlt — Button-Label fehlt",
    )

    # ── 9. HTML: Button triggert previewAndOpen(), keinen direkten Send ───────
    print("\n[9] HTML — Button triggert previewAndOpen(), kein direkter Send")
    check(
        "btn-outreach-preview ruft previewAndOpen() auf",
        bool(re.search(r'btn-outreach-preview[^>]*onclick=["\']previewAndOpen\(\)', relay_html)),
        "fehlt — Button loest keinen Preview-Job aus",
    )
    # Sicherheit: kein direkter /api/send in onclick des Buttons
    btn_region = ""
    m = re.search(r'btn-outreach-preview[^>]*>', relay_html)
    if m:
        btn_region = relay_html[m.start():m.start() + 300]
    for bad_route in ["/api/send", "/api/approve", "send-batch", "full-auto"]:
        check(
            f"btn-outreach-preview enthaelt NICHT '{bad_route}' in onclick",
            bad_route not in btn_region,
            "GEFAEHRLICH: Button loest Versand aus!",
        )

    # ── 10. HTML: Pipeline-Button ruft setView() auf ─────────────────────────
    print("\n[10] HTML — Pipeline-Button wechselt nur die UI")
    check(
        "id=\"btn-go-pipeline\" vorhanden",
        "btn-go-pipeline" in relay_html,
        "fehlt — Pipeline-Button nicht auffindbar",
    )
    check(
        "btn-go-pipeline ruft setView('pipeline') auf",
        bool(re.search(r"btn-go-pipeline[^>]*onclick=[\"']setView\(['\"]pipeline['\"]", relay_html)),
        "fehlt — Pipeline-Button wechselt Ansicht nicht",
    )

    # ── 11. HTML: Review-Button ruft setView() auf ────────────────────────────
    print("\n[11] HTML — Review-Button wechselt nur die UI")
    check(
        "id=\"btn-go-review\" vorhanden",
        "btn-go-review" in relay_html,
        "fehlt — Review-Button nicht auffindbar",
    )
    check(
        "btn-go-review ruft setView('review') auf",
        bool(re.search(r"btn-go-review[^>]*onclick=[\"']setView\(['\"]review['\"]", relay_html)),
        "fehlt — Review-Button wechselt Ansicht nicht",
    )

    # ── 12. HTML: Aktualisieren-Button ruft loadAll() auf ────────────────────
    print("\n[12] HTML — Aktualisieren-Button ruft loadAll()")
    check(
        "id=\"btn-refresh-data\" vorhanden",
        "btn-refresh-data" in relay_html,
        "fehlt — Aktualisieren-Button nicht auffindbar",
    )
    check(
        "btn-refresh-data ruft loadAll() auf",
        bool(re.search(r"btn-refresh-data[^>]*onclick=[\"']loadAll\(\)", relay_html)),
        "fehlt — Aktualisieren-Button laedt keine Daten nach",
    )

    # ── 13. HTML: enrichFunnelGapHtml() zeigt target_gap_count ───────────────
    print("\n[13] HTML — enrichFunnelGapHtml() zeigt target_gap_count")
    check(
        "enrichFunnelGapHtml() liest rf.target_gap_count",
        bool(re.search(r"enrichFunnelGapHtml.*?target_gap_count", relay_html, re.DOTALL)),
        "fehlt — Luecke zum Ziel wird nicht angezeigt",
    )
    check(
        "enrichFunnelGapHtml() zeigt target_gap_count im Banner",
        bool(re.search(r"enrichFunnelGapHtml.*?target_gap_count.*?gap", relay_html, re.DOTALL)),
        "fehlt — target_gap_count erscheint nicht im UI-Banner",
    )

    # ── 14. HTML: enrichFunnelGapHtml() zeigt stop_reason ────────────────────
    print("\n[14] HTML — enrichFunnelGapHtml() zeigt stop_reason")
    check(
        "enrichFunnelGapHtml() liest rf.stop_reason",
        bool(re.search(r"enrichFunnelGapHtml.*?stop_reason", relay_html, re.DOTALL)),
        "fehlt — stop_reason wird nicht angezeigt",
    )

    # ── 15. HTML: Ablehnungsgruende aus rejected_count_by_reason ─────────────
    print("\n[15] HTML — enrichFunnelGapHtml() zeigt Ablehnungsgruende")
    check(
        "enrichFunnelGapHtml() liest rf.rejected_count_by_reason",
        bool(re.search(r"enrichFunnelGapHtml.*?rejected_count_by_reason", relay_html, re.DOTALL)),
        "fehlt — Ablehnungsgruende werden nicht angezeigt",
    )

    # ── 16. HTML: Ziel-erfuellt-Banner ───────────────────────────────────────
    print("\n[16] HTML — enrichFunnelGapHtml() zeigt Ziel-erfuellt-Banner")
    check(
        "enrichFunnelGapHtml() zeigt 'Ziel erreicht' wenn target_fulfilled",
        "Ziel erreicht" in relay_html,
        "fehlt — Erfolgsfall wird nicht dargestellt",
    )
    check(
        "enrichFunnelGapHtml() zeigt 'Ziel nicht erreicht' wenn Luecke",
        "Ziel nicht erreicht" in relay_html,
        "fehlt — Gap-Fall wird nicht dargestellt",
    )

    # ── 17. Payload: run_funnel Felder ───────────────────────────────────────
    print("\n[17] Payload — run_funnel Felder vorhanden")
    try:
        sys.path.insert(0, str(ROOT))
        import cockpit_server as cs
        payload = cs._premium_dashboard_payload()
        check("_premium_dashboard_payload() laeuft fehlerfrei", isinstance(payload, dict))
        rf = payload.get("run_funnel") or {}
        check("run_funnel ist dict oder None",
              rf is None or isinstance(rf, dict))
        if isinstance(rf, dict) and rf:
            for key in ["target_gap_count", "stop_reason", "requested_count",
                        "qualified_leads_found", "target_fulfilled"]:
                present = key in rf
                check(f"  run_funnel['{key}'] vorhanden", present,
                      "fehlt — enrichFunnelGapHtml() kann diesen Wert nicht anzeigen")
                if present:
                    info(f"    run_funnel.{key} = {rf[key]!r}")
        else:
            skip("run_funnel Felder", "run_funnel ist leer oder None (kein Lauf-Status vorhanden)")
            info("run_funnel wird nach einem Bot-Lauf befuellt (lead_funnel_diagnostics.json)")
    except Exception as exc:
        check("_premium_dashboard_payload() laeuft fehlerfrei", False, str(exc)[:160])

    # ── 18. Sicherheit: previewAndOpen() loest keinen Versand aus ────────────
    print("\n[18] Sicherheit — previewAndOpen() loest keinen Versand aus")
    # Extrahiere den Bereich der Funktion
    pa_match = re.search(r"async function previewAndOpen\([^}]*\}", relay_html, re.DOTALL)
    if pa_match:
        pa_body = pa_match.group(0)
        info(f"previewAndOpen(): {pa_body[:120].strip()}")
        check(
            "previewAndOpen() enthaelt nur /api/preview",
            "/api/preview" in pa_body,
            "fehlt — kein Preview-Aufruf",
        )
        for bad in ["/api/send", "/api/approve", "full-auto", "send-batch", "approve-all"]:
            check(
                f"previewAndOpen() enthaelt NICHT '{bad}'",
                bad not in pa_body,
                f"GEFAEHRLICH: loest '{bad}' aus!",
            )
    else:
        check("previewAndOpen() Funktion auffindbar", False,
              "Funktion nicht gefunden — Sicherheitspruefung nicht moeglich")

    # ── 19. Keine gefaehrlichen Routen im gesamten HTML ──────────────────────
    print("\n[19] Gefaehrliche Send/Approve-Routen NICHT in Relay-HTML")
    for route in ["/api/send-batch", "/api/send-followups", "/api/full-auto",
                  "/api/approve-all", "/api/lead/send", "/api/lead/approve"]:
        check(f"  '{route}' NICHT in Relay-HTML", route not in relay_html,
              "EXPONIERT GEFAEHRLICHE ROUTE!")

    # ── 20. HTTP-Tests gegen laufenden Server ────────────────────────────────
    print("\n[20] HTTP-Tests gegen laufenden Server (Port 8765, falls erreichbar)")
    server_running = _port_open(DEFAULT_HOST, DEFAULT_PORT)
    if not server_running:
        skip("HTTP-Tests uebersprungen",
             f"kein Server auf {DEFAULT_HOST}:{DEFAULT_PORT} — starte python cockpit_server.py")
        info("Starte cockpit_server.py und fuehre diesen Test erneut aus fuer vollstaendige Pruefung")
    else:
        info(f"Server erreichbar auf {DEFAULT_HOST}:{DEFAULT_PORT}")
        # GET / → Redirect zu /relay
        root_status, root_loc, _ = http_get(DEFAULT_HOST, DEFAULT_PORT, "/")
        check(
            f"GET / leitet auf /relay weiter (erhalten: {root_loc!r})",
            "/relay" in root_loc,
            "Staler Server — bitte neu starten",
        )
        if "/relay" not in root_loc:
            skip("HTTP Payload-Tests", "Server laeuft noch mit alter Code-Version")
        else:
            # /api/premium-dashboard
            status, _, body = http_get(DEFAULT_HOST, DEFAULT_PORT, "/api/premium-dashboard")
            check(f"GET /api/premium-dashboard liefert 200 (erhalten: {status})",
                  status == 200, body[:80] if status != 200 else "")
            if status == 200:
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    data = None
                if data is None:
                    check("Payload ist gueltiges JSON", False, "JSON-Parse fehlgeschlagen")
                else:
                    check("Payload ist gueltiges JSON", True)
                    rf_http = data.get("run_funnel") or {}
                    check("run_funnel im HTTP-Payload",
                          "run_funnel" in data,
                          "fehlt — enrichFunnelGapHtml() hat keine Daten")
                    if isinstance(rf_http, dict) and rf_http:
                        for k in ["target_gap_count", "stop_reason", "target_fulfilled"]:
                            present = k in rf_http
                            check(f"  HTTP run_funnel['{k}'] vorhanden", present)
                            if present:
                                info(f"    run_funnel.{k} = {rf_http[k]!r}")
                    # GET /relay liefert 200 + HTML mit enrich-next-actions
                    relay_status, _, relay_body = http_get(DEFAULT_HOST, DEFAULT_PORT, "/relay")
                    check(f"GET /relay liefert 200 (erhalten: {relay_status})",
                          relay_status == 200)
                    if relay_status == 200:
                        check(
                            "/relay HTML enthaelt 'enrich-next-actions'",
                            "enrich-next-actions" in relay_body,
                            "fehlt — Block wird vom Server nicht ausgeliefert",
                        )
                        check(
                            "/relay HTML enthaelt 'btn-outreach-preview'",
                            "btn-outreach-preview" in relay_body,
                            "fehlt — Button wird nicht ausgeliefert",
                        )

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
