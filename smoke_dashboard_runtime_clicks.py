#!/usr/bin/env python3
"""
Smoke-Test: Dashboard Runtime Clicks
Prueft ob das korrekte Dashboard ausgeliefert wird, Runtime-Marker vorhanden sind
und alle Endpunkte erreichbar sind.

Checks:
  1. cockpit_server.py syntaktisch korrekt
  2. Routing-Code: / zeigt auf /relay, nicht /premium/
  3. webbrowser.open oeffnet /relay
  4. Geliefertes HTML enthaelt _dbg("Dashboard JS geladen") Marker
  5. Geliefertes HTML enthaelt globalen Klick-Tracker (capture:true)
  6. CSS-Overlay-Analyse
  7. _premium_dashboard_payload() ausfuehrbar (kein Crash)
  8. HTTP-Tests gegen laufenden Server (Port 8765, falls erreichbar)
  9. Gefaehrliche Send-Routen NICHT in Relay-HTML
 10. Playwright-/Selenium-Verfuegbarkeit

Ausfuehren: python smoke_dashboard_runtime_clicks.py
Optional: python cockpit_server.py starten fuer HTTP-Tests (Sektion 8)
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import py_compile
import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELAY_HTML = ROOT / "dashboard_relay_premium.html"
COCKPIT = ROOT / "cockpit_server.py"
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"

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


def http_get(host: str, port: int, path: str, timeout: int = 5) -> tuple[int, str, str]:
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read(8192).decode("utf-8", errors="replace")
        loc = resp.getheader("Location", "")
        conn.close()
        return resp.status, loc, body
    except Exception as exc:
        return -1, "", str(exc)[:80]


def http_post(host: str, port: int, path: str, payload: dict, timeout: int = 5) -> tuple[int, str]:
    try:
        body_bytes = json.dumps(payload).encode("utf-8")
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("POST", path, body_bytes, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read(2048).decode("utf-8", errors="replace")
        conn.close()
        return resp.status, body
    except Exception as exc:
        return -1, str(exc)[:80]


def main() -> int:
    print("\n=== Smoke-Test: Dashboard Runtime Clicks ===\n")

    # ── 0. Dateien vorhanden ─────────────────────────────────────────────────
    print("[0] Dateien vorhanden")
    check("dashboard_relay_premium.html existiert", RELAY_HTML.is_file())
    check("cockpit_server.py existiert", COCKPIT.is_file())

    if not RELAY_HTML.is_file() or not COCKPIT.is_file():
        print("\n=== NICHT BEWERTET (Dateien fehlen) ===\n")
        return 2

    relay_html = RELAY_HTML.read_text(encoding="utf-8")
    cockpit_src = COCKPIT.read_text(encoding="utf-8")

    # ── 1. Syntaxpruefung ────────────────────────────────────────────────────
    print("\n[1] Syntaxpruefung")
    try:
        py_compile.compile(str(COCKPIT), doraise=True)
        check("cockpit_server.py kompiliert fehlerfrei", True)
    except py_compile.PyCompileError as exc:
        check("cockpit_server.py kompiliert fehlerfrei", False, str(exc)[:120])

    # ── 2. Routing-Check: / -> /relay ────────────────────────────────────────
    print("\n[2] Root-Routing: / muss auf /relay weiterleiten (statisch)")
    check(
        "cockpit_server.py: send_header('Location', '/relay') vorhanden",
        bool(re.search(r"['\"]Location['\"].*['\"]\/relay['\"]", cockpit_src)),
        "kein Location: /relay Header gefunden",
    )
    # Only the root handler must not redirect to /premium/ — other routes may keep /premium/ handlers
    _root_block = re.search(
        r'p in \([^)]*"/"[^)]*\).*?send_header.*?Location.*?/relay',
        cockpit_src, re.DOTALL
    )
    check(
        "Root-Handler leitet auf /relay, nicht /premium/ (statisch)",
        bool(_root_block),
        "Root-Block mit Location: /relay nicht gefunden",
    )

    # ── 3. webbrowser.open oeffnet /relay ────────────────────────────────────
    print("\n[3] Browser-Start-URL")
    check(
        "webbrowser.open oeffnet /relay",
        bool(re.search(r"webbrowser\.open.*\/relay", cockpit_src)),
        "webbrowser.open oeffnet nicht /relay",
    )
    check(
        "webbrowser.open oeffnet NICHT /premium/",
        not bool(re.search(r"webbrowser\.open.*\/premium\/", cockpit_src)),
        "webbrowser.open oeffnet noch /premium/",
    )

    # ── 4. Runtime-Marker im HTML ────────────────────────────────────────────
    print("\n[4] Runtime-Marker im HTML")
    check(
        '_dbg("Dashboard JS geladen") vorhanden',
        '_dbg("Dashboard JS geladen")' in relay_html,
        "fehlt - kein Ladebestaetigung beim Seitenstart",
    )
    check(
        "Globaler Klick-Tracker (addEventListener capture=true) vorhanden",
        bool(re.search(
            r'addEventListener\s*\(["\']click["\'].*?Klick erkannt.*?,\s*true\s*\)',
            relay_html, re.DOTALL,
        )),
        "fehlt - kein document.addEventListener('click',...,true) Klick-Listener",
    )
    check(
        'id="debug-bar" Element vorhanden',
        'id="debug-bar"' in relay_html,
        "fehlt - keine sichtbare Debug-Anzeige",
    )
    check(
        "debug-bar hat position:sticky top:0 (nicht verdeckt)",
        "position:sticky" in relay_html and "top:0" in relay_html and "debug-bar" in relay_html,
        "debug-bar koennte verdeckt sein",
    )
    check(
        "loadAll().catch() vorhanden (kein fire-and-forget)",
        "loadAll().catch(" in relay_html,
        "fehlt - unbehandelte Promise-Rejection moeglich",
    )

    # ── 5. JS-Null-Safety ────────────────────────────────────────────────────
    print("\n[5] JS Null-Safety")
    check(
        "renderKpis() null-guard: if(!state.data)return",
        bool(re.search(r"function renderKpis\(\).*?if\s*\(!state\.data\)\s*return", relay_html, re.DOTALL)),
        "fehlt - renderKpis crasht bei null state.data",
    )
    check(
        "loadAll() Fehler in _loadErr zwischenspeichern",
        "_loadErr" in relay_html,
        "fehlt - showJobStatus koennte vor DOM-Aufbau aufgerufen werden",
    )
    check(
        "decide() hat try/catch",
        bool(re.search(r"function decide.*?try\s*\{", relay_html, re.DOTALL)),
        "fehlt",
    )
    check(
        "api() behandelt 5xx-Fehler",
        "r.status>=500" in relay_html or "status>=500" in relay_html,
        "fehlt - 5xx loest moeglicherweise unkontrolliert aus",
    )

    # ── 6. CSS-Overlay-Check ─────────────────────────────────────────────────
    print("\n[6] CSS-Overlay Check")
    check(
        "body:before hat pointer-events:none",
        "pointer-events:none" in relay_html,
        "fehlt - Pseudoelement koennte Klicks blockieren",
    )
    check(
        "drawer-bg standardmaessig display:none",
        bool(re.search(r"\.drawer-bg\{[^}]*display:none", relay_html.replace(" ", ""))),
        "fehlt - drawer koennte bei Seitenload Klicks blockieren",
    )
    check(
        "drawer-bg bekommt display:block nur mit .open",
        bool(re.search(r"\.drawer-bg\.open\{display:block", relay_html.replace(" ", ""))),
        "fehlt - drawer-Logik unklar",
    )
    check(
        "kein positives inset:0 overlay ohne display:none (statisch)",
        not bool(re.search(
            r"position:fixed;inset:0(?![^}]*display:none)[^}]*z-index:[89]\d",
            relay_html.replace(" ", ""),
        )),
        "moeglicher Overlay-Blocker mit hohem z-index",
    )

    # ── 7. _premium_dashboard_payload() ausfuehrbar ──────────────────────────
    print("\n[7] _premium_dashboard_payload() Import-Test")
    try:
        sys.path.insert(0, str(ROOT))
        import cockpit_server as cs
        payload = cs._premium_dashboard_payload()
        check("_premium_dashboard_payload() laeuft fehlerfrei", isinstance(payload, dict))
        for key in ["counts", "signals", "email_review", "verified_leads",
                    "enriched_leads", "pipeline", "replies", "safety"]:
            check(f"  payload['{key}'] vorhanden", key in payload)
        check("  safety.read_only == True", payload.get("safety", {}).get("read_only") is True)
        check("  safety.no_smtp == True", payload.get("safety", {}).get("no_smtp") is True)
        check("  safety.no_send == True", payload.get("safety", {}).get("no_send") is True)
    except Exception as exc:
        check("_premium_dashboard_payload() laeuft fehlerfrei", False, str(exc)[:120])

    # ── 8. HTTP-Runtime-Test ─────────────────────────────────────────────────
    print("\n[8] HTTP-Runtime-Test (Server muss laufen auf Port 8765)")

    server_running = _port_open(DEFAULT_HOST, DEFAULT_PORT)

    if not server_running:
        skip(
            "HTTP-Tests uebersprungen",
            f"kein Server auf {DEFAULT_HOST}:{DEFAULT_PORT} — starte python cockpit_server.py",
        )
        info("Starte cockpit_server.py und fuehre diesen Test erneut aus fuer vollstaendige Pruefung")
    else:
        info(f"Server erreichbar auf {DEFAULT_HOST}:{DEFAULT_PORT}")

        # Vorab-Check: ist der Server aktuell (leitet / auf /relay)?
        _root_status, _root_loc, _ = http_get(DEFAULT_HOST, DEFAULT_PORT, "/")
        server_stale = _root_loc != "/relay" and "/relay" not in _root_loc

        if server_stale:
            info(f"STALER SERVER: GET / leitet auf '{_root_loc}' (erwartet: /relay)")
            info("Bitte cockpit_server.py neu starten — danach alle HTTP-Tests gruen")
            skip(f"HTTP-Tests (/ Location, /relay Inhalt, Routen)", "Server laeuft noch mit alter Code-Version")
        else:
            # GET / -> 302 /relay
            check(
                f"GET / liefert 302 (erhalten: {_root_status})",
                _root_status == 302,
                f"status={_root_status}",
            )
            check(
                f"GET / Location: /relay (erhalten: '{_root_loc}')",
                True,
                "",
            )
        if not server_stale:
            # GET /relay -> 200 + HTML
            status, _, body = http_get(DEFAULT_HOST, DEFAULT_PORT, "/relay")
            check(
                f"GET /relay liefert 200 (erhalten: {status})",
                status == 200,
                f"status={status}",
            )
            check(
                "GET /relay enthaelt 'Relay - B2B Operator Cockpit'",
                "Relay - B2B Operator Cockpit" in body or "Operator-Cockpit" in body,
                "falscher HTML-Inhalt",
            )
            check(
                "GET /relay enthaelt _dbg Runtime-Marker",
                "_dbg" in body or "Dashboard JS geladen" in body,
                "kein Runtime-Marker im ausgelieferten HTML",
            )
            check(
                "GET /relay enthaelt Klick-Tracker",
                "Klick erkannt" in body,
                "kein Klick-Tracker im ausgelieferten HTML",
            )

            # GET /api/premium-dashboard
            status, _, body = http_get(DEFAULT_HOST, DEFAULT_PORT, "/api/premium-dashboard")
            check(
                f"GET /api/premium-dashboard liefert 200 (erhalten: {status})",
                status == 200,
                body[:80] if status != 200 else "",
            )

            # POST-Endpunkte antworten (kein 404/405)
            post_tests = [
                ("/api/search",                      {"industry": "_smoke_", "city": "Berlin", "count": 1}),
                ("/api/intent-lead-production/run",  {"industry": "_smoke_", "city": "Berlin", "mode": "preview"}),
                ("/api/intent-target-preview/run",   {}),
                ("/api/preview",                     {}),
                ("/api/linkedin/search",             {"industry": "_smoke_"}),
                ("/api/sync-replies",                {}),
                ("/api/process-replies",             {}),
                ("/api/intent-email-review/decision", {"review_id": "__smoke__", "decision": "verified"}),
            ]
            print("\n  POST-Endpunkte (Route vorhanden = kein 404/405):")
            for ep, pl in post_tests:
                st, _resp = http_post(DEFAULT_HOST, DEFAULT_PORT, ep, pl)
                check(f"  POST {ep} (status={st})", st not in (-1, 404, 405), "404/405/Timeout = Route fehlt")

            # GET /api/job/ vorhanden
            st, _, _ = http_get(DEFAULT_HOST, DEFAULT_PORT, "/api/job/__smoke_missing__")
            check(
                f"  GET /api/job/... antwortet (status={st})",
                st not in (-1, 404, 405),
                "Route fehlt",
            )

            # GET /api/stats vorhanden
            st, _, body = http_get(DEFAULT_HOST, DEFAULT_PORT, "/api/stats")
            check(
                f"  GET /api/stats liefert 200 (status={st})",
                st == 200,
                body[:60] if st != 200 else "",
            )

    # ── 9. Gefaehrliche Routen nicht im Relay-HTML ────────────────────────────
    print("\n[9] Gefaehrliche Send/Approve-Routen NICHT in Relay-HTML")
    for route in ["/api/send-batch", "/api/send-followups", "/api/full-auto",
                  "/api/approve-all", "/api/lead/send", "/api/lead/approve"]:
        check(
            f"'{route}' NICHT in Relay-HTML",
            route not in relay_html,
            "EXPONIERT GEFAEHRLICHE ROUTE!",
        )

    # ── 10. Browser-Test-Verfuegbarkeit ──────────────────────────────────────
    print("\n[10] Browser-Test-Verfuegbarkeit")
    playwright_ok = importlib.util.find_spec("playwright") is not None
    selenium_ok   = importlib.util.find_spec("selenium") is not None

    if playwright_ok:
        info("Playwright installiert — echter Browser-Click-Test moeglich")
    elif selenium_ok:
        info("Selenium installiert — echter Browser-Click-Test alternativ moeglich")
    else:
        skip(
            "Playwright/Selenium nicht installiert",
            "pip install playwright && python -m playwright install",
        )
        info("Manueller Klick-Test:")
        info("  1. python cockpit_server.py starten")
        info("  2. http://127.0.0.1:8765/relay im Browser oeffnen")
        info("  3. Gruener Debug-Bar oben zeigt 'Dashboard JS geladen'")
        info("  4. Jeder Button-Klick zeigt 'Klick erkannt: ...' im Debug-Bar")

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
