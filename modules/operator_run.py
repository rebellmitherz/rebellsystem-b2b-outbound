"""Safe Operator-Run — read-only Statusaggregator.

Liest (alle read-only, kein Schreiben):
  output/latest/monthly_report.json
  output/latest/crm_payload_preview.json
  output/latest/crm_status_report.json
  output/latest/crm_push_log.json
  output/latest/reply_queue.json
  output/latest/hot_handoffs.json
  output/latest/outreach_pipeline.json
  output/latest/ready_to_send.csv

Schreibt:
  output/latest/operator_run_report.json

══════════════════════════════════════════════════════════════════
  VERBOTEN (niemals in dieser Datei):
    SMTP / IMAP / E-Mail-Versand / Approve / Auto-Send
    Echter CRM-Push
    Pipeline-Zustandsänderungen
    Dashboard-Rewrite
══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT       = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LATEST     = OUTPUT_DIR / "latest"

OPERATOR_REPORT_FILE = LATEST / "operator_run_report.json"


# ── Hilfsfunktionen ────────────────────────────────────────────────────────────

def _load_json(path: Path) -> Any:
    """Liest JSON-Datei, None bei Fehler/Fehlen."""
    if not path.is_file():
        return None
    for enc in ("utf-8", "utf-8-sig"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def _count_csv_rows(path: Path) -> int:
    """Zählt Datenzeilen in CSV (ohne Header). 0 bei Fehler/Fehlen."""
    if not path.is_file():
        return 0
    try:
        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            next(reader, None)           # Header überspringen
            return sum(1 for _ in reader)
    except Exception:
        return 0


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Kern-Logik ─────────────────────────────────────────────────────────────────

def build_operator_run_report(
    latest: Path = LATEST,
    out_file: Path = OPERATOR_REPORT_FILE,
) -> dict[str, Any]:
    """Aggregiert alle CRM- und Outreach-Quellen zu einem Operator-Report.

    Read-only. Kein Netzwerk. Kein Push. Kein Send.
    """
    now_iso  = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []

    # ── 1. Monthly Report ─────────────────────────────────────────────────────
    mr = _load_json(latest / "monthly_report.json")
    if mr is None:
        warnings.append(
            "monthly_report.json fehlt — Werte aus anderen Quellen erhoben. "
            "Tipp: 'python mine.py --monthly-report' ausfuehren."
        )
    leads_found_total   = int((mr or {}).get("leads_found_total",   0))
    ready_to_send_total = int((mr or {}).get("ready_to_send_total", 0))
    sent_total          = int((mr or {}).get("sent_total",          0))
    replies_total       = int((mr or {}).get("replies_total",       0))

    # ready_to_send.csv als Fallback wenn monthly_report fehlt oder 0
    if ready_to_send_total == 0:
        csv_count = _count_csv_rows(latest / "ready_to_send.csv")
        if csv_count > 0:
            ready_to_send_total = csv_count

    # ── 2. CRM Payload Preview ────────────────────────────────────────────────
    crm_preview = _load_json(latest / "crm_payload_preview.json")
    if crm_preview is None:
        warnings.append(
            "crm_payload_preview.json fehlt — CRM-Zaehler auf 0. "
            "Tipp: 'python mine.py --crm-preview' ausfuehren."
        )
    crm_preview_count    = int((crm_preview or {}).get("count",           0))
    crm_push_ready_count = int((crm_preview or {}).get("push_ready_count", 0))
    crm_blocked_count    = int((crm_preview or {}).get("blocked_count",    0))

    # ── 3. CRM Status Report ──────────────────────────────────────────────────
    crm_status = _load_json(latest / "crm_status_report.json")
    if crm_status is None:
        warnings.append(
            "crm_status_report.json fehlt — CRM-Live-Push-Status unbekannt. "
            "Tipp: 'python mine.py --crm-status' ausfuehren."
        )
    crm_live_push_possible = bool((crm_status or {}).get("live_push_possible", False))
    crm_next_action        = str((crm_status or {}).get("next_action", "unknown"))

    # ── 4. Reply Queue ────────────────────────────────────────────────────────
    rq = _load_json(latest / "reply_queue.json")
    if rq is None:
        reply_queue_count = 0
    else:
        # Bevorzuge len(items) — "total" ist im Datensatz manchmal inkonsistent
        items = rq.get("items", [])
        if isinstance(items, list) and len(items) > 0:
            reply_queue_count = len(items)
        else:
            reply_queue_count = int(rq.get("total", 0))

    # ── 5. Hot Handoffs ───────────────────────────────────────────────────────
    hh = _load_json(latest / "hot_handoffs.json")
    if hh is None:
        hot_handoffs_count = 0
        warnings.append(
            "hot_handoffs.json fehlt — Hot-Handoff-Zaehler auf 0. "
            "Tipp: Outreach-Pipeline laufen lassen um Hot Handoffs zu erzeugen."
        )
    elif isinstance(hh, list):
        hot_handoffs_count = len(hh)
    else:
        # Bevorzuge echte Liste wenn vorhanden
        handoffs_list = hh.get("handoffs", [])
        if isinstance(handoffs_list, list):
            hot_handoffs_count = len(handoffs_list)
        else:
            hot_handoffs_count = int(hh.get("count", 0))

    # ── 6. Outreach Pipeline (Kontext) ────────────────────────────────────────
    pipe = _load_json(latest / "outreach_pipeline.json")
    pipeline_entries = 0
    if pipe is not None:
        entries = pipe.get("entries", [])
        pipeline_entries = len(entries) if isinstance(entries, list) else 0

    # ── 7. CRM Push Log (Kontext) ─────────────────────────────────────────────
    push_log = _load_json(latest / "crm_push_log.json")
    last_push_summary = {}
    if push_log is not None:
        last_push_summary = push_log.get("summary", {})

    # ── 8. operator_next_action — Prioritätsreihenfolge ──────────────────────
    if crm_push_ready_count > 0 and not crm_live_push_possible:
        operator_next_action = "configure_crm_env_or_keep_dry_run"
    elif crm_blocked_count > 0 and crm_push_ready_count == 0:
        operator_next_action = "review_blocked_crm_payloads"
    elif hot_handoffs_count == 0:
        operator_next_action = "generate_more_hot_handoffs"
    elif reply_queue_count > 0:
        operator_next_action = "review_replies"
    elif ready_to_send_total > 0:
        operator_next_action = "review_ready_outreach"
    else:
        operator_next_action = "no_action"

    # ── Report ────────────────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "generated_at":           now_iso,
        "mode":                   "safe_operator_run",
        # Outreach-Zahlen
        "leads_found_total":      leads_found_total,
        "ready_to_send_total":    ready_to_send_total,
        "sent_total":             sent_total,
        "replies_total":          replies_total,
        # Pipeline-Kontext
        "pipeline_entries":       pipeline_entries,
        "hot_handoffs_count":     hot_handoffs_count,
        "reply_queue_count":      reply_queue_count,
        # CRM-Zahlen
        "crm_preview_count":      crm_preview_count,
        "crm_push_ready_count":   crm_push_ready_count,
        "crm_blocked_count":      crm_blocked_count,
        "crm_live_push_possible": crm_live_push_possible,
        "crm_next_action":        crm_next_action,
        # Letzter Push-Log (Kontext)
        "last_crm_push_dry_run":  bool(last_push_summary.get("dry_run", True)),
        "last_crm_pushed_count":  int(last_push_summary.get("pushed",   0)),
        "last_crm_failed_count":  int(last_push_summary.get("failed",   0)),
        # Aktion
        "operator_next_action":   operator_next_action,
        "warnings":               warnings,
    }

    _save_json(out_file, report)
    return report


# ── CLI-Einstiegspunkt ─────────────────────────────────────────────────────────

def run_operator_run_cli(
    latest: Path = LATEST,
    out_file: Path = OPERATOR_REPORT_FILE,
) -> None:
    """CLI: baut Operator-Report, gibt ihn als lesbaren Text + JSON aus."""
    report = build_operator_run_report(latest=latest, out_file=out_file)

    print("=" * 65)
    print("  Operator Run — Safe Status Report")
    print("  (read-only | kein Send | kein CRM-Push | kein Netzwerk)")
    print("=" * 65)

    # Outreach
    print(f"  Leads gefunden     : {report['leads_found_total']}")
    print(f"  Versandbereit      : {report['ready_to_send_total']}")
    print(f"  Versendet          : {report['sent_total']}")
    print(f"  Antworten          : {report['replies_total']}")
    print(f"  Reply-Queue        : {report['reply_queue_count']}")
    print(f"  Hot Handoffs       : {report['hot_handoffs_count']}")
    print(f"  Pipeline Eintraege : {report['pipeline_entries']}")
    print()

    # CRM
    print(f"  CRM Payloads       : {report['crm_preview_count']}")
    print(f"  Push-ready         : {report['crm_push_ready_count']}")
    print(f"  Blockiert          : {report['crm_blocked_count']}")
    live = "JA  <-- ACHTUNG: echter Push moeglich!" if report['crm_live_push_possible'] else "NEIN"
    print(f"  Live-Push moeglich : {live}")
    print(f"  CRM next_action    : {report['crm_next_action']}")
    print()

    # Letzter Push-Log
    if report['last_crm_pushed_count'] > 0 or report['last_crm_failed_count'] > 0:
        dr = "Dry-Run" if report['last_crm_push_dry_run'] else "ECHTER PUSH"
        print(f"  Letzter CRM-Push   : {dr}")
        print(f"  Gepusht            : {report['last_crm_pushed_count']}")
        print(f"  Fehlgeschlagen     : {report['last_crm_failed_count']}")
        print()

    # Nächste Aktion
    print(f"  >> Operator-Aktion : {report['operator_next_action']}")
    print()

    if report['warnings']:
        print("  Warnungen:")
        for w in report['warnings']:
            print(f"    [!] {w}")
        print()

    print(f"  Report gespeichert : {out_file}")
    print("=" * 65)
    print()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run_operator_run_cli()
