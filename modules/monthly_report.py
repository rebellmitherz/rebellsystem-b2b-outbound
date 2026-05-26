"""Read-only cross-run monthly aggregation report.

Reads existing output files only. No SMTP. No IMAP. No sends. No approvals.
No pipeline state changes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

# Estimated EUR value per pipeline entry by close-potential label.
# Used only for the estimated_pipeline_value_eur field.
_POTENTIAL_EUR: dict[str, int] = {"high": 15_000, "medium": 8_000, "low": 3_000}


def _load_json(path: Path) -> Any:
    """Load JSON from path; try utf-8, then utf-8-sig, then replace. Returns None if absent/broken."""
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


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _ts_from_dirname(name: str) -> datetime | None:
    """Extract datetime from run dir names like '2026-04-25_14-22-06_...'."""
    try:
        return datetime.strptime(name[:19], "%Y-%m-%d_%H-%M-%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def build_monthly_report(days: int = 30, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Aggregate output data across runs and global files into one report dict.

    Args:
        days: rolling window in days to define the reporting period.
        output_dir: root output directory (injected for testing).

    Returns:
        Report dict with all required fields. Missing data → 0 + warning entry.
    """
    warnings: list[str] = []
    latest = output_dir / "latest"
    runs_dir = output_dir / "runs"

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    # ── Scan run directories ──────────────────────────────────────────────────
    leads_found_total = 0
    ready_to_send_total = 0
    runs_scanned = 0
    earliest_ts: datetime | None = None

    if runs_dir.is_dir():
        for run_dir in sorted(runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue

            # Derive timestamp from directory name first (most reliable), then file.
            dir_ts = _ts_from_dirname(run_dir.name)
            rr = _load_json(run_dir / "run_report.json")
            file_ts = _parse_ts((rr or {}).get("timestamp", "")) if rr else None
            run_ts = dir_ts or file_ts

            if run_ts and run_ts < cutoff:
                continue  # outside rolling window

            runs_scanned += 1
            if run_ts:
                earliest_ts = run_ts if earliest_ts is None else min(earliest_ts, run_ts)

            if rr:
                leads_found_total += int(rr.get("count_found", 0) or 0)

            sm = _load_json(run_dir / "summary.json")
            if sm:
                ready_to_send_total += int(sm.get("ready_to_send_yes_count", 0) or 0)
    else:
        warnings.append("output/runs/: Verzeichnis nicht gefunden — runs_scanned und leads_found_total auf 0")

    if runs_scanned == 0:
        warnings.append(f"Keine Run-Verzeichnisse im Zeitfenster ({days} Tage) gefunden.")

    period_start = (earliest_ts or cutoff).strftime("%Y-%m-%d")
    period_end = now.strftime("%Y-%m-%d")

    # ── Outreach pipeline ────────────────────────────────────────────────────
    pipeline_entries: list[dict] = []
    for pipeline_path in (output_dir / "outreach_pipeline.json", latest / "outreach_pipeline.json"):
        raw = _load_json(pipeline_path)
        if raw is None:
            continue
        if isinstance(raw, dict):
            pipeline_entries = raw.get("entries", [])
        elif isinstance(raw, list):
            pipeline_entries = raw
        if pipeline_entries:
            break

    if not pipeline_entries:
        warnings.append("outreach_pipeline.json: nicht gefunden oder leer — Pipeline-Felder auf 0")

    approved_total = sum(1 for e in pipeline_entries if e.get("approved_for_send"))

    # Approval mode: human explicitly approved (approved_by non-empty).
    # Autonomous mode: approved_for_send=True but approved_by is empty (future auto-path).
    approval_mode_signals_total = sum(
        1 for e in pipeline_entries
        if e.get("approved_for_send") and (e.get("approved_by") or "").strip()
    )
    autonomous_mode_signals_total = sum(
        1 for e in pipeline_entries
        if e.get("approved_for_send") and not (e.get("approved_by") or "").strip()
    )
    if autonomous_mode_signals_total == 0:
        warnings.append(
            "autonomous_mode_signals_total: 0 — autonomer Modus noch nicht aktiv "
            "(approved_for_send=True mit leerem approved_by wurde nicht gefunden). "
            "Feld ist vorbereitet für zukünftige Auto-Runs."
        )

    followups_due_total = sum(
        1 for e in pipeline_entries
        if (e.get("next_followup_at") or "").strip()
        and str(e.get("reply_status", "none")).strip() in ("none", "", "None")
        and not e.get("do_not_resend")
    )

    estimated_pipeline_value_eur = sum(
        _POTENTIAL_EUR.get(str(e.get("estimated_close_potential") or "").lower(), 0)
        for e in pipeline_entries
        if e.get("approved_for_send")
    )
    if pipeline_entries and not any(e.get("estimated_close_potential") for e in pipeline_entries):
        warnings.append(
            "estimated_pipeline_value_eur: estimated_close_potential fehlt in allen Einträgen — Wert 0 EUR"
        )

    # ── Sent log ─────────────────────────────────────────────────────────────
    sent_events: list[dict] = []
    for sent_path in (latest / "sent_log.json", output_dir / "sent_log.json"):
        raw = _load_json(sent_path)
        if raw is None:
            continue
        if isinstance(raw, dict):
            sent_events = raw.get("events", [])
        elif isinstance(raw, list):
            sent_events = raw
        if sent_events:
            break

    if not sent_events:
        warnings.append("sent_log.json: nicht gefunden oder leer — sent_total und followups_sent_total auf 0")

    sent_total = sum(
        1 for e in sent_events
        if e.get("kind") == "first_send" and e.get("ok") is True
    )
    followups_sent_total = sum(
        1 for e in sent_events
        if str(e.get("kind", "")).startswith("followup") and e.get("ok") is True
    )

    # ── Reply queue ──────────────────────────────────────────────────────────
    reply_items: list[dict] = []
    for rq_path in (latest / "reply_queue.json", output_dir / "reply_queue.json"):
        raw = _load_json(rq_path)
        if raw is None:
            continue
        if isinstance(raw, dict):
            reply_items = raw.get("items", [])
        elif isinstance(raw, list):
            reply_items = raw
        if reply_items:
            break

    if not reply_items:
        warnings.append("reply_queue.json: nicht gefunden oder leer — replies_total auf 0")

    replies_total = len(reply_items)
    appointment_ready_total = sum(1 for i in reply_items if i.get("appointment_ready"))

    if replies_total > 0 and appointment_ready_total == 0:
        warnings.append(
            "appointment_ready_total: 0 — Feld 'appointment_ready' in reply_queue nicht gesetzt. "
            "Wird korrekt befüllt, sobald Reply-Intelligence Termine erkennt."
        )

    # ── Hot handoffs ─────────────────────────────────────────────────────────
    hot_handoffs_total = 0
    for hh_path in (latest / "hot_handoffs.json", output_dir / "hot_handoffs.json"):
        raw = _load_json(hh_path)
        if raw is None:
            continue
        if isinstance(raw, dict):
            hot_handoffs_total = int(raw.get("count", len(raw.get("handoffs", []))))
        elif isinstance(raw, list):
            hot_handoffs_total = len(raw)
        break

    # ── Assemble ─────────────────────────────────────────────────────────────
    return {
        "period_start": period_start,
        "period_end": period_end,
        "runs_scanned": runs_scanned,
        "leads_found_total": leads_found_total,
        "ready_to_send_total": ready_to_send_total,
        "approved_total": approved_total,
        "sent_total": sent_total,
        "replies_total": replies_total,
        "hot_handoffs_total": hot_handoffs_total,
        "appointment_ready_total": appointment_ready_total,
        "followups_due_total": followups_due_total,
        "followups_sent_total": followups_sent_total,
        "autonomous_mode_signals_total": autonomous_mode_signals_total,
        "approval_mode_signals_total": approval_mode_signals_total,
        "estimated_pipeline_value_eur": estimated_pipeline_value_eur,
        "warnings": warnings,
        "generated_at": now.isoformat(timespec="seconds"),
    }


def run_monthly_report_cli(days: int = 30, output_dir: Path = OUTPUT_DIR) -> None:
    """CLI entry point: build report, print JSON, save to output/latest/monthly_report.json."""
    report = build_monthly_report(days=days, output_dir=output_dir)

    latest = output_dir / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    out_path = latest / "monthly_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[monthly_report] Gespeichert: {out_path}", flush=True)

    if report["warnings"]:
        print(f"\n[monthly_report] {len(report['warnings'])} Warnung(en):")
        for w in report["warnings"]:
            print(f"  [!] {w}")
