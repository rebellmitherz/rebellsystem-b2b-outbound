#!/usr/bin/env python3
"""
B2B Akquise Cockpit — Premium Interactive SPA Server.

Premium UI mit echten Action-Buttons pro Zeile, Drawer-Detailansicht,
Live-Updates, Toast-Notifications, Filter-Sidebar.
Stdlib only — keine Dependencies.

Start:
  python cockpit_server.py
  → http://127.0.0.1:8765
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote, urlparse

# ── Pfade ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
PIPELINE_JSON = OUT / "outreach_pipeline.json"
REPLY_QUEUE_JSON = OUT / "reply_queue.json"
REPLY_EVENTS_JSON = OUT / "reply_events.json"
SENT_LOG_JSON = OUT / "sent_log.json"
SEARCH_META_FILE = OUT / "_search_meta.json"
INTENT_FOCUS_DECISION_FILE = OUT / "latest" / "intent_focus_decision_report.json"
INTENT_JOB_DETAIL_LIVE_FILE = OUT / "latest" / "intent_job_detail_live_test.json"
INTENT_JOB_DETAIL_RELEVANCE_FILE = OUT / "latest" / "intent_job_detail_relevance.json"
INTENT_TARGET_PREVIEW_FILE = OUT / "latest" / "intent_target_preview_report.json"
INTENT_TARGET_PREVIEW_SCRIPT = str(ROOT / "run_intent_target_preview.py")
INTENT_LEAD_PRODUCTION_FILE = OUT / "latest" / "intent_lead_production.json"
INTENT_LEAD_PRODUCTION_SCRIPT = str(ROOT / "run_intent_lead_production.py")
INTENT_EMAIL_REVIEW_QUEUE_FILE = OUT / "latest" / "intent_email_review_queue.json"
INTENT_VERIFIED_LEADS_FILE = OUT / "latest" / "intent_verified_leads.json"
INTENT_ENRICHED_LEADS_FILE = OUT / "latest" / "intent_enriched_leads.json"
INTENT_AUTO_SEND_CANDIDATES_FILE = OUT / "latest" / "intent_auto_send_candidates.json"
INTENT_MANUAL_DECISION_MAKER_REVIEWS_FILE = OUT / "latest" / "intent_manual_decision_maker_reviews.json"
SIGNAL_SOURCE_HARVEST_POOL_FILE = OUT / "latest" / "signal_source_harvest_pool.json"
INTENT_BATCH_5_DRY_RUN_REPORT_FILE = OUT / "latest" / "intent_batch_5_dry_run_report.json"
INTENT_TO_OUTREACH_BRIDGE_REPORT_FILE = OUT / "latest" / "intent_to_outreach_bridge_report.json"
INTENT_LP_ALLOWED_SIGNALS = ("sales_hiring", "growth_expansion", "demand_generation_gap")
INTENT_LP_ALLOWED_MODES = ("preview", "approval", "auto")
INTENT_LP_HARD_MAX_LIMIT = 10

PYTHON = sys.executable
MINE = str(ROOT / "mine.py")

def _read_key_file(filename: str = "serper_key.txt") -> str:
    """Liest API-Key aus einer Key-Datei im Bot-Root (schnell wechselbar)."""
    try:
        key_file = ROOT / filename
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    # Fallback: aus passender Env-Var (SERPER_API_KEY oder TAVILY_API_KEY)
    env_map = {"serper_key.txt": "SERPER_API_KEY", "tavily_key.txt": "TAVILY_API_KEY"}
    env_key = env_map.get(filename, "")
    return os.environ.get(env_key, "") if env_key else ""

PORT = int(os.environ.get("COCKPIT_PORT", "8765"))
HOST = os.environ.get("COCKPIT_HOST", "127.0.0.1")
os.environ["REPLY_AUTO_SEND"] = "false"

# ── Search Meta ──────────────────────────────────────────────────────────────
_search_meta_lock = threading.Lock()


def _load_search_meta() -> dict:
    try:
        return json.loads(SEARCH_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_search_meta(meta: dict) -> None:
    try:
        SEARCH_META_FILE.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _get_last_search_started_at() -> str:
    return _load_search_meta().get("last_search_started_at", "")


def _set_last_search_started_at(ts: str) -> None:
    with _search_meta_lock:
        m = _load_search_meta()
        m["last_search_started_at"] = ts
        m["last_search_label"] = m.get("last_search_label", "")
        _save_search_meta(m)


def _increment_search_run() -> int:
    with _search_meta_lock:
        m = _load_search_meta()
        run = m.get("search_run", 0) + 1
        m["search_run"] = run
        _save_search_meta(m)
        return run


def _get_search_run() -> int:
    return _load_search_meta().get("search_run", 0)


def _safe_read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _intent_email_review_queue_payload() -> dict:
    data = _safe_read_json(INTENT_EMAIL_REVIEW_QUEUE_FILE)
    if not data:
        return {
            "available": False,
            "message": "Intent Email Review Queue noch nicht erzeugt.",
            "enriched_leads_loaded": 0,
            "review_items_created": 0,
            "pending": 0,
            "verified_existing": 0,
            "rejected": 0,
            "review_items": [],
            "safety": {
                "no_email_sending": True,
                "no_smtp_verification": True,
                "no_pipeline_integration": True,
            },
        }
    data = dict(data)
    data["available"] = True
    data.setdefault("review_items", [])
    data.setdefault("review_items_created", len(data.get("review_items") or []))
    data.setdefault("pending", sum(1 for i in data.get("review_items", []) if i.get("review_status") == "pending"))
    data.setdefault("verified_existing", 0)
    data.setdefault("rejected", sum(1 for i in data.get("review_items", []) if i.get("review_status") == "rejected"))
    return data


def _intent_manual_decision_maker_review_payload() -> dict:
    try:
        from run_intent_manual_decision_maker_review import load_review_items
        return load_review_items()
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "items": [],
            "count": 0,
            "completed_count": 0,
            "safety": {
                "no_send": True,
                "no_smtp": True,
                "no_pipeline_integration": True,
            },
        }


def _save_intent_manual_decision_maker_review(payload: dict) -> tuple[dict, int]:
    try:
        from run_intent_manual_decision_maker_review import save_manual_review, load_review_items
        result = save_manual_review(payload)
        if not result.get("ok"):
            return result, 400
        result["queue"] = load_review_items()
        return result, 200
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500


def _intent_domain_from_url(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.netloc or parsed.path).split("/")[0].split(":")[0].removeprefix("www.")


def _intent_sent_event_keys(events: list[dict]) -> tuple[set[str], set[str]]:
    emails: set[str] = set()
    domains: set[str] = set()
    for ev in events:
        if not isinstance(ev, dict) or ev.get("ok") is not True:
            continue
        if not str(ev.get("kind") or "").startswith(("first", "followup")):
            continue
        email = str(ev.get("to") or ev.get("email") or "").strip().lower()
        if email:
            emails.add(email)
            if "@" in email:
                domains.add(email.rsplit("@", 1)[-1])
        domain = _intent_domain_from_url(str(ev.get("website") or ev.get("company_domain") or ""))
        if domain:
            domains.add(domain)
    return emails, domains


def _pipeline_stage_bucket(entry: dict) -> str:
    stage = str(entry.get("outreach_stage") or "").strip().lower()
    reply = str(entry.get("reply_status") or "").strip().lower()
    sent_already = bool(entry.get("first_sent_at") or entry.get("sent_message_id") or stage == "sent")
    if stage in ("won", "lost"):
        return stage
    if reply and reply not in ("none", "new"):
        return "replied"
    if stage in ("followup_1", "followup_2"):
        return stage
    if sent_already:
        return "sent"
    if _is_approved(entry) and _is_unsent(entry) and str(entry.get("email") or "").strip():
        return "approved_pending_send"
    if _is_ready(entry) and _is_unsent(entry):
        return "ready"
    return stage or "new"


def _intent_pipeline_item(entry: dict) -> dict:
    return {
        "entry_key": str(entry.get("entry_key") or ""),
        "company_name": str(entry.get("company_name") or ""),
        "email": str(entry.get("email") or ""),
        "website": str(entry.get("website") or ""),
        "outreach_stage": str(entry.get("outreach_stage") or ""),
        "pipeline_bucket": _pipeline_stage_bucket(entry),
        "approved_for_send": _is_approved(entry),
        "ready_to_send": str(entry.get("ready_to_send") or ""),
        "source": str(entry.get("source") or ""),
        "sent_at": str(entry.get("sent_at") or entry.get("first_sent_at") or ""),
        "first_sent_at": str(entry.get("first_sent_at") or ""),
        "reply_status": str(entry.get("reply_status") or ""),
        "do_not_resend": bool(entry.get("do_not_resend")),
    }


def _intent_blocked_items(
    *,
    lead_production: dict,
    enriched: dict,
    review_queue: dict,
    auto_candidates: dict,
    sent_events: list[dict],
) -> list[dict]:
    out: list[dict] = []
    sent_emails, sent_domains = _intent_sent_event_keys(sent_events)

    def add(company: str, reason: str, source: str, status: str = "blocked", extra: dict | None = None) -> None:
        payload = {
            "company_name": company or "-",
            "reason": reason or "-",
            "source": source,
            "status": status,
        }
        if extra:
            payload.update(extra)
        out.append(payload)

    for lead in lead_production.get("leads") or []:
        company = str(lead.get("company_name") or "")
        status = str(lead.get("status") or "")
        if status in ("already_contacted", "discard"):
            add(company, str(lead.get("duplicate_reason") or status), "intent_lead_production", status)
            continue
        email = str(lead.get("email") or "").strip().lower()
        domain = _intent_domain_from_url(str(lead.get("website") or ""))
        if (email and email in sent_emails) or (domain and domain in sent_domains):
            add(company, "sent_or_already_contacted", "sent_log", "already_contacted")

    for lead in enriched.get("enriched_leads") or []:
        company = str(lead.get("company_name") or "")
        if lead.get("rejected_email") or lead.get("rejected_email_reason"):
            add(company, str(lead.get("rejected_email_reason") or "rejected_email"), "intent_enriched_leads", "rejected_contact", {
                "rejected_email": str(lead.get("rejected_email") or ""),
            })
        if lead.get("rejected_phone") or lead.get("rejected_phone_reason"):
            add(company, str(lead.get("rejected_phone_reason") or "rejected_phone"), "intent_enriched_leads", "rejected_contact", {
                "rejected_phone": str(lead.get("rejected_phone") or ""),
            })
        if str(lead.get("lead_quality_status") or "") == "discard":
            add(company, "discard", "intent_enriched_leads", "discard")

    for item in review_queue.get("review_items") or []:
        if str(item.get("review_status") or "") == "rejected":
            add(str(item.get("company_name") or ""), "email_candidate_rejected", "intent_email_review_queue", "rejected")

    for cand in auto_candidates.get("candidates") or []:
        if str(cand.get("auto_send_status") or "") != "auto_eligible":
            reasons = cand.get("block_reasons") or []
            add(str(cand.get("company_name") or ""), "; ".join(map(str, reasons)) or "auto_candidate_blocked", "intent_auto_send_candidates", "blocked")

    deduped: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in out:
        key = (
            str(item.get("company_name") or ""),
            str(item.get("reason") or ""),
            str(item.get("source") or ""),
            str(item.get("status") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _intent_operator_queue_payload() -> dict:
    review_queue = _intent_email_review_queue_payload()
    verified = _safe_read_json(INTENT_VERIFIED_LEADS_FILE)
    auto_candidates = _safe_read_json(INTENT_AUTO_SEND_CANDIDATES_FILE)
    enriched = _safe_read_json(INTENT_ENRICHED_LEADS_FILE)
    lead_production = _safe_read_json(INTENT_LEAD_PRODUCTION_FILE)
    batch = _safe_read_json(INTENT_BATCH_5_DRY_RUN_REPORT_FILE)
    bridge = _safe_read_json(INTENT_TO_OUTREACH_BRIDGE_REPORT_FILE)
    pipeline_entries = _load_pipeline()
    sent_events = _load_sent_events()

    review_items = list(review_queue.get("review_items") or [])
    verified_leads = list(verified.get("verified_leads") or [])
    candidates = list(auto_candidates.get("candidates") or [])
    pipeline_items = [_intent_pipeline_item(e) for e in pipeline_entries]

    pipeline_counts = {
        "ready": sum(1 for i in pipeline_items if i["pipeline_bucket"] == "ready"),
        "approved_pending_send": sum(1 for i in pipeline_items if i["pipeline_bucket"] == "approved_pending_send"),
        "sent": sum(1 for i in pipeline_items if i["pipeline_bucket"] == "sent"),
        "replied": sum(1 for i in pipeline_items if i["pipeline_bucket"] == "replied"),
        "followup_1": sum(1 for i in pipeline_items if i["pipeline_bucket"] == "followup_1"),
        "followup_2": sum(1 for i in pipeline_items if i["pipeline_bucket"] == "followup_2"),
        "lost": sum(1 for i in pipeline_items if i["pipeline_bucket"] == "lost"),
        "won": sum(1 for i in pipeline_items if i["pipeline_bucket"] == "won"),
    }
    blocked_items = _intent_blocked_items(
        lead_production=lead_production,
        enriched=enriched,
        review_queue=review_queue,
        auto_candidates=auto_candidates,
        sent_events=sent_events,
    )
    search_status = str(batch.get("search_status") or batch.get("status") or "")
    sync_status = str(bridge.get("sync_status") or "")
    warnings = []
    if "blocked" in search_status.lower():
        warnings.append("search_blocked")
    if sync_status and sync_status not in ("synced", "dry_run_no_write", "no_import"):
        warnings.append("pipeline_sync_not_ok")

    today = time.strftime("%Y-%m-%d")
    sent_today_total = sum(
        1 for ev in sent_events
        if isinstance(ev, dict)
        and ev.get("ok") is True
        and str(ev.get("kind") or "").startswith(("first", "followup"))
        and str(ev.get("ts") or "").startswith(today)
    )
    pending_review = sum(1 for i in review_items if str(i.get("review_status") or "pending") == "pending")
    auto_eligible = sum(1 for c in candidates if str(c.get("auto_send_status") or "") == "auto_eligible")

    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_sources": {
            "review_queue": str(INTENT_EMAIL_REVIEW_QUEUE_FILE),
            "verified_leads": str(INTENT_VERIFIED_LEADS_FILE),
            "auto_send_candidates": str(INTENT_AUTO_SEND_CANDIDATES_FILE),
            "enriched_leads": str(INTENT_ENRICHED_LEADS_FILE),
            "lead_production": str(INTENT_LEAD_PRODUCTION_FILE),
            "canonical_pipeline": str(PIPELINE_JSON),
            "canonical_sent_log": str(SENT_LOG_JSON),
            "batch_5_dry_run_report": str(INTENT_BATCH_5_DRY_RUN_REPORT_FILE),
            "bridge_report": str(INTENT_TO_OUTREACH_BRIDGE_REPORT_FILE),
        },
        "health": {
            "search_status": search_status or "unknown",
            "canonical_pipeline_file": str(PIPELINE_JSON),
            "pipeline_sync_status": sync_status or "unknown",
            "sent_today_total": sent_today_total,
            "pending_review_count": pending_review,
            "verified_count": len(verified_leads),
            "auto_eligible_count": auto_eligible,
            "approved_pending_send_count": pipeline_counts["approved_pending_send"],
            "sent_count": pipeline_counts["sent"],
            "warnings": warnings,
        },
        "counts": {
            "pending_review": pending_review,
            "verified": len(verified_leads),
            "auto_eligible": auto_eligible,
            "ready": pipeline_counts["ready"],
            "approved_pending_send": pipeline_counts["approved_pending_send"],
            "sent": pipeline_counts["sent"],
            "blocked_rejected_already_contacted": len(blocked_items),
        },
        "needs_email_review": review_items,
        "manually_verified": verified_leads,
        "auto_send_candidates": candidates,
        "outreach_pipeline": {
            "canonical_pipeline_file": str(PIPELINE_JSON),
            "counts": pipeline_counts,
            "items": pipeline_items,
        },
        "blocked_rejected_already_contacted": blocked_items,
        "safety": {
            "read_only": True,
            "no_email_sending": True,
            "no_smtp": True,
            "canonical_pipeline_only": True,
        },
    }


def _premium_status_tier(status: str) -> str:
    status = (status or "").strip()
    return {
        "signal_ready_company_resolved": "A",
        "signal_needs_website_resolution": "B",
        "signal_needs_manual_review": "C",
        "blocked_signal": "D",
    }.get(status, "C")


def _premium_signal_vm(row: dict) -> dict:
    status = str(row.get("status") or "").strip()
    return {
        "id": str(row.get("signal_id") or row.get("signal_source_url") or row.get("raw_url") or ""),
        "tier": _premium_status_tier(status),
        "status": status,
        "title": str(row.get("signal_title") or row.get("extracted_job_title") or row.get("raw_title") or ""),
        "company": str(row.get("extracted_company_name") or ""),
        "website": str(row.get("extracted_company_website") or ""),
        "source": str(row.get("signal_source_domain") or row.get("source_type") or ""),
        "source_type": str(row.get("source_type") or ""),
        "keyword": str(row.get("job_role_category") or row.get("industry_query") or ""),
        "city": str(row.get("city") or row.get("extracted_location") or ""),
        "industry": str(row.get("industry_query") or ""),
        "url": str(row.get("signal_source_url") or row.get("raw_url") or ""),
        "score": str(row.get("signal_score") or ""),
        "duplicate_status": str(row.get("duplicate_status") or ""),
        "already_contacted": bool(row.get("already_contacted") is True),
        "next_action": str(row.get("next_action") or ""),
        "drop_reason": str(row.get("drop_reason") or ""),
    }


def _premium_lead_vm(row: dict) -> dict:
    return {
        "company": str(row.get("company_name") or ""),
        "website": str(row.get("website") or ""),
        "name": str(row.get("decision_maker_name") or ""),
        "role": str(row.get("decision_maker_role") or ""),
        "email": str(row.get("personal_email_candidate") or row.get("personal_email") or row.get("generic_email") or ""),
        "verified": bool(row.get("personal_email_verified") is True),
        "status": str(row.get("lead_quality_status") or row.get("review_status") or ""),
        "next_action": str(row.get("next_action") or row.get("recommended_decision") or ""),
        "signal": str(row.get("intent_signal_title") or ""),
        "signal_url": str(row.get("intent_signal_source_url") or ""),
        "missing": list(row.get("missing_fields") or []),
        "risk_flags": list(row.get("risk_flags") or []),
    }


def _premium_source_counts(signals: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for item in signals:
        source = str(item.get("source") or item.get("source_type") or "unknown").strip() or "unknown"
        counts[source] = counts.get(source, 0) + 1
    return [{"source": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _premium_dashboard_payload() -> dict:
    signal_payload = _safe_read_json(SIGNAL_SOURCE_HARVEST_POOL_FILE)
    review_queue = _intent_email_review_queue_payload()
    verified_payload = _safe_read_json(INTENT_VERIFIED_LEADS_FILE)
    auto_payload = _safe_read_json(INTENT_AUTO_SEND_CANDIDATES_FILE)
    enriched_payload = _safe_read_json(INTENT_ENRICHED_LEADS_FILE)
    reply_queue = _safe_read_json(REPLY_QUEUE_JSON)
    reply_events = _safe_read_json(REPLY_EVENTS_JSON)
    sent_payload = _safe_read_json(SENT_LOG_JSON)
    pipeline_items = [_intent_pipeline_item(e) for e in _load_pipeline()]

    signals = [_premium_signal_vm(row) for row in list(signal_payload.get("signals") or []) if isinstance(row, dict)]
    review_items = list(review_queue.get("review_items") or [])
    verified_leads = list(verified_payload.get("verified_leads") or [])
    auto_candidates = list(auto_payload.get("candidates") or [])
    enriched_leads = list(enriched_payload.get("enriched_leads") or [])
    sent_events = _load_sent_events()
    replies = _load_replies()

    sent_companies = {
        str(ev.get("company_name") or ev.get("company") or "").strip().casefold()
        for ev in sent_events
        if isinstance(ev, dict)
    }
    safe_auto_candidates = [
        c for c in auto_candidates
        if str(c.get("company_name") or "").strip().casefold() not in sent_companies
    ]

    tier_counts = {tier: sum(1 for s in signals if s.get("tier") == tier) for tier in ("A", "B", "C", "D")}
    pattern_review = sum(
        1 for item in review_items
        if str(item.get("personal_email_source_type") or item.get("source_type") or "").strip() == "pattern_candidate"
        or (item.get("personal_email_verified") is not True and str(item.get("personal_email_candidate") or ""))
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_sources": {
            "signal_source_harvest_pool": str(SIGNAL_SOURCE_HARVEST_POOL_FILE),
            "intent_email_review_queue": str(INTENT_EMAIL_REVIEW_QUEUE_FILE),
            "intent_verified_leads": str(INTENT_VERIFIED_LEADS_FILE),
            "intent_auto_send_candidates": str(INTENT_AUTO_SEND_CANDIDATES_FILE),
            "intent_enriched_leads": str(INTENT_ENRICHED_LEADS_FILE),
            "canonical_pipeline": str(PIPELINE_JSON),
            "canonical_sent_log": str(SENT_LOG_JSON),
            "reply_queue": str(REPLY_QUEUE_JSON),
            "reply_events": str(REPLY_EVENTS_JSON),
        },
        "counts": {
            "signals": len(signals),
            "signal_A": tier_counts["A"],
            "signal_B": tier_counts["B"],
            "signal_C": tier_counts["C"],
            "signal_D": tier_counts["D"],
            "review_items": len(review_items),
            "pattern_review": pattern_review,
            "verified": len(verified_leads),
            "auto_candidates_not_sent": len(safe_auto_candidates),
            "enriched": len(enriched_leads),
            "pipeline": len(pipeline_items),
            "sent": len(sent_events),
            "replies": len(replies),
        },
        "signals": signals,
        "sources": _premium_source_counts(signals),
        "email_review": review_items,
        "verified_leads": [_premium_lead_vm(row) for row in verified_leads],
        "auto_send_candidates": [_premium_lead_vm(row) for row in safe_auto_candidates],
        "enriched_leads": [_premium_lead_vm(row) for row in enriched_leads],
        "pipeline": {
            "canonical_pipeline_file": str(PIPELINE_JSON),
            "items": pipeline_items,
        },
        "sent_events": sent_events[-100:],
        "reply_queue": reply_queue,
        "reply_events": reply_events,
        "replies": [_reply_summary(r) for r in replies],
        "safety": {
            "read_only": True,
            "no_smtp": True,
            "no_send": True,
            "canonical_pipeline_only": True,
            "sent_leads_not_ready": True,
            "pattern_emails_require_review": True,
        },
    }


def _claude_data_live_js() -> str:
    """Generate live data.jsx content from output/latest for the /premium Claude dashboard."""
    try:
        d = _premium_dashboard_payload()
    except Exception:
        d = {}
    counts = d.get("counts") or {}
    signals_raw = list(d.get("signals") or [])
    leads_raw = list(d.get("enriched_leads") or []) + list(d.get("verified_leads") or [])
    replies_raw = list(d.get("replies") or [])
    sources_raw = list(d.get("sources") or [])

    n_signals = int(counts.get("signals") or 0)
    n_enriched = int(counts.get("enriched") or 0)
    n_review   = int(counts.get("review_items") or 0)
    n_ready    = int(counts.get("auto_candidates_not_sent") or 0)
    n_sent     = int(counts.get("sent") or 0)
    n_replies  = int(counts.get("replies") or 0)

    def _spark(n):
        if n <= 0:
            return [0] * 10
        step = max(1, n // 10)
        return [max(0, n - step * (9 - i)) for i in range(10)]

    kpis = [
        {"key": "signals",  "label": "Signale erfasst",    "value": n_signals,  "delta": f"Tier A: {counts.get('signal_A', 0)}", "trend": "up",                              "color": "violet", "spark": _spark(n_signals)},
        {"key": "enriched", "label": "Leads angereichert", "value": n_enriched, "delta": f"+{n_enriched}",                        "trend": "up",                              "color": "blue",   "spark": _spark(n_enriched)},
        {"key": "review",   "label": "Prüfung ausstehend", "value": n_review,   "delta": f"{n_review} offen",                     "trend": "up" if n_review > 0 else "flat",  "color": "yellow", "spark": _spark(n_review)},
        {"key": "ready",    "label": "Versandbereit",      "value": n_ready,    "delta": f"{n_ready}",                            "trend": "up",                              "color": "green",  "spark": _spark(n_ready)},
        {"key": "sent",     "label": "Versendet",          "value": n_sent,     "delta": f"{n_sent}",                             "trend": "up",                              "color": "accent", "spark": _spark(n_sent)},
        {"key": "replies",  "label": "Antworten",          "value": n_replies,  "delta": f"{n_replies}",                          "trend": "up" if n_replies > 0 else "flat", "color": "red",    "spark": _spark(n_replies)},
    ]
    stages = [
        {"name": "Erkennung",    "num": n_signals,  "sub": "Rohsignale",    "active": False},
        {"name": "Anreicherung", "num": n_enriched, "sub": "in Pipeline",   "active": False},
        {"name": "Prüfung",      "num": n_review,   "sub": "ausstehend",    "active": True},
        {"name": "Freigegeben",  "num": n_ready,    "sub": "versandbereit", "active": False},
        {"name": "Versendet",    "num": n_sent,     "sub": "insgesamt",     "active": False},
    ]

    tier_score = {"A": 90, "B": 75, "C": 55, "D": 30}
    mapped_signals = []
    for i, s in enumerate(signals_raw[:20]):
        tier = str(s.get("tier") or "C")
        score_raw = s.get("score") or ""
        try:
            score = int(score_raw)
        except Exception:
            score = tier_score.get(tier, 50)
        mapped_signals.append({
            "id":      s.get("id") or f"s-{i}",
            "title":   str(s.get("title") or ""),
            "company": str(s.get("company") or ""),
            "sector":  str(s.get("industry") or s.get("keyword") or ""),
            "tier":    tier,
            "source":  str(s.get("source") or ""),
            "score":   score,
            "keyword": str(s.get("keyword") or ""),
            "time":    str(s.get("city") or ""),
        })
    if not mapped_signals:
        mapped_signals = [{"id": "s-0", "title": "Keine Signale geladen", "company": "—", "sector": "—",
                           "tier": "C", "source": "—", "score": 0, "keyword": "—", "time": "—"}]

    av_colors = ["av-5", "av-3", "av-6", "av-2", "av-4", "av-1"]
    mapped_sources = []
    for i, s in enumerate(sources_raw[:6]):
        name = str(s.get("source") or "Unbekannt")
        cnt  = int(s.get("count") or 0)
        mapped_sources.append({
            "name": name, "abbr": name[:2].upper(),
            "harvested": cnt, "conv": round(cnt * 0.15, 1),
            "color": av_colors[i % len(av_colors)],
        })
    if not mapped_sources:
        mapped_sources = [{"name": "Quellen", "abbr": "QU", "harvested": n_signals, "conv": 15.0, "color": "av-5"}]

    seen_leads: set = set()
    mapped_leads = []
    status_map = {
        "ready_for_approval": "needs-review",
        "approved": "approved",
        "verified": "verified",
        "rejected": "rejected",
        "blocked":  "blocked",
    }
    for i, l in enumerate(leads_raw):
        key = (str(l.get("company") or ""), str(l.get("name") or ""))
        if key in seen_leads:
            continue
        seen_leads.add(key)
        if len(mapped_leads) >= 20:
            break
        name    = str(l.get("name") or "")
        company = str(l.get("company") or "")
        inits   = "".join(p[0] for p in name.split()[:2]) if name else (company[:2].upper() if company else "??")
        status  = str(l.get("status") or "needs-review")
        mapped_leads.append({
            "id": f"l-{i}", "company": company,
            "website":  str(l.get("website") or ""),
            "name":     name,
            "role":     str(l.get("role") or ""),
            "email":    str(l.get("email") or "—"),
            "quality":  4 if l.get("verified") else 3,
            "tier":     "A" if l.get("verified") else "B",
            "missing":  list(l.get("missing") or []),
            "status":   status_map.get(status, "needs-review"),
            "reviewer": "—",
            "initials": inits,
            "avatar":   f"av-{(i % 8) + 1}",
            "signal":   str(l.get("signal") or ""),
            "city": "", "phone": "—",
        })
    if not mapped_leads:
        mapped_leads = [{"id": "l-0", "company": "Keine Leads", "website": "—", "name": "—", "role": "—",
                         "email": "—", "quality": 0, "tier": "C", "missing": [], "status": "needs-review",
                         "reviewer": "—", "initials": "??", "avatar": "av-1", "signal": "—", "city": "—", "phone": "—"}]

    mapped_replies = []
    cat_map = {"interested": "positive", "positive": "positive", "negative": "negative",
               "auto_reply": "auto", "auto": "auto", "ooo": "auto"}
    for i, r in enumerate(replies_raw[:10]):
        name    = str(r.get("from_name") or r.get("contact_name") or "")
        company = str(r.get("company_name") or "")
        inits   = "".join(p[0] for p in name.split()[:2]) if name else (company[:2].upper() if company else "??")
        cat = cat_map.get(str(r.get("category") or r.get("intent") or "").lower(), "human-review")
        try:
            conf = int(r.get("confidence") or r.get("intent_score") or 80)
        except Exception:
            conf = 80
        mapped_replies.append({
            "id":         str(r.get("entry_key") or f"r-{i}"),
            "from":       str(r.get("from_email") or r.get("reply_from") or ""),
            "name":       name or company,
            "company":    company,
            "subject":    str(r.get("subject") or ""),
            "category":   cat,
            "confidence": conf,
            "preview":    str(r.get("body_snippet") or r.get("preview") or ""),
            "time":       str(r.get("received_at") or ""),
            "initials":   inits,
            "avatar":     f"av-{(i % 8) + 1}",
        })

    data = {
        "kpis": kpis, "stages": stages,
        "signals": mapped_signals, "sources": mapped_sources,
        "blocked": [], "leads": mapped_leads, "replies": mapped_replies,
    }
    ts = datetime.now(timezone.utc).isoformat()
    return (
        f"// Live-Daten generiert: {ts}\n"
        f"const DATA = {json.dumps(data, ensure_ascii=False, default=str)};\n"
        "function avatarClass(seed) {\n"
        "  const k = ((seed.charCodeAt(0) || 0) + (seed.charCodeAt(1) || 0)) % 8 + 1;\n"
        "  return 'av-' + k;\n"
        "}\n"
        "window.DATA = DATA;\n"
        "window.avatarClass = avatarClass;\n"
    )


def _recount_intent_email_review_queue(queue: dict) -> dict:
    items = list(queue.get("review_items") or [])
    queue["review_items_created"] = len(items)
    queue["pending"] = sum(1 for i in items if i.get("review_status") == "pending")
    queue["rejected"] = sum(1 for i in items if i.get("review_status") == "rejected")
    verified_data = _safe_read_json(INTENT_VERIFIED_LEADS_FILE)
    queue["verified_existing"] = len(verified_data.get("verified_leads") or [])
    return queue


def _verified_lead_from_review_item(item: dict) -> dict:
    return {
        "review_id": str(item.get("review_id") or ""),
        "company_name": str(item.get("company_name") or ""),
        "website": str(item.get("website") or ""),
        "decision_maker_name": str(item.get("decision_maker_name") or ""),
        "decision_maker_role": str(item.get("decision_maker_role") or ""),
        "personal_email": str(item.get("personal_email_candidate") or ""),
        "personal_email_candidate": str(item.get("personal_email_candidate") or ""),
        "personal_email_verified": True,
        "generic_email": str(item.get("generic_email") or ""),
        "phone": str(item.get("phone") or ""),
        "intent_signal_title": str(item.get("intent_signal_title") or ""),
        "intent_signal_source_url": str(item.get("intent_signal_source_url") or ""),
        "email_subject": str(item.get("email_subject") or ""),
        "email_body": str(item.get("email_body") or ""),
        "followup_1": str(item.get("followup_1") or ""),
        "followup_2": str(item.get("followup_2") or ""),
        "lead_quality_status": "ready_for_approval",
        "next_action": "approve_for_send",
        "ready_for_approval": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verification_method": "manual_dashboard_review",
    }


def _apply_intent_email_review_decision(review_id: str, decision: str) -> tuple[dict, int]:
    review_id = (review_id or "").strip()
    decision = (decision or "").strip().lower()
    if not review_id:
        return {"error": "review_id_required"}, 400
    if decision not in ("verified", "rejected"):
        return {"error": "decision_invalid"}, 400

    queue = _safe_read_json(INTENT_EMAIL_REVIEW_QUEUE_FILE)
    items = list(queue.get("review_items") or [])
    item = next((i for i in items if str(i.get("review_id") or "") == review_id), None)
    if not item:
        return {"error": "review_item_not_found"}, 404

    now = datetime.now(timezone.utc).isoformat()
    item["decided_at"] = now
    item["decision_source"] = "dashboard_manual_review"
    if decision == "verified":
        item["review_status"] = "verified"
        item["recommended_decision"] = "verified"
        item["personal_email_verified"] = True
        item["lead_quality_status"] = "ready_for_approval"
        item["next_action"] = "approve_for_send"

        verified = _safe_read_json(INTENT_VERIFIED_LEADS_FILE)
        verified_leads = list(verified.get("verified_leads") or [])
        vlead = _verified_lead_from_review_item(item)
        existing_idx = next((idx for idx, lead in enumerate(verified_leads)
                             if str(lead.get("review_id") or "") == review_id), None)
        if existing_idx is None:
            verified_leads.append(vlead)
        else:
            verified_leads[existing_idx] = vlead
        verified.update({
            "status": "ok",
            "updated_at": now,
            "verified_existing": len(verified_leads),
            "ready_for_approval": len(verified_leads),
            "verified_leads": verified_leads,
            "safety": {
                "no_email_sending": True,
                "no_smtp_verification": True,
                "no_pipeline_integration": True,
            },
        })
        _safe_write_json(INTENT_VERIFIED_LEADS_FILE, verified)
    else:
        item["review_status"] = "rejected"
        item["recommended_decision"] = "reject"
        item["personal_email_verified"] = False
        item["lead_quality_status"] = "discard"
        item["next_action"] = "discard_email_candidate"
        verified = _safe_read_json(INTENT_VERIFIED_LEADS_FILE)
        verified_leads = [
            lead for lead in list(verified.get("verified_leads") or [])
            if str(lead.get("review_id") or "") != review_id
        ]
        verified.update({
            "status": "ok",
            "updated_at": now,
            "verified_existing": len(verified_leads),
            "ready_for_approval": len(verified_leads),
            "verified_leads": verified_leads,
            "safety": {
                "no_email_sending": True,
                "no_smtp_verification": True,
                "no_pipeline_integration": True,
            },
        })
        _safe_write_json(INTENT_VERIFIED_LEADS_FILE, verified)

    queue["status"] = "ok"
    queue["updated_at"] = now
    queue["review_items"] = items
    queue.setdefault("safety", {})
    queue["safety"].update({
        "no_email_sending": True,
        "no_smtp_verification": True,
        "no_pipeline_integration": True,
    })
    queue = _recount_intent_email_review_queue(queue)
    _safe_write_json(INTENT_EMAIL_REVIEW_QUEUE_FILE, queue)
    return {"ok": True, "decision": decision, "item": item, "queue": queue}, 200


def _intent_preview_payload() -> dict:
    decision = _safe_read_json(INTENT_FOCUS_DECISION_FILE)
    live = _safe_read_json(INTENT_JOB_DETAIL_LIVE_FILE)
    relevance = _safe_read_json(INTENT_JOB_DETAIL_RELEVANCE_FILE)
    target = _safe_read_json(INTENT_TARGET_PREVIEW_FILE)

    # Target Preview Report immer lesen, auch wenn alte Preview-Dateien fehlen
    target_preview_report = None
    if target:
        target_results = list(target.get("results") or [])
        target_candidates = []
        for r in target_results:
            target_candidates.append({
                "company": str(r.get("company_name") or "-"),
                "fit_status": str(r.get("fit_status") or ""),
                "score": float(r.get("fit_score") or 0),
                "next_action": str(r.get("next_action") or ""),
                "source_url": str(r.get("url") or ""),
            })
        target_preview_report = {
            "available": True,
            "queries_used": int(target.get("queries_used") or 0),
            "raw_results": int(target.get("raw_results") or 0),
            "unique_job_detail_pages": int(target.get("unique_job_detail_pages") or 0),
            "fetched_details": int(target.get("fetched_details") or 0),
            "resolved_companies": int(target.get("resolved_companies") or 0),
            "target_fit": int(target.get("target_fit") or 0),
            "maybe_fit": int(target.get("maybe_fit") or 0),
            "discard": int(target.get("discard") or 0),
            "candidates": target_candidates,
        }

    has_classic_preview = bool(decision or live or relevance)
    has_target_preview = bool(target_preview_report)
    if not has_classic_preview and not has_target_preview:
        return {
            "available": False,
            "message": "Intent Preview noch nicht erzeugt.",
            "recommended_default_focus": "",
            "focus_scores": {},
            "job_detail_summary": {},
            "job_detail_raw_result_count": 0,
            "top_job_detail_urls": [],
            "note": "Preview only \u2013 noch nicht in normale Lead-Pipeline integriert.",
            "relevance_summary": None,
            "relevance_fetch_candidates": [],
            "target_preview_report": None,
        }

    results = list(live.get("results") or [])
    top_job_detail_urls = []
    for item in results:
        if item.get("portal_url_type") == "job_detail_page":
            top_job_detail_urls.append({
                "url": str(item.get("url") or ""),
                "title": str(item.get("title") or ""),
                "portal_domain": str(item.get("portal_domain") or ""),
            })
        if len(top_job_detail_urls) >= 5:
            break

    relevance_summary = None
    relevance_fetch_candidates = []
    if relevance:
        counts = dict(relevance.get("counts") or {})
        relevance_summary = {
            "total_job_detail_pages": int(relevance.get("total_job_detail_pages") or 0),
            "relevant": int(counts.get("relevant") or 0),
            "maybe_relevant": int(counts.get("maybe_relevant") or 0),
            "needs_review": int(counts.get("needs_review") or 0),
            "irrelevant": int(counts.get("irrelevant") or 0),
            "fetch_detail_count": int(counts.get("fetch_detail") or 0),
            "review_count": int(counts.get("review") or 0),
            "discard_count": int(counts.get("discard") or 0),
        }
        for item in (relevance.get("results") or []):
            if item.get("recommended_next_action") == "fetch_detail":
                relevance_fetch_candidates.append({
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                    "relevance_score": float(item.get("relevance_score") or 0),
                    "relevance_status": str(item.get("relevance_status") or ""),
                    "recommended_next_action": str(item.get("recommended_next_action") or ""),
                    "relevance_reasons": list(item.get("relevance_reasons") or []),
                    "rejection_reasons": list(item.get("rejection_reasons") or []),
                })

    return {
        "available": True,
        "message": "",
        "recommended_default_focus": str(decision.get("recommended_default_focus") or ""),
        "focus_scores": dict(decision.get("focus_scores") or {}),
        "job_detail_summary": dict(live.get("classification_counts") or {}),
        "job_detail_raw_result_count": int(live.get("raw_result_count") or 0),
        "top_job_detail_urls": top_job_detail_urls,
        "note": "Preview only \u2013 noch nicht in normale Lead-Pipeline integriert.",
        "relevance_summary": relevance_summary,
        "relevance_fetch_candidates": relevance_fetch_candidates,
        "target_preview_report": target_preview_report,
    }


def _intent_lead_production_payload() -> dict:
    data = _safe_read_json(INTENT_LEAD_PRODUCTION_FILE)
    if not data:
        return {
            "available": False,
            "message": "Intent Lead Production noch nicht erzeugt.",
            "loaded_candidates": 0,
            "normalized_leads": 0,
            "ready_for_approval": 0,
            "needs_enrichment": 0,
            "discard": 0,
            "requested_limit": 0,
            "effective_limit": 0,
            "industry": "",
            "city": "",
            "signal_type": "",
            "mode_requested": "",
            "mode_effective": "",
            "auto_send_disabled": False,
            "refreshed": False,
            "duration_seconds": 0,
            "target_preview_exit_code": None,
            "outreach_preview_exit_code": None,
            "source_files_used": [],
            "started_at": "",
            "finished_at": "",
            "leads": [],
        }
    return {
        "available": True,
        "message": "",
        "generated_at": str(data.get("generated_at") or ""),
        "loaded_candidates": int(data.get("loaded_candidates") or 0),
        "normalized_leads": int(data.get("normalized_leads") or 0),
        "ready_for_approval": int(data.get("ready_for_approval") or 0),
        "needs_enrichment": int(data.get("needs_enrichment") or 0),
        "discard": int(data.get("discard") or 0),
        "requested_limit": int(data.get("requested_limit") or data.get("limit") or 0),
        "effective_limit": int(data.get("effective_limit") or data.get("limit") or 0),
        "industry": str(data.get("industry") or ""),
        "city": str(data.get("city") or ""),
        "signal_type": str(data.get("signal_type") or ""),
        "mode_requested": str(data.get("mode_requested") or data.get("mode") or ""),
        "mode_effective": str(data.get("mode_effective") or data.get("mode") or ""),
        "auto_send_disabled": bool(data.get("auto_send_disabled") or False),
        "refreshed": bool(data.get("refreshed") or False),
        "duration_seconds": float(data.get("duration_seconds") or 0),
        "target_preview_exit_code": data.get("target_preview_exit_code"),
        "outreach_preview_exit_code": data.get("outreach_preview_exit_code"),
        "source_files_used": list(data.get("source_files_used") or []),
        "started_at": str(data.get("started_at") or ""),
        "finished_at": str(data.get("finished_at") or ""),
        "leads": [
            {
                "company_name": str(ld.get("company_name") or "-"),
                "website": str(ld.get("website") or ""),
                "industry": str(ld.get("industry") or ""),
                "city_region": str(ld.get("city_region") or ""),
                "intent_signal_type": str(ld.get("intent_signal_type") or ""),
                "intent_signal_source_url": str(ld.get("intent_signal_source_url") or ""),
                "intent_signal_title": str(ld.get("intent_signal_title") or ""),
                "signal_reason": str(ld.get("signal_reason") or ""),
                "decision_maker_name": str(ld.get("decision_maker_name") or ""),
                "decision_maker_role": str(ld.get("decision_maker_role") or ""),
                "email": str(ld.get("email") or ""),
                "email_type": str(ld.get("email_type") or ""),
                "phone": str(ld.get("phone") or ""),
                "linkedin_url": str(ld.get("linkedin_url") or ""),
                "contact_quality": str(ld.get("contact_quality") or ""),
                "outreach_angle": str(ld.get("outreach_angle") or ""),
                "recommended_first_line": str(ld.get("recommended_first_line") or ""),
                "email_subject": str(ld.get("email_subject") or ""),
                "email_body": str(ld.get("email_body") or ""),
                "followup_1": str(ld.get("followup_1") or ""),
                "followup_2": str(ld.get("followup_2") or ""),
                "next_action": str(ld.get("next_action") or ""),
                "status": str(ld.get("status") or ""),
                "missing_fields": list(ld.get("missing_fields") or []),
            }
            for ld in (data.get("leads") or [])
        ],
    }


# ── Job-Tracking ─────────────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_log_buffer: deque[str] = deque(maxlen=500)
_periodic_sync_active: bool = False
_periodic_sync_lock = threading.Lock()


def _log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    _log_buffer.append(line)


def _start_job(name: str, cmd: list[str], post_cmds: list[list[str]] | None = None, periodic_sync: bool = False) -> str:
    job_id = uuid.uuid4().hex[:8]
    is_search = bool(periodic_sync)
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id, "name": name, "cmd": " ".join(cmd),
            "status": "running", "started_at": time.strftime("%H:%M:%S"),
            "ended_at": None, "exit_code": None,
            "stdout_tail": "", "stderr_tail": "",
            "progress_msg": "Starte...",
            "progress_pct": 0,
            "is_search": is_search,
        }
    
    # Periodischer Sync-Thread: läuft parallel zum Mining und merged
    # neue Leads alle 5s in die Pipeline, damit das Dashboard
    # inkrementell Ergebnisse sieht.
    if periodic_sync:
        sync_stop = threading.Event()
        def _periodic_syncer():
            _log(f"[sync] periodisch gestartet für {name}")
            while not sync_stop.is_set():
                sync_stop.wait(5.0)
                if sync_stop.is_set():
                    break
                try:
                    prc = subprocess.run(
                        [PYTHON, MINE, "--outreach", "sync"],
                        cwd=str(ROOT), capture_output=True,
                        text=True, encoding="utf-8", errors="replace",
                        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                             "SERPER_API_KEY": _read_key_file(),
                             "TAVILY_API_KEY": _read_key_file("tavily_key.txt")},
                        timeout=60,
                    )
                    if prc.returncode != 0:
                        _log(f"[sync] periodisch fehlgeschlagen (exit={prc.returncode})")
                except Exception as e:
                    _log(f"[sync] periodisch Fehler: {e}")
            _log(f"[sync] periodisch beendet für {name}")
        threading.Thread(target=_periodic_syncer, daemon=True).start()
    else:
        sync_stop = None

    # LINKEDIN_SERP_RESOLVE=0: kein DDG-Lookup pro Lead während Mining (war Hauptursache
    # für 4-min Stille — 15+ DDG-Requests × ~10s je Request = Scraping-Block).
    # LinkedIn-URLs werden nach dem Mining via LinkedIn-Tab separat recherchiert.
    _job_env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "LOG_LEVEL": "INFO",
                "SERPER_API_KEY": _read_key_file(),
                "TAVILY_API_KEY": _read_key_file("tavily_key.txt"),
                "LINKEDIN_SERP_RESOLVE": "0"}

    def _run_cmd(c: list[str], stdout_lines: list[str], started: float) -> int:
        """Führt einen einzelnen Subprocess aus, streamt Output live ins Job-Dict."""
        try:
            proc = subprocess.Popen(
                c, cwd=str(ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, env=_job_env,
            )
        except Exception as e:
            with _jobs_lock:
                _jobs[job_id]["stderr_tail"] = f"spawn error: {e}"
                _jobs[job_id]["progress_msg"] = f"Fehler: {e}"
            _log(f"[!] {name} SPAWN ERROR: {e}")
            return 1
        last_update = 0.0
        for raw in proc.stdout or []:
            ln = raw.rstrip("\n")
            stdout_lines.append(ln)
            if len(stdout_lines) > 600:
                stdout_lines.pop(0)
            now = time.time()
            if now - last_update >= 0.4 and ln.strip():
                last_update = now
                with _jobs_lock:
                    j = _jobs.get(job_id)
                    if j is not None:
                        j["progress_msg"] = ln[:200]
                        j["stdout_tail"] = "\n".join(stdout_lines)[-3000:]
                        elapsed = now - started
                        j["progress_pct"] = min(95, int(elapsed / 4))
        try:
            return proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            try: proc.kill()
            except Exception: pass
            return -9

    def _runner():
        _log(f"[>] {name}")
        stdout_lines: list[str] = []
        started = time.time()
        rc = _run_cmd(cmd, stdout_lines, started)
        # Periodischen Sync stoppen, falls aktiv
        if sync_stop is not None:
            sync_stop.set()
        # Letzten finalen Sync nach Mining (falls periodischer Sync aktiv war)
        if periodic_sync:
            _log(f"[>] {name} finaler Sync nach Mining")
            with _jobs_lock:
                j = _jobs.get(job_id)
                if j is not None:
                    j["progress_msg"] = "Finaler Sync..."
            try:
                prc = subprocess.run(
                    [PYTHON, MINE, "--outreach", "sync"],
                    cwd=str(ROOT), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", env=_job_env,
                    timeout=120,
                )
                for ln in (prc.stdout + prc.stderr).split("\n"):
                    if ln.strip():
                        stdout_lines.append(ln)
            except Exception as e:
                _log(f"[!] {name} finaler Sync Fehler: {e}")
        # Nach erfolgreichem Haupt-Job: optionale Post-Steps (z.B. auto-sync)
        if rc == 0 and post_cmds:
            for i, pcmd in enumerate(post_cmds):
                step_label = f"Sync ({i+1}/{len(post_cmds)})"
                _log(f"[>] {name} post-step: {' '.join(pcmd)}")
                with _jobs_lock:
                    j = _jobs.get(job_id)
                    if j is not None:
                        j["progress_msg"] = step_label + "..."
                stdout_lines.append(f"[post] {step_label}")
                prc = subprocess.run(
                    pcmd, cwd=str(ROOT), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", env=_job_env,
                )
                for ln in (prc.stdout + prc.stderr).split("\n"):
                    if ln.strip():
                        stdout_lines.append(ln)
                if prc.returncode != 0:
                    rc = prc.returncode
                    _log(f"[!] {name} post-step failed (exit={prc.returncode})")
                    break
        with _jobs_lock:
            j = _jobs[job_id]
            j["status"] = "ok" if rc == 0 else ("timeout" if rc == -9 else "error")
            j["ended_at"] = time.strftime("%H:%M:%S")
            j["exit_code"] = rc
            j["stdout_tail"] = "\n".join(stdout_lines)[-3000:]
            j["stderr_tail"] = ""
            j["progress_pct"] = 100 if rc == 0 else j.get("progress_pct", 0)
            j["progress_msg"] = "Fertig" if rc == 0 else f"Beendet (exit={rc})"
        _log(f"[{'OK' if rc == 0 else '!'}] {name} (exit={rc})")

    threading.Thread(target=_runner, daemon=True).start()
    return job_id


# ── Daten-Loader ─────────────────────────────────────────────────────────────

def _load_pipeline() -> list[dict]:
    try:
        d = json.loads(PIPELINE_JSON.read_text(encoding="utf-8"))
        return d.get("entries", []) if isinstance(d, dict) else (d or [])
    except Exception:
        return []


def _load_replies() -> list[dict]:
    try:
        d = json.loads(REPLY_QUEUE_JSON.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d.get("items") or list(d.values())
        return d or []
    except Exception:
        return []


def _load_sent_events() -> list[dict]:
    try:
        d = json.loads(SENT_LOG_JSON.read_text(encoding="utf-8"))
        return d.get("events", []) if isinstance(d, dict) else (d or [])
    except Exception:
        return []


def _is_unsent(e: dict) -> bool:
    """Wirklich noch nie versendet?"""
    return not (e.get("first_sent_at") or e.get("sent_message_id")) and not e.get("do_not_resend")


def _is_ready(e: dict) -> bool:
    """Vom Preview als bereit markiert (kann noch unapproved sein)."""
    return str(e.get("ready_to_send", "")).lower() in ("1", "true", "yes")


def _is_approved(e: dict) -> bool:
    """Vom User wirklich freigegeben für den Versand."""
    v = e.get("approved_for_send")
    if isinstance(v, bool):
        return v
    return str(v or "").lower() in ("1", "true", "yes")


# ── Research-Link-Builder & LinkedIn-Texte ───────────────────────────────────

_LI_ORIGIN = "GLOBAL_SEARCH_HEADER"


def _domain_from_website(url: str) -> str:
    """Extrahiert die Hauptdomain aus einer URL ohne tld. zb. https://acme.de → acme"""
    s = (url or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0]
    s = s.replace("www.", "").strip()
    if not s:
        return ""
    parts = s.split(".")
    return parts[0] if parts else s


def _company_label(e: dict) -> str:
    """Bevorzugt sauberen Firmennamen, fällt auf Domain-Label zurück."""
    raw = (e.get("company_name") or e.get("company_display") or "").strip()
    if raw and len(raw) > 2 and not raw.lower() in ("homepage", "startseite", "—"):
        return raw
    dom = _domain_from_website(e.get("website") or "")
    return dom or raw


def _contact_name_from_lead(e: dict) -> str:
    for key in ("contact_full_name", "contact_person_clean", "managing_director", "contact_name"):
        v = (e.get(key) or "").strip()
        if v and len(v) > 2:
            return v
    fn = (e.get("contact_first_name") or "").strip()
    ln = (e.get("contact_last_name") or "").strip()
    if fn or ln:
        return f"{fn} {ln}".strip()
    return ""


def _is_quality_contact_name(name: str) -> bool:
    """True nur, wenn Name plausibel als echte Person durchgehen kann.
    Filtert UI-/CMS-/Rollen-/Platzhalter-Fragmente raus, damit LinkedIn-Suchen
    nicht mit Müll wie 'Impressum', 'Geschäftsführer', 'Datenschutz' geflutet werden.
    """
    n = (name or "").strip()
    if len(n) < 4:
        return False
    low = n.lower()
    # Harte Block-Tokens (Rollen, UI-Artefakte, Generics)
    bad_tokens = (
        "impressum", "datenschutz", "kontakt", "ansprechpartner",
        "geschäftsführer", "geschaeftsfuehrer", "inhaber", "gf ",
        "ceo", "vorstand", "redaktion", "team", "unternehmen",
        "cookies", "agb", "rechtlich", "startseite", "homepage",
        "info ", "vertrieb", "support", "service", "kundenbetreuung",
        "anrede", "vorname", "nachname", "platzhalter",
    )
    for t in bad_tokens:
        if t in low:
            return False
    # Mindestens 2 Worte (Vor + Nach) ODER ein Wort mit ≥4 Zeichen Großbuchstaben-Anfang
    parts = [p for p in n.split() if len(p) >= 2]
    if len(parts) < 2:
        return False
    # Mindestens 60% der Zeichen müssen Buchstaben sein
    letters = sum(1 for c in n if c.isalpha())
    if letters < int(len(n) * 0.6):
        return False
    return True


def _research_links(e: dict) -> dict:
    """Erzeugt klickfertige Such-URLs — radikal reduziert auf verlässliche Pfade.

    PRIMÄR (immer wenn Daten reichen):
      - website_direct  → direkt zur Firmenwebsite
      - g_person_li     → site:linkedin.com/in "{Name}"  (sehr präzise)
      - g_company       → "{Firma}" + Stadt + Branche
      - li_person       → LinkedIn People-Suche (NUR wenn Kontaktname plausibel)

    FALLBACK (sekundär — nur falls Primär nicht reicht):
      - g_gf            → "{Firma}" Geschäftsführer (NUR wenn KEIN Kontaktname)
      - li_company      → LinkedIn-Firma (direkter Link bevorzugt)
      - g_impressum     → "{Firma}" Impressum

    BEWUSST ENTFERNT (zu viele Fehltreffer):
      - impressum_direct  (60%+ 404s — Pfad geraten)
      - li_person_role    (dupliziert li_person)
      - g_inhaber         (synonym zu g_gf, doppelt)
      - g_person ohne LI  (zu generisch; flutet mit fremden Personen)
    """
    company = _company_label(e)
    contact = _contact_name_from_lead(e)
    city = (e.get("city") or e.get("city_detected") or "").strip()
    website = (e.get("website") or "").strip()
    domain = _domain_from_website(website)
    industry = (e.get("industry") or "").strip()

    def li_people(q: str) -> str:
        return f"https://www.linkedin.com/search/results/people/?keywords={quote(q)}&origin={_LI_ORIGIN}"

    def li_company_url(q: str) -> str:
        return f"https://www.linkedin.com/search/results/companies/?keywords={quote(q)}&origin={_LI_ORIGIN}"

    def gsearch(q: str) -> str:
        return f"https://www.google.com/search?q={quote(q)}"

    links: dict[str, str] = {}

    # Plausibilitäts-Gate für LinkedIn-Personensuchen
    contact_ok = _is_quality_contact_name(contact)
    company_ok = bool(company and len(company) > 2 and company.lower() not in ("homepage", "startseite", "—"))

    # ════════════════════════════ PRIMÄR ════════════════════════════
    # 1) Website direkt (oft der schnellste Weg zur Wahrheit)
    if website:
        site_url = website if website.startswith("http") else f"https://{website}"
        links["website_direct"] = site_url

    # 2) Google → LinkedIn Person  (site:linkedin.com/in — höchste Präzision)
    if contact_ok:
        if company_ok:
            links["g_person_li"] = gsearch(f'site:linkedin.com/in "{contact}" "{company}"')
        else:
            links["g_person_li"] = gsearch(f'site:linkedin.com/in "{contact}"')

    # 3) Google: Firma allgemein  (verlässlich; Disambiguierung über Stadt+Branche)
    if company_ok:
        comp_q = company
        if city:
            comp_q += f" {city}"
        if industry and industry.lower() not in comp_q.lower():
            comp_q += f" {industry}"
        links["g_company"] = gsearch(comp_q)
    elif domain:
        links["g_company"] = gsearch(f"{domain} {city}".strip())

    # 4) LinkedIn People-Suche  — NUR wenn Name plausibel UND Firma vorhanden
    if contact_ok and company_ok:
        links["li_person"] = li_people(f"{contact} {company}")
    # Falls bereits aufgelöste LinkedIn-Person-URL existiert: bevorzugen
    if e.get("linkedin_person_url"):
        links["li_person"] = e["linkedin_person_url"]

    # ═══════════════════════════ FALLBACK ═══════════════════════════
    # 5) GF-Suche  — NUR wenn KEIN Kontaktname (sonst doppelt zu g_person_li)
    if not contact_ok and company_ok:
        gf_q = f'"{company}" Geschäftsführer'
        if city:
            gf_q += f" {city}"
        links["g_gf"] = gsearch(gf_q)

    # 6) LinkedIn Firma  — direkter Link bevorzugt; sonst Suche nur wenn Firma plausibel
    if e.get("linkedin_company_url_clean"):
        links["li_company"] = e["linkedin_company_url_clean"]
    elif e.get("linkedin_company_url"):
        links["li_company"] = e["linkedin_company_url"]
    elif company_ok:
        links["li_company"] = li_company_url(company)

    # 7) Impressum-Suche  — verlässlicher als geratener Direktpfad
    if company_ok:
        links["g_impressum"] = gsearch(f'"{company}" Impressum')
    elif website:
        links["g_impressum"] = gsearch(f"site:{website} impressum")

    return links


def _li_copy_texts(e: dict) -> dict:
    """3 Copy-Paste-Vorlagen pro Lead: Connection-Request, 1st-DM, Follow-up."""
    company = _company_label(e) or "Ihre Firma"
    contact = _contact_name_from_lead(e)
    first = contact.split()[0] if contact else ""
    industry = (e.get("industry") or "").strip()

    anrede_du = first if first else "kurz"
    anrede_sie = f"Herr/Frau {contact.split()[-1]}" if contact else "Sie"

    branche_zusatz = f" im Bereich {industry}" if industry else ""

    cr = (
        f"Hallo {first or 'zusammen'}, "
        f"ich schaue mir gerade {company}{branche_zusatz} an und würde mich gerne vernetzen — "
        f"vielleicht ergibt sich ein Austausch."
    )[:300]  # LinkedIn-Connect-Limit

    dm = (
        f"Hallo {first or 'zusammen'},\n\n"
        f"danke für die Vernetzung! Kurz zum Hintergrund: "
        f"Wir arbeiten mit Unternehmen wie {company}{branche_zusatz} an automatisierter B2B-Akquise — "
        f"konkret: Zielkunden finden, persönlich anschreiben und Antworten klassifizieren.\n\n"
        f"Wäre 15 Min nächste Woche spannend für Sie/dich?\n\n"
        f"Viele Grüße"
    )

    fu = (
        f"Hallo {first or 'nochmal'},\n\n"
        f"ich wollte nochmal kurz nachhaken — falls die letzte Nachricht in der Flut untergegangen ist. "
        f"Wir hatten überlegt, ob ein 15-Min-Austausch zu B2B-Akquise spannend wäre.\n\n"
        f"Falls aktuell schlecht passt, einfach kurz Bescheid — kein Problem.\n\n"
        f"Viele Grüße"
    )

    return {"connect": cr, "dm": dm, "followup": fu}


# ── LinkedIn-Status persistieren ─────────────────────────────────────────────

LI_STATUS_VALUES = ("todo", "found", "connect_sent", "connected", "dm_sent",
                    "replied", "meeting", "skip")


def _set_linkedin_status(entry_key: str, status: str, note: str = "") -> bool:
    """Schreibt linkedin_status direkt in outreach_pipeline.json zurück."""
    if status not in LI_STATUS_VALUES:
        return False
    try:
        d = json.loads(PIPELINE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return False
    if isinstance(d, dict):
        entries = d.get("entries", [])
    else:
        entries = d or []
    found = False
    for e in entries:
        if e.get("entry_key") == entry_key:
            e["linkedin_status"] = status
            e["linkedin_status_at"] = time.strftime("%Y-%m-%d %H:%M")
            if note:
                e["linkedin_note"] = note
            found = True
            break
    if not found:
        return False
    if isinstance(d, dict):
        d["entries"] = entries
        out = d
    else:
        out = entries
    try:
        PIPELINE_JSON.write_text(
            json.dumps(out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def _get_senders_status() -> list:
    """Gibt alle konfigurierten Sender mit heutigem Zähler zurück."""
    from dotenv import dotenv_values
    env_path = ROOT / ".env"
    # Env-Werte direkt aus Datei lesen (aktuellste Werte)
    env = dotenv_values(str(env_path)) if env_path.exists() else {}
    # Fallback auf os.environ
    def _ev(key: str) -> str:
        return (env.get(key) or os.environ.get(key) or "").strip()

    try:
        max_slots = max(1, min(20, int(_ev("OUTREACH_SENDER_MAX_SLOTS") or "3")))
    except ValueError:
        max_slots = 3

    today = time.strftime("%Y-%m-%d")
    # Heutige Sends aus sent_log lesen
    sent_today_by: dict[str, int] = {}
    try:
        log_data = json.loads(SENT_LOG_JSON.read_text(encoding="utf-8")) if SENT_LOG_JSON.exists() else []
        if isinstance(log_data, dict):
            log_data = log_data.get("events", [])
        for ev in (log_data if isinstance(log_data, list) else []):
            if not isinstance(ev, dict): continue
            if ev.get("ok") is not True: continue
            if ev.get("kind") not in ("first_send", "followup_1_send", "followup_2_send"): continue
            ts = str(ev.get("ts") or "")
            if not ts.startswith(today): continue
            su = (ev.get("sender_email") or "").strip()
            if su:
                sent_today_by[su] = sent_today_by.get(su, 0) + 1
    except Exception:
        pass

    senders = []
    for i in range(1, max_slots + 1):
        user = _ev(f"OUTREACH_SENDER_{i}_USER")
        if not user:
            continue
        try:
            limit = max(0, int(_ev(f"OUTREACH_SENDER_{i}_DAILY_LIMIT") or "5"))
        except ValueError:
            limit = 5
        try:
            weight = max(1, min(20, int(_ev(f"OUTREACH_SENDER_{i}_WEIGHT") or "1")))
        except ValueError:
            weight = 1
        sent_t = sent_today_by.get(user, 0)
        senders.append({
            "idx": i,
            "user": user,
            "smtp_host": _ev(f"OUTREACH_SENDER_{i}_SMTP_HOST"),
            "daily_limit": limit,
            "weight": weight,
            "sent_today": sent_t,
            "remaining": max(0, limit - sent_t),
        })
    return senders


def _save_sender_settings(updates: list) -> bool:
    """Schreibt Limit und Weight für Sender in die .env Datei."""
    env_path = ROOT / ".env"
    try:
        text = env_path.read_text(encoding="utf-8")
        for upd in updates:
            idx = int(upd.get("idx", 0))
            if not idx: continue
            limit = max(0, int(upd.get("daily_limit", 5)))
            weight = max(1, min(20, int(upd.get("weight", 1))))
            # Limit ersetzen
            import re as _re
            text = _re.sub(
                rf"(OUTREACH_SENDER_{idx}_DAILY_LIMIT\s*=\s*)\S+",
                rf"\g<1>{limit}",
                text,
            )
            text = _re.sub(
                rf"(OUTREACH_SENDER_{idx}_WEIGHT\s*=\s*)\S+",
                rf"\g<1>{weight}",
                text,
            )
        env_path.write_text(text, encoding="utf-8")
        # Auch os.environ aktualisieren für laufende Session
        for upd in updates:
            idx = int(upd.get("idx", 0))
            if not idx: continue
            os.environ[f"OUTREACH_SENDER_{idx}_DAILY_LIMIT"] = str(max(0, int(upd.get("daily_limit", 5))))
            os.environ[f"OUTREACH_SENDER_{idx}_WEIGHT"] = str(max(1, min(20, int(upd.get("weight", 1)))))
        return True
    except Exception as exc:
        _log(f"[sender-settings] Fehler: {exc}")
        return False


def _stats() -> dict:
    p = _load_pipeline()
    r = _load_replies()

    today = time.strftime("%Y-%m-%d")
    sent = sum(1 for e in p if e.get("outreach_stage") == "sent" or e.get("first_sent_at") or e.get("sent_message_id"))
    sent_today = sum(1 for e in p if (e.get("first_sent_at") or "")[:10] == today)
    # Approval pipeline
    ready = sum(1 for e in p if _is_unsent(e) and _is_ready(e) and (e.get("email") or "").strip())
    approved = sum(1 for e in p if _is_unsent(e) and _is_approved(e) and (e.get("email") or "").strip())
    awaiting_approval = sum(1 for e in p if _is_unsent(e) and _is_ready(e) and not _is_approved(e) and (e.get("email") or "").strip())

    replies_open = sum(1 for x in r if isinstance(x, dict)
                       and (x.get("status") or x.get("inbound_class") or "open") not in ("handled", "closed"))
    hot = sum(1 for x in r if isinstance(x, dict)
              and str(x.get("inbound_class", x.get("sentiment", ""))).lower()
              in ("positive", "interested", "appointment", "meeting", "meeting_intent"))
    with_email = sum(1 for e in p if str(e.get("email", "") or "").strip())
    with_phone = sum(1 for e in p if str(e.get("phone", "") or "").strip())
    fu_due = sum(1 for e in p if e.get("next_followup_at") and not str(e.get("reply_status", "")).startswith("pos"))

    # LinkedIn-Pipeline-KPIs
    li_todo = sum(1 for e in p if (e.get("linkedin_status") or "todo") == "todo"
                  and bool(e.get("company_name") or e.get("company")))
    li_progress = sum(1 for e in p if (e.get("linkedin_status") or "")
                      in ("found", "connect_sent", "connected", "dm_sent"))
    li_replied = sum(1 for e in p if (e.get("linkedin_status") or "")
                     in ("replied", "meeting"))

    return {
        "total": len(p), "sent": sent, "sent_today": sent_today,
        "ready": ready, "approved": approved, "awaiting_approval": awaiting_approval,
        "replies_open": replies_open, "replies_hot": hot, "fu_due": fu_due,
        "with_email": with_email, "with_phone": with_phone,
        "li_todo": li_todo, "li_progress": li_progress, "li_replied": li_replied,
        "ts": time.strftime("%H:%M:%S"),
    }


def _lead_summary(e: dict) -> dict:
    """Reduzierte Lead-Daten für Frontend (kein riesiges Body)."""
    sent_already = bool(e.get("first_sent_at") or e.get("sent_message_id"))
    return {
        "key": e.get("entry_key", ""),
        "company": e.get("company_name", "—"),
        "email": e.get("email", ""),
        "phone": e.get("phone", ""),
        "website": e.get("website", ""),
        "contact": e.get("contact_full_name") or e.get("contact_name") or "",
        "city": e.get("city", e.get("city_detected", "")),
        "industry": e.get("industry", ""),
        "stage": e.get("outreach_stage", "new"),
        "reply_status": e.get("reply_status", ""),
        "ready": _is_ready(e),                # vom Preview vorbereitet
        "approved": _is_approved(e),           # vom User freigegeben
        "sent_already": sent_already,
        "do_not_resend": bool(e.get("do_not_resend")),
        # Fallback fuer Alt-Eintraege ohne added_at: first_sent_at, sonst last_contacted_at.
        # Damit funktioniert "Neueste"-Sort sofort, nicht erst nach neuem Sync.
        "added_at": e.get("added_at") or e.get("first_sent_at") or e.get("last_contacted_at") or "",
        "sent_at": (e.get("first_sent_at") or "")[:16],
        "next_followup": (e.get("next_followup_at") or "")[:10],
        "subject": e.get("first_email_subject", ""),
        "lead_temp": e.get("lead_temperature", ""),
        "score": e.get("contact_quality_score", e.get("score", 0)),
        "linkedin_company": e.get("linkedin_company_url_clean") or e.get("linkedin_company_url") or "",
        "linkedin_person": e.get("linkedin_person_url") or "",
        "li_status": e.get("linkedin_status") or "todo",
        "li_status_at": e.get("linkedin_status_at") or "",
        "li_note": e.get("linkedin_note") or "",
        "research": _research_links(e),
        "last_error": e.get("last_error", ""),
        "source": e.get("source", "search"),
    }


def _reply_summary(r: dict) -> dict:
    return {
        "key": r.get("entry_key", r.get("message_id", "")),
        "from": r.get("from_email_actual") or r.get("from_email", ""),
        "subject": r.get("inbound_subject", ""),
        "snippet": r.get("inbound_snippet", "")[:200],
        "class": r.get("inbound_class", r.get("sentiment", "")),
        "confidence": r.get("confidence", 0),
        "route": r.get("route", ""),
        "needs_approval": r.get("needs_approval", False),
        "appointment_ready": r.get("appointment_ready", False),
        "received_account": r.get("received_account", ""),
        "ts": r.get("received_at", r.get("ts", ""))[:16],
    }


def _sent_log_index(events: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("ok") is not True:
            continue
        em = str(ev.get("to") or ev.get("email") or "").strip().lower()
        if em and em not in out:
            out[em] = ev
        sid = str(ev.get("sent_log_id") or "").strip()
        if sid and sid not in out:
            out[sid] = ev
    return out


def _reply_operator_item(r: dict, pipeline_by_key: dict[str, dict], pipeline_by_email: dict[str, dict], sent_idx: dict[str, dict]) -> dict:
    from_email = str(r.get("from_email") or "").strip().lower()
    actual = str(r.get("from_email_actual") or from_email).strip().lower()
    entry_key = str(r.get("entry_key") or "").strip()
    sent_log_id = str(r.get("sent_log_id") or "").strip()
    entry = pipeline_by_key.get(entry_key) or pipeline_by_email.get(from_email) or pipeline_by_email.get(actual) or {}
    sent_ev = sent_idx.get(sent_log_id) or sent_idx.get(from_email) or sent_idx.get(actual) or {}
    source = "pipeline" if entry else ("sent_log" if sent_ev or "sent_log" in str(r.get("reason") or "") else "unmatched")
    is_auto = bool(r.get("is_auto_reply"))
    cls = str(r.get("inbound_class") or r.get("sentiment") or "unclear").strip().lower()
    sentiment = str(r.get("sentiment") or "").strip().lower()
    reason = str(r.get("reason") or "").strip()
    appointment = bool(r.get("appointment_ready")) and not is_auto
    is_positive = (cls in ("positive", "interested") or sentiment == "positive" or appointment) and not is_auto
    is_negative = cls == "negative" or sentiment == "negative" or "do_not_contact" in reason
    if is_auto:
        group = "auto_replies"
        suggested = "ignore_auto_reply"
    elif is_negative:
        group = "negative_do_not_contact"
        suggested = "mark_do_not_contact"
    elif is_positive or appointment:
        group = "positive_appointment_ready"
        suggested = "human_follow_up"
    elif source == "unmatched" or cls in ("unclear", ""):
        group = "unmatched_unclear"
        suggested = "review_match"
    else:
        group = "human_review"
        suggested = "review_manually"
    company = str(entry.get("company_name") or sent_ev.get("company_name") or "").strip()
    return {
        "key": entry_key or str(r.get("message_id") or ""),
        "message_id": str(r.get("message_id") or ""),
        "from_email": from_email,
        "from_email_actual": actual,
        "matched_company": company,
        "matched_entry_source": source,
        "matched_entry_key": entry_key,
        "received_account": str(r.get("received_account") or ""),
        "subject": str(r.get("inbound_subject") or r.get("subject") or ""),
        "date": str(r.get("received_at") or r.get("ts") or r.get("sent_at") or ""),
        "inbound_class": cls or "unclear",
        "sentiment": sentiment or ("neutral" if is_auto else ""),
        "route": str(r.get("route") or ""),
        "needs_approval": bool(r.get("needs_approval")),
        "appointment_ready": appointment,
        "is_auto_reply": is_auto,
        "reason": reason,
        "suggested_action": suggested,
        "body_preview": str(r.get("inbound_snippet") or r.get("body") or r.get("snippet") or "")[:500],
        "original_sent_email": str(sent_ev.get("to") or entry.get("email") or from_email),
        "original_company": company,
        "auto_reply_reason": str(r.get("auto_reply_reason") or ""),
        "group": group,
        "can_classify": bool(entry_key and entry),
        "not_hot": bool(is_auto),
    }


def _reply_operator_queue_payload() -> dict:
    os.environ["REPLY_AUTO_SEND"] = "false"
    replies = _load_replies()
    pipeline = _load_pipeline()
    sent_events = _load_sent_events()
    pipeline_by_key = {str(e.get("entry_key") or ""): e for e in pipeline if isinstance(e, dict) and e.get("entry_key")}
    pipeline_by_email = {
        str(e.get("email") or "").strip().lower(): e
        for e in pipeline
        if isinstance(e, dict) and str(e.get("email") or "").strip()
    }
    sent_idx = _sent_log_index(sent_events)
    items = [_reply_operator_item(r, pipeline_by_key, pipeline_by_email, sent_idx) for r in replies if isinstance(r, dict)]
    groups = {
        "positive_appointment_ready": [i for i in items if i["group"] == "positive_appointment_ready"],
        "human_review": [i for i in items if i["group"] == "human_review"],
        "auto_replies": [i for i in items if i["group"] == "auto_replies"],
        "negative_do_not_contact": [i for i in items if i["group"] == "negative_do_not_contact"],
        "unmatched_unclear": [i for i in items if i["group"] == "unmatched_unclear"],
    }
    positive = len(groups["positive_appointment_ready"])
    auto = len(groups["auto_replies"])
    appointment = sum(1 for i in items if i.get("appointment_ready"))
    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_sources": {
            "reply_queue": str(REPLY_QUEUE_JSON),
            "reply_events": str(OUT / "reply_events.json"),
            "canonical_pipeline": str(PIPELINE_JSON),
            "canonical_sent_log": str(SENT_LOG_JSON),
        },
        "safety": {
            "reply_auto_send": os.environ.get("REPLY_AUTO_SEND", "false"),
            "auto_sent": 0,
            "no_smtp": True,
            "no_send_email": True,
            "read_only": True,
        },
        "counts": {
            "reply_queue_pending": len(items),
            "positive_count": positive,
            "auto_reply_count": auto,
            "appointment_ready_count": appointment,
            "sent_log_only_count": sum(1 for i in items if i.get("matched_entry_source") == "sent_log"),
            "human_review_count": len(groups["human_review"]),
            "negative_count": len(groups["negative_do_not_contact"]),
            "unmatched_unclear_count": len(groups["unmatched_unclear"]),
        },
        "groups": groups,
        "items": items,
    }


# ── HTTP-Handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def _send(self, code: int, body: bytes, ct: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def do_GET(self):

        # >>> PREMIUM_STATIC_ROUTE_PATCH_V1

        # Serve design_reference exactly like: cd design_reference && python -m http.server

        try:

            import urllib.parse as _premium_urlparse

            import mimetypes as _premium_mimetypes

            from pathlib import Path as _PremiumPath

        

            _premium_path = _premium_urlparse.urlparse(self.path).path

        

            if _premium_path == "/premium":

                self.send_response(302)

                self.send_header("Location", "/premium/")

                self.end_headers()

                return

        

            if _premium_path == "/premium/linkedin-report":

                _li_file = _PremiumPath(__file__).resolve().parent / "linkedin_bot" / "output" / "linkedin_outreach.html"

                if _li_file.exists():

                    _li_data = _li_file.read_bytes()

                else:

                    _li_data = b"<html><head><meta charset='utf-8'><title>LinkedIn</title></head><body style='font-family:sans-serif;padding:40px'><h2>LinkedIn-Report noch nicht erzeugt.</h2><p>Starte den LinkedIn-Bot, um einen Report zu erzeugen.</p></body></html>"

                self.send_response(200)

                self.send_header("Content-Type", "text/html; charset=utf-8")

                self.send_header("Cache-Control", "no-store")

                self.end_headers()

                self.wfile.write(_li_data)

                return



            if _premium_path == "/premium/workspace":

                _ws_file = _PremiumPath(__file__).resolve().parent / "output" / "latest" / "client_report.html"

                if not _ws_file.exists():

                    _ws_file = _PremiumPath(__file__).resolve().parent / "output" / "latest" / "00_Client_Acquisition_Report.html"

                if _ws_file.exists():

                    _ws_data = _ws_file.read_bytes()

                else:

                    _ws_data = b"<html><head><meta charset='utf-8'><title>Arbeitsbereich</title></head><body style='font-family:sans-serif;padding:40px'><h2>Kein Report vorhanden.</h2><p>output/latest/client_report.html nicht gefunden.</p></body></html>"

                self.send_response(200)

                self.send_header("Content-Type", "text/html; charset=utf-8")

                self.send_header("Cache-Control", "no-store")

                self.end_headers()

                self.wfile.write(_ws_data)

                return



            if _premium_path.startswith("/premium/"):

                _premium_base = (_PremiumPath(__file__).resolve().parent / "design_reference").resolve()

                _premium_rel = _premium_path[len("/premium/"):] or "index.html"

                _premium_target = (_premium_base / _premium_rel).resolve()

        

                if _premium_target != _premium_base and _premium_base not in _premium_target.parents:

                    self.send_response(403)

                    self.end_headers()

                    self.wfile.write(b"Forbidden")

                    return

        

                if _premium_target.is_dir():

                    _premium_target = _premium_target / "index.html"

        

                if not _premium_target.exists() or not _premium_target.is_file():

                    self.send_response(404)

                    self.end_headers()

                    self.wfile.write(b"Not found")

                    return

        

        

                _premium_data = _premium_target.read_bytes()

                _premium_ct = _premium_mimetypes.guess_type(str(_premium_target))[0] or "application/octet-stream"

        

                if _premium_target.suffix == ".jsx":

                    _premium_ct = "text/javascript; charset=utf-8"

                elif _premium_target.suffix == ".js":

                    _premium_ct = "text/javascript; charset=utf-8"

                elif _premium_target.suffix == ".css":

                    _premium_ct = "text/css; charset=utf-8"

                elif _premium_target.suffix == ".html":

                    _premium_ct = "text/html; charset=utf-8"

                    # Swap mock data.jsx for live-data loader so /premium shows real bot data

                    _premium_data = _premium_data.replace(b'src="data.jsx"', b'src="data-live.jsx"')



                self.send_response(200)

                self.send_header("Content-Type", _premium_ct)

                self.send_header("Cache-Control", "no-store")

                self.end_headers()

                self.wfile.write(_premium_data)

                return

        except Exception as _premium_exc:

            try:

                if str(getattr(self, "path", "")).startswith("/premium"):

                    self.send_response(500)

                    self.send_header("Content-Type", "text/plain; charset=utf-8")

                    self.end_headers()

                    self.wfile.write(("Premium route error: " + repr(_premium_exc)).encode("utf-8"))

                    return

            except Exception:

                pass

        # <<< PREMIUM_STATIC_ROUTE_PATCH_V1

        raw_path = urlparse(self.path).path.strip()
        p = raw_path.rstrip("/") if raw_path != "/" else raw_path
        if not p:
            p = "/"
        try:
            if p in ("/relay", "/relay.html"):
                relay_file = ROOT / "dashboard_relay_premium.html"
                if relay_file.exists():
                    return self._send(200, relay_file.read_text(encoding="utf-8").encode("utf-8"))
                return self._json({"error": "relay_dashboard_missing"}, 404)
            if p == "/premium":
                return self._send(200, CLAUDE_DASHBOARD_HTML.encode("utf-8"))
            if p.startswith("/dr/"):
                fname = p[4:]
                if fname and ".." not in fname and "/" not in fname:
                    dr_file = ROOT / "design_reference" / fname
                    if dr_file.exists() and dr_file.is_file():
                        ct = "text/css; charset=utf-8" if fname.endswith(".css") else "application/javascript; charset=utf-8"
                        return self._send(200, dr_file.read_bytes(), ct)
                return self._json({"error": "not_found"}, 404)
            if p == "/api/data-live.jsx":
                return self._send(200, _claude_data_live_js().encode("utf-8"),
                                  "application/javascript; charset=utf-8")
            if p in ("/", "/index.html"):
                # Relay-Dashboard ist das aktive Operator-Cockpit
                self.send_response(302)
                self.send_header("Location", "/relay")
                self.end_headers()
                return
            if p in ("/linkedin", "/linkedin.html", "/linkedin/"):
                return self._send(200, MINIMAL_PREMIUM_HTML.encode("utf-8"))
            if p in ("/classic", "/classic.html", "/classic/"):
                return self._send(200, PREMIUM_HTML.encode("utf-8"))
            if p == "/api/premium/min-leads":
                _lp = ROOT / "output" / "latest" / "intent_lead_production.json"
                _op = ROOT / "output" / "latest" / "outreach_pipeline.json"
                _leads_out = []
                _source = ""
                if _lp.exists():
                    try:
                        _d = json.loads(_lp.read_text(encoding="utf-8"))
                        for _L in (_d.get("leads") or []):
                            _co = str(_L.get("company_name") or "").strip()
                            _ws = str(_L.get("website") or "").strip()
                            if _co:
                                _leads_out.append({"company": _co, "website": _ws})
                        if _leads_out:
                            _source = "intent_lead_production"
                    except Exception:
                        pass
                if not _leads_out and _op.exists():
                    try:
                        _d = json.loads(_op.read_text(encoding="utf-8"))
                        _seen = set()
                        for _e in (_d.get("entries") or []):
                            _co = str(_e.get("outreach_display_company") or _e.get("company_name_clean") or _e.get("company_name") or "").strip()
                            _ws = str(_e.get("website") or "").strip()
                            if not _ws:
                                _dom = str(_e.get("website_domain") or "").strip()
                                if _dom:
                                    _ws = "https://" + _dom
                            if _co and _co not in _seen:
                                _seen.add(_co)
                                _leads_out.append({"company": _co, "website": _ws})
                        if _leads_out:
                            _source = "outreach_pipeline"
                    except Exception:
                        pass
                return self._json({"leads": _leads_out, "source": _source})
            if p == "/api/premium/li-resolve":
                # Resolves the top Google result for "site:linkedin.com/in NAME COMPANY"
                # via Serper API. Returns {url} with the actual LinkedIn profile, or
                # {url: null, fallback_url: googleSearch} when no result found.
                _qp = urlparse(self.path).query
                _qs = {}
                for _kv in _qp.split("&"):
                    if "=" in _kv:
                        _k, _v = _kv.split("=", 1)
                        try:
                            from urllib.parse import unquote_plus
                            _qs[_k] = unquote_plus(_v)
                        except Exception:
                            _qs[_k] = _v
                _name = (_qs.get("name") or "").strip()
                _company = (_qs.get("company") or "").strip()
                if not _name and not _company:
                    return self._json({"url": None, "error": "name_or_company_required"}, 400)
                _key = _read_key_file("serper_key.txt")
                _query_parts = []
                if _name: _query_parts.append('"' + _name + '"')
                if _company: _query_parts.append(_company)
                _search_q = "site:linkedin.com/in " + " ".join(_query_parts)
                _fallback_g = "https://www.google.com/search?q=" + quote(_search_q)
                if not _key:
                    return self._json({"url": None, "fallback_url": _fallback_g, "reason": "no_serper_key"})
                try:
                    import urllib.request as _ur
                    import urllib.error as _ue
                    _body = json.dumps({"q": _search_q, "gl": "de", "hl": "de", "num": 5}).encode("utf-8")
                    _req = _ur.Request(
                        "https://google.serper.dev/search",
                        data=_body,
                        headers={"X-API-KEY": _key, "Content-Type": "application/json"},
                        method="POST",
                    )
                    with _ur.urlopen(_req, timeout=8) as _r:
                        _sj = json.loads(_r.read().decode("utf-8"))
                    _hit = None
                    for _item in (_sj.get("organic") or []):
                        _u = _item.get("link") or ""
                        if "linkedin.com/in/" in _u.lower():
                            _hit = _u
                            break
                    if _hit:
                        return self._json({"url": _hit, "title": (_sj.get("organic") or [{}])[0].get("title", "")})
                    return self._json({"url": None, "fallback_url": _fallback_g, "reason": "no_li_hit"})
                except Exception as _ex:
                    return self._json({"url": None, "fallback_url": _fallback_g, "reason": "serper_error: " + str(_ex)[:120]})
            if p == "/api/premium/data":
                _pf = ROOT / "output" / "latest" / "outreach_pipeline.json"
                _rf = ROOT / "output" / "latest" / "reply_queue.json"
                _pd: dict = {}
                _rd: dict = {}
                if _pf.exists():
                    try:
                        _pd = json.loads(_pf.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                if _rf.exists():
                    try:
                        _rd = json.loads(_rf.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                return self._json({"pipeline": _pd, "replies": _rd})
            if p == "/api/stats":
                stats = _stats()
                stats["last_search_started_at"] = _get_last_search_started_at()
                return self._json(stats)
            if p == "/api/senders":
                return self._json({"senders": _get_senders_status()})
            if p == "/api/leads":
                pipeline = _load_pipeline()
                last_search = _get_last_search_started_at()
                return self._json({"items": [_lead_summary(e) for e in pipeline], "last_search_started_at": last_search})
            if p == "/api/replies":
                return self._json({"items": [_reply_summary(r) for r in _load_replies()]})
            if p == "/api/reply-operator-queue":
                return self._json(_reply_operator_queue_payload())
            if p == "/api/sent":
                return self._json({"items": _load_sent_events()[-50:]})
            if p == "/api/jobs":
                with _jobs_lock:
                    return self._json({"jobs": list(_jobs.values())[-20:],
                                        "log": list(_log_buffer)[-30:]})
            if p == "/api/intent-preview":
                return self._json(_intent_preview_payload())
            if p == "/api/intent-lead-production":
                return self._json(_intent_lead_production_payload())
            if p == "/api/intent-email-review-queue":
                return self._json(_intent_email_review_queue_payload())
            if p == "/api/intent-manual-decision-maker-review":
                return self._json(_intent_manual_decision_maker_review_payload())
            if p == "/api/intent-operator-queue":
                return self._json(_intent_operator_queue_payload())
            if p == "/api/premium-dashboard":
                return self._json(_premium_dashboard_payload())
            if p.startswith("/api/job/"):
                jid = p.rsplit("/", 1)[-1]
                with _jobs_lock:
                    j = _jobs.get(jid)
                return self._json(j or {"error": "not_found"}, 200 if j else 404)
            if p.startswith("/api/lead/") and p.endswith("/copy-texts"):
                key = p.split("/")[3]
                pipeline = _load_pipeline()
                e = next((x for x in pipeline if x.get("entry_key") == key), None)
                if not e:
                    return self._json({"error": "not_found"}, 404)
                return self._json({
                    "research": _research_links(e),
                    "texts": _li_copy_texts(e),
                    "company": _company_label(e),
                    "contact": _contact_name_from_lead(e),
                })
            if p.startswith("/api/lead/"):
                key = p.rsplit("/", 1)[-1]
                pipeline = _load_pipeline()
                e = next((x for x in pipeline if x.get("entry_key") == key), None)
                if not e:
                    return self._json({"error": "not_found"}, 404)
                # Anreichern um Research-Links für Drawer
                out = dict(e)
                out["_research"] = _research_links(e)
                out["_li_texts"] = _li_copy_texts(e)
                return self._json(out)
            if p.startswith("/api/reply/"):
                key = p.rsplit("/", 1)[-1]
                rs = _load_replies()
                r = next((x for x in rs if x.get("entry_key") == key or x.get("message_id") == key), None)
                pipeline = _load_pipeline()
                e = next((x for x in pipeline if x.get("entry_key") == (r.get("entry_key") if r else key)), None)
                return self._json({"reply": r, "lead": e})
            return self._json({"error": "unknown"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        b = self._read()
        try:
            if p == "/api/search":
                ind = (b.get("industry") or "").strip()
                city = (b.get("city") or "").strip()
                cnt = int(b.get("count", 20) or 20)
                if not ind:
                    return self._json({"error": "industry_required"}, 400)
                cmd = [PYTHON, MINE, "-i", ind, "-n", str(cnt)]
                if city:
                    cmd += ["-c", city]
                # Such-Startzeit tracken für "Neu"-Filter & inkrementelle Anzeige
                _set_last_search_started_at(time.strftime("%Y-%m-%dT%H:%M:%S"))
                # Periodischer Sync alle 5s während Mining → inkrementelle Results im Dashboard
                return self._json({"job_id": _start_job(f"Suche: {ind} ({cnt})", cmd, periodic_sync=True)})

            if p == "/api/sync-replies":
                return self._json({"job_id": _start_job("Replies syncen", [PYTHON, MINE, "--outreach", "sync"])})
            if p == "/api/process-replies":
                os.environ["REPLY_AUTO_SEND"] = "false"
                return self._json({"job_id": _start_job("Replies verarbeiten", [PYTHON, MINE, "--outreach", "process-replies"])})
            if p == "/api/preview":
                return self._json({"job_id": _start_job("Preview generieren", [PYTHON, MINE, "--outreach", "preview"])})
            if p == "/api/send-batch":
                lim = int(b.get("limit", 10) or 10)
                return self._json({"job_id": _start_job(f"Senden ({lim})",
                    [PYTHON, MINE, "--outreach", "send", "--outreach-limit", str(lim)])})
            if p == "/api/send-followups":
                lim = int(b.get("limit", 10) or 10)
                return self._json({"job_id": _start_job(f"Follow-ups ({lim})",
                    [PYTHON, MINE, "--outreach", "followups", "--outreach-limit", str(lim)])})
            if p == "/api/send-reply-drafts":
                return self._json({"job_id": _start_job("Reply-Drafts senden",
                    [PYTHON, MINE, "--outreach", "send-reply-drafts"])})
            if p == "/api/full-auto":
                return self._json({"job_id": _start_job("FULL AUTO", [PYTHON, MINE, "--outreach", "full-auto"])})
            if p == "/api/intent-target-preview/run":
                return self._json({"job_id": _start_job("Intent Target Preview", [PYTHON, INTENT_TARGET_PREVIEW_SCRIPT])})
            if p == "/api/intent-lead-production/run":
                industry = str(b.get("industry") or "").strip()
                city = str(b.get("city") or "").strip()
                signal_type = str(b.get("signal_type") or "").strip() or "sales_hiring"
                mode = str(b.get("mode") or "").strip() or "preview"
                try:
                    raw_limit = int(b.get("limit") or 10)
                except (TypeError, ValueError):
                    raw_limit = 10
                if not industry:
                    return self._json({"error": "industry_required"}, 400)
                if signal_type not in INTENT_LP_ALLOWED_SIGNALS:
                    return self._json({"error": f"signal_type_invalid: {signal_type}"}, 400)
                if mode not in INTENT_LP_ALLOWED_MODES:
                    return self._json({"error": f"mode_invalid: {mode}"}, 400)
                # Hard cap at server boundary too — defence in depth.
                effective_limit = max(1, min(raw_limit, INTENT_LP_HARD_MAX_LIMIT))
                if not city:
                    city = "Muenchen"
                cmd = [
                    PYTHON, INTENT_LEAD_PRODUCTION_SCRIPT,
                    "--industry", industry,
                    "--city", city,
                    "--signal-type", signal_type,
                    "--limit", str(effective_limit),
                    "--mode", mode,
                ]
                job_id = _start_job("Intent Lead Production", cmd)
                return self._json({
                    "job_id": job_id,
                    "industry": industry,
                    "city": city,
                    "signal_type": signal_type,
                    "mode": mode,
                    "requested_limit": raw_limit,
                    "effective_limit": effective_limit,
                })
            if p == "/api/intent-email-review/decision":
                result, code = _apply_intent_email_review_decision(
                    str(b.get("review_id") or ""),
                    str(b.get("decision") or ""),
                )
                return self._json(result, code)
            if p == "/api/intent-manual-decision-maker-review/save":
                result, code = _save_intent_manual_decision_maker_review(b)
                return self._json(result, code)

            if p == "/api/approve-all":
                lim = int(b.get("limit", 9999) or 9999)
                return self._json({"job_id": _start_job("Alle freigeben",
                    [PYTHON, MINE, "--outreach", "approve", "--outreach-limit", str(lim)])})

            if p == "/api/sender-settings":
                updates = b.get("senders", [])
                ok = _save_sender_settings(updates)
                return self._json({"ok": ok, "senders": _get_senders_status()})


            if p == "/api/lead/approve":
                k = (b.get("key") or "").strip()
                if not k:
                    return self._json({"error": "key_required"}, 400)
                return self._json({"job_id": _start_job(f"Approve: {k[:10]}",
                    [PYTHON, MINE, "--outreach", "approve", "--approve-keys", k])})

            if p == "/api/lead/send":
                k = (b.get("key") or "").strip()
                if not k:
                    return self._json({"error": "key_required"}, 400)
                # Echtes Approve+Send: erst approve, dann send (sequenziell).
                # Vorher feuerte hier nur "approve" — das hat nie gemailt.
                job_id = uuid.uuid4().hex[:8]
                with _jobs_lock:
                    _jobs[job_id] = {
                        "id": job_id,
                        "name": f"Approve+Send: {k[:10]}",
                        "cmd": "approve + send (limit=1)",
                        "status": "running",
                        "started_at": time.strftime("%H:%M:%S"),
                        "ended_at": None, "exit_code": None,
                        "stdout_tail": "", "stderr_tail": "",
                    }

                def _runner_approve_send(_k=k, _jid=job_id):
                    parts: list[str] = []
                    final_rc = 0
                    final_stderr = ""
                    for step_args, step_name in (
                        (["--outreach", "approve", "--approve-keys", _k, "--outreach-limit", "1"], "approve"),
                        (["--outreach", "send", "--outreach-limit", "1"], "send"),
                    ):
                        try:
                            r = subprocess.run(
                                [PYTHON, MINE, *step_args], cwd=str(ROOT),
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=300,
                            )
                            parts.append(f"[{step_name} exit={r.returncode}]\n{(r.stdout or '')[-1200:]}")
                            if r.returncode != 0:
                                final_rc = r.returncode
                                final_stderr = (r.stderr or "")[-1500:]
                                break
                        except Exception as ex:  # noqa: BLE001
                            final_rc = -1
                            final_stderr = f"{step_name} exception: {ex}"
                            parts.append(f"[{step_name} EXC] {ex}")
                            break
                    with _jobs_lock:
                        j = _jobs[_jid]
                        j["status"] = "ok" if final_rc == 0 else "error"
                        j["ended_at"] = time.strftime("%H:%M:%S")
                        j["exit_code"] = final_rc
                        j["stdout_tail"] = "\n\n".join(parts)[-3000:]
                        j["stderr_tail"] = final_stderr
                    _log(f"[{'OK' if final_rc == 0 else '!'}] Approve+Send: {_k[:10]} (exit={final_rc})")

                threading.Thread(target=_runner_approve_send, daemon=True).start()
                return self._json({"job_id": job_id})

            if p == "/api/lead/li-status":
                k = (b.get("key") or "").strip()
                s = (b.get("status") or "").strip()
                note = (b.get("note") or "").strip()
                if not (k and s):
                    return self._json({"error": "missing"}, 400)
                if s not in LI_STATUS_VALUES:
                    return self._json({"error": "invalid_status",
                                       "valid": list(LI_STATUS_VALUES)}, 400)
                ok = _set_linkedin_status(k, s, note)
                return self._json({"ok": ok, "key": k, "status": s})

            if p == "/api/linkedin/run":
                limit = int(b.get("limit", 20) or 20)
                # Versucht zuerst die latest leads.csv, dann output/leads.csv
                csv_path = None
                latest = OUT / "latest" / "leads.csv"
                fallback = OUT / "leads.csv"
                if latest.exists():
                    csv_path = str(latest)
                elif fallback.exists():
                    csv_path = str(fallback)
                if not csv_path:
                    return self._json({"error": "no_csv",
                                       "msg": "Keine leads.csv gefunden. Erst Lead-Suche starten."}, 400)
                cmd = [PYTHON, "-m", "linkedin_bot",
                       "--input", csv_path, "--daily-limit", str(limit)]
                return self._json({"job_id": _start_job(
                    f"LinkedIn-Tagesliste ({limit})", cmd)})

            if p == "/api/linkedin/search":
                ind = (b.get("industry") or "").strip()
                city = (b.get("city") or "").strip()
                role = (b.get("role") or "").strip()
                cnt = int(b.get("count", 20) or 20)
                if not ind:
                    return self._json({"error": "industry_required"}, 400)

                # Such-Startzeit tracken für "Neu"-Filter
                _set_last_search_started_at(time.strftime("%Y-%m-%dT%H:%M:%S"))

                # Direkte LinkedIn People-Search URL (für manuelles Klicken)
                _li_kw_parts = [ind]
                if city: _li_kw_parts.append(city)
                _li_search_url = (
                    f"https://www.linkedin.com/search/results/people/"
                    f"?keywords={quote(' '.join(_li_kw_parts))}"
                    + (f"&titleKeywords={quote(role)}" if role else "")
                    + f"&origin={_LI_ORIGIN}"
                )

                # Industry um Rolle erweitern, damit der Lead-Crawler
                # gezielter sucht (z.B. "Marketingagentur Geschäftsführer")
                ind_full = f"{ind} {role}".strip() if role else ind

                # Sequenzieller Combined-Job:
                # 1) mine.py Lead-Suche
                # 2) linkedin_bot Tagesliste generieren
                latest_csv = OUT / "latest" / "leads.csv"
                fallback_csv = OUT / "leads.csv"

                def _combined_runner():
                    job_id = _start_job(
                        f"LinkedIn-Suche: {ind_full[:40]}",
                        [PYTHON, MINE, "-i", ind_full, "-n", str(cnt)] +
                        (["-c", city] if city else []),
                    )
                    # Auf Lead-Suche warten (wird im Job-Runner async erledigt)
                    # Dafuer pruefen wir die CSV nach kurzer Verzögerung
                    return job_id

                # Job-Wrapper mit Live-Progress (streamt subprocess stdout in
                # _jobs[job_id]["progress_*"], damit das Dashboard mitlaufen kann).
                job_id = uuid.uuid4().hex[:8]
                with _jobs_lock:
                    _jobs[job_id] = {
                        "id": job_id,
                        "name": f"LI-Suche+Liste: {ind_full[:30]}{(' ' + city) if city else ''}",
                        "cmd": f"mine.py + linkedin_bot ({cnt})",
                        "status": "running",
                        "started_at": time.strftime("%H:%M:%S"),
                        "ended_at": None, "exit_code": None,
                        "stdout_tail": "", "stderr_tail": "",
                        # Live-Progress (vom Frontend gepollt)
                        "progress_pct": 0,
                        "progress_phase": "init",
                        "progress_msg": "Starte Suche...",
                        "progress_total": cnt,
                        "progress_done": 0,
                        "with_linkedin": 0,
                        "search_label": f"{ind_full}{(' · ' + city) if city else ''}",
                    }

                def _set_progress(pct: int, phase: str, msg: str, **extra) -> None:
                    with _jobs_lock:
                        j = _jobs.get(job_id)
                        if not j:
                            return
                        j["progress_pct"] = max(0, min(100, int(pct)))
                        j["progress_phase"] = phase
                        j["progress_msg"] = msg[:200]
                        for k, v in extra.items():
                            j[k] = v

                _PROGRESS_PARSERS_MINE = (
                    # match e.g. "[Search] Marketing | München | region=de-de → 47 Kandidaten"
                    re.compile(r"→\s*(\d+)\s+Kandidat", re.I),
                    re.compile(r"(\d+)\s*/\s*(\d+)\b"),
                )

                def _stream_subprocess(cmd: list[str], phase_start: int, phase_end: int,
                                        phase_label: str, on_line=None) -> tuple[int, str, str]:
                    """Startet Subprocess, streamt stdout in Job-Tail + ruft on_line(line)."""
                    stdout_buf: list[str] = []
                    stderr_buf: list[str] = []
                    try:
                        proc = subprocess.Popen(
                            cmd, cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1,
                            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "LOG_LEVEL": "INFO",
                                 "SERPER_API_KEY": _read_key_file(),
                                 "TAVILY_API_KEY": _read_key_file("tavily_key.txt")},
                        )
                    except Exception as ex:
                        return -1, "", f"spawn error: {ex}"

                    started = time.time()
                    last_pct = phase_start
                    for raw in proc.stdout or []:
                        ln = raw.rstrip("\n")
                        stdout_buf.append(ln)
                        if len(stdout_buf) > 600:
                            stdout_buf.pop(0)
                        if on_line:
                            try:
                                on_line(ln)
                            except Exception:
                                pass
                        # Heuristisches Time-basiertes Mitziehen: pro 10s ~+1% bis phase_end-2
                        elapsed = time.time() - started
                        approx = phase_start + min(phase_end - phase_start - 2,
                                                    int(elapsed / 4))
                        if approx > last_pct:
                            last_pct = approx
                            _set_progress(approx, phase_label, ln[:120] or "...")
                    rc = proc.wait()
                    return rc, "\n".join(stdout_buf)[-3000:], "\n".join(stderr_buf)[-1500:]

                def _count_linkedin_in_csv(csv_path) -> int:
                    """Zählt Leads in der CSV mit echtem LinkedIn-Link."""
                    try:
                        import csv as _csv
                        n = 0
                        with open(csv_path, encoding="utf-8-sig") as f:
                            for row in _csv.DictReader(f):
                                u = (row.get("linkedin_company_url_clean")
                                        or row.get("linkedin_company_url")
                                        or row.get("linkedin_person_url") or "")
                                if "linkedin.com" in u.lower():
                                    n += 1
                        return n
                    except Exception:
                        return 0

                def _runner_seq():
                    # Phase 1: Lead-Suche  (0% → 75%)
                    _set_progress(2, "search", f"Lead-Suche: {ind_full}")
                    cmd1 = [PYTHON, MINE, "-i", ind_full, "-n", str(cnt)]
                    if city:
                        cmd1 += ["-c", city]

                    def _on_search_line(ln: str):
                        # Versuche Kandidaten-Anzahl als Mitlauf-Signal zu nutzen
                        for rgx in _PROGRESS_PARSERS_MINE:
                            m = rgx.search(ln)
                            if m:
                                try:
                                    if len(m.groups()) == 2:
                                        done, total = int(m.group(1)), int(m.group(2))
                                        if total > 0:
                                            pct = 5 + int(70 * min(done, total) / total)
                                            _set_progress(min(pct, 72), "search",
                                                            f"Suche {done}/{total}",
                                                            progress_done=done,
                                                            progress_total=total)
                                            return
                                    cands = int(m.group(1))
                                    pct = 5 + int(70 * min(cands, cnt) / max(cnt, 1))
                                    _set_progress(min(pct, 72), "search",
                                                    f"{cands} Kandidaten gefunden",
                                                    progress_done=cands)
                                    return
                                except Exception:
                                    pass

                    rc1, out1, err1 = _stream_subprocess(cmd1, 2, 70, "search",
                                                          on_line=_on_search_line)
                    if rc1 != 0:
                        return False, f"Lead-Suche fehlgeschlagen (exit={rc1}): {err1[-400:]}"

                    # Auto-Sync: leads.json → outreach_pipeline.json
                    _set_progress(72, "sync", "Sync Leads → Pipeline... (max 5 min)")
                    try:
                        sync_r = subprocess.run(
                            [PYTHON, MINE, "--outreach", "sync"],
                            cwd=str(ROOT), capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            timeout=300,  # 5 min Hard-Cap — verhindert 72%-Hänger
                            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "LOG_LEVEL": "INFO",
                                 "SERPER_API_KEY": _read_key_file(),
                                 "TAVILY_API_KEY": _read_key_file("tavily_key.txt")},
                        )
                    except subprocess.TimeoutExpired:
                        _log("[warn] LinkedIn-Suche: sync timeout nach 5 min, fahre fort mit Phase 2")
                        _set_progress(74, "sync", "Sync-Timeout — fahre fort")
                        sync_r = None
                    if sync_r is not None and sync_r.returncode != 0:
                        _log(f"[warn] LinkedIn-Suche: sync fehlgeschlagen (exit={sync_r.returncode})")
                    else:
                        # Markiere Leads aus dieser LinkedIn-Suche als source=linkedin
                        try:
                            from modules.outreach_pipeline import load_pipeline_state, save_pipeline_state
                            st = load_pipeline_state()
                            cutoff = _get_last_search_started_at() or ""
                            tagged = 0
                            for e in st.get("entries", []):
                                if e.get("added_at", "") >= cutoff and e.get("source") != "linkedin":
                                    e["source"] = "linkedin"
                                    tagged += 1
                            if tagged:
                                save_pipeline_state(st)
                                _log(f"[linkedin] {tagged} Leads als source=linkedin markiert")
                        except Exception as ex:
                            _log(f"[warn] LinkedIn source-tagging fehlgeschlagen: {ex}")

                    _set_progress(75, "list", "Lead-Suche fertig, baue LinkedIn-Liste ...")

                    # Phase 2: CSV finden + LinkedIn-Filter
                    csv_use = None
                    if latest_csv.exists():
                        csv_use = str(latest_csv)
                    elif fallback_csv.exists():
                        csv_use = str(fallback_csv)
                    if not csv_use:
                        return False, "Keine leads.csv nach Suche gefunden"

                    li_count = _count_linkedin_in_csv(csv_use)
                    _set_progress(80, "list",
                                    f"{li_count} Leads mit LinkedIn-Link gefunden",
                                    with_linkedin=li_count)

                    # Phase 3: LinkedIn-Bot Tagesliste  (80% → 99%)
                    cmd2 = [PYTHON, "-m", "linkedin_bot",
                            "--input", csv_use, "--daily-limit", str(cnt)]
                    rc2, out2, err2 = _stream_subprocess(cmd2, 80, 99, "list")
                    if rc2 != 0:
                        return False, f"LinkedIn-Bot fehlgeschlagen (exit={rc2}): {err2[-400:]}"
                    li_count_final = _count_linkedin_in_csv(csv_use)
                    _set_progress(100, "done",
                                    f"Fertig — {li_count_final} Leads mit LinkedIn-Link gelistet",
                                    with_linkedin=li_count_final)
                    return True, (
                        f"OK: Lead-Suche + LinkedIn-Liste fertig "
                        f"({li_count_final} mit LinkedIn-Link, von {cnt} angefragten)"
                    )

                def _wrapper():
                    _log(f"[>] LinkedIn-Suche kombiniert: {ind_full} | {city} | n={cnt}")
                    ok, msg = _runner_seq()
                    with _jobs_lock:
                        j = _jobs[job_id]
                        j["status"] = "ok" if ok else "error"
                        j["ended_at"] = time.strftime("%H:%M:%S")
                        j["exit_code"] = 0 if ok else 1
                        j["stdout_tail"] = msg
                        if not ok:
                            j["progress_phase"] = "error"
                            j["progress_msg"] = msg[:200]
                    _log(f"[{'OK' if ok else '!'}] LinkedIn-Suche kombiniert: {msg}")

                threading.Thread(target=_wrapper, daemon=True).start()
                return self._json({
                    "job_id": job_id,
                    "msg": f"Suche gestartet: {ind_full} ({cnt})",
                    "li_search_url": _li_search_url,
                    "industry": ind, "city": city, "role": role, "count": cnt,
                })

            if p == "/api/reply/classify":
                k = (b.get("key") or "").strip()
                s = (b.get("status") or "").strip()
                if not (k and s):
                    return self._json({"error": "missing"}, 400)
                return self._json({"job_id": _start_job(f"Reply: {s}",
                    [PYTHON, MINE, "--outreach", "reply", "--reply-entry-key", k, "--reply-status", s])})

            return self._json({"error": "unknown"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)


# ── Minimal Premium Dashboard (served at /) ─────────────────────────────────

MINIMAL_PREMIUM_HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relay — LinkedIn-Suche</title>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0a0c;
    --panel: #131316;
    --panel-2: #18181c;
    --line: #1f1f24;
    --line-soft: #1a1a1f;
    --text: #e4e4e7;
    --text-dim: #a1a1aa;
    --text-mute: #71717a;
    --accent: oklch(72% 0.15 250);
    --accent-soft: oklch(72% 0.15 250 / 0.14);
    --blue: oklch(72% 0.16 230);
    --green: oklch(72% 0.16 152);
    --green-soft: oklch(72% 0.16 152 / 0.14);
    --yellow: oklch(82% 0.16 90);
    --red: oklch(70% 0.18 25);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    font-family: 'Manrope', -apple-system, system-ui, sans-serif;
    background: var(--bg); color: var(--text);
    -webkit-font-smoothing: antialiased;
  }
  .topbar {
    border-bottom: 1px solid var(--line);
    padding: 16px 32px;
    display: flex; align-items: center; gap: 14px;
    background: var(--panel);
  }
  .brand-mark {
    width: 28px; height: 28px; border-radius: 8px;
    background: linear-gradient(135deg, var(--accent) 0%, var(--blue) 100%);
  }
  .brand-text { display: flex; flex-direction: column; line-height: 1.1; }
  .brand-name { font-weight: 700; font-size: 15px; letter-spacing: -0.01em; }
  .brand-sub { font-size: 11px; color: var(--text-mute); }
  .spacer { flex: 1; }
  .topbar a.lnk { color: var(--text-mute); font-size: 12px; text-decoration: none; margin-left: 14px; }
  .topbar a.lnk:hover { color: var(--text); }

  .container { max-width: 1280px; margin: 0 auto; padding: 28px 32px 60px; }
  h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.01em; margin-bottom: 4px; }
  .sub { color: var(--text-dim); font-size: 13px; margin-bottom: 20px; }

  .search-row {
    display: flex; gap: 10px; margin-bottom: 20px; align-items: stretch;
  }
  .input-shell {
    flex: 1; display: flex; align-items: center; gap: 10px;
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: 0 14px; min-height: 46px;
  }
  .input-shell:focus-within { border-color: var(--accent); }
  .input-shell svg { color: var(--text-mute); }
  .input-shell input {
    flex: 1; background: transparent; border: none; outline: none;
    color: var(--text); font-size: 14px; font-family: inherit;
    padding: 13px 0;
  }
  .input-shell.count { flex: 0 0 140px; }
  .input-shell.count input { text-align: right; font-variant-numeric: tabular-nums; }
  .input-shell.city { flex: 0 0 240px; }
  .input-shell .lbl { color: var(--text-mute); font-size: 11.5px; }

  .btn {
    padding: 0 18px; height: 46px;
    background: var(--accent); color: #fff;
    border: none; border-radius: 10px;
    font-size: 13.5px; font-weight: 600;
    font-family: inherit; cursor: pointer;
    display: inline-flex; align-items: center; gap: 8px;
    transition: opacity 0.12s, background 0.12s;
    white-space: nowrap;
  }
  .btn:hover:not(:disabled) { opacity: 0.9; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn.ghost {
    background: var(--panel); border: 1px solid var(--line);
    color: var(--text); height: 30px; padding: 0 11px; font-size: 11.5px; border-radius: 7px;
  }
  .btn.ghost:hover:not(:disabled) { background: var(--panel-2); border-color: var(--accent); }
  .btn.ghost.li { color: var(--blue); border-color: rgba(110,139,255,0.3); }
  .btn.ghost.li:hover { background: rgba(110,139,255,0.08); }
  .btn.ghost.web { color: var(--green); border-color: rgba(52,211,154,0.3); }
  .btn.ghost.web:hover { background: rgba(52,211,154,0.08); }
  .btn.ghost.kd { color: var(--yellow); border-color: rgba(240,184,64,0.3); }
  .btn.ghost.kd:hover { background: rgba(240,184,64,0.08); }
  .btn.ghost:disabled { opacity: 0.35; }

  .progress-panel {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 12px; padding: 18px 20px; margin-bottom: 18px;
  }
  .progress-panel.hidden { display: none; }
  .progress-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; font-size: 13px; }
  .progress-phase { color: var(--text-dim); font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.08em; }
  .progress-bar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; }
  .progress-fill { height: 100%; background: linear-gradient(90deg, var(--accent), var(--blue)); transition: width 0.4s; }
  .progress-msg { margin-top: 10px; font-size: 12px; color: var(--text-mute); font-family: 'JetBrains Mono', monospace; }

  .panel {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 12px; overflow: hidden;
  }
  .panel-head {
    padding: 14px 18px; border-bottom: 1px solid var(--line-soft);
    display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 600;
  }
  .panel-head .count-pill {
    margin-left: auto; padding: 3px 10px; border-radius: 999px;
    background: var(--accent-soft); color: var(--accent);
    font-size: 11.5px; font-weight: 600; font-variant-numeric: tabular-nums;
  }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--line-soft); font-size: 13px; vertical-align: middle; }
  th {
    background: rgba(255,255,255,0.015);
    color: var(--text-dim); font-weight: 600;
    font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em;
    position: sticky; top: 0; z-index: 1;
  }
  tbody tr:last-child td { border-bottom: none; }
  tbody tr:hover { background: rgba(255,255,255,0.012); }
  .name { font-weight: 600; }
  .firma { color: var(--text-dim); }
  .mono { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
  .mute { color: var(--text-mute); font-style: italic; font-size: 12px; }
  .actions { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
  .empty {
    padding: 70px 24px; text-align: center; color: var(--text-dim);
  }
  .empty .ic { font-size: 30px; opacity: 0.5; margin-bottom: 10px; }
  a.li-inline {
    color: var(--blue); text-decoration: none;
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 12px;
  }
  a.li-inline:hover { text-decoration: underline; }

  /* Modal */
  .modal-scrim {
    position: fixed; inset: 0; background: rgba(0,0,0,0.6); backdrop-filter: blur(6px);
    z-index: 100; display: none; align-items: center; justify-content: center;
  }
  .modal-scrim.open { display: flex; }
  .modal {
    background: var(--panel-2); border: 1px solid var(--line);
    border-radius: 14px; max-width: 480px; width: 90%;
    padding: 24px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }
  .modal-head { display: flex; align-items: flex-start; gap: 14px; margin-bottom: 18px; }
  .modal-avatar {
    width: 44px; height: 44px; border-radius: 10px;
    background: linear-gradient(135deg, var(--accent) 0%, var(--blue) 100%);
    display: grid; place-items: center; color: white; font-weight: 700;
  }
  .modal-name { font-size: 17px; font-weight: 700; }
  .modal-company { font-size: 13px; color: var(--text-dim); margin-top: 2px; }
  .modal-close { background: transparent; border: none; color: var(--text-mute); cursor: pointer; padding: 4px; font-size: 18px; }
  .modal-close:hover { color: var(--text); }
  .field-list { display: flex; flex-direction: column; gap: 14px; }
  .field-row {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 14px; background: var(--panel); border: 1px solid var(--line-soft); border-radius: 9px;
  }
  .field-ic { color: var(--text-mute); width: 16px; flex-shrink: 0; }
  .field-meta { flex: 1; min-width: 0; }
  .field-label { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-mute); font-weight: 600; }
  .field-value { font-size: 13.5px; margin-top: 2px; word-break: break-all; }
  .field-value.mono { font-family: 'JetBrains Mono', monospace; font-size: 12.5px; }
  .field-copy { background: transparent; border: none; color: var(--text-mute); cursor: pointer; padding: 4px; }
  .field-copy:hover { color: var(--text); }

  .status-pill {
    padding: 3px 9px; border-radius: 6px; font-size: 11px; font-weight: 600;
    display: inline-flex; align-items: center; gap: 5px;
  }
  .status-pill .dot { width: 6px; height: 6px; border-radius: 999px; background: currentColor; }
  .status-pill.running { background: rgba(110,139,255,0.12); color: var(--blue); }
  .status-pill.ok { background: var(--green-soft); color: var(--green); }
  .status-pill.error { background: rgba(244,63,94,0.12); color: var(--red); }
</style>
</head>
<body>
<header class="topbar">
  <div class="brand-mark"></div>
  <div class="brand-text">
    <div class="brand-name">Relay</div>
    <div class="brand-sub">LinkedIn-Suche</div>
  </div>
  <div class="spacer"></div>
  <a class="lnk" href="/premium/">→ Voller Premium-Bot</a>
  <a class="lnk" href="/classic">→ Klassisches Cockpit</a>
</header>

<main class="container">
  <h1>LinkedIn-Ansprechpartner finden</h1>
  <div class="sub">Suchbegriff eingeben (z. B. „Marketing“). Der Bot sucht über Tavily + Serper nach Firmen, identifiziert Ansprechpartner und liefert Kontaktdaten + LinkedIn-Profile.</div>

  <div class="search-row">
    <div class="input-shell">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
      <input id="qInput" placeholder="Branche / Keyword (z. B. Marketing, Bauunternehmen, Steuerberater …)" autocomplete="off">
    </div>
    <div class="input-shell city">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
      <input id="cityInput" placeholder="Stadt (leer = ganz Deutschland)" autocomplete="off">
    </div>
    <div class="input-shell count">
      <span class="lbl">Anzahl</span>
      <input id="cntInput" type="number" min="5" max="1000" value="20">
    </div>
    <button class="btn" id="searchBtn" onclick="startSearch()">Suchen</button>
    <button class="btn" id="refreshBtn" onclick="loadResults(true)" style="background: var(--panel); border: 1px solid var(--line); color: var(--text);" title="Aktuelle Pipeline-Leads aus output/latest/outreach_pipeline.json laden">↻ Zwischenstand</button>
  </div>

  <div class="progress-panel hidden" id="progPanel">
    <div class="progress-top">
      <div>
        <span class="status-pill running" id="statusPill"><span class="dot"></span><span id="statusTxt">läuft</span></span>
        <span style="margin-left: 10px;" id="progLabel">Initialisiere …</span>
      </div>
      <div style="display:flex; align-items:center; gap:14px">
        <span class="progress-phase" id="progPhase">init</span>
        <span class="mono" style="font-size:13px; font-weight:600;" id="progPct">0%</span>
      </div>
    </div>
    <div class="progress-bar"><div class="progress-fill" id="progFill" style="width:0%"></div></div>
    <div class="progress-msg" id="progMsg">—</div>
  </div>

  <div class="panel">
    <div class="panel-head">
      <span>Gefundene Ansprechpartner</span>
      <span class="count-pill" id="resultCount">0</span>
    </div>
    <div style="max-height: 65vh; overflow: auto;">
      <table>
        <thead>
          <tr>
            <th style="width: 18%">Name</th>
            <th style="width: 22%">Firma</th>
            <th style="width: 16%">Telefon</th>
            <th style="width: 19%">E-Mail</th>
            <th style="width: 25%; text-align: right">Aktionen</th>
          </tr>
        </thead>
        <tbody id="leadsBody">
          <tr><td colspan="5" class="empty"><div class="ic">○</div>Noch keine Suche gestartet.<br><span style="font-size:12px">Suchbegriff eingeben und „Suchen“ klicken.</span></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</main>

<div class="modal-scrim" id="modalScrim" onclick="closeModal(event)">
  <div class="modal" onclick="event.stopPropagation()">
    <div class="modal-head">
      <div class="modal-avatar" id="mAvatar">?</div>
      <div style="flex:1; min-width:0">
        <div class="modal-name" id="mName">—</div>
        <div class="modal-company" id="mCompany">—</div>
      </div>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div class="field-list">
      <div class="field-row">
        <svg class="field-ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        <div class="field-meta">
          <div class="field-label">Name</div>
          <div class="field-value" id="mfName">—</div>
        </div>
        <button class="field-copy" onclick="copyField('mfName')" title="Kopieren">⧉</button>
      </div>
      <div class="field-row">
        <svg class="field-ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
        <div class="field-meta">
          <div class="field-label">Telefon</div>
          <div class="field-value mono" id="mfPhone">—</div>
        </div>
        <button class="field-copy" onclick="copyField('mfPhone')" title="Kopieren">⧉</button>
      </div>
      <div class="field-row">
        <svg class="field-ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
        <div class="field-meta">
          <div class="field-label">E-Mail</div>
          <div class="field-value mono" id="mfEmail">—</div>
        </div>
        <button class="field-copy" onclick="copyField('mfEmail')" title="Kopieren">⧉</button>
      </div>
      <div class="field-row">
        <svg class="field-ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/><rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/></svg>
        <div class="field-meta">
          <div class="field-label">LinkedIn</div>
          <div class="field-value mono" id="mfLi">—</div>
        </div>
        <button class="field-copy" onclick="copyField('mfLi')" title="Kopieren">⧉</button>
      </div>
    </div>
  </div>
</div>

<script>
var _jobId = null;
var _pollTimer = null;
var _searchStartedAt = "";
var _startedTs = 0;
var _lastPct = -1;
var _lastPctChangeTs = 0;
var _elapsedTimer = null;
var _running = false;        // Hard-Lock gegen Doppel-Trigger
var _liResolveCache = {};    // company+name → URL Cache

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function setStatus(state, txt) {
  var pill = document.getElementById('statusPill');
  pill.classList.remove('running', 'ok', 'error');
  pill.classList.add(state);
  document.getElementById('statusTxt').textContent = txt;
}

function setProgress(pct, phase, msg) {
  document.getElementById('progFill').style.width = (pct || 0) + '%';
  document.getElementById('progPct').textContent = Math.round(pct || 0) + '%';
  document.getElementById('progPhase').textContent = phase || '—';
  document.getElementById('progMsg').textContent = msg || '—';
}

function showProgressPanel() {
  document.getElementById('progPanel').classList.remove('hidden');
}

function startSearch() {
  if (_running) return;                       // Hard-Lock
  var btn = document.getElementById('searchBtn');
  if (btn.disabled) return;                   // doppelter Schutz
  if (_pollTimer)    { clearTimeout(_pollTimer);    _pollTimer = null; }
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
  _jobId = null;
  _running = true;

  var q    = (document.getElementById('qInput').value    || '').trim();
  var city = (document.getElementById('cityInput').value || '').trim();
  var cnt  = parseInt(document.getElementById('cntInput').value, 10) || 20;
  if (cnt < 5) cnt = 5;
  if (cnt > 1000) cnt = 1000;
  if (!q) { alert('Bitte einen Suchbegriff eingeben.'); return; }

  btn.disabled = true;
  btn.textContent = 'Suche läuft …';
  _startedTs = Date.now();
  _lastPct = -1;
  _lastPctChangeTs = _startedTs;

  document.getElementById('leadsBody').innerHTML =
    '<tr><td colspan="5" class="empty"><div class="ic">⟳</div>Suche läuft …<br><span style="font-size:12px">Tavily + Serper analysieren Firmen und Ansprechpartner.</span></td></tr>';
  document.getElementById('resultCount').textContent = '...';

  showProgressPanel();
  setStatus('running', 'läuft');
  setProgress(2, 'init', 'Starte Suche: ' + q + (city ? ' · ' + city : ' · Deutschland') + ' (' + cnt + ')');
  document.getElementById('progLabel').textContent = q + (city ? ' · ' + city : ' · DE') + ' · ' + cnt + ' Leads';

  // Elapsed-Timer
  _elapsedTimer = setInterval(updateElapsed, 1000);
  updateElapsed();

  fetch('/api/linkedin/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ industry: q, city: city, count: cnt })
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) {
      finishSearch('error', d.error + ': ' + (d.msg || ''));
      return;
    }
    _jobId = d.job_id;
    pollJob();
  })
  .catch(function(e) {
    finishSearch('error', 'Request fehlgeschlagen: ' + e.message);
  });
}

function updateElapsed() {
  if (!_startedTs) return;
  var s = Math.floor((Date.now() - _startedTs) / 1000);
  var mm = Math.floor(s / 60), ss = s % 60;
  var elapsedStr = mm + ':' + (ss < 10 ? '0' : '') + ss;
  var stalled = Date.now() - _lastPctChangeTs;
  var suffix = '';
  if (stalled > 20000) {
    var stalledS = Math.floor(stalled / 1000);
    suffix = ' · keine Bewegung seit ' + stalledS + 's (Backend verarbeitet, bitte warten)';
  }
  document.getElementById('progLabel').innerHTML =
    document.getElementById('progLabel').innerHTML.replace(/ · ⏱.*$/, '') +
    ' · ⏱ ' + elapsedStr + suffix;
}

function finishSearch(state, msg) {
  setStatus(state, state === 'ok' ? 'fertig' : 'Fehler');
  if (state === 'error') setProgress(0, 'error', msg);
  var btn = document.getElementById('searchBtn');
  btn.disabled = false;
  btn.textContent = 'Suchen';
  if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
  _running = false;
}

function pollJob() {
  if (!_jobId) return;
  fetch('/api/job/' + _jobId)
    .then(function(r) { return r.json(); })
    .then(function(j) {
      if (!j || j.error) {
        finishSearch('error', 'Job nicht gefunden');
        return;
      }
      var pct   = j.progress_pct   || 0;
      var phase = j.progress_phase || '—';
      var msg   = j.progress_msg   || '';
      setProgress(pct, phase, msg);

      // Track if pct actually moves
      if (pct !== _lastPct) {
        _lastPct = pct;
        _lastPctChangeTs = Date.now();
      }

      if (j.status === 'ok') {
        setProgress(100, 'done', msg || 'Suche fertig');
        finishSearch('ok', 'fertig');
        loadResults();
      } else if (j.status === 'error') {
        setProgress(pct, 'error', msg || 'Suche fehlgeschlagen');
        finishSearch('error', msg || 'Suche fehlgeschlagen');
        document.getElementById('leadsBody').innerHTML =
          '<tr><td colspan="5" class="empty"><div class="ic">⚠</div>Suche fehlgeschlagen.<br><span style="font-size:12px">' + escapeHtml(msg || '') + '</span></td></tr>';
      } else {
        _pollTimer = setTimeout(pollJob, 1500);
      }
    })
    .catch(function(e) {
      _pollTimer = setTimeout(pollJob, 2500);
    });
}

function loadResults(manualRefresh) {
  fetch('/api/leads')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var items = d.items || [];
      var cutoff = d.last_search_started_at || '';
      var fresh = items;
      if (cutoff && !manualRefresh) {
        fresh = items.filter(function(l) { return (l.added_at || '') >= cutoff; });
      }
      // Manual refresh OR no fresh leads: sort by added_at desc, take top 100
      if (manualRefresh || (fresh.length === 0 && items.length > 0)) {
        fresh = items.slice().sort(function(a, b) { return (b.added_at || '').localeCompare(a.added_at || ''); }).slice(0, 100);
      }
      window._allLeads = fresh;
      renderLeads(fresh);
    })
    .catch(function(e) {
      document.getElementById('leadsBody').innerHTML =
        '<tr><td colspan="5" class="empty"><div class="ic">⚠</div>Ergebnisse konnten nicht geladen werden.<br><span style="font-size:12px">' + escapeHtml(e.message) + '</span></td></tr>';
    });
}

function liUrlFor(lead) {
  // 1. Direct LinkedIn person URL if known
  if (lead.linkedin_person && /linkedin\.com\/in\//i.test(lead.linkedin_person)) return lead.linkedin_person;
  return null;  // Need to resolve via Serper
}

function openLinkedInProfile(idx, btnEl) {
  var l = (window._allLeads || [])[idx];
  if (!l) return;
  // 1) Direct URL?
  var direct = liUrlFor(l);
  if (direct) { window.open(direct, '_blank', 'noopener'); return; }

  // 2) Cache check
  var ck = (l.company || '') + '|' + (l.contact || '');
  if (_liResolveCache[ck]) { window.open(_liResolveCache[ck], '_blank', 'noopener'); return; }

  // 3) Resolve via Serper
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Suche…'; }
  var qs = 'name=' + encodeURIComponent(l.contact || '') +
           '&company=' + encodeURIComponent(l.company || '');
  fetch('/api/premium/li-resolve?' + qs)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var url = d.url || d.fallback_url || ('https://www.google.com/search?q=' + encodeURIComponent('site:linkedin.com/in "' + (l.contact || l.company || '') + '"'));
      _liResolveCache[ck] = url;
      window.open(url, '_blank', 'noopener');
    })
    .catch(function(e) {
      window.open('https://www.google.com/search?q=' + encodeURIComponent('site:linkedin.com/in "' + (l.contact || l.company || '') + '"'), '_blank', 'noopener');
    })
    .finally(function() {
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'LinkedIn-Profil'; }
    });
}

function webUrlFor(lead) {
  var w = (lead.website || '').trim();
  if (!w) return '';
  return /^https?:\/\//.test(w) ? w : ('https://' + w);
}

function dedupeLeads(leads) {
  var seen = {};
  var out = [];
  for (var i = 0; i < leads.length; i++) {
    var l = leads[i];
    var key = ((l.company || '') + '|' + (l.contact || '') + '|' + (l.email || '')).toLowerCase();
    if (key === '||' || seen[key]) continue;
    seen[key] = true;
    out.push(l);
  }
  return out;
}

function renderLeads(leads) {
  var body = document.getElementById('leadsBody');
  // Dedupe + Re-index for stable button refs
  leads = dedupeLeads(leads || []);
  window._allLeads = leads;
  document.getElementById('resultCount').textContent = leads.length;
  if (leads.length === 0) {
    body.innerHTML = '<tr><td colspan="5" class="empty"><div class="ic">∅</div>Keine Ansprechpartner gefunden.<br><span style="font-size:12px">Anderen Suchbegriff probieren oder Anzahl erhöhen.</span></td></tr>';
    return;
  }
  body.innerHTML = leads.map(function(l, i) {
    var name    = (l.contact || '').trim();
    var company = (l.company || '—').trim();
    var phone   = (l.phone   || '').trim();
    var email   = (l.email   || '').trim();
    var webUrl  = webUrlFor(l);
    var hasContact = !!name;
    var hasDirectLi = !!(l.linkedin_person && /linkedin\.com\/in\//i.test(l.linkedin_person));

    // Name column: show name OR clear "Kein Ansprechpartner" marker
    var nameCell = hasContact
      ? '<div class="name">' + escapeHtml(name) + '</div>'
      : '<span class="mute">Kein Ansprechpartner</span>';

    // LinkedIn button: enabled when we have anything to search by (name OR company)
    var liEnabled = hasContact || (company && company !== '—');
    var liLabel = hasDirectLi ? 'LinkedIn ✓' : 'LinkedIn-Profil';
    var liBtn = liEnabled
      ? '<button class="btn ghost li" onclick="openLinkedInProfile(' + i + ', this)" title="' + (hasDirectLi ? 'Direkter Link' : 'Sucht per Google Platz 1') + '">' + liLabel + '</button>'
      : '<button class="btn ghost li" disabled title="Keine Daten">LinkedIn</button>';

    // Contact button: disable when ALL contact fields are empty
    var kdEnabled = !!(name || phone || email);
    var kdBtn = kdEnabled
      ? '<button class="btn ghost kd" onclick="showContact(' + i + ')">Kontaktdaten</button>'
      : '<button class="btn ghost kd" disabled title="Keine Kontaktdaten">Kontaktdaten</button>';

    return '<tr>' +
      '<td>' + nameCell + '</td>' +
      '<td class="firma">' + escapeHtml(company) + '</td>' +
      '<td class="mono">' + (phone ? escapeHtml(phone) : '<span class="mute">—</span>') + '</td>' +
      '<td class="mono">' + (email ? escapeHtml(email) : '<span class="mute">—</span>') + '</td>' +
      '<td><div class="actions">' +
        liBtn +
        (webUrl
          ? '<button class="btn ghost web" onclick="openUrl(\'' + escapeAttr(webUrl) + '\')">Website</button>'
          : '<button class="btn ghost web" disabled title="Keine Website">Website</button>') +
        kdBtn +
      '</div></td>' +
    '</tr>';
  }).join('');
}

function escapeAttr(s) {
  return String(s || '').replace(/'/g, '&#39;').replace(/"/g, '&quot;');
}

function openUrl(u) {
  if (!u) { alert('Keine URL verfügbar.'); return; }
  window.open(u, '_blank', 'noopener');
}

function showContact(i) {
  var l = (window._allLeads || [])[i];
  if (!l) return;
  document.getElementById('mName').textContent     = l.contact || '—';
  document.getElementById('mCompany').textContent  = l.company || '—';
  document.getElementById('mAvatar').textContent   = initialsOf(l.contact || l.company || '?');
  document.getElementById('mfName').textContent    = l.contact || '—';
  document.getElementById('mfPhone').textContent   = l.phone   || '—';
  document.getElementById('mfEmail').textContent   = l.email   || '—';
  var li = l.linkedin_person || l.linkedin_company || '';
  document.getElementById('mfLi').textContent      = li        || '—';
  document.getElementById('modalScrim').classList.add('open');
}

function initialsOf(name) {
  var p = String(name || '?').trim().split(/\s+/).filter(Boolean);
  return p.slice(0, 2).map(function(w){ return w[0]; }).join('').toUpperCase() || '?';
}

function closeModal(e) {
  if (e && e.target.id !== 'modalScrim' && e.target.className !== 'modal-close') return;
  document.getElementById('modalScrim').classList.remove('open');
}

function copyField(id) {
  var v = document.getElementById(id).textContent || '';
  if (!v || v === '—') return;
  try {
    navigator.clipboard.writeText(v);
  } catch(e) {
    var ta = document.createElement('textarea'); ta.value = v; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
  }
}

document.getElementById('qInput').addEventListener('keypress', function(e) {
  if (e.key === 'Enter') startSearch();
});
document.getElementById('cityInput').addEventListener('keypress', function(e) {
  if (e.key === 'Enter') startSearch();
});
document.getElementById('cntInput').addEventListener('keypress', function(e) {
  if (e.key === 'Enter') startSearch();
});
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') document.getElementById('modalScrim').classList.remove('open');
});

// Show last existing leads on first load
loadResults();
</script>
</body>
</html>
"""



# ── Premium SPA HTML ─────────────────────────────────────────────────────────

PREMIUM_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>B2B Cockpit · Premium</title>
<style>
:root {
  --bg:#0a0d14; --bg2:#10141f; --surface:#161b2a; --surface2:#1d2336;
  --border:#2a3050; --border2:#3a4170;
  --text:#e8ecf5; --muted:#7a8499; --dim:#5a6378;
  --accent:#6c8eff; --accent2:#a78bfa; --accent3:#f472b6;
  --ok:#10b981; --warn:#f59e0b; --err:#ef4444; --hot:#fb923c;
  --gold:#fbbf24;
  --grad-1:linear-gradient(135deg,#6c8eff 0%,#a78bfa 100%);
  --grad-2:linear-gradient(135deg,#fb923c 0%,#ef4444 100%);
  --grad-3:linear-gradient(135deg,#10b981 0%,#0ea5e9 100%);
  --grad-bg:radial-gradient(ellipse at top left,rgba(108,142,255,.08),transparent 50%),radial-gradient(ellipse at bottom right,rgba(167,139,250,.06),transparent 50%);
  --shadow:0 8px 32px rgba(0,0,0,.4);
  --shadow-glow:0 0 24px rgba(108,142,255,.15);
  --r-sm:6px; --r:10px; --r-lg:14px; --r-xl:20px;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{
  background:var(--bg) var(--grad-bg);background-attachment:fixed;
  color:var(--text);font-family:-apple-system,'Inter','Segoe UI',Roboto,sans-serif;
  font-size:14px;line-height:1.5;overflow:hidden;
}
button,input,select{font-family:inherit;font-size:inherit;color:inherit}
button{cursor:pointer;border:none;background:transparent}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:var(--bg2)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:10px}
::-webkit-scrollbar-thumb:hover{background:var(--border2)}

/* ── Layout ── */
.app{display:grid;grid-template-columns:240px 1fr;grid-template-rows:64px 1fr;height:100vh}
.brand{
  grid-column:1;grid-row:1;
  display:flex;align-items:center;gap:12px;padding:0 22px;
  border-right:1px solid var(--border);border-bottom:1px solid var(--border);
  background:var(--surface);
}
.brand-mark{
  width:32px;height:32px;border-radius:8px;
  background:var(--grad-1);
  display:grid;place-items:center;font-weight:900;color:#fff;
  box-shadow:var(--shadow-glow);
}
.brand-text{display:flex;flex-direction:column;line-height:1.2}
.brand-text strong{font-weight:800;font-size:15px;letter-spacing:-.3px}
.brand-text small{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:1px}

.topbar{
  grid-column:2;grid-row:1;
  display:flex;align-items:center;gap:14px;padding:0 24px;
  border-bottom:1px solid var(--border);
  background:var(--surface);
  backdrop-filter:blur(12px);
}
.topbar-stats{display:flex;gap:18px;flex:1}
.stat-pill{
  display:flex;flex-direction:column;line-height:1.1;
  padding:0 12px;border-right:1px solid var(--border);
}
.stat-pill:last-of-type{border-right:none}
.stat-pill .lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px}
.stat-pill .val{font-size:20px;font-weight:800;margin-top:3px;font-feature-settings:"tnum"}
.stat-pill .val.ok{color:var(--ok)}.stat-pill .val.warn{color:var(--warn)}
.stat-pill .val.hot{color:var(--hot)}.stat-pill .val.acc{color:var(--accent)}

.live-dot{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)}
.live-dot::before{content:'';width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 8px var(--ok);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* ── Sidebar ── */
.sidebar{
  grid-column:1;grid-row:2;
  border-right:1px solid var(--border);
  background:var(--surface);
  overflow-y:auto;padding:18px 14px;
}
.nav-section{margin-bottom:22px}
.nav-section h4{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;padding:0 8px}
.nav-item{
  display:flex;align-items:center;gap:10px;
  padding:9px 12px;margin-bottom:2px;
  border-radius:var(--r-sm);
  color:var(--muted);font-size:13px;font-weight:500;
  cursor:pointer;transition:all .15s;
  position:relative;
}
.nav-item:hover{background:var(--surface2);color:var(--text)}
.nav-item.active{background:rgba(108,142,255,.12);color:var(--accent);font-weight:600}
.nav-item.active::before{content:'';position:absolute;left:0;top:6px;bottom:6px;width:3px;background:var(--accent);border-radius:2px}
.nav-item .ico{font-size:15px;width:18px}
.nav-item .badge{margin-left:auto;font-size:10px;padding:2px 6px;border-radius:10px;background:var(--surface2);color:var(--muted);font-weight:700}
.nav-item.active .badge{background:var(--accent);color:#fff}
.nav-item .badge.hot{background:var(--hot);color:#fff}

/* ── Main ── */
.main{
  grid-column:2;grid-row:2;
  overflow-y:auto;padding:20px 24px 80px;
}

/* ── Action Toolbar ── */
.toolbar{
  display:flex;flex-wrap:wrap;gap:10px;align-items:center;
  padding:14px 16px;margin-bottom:18px;
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r-lg);box-shadow:var(--shadow);
}
.tb-group{display:flex;gap:6px;align-items:center;padding:4px;background:var(--bg2);border-radius:var(--r);border:1px solid var(--border)}
.tb-input{
  background:transparent;border:none;color:var(--text);
  padding:6px 10px;font-size:12px;width:140px;outline:none;
}
.tb-input.sm{width:60px;text-align:center}
.tb-input::placeholder{color:var(--dim)}
.btn{
  display:inline-flex;align-items:center;gap:6px;
  padding:8px 14px;border-radius:var(--r-sm);
  font-size:12px;font-weight:600;letter-spacing:.2px;
  cursor:pointer;transition:all .15s;
  border:1px solid transparent;white-space:nowrap;
}
.btn.primary{background:var(--grad-1);color:#fff;border-color:transparent;box-shadow:0 4px 14px rgba(108,142,255,.3)}
.btn.primary:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(108,142,255,.5)}
.btn.success{background:var(--ok);color:#fff}
.btn.success:hover{background:#0e9f6e}
.btn.warn{background:var(--warn);color:#000}
.btn.warn:hover{background:#d97706}
.btn.danger{background:var(--err);color:#fff}
.btn.ghost{background:var(--surface2);color:var(--text);border-color:var(--border)}
.btn.ghost:hover{background:var(--border)}
.btn.gold{background:var(--grad-2);color:#fff;font-weight:700}
.btn.gold:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(251,146,60,.5)}
.btn.sm{padding:5px 10px;font-size:11px}
.btn.icon-only{padding:6px 8px}

/* ── KPI Cards ── */
.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:20px}
.kpi-card{
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r-lg);padding:16px 18px;
  position:relative;overflow:hidden;
  transition:all .2s;
}
.kpi-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:var(--shadow-glow)}
.kpi-card::after{content:'';position:absolute;top:0;left:0;width:100%;height:3px;background:var(--grad-1)}
.kpi-card.ok::after{background:var(--grad-3)}
.kpi-card.hot::after{background:var(--grad-2)}
.kpi-card.warn::after{background:linear-gradient(90deg,var(--warn),var(--gold))}
.kpi-lbl{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px;display:flex;align-items:center;gap:6px}
.kpi-lbl .ico{font-size:14px}
.kpi-val{font-size:30px;font-weight:800;line-height:1;font-feature-settings:"tnum"}
.kpi-sub{font-size:11px;color:var(--muted);margin-top:6px}
.kpi-spark{height:24px;margin-top:8px;display:flex;align-items:flex-end;gap:2px}
.kpi-spark span{flex:1;background:var(--accent);border-radius:1px;opacity:.4;transition:opacity .15s}
.kpi-spark span.bright{opacity:1}

/* ── Cards / Sections ── */
.section{
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r-lg);padding:18px 20px;margin-bottom:18px;
}
.section-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.section-head h2{font-size:15px;font-weight:700;letter-spacing:-.2px}
.section-head .badge{font-size:10px;padding:2px 8px;background:var(--surface2);border-radius:10px;color:var(--muted);font-weight:700;letter-spacing:.5px}
.section-head .actions{margin-left:auto;display:flex;gap:6px}

/* ── Tables ── */
.tbl-wrap{overflow-x:auto;border-radius:var(--r);border:1px solid var(--border)}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl thead th{
  text-align:left;padding:10px 12px;font-size:11px;text-transform:uppercase;
  color:var(--muted);letter-spacing:.5px;font-weight:700;
  background:var(--bg2);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:1;
}
.tbl tbody td{padding:11px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
.tbl tbody tr{transition:background .12s}
.tbl tbody tr:hover{background:rgba(108,142,255,.04)}
.tbl tbody tr:last-child td{border-bottom:none}
.tbl tbody tr.clickable{cursor:pointer}

.cell-company{font-weight:600}
.cell-company small{display:block;color:var(--muted);font-weight:400;margin-top:2px}
.cell-actions{white-space:nowrap;display:flex;gap:4px;justify-content:flex-end}

/* ── Pills / Badges ── */
.pill{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:10px;font-size:10px;font-weight:700;letter-spacing:.3px}
.pill.ok{background:rgba(16,185,129,.12);color:var(--ok)}
.pill.warn{background:rgba(245,158,11,.12);color:var(--warn)}
.pill.err{background:rgba(239,68,68,.12);color:var(--err)}
.pill.hot{background:rgba(251,146,60,.12);color:var(--hot)}
.pill.acc{background:rgba(108,142,255,.12);color:var(--accent)}
.pill.dim{background:var(--surface2);color:var(--muted)}

.score-chip{
  display:inline-flex;align-items:center;justify-content:center;
  min-width:38px;padding:3px 10px;border-radius:14px;
  font-size:12px;font-weight:800;font-feature-settings:"tnum";
  background:var(--grad-1);color:#fff;
}
.score-chip.hot{background:var(--grad-2)}
.score-chip.dim{background:var(--surface2);color:var(--muted)}

/* ── Filter Bar ── */
.filter-bar{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.search-box{
  flex:1;min-width:240px;display:flex;align-items:center;gap:8px;
  background:var(--bg2);border:1px solid var(--border);
  padding:6px 12px;border-radius:var(--r);
}
.search-box input{flex:1;background:transparent;border:none;outline:none;color:var(--text);font-size:13px;padding:4px 0}
.search-box::before{content:'🔍';opacity:.5;font-size:13px}
.chip-group{display:flex;gap:4px;background:var(--bg2);padding:3px;border-radius:var(--r);border:1px solid var(--border)}
.chip{padding:5px 11px;font-size:11px;color:var(--muted);border-radius:6px;cursor:pointer;font-weight:600;transition:all .12s}
.chip:hover{color:var(--text)}
.chip.active{background:var(--accent);color:#fff}

/* ── Drawer ── */
.drawer-bg{position:fixed;inset:0;background:rgba(0,0,0,.5);backdrop-filter:blur(4px);z-index:200;opacity:0;pointer-events:none;transition:opacity .2s}
.drawer-bg.open{opacity:1;pointer-events:auto}
.drawer{
  position:fixed;top:0;right:0;bottom:0;width:600px;max-width:96vw;z-index:201;
  background:var(--surface);border-left:1px solid var(--border);
  transform:translateX(100%);transition:transform .25s cubic-bezier(.4,0,.2,1);
  display:flex;flex-direction:column;
}
.drawer.open{transform:translateX(0)}
.drawer-head{display:flex;align-items:center;gap:14px;padding:18px 22px;border-bottom:1px solid var(--border)}
.drawer-head h3{font-size:16px;font-weight:700;flex:1}
.drawer-body{flex:1;overflow-y:auto;padding:20px 22px}
.drawer-actions{padding:14px 22px;border-top:1px solid var(--border);display:flex;gap:8px;flex-wrap:wrap;background:var(--bg2)}
.drawer-section{margin-bottom:18px}
.drawer-section h5{font-size:10px;text-transform:uppercase;color:var(--muted);letter-spacing:.8px;margin-bottom:8px}
.drawer-section .row{display:grid;grid-template-columns:130px 1fr;gap:10px;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px}
.drawer-section .row:last-child{border-bottom:none}
.drawer-section .row span:first-child{color:var(--muted);font-size:12px}
.email-box{background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);padding:12px 14px;font-size:13px;line-height:1.6;white-space:pre-wrap;word-wrap:break-word;font-family:-apple-system,'Segoe UI',sans-serif;max-height:220px;overflow-y:auto}
.email-box.draft{border-color:var(--ok);background:rgba(16,185,129,.04)}

/* ── Toast ── */
.toast-stack{position:fixed;top:80px;right:24px;z-index:300;display:flex;flex-direction:column;gap:10px;max-width:420px}
.toast{
  background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--accent);
  border-radius:var(--r);padding:12px 16px;font-size:13px;
  box-shadow:var(--shadow);min-width:300px;
  display:flex;align-items:flex-start;gap:10px;
  animation:slideIn .25s cubic-bezier(.4,0,.2,1);
}
.toast.ok{border-left-color:var(--ok)}.toast.err{border-left-color:var(--err)}.toast.warn{border-left-color:var(--warn)}
.toast.fade{animation:slideOut .25s forwards}
.toast .ico{font-size:18px}
.toast .title{font-weight:700;margin-bottom:2px}
.toast .msg{color:var(--muted);font-size:12px;line-height:1.4;word-wrap:break-word;overflow:hidden;text-overflow:ellipsis;max-width:340px}
@keyframes slideIn{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}
@keyframes slideOut{to{transform:translateX(120%);opacity:0}}

/* ── Job Indicator ── */
.job-bar{position:fixed;bottom:0;left:0;right:0;height:auto;background:var(--surface);border-top:1px solid var(--border);padding:10px 24px;font-size:12px;display:flex;align-items:center;gap:14px;z-index:50}
.job-bar.hidden{display:none}
.job-bar .spinner{width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── Loader ── */
.loader{display:flex;align-items:center;justify-content:center;padding:40px;color:var(--muted);font-size:13px;gap:10px}
.empty{padding:40px;text-align:center;color:var(--muted);font-size:13px;border:1px dashed var(--border);border-radius:var(--r)}
.empty .big{font-size:32px;display:block;margin-bottom:10px;opacity:.5}

/* ── Page sections (tabs) ── */
.page{display:none;animation:fadeIn .2s}
.page.active{display:block}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}

/* ── Hot ribbon ── */
.hot-strip{
  background:linear-gradient(135deg,rgba(251,146,60,.12),rgba(239,68,68,.08));
  border:1px solid rgba(251,146,60,.3);
  border-radius:var(--r-lg);padding:14px 18px;margin-bottom:14px;
  display:flex;align-items:center;gap:12px;
}
.hot-strip .ico{font-size:22px}
.hot-strip strong{color:var(--hot);font-size:14px}

@media(max-width:980px){
  .app{grid-template-columns:1fr}
  .sidebar{display:none}
  .brand{grid-column:1}
  .topbar{grid-column:1}
  .main{grid-column:1}
  .drawer{width:100vw}
}
.intent-link,.intent-link:visited{color:var(--text);text-decoration:none}
.intent-link:hover{color:#fff;text-decoration:underline}

/* Claude Design Reference integration: dark premium operator shell */
:root{
  --bg:#0b0d12;--bg-elev:#11141b;--panel:#141823;--panel-2:#1a1f2c;
  --line:#232a3a;--line-soft:#1c2230;--text:#e8ecf3;--text-dim:#a3acc0;--text-mute:#6b7588;
  --accent:#6e8bff;--accent-2:#8d6bff;--green:#34d39a;--green-soft:rgba(52,211,154,.14);
  --yellow:#f0b840;--yellow-soft:rgba(240,184,64,.14);--red:#ff6b7a;--red-soft:rgba(255,107,122,.14);
  --blue:#5fb6ff;--blue-soft:rgba(95,182,255,.14);--violet:#b08bff;--violet-soft:rgba(176,139,255,.14);
  --surface:var(--panel);--surface2:var(--panel-2);--border:var(--line);--border2:#31394f;
  --muted:var(--text-dim);--dim:var(--text-mute);--ok:var(--green);--warn:var(--yellow);--err:var(--red);
  --shadow-card:0 1px 0 rgba(255,255,255,.03) inset,0 12px 32px -16px rgba(0,0,0,.6);
}
body{background:var(--bg);font-family:Manrope,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:0}
body::before{content:"";position:fixed;inset:0;background:radial-gradient(900px 500px at 12% -10%,rgba(110,139,255,.08),transparent 60%),radial-gradient(800px 600px at 110% 10%,rgba(141,107,255,.06),transparent 60%);pointer-events:none;z-index:0}
.app{position:relative;z-index:1;grid-template-columns:260px 1fr;grid-template-rows:64px 1fr}
.brand,.sidebar{background:linear-gradient(180deg,#0d1018 0%,#0a0c12 100%);border-color:var(--line-soft)}
.brand-mark{background:radial-gradient(120% 120% at 0% 0%,rgba(110,139,255,.55),transparent 60%),linear-gradient(135deg,#1d2538 0%,#0e111a 100%);border:1px solid #2a3450;color:transparent;position:relative}
.brand-mark::after{content:"";position:absolute;inset:8px;border-radius:4px;background:linear-gradient(135deg,var(--accent),var(--accent-2));clip-path:polygon(0 30%,50% 0,100% 30%,100% 70%,50% 100%,0 70%)}
.topbar{background:rgba(11,13,18,.78);backdrop-filter:blur(14px);border-color:var(--line-soft)}
.nav-item{border:1px solid transparent;border-radius:10px}
.nav-item.active{background:linear-gradient(180deg,rgba(110,139,255,.16),rgba(110,139,255,.06));color:var(--text);border-color:rgba(110,139,255,.22);box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}
.section,.panel,.kpi-card,.premium-card{background:linear-gradient(180deg,var(--panel) 0%,#11151f 100%);border:1px solid var(--line-soft);border-radius:14px;box-shadow:var(--shadow-card)}
.kpi-card{overflow:hidden}.kpi-card::after{height:2px;background:var(--accent)}.kpi-card.ok::after{background:var(--green)}.kpi-card.warn::after{background:var(--yellow)}.kpi-card.hot::after{background:var(--red)}
.btn.primary{background:linear-gradient(180deg,#7892ff,#5b78f0);box-shadow:0 1px 0 rgba(255,255,255,.2) inset,0 8px 20px -8px rgba(110,139,255,.55)}
.btn.success{background:linear-gradient(180deg,#3ee0a6,#25b889);color:#06231a}.btn.warn{background:rgba(240,184,64,.12);color:var(--yellow);border-color:rgba(240,184,64,.25)}.btn.danger{background:rgba(255,107,122,.12);color:var(--red);border-color:rgba(255,107,122,.25)}
.pill.ok{background:var(--green-soft);color:var(--green)}.pill.warn{background:var(--yellow-soft);color:var(--yellow)}.pill.err{background:var(--red-soft);color:var(--red)}.pill.acc{background:var(--blue-soft);color:var(--blue)}.pill.violet{background:var(--violet-soft);color:var(--violet)}
.tbl thead th{background:rgba(255,255,255,.015);color:var(--text-mute);letter-spacing:.12em}.tbl tbody tr:hover{background:rgba(110,139,255,.045)}
.premium-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin-bottom:18px}
.premium-grid-4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:18px}
#page-dashboard .dash-legacy{display:none !important}
.source-row{display:flex;align-items:center;gap:12px;padding:14px 16px;border-bottom:1px solid var(--line-soft)}.source-row:last-child{border-bottom:none}
.source-mark{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:grid;place-items:center;font-size:11px;font-weight:800}
.premium-title{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:800}.premium-sub{color:var(--text-mute);font-size:12px;margin-top:2px}
.tier{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:7px;font-family:ui-monospace,monospace;font-size:11px;font-weight:800}
.tier.A{background:var(--green-soft);color:var(--green);border:1px solid rgba(52,211,154,.3)}.tier.B{background:var(--blue-soft);color:var(--blue);border:1px solid rgba(95,182,255,.3)}.tier.C{background:var(--yellow-soft);color:var(--yellow);border:1px solid rgba(240,184,64,.3)}.tier.D{background:var(--red-soft);color:var(--red);border:1px solid rgba(255,107,122,.3)}
.lead-cell{display:flex;align-items:center;gap:10px}.lead-avatar{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,#2a3a66,#1a2240);display:grid;place-items:center;color:#c9d4ff;font-weight:800}.lead-name{font-weight:700}.lead-sub{color:var(--text-mute);font-size:12px}
.drawer{background:var(--panel);box-shadow:-24px 0 60px -20px rgba(0,0,0,.8)}.drawer-section .row{border-color:var(--line-soft)}
@media(max-width:1180px){.premium-grid,.premium-grid-4{grid-template-columns:1fr}.kpi-row{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
</head>
<body>

<div class="app">
  <!-- BRAND -->
  <div class="brand">
    <div class="brand-mark">B²</div>
    <div class="brand-text">
      <strong>Relay</strong>
      <small>Operator-Cockpit</small>
    </div>
  </div>

  <!-- TOP BAR -->
  <div class="topbar">
    <div class="topbar-stats">
      <div class="stat-pill"><div class="lbl">Pipeline</div><div class="val acc" id="kpi-total">–</div></div>
      <div class="stat-pill"><div class="lbl">Gesendet</div><div class="val ok" id="kpi-sent">–</div></div>
      <div class="stat-pill"><div class="lbl">Heute</div><div class="val" id="kpi-today">–</div></div>
      <div class="stat-pill"><div class="lbl">Replies</div><div class="val acc" id="kpi-replies">–</div></div>
      <div class="stat-pill"><div class="lbl">Hot 🔥</div><div class="val hot" id="kpi-hot">–</div></div>
      <div class="stat-pill"><div class="lbl">Follow-up</div><div class="val warn" id="kpi-fu">–</div></div>
    </div>
    <div class="live-dot" id="live-status">LIVE · <span id="live-ts">–</span></div>
  </div>

  <!-- SIDEBAR -->
  <aside class="sidebar">
    <div class="nav-section">
      <h4>Workflow</h4>
      <div class="nav-item active" data-page="dashboard"><span class="ico">📊</span> Übersicht</div>
      <div class="nav-item" data-page="signals"><span class="ico">📡</span> Signal-Erkennung <span class="badge" id="nav-signals">–</span></div>
      <div class="nav-item" data-page="enrichment"><span class="ico">✨</span> Lead-Anreicherung <span class="badge" id="nav-enrichment">–</span></div>
      <div class="nav-item" data-page="review"><span class="ico">🛡</span> Manuelle Prüfung <span class="badge hot" id="nav-review">–</span></div>
      <div class="nav-item" data-page="pipeline"><span class="ico">🧭</span> Outreach-Pipeline <span class="badge" id="nav-pipeline">–</span></div>
      <div class="nav-item" data-page="replycenter"><span class="ico">💬</span> Antwort-Zentrale <span class="badge hot" id="nav-replycenter">–</span></div>
    </div>
    <div class="nav-section">
      <h4>Werkzeuge</h4>
      <div class="nav-item" data-page="linkedin"><span class="ico">💼</span> LinkedIn-Suche <span class="badge" id="nav-li">–</span></div>
      <div class="nav-item" data-page="jobs"><span class="ico">⚙</span> Job-Log</div>
      <div class="nav-item" data-page="automation"><span class="ico">🚀</span> Automation</div>
    </div>
  </aside>

  <!-- MAIN -->
  <main class="main">

    <!-- ── DASHBOARD ── -->
    <div class="page active" id="page-dashboard">
      <div class="toolbar">
        <strong style="color:var(--accent);font-size:12px;letter-spacing:.6px">⚡ SCHNELL-AKTIONEN</strong>
        <button class="btn ghost sm" onclick="api('/api/sync-replies',{},'Replies syncen')">📥 Sync Replies</button>
        <button class="btn ghost sm" onclick="api('/api/process-replies',{},'Replies verarbeiten')">🧠 Verarbeiten</button>
        <button class="btn ghost sm" onclick="api('/api/preview',{},'Preview generieren')">✉ Preview</button>
        <button class="btn gold sm" style="margin-left:auto" onclick="confirmRun('FULL AUTO starten? Ganze Kette läuft autonom.','/api/full-auto',{},'FULL AUTO')">🚀 FULL AUTO</button>
        <button class="btn ghost icon-only sm" onclick="loadAll()" title="Refresh">🔄</button>
      </div>

      <div class="kpi-row">
        <div class="kpi-card ok"><div class="kpi-lbl"><span class="ico">📤</span> Gesendet</div><div class="kpi-val" id="d-sent">0</div><div class="kpi-sub"><span id="d-sent-today">0</span> heute</div></div>
        <div class="kpi-card"><div class="kpi-lbl"><span class="ico">🚀</span> Freigegeben</div><div class="kpi-val" id="d-approved">0</div><div class="kpi-sub">bereit zum Versand</div></div>
        <div class="kpi-card warn"><div class="kpi-lbl"><span class="ico">⏸</span> Warten auf Approval</div><div class="kpi-val" id="d-awaiting">0</div><div class="kpi-sub">Vorschau bereit</div></div>
        <div class="kpi-card hot"><div class="kpi-lbl"><span class="ico">🔥</span> Hot Replies</div><div class="kpi-val" id="d-hot">0</div><div class="kpi-sub">Interesse signalisiert</div></div>
        <div class="kpi-card"><div class="kpi-lbl"><span class="ico">💬</span> Antworten offen</div><div class="kpi-val" id="d-replies">0</div><div class="kpi-sub">Review nötig</div></div>
        <div class="kpi-card"><div class="kpi-lbl"><span class="ico">🔁</span> Follow-up</div><div class="kpi-val" id="d-fu">0</div><div class="kpi-sub">Wiedervorlage</div></div>
      </div>

      <div class="hot-strip">
        <span class="ico">⚡</span>
        <div style="flex:1">
          <strong id="overview-banner-title">Übersicht lädt…</strong>
          <div id="overview-banner-sub" style="color:var(--muted);font-size:12px;margin-top:2px">Signal-First Operator Cockpit mit echten Datenquellen.</div>
        </div>
        <button class="btn primary sm" onclick="goPage('signals')">Signal-Queue öffnen</button>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>🧭 Pipeline auf einen Blick</h2>
          <span class="badge">letzte 30 Tage</span>
          <div class="actions">
            <button class="btn ghost sm" onclick="goPage('pipeline')">Outreach-Pipeline →</button>
          </div>
        </div>
        <div id="overview-stage-strip" style="display:flex;gap:12px;flex-wrap:wrap"></div>
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:16px">
          <div style="flex:1;min-width:0;padding:10px 12px;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,.015)">
            <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.12em;font-weight:600">Erkennung → Anreicherung</div>
            <div style="display:flex;align-items:baseline;gap:8px;margin-top:4px"><span style="font-size:18px;font-weight:700" id="overview-conv-1">–</span></div>
            <div style="background:var(--border);height:8px;border-radius:4px;overflow:hidden;margin-top:8px"><div id="overview-convbar-1" style="height:100%;width:0%;background:var(--accent)"></div></div>
          </div>
          <div style="flex:1;min-width:0;padding:10px 12px;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,.015)">
            <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.12em;font-weight:600">Anreicherung → Prüfung</div>
            <div style="display:flex;align-items:baseline;gap:8px;margin-top:4px"><span style="font-size:18px;font-weight:700" id="overview-conv-2">–</span></div>
            <div style="background:var(--border);height:8px;border-radius:4px;overflow:hidden;margin-top:8px"><div id="overview-convbar-2" style="height:100%;width:0%;background:var(--accent)"></div></div>
          </div>
          <div style="flex:1;min-width:0;padding:10px 12px;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,.015)">
            <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.12em;font-weight:600">Prüfung → Freigegeben</div>
            <div style="display:flex;align-items:baseline;gap:8px;margin-top:4px"><span style="font-size:18px;font-weight:700" id="overview-conv-3">–</span></div>
            <div style="background:var(--border);height:8px;border-radius:4px;overflow:hidden;margin-top:8px"><div id="overview-convbar-3" style="height:100%;width:0%;background:var(--accent)"></div></div>
          </div>
        </div>
      </div>

      <div class="premium-grid">
        <div class="section">
          <div class="section-head"><h2>🔥 Top-Antworten</h2><span class="badge hot" id="overview-hot-count">0</span><div class="actions"><button class="btn ghost sm" onclick="goPage('replycenter')">Antwort-Zentrale</button></div></div>
          <div id="overview-top-replies"><div class="empty">Keine Antworten vorhanden.</div></div>
        </div>
        <div class="section">
          <div class="section-head"><h2>🛡 Wartet auf Prüfung</h2><span class="badge" id="overview-review-count">0</span><div class="actions"><button class="btn ghost sm" onclick="goPage('review')">Warteschlange</button></div></div>
          <div id="overview-review-table"><div class="empty">Keine Review Items vorhanden.</div></div>
        </div>
      </div>

      <div class="section">
        <div class="section-head"><h2>⚡ Schnellaktionen</h2><span class="badge">Operator-Shortcuts</span></div>
        <div id="overview-actions" class="premium-grid-4" style="margin-bottom:0"></div>
      </div>

      <div class="premium-grid dash-legacy" style="margin-top:18px">
        <div class="section">
          <div class="section-head">
            <h2>📡 Signal Discovery</h2>
            <span class="badge" id="dash-premium-signals-badge">0 Signale</span>
            <div class="actions"><button class="btn ghost sm" onclick="goPage('signals')">Öffnen</button></div>
          </div>
          <div id="dash-premium-signals" class="premium-grid-4"></div>
          <div id="dash-premium-signals-table"><div class="empty">Keine Signale geladen.</div></div>
        </div>
        <div class="section">
          <div class="section-head">
            <h2>🛡 Manual Review</h2>
            <span class="badge hot" id="dash-premium-review-badge">0 pending</span>
            <div class="actions"><button class="btn ghost sm" onclick="goPage('review')">Öffnen</button></div>
          </div>
          <div id="dash-premium-review-summary" style="margin-bottom:12px"></div>
          <div id="dash-premium-review-table"><div class="empty">Keine Review Items geladen.</div></div>
        </div>
      </div>

      <div class="premium-grid dash-legacy" style="margin-top:18px">
        <div class="section">
          <div class="section-head">
            <h2>✨ Lead Enrichment</h2>
            <span class="badge" id="dash-premium-enrichment-badge">0</span>
            <div class="actions"><button class="btn ghost sm" onclick="goPage('enrichment')">Öffnen</button></div>
          </div>
          <div id="dash-premium-enrichment-table"><div class="empty">Keine Enrichment-Daten geladen.</div></div>
        </div>
        <div class="section">
          <div class="section-head">
            <h2>💬 Reply Center</h2>
            <span class="badge hot" id="dash-premium-reply-badge">0</span>
            <div class="actions"><button class="btn ghost sm" onclick="goPage('replycenter')">Öffnen</button></div>
          </div>
          <div id="dash-premium-reply-list"><div class="empty">Keine Replies geladen.</div></div>
        </div>
      </div>

      <div class="hot-strip dash-legacy" id="awaiting-strip" style="display:none;background:linear-gradient(135deg,rgba(245,158,11,.12),rgba(245,158,11,.04));border-color:rgba(245,158,11,.3)">
        <span class="ico">⏸</span>
        <div style="flex:1">
          <strong style="color:var(--warn)" id="awaiting-strip-count">– warten auf Approval</strong>
          <div style="color:var(--muted);font-size:12px;margin-top:2px">Diese Leads sind via Preview vorbereitet, brauchen aber noch deine Freigabe (✓ Approve) bevor sie versendet werden können.</div>
        </div>
        <button class="btn primary sm" onclick="goPage('ready')">Anschauen →</button>
      </div>

      <div class="hot-strip dash-legacy" id="hot-strip" style="display:none">
        <span class="ico">🔥</span>
        <div style="flex:1">
          <strong id="hot-strip-count">– Hot Replies</strong>
          <div style="color:var(--muted);font-size:12px;margin-top:2px">warten auf Bearbeitung</div>
        </div>
        <button class="btn primary sm" onclick="goPage('replies')">Bearbeiten →</button>
      </div>

      <!-- LinkedIn Quick-Search Card auf dem Dashboard -->
      <div class="section dash-legacy" style="background:linear-gradient(135deg,rgba(10,102,194,.08),rgba(124,142,255,.04));border:1px solid rgba(10,102,194,.2)">
        <div class="section-head">
          <h2 style="color:#4d9aff">💼 LinkedIn-Kontaktsuche</h2>
          <span style="font-size:11px;color:var(--muted);font-weight:500">Branche eingeben → Suchen → Link klicken → LinkedIn-Cockpit</span>
          <div class="actions">
            <button class="btn ghost sm" onclick="goPage('linkedin')">LinkedIn-Cockpit →</button>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1.6fr 1fr 1fr 90px auto;gap:8px;align-items:end;margin-top:10px">
          <div>
            <label style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">Bereich / Branche *</label>
            <input id="dash-li-industry" class="tb-input" style="width:100%;padding:9px 11px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);font-size:13px" placeholder="z.B. Marketing, IT-Agentur…" onkeydown="if(event.key==='Enter')dashboardLiSearch()">
          </div>
          <div>
            <label style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">Ort</label>
            <input id="dash-li-city" class="tb-input" style="width:100%;padding:9px 11px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);font-size:13px" placeholder="z.B. München" onkeydown="if(event.key==='Enter')dashboardLiSearch()">
          </div>
          <div>
            <label style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">Rolle / Titel</label>
            <input id="dash-li-role" class="tb-input" style="width:100%;padding:9px 11px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);font-size:13px" placeholder="z.B. Geschäftsführer" onkeydown="if(event.key==='Enter')dashboardLiSearch()">
          </div>
          <div>
            <label style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:4px">Anzahl</label>
            <input id="dash-li-count" class="tb-input" style="width:100%;padding:9px 11px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r);font-size:13px" type="number" value="20" min="5" max="50">
          </div>
          <button class="btn primary" onclick="dashboardLiSearch()" style="padding:10px 18px;background:linear-gradient(135deg,#0a66c2,#4d9aff);box-shadow:0 6px 20px rgba(10,102,194,.4);white-space:nowrap">🔍 Suchen</button>
        </div>
        <div style="margin-top:8px;display:flex;gap:5px;flex-wrap:wrap;font-size:11px">
          <button class="chip" onclick="dashboardLiQuick('Marketingagentur','München','Geschäftsführer')">Marketing-GFs München</button>
          <button class="chip" onclick="dashboardLiQuick('IT-Dienstleister','Berlin','CTO')">IT-CTOs Berlin</button>
          <button class="chip" onclick="dashboardLiQuick('Steuerberater','Hamburg','Inhaber')">Steuer Hamburg</button>
          <button class="chip" onclick="dashboardLiQuick('Unternehmensberatung','Frankfurt','Partner')">Beratung Frankfurt</button>
          <button class="chip" onclick="dashboardLiQuick('E-Commerce','','Inhaber')">E-Commerce Inhaber</button>
          <button class="chip" onclick="dashboardLiQuick('PR-Agentur','','Geschäftsführer')">PR-Agenturen</button>
        </div>
        <!-- Ergebnis-Bereich: Live-Progress + Cockpit-Link nach Abschluss -->
        <div id="dash-li-result" style="display:none;margin-top:14px;padding:12px 16px;background:rgba(10,102,194,.1);border:1px solid rgba(10,102,194,.3);border-radius:var(--r)">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px">
            <span id="dash-li-result-text" style="color:var(--text);font-size:12px;font-weight:500">Suche läuft…</span>
            <span id="dash-li-result-pct" style="color:#4d9aff;font-size:13px;font-weight:700;margin-left:auto">0%</span>
          </div>
          <!-- Progress-Bar -->
          <div style="height:8px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden;margin-bottom:10px">
            <div id="dash-li-progress-bar" style="height:100%;width:0%;background:linear-gradient(90deg,#0a66c2,#4d9aff);transition:width .4s ease"></div>
          </div>
          <div id="dash-li-result-msg" style="color:var(--muted);font-size:11px;margin-bottom:10px;font-family:ui-monospace,Menlo,monospace">…</div>
          <div id="dash-li-result-actions" style="display:flex;gap:8px;flex-wrap:wrap">
            <a id="dash-li-cockpit-link" href="#" onclick="event.preventDefault();goPage('linkedin')" class="btn primary sm" style="background:linear-gradient(135deg,#0a66c2,#4d9aff);text-decoration:none">
              💼 LinkedIn-Cockpit öffnen →
            </a>
          </div>
        </div>
        <!-- Letzte Suchen (per Klick erneut starten oder ergebnisliste laden) -->
        <div id="dash-li-history-row" style="display:none;margin-top:10px">
          <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px">Letzte Suchen</div>
          <div id="dash-li-history" style="display:flex;gap:6px;flex-wrap:wrap;font-size:11px"></div>
        </div>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>📥 Neueste Antworten</h2>
          <span class="badge" id="recent-replies-count">0</span>
          <div class="actions">
            <button class="btn ghost sm" onclick="goPage('replies')">Alle ansehen →</button>
          </div>
        </div>
        <div id="recent-replies"><div class="loader">Lade…</div></div>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>📤 Heute versendet</h2>
          <span class="badge" id="recent-sent-count">0</span>
          <div class="actions">
            <button class="btn ghost sm" onclick="goPage('sent')">Alle ansehen →</button>
          </div>
        </div>
        <div id="recent-sent"><div class="loader">Lade…</div></div>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>🧠 Intent Discovery Preview</h2>
          <span class="badge" id="intent-preview-badge">Preview</span>
          <button class="btn warn sm" style="margin-left:auto" onclick="api('/api/intent-target-preview/run',{},'Intent Target Preview')">🧠 Intent Preview starten</button>
        </div>
        <div id="intent-preview-note" style="color:var(--muted);font-size:12px;margin-bottom:10px">Preview only – noch nicht in normale Lead-Pipeline integriert.</div>
        <div id="intent-preview-content"><div class="loader">Lade…</div></div>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>🎯 Intent Lead Production</h2>
          <span class="badge" id="intent-lp-badge">Production</span>
        </div>

        <!-- Steuerformular: Signalbasierte Lead-Produktion starten -->
        <div style="background:var(--surface2);border:1px solid var(--border);border-radius:var(--r);padding:14px;margin-bottom:14px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
            <strong style="font-size:14px">🎯 Signalbasierte Lead-Produktion starten</strong>
            <span style="color:var(--muted);font-size:11px">Branche + Stadt + Signaltyp + Limit → personalisierte Erstmail-Drafts (kein Versand)</span>
          </div>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:10px">
            <div>
              <label style="display:block;font-size:11px;color:var(--muted);margin-bottom:3px">Branche / Zielgruppe</label>
              <input id="intent-prod-industry" class="tb-input" value="Marketingagentur" placeholder="z.B. Marketingagentur" style="width:100%">
            </div>
            <div>
              <label style="display:block;font-size:11px;color:var(--muted);margin-bottom:3px">Stadt / Region</label>
              <input id="intent-prod-city" class="tb-input" value="Muenchen" placeholder="z.B. Muenchen" style="width:100%">
            </div>
            <div>
              <label style="display:block;font-size:11px;color:var(--muted);margin-bottom:3px">Signaltyp</label>
              <select id="intent-prod-signal" class="tb-input" style="width:100%">
                <option value="sales_hiring" selected>sales_hiring</option>
                <option value="growth_expansion">growth_expansion</option>
                <option value="demand_generation_gap">demand_generation_gap</option>
              </select>
            </div>
            <div>
              <label style="display:block;font-size:11px;color:var(--muted);margin-bottom:3px">Limit (max 10)</label>
              <input id="intent-prod-limit" class="tb-input" type="number" min="1" max="10" value="10" style="width:100%">
            </div>
            <div>
              <label style="display:block;font-size:11px;color:var(--muted);margin-bottom:3px">Modus</label>
              <select id="intent-prod-mode" class="tb-input" style="width:100%">
                <option value="preview" selected>preview</option>
                <option value="approval">approval</option>
                <option value="auto">auto (kein Versand)</option>
              </select>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:10px">
            <button class="btn warn sm" onclick="startIntentLeadProduction()">🚀 Intent Lead Production starten</button>
            <span style="color:var(--muted);font-size:11px">⚠ Modus <code>auto</code> sendet aktuell <strong>nicht</strong> – fallback auf <code>approval</code>.</span>
          </div>
        </div>

        <div id="intent-lp-summary" style="margin-bottom:12px"></div>
        <div id="intent-lp-content"><div class="loader">Lade…</div></div>
      </div>

      <div class="section">
        <div class="section-head">
          <h2>Intent Email Review Queue</h2>
          <span class="badge" id="intent-email-review-badge">Review</span>
          <div class="actions">
            <button class="btn ghost sm" onclick="loadIntentEmailReviewQueue().then(renderIntentEmailReviewQueue)">Refresh</button>
          </div>
        </div>
        <div id="intent-email-review-summary" style="margin-bottom:12px"></div>
        <div id="intent-email-review-content"><div class="loader">Lade…</div></div>
      </div>

	      <div class="section">
	        <div class="section-head">
	          <h2>Manual Decision Maker Review</h2>
	          <span class="badge" id="intent-manual-dm-badge">Manual</span>
	          <div class="actions">
	            <button class="btn ghost sm" onclick="loadIntentManualDecisionMakerReview().then(renderIntentManualDecisionMakerReview)">Refresh</button>
	          </div>
	        </div>
	        <div id="intent-manual-dm-summary" style="margin-bottom:12px"></div>
	        <div id="intent-manual-dm-content"><div class="loader">Lade...</div></div>
	      </div>

	      <div class="section">
	        <div class="section-head">
	          <h2>Intent Operator Queue</h2>
          <span class="badge" id="intent-operator-badge">Operator</span>
          <div class="actions">
            <button class="btn ghost sm" onclick="loadIntentOperatorQueue().then(renderIntentOperatorQueue)">Refresh</button>
          </div>
        </div>
        <div id="intent-operator-health" style="margin-bottom:12px"></div>
        <div id="intent-operator-content"><div class="loader">Lade…</div></div>
      </div>
    </div>

    <!-- ── LEADS ── -->
    <!-- PREMIUM SIGNAL DISCOVERY -->
    <div class="page" id="page-signals">
      <div class="premium-grid">
        <div class="section">
          <div class="section-head"><h2>📡 Signal-Quellen</h2><span class="badge" id="premium-source-count">0 Quellen</span></div>
          <div id="premium-sources"><div class="empty">Keine Signalquellen geladen.</div></div>
        </div>
        <div class="section">
          <div class="section-head"><h2>🏷 Signal-Stufen</h2><span class="badge">A/B/C/D</span></div>
          <div id="premium-tier-summary" class="premium-grid-4" style="margin-bottom:0"></div>
        </div>
      </div>
      <div class="section">
        <div class="section-head"><h2>Erfasste Signale</h2><span class="badge" id="premium-signals-badge">0</span><div class="actions"><button class="btn ghost sm" onclick="loadAll()">Refresh</button></div></div>
        <div id="premium-signals-table"><div class="empty">Keine Signale vorhanden.</div></div>
      </div>
    </div>

    <!-- PREMIUM ENRICHMENT -->
    <div class="page" id="page-enrichment">
      <div class="premium-grid-4" id="premium-enrichment-kpis"></div>
      <div class="section">
        <div class="section-head"><h2>✨ Lead-Anreicherung</h2><span class="badge" id="premium-enrichment-badge">0</span><div class="actions"><button class="btn ghost sm" onclick="loadAll()">Refresh</button></div></div>
        <div id="premium-enrichment-table"><div class="empty">Keine Enrichment-Daten vorhanden.</div></div>
      </div>
    </div>

    <!-- PREMIUM MANUAL REVIEW -->
    <div class="page" id="page-review">
      <div class="section">
        <div class="section-head"><h2>🛡 Manual Review</h2><span class="badge hot" id="premium-review-badge">0 pending</span><div class="actions"><button class="btn ghost sm" onclick="loadAll()">Refresh</button></div></div>
        <div style="color:var(--text-mute);font-size:12px;margin-bottom:12px">Pattern-Mails bleiben manuell pruefpflichtig. Verified/Reject aktualisiert nur Review-Dateien, kein Versand.</div>
        <div id="premium-review-table"><div class="empty">Keine Review Items vorhanden.</div></div>
      </div>
      <div class="section">
        <div class="section-head"><h2>Manual Decision Maker Review</h2><span class="badge" id="premium-manual-dm-badge">Dashboard Save aktiv</span></div>
        <div id="premium-manual-dm-mount"></div>
      </div>
    </div>

    <!-- PREMIUM PIPELINE -->
    <div class="page" id="page-pipeline">
      <div class="premium-grid-4" id="premium-pipeline-kpis"></div>
      <div class="section">
        <div class="section-head"><h2>🧭 Outreach Pipeline</h2><span class="badge" id="premium-pipeline-badge">canonical</span></div>
        <div style="color:var(--text-mute);font-size:12px;margin-bottom:12px">Canonical Pipeline: output/outreach_pipeline.json. Gesendete Leads werden nicht als ready gezaehlt.</div>
        <div id="premium-pipeline-table"><div class="empty">Keine Pipeline-Daten vorhanden.</div></div>
      </div>
    </div>

    <!-- PREMIUM REPLY CENTER -->
    <div class="page" id="page-replycenter">
      <div class="section">
        <div class="section-head"><h2>💬 Reply Center</h2><span class="badge hot" id="premium-reply-badge">0</span><div class="actions"><button class="btn ghost sm" onclick="loadAll()">Refresh</button></div></div>
        <div style="color:var(--text-mute);font-size:12px;margin-bottom:12px">Reply Queue bleibt read-only, ausser bestehende sichere Aktionen sind bereits vorhanden.</div>
        <div id="premium-reply-list"><div class="empty">Keine Replies vorhanden.</div></div>
      </div>
    </div>

    <div class="page" id="page-leads">
      <div class="toolbar">
        <div class="filter-bar" style="flex:1;margin:0">
          <div class="search-box"><input id="leads-search" placeholder="Suche Firma, Email, Stadt, Branche…" oninput="renderLeads()"></div>
          <div class="chip-group">
            <div class="chip active" data-filter="stage" data-val="all" onclick="setFilter(this)">Alle</div>
            <div class="chip" data-filter="stage" data-val="new" onclick="setFilter(this)">Neu <span id="new-leads-count" style="font-size:9px;opacity:.7"></span></div>
            <div class="chip" data-filter="stage" data-val="ready" onclick="setFilter(this)">Bereit</div>
            <div class="chip" data-filter="stage" data-val="sent" onclick="setFilter(this)">Gesendet</div>
            <div class="chip" data-filter="stage" data-val="replied" onclick="setFilter(this)">Beantwortet</div>
          </div>
          <div class="chip-group">
            <div class="chip active" data-filter="contact" data-val="all" onclick="setFilter(this)">Alle</div>
            <div class="chip" data-filter="contact" data-val="email" onclick="setFilter(this)">📧 Email</div>
            <div class="chip" data-filter="contact" data-val="phone" onclick="setFilter(this)">📞 Phone</div>
          </div>
          <div class="chip-group" data-sort-group="leads">
            <span style="font-size:10px;color:var(--muted);align-self:center;margin-right:4px">Sortieren:</span>
            <div class="chip active" data-sort="leads" data-val="newest" onclick="setSort(this)">⬇ Neueste</div>
            <div class="chip" data-sort="leads" data-val="oldest" onclick="setSort(this)">⬆ Älteste</div>
            <div class="chip" data-sort="leads" data-val="alpha" onclick="setSort(this)">A–Z</div>
            <div class="chip" data-sort="leads" data-val="score" onclick="setSort(this)">Score</div>
          </div>
        </div>
      </div>
      <div class="section" style="padding:0">
        <div id="leads-table" class="tbl-wrap"></div>
      </div>
    </div>

    <!-- ── READY TO SEND ── -->
    <div class="page" id="page-ready">
      <div class="toolbar">
        <strong>📨 Versandbereit</strong>
        <div class="tb-group" style="margin-left:auto">
          <button class="btn sm" onclick="refreshReady()">🔄 Aktualisieren</button>
          <button class="btn warn sm" onclick="confirmRun('Alle ready-Leads freigeben?','/api/approve-all',{limit:9999},'Alle freigeben')">✅ Alle freigeben</button>
          <input class="tb-input sm" id="batch-limit" type="number" value="10" min="1" max="50" style="width:60px">
          <button class="btn success sm" onclick="confirmRun('Batch senden?','/api/send-batch',{limit:parseInt(document.getElementById('batch-limit').value)||10},'Batch senden')">📤 Batch senden</button>
        </div>
      </div>

      <!-- Sender Panel -->
      <div class="section" style="margin-bottom:0;border-bottom:none;padding-bottom:8px">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
          <strong style="font-size:14px">📬 E-Mail Konten &amp; Versand-Einstellungen</strong>
          <span style="color:var(--muted);font-size:12px">Limit = max. E-Mails pro Tag · Gewicht = Anteil bei automatischem Versand</span>
          <button class="btn sm" style="margin-left:auto" onclick="saveSenderSettings()" id="save-sender-btn">💾 Einstellungen speichern</button>
        </div>
        <div id="sender-cards" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px">
          <div style="color:var(--muted);padding:20px;text-align:center">⏳ Lädt…</div>
        </div>
      </div>

      <div class="section" style="padding:0;margin-top:0;border-top:1px solid var(--border)">
        <div style="padding:10px 16px;background:var(--panel-bg);font-size:12px;color:var(--muted)">
          📋 <strong style="color:var(--fg)">Leads bereit zum Senden</strong> — nur freigegebene (✅ Approve) werden gesendet
        </div>
        <div id="ready-table" class="tbl-wrap"><div class="empty"><span class="big">⏳</span>Wird geladen…</div></div>
      </div>
    </div>

    <!-- ── SENT ── -->
    <div class="page" id="page-sent">
      <div class="toolbar">
        <strong>📤 Versendet</strong>
        <div class="chip-group" data-sort-group="sent" style="margin-left:14px">
          <span style="font-size:10px;color:var(--muted);align-self:center;margin-right:4px">Sortieren:</span>
          <div class="chip active" data-sort="sent" data-val="sent_desc" onclick="setSort(this)">⬇ Neueste</div>
          <div class="chip" data-sort="sent" data-val="sent_asc" onclick="setSort(this)">⬆ Älteste</div>
          <div class="chip" data-sort="sent" data-val="alpha" onclick="setSort(this)">A–Z</div>
        </div>
        <button class="btn ghost sm" style="margin-left:auto" onclick="loadAll()">🔄 Refresh</button>
      </div>
      <div class="section" style="padding:0"><div id="sent-table" class="tbl-wrap"></div></div>
    </div>

    <!-- ── REPLIES ── -->
    <div class="page" id="page-replies">
      <div class="toolbar">
        <strong>💬 Antworten</strong>
        <button class="btn ghost sm" onclick="api('/api/sync-replies',{},'Replies syncen')">📥 Neue holen</button>
        <button class="btn ghost sm" onclick="api('/api/process-replies',{},'Replies klassifizieren')">🧠 Klassifizieren</button>
        <button class="btn ghost sm" onclick="loadReplyOperatorQueue().then(renderReplyOperatorQueue)">Reply Queue aktualisieren</button>
      </div>
      <div id="reply-operator-safety" style="margin-bottom:12px"></div>
      <div id="reply-operator-list"></div>
      <div id="replies-list" style="display:none"></div>
    </div>

    <!-- ── FOLLOWUPS ── -->
    <div class="page" id="page-followup">
      <div class="toolbar">
        <strong>🔁 Follow-ups</strong>
        <div class="tb-group" style="margin-left:auto">
          <input class="tb-input sm" id="fu-limit" type="number" value="10" min="1" max="20">
          <button class="btn warn sm" onclick="confirmRun('Follow-ups senden?','/api/send-followups',{limit:parseInt(document.getElementById('fu-limit').value)||10},'Follow-ups senden')">🔁 Senden</button>
        </div>
      </div>
      <div class="section" style="padding:0"><div id="fu-table" class="tbl-wrap"></div></div>
    </div>

    <!-- ── LINKEDIN ── -->
    <div class="page" id="page-linkedin">
      <div class="toolbar">
        <strong>💼 LinkedIn-Cockpit</strong>
        <span style="color:var(--muted);font-size:12px">Suche → Klick → Tagesliste mit Such-Links & fertigen Texten</span>
        <button class="btn ghost sm" style="margin-left:auto" onclick="loadAll()">🔄 Refresh</button>
      </div>

      <!-- ⚡ LinkedIn-Suche (frischer Lauf) -->
      <div class="section">
        <h2 style="font-size:15px;margin-bottom:6px">🔍 Frische LinkedIn-Recherche starten</h2>
        <p style="color:var(--muted);font-size:12px;margin-bottom:14px">Branche + Stadt eingeben → Lead-Suche läuft, anschliessend wird die LinkedIn-Tagesliste automatisch generiert. Ergebnisse erscheinen unten zum Anklicken.</p>
        <div style="display:grid;grid-template-columns:1.2fr 1fr 1fr 90px auto;gap:10px;align-items:end">
          <div>
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Branche *</label>
            <input id="li-search-industry" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="z.B. Marketingagentur, Steuerberater">
          </div>
          <div>
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Stadt / Region</label>
            <input id="li-search-city" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="optional, z.B. München">
          </div>
          <div>
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Rolle / Titel</label>
            <input id="li-search-role" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="optional, z.B. Geschäftsführer, CTO">
          </div>
          <div>
            <label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Anzahl</label>
            <input id="li-search-count" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" type="number" value="20" min="5" max="100">
          </div>
          <button class="btn primary" onclick="doLinkedinSearch()" style="padding:10px 18px">🔍 Suche + Liste</button>
        </div>
        <div style="margin-top:14px;display:flex;gap:6px;flex-wrap:wrap">
          <strong style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;align-self:center;margin-right:6px">Quick-Start:</strong>
          <button class="chip" onclick="quickLiSearch('Marketingagentur','München','Geschäftsführer',20)">Marketing-GFs München</button>
          <button class="chip" onclick="quickLiSearch('IT-Dienstleister','Berlin','CTO',20)">IT-CTOs Berlin</button>
          <button class="chip" onclick="quickLiSearch('Steuerberater','Hamburg','Inhaber',20)">Steuer-Inhaber Hamburg</button>
          <button class="chip" onclick="quickLiSearch('Personalberatung','Frankfurt','Geschäftsführer',20)">HR-GFs Frankfurt</button>
          <button class="chip" onclick="quickLiSearch('SaaS','Köln','Sales',20)">SaaS-Sales Köln</button>
        </div>

        <!-- Shortcut: nur Liste regenerieren ohne neue Suche -->
        <div style="margin-top:14px;padding-top:14px;border-top:1px dashed var(--border);display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <span style="font-size:12px;color:var(--muted)">⚡ Schnell-Aktion:</span>
          <input class="tb-input sm" id="li-limit" type="number" value="20" min="5" max="50" style="width:70px;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);border-radius:6px">
          <button class="btn ghost sm" onclick="runLinkedinBot()" title="Tagesliste aus bestehenden Leads neu sortieren">🔁 Liste aus Pipeline neu sortieren</button>
        </div>
      </div>

      <div class="section">
        <div style="background:linear-gradient(135deg,rgba(10,102,194,.12),rgba(124,142,255,.04));border:1px solid rgba(10,102,194,.25);border-radius:var(--r);padding:14px 18px;margin-bottom:18px;font-size:13px;color:var(--muted);line-height:1.6">
          <strong style="color:var(--accent)">⚡ So nutzt du das LinkedIn-Cockpit:</strong>
          1️⃣ Klick <strong>🔍 Person</strong> oder <strong>💼 Firma</strong> → öffnet LinkedIn-Suche in neuem Tab ·
          2️⃣ Connection-Request mit dem <strong>📝 CR</strong>-Button kopieren ·
          3️⃣ Status setzen (<strong>Connected → DM → Replied</strong>) — alles bleibt gespeichert.
          <br><strong style="color:var(--text)">Ehrlich:</strong> Versand machst du selbst. Account-Risiko bleibt bei Null.
        </div>

        <div class="kpi-row" style="margin-bottom:18px">
          <div class="kpi-card"><div class="kpi-lbl"><span class="ico">📋</span> To-Do</div><div class="kpi-val" id="li-kpi-todo">0</div><div class="kpi-sub">noch nicht angefasst</div></div>
          <div class="kpi-card warn"><div class="kpi-lbl"><span class="ico">⏳</span> In Arbeit</div><div class="kpi-val" id="li-kpi-progress">0</div><div class="kpi-sub">Connect/DM gesendet</div></div>
          <div class="kpi-card ok"><div class="kpi-lbl"><span class="ico">💬</span> Antworten</div><div class="kpi-val" id="li-kpi-replied">0</div><div class="kpi-sub">replied / Termin</div></div>
        </div>
      </div>
      <div class="toolbar" style="border-bottom:1px solid var(--border);background:var(--bg2)">
        <div class="chip-group">
          <div class="chip active" data-li-filter="all" onclick="setLiFilter(this)">Alle</div>
          <div class="chip" data-li-filter="todo" onclick="setLiFilter(this)">📋 To-Do</div>
          <div class="chip" data-li-filter="progress" onclick="setLiFilter(this)">⏳ In Arbeit</div>
          <div class="chip" data-li-filter="replied" onclick="setLiFilter(this)">💬 Antworten</div>
          <div class="chip" data-li-filter="skip" onclick="setLiFilter(this)">⏭ Skip</div>
        </div>
        <div class="chip-group" data-sort-group="linkedin">
          <span style="font-size:10px;color:var(--muted);align-self:center;margin-right:4px">Sortieren:</span>
          <div class="chip active" data-sort="linkedin" data-val="newest" onclick="setSort(this)">⬇ Neueste</div>
          <div class="chip" data-sort="linkedin" data-val="oldest" onclick="setSort(this)">⬆ Älteste</div>
          <div class="chip" data-sort="linkedin" data-val="alpha" onclick="setSort(this)">A–Z</div>
        </div>
        <div class="search-box" style="margin-left:auto"><input id="li-search" placeholder="Suche…" oninput="renderLinkedin()"></div>
      </div>
      <div class="section" style="padding:0"><div id="li-table" class="tbl-wrap"></div></div>
    </div>

    <!-- ── SEARCH ── -->
    <div class="page" id="page-search">
      <div class="toolbar"><strong>🔍 Lead-Suche starten</strong></div>
      <div class="section">
        <p style="color:var(--muted);margin-bottom:18px;font-size:13px">Neue Leads werden gesucht, gescraped und direkt in die Pipeline gelegt. Saubere Leads (mit Email/Phone) werden automatisch bereitgestellt.</p>
        <div style="display:grid;grid-template-columns:1fr 1fr 120px auto;gap:12px;align-items:end">
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Branche *</label><input id="search-industry" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="z.B. Marketingagentur, Steuerberater"></div>
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Stadt / Region</label><input id="search-city" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" placeholder="optional, z.B. München"></div>
          <div><label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:6px">Anzahl</label><input id="search-count" class="tb-input" style="width:100%;padding:10px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:var(--r)" type="number" value="20" min="1" max="100"></div>
          <button class="btn primary" onclick="doSearch()" style="padding:10px 18px">🔍 Suche starten</button>
        </div>
        <div style="margin-top:24px">
          <h5 style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px">Beliebte Branchen</h5>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="chip" onclick="quickSearch('Marketingagentur','München',20)">Marketingagentur München</button>
            <button class="chip" onclick="quickSearch('Steuerberater','Berlin',20)">Steuerberater Berlin</button>
            <button class="chip" onclick="quickSearch('Webagentur','Hamburg',20)">Webagentur Hamburg</button>
            <button class="chip" onclick="quickSearch('Personalberatung','Frankfurt',20)">Personalberatung Frankfurt</button>
            <button class="chip" onclick="quickSearch('IT-Dienstleister','Köln',20)">IT München</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ── AUTOMATION ── -->
    <div class="page" id="page-automation">
      <div class="toolbar"><strong>🚀 Automation</strong></div>
      <div class="section">
        <h2 style="margin-bottom:12px">Komplette Outreach-Automation</h2>
        <p style="color:var(--muted);margin-bottom:20px;font-size:13px">Führt alle Schritte automatisch aus: Preview → Batch-Send → Sync → Process Replies → Follow-ups</p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px">
          <div class="kpi-card" onclick="confirmRun('Volle Kette starten?','/api/full-auto',{},'FULL AUTO')" style="cursor:pointer">
            <div class="kpi-lbl"><span class="ico">🚀</span> FULL AUTO</div>
            <div class="kpi-val" style="font-size:18px">Komplette Kette</div>
            <div class="kpi-sub">Auto-Sequenz starten</div>
          </div>
          <div class="kpi-card" onclick="api('/api/sync-replies',{},'Replies syncen')" style="cursor:pointer">
            <div class="kpi-lbl"><span class="ico">📥</span> Sync</div>
            <div class="kpi-val" style="font-size:18px">Replies holen</div>
            <div class="kpi-sub">IMAP-Polling alle 3 Accounts</div>
          </div>
          <div class="kpi-card" onclick="api('/api/process-replies',{},'Replies verarbeiten')" style="cursor:pointer">
            <div class="kpi-lbl"><span class="ico">🧠</span> Process</div>
            <div class="kpi-val" style="font-size:18px">Klassifizieren</div>
            <div class="kpi-sub">+ Drafts erstellen</div>
          </div>
          <div class="kpi-card" onclick="api('/api/preview',{},'Preview generieren')" style="cursor:pointer">
            <div class="kpi-lbl"><span class="ico">✉</span> Preview</div>
            <div class="kpi-val" style="font-size:18px">Erstansprache</div>
            <div class="kpi-sub">vorbereiten</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── JOBS ── -->
    <div class="page" id="page-jobs">
      <div class="toolbar"><strong>⚙ Job-Log</strong><button class="btn ghost sm" style="margin-left:auto" onclick="loadJobs()">🔄</button></div>
      <div class="section" style="padding:0"><div id="jobs-table" class="tbl-wrap"></div></div>
      <div class="section">
        <h2>Live-Log</h2>
        <pre id="job-log" style="background:var(--bg2);padding:14px;border-radius:var(--r);font-size:11px;font-family:'Consolas','Monaco',monospace;max-height:300px;overflow-y:auto;color:var(--muted)"></pre>
      </div>
    </div>

  </main>
</div>

<!-- DRAWER -->
<div class="drawer-bg" id="drawer-bg" onclick="closeDrawer()"></div>
<div class="drawer" id="drawer">
  <div class="drawer-head">
    <h3 id="drawer-title">–</h3>
    <button class="btn ghost icon-only" onclick="closeDrawer()">✕</button>
  </div>
  <div class="drawer-body" id="drawer-body"></div>
  <div class="drawer-actions" id="drawer-actions"></div>
</div>

<!-- TOAST -->
<div class="toast-stack" id="toasts"></div>

<!-- JOB BAR -->
<div class="job-bar hidden" id="job-bar">
  <div class="spinner"></div>
  <strong id="job-bar-name">Job läuft…</strong>
  <span style="color:var(--muted)" id="job-bar-time"></span>
  <button class="btn ghost sm" style="margin-left:auto" onclick="goPage('jobs')">Details</button>
</div>

<script>
// ═════════════════════════════════════════════════════════
// STATE
// ═════════════════════════════════════════════════════════
const state = {
  stats: {},
  leads: [],
  replies: [],
  replyOperatorQueue: null,
  sent: [],
  jobs: [],
  senders: [],
  intentPreview: null,
  intentEmailReviewQueue: null,
  intentManualDecisionMakerReview: null,
  intentOperatorQueue: null,
  premiumDashboard: null,
  filters: { stage: 'all', contact: 'all', li: 'all' },
  sorts: { leads: 'newest', sent: 'sent_desc', linkedin: 'newest' },
  page: 'dashboard',
  activeJob: null,
  last_search_started_at: '',
  search_job_active: false,
};
const E = (s) => String(s||'').replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));

// ═════════════════════════════════════════════════════════
// API CALLS
// ═════════════════════════════════════════════════════════
async function api(endpoint, payload, label) {
  toast('info', label || 'Läuft…', 'Aktion gestartet');
  try {
    const r = await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload||{})});
    const j = await r.json();
    if (j.error) { toast('err', 'Fehler', j.error); return; }
    if (j.job_id) { trackJob(j.job_id, label); }
  } catch(e) { toast('err','Netzwerkfehler', String(e)); }
}
async function confirmRun(msg, endpoint, payload, label) {
  if (!confirm(msg)) return;
  api(endpoint, payload, label);
}
async function fetchJSON(url, opts) {
  try { const r = await fetch(url, opts); return await r.json(); }
  catch(e) { return null; }
}

// ═════════════════════════════════════════════════════════
// JOB TRACKING
// ═════════════════════════════════════════════════════════
async function trackJob(jobId, label) {
  state.activeJob = {id: jobId, label, start: Date.now()};
  showJobBar(label);
  let tries = 0;
  while (tries < 600) {
    await new Promise(r=>setTimeout(r,1500));
    const j = await fetchJSON('/api/job/'+jobId);
    if (!j) { tries++; continue; }
    if (j.status === 'running') {
      const sec = Math.floor((Date.now()-state.activeJob.start)/1000);
      const msg = (j.progress_msg||'').toString().substring(0,80);
      document.getElementById('job-bar-time').textContent = msg
        ? `läuft ${sec}s · ${msg}` : `läuft ${sec}s`;
      // Periodischer Reload während Suche: alle 3s Leads + Stats neu laden
      // damit neue Ergebnisse inkrementell im Dashboard erscheinen
      const isSearchJob = /^Suche:/i.test(label || '');
      if (isSearchJob && tries % 2 === 0) {
        state.search_job_active = true;
        loadLeads().then(() => {
          if (state.page === 'leads') renderLeads();
          if (state.page === 'ready') renderReady();
        }).catch(()=>{});
        loadStats().catch(()=>{});
      }
    } else {
      hideJobBar();
      // Parse JSON from stdout if present (mine.py outreach commands return JSON summary)
      const stdout = j.stdout_tail || '';
      const stderr = j.stderr_tail || '';
      let summary = null;
      try {
        const m = stdout.match(/\{[\s\S]*"ok"\s*:\s*(?:true|false)[\s\S]*\}/);
        if (m) summary = JSON.parse(m[0]);
      } catch(e) {}

      if (j.status === 'ok') {
        if (summary) {
          // Build readable summary
          const parts = [];
          if ('sent' in summary) parts.push(`✉ ${summary.sent} gesendet`);
          if (summary.errors) parts.push(`<span style="color:var(--err)">⚠ ${summary.errors} Sendefehler</span>`);
          if (summary.skipped_unapproved) parts.push(`<span style="color:var(--warn)">⏸ ${summary.skipped_unapproved} brauchen Approval</span>`);
          if (summary.skipped_enterprise) parts.push(`🏢 ${summary.skipped_enterprise} Enterprise blockiert`);
          if (summary.skipped_invalid_domain) parts.push(`✗ ${summary.skipped_invalid_domain} ungültige Domain`);
          if (summary.skipped_duplicate_recipient) parts.push(`↻ ${summary.skipped_duplicate_recipient} Duplikate`);
          if (summary.classified !== undefined) parts.push(`🧠 ${summary.classified} klassifiziert`);
          if (summary.fetched !== undefined) parts.push(`📥 ${summary.fetched} neue Mails`);
          const detail = parts.length ? parts.join(' · ') : JSON.stringify(summary).substring(0,200);
          if (summary.sent === 0 && summary.errors > 0) {
            toast('warn', '⚠ ' + label + ' — keine Mails gesendet', detail);
            // Show hint about possible IONOS issue
            if (stderr.includes('Sender address is not allowed') || stderr.includes('mailbox unavailable')) {
              setTimeout(() => toast('err', '🚨 IONOS Sender-Problem',
                'IONOS lehnt deine Sender-Adresse ab. SPF/DKIM checken oder Daily-Limit erreicht?'), 800);
            }
          } else if (summary.skipped_unapproved > 0 && summary.sent === 0) {
            toast('warn', '⏸ ' + label + ' — Approval nötig',
              `${summary.skipped_unapproved} Leads warten auf manuelle Freigabe. Klick im Lead auf ✓ Approve.`);
          } else {
            toast('ok', '✓ ' + label, detail);
          }
        } else {
          toast('ok', '✓ ' + label, stdout.split('\n').filter(x=>x.trim()).slice(-2).join(' · ').substring(0,200));
        }
        // Nach Lead-Suche: zur Leads-Seite mit "Neu"-Filter springen,
        // damit nur die gefundenen Leads der aktuellsten Suche sichtbar sind.
        const isSearchJob = /^Suche:/i.test(label || '');
        if (isSearchJob) {
          state.search_job_active = false;
          await loadAll();
          state.sorts.leads = 'newest';
          // "Neu"-Filter aktiv: zeigt nur Leads der aktuellsten Suche
          state.filters.stage = 'new';
          state.filters.contact = 'all';
          document.querySelectorAll('[data-filter="stage"]').forEach(c=>c.classList.toggle('active', c.dataset.val==='new'));
          document.querySelectorAll('[data-filter="contact"]').forEach(c=>c.classList.toggle('active', c.dataset.val==='all'));
          document.querySelectorAll('[data-sort="leads"]').forEach(c=>c.classList.toggle('active', c.dataset.val==='newest'));
          goPage('leads');
        } else {
          loadAll();
        }
      } else {
        toast('err', '✗ ' + label + ' — ' + j.status, (stderr || stdout).substring(0,400));
      }
      state.activeJob = null;
      return;
    }
    tries++;
  }
  hideJobBar();
}
function showJobBar(label) {
  document.getElementById('job-bar-name').textContent = label || 'Job läuft…';
  document.getElementById('job-bar-time').textContent = '0s';
  document.getElementById('job-bar').classList.remove('hidden');
}
function hideJobBar() { document.getElementById('job-bar').classList.add('hidden'); }

// ═════════════════════════════════════════════════════════
// TOAST
// ═════════════════════════════════════════════════════════
function toast(type, title, msg) {
  const stack = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + (type||'info');
  const icon = type==='ok'?'✓':type==='err'?'✗':type==='warn'?'⚠':'ℹ';
  el.innerHTML = `<span class="ico">${icon}</span><div style="flex:1;min-width:0"><div class="title">${E(title||'')}</div><div class="msg">${E(msg||'')}</div></div>`;
  stack.appendChild(el);
  setTimeout(()=>{ el.classList.add('fade'); setTimeout(()=>el.remove(),250); }, type==='err'?7000:4000);
}

// ═════════════════════════════════════════════════════════
// NAV
// ═════════════════════════════════════════════════════════
document.querySelectorAll('.nav-item').forEach(n => n.onclick = () => goPage(n.dataset.page));
function goPage(p) {
  state.page = p;
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.toggle('active', n.dataset.page===p));
  document.querySelectorAll('.page').forEach(s=>s.classList.toggle('active', s.id==='page-'+p));
  // Sofort aus bestehendem State rendern (kein Warten)
  if (p==='leads') renderLeads();
  if (p==='sent') renderSent();
  if (p==='replies') renderReplies();
  if (p==='followup') renderFollowup();
  if (p==='linkedin') renderLinkedin();
  if (['signals','enrichment','review','pipeline','replycenter'].includes(p)) renderPremiumDashboard();
  if (p==='jobs') loadJobs();
  // Bereit zum Senden: erst frisch laden, dann rendern
  if (p==='ready') {
    document.getElementById('ready-table').innerHTML = '<div class="empty"><span class="big">⏳</span>Wird geladen…</div>';
    Promise.all([loadLeads(), loadStats(), loadSenders()]).then(() => {
      renderReady();
      renderSenderCards();
    });
    return;
  }
  // Im Hintergrund frische Daten laden
  loadAll();
}
async function refreshReady() {
  const btn = document.getElementById('ready-refresh-btn') || document.querySelector('[onclick="refreshReady()"]');
  if (btn) { btn.textContent = '⏳ …'; btn.disabled = true; }
  document.getElementById('ready-table').innerHTML = '<div class="empty"><span class="big">⏳</span>Wird geladen…</div>';
  await Promise.all([loadLeads(), loadStats(), loadSenders()]);
  renderReady();
  renderSenderCards();
  if (btn) { btn.textContent = '🔄 Aktualisieren'; btn.disabled = false; }
}

// ═════════════════════════════════════════════════════════
// SENDER MANAGEMENT
// ═════════════════════════════════════════════════════════
async function loadSenders() {
  const d = await fetchJSON('/api/senders');
  if (d) state.senders = d.senders || [];
}

function renderSenderCards() {
  const box = document.getElementById('sender-cards');
  if (!box) return;
  const senders = state.senders || [];
  if (!senders.length) {
    box.innerHTML = '<div style="color:var(--muted);padding:20px">Keine Sender konfiguriert.</div>';
    return;
  }
  // Gesamt-Gewichtssumme für Prozent-Anzeige
  const totalWeight = senders.reduce((s, x) => s + (x.weight || 1), 0);
  box.innerHTML = senders.map(s => {
    const pct = Math.round((s.weight / totalWeight) * 100);
    const used = s.sent_today || 0;
    const limit = s.daily_limit || 0;
    const rem = s.remaining || 0;
    const barPct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
    const barColor = barPct >= 100 ? 'var(--err)' : barPct >= 80 ? 'var(--warn)' : 'var(--ok)';
    const isGmail = s.smtp_host && s.smtp_host.includes('gmail');
    const icon = isGmail ? '📧' : '📬';
    const provider = isGmail ? 'Gmail' : (s.smtp_host || 'SMTP').replace('smtp.','').split('.')[0];
    return `
    <div style="background:var(--card-bg);border:1px solid var(--border);border-radius:10px;padding:14px;display:flex;flex-direction:column;gap:10px">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:18px">${icon}</span>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${s.user}</div>
          <div style="font-size:11px;color:var(--muted)">${provider} · Anteil: <strong>${pct}%</strong></div>
        </div>
        <div style="text-align:right;font-size:12px">
          <div style="font-weight:700;font-size:16px;color:${rem===0?'var(--err)':'var(--ok)'}">${rem}</div>
          <div style="color:var(--muted)">verbleibend</div>
        </div>
      </div>
      <!-- Fortschrittsbalken -->
      <div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:4px">
          <span>Heute gesendet: <strong style="color:var(--fg)">${used}</strong></span>
          <span>Limit: <strong style="color:var(--fg)">${limit}</strong></span>
        </div>
        <div style="background:var(--border);border-radius:4px;height:6px;overflow:hidden">
          <div style="height:100%;width:${barPct}%;background:${barColor};border-radius:4px;transition:width .3s"></div>
        </div>
      </div>
      <!-- Einstellungen -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding-top:4px;border-top:1px solid var(--border)">
        <div>
          <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:3px">📅 Limit / Tag</label>
          <input type="number" min="0" max="500" value="${limit}"
            id="sender-limit-${s.idx}" data-idx="${s.idx}"
            style="width:100%;padding:5px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:13px">
        </div>
        <div>
          <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:3px">⚖️ Gewicht (${pct}%)</label>
          <input type="number" min="1" max="20" value="${s.weight || 1}"
            id="sender-weight-${s.idx}" data-idx="${s.idx}"
            onchange="updateWeightLabel(${s.idx})"
            style="width:100%;padding:5px 8px;background:var(--input-bg);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:13px">
        </div>
      </div>
    </div>`;
  }).join('');
}

function updateWeightLabel(idx) {
  // Prozentzahl live aktualisieren nach Gewicht-Änderung
  const senders = state.senders || [];
  let totalW = 0;
  senders.forEach(s => {
    const inp = document.getElementById(`sender-weight-${s.idx}`);
    totalW += inp ? parseInt(inp.value)||1 : (s.weight||1);
  });
  senders.forEach(s => {
    const inp = document.getElementById(`sender-weight-${s.idx}`);
    if (!inp) return;
    const w = parseInt(inp.value)||1;
    const pct = Math.round((w / totalW) * 100);
    const label = inp.previousElementSibling;
    if (label) label.textContent = `⚖️ Gewicht (${pct}%)`;
  });
}

async function saveSenderSettings() {
  const btn = document.getElementById('save-sender-btn');
  if (btn) { btn.textContent = '⏳ Speichert…'; btn.disabled = true; }
  const senders = (state.senders || []).map(s => ({
    idx: s.idx,
    daily_limit: parseInt(document.getElementById(`sender-limit-${s.idx}`)?.value || s.daily_limit) || 5,
    weight: parseInt(document.getElementById(`sender-weight-${s.idx}`)?.value || s.weight) || 1,
  }));
  const res = await fetchJSON('/api/sender-settings', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({senders})});
  if (res && res.ok) {
    state.senders = res.senders || state.senders;
    renderSenderCards();
    toast('ok', 'Gespeichert', 'Sender-Einstellungen wurden in .env gespeichert.');
  } else {
    toast('err', 'Fehler', 'Einstellungen konnten nicht gespeichert werden.');
  }
  if (btn) { btn.textContent = '💾 Einstellungen speichern'; btn.disabled = false; }
}

// LinkedIn-Filter + Aktionen
function setLiFilter(el) {
  state.filters.li = el.dataset.liFilter;
  el.parentElement.querySelectorAll('.chip').forEach(c=>c.classList.toggle('active', c===el));
  renderLinkedin();
}
function runLinkedinBot() {
  const lim = parseInt(document.getElementById('li-limit').value)||20;
  api('/api/linkedin/run', {limit:lim}, `LinkedIn-Liste (${lim})`);
}

function doLinkedinSearch() {
  const industry = document.getElementById('li-search-industry').value.trim();
  const city = document.getElementById('li-search-city').value.trim();
  const role = document.getElementById('li-search-role').value.trim();
  const count = parseInt(document.getElementById('li-search-count').value)||20;
  if (!industry) {
    toast('warn', 'Branche fehlt', 'Bitte gib eine Branche ein.');
    document.getElementById('li-search-industry').focus();
    return;
  }
  toast('info', '🔍 LinkedIn-Suche läuft', `${industry}${city?' · '+city:''}${role?' · '+role:''} (${count} Leads)`);
  api('/api/linkedin/search', {industry, city, role, count}, `LinkedIn-Suche: ${industry}`);
  // Nach Abschluss automatisch Daten neu laden — wird durch trackJob → loadAll abgedeckt
}

function quickLiSearch(industry, city, role, count) {
  document.getElementById('li-search-industry').value = industry;
  document.getElementById('li-search-city').value = city||'';
  document.getElementById('li-search-role').value = role||'';
  document.getElementById('li-search-count').value = count||20;
  doLinkedinSearch();
}

// Dashboard-Card → Suche starten + Live-Progress
function dashboardLiSearch() {
  const ind = document.getElementById('dash-li-industry').value.trim();
  const city = document.getElementById('dash-li-city').value.trim();
  const role = document.getElementById('dash-li-role').value.trim();
  const cnt = parseInt(document.getElementById('dash-li-count').value)||20;
  if (!ind) {
    toast('warn', 'Bereich fehlt', 'Bitte gib einen Bereich / eine Branche ein.');
    document.getElementById('dash-li-industry').focus();
    return;
  }
  const resDiv = document.getElementById('dash-li-result');
  const resText = document.getElementById('dash-li-result-text');
  const resPct = document.getElementById('dash-li-result-pct');
  const resMsg = document.getElementById('dash-li-result-msg');
  const bar = document.getElementById('dash-li-progress-bar');
  resDiv.style.display = 'block';
  resText.textContent = `Suche läuft: ${ind}${city ? ' · ' + city : ''}${role ? ' · ' + role : ''} (${cnt})`;
  resPct.textContent = '0%';
  bar.style.width = '0%';
  bar.style.background = 'linear-gradient(90deg,#0a66c2,#4d9aff)';
  resMsg.textContent = 'Starte Suche...';

  fetch('/api/linkedin/search', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({industry: ind, city, role, count: cnt})
  }).then(r => r.json()).then(j => {
    if (j.error) { toast('err', 'Fehler', j.error); resText.textContent = '⚠ Fehler: '+j.error; return; }
    document.getElementById('li-search-industry').value = ind;
    document.getElementById('li-search-city').value = city||'';
    document.getElementById('li-search-role').value = role||'';
    document.getElementById('li-search-count').value = cnt;
    if (j.job_id) {
      pollLiSearchProgress(j.job_id, {industry: ind, city, role, count: cnt});
      trackJob(j.job_id, `LinkedIn-Suche: ${ind}`);
    }
  }).catch(e => { toast('err', 'Netzwerkfehler', String(e)); resText.textContent = '⚠ Netzwerkfehler'; });
}

async function pollLiSearchProgress(jobId, params) {
  const resText = document.getElementById('dash-li-result-text');
  const resPct = document.getElementById('dash-li-result-pct');
  const resMsg = document.getElementById('dash-li-result-msg');
  const bar = document.getElementById('dash-li-progress-bar');
  let last = 0, tries = 0;
  while (tries < 1200) {
    await new Promise(r=>setTimeout(r, 1200));
    const j = await fetchJSON('/api/job/'+jobId); if (!j) { tries++; continue; }
    const pct = parseInt(j.progress_pct)||0;
    const phase = j.progress_phase || '';
    const msg = j.progress_msg || '';
    const liCount = parseInt(j.with_linkedin)||0;
    if (pct !== last) { bar.style.width = pct + '%'; resPct.textContent = pct + '%'; last = pct; }
    if (msg) resMsg.textContent = msg;
    if (j.status === 'running') {
      resText.textContent = phase === 'list'
        ? `⏳ LinkedIn-Liste wird gebaut... (${liCount} mit LinkedIn)`
        : `⏳ Suche läuft... (${j.progress_done||0} Kandidaten)`;
      tries++; continue;
    }
    // fertig
    if (j.status === 'ok') {
      bar.style.width = '100%';
      resPct.textContent = '100%';
      bar.style.background = 'linear-gradient(90deg,#10b981,#34d399)';
      resText.textContent = `✓ Fertig — ${liCount} Leads mit LinkedIn-Link unten gelistet`;
      toast('ok', '✓ LinkedIn-Suche fertig', `${liCount} Leads mit LinkedIn-Link`);
      saveLiSearchHistory({...params, ts: Date.now(), li_count: liCount, job_id: jobId});
      renderLiSearchHistory();
      // Lead-Daten neu laden, dann LinkedIn-Tab anzeigen
      await loadAll();
      goPage('linkedin');
    } else {
      bar.style.background = 'linear-gradient(90deg,#ef4444,#f87171)';
      resText.textContent = `⚠ Fehler: ${msg || j.status}`;
      toast('err', 'Suche fehlgeschlagen', msg || j.status);
    }
    return;
  }
}

function dashboardLiQuick(ind, city, role) {
  document.getElementById('dash-li-industry').value = ind;
  document.getElementById('dash-li-city').value = city||'';
  document.getElementById('dash-li-role').value = role||'';
  dashboardLiSearch();
}

// ── Suchverlauf (lokal) ─────────────────────────────────────
const LI_HISTORY_KEY = 'b2b_li_search_history_v1';
function loadLiSearchHistory() {
  try { return JSON.parse(localStorage.getItem(LI_HISTORY_KEY)||'[]'); } catch(e) { return []; }
}
function saveLiSearchHistory(item) {
  let hist = loadLiSearchHistory();
  // Dedup nach industry/city/role
  hist = hist.filter(h => !(h.industry===item.industry && h.city===item.city && h.role===item.role));
  hist.unshift(item);
  hist = hist.slice(0, 12);
  try { localStorage.setItem(LI_HISTORY_KEY, JSON.stringify(hist)); } catch(e){}
}
function renderLiSearchHistory() {
  const row = document.getElementById('dash-li-history-row');
  const list = document.getElementById('dash-li-history');
  if (!row || !list) return;
  const hist = loadLiSearchHistory();
  if (!hist.length) { row.style.display = 'none'; return; }
  row.style.display = 'block';
  list.innerHTML = hist.map(h => {
    const d = new Date(h.ts||0);
    const stamp = isNaN(d) ? '' : `${String(d.getDate()).padStart(2,'0')}.${String(d.getMonth()+1).padStart(2,'0')}. ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
    const lbl = `${E(h.industry||'')}${h.city?' · '+E(h.city):''}${h.role?' · '+E(h.role):''}`;
    const liCount = parseInt(h.li_count)||0;
    return `<button class="chip" onclick="rerunLiSearch('${E(h.industry||'')}','${E(h.city||'')}','${E(h.role||'')}',${parseInt(h.count)||20})" title="Erneut suchen">
      <span style="opacity:.6;margin-right:5px">${stamp}</span>${lbl}${liCount?` <span style="color:#4d9aff;margin-left:4px">${liCount} LI</span>`:''}
    </button>`;
  }).join('');
}
function rerunLiSearch(ind, city, role, count) {
  document.getElementById('dash-li-industry').value = ind||'';
  document.getElementById('dash-li-city').value = city||'';
  document.getElementById('dash-li-role').value = role||'';
  document.getElementById('dash-li-count').value = count||20;
  dashboardLiSearch();
}

// ═════════════════════════════════════════════════════════
// FILTERS
// ═════════════════════════════════════════════════════════
function setFilter(el) {
  const f = el.dataset.filter, v = el.dataset.val;
  state.filters[f] = v;
  el.parentElement.querySelectorAll('.chip').forEach(c=>c.classList.toggle('active', c===el));
  renderLeads();
}
function setSort(el) {
  const tab = el.dataset.sort, v = el.dataset.val;
  state.sorts[tab] = v;
  el.parentElement.querySelectorAll('.chip').forEach(c=>c.classList.toggle('active', c===el));
  if (tab === 'leads') renderLeads();
  else if (tab === 'sent') renderSent();
  else if (tab === 'linkedin') renderLinkedin();
}
function applySort(rows, mode) {
  const r = rows.slice();
  if (mode === 'newest')      r.sort((a,b)=>(b.added_at||'').localeCompare(a.added_at||''));
  else if (mode === 'oldest') r.sort((a,b)=>(a.added_at||'').localeCompare(b.added_at||''));
  else if (mode === 'alpha')  r.sort((a,b)=>(a.company||'').localeCompare(b.company||'','de',{sensitivity:'base'}));
  else if (mode === 'score')  r.sort((a,b)=>(b.score||0)-(a.score||0));
  else if (mode === 'sent_desc') r.sort((a,b)=>(b.sent_at||'').localeCompare(a.sent_at||''));
  else if (mode === 'sent_asc')  r.sort((a,b)=>(a.sent_at||'').localeCompare(b.sent_at||''));
  return r;
}

// ═════════════════════════════════════════════════════════
// LOAD DATA
// ═════════════════════════════════════════════════════════
async function loadAll() {
  await Promise.all([loadStats(), loadLeads(), loadReplies(), loadReplyOperatorQueue(), loadSent(), loadIntentPreview(), loadIntentLeadProduction(), loadIntentEmailReviewQueue(), loadIntentManualDecisionMakerReview(), loadIntentOperatorQueue(), loadPremiumDashboard()]);
  renderDashboard();
  renderPremiumDashboard();
  try { renderLiSearchHistory(); } catch(e) {}
  if (state.page==='leads') renderLeads();
  if (state.page==='ready') renderReady();
  if (state.page==='sent') renderSent();
  if (state.page==='replies') renderReplyOperatorQueue();
  if (state.page==='followup') renderFollowup();
  if (state.page==='linkedin') renderLinkedin();
  if (['signals','enrichment','review','pipeline','replycenter'].includes(state.page)) renderPremiumDashboard();
}
async function loadStats() {
  const s = await fetchJSON('/api/stats'); if (s) state.stats = s;
  paintStats();
}
async function loadLeads() {
  const d = await fetchJSON('/api/leads'); if (d) { state.leads = d.items||[]; state.last_search_started_at = d.last_search_started_at || ''; }
}
async function loadReplies() {
  const d = await fetchJSON('/api/replies'); if (d) state.replies = d.items||[];
}
async function loadReplyOperatorQueue() {
  const d = await fetchJSON('/api/reply-operator-queue'); if (d) state.replyOperatorQueue = d;
}
async function loadSent() {
  const d = await fetchJSON('/api/sent'); if (d) state.sent = d.items||[];
}
async function loadIntentPreview() {
  const d = await fetchJSON('/api/intent-preview'); if (d) state.intentPreview = d;
}
async function loadIntentLeadProduction() {
  const d = await fetchJSON('/api/intent-lead-production'); if (d) state.intentLeadProduction = d;
}
async function loadIntentEmailReviewQueue() {
  const d = await fetchJSON('/api/intent-email-review-queue'); if (d) state.intentEmailReviewQueue = d;
}
async function loadIntentManualDecisionMakerReview() {
  const d = await fetchJSON('/api/intent-manual-decision-maker-review'); if (d) state.intentManualDecisionMakerReview = d;
}
async function loadIntentOperatorQueue() {
  const d = await fetchJSON('/api/intent-operator-queue'); if (d) state.intentOperatorQueue = d;
}
async function loadPremiumDashboard() {
  const d = await fetchJSON('/api/premium-dashboard'); if (d) state.premiumDashboard = d;
}
async function startIntentLeadProduction() {
  const industry = (document.getElementById('intent-prod-industry')?.value||'').trim();
  const city = (document.getElementById('intent-prod-city')?.value||'').trim();
  const signalType = (document.getElementById('intent-prod-signal')?.value||'sales_hiring').trim();
  const modeSel = (document.getElementById('intent-prod-mode')?.value||'preview').trim();
  let limit = parseInt(document.getElementById('intent-prod-limit')?.value||'10', 10);
  if (!Number.isFinite(limit) || limit < 1) limit = 10;
  if (limit > 10) limit = 10;
  if (!industry) {
    toast('warn', 'Branche fehlt', 'Bitte gib eine Branche / Zielgruppe ein.');
    return;
  }
  toast('info', '🎯 Intent Lead Production gestartet', `${industry}${city?' · '+city:''} · ${signalType} · limit ${limit} · ${modeSel}`);
  try {
    const r = await fetch('/api/intent-lead-production/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({industry, city, signal_type: signalType, mode: modeSel, limit}),
    });
    const j = await r.json();
    if (j.error) {
      toast('err', 'Fehler', j.error);
      return;
    }
    if (j.job_id) {
      // Refresh nach kurzer Wartezeit, damit der Background-Job die Ausgabe schreiben kann.
      setTimeout(async () => {
        await loadIntentLeadProduction();
        renderIntentLeadProduction();
        toast('ok', '✓ Intent Lead Production aktualisiert', 'Dashboard zeigt neue Daten.');
      }, 4000);
    }
  } catch (e) {
    toast('err', 'Netzwerkfehler', String(e));
  }
}
async function loadJobs() {
  const d = await fetchJSON('/api/jobs'); if (!d) return;
  state.jobs = d.jobs||[];
  document.getElementById('job-log').textContent = (d.log||[]).join('\n');
  const rows = state.jobs.slice().reverse().map(j=>{
    const cls = j.status==='ok'?'ok':j.status==='running'?'acc':'err';
    return `<tr><td>${E(j.started_at||'')}</td><td><strong>${E(j.name)}</strong><br><small style="color:var(--muted)">${E(j.cmd||'')}</small></td><td><span class="pill ${cls}">${E(j.status)}</span></td><td>${E(j.ended_at||'–')}</td></tr>`;
  }).join('');
  document.getElementById('jobs-table').innerHTML = `<table class="tbl"><thead><tr><th>Start</th><th>Job</th><th>Status</th><th>Ende</th></tr></thead><tbody>${rows||'<tr><td colspan=4 class="empty">Noch keine Jobs.</td></tr>'}</tbody></table>`;
}

// ═════════════════════════════════════════════════════════
// PAINT
// ═════════════════════════════════════════════════════════
function paintStats() {
  const s = state.stats;
  document.getElementById('kpi-total').textContent = s.total ?? '–';
  document.getElementById('kpi-sent').textContent = s.sent ?? '–';
  document.getElementById('kpi-today').textContent = s.sent_today ?? '–';
  document.getElementById('kpi-replies').textContent = s.replies_open ?? '–';
  document.getElementById('kpi-hot').textContent = s.replies_hot ?? '–';
  document.getElementById('kpi-fu').textContent = s.fu_due ?? '–';
  document.getElementById('live-ts').textContent = s.ts || '–';
  document.getElementById('nav-leads').textContent = s.total ?? '–';
  // Nav-Ready zeigt approved + awaiting kombiniert
  document.getElementById('nav-ready').textContent = (s.approved||0) + (s.awaiting_approval||0);
  document.getElementById('nav-sent').textContent = s.sent ?? '–';
  document.getElementById('nav-replies').textContent = s.replies_hot ?? '–';
  document.getElementById('nav-fu').textContent = s.fu_due ?? '–';
  const liNav = document.getElementById('nav-li');
  if (liNav) liNav.textContent = (s.li_todo||0) + (s.li_progress||0);
}

function tierBadge(t) { return `<span class="tier ${E(t||'C')}">${E(t||'C')}</span>`; }
function statusPill(label, cls='acc') { return `<span class="pill ${cls}">${E(label||'-')}</span>`; }
function initials(name) {
  const p = String(name||'?').trim().split(/\s+/).filter(Boolean);
  return p.slice(0, 2).map(w => w[0]).join('').toUpperCase() || '?';
}
function renderPremiumDashboard() {
  const d = state.premiumDashboard || {};
  const c = d.counts || {};
  const byId = (id) => document.getElementById(id);
  if (byId('nav-signals')) byId('nav-signals').textContent = c.signals || 0;
  if (byId('nav-enrichment')) byId('nav-enrichment').textContent = c.enriched || 0;
  if (byId('nav-review')) byId('nav-review').textContent = c.review_items || 0;
  if (byId('nav-pipeline')) byId('nav-pipeline').textContent = c.pipeline || 0;
  if (byId('nav-replycenter')) byId('nav-replycenter').textContent = c.replies || 0;
  const overviewTitle = byId('overview-banner-title');
  const overviewSub = byId('overview-banner-sub');
  if (overviewTitle) overviewTitle.textContent = `${c.signals || 0} Signale · ${c.review_items || 0} Review Items · ${c.pipeline || 0} Pipeline Leads`;
  if (overviewSub) overviewSub.textContent = `Signal A: ${c.signal_A || 0} · Signal B: ${c.signal_B || 0} · Pending Reviews: ${c.review_items || 0}`;
  const stageWrap = byId('overview-stage-strip');
  if (stageWrap) {
    const stages = [
      ['A', c.signal_A || 0, 'ok', 'ready/resolved'],
      ['B', c.signal_B || 0, 'acc', 'website resolution'],
      ['C', c.signal_C || 0, 'warn', 'manual review'],
      ['D', c.signal_D || 0, 'err', 'blocked'],
    ];
    stageWrap.innerHTML = stages.map(s => `<div class="premium-card" style="padding:14px;min-width:140px;flex:1"><div>${tierBadge(s[0])}</div><div class="kpi-val" style="font-size:24px;margin-top:8px">${s[1]}</div><div class="kpi-sub">${s[3]}</div></div>`).join('');
  }
  const conv1 = byId('overview-conv-1'); const conv2 = byId('overview-conv-2'); const conv3 = byId('overview-conv-3');
  const convBar1 = byId('overview-convbar-1'); const convBar2 = byId('overview-convbar-2'); const convBar3 = byId('overview-convbar-3');
  const sig = Math.max(1, c.signals || 1);
  const review = Math.max(1, c.review_items || 1);
  if (conv1) conv1.textContent = `${c.enriched || 0}%`;
  if (conv2) conv2.textContent = `${review}%`;
  if (conv3) conv3.textContent = `${c.pipeline || 0}%`;
  if (convBar1) convBar1.style.width = `${Math.min(100, Math.round(((c.enriched || 0) / sig) * 100))}%`;
  if (convBar2) convBar2.style.width = `${Math.min(100, Math.round(((c.review_items || 0) / Math.max(1, c.enriched || 1)) * 100))}%`;
  if (convBar3) convBar3.style.width = `${Math.min(100, Math.round(((c.pipeline || 0) / Math.max(1, c.review_items || 1)) * 100))}%`;
  const hotCount = byId('overview-hot-count');
  const reviewCount = byId('overview-review-count');
  if (hotCount) hotCount.textContent = d.replies ? (d.replies.filter(r => r.class === 'positive').length || 0) : 0;
  if (reviewCount) reviewCount.textContent = d.email_review ? d.email_review.length : 0;
  const topReplies = byId('overview-top-replies');
  if (topReplies) {
    const replies = (d.replies || []).slice(0, 3);
    topReplies.innerHTML = replies.length ? replies.map(r => `<div class="reply-card"><div class="reply-avatar">${E(initials(r.company||r.from||'?'))}</div><div style="min-width:0"><div class="from">${E(r.subject||'(kein Betreff)')} <span class="muted" style="font-weight:500;font-size:12px">· ${E(r.company||'')}</span></div><div class="subj">${E(r.from||'')}</div><div class="preview">${E(r.preview||r.body_preview||'').slice(0,160)}</div></div><div class="meta">${statusPill(r.class||r.group||'review', r.class==='positive'?'ok':'violet')}<div class="mono">${E(r.ts||'')}</div></div></div>`).join('') : '<div class="empty">Keine Antworten vorhanden.</div>';
  }
  const reviewTable = byId('overview-review-table');
  if (reviewTable) {
    const items = d.email_review || [];
    reviewTable.innerHTML = items.length ? `<table class="tbl"><tbody>${items.slice(0,4).map(it => `<tr><td class="cell-company">${E(it.company_name||'-')}<small>${E(it.website||'')}</small></td><td>${E(it.decision_maker_name||'-')}<br><small>${E(it.decision_maker_role||'')}</small></td><td>${emailReviewStatusPill(it.review_status)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">Keine Review Items vorhanden.</div>';
  }
  const actions = byId('overview-actions');
  if (actions) {
    actions.innerHTML = [
      ['radar','Signale entdecken','Nach Branche + Stadt suchen','signals',''],
      ['sparkles','Queue anreichern',`${c.enriched || 0} Leads offen`,'enrichment',''],
      ['shield-check','Prüfung offen',`${c.review_items || 0} warten auf Entscheidung`,'review','yellow'],
      ['send','Freigeben & senden',`${c.auto_candidates_not_sent || 0} versandbereite Entwürfe`,'pipeline','green'],
    ].map(x => `<div class="stat-tile" style="display:flex;align-items:center;gap:12px;cursor:pointer" onclick="goPage('${x[3]}')"><div style="width:36px;height:36px;border-radius:10px;display:grid;place-items:center;background:${x[4]==='yellow'?'var(--yellow-soft)':x[4]==='green'?'var(--green-soft)':'rgba(110,139,255,0.1)'};color:${x[4]==='yellow'?'var(--yellow)':x[4]==='green'?'var(--green)':'var(--accent)'};border:1px solid ${x[4]==='yellow'?'rgba(240,184,64,0.25)':x[4]==='green'?'rgba(52,211,154,0.25)':'rgba(110,139,255,0.25)'}"><span style="font-size:16px">${x[0]==='radar'?'📡':x[0]==='sparkles'?'✨':x[0]==='shield-check'?'🛡':'📤'}</span></div><div style="flex:1"><div style="font-weight:600;font-size:13px">${x[1]}</div><div class="muted" style="font-size:11.5px;margin-top:2px">${x[2]}</div></div><span style="font-size:14px">›</span></div>`).join('');
  }
  renderPremiumSignals(d);
  renderPremiumEnrichment(d);
  renderPremiumReview(d);
  renderPremiumPipeline(d);
  renderPremiumReplies(d);
}
function renderPremiumSignals(d) {
  const signals = d.signals || [], sources = d.sources || [], c = d.counts || {};
  const sourceBox = document.getElementById('premium-sources');
  if (!sourceBox) return;
  document.getElementById('premium-source-count').textContent = `${sources.length} Quellen`;
  sourceBox.innerHTML = sources.length ? sources.map(s => `
    <div class="source-row"><div class="source-mark">${E((s.source||'?').slice(0,2).toUpperCase())}</div>
      <div style="flex:1;min-width:0"><div class="lead-name">${E(s.source)}</div><div class="lead-sub">${E(s.count)} Signale erfasst</div></div>
      <span class="pill acc">${E(s.count)}</span></div>`).join('') : '<div class="empty">Keine Signalquellen geladen.</div>';
  document.getElementById('premium-tier-summary').innerHTML = ['A','B','C','D'].map(t => {
    const val = c['signal_'+t] || 0, cls = t==='A'?'ok':t==='B'?'acc':t==='C'?'warn':'err';
    return `<div class="premium-card" style="padding:14px"><div>${tierBadge(t)}</div><div class="kpi-val" style="font-size:24px;margin-top:8px">${val}</div><div class="kpi-sub">${t==='A'?'ready/resolved':t==='B'?'website resolution':t==='C'?'manual review':'blocked'}</div></div>`;
  }).join('');
  document.getElementById('premium-signals-badge').textContent = signals.length;
  document.getElementById('premium-signals-table').innerHTML = signals.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Stufe</th><th>Signal</th><th>Firma</th><th>Quelle</th><th>Status</th><th>Next</th></tr></thead><tbody>${
    signals.slice(0,80).map(s => `<tr onclick="openPremiumDrawer(${JSON.stringify(s).replace(/"/g,'&quot;')},'signal')"><td>${tierBadge(s.tier)}</td><td><strong>${E(s.title)}</strong><br><small>${E(s.city)} · ${E(s.industry)}</small></td><td class="cell-company">${E(s.company||'-')}<small>${E(s.website||'')}</small></td><td><a class="intent-link" href="${E(s.url)}" target="_blank" rel="noopener">${E(s.source||'-')}</a></td><td>${statusPill(s.status, s.tier==='A'?'ok':s.tier==='B'?'acc':s.tier==='C'?'warn':'err')}</td><td>${E(s.next_action||'-')}</td></tr>`).join('')
  }</tbody></table></div>` : '<div class="empty">Keine Signale vorhanden.</div>';
}
function renderPremiumEnrichment(d) {
  const rows = d.enriched_leads || [], c = d.counts || {};
  const kpis = document.getElementById('premium-enrichment-kpis');
  if (!kpis) return;
  kpis.innerHTML = [
    ['Aufgelöste Firmen', c.enriched||0, 'acc'],
    ['Review Items', c.review_items||0, 'warn'],
    ['Verified', c.verified||0, 'ok'],
    ['Pattern Review', c.pattern_review||0, 'warn'],
  ].map(x => `<div class="kpi-card"><div class="kpi-lbl">${x[0]}</div><div class="kpi-val ${x[2]}">${x[1]}</div><div class="kpi-sub">echte Datenquelle</div></div>`).join('');
  document.getElementById('premium-enrichment-badge').textContent = rows.length;
  document.getElementById('premium-enrichment-table').innerHTML = rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Firma / Entscheider</th><th>Website</th><th>E-Mail</th><th>Status</th><th>Fehlend</th></tr></thead><tbody>${
    rows.slice(0,80).map(l => `<tr onclick="openPremiumDrawer(${JSON.stringify(l).replace(/"/g,'&quot;')},'lead')"><td><div class="lead-cell"><div class="lead-avatar">${E(initials(l.company))}</div><div><div class="lead-name">${E(l.company||'-')}</div><div class="lead-sub">${E(l.name||'kein Entscheider')}</div></div></div></td><td class="mono">${E(l.website||'-')}</td><td class="mono">${E(l.email||'-')}</td><td>${statusPill(l.status||l.next_action, l.verified?'ok':'warn')}</td><td>${(l.missing||[]).length ? (l.missing||[]).map(m=>statusPill(m,'warn')).join(' ') : statusPill('vollständig','ok')}</td></tr>`).join('')
  }</tbody></table></div>` : '<div class="empty">Keine Enrichment-Daten vorhanden.</div>';
}
function renderPremiumReview(d) {
  const items = d.email_review || [];
  const pending = items.filter(i => (i.review_status||'pending') === 'pending').length;
  const box = document.getElementById('premium-review-table');
  if (!box) return;
  document.getElementById('premium-review-badge').textContent = `${pending} pending`;
  box.innerHTML = items.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Firma</th><th>Entscheider</th><th>E-Mail Kandidat</th><th>Status</th><th style="text-align:right">Aktionen</th></tr></thead><tbody>${
    items.map(it => {
      const rid = E(it.review_id||'');
      const verified = it.personal_email_verified === true;
      const pattern = !verified && (it.personal_email_candidate||'');
      return `<tr><td class="cell-company">${E(it.company_name||'-')}<small>${E(it.website||'')}</small></td><td>${E(it.decision_maker_name||'-')}<br><small>${E(it.decision_maker_role||'')}</small></td><td class="mono">${E(it.personal_email_candidate||it.generic_email||'-')}<br>${pattern?statusPill('Pattern · manuell prüfen','warn'):statusPill('verified','ok')}</td><td>${emailReviewStatusPill(it.review_status)}</td><td class="cell-actions"><button class="btn success sm" onclick="decideIntentEmailReview('${rid}','verified')" ${it.review_status==='verified'?'disabled':''}>Verified</button><button class="btn danger sm" onclick="decideIntentEmailReview('${rid}','rejected')" ${it.review_status==='rejected'?'disabled':''}>Verwerfen</button></td></tr>`;
    }).join('')
  }</tbody></table></div>` : '<div class="empty">Keine Review Items vorhanden.</div>';
  const dmMount = document.getElementById('premium-manual-dm-mount');
  if (dmMount) dmMount.innerHTML = document.getElementById('intent-manual-dm-content')?.innerHTML || '<div class="empty">Manual Decision Maker Queue lädt über bestehende Funktion.</div>';
}
function renderPremiumPipeline(d) {
  const items = (d.pipeline||{}).items || [], sent = d.sent_events || [], c = d.counts || {};
  const k = document.getElementById('premium-pipeline-kpis');
  if (!k) return;
  const ready = Math.max(0, c.auto_candidates_not_sent || 0);
  k.innerHTML = [['Review-ready',ready,'acc'],['Verified',c.verified||0,'ok'],['Pipeline',items.length,'acc'],['Gesendet',sent.length,'ok']].map(x=>`<div class="kpi-card"><div class="kpi-lbl">${x[0]}</div><div class="kpi-val ${x[2]}">${x[1]}</div><div class="kpi-sub">${x[0]==='Review-ready'?'nicht sent-ready':''}</div></div>`).join('');
  document.getElementById('premium-pipeline-table').innerHTML = items.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Firma</th><th>Status</th><th>Bucket</th><th>Kontakt</th></tr></thead><tbody>${items.slice(0,80).map(it=>`<tr><td>${E(it.company_name||it.company||'-')}</td><td>${statusPill(it.status||'-','acc')}</td><td>${statusPill(it.pipeline_bucket||'-',String(it.pipeline_bucket||'').includes('sent')?'ok':'acc')}</td><td class="mono">${E(it.email||it.to||'-')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Keine Pipeline-Daten vorhanden.</div>';
}
function renderPremiumReplies(d) {
  const replies = d.replies || [];
  const box = document.getElementById('premium-reply-list');
  if (!box) return;
  document.getElementById('premium-reply-badge').textContent = replies.length;
  box.innerHTML = replies.length ? replies.slice(0,50).map(r => `<div class="premium-card" style="padding:14px;margin-bottom:10px"><div style="display:flex;gap:12px"><div class="lead-avatar">${E(initials(r.company||r.from||'?'))}</div><div style="flex:1"><div class="lead-name">${E(r.subject||'(kein Betreff)')}</div><div class="lead-sub">${E(r.from||'')} · ${E(r.company||'')}</div><div style="color:var(--text-dim);font-size:12px;margin-top:8px">${E(r.preview||r.body_preview||'').slice(0,260)}</div></div>${statusPill(r.class||r.group||'review','violet')}</div></div>`).join('') : '<div class="empty">Keine Replies vorhanden.</div>';
}
function openPremiumDrawer(item, kind) {
  document.getElementById('drawer-title').textContent = item.company || item.title || 'Detail';
  document.getElementById('drawer-body').innerHTML = `<div class="drawer-section"><h5>${E(kind)}</h5>${Object.entries(item).slice(0,18).map(([k,v])=>`<div class="row"><span>${E(k)}</span><strong>${E(Array.isArray(v)?v.join(', '):v)}</strong></div>`).join('')}</div>`;
  document.getElementById('drawer-actions').innerHTML = '<button class="btn ghost" onclick="closeDrawer()">Schliessen</button>';
  openDrawer();
}

function renderDashboard() {
  const s = state.stats;
  document.getElementById('d-sent').textContent = s.sent||0;
  document.getElementById('d-sent-today').textContent = s.sent_today||0;
  document.getElementById('d-approved').textContent = s.approved||0;
  document.getElementById('d-awaiting').textContent = s.awaiting_approval||0;
  document.getElementById('d-hot').textContent = s.replies_hot||0;
  document.getElementById('d-replies').textContent = s.replies_open||0;
  document.getElementById('d-fu').textContent = s.fu_due||0;

  // Awaiting approval strip
  const as = document.getElementById('awaiting-strip');
  if ((s.awaiting_approval||0) > 0) {
    as.style.display = 'flex';
    document.getElementById('awaiting-strip-count').textContent = `${s.awaiting_approval} Leads warten auf deine Approval`;
  } else as.style.display = 'none';

  // Hot strip
  const hs = document.getElementById('hot-strip');
  if ((s.replies_hot||0) > 0) {
    hs.style.display = 'flex';
    document.getElementById('hot-strip-count').textContent = `${s.replies_hot} Hot Replies`;
  } else hs.style.display = 'none';

  // Recent replies
  const recent = state.replies.slice(0,5);
  document.getElementById('recent-replies-count').textContent = state.replies.length;
  if (!recent.length) {
    document.getElementById('recent-replies').innerHTML = '<div class="empty"><span class="big">📭</span>Noch keine Antworten.<br><small>Sync Replies, um neu eingegangene Mails zu holen.</small></div>';
  } else {
    document.getElementById('recent-replies').innerHTML = `<div class="tbl-wrap"><table class="tbl"><tbody>${
      recent.map(r => {
        const cls = r.class || 'unklar';
        const pillCls = (cls.match(/positive|interest|appoint/i)) ? 'ok' : (cls.match(/neg/i)?'err':(cls.match(/neutr/i)?'acc':'warn'));
        return `<tr class="clickable" onclick="openReply('${E(r.key)}')">
          <td class="cell-company">${E(r.from)}<small>${E(r.subject)}</small></td>
          <td><span class="pill ${pillCls}">${E(cls||'unklar')}</span></td>
          <td style="color:var(--muted);font-size:12px">${E(r.snippet)}</td>
          <td style="color:var(--muted);font-size:11px;text-align:right">${E(r.ts)}</td>
        </tr>`;
      }).join('')
    }</tbody></table></div>`;
  }

  // Recent sent (today)
  const today = new Date().toISOString().slice(0,10);
  const sentToday = state.leads.filter(l => (l.sent_at||'').startsWith(today)).slice(0,5);
  document.getElementById('recent-sent-count').textContent = sentToday.length;
  if (!sentToday.length) {
    document.getElementById('recent-sent').innerHTML = '<div class="empty"><span class="big">📤</span>Heute noch nichts versendet.<br><small>Klick auf "✉ Preview" und dann "📤 Batch senden".</small></div>';
  } else {
    document.getElementById('recent-sent').innerHTML = `<div class="tbl-wrap"><table class="tbl"><tbody>${
      sentToday.map(l => `<tr class="clickable" onclick="openLead('${E(l.key)}')">
        <td class="cell-company">${E(l.company)}<small>${E(l.email)}</small></td>
        <td>${E(l.subject)}</td>
        <td style="color:var(--muted);font-size:11px;text-align:right">${E(l.sent_at)}</td>
      </tr>`).join('')
    }</tbody></table></div>`;
  }

  renderIntentPreview();
  renderIntentLeadProduction();
  renderIntentEmailReviewQueue();
  renderIntentManualDecisionMakerReview();
  renderIntentOperatorQueue();
}

function renderIntentPreview() {
  const box = document.getElementById('intent-preview-content');
  const note = document.getElementById('intent-preview-note');
  const d = state.intentPreview;
  if (!box) return;
  if (!d || !d.available) {
    note.textContent = 'Preview only – noch nicht in normale Lead-Pipeline integriert.';
    box.innerHTML = '<div class="empty"><span class="big">🧠</span>Intent Preview noch nicht erzeugt.</div>';
    return;
  }
  const fs = d.focus_scores || {};
  const sum = d.job_detail_summary || {};
  const urls = d.top_job_detail_urls || [];
  const relSummary = d.relevance_summary;
  const relCandidates = d.relevance_fetch_candidates || [];
  const scoreRows = ['balanced','company_site_focus','portal_signal_focus'].map(k => {
    const s = fs[k] || {};
    return `<tr><td><strong>${E(k)}</strong></td><td>${E(s.score ?? '–')}</td><td>${E(s.company_site_total ?? '–')}</td><td>${E(s.portal_signal_total ?? '–')}</td><td>${E(s.low_quality_total ?? '–')}</td><td>${E(s.average_confidence ?? '–')}</td></tr>`;
  }).join('');
  const urlRows = urls.length ? urls.map(u => `<tr><td class="cell-company">${E(u.portal_domain||'')}<small>${E(u.title||'')}</small></td><td style="word-break:break-all"><a class="intent-link" href="${E(u.url||'#')}" target="_blank" rel="noopener">${E(u.url||'')}</a></td></tr>`).join('') : '<tr><td colspan="2" class="empty">Keine job_detail_page URLs vorhanden.</td></tr>';
  const relStatusPill = {
    relevant: 'ok', maybe_relevant: 'warn', needs_review: 'warn', irrelevant: 'err'
  };
  const relCandRows = relCandidates.map(c =>
    `<tr>
      <td class="cell-company"><strong>${E(c.title||'')}</strong><br><small>Score: ${(c.relevance_score??0).toFixed(2)} · <span class="pill ${relStatusPill[c.relevance_status]||''}">${E(c.relevance_status||'')}</span> · ${E(c.recommended_next_action||'')}</small><br><small style="color:var(--green)">+ ${(c.relevance_reasons||[]).join(', ')||'–'}</small><br><small style="color:var(--red)">− ${(c.rejection_reasons||[]).join(', ')||'–'}</small></td>
      <td style="word-break:break-all"><a class="intent-link" href="${E(c.url||'#')}" target="_blank" rel="noopener">${E(c.url||'')}</a></td>
    </tr>`
  ).join('');
  const relCandTable = relCandRows
    ? `<div class="tbl-wrap" style="margin-top:8px"><table class="tbl"><thead><tr><th>Kandidat</th><th>URL</th></tr></thead><tbody>${relCandRows}</tbody></table></div>`
    : '<div class="empty" style="margin-top:8px">Keine fetch-fähigen Kandidaten.</div>';
  const relSummaryRows = relSummary
    ? `<tr><td>Total Job-Detail-Seiten</td><td>${E(relSummary.total_job_detail_pages)}</td></tr>
       <tr><td>Relevant</td><td><span class="pill ok">${E(relSummary.relevant)}</span></td></tr>
       <tr><td>Maybe Relevant</td><td><span class="pill warn">${E(relSummary.maybe_relevant)}</span></td></tr>
       <tr><td>Needs Review</td><td><span class="pill warn">${E(relSummary.needs_review)}</span></td></tr>
       <tr><td>Irrelevant</td><td><span class="pill err">${E(relSummary.irrelevant)}</span></td></tr>
       <tr><td>→ Fetch Detail</td><td><strong>${E(relSummary.fetch_detail_count)}</strong></td></tr>
       <tr><td>→ Discard</td><td>${E(relSummary.discard_count)}</td></tr>`
    : '<tr><td colspan="2" class="empty">Relevance Filter noch nicht erzeugt.</td></tr>';
  note.textContent = d.note || 'Preview only – noch nicht in normale Lead-Pipeline integriert.';
  box.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div>
        <div style="margin-bottom:10px"><strong>Empfohlener Default-Focus:</strong> <span class="pill ok">${E(d.recommended_default_focus||'–')}</span></div>
        <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Focus</th><th>Score</th><th>Company</th><th>Portal</th><th>Low</th><th>Conf</th></tr></thead><tbody>${scoreRows}</tbody></table></div>
      </div>
      <div>
        <div style="margin-bottom:10px"><strong>Job-Detail Live Test</strong></div>
        <div class="tbl-wrap"><table class="tbl"><tbody>
          <tr><td>Raw Results</td><td>${E(d.job_detail_raw_result_count ?? '–')}</td></tr>
          <tr><td>job_detail_page</td><td>${E(sum.job_detail_page ?? '–')}</td></tr>
          <tr><td>listing_page</td><td>${E(sum.listing_page ?? '–')}</td></tr>
          <tr><td>search_page</td><td>${E(sum.search_page ?? '–')}</td></tr>
          <tr><td>company_profile</td><td>${E(sum.company_profile ?? '–')}</td></tr>
          <tr><td>unknown</td><td>${E(sum.unknown ?? '–')}</td></tr>
          <tr><td>should_fetch_detail</td><td>${E(sum.should_fetch_detail ?? '–')}</td></tr>
        </tbody></table></div>
      </div>
    </div>
    <div style="margin-top:18px"><strong>🧠 Relevance Filter</strong></div>
      <div class="tbl-wrap" style="margin-top:6px"><table class="tbl"><tbody>${relSummaryRows}</tbody></table></div>
    <div style="margin-top:18px"><strong>📌 Fetch-fähige Kandidaten nach Relevance Filter</strong></div>
      ${relCandTable}
    <div style="margin-top:18px"><strong>🎯 Target-Industry Preview Report</strong></div>
      ${renderTargetPreviewReport(d.target_preview_report)}
    <div style="margin-top:18px"><strong>Raw Job Detail URLs</strong></div>
    <div class="tbl-wrap" style="margin-top:8px"><table class="tbl"><tbody>${urlRows}</tbody></table></div>`;
}

function renderTargetPreviewReport(report) {
  if (!report || !report.available) {
    return '<div class="empty" style="margin-top:8px">Noch kein Target Intent Preview Lauf vorhanden.</div>';
  }
  const fitPill = {
    target_fit: 'ok', maybe_fit: 'warn', weak_fit: 'warn', discard: 'err'
  };
  const candRows = (report.candidates || []).map(c =>
    `<tr>
      <td class="cell-company"><strong>${E(c.company||'-')}</strong></td>
      <td><span class="pill ${fitPill[c.fit_status]||''}">${E(c.fit_status||'')}</span></td>
      <td>${(c.score||0).toFixed(3)}</td>
      <td>${E(c.next_action||'')}</td>
      <td style="word-break:break-all"><a class="intent-link" href="${E(c.source_url||'#')}" target="_blank" rel="noopener">${E(c.source_url||'')}</a></td>
    </tr>`
  ).join('') || '<tr><td colspan="5" class="empty">Keine Kandidaten.</td></tr>';
  return `
    <div class="tbl-wrap" style="margin-top:8px"><table class="tbl"><tbody>
      <tr><td>Queries Used</td><td>${E(report.queries_used)}</td></tr>
      <tr><td>Raw Ergebnisse</td><td>${E(report.raw_results)}</td></tr>
      <tr><td>Unique Job-Detail-Seiten</td><td>${E(report.unique_job_detail_pages)}</td></tr>
      <tr><td>Gefetchte Details</td><td>${E(report.fetched_details)}</td></tr>
      <tr><td>Resolved Companies</td><td>${E(report.resolved_companies)}</td></tr>
      <tr><td>Target Fit</td><td><span class="pill ok">${E(report.target_fit)}</span></td></tr>
      <tr><td>Maybe Fit</td><td><span class="pill warn">${E(report.maybe_fit)}</span></td></tr>
      <tr><td>Discard</td><td><span class="pill err">${E(report.discard)}</span></td></tr>
    </tbody></table></div>
    <div style="margin-top:12px"><strong>Kandidaten</strong></div>
    <div class="tbl-wrap" style="margin-top:6px"><table class="tbl"><thead><tr><th>Firma</th><th>Fit</th><th>Score</th><th>Action</th><th>URL</th></tr></thead><tbody>${candRows}</tbody></table></div>`;
}

function renderIntentLeadProduction() {
  const box = document.getElementById('intent-lp-content');
  const summary = document.getElementById('intent-lp-summary');
  const d = state.intentLeadProduction;
  if (!box) return;
  if (!d || !d.available) {
    summary.innerHTML = '';
    box.innerHTML = '<div class="empty"><span class="big">🎯</span>Intent Lead Production noch nicht erzeugt.</div>';
    return;
  }
  // Summary pills
  const statusPill = (label, count, cls) =>
    `<span class="pill ${cls}" style="font-size:14px;padding:6px 14px">${label}: <strong>${count}</strong></span>`;
  // Param-Zeile (Branche/Stadt/Signal/Modus/Limits) — nur wenn Daten vorhanden
  const paramChips = [];
  if (d.industry) paramChips.push(`<span class="pill" style="font-size:11px">Branche: <strong>${E(d.industry)}</strong></span>`);
  if (d.city) paramChips.push(`<span class="pill" style="font-size:11px">Stadt: <strong>${E(d.city)}</strong></span>`);
  if (d.signal_type) paramChips.push(`<span class="pill acc" style="font-size:11px">Signal: <strong>${E(d.signal_type)}</strong></span>`);
  if (d.mode_requested || d.mode_effective) {
    const modeReq = d.mode_requested || '–';
    const modeEff = d.mode_effective || '–';
    const modeMatch = modeReq === modeEff;
    paramChips.push(`<span class="pill ${modeMatch ? '' : 'warn'}" style="font-size:11px">Modus: <strong>${E(modeReq)}</strong>${modeMatch ? '' : ' → <strong>' + E(modeEff) + '</strong>'}</span>`);
  }
  if (d.auto_send_disabled) paramChips.push('<span class="pill err" style="font-size:11px">⚠ Auto-Send deaktiviert</span>');
  if (d.requested_limit || d.effective_limit) {
    const reqL = d.requested_limit || 0;
    const effL = d.effective_limit || 0;
    const cap = reqL !== effL && reqL > 0;
    paramChips.push(`<span class="pill ${cap ? 'warn' : ''}" style="font-size:11px">Limit: <strong>${E(effL)}</strong>${cap ? ' (angefragt: ' + E(reqL) + ')' : ''}</span>`);
  }

  summary.innerHTML = `
    ${paramChips.length ? '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px">' + paramChips.join('') + '</div>' : ''}
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      ${statusPill('Geladen', d.loaded_candidates, '')}
      ${statusPill('Normalisiert', d.normalized_leads, '')}
      ${statusPill('Ready', d.ready_for_approval, 'ok')}
      ${statusPill('Enrichment', d.needs_enrichment, 'warn')}
      ${statusPill('Discard', d.discard, 'err')}
      ${d.generated_at ? `<span style="color:var(--muted);font-size:10px;margin-left:8px">Stand: ${E(d.generated_at)}</span>` : ''}
    </div>`;

  const leads = d.leads || [];
  if (!leads.length) {
    box.innerHTML = '<div class="empty"><span class="big">📋</span>Keine Leads vorhanden.</div>';
    return;
  }

  const statusCls = {
    ready_for_approval: 'ok',
    needs_enrichment: 'warn',
    discard: 'err',
  };

  const rows = leads.map((ld, idx) => {
    const isMock = (ld.contact_quality || '').toLowerCase() === 'invalid_or_mock';
    const stCls = statusCls[ld.status] || '';
    const mockBadge = isMock ? '<br><span class="pill err" style="font-size:9px;margin-top:2px">⚠ Fake/Test-Mail blockiert</span>' : '';
    const missingTags = (ld.missing_fields || []).length
      ? '<br><span style="color:var(--warn);font-size:10px">Fehlt: ' + E((ld.missing_fields||[]).join(', ')) + '</span>'
      : '';

    // Encode body for clipboard copy
    const bodyEncoded = encodeURIComponent(ld.email_body || '');
    const subjEncoded = encodeURIComponent(ld.email_subject || '');

    return `<tr style="${isMock ? 'opacity:.55' : ''}">
      <td class="cell-company">
        <strong>${E(ld.company_name||'-')}</strong>
        ${ld.intent_signal_title ? '<br><small style="color:var(--accent)">📡 ' + E(ld.intent_signal_title) + '</small>' : ''}
        ${ld.signal_reason ? '<br><small style="color:var(--muted);font-size:10px">' + E(ld.signal_reason).substring(0, 100) + '</small>' : ''}
      </td>
      <td>
        ${ld.email ? '<div>' + E(ld.email) + ' <span class="pill dim" style="font-size:9px">' + E(ld.email_type||'') + '</span></div>' : '<span style="color:var(--muted)">–</span>'}
        ${ld.phone ? '<div style="font-size:11px">📞 ' + E(ld.phone) + '</div>' : ''}
        ${mockBadge}
        ${missingTags}
      </td>
      <td>
        <span class="pill ${stCls}">${E(ld.status||'')}</span>
        ${ld.next_action ? '<br><small style="color:var(--muted);font-size:10px">→ ' + E(ld.next_action) + '</small>' : ''}
        ${ld.decision_maker_name ? '<br><small style="font-size:10px">' + E(ld.decision_maker_name) + (ld.decision_maker_role ? ' · ' + E(ld.decision_maker_role) : '') + '</small>' : ''}
      </td>
      <td style="text-align:right;white-space:nowrap">
        ${ld.email_body ? `<button class="btn ghost sm" title="Mailtext kopieren" onclick="event.stopPropagation();navigator.clipboard.writeText(decodeURIComponent('${bodyEncoded}'));toast('ok','Kopiert','Mailtext kopiert')">📋 Mailtext</button>` : ''}
        ${ld.intent_signal_source_url ? `<a class="btn ghost sm" href="${E(ld.intent_signal_source_url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="Signal öffnen">📡 Signal</a>` : ''}
        ${ld.website ? `<a class="btn ghost sm" href="${E(ld.website)}" target="_blank" rel="noopener" onclick="event.stopPropagation()" title="Website öffnen">🌐 Website</a>` : ''}
      </td>
    </tr>
    <tr class="email-preview-row" style="background:var(--surface2);font-size:12px">
      <td colspan="4" style="padding:8px 16px">
        ${ld.email_subject ? '<div style="margin-bottom:4px"><strong>Betreff:</strong> ' + E(ld.email_subject) + '</div>' : ''}
        <div style="color:var(--muted);white-space:pre-wrap;max-height:120px;overflow-y:auto;font-family:ui-monospace,Menlo,monospace;font-size:11px">${E((ld.email_body || '').substring(0, 600))}${(ld.email_body||'').length > 600 ? '…' : ''}</div>
      </td>
    </tr>`;
  }).join('');

  box.innerHTML = `
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th>Firma / Signal</th><th>Kontakt</th><th>Status</th><th style="text-align:right">Aktionen</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div style="margin-top:10px;color:var(--muted);font-size:11px">
      🟢 Ready for Approval · 🟡 Needs Enrichment · 🔴 Discard
      · <a href="#" onclick="event.preventDefault();window.open('/api/intent-lead-production','_blank')" style="color:var(--accent)">📄 JSON Rohdaten</a>
    </div>`;
}

function emailReviewStatusPill(status) {
  const cls = status === 'verified' ? 'ok' : status === 'rejected' ? 'err' : 'warn';
  return `<span class="pill ${cls}">${E(status||'pending')}</span>`;
}

function emailReviewDecisionPill(decision) {
  const cls = decision === 'verify_manually' ? 'warn' : decision === 'use_generic_context' ? 'acc' : decision === 'verified' ? 'ok' : 'err';
  return `<span class="pill ${cls}">${E(decision||'')}</span>`;
}

async function decideIntentEmailReview(reviewId, decision) {
  if (!reviewId || !decision) return;
  const msg = decision === 'verified'
    ? 'Diese Adresse manuell als verified markieren? Es wird keine Mail gesendet.'
    : 'Diesen E-Mail-Kandidaten verwerfen? Es wird keine Mail gesendet.';
  if (!confirm(msg)) return;
  try {
    const r = await fetch('/api/intent-email-review/decision', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({review_id: reviewId, decision}),
    });
    const j = await r.json();
    if (!r.ok || j.error) {
      toast('err', 'Review-Entscheidung fehlgeschlagen', j.error || r.statusText);
      return;
    }
    state.intentEmailReviewQueue = j.queue || state.intentEmailReviewQueue;
    await loadIntentOperatorQueue();
    renderIntentEmailReviewQueue();
    renderIntentOperatorQueue();
    toast('ok', decision === 'verified' ? 'Als verified markiert' : 'Verworfen', 'Nur Review-Dateien aktualisiert. Kein Versand.');
  } catch(e) {
    toast('err', 'Netzwerkfehler', String(e));
  }
}

function renderIntentEmailReviewQueue() {
  const box = document.getElementById('intent-email-review-content');
  const summary = document.getElementById('intent-email-review-summary');
  const badge = document.getElementById('intent-email-review-badge');
  const d = state.intentEmailReviewQueue;
  if (!box || !summary) return;
  if (!d || !d.available) {
    summary.innerHTML = '';
    if (badge) badge.textContent = 'Review';
    box.innerHTML = '<div class="empty"><span class="big">📬</span>Intent Email Review Queue noch nicht erzeugt.</div>';
    return;
  }
  const items = d.review_items || [];
  if (badge) badge.textContent = `${d.pending||0} pending`;
  const pill = (label, count, cls) => `<span class="pill ${cls}" style="font-size:13px;padding:6px 12px">${label}: <strong>${count||0}</strong></span>`;
  summary.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      ${pill('Review Items', d.review_items_created, '')}
      ${pill('Pending', d.pending, 'warn')}
      ${pill('Verified', d.verified_existing, 'ok')}
      ${pill('Rejected', d.rejected, 'err')}
      ${d.generated_at ? `<span style="color:var(--muted);font-size:10px;margin-left:8px">Stand: ${E(d.generated_at)}</span>` : ''}
    </div>`;
  if (!items.length) {
    box.innerHTML = '<div class="empty"><span class="big">📬</span>Keine E-Mail-Kandidaten zur Review.</div>';
    return;
  }
  const rows = items.map((it) => {
    const reviewId = E(it.review_id || '');
    const bodyEncoded = encodeURIComponent(it.email_body || '');
    return `<tr>
      <td class="cell-company">
        <strong>${E(it.company_name||'-')}</strong>
        ${it.website ? `<br><small><a class="intent-link" href="${E(it.website)}" target="_blank" rel="noopener">Website</a></small>` : ''}
        ${it.intent_signal_title ? `<br><small style="color:var(--accent)">📡 ${E(it.intent_signal_title)}</small>` : ''}
      </td>
      <td>
        <strong>${E(it.decision_maker_name||'-')}</strong>
        ${it.decision_maker_role ? `<br><small>${E(it.decision_maker_role)}</small>` : ''}
        ${it.phone ? `<br><small>☎ ${E(it.phone)}</small>` : ''}
      </td>
      <td>
        ${it.personal_email_candidate ? `<div><strong>${E(it.personal_email_candidate)}</strong></div>` : '<span style="color:var(--muted)">-</span>'}
        ${it.generic_email ? `<small>Generic: ${E(it.generic_email)}</small>` : ''}
      </td>
      <td>
        ${emailReviewStatusPill(it.review_status)}
        <br>${emailReviewDecisionPill(it.recommended_decision)}
      </td>
      <td style="text-align:right;white-space:nowrap">
        ${it.email_body ? `<button class="btn ghost sm" onclick="event.stopPropagation();navigator.clipboard.writeText(decodeURIComponent('${bodyEncoded}'));toast('ok','Kopiert','Mailtext kopiert')">Mailtext kopieren</button>` : ''}
        ${it.intent_signal_source_url ? `<a class="btn ghost sm" href="${E(it.intent_signal_source_url)}" target="_blank" rel="noopener">Signal öffnen</a>` : ''}
        ${it.website ? `<a class="btn ghost sm" href="${E(it.website)}" target="_blank" rel="noopener">Website öffnen</a>` : ''}
        <button class="btn success sm" onclick="decideIntentEmailReview('${reviewId}','verified')" ${it.review_status==='verified'?'disabled':''}>Als verified markieren</button>
        <button class="btn danger sm" onclick="decideIntentEmailReview('${reviewId}','rejected')" ${it.review_status==='rejected'?'disabled':''}>Verwerfen</button>
      </td>
    </tr>
    <tr class="email-preview-row" style="background:var(--surface2);font-size:12px">
      <td colspan="5" style="padding:8px 16px">
        ${it.email_subject ? '<div style="margin-bottom:4px"><strong>Betreff:</strong> ' + E(it.email_subject) + '</div>' : ''}
        <div style="color:var(--muted);white-space:pre-wrap;max-height:150px;overflow-y:auto;font-family:ui-monospace,Menlo,monospace;font-size:11px">${E((it.email_body || '').substring(0, 900))}${(it.email_body||'').length > 900 ? '…' : ''}</div>
      </td>
    </tr>`;
  }).join('');
  box.innerHTML = `
    <div style="color:var(--muted);font-size:12px;margin-bottom:10px">Review only: keine SMTP-Prüfung, keine Pipeline-Integration, kein Versand.</div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th>Firma / Signal</th><th>Entscheider</th><th>E-Mail</th><th>Status</th><th style="text-align:right">Aktionen</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function verifiedEmailPill(verified) {
  return verified
    ? '<span class="pill ok">Manuell verified</span>'
    : '<span class="pill warn">Pattern-Kandidat - manuelle Prüfung nötig</span>';
}

async function saveManualDecisionMakerReview(idx) {
  const item = (state.intentManualDecisionMakerReview?.items || [])[idx];
  if (!item) return;
  const payload = {
    company_name: item.company_name || '',
    website: item.website || '',
    decision_maker_name: (document.getElementById(`manual-dm-name-${idx}`)?.value || '').trim(),
    decision_maker_role: (document.getElementById(`manual-dm-role-${idx}`)?.value || '').trim(),
    decision_maker_source_url: (document.getElementById(`manual-dm-source-${idx}`)?.value || '').trim(),
    decision_maker_confidence: parseFloat(document.getElementById(`manual-dm-confidence-${idx}`)?.value || '0'),
    note: (document.getElementById(`manual-dm-note-${idx}`)?.value || '').trim(),
  };
  try {
    const r = await fetch('/api/intent-manual-decision-maker-review/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const j = await r.json();
    if (!j.ok) { toast('err', 'Nicht gespeichert', j.error || 'Validierung fehlgeschlagen'); return; }
    state.intentManualDecisionMakerReview = j.queue || state.intentManualDecisionMakerReview;
    renderIntentManualDecisionMakerReview();
    toast('ok', 'Entscheider gespeichert', payload.decision_maker_name);
  } catch(e) { toast('err', 'Netzwerkfehler', String(e)); }
}

function renderIntentManualDecisionMakerReview() {
  const box = document.getElementById('intent-manual-dm-content');
  const summary = document.getElementById('intent-manual-dm-summary');
  const badge = document.getElementById('intent-manual-dm-badge');
  const d = state.intentManualDecisionMakerReview;
  if (!box || !summary) return;
  if (!d || d.available === false) {
    summary.innerHTML = '';
    box.innerHTML = '<div class="empty"><span class="big">Manual Review nicht verfuegbar.</span></div>';
    return;
  }
  const items = d.items || [];
  if (badge) badge.textContent = `${items.length} offen`;
  summary.innerHTML = `<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <span class="pill warn">Needs Review: <strong>${items.length}</strong></span>
    <span class="pill ok">Gespeichert: <strong>${d.completed_count||0}</strong></span>
    <span style="color:var(--muted);font-size:11px">Kein Send, keine Pipeline-Integration.</span>
  </div>`;
  if (!items.length) {
    box.innerHTML = '<div class="empty"><span class="big">Keine Leads fuer manuelle Entscheider-Review.</span></div>';
    return;
  }
  const rows = items.map((it, idx) => {
    const completed = it.manual_review || null;
    const cand = (it.existing_candidates || [])[0] || {};
    return `<tr>
      <td class="cell-company">
        <strong>${E(it.company_name||'-')}</strong>
        ${it.website ? `<br><a class="intent-link" href="${E(it.website)}" target="_blank" rel="noopener">Website</a>` : ''}
        ${it.signal_title ? `<br><small style="color:var(--accent)">${E(it.signal_title)}</small>` : ''}
        ${it.signal_url ? `<br><a class="intent-link" href="${E(it.signal_url)}" target="_blank" rel="noopener">Signal</a>` : ''}
      </td>
      <td>
        <span class="pill warn">${E(it.final_status||it.lead_quality_status||'needs_review')}</span>
        <br><small>${E(it.debug_reason||'-')}</small>
        <br><small>Queries: ${E(it.queries_tried_count||0)} · Pages: ${E(it.pages_checked_count||0)}</small>
        ${cand.name ? `<br><small>Kandidat: ${E(cand.name)} (${E(cand.role||'')}, ${E(cand.confidence||0)})</small>` : ''}
        ${completed ? `<br><span class="pill ok">gespeichert: ${E(completed.decision_maker_name||'')}</span>` : ''}
      </td>
      <td>
        <input id="manual-dm-name-${idx}" class="tb-input" placeholder="Name" value="${E(completed?.decision_maker_name||'')}" style="width:100%;margin-bottom:6px">
        <input id="manual-dm-role-${idx}" class="tb-input" placeholder="Rolle" value="${E(completed?.decision_maker_role||'')}" style="width:100%;margin-bottom:6px">
        <input id="manual-dm-source-${idx}" class="tb-input" placeholder="Quelle/URL" value="${E(completed?.decision_maker_source_url||'')}" style="width:100%;margin-bottom:6px">
        <input id="manual-dm-confidence-${idx}" class="tb-input" type="number" min="0" max="1" step="0.05" value="${E(completed?.decision_maker_confidence||'0.9')}" style="width:100%;margin-bottom:6px">
        <input id="manual-dm-note-${idx}" class="tb-input" placeholder="Notiz optional" value="${E(completed?.note||'')}" style="width:100%">
      </td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn success sm" onclick="saveManualDecisionMakerReview(${idx})">Entscheider speichern</button>
      </td>
    </tr>`;
  }).join('');
  box.innerHTML = `<div class="tbl-wrap"><table class="tbl">
    <thead><tr><th>Firma / Signal</th><th>Status / Debug</th><th>Manuelle Eingabe</th><th style="text-align:right">Aktion</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderIntentOperatorQueue() {
  const healthBox = document.getElementById('intent-operator-health');
  const box = document.getElementById('intent-operator-content');
  const badge = document.getElementById('intent-operator-badge');
  const d = state.intentOperatorQueue;
  if (!healthBox || !box) return;
  if (!d || !d.available) {
    healthBox.innerHTML = '';
    box.innerHTML = '<div class="empty"><span class="big">Operator Queue nicht verfügbar.</span></div>';
    return;
  }
  const h = d.health || {};
  const c = d.counts || {};
  if (badge) badge.textContent = `${c.pending_review||0} review`;
  const warn = (h.warnings||[]).length
    ? `<div class="pill err">Warnung: ${E((h.warnings||[]).join(', '))}</div>`
    : '<div class="pill ok">Safety OK</div>';
  const pill = (label, val, cls='') => `<span class="pill ${cls}" style="font-size:12px;padding:6px 10px">${label}: <strong>${E(val)}</strong></span>`;
  healthBox.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
      ${warn}
      ${pill('Search', h.search_status||'unknown', String(h.search_status||'').includes('blocked')?'err':'ok')}
      ${pill('Pipeline Sync', h.pipeline_sync_status||'unknown', (h.pipeline_sync_status==='synced'||h.pipeline_sync_status==='dry_run_no_write')?'ok':'warn')}
      ${pill('Pending Review', h.pending_review_count||0, 'warn')}
      ${pill('Verified', h.verified_count||0, 'ok')}
      ${pill('Auto Eligible', h.auto_eligible_count||0, 'acc')}
      ${pill('Approved Pending Send', h.approved_pending_send_count||0, 'warn')}
      ${pill('Sent', h.sent_count||0, 'ok')}
      ${pill('Sent Today', h.sent_today_total||0, '')}
    </div>
    <div style="font-size:11px;color:var(--muted)">Canonical Pipeline: <code>${E(h.canonical_pipeline_file||'')}</code></div>`;

  const reviewRows = (d.needs_email_review||[]).map(it => {
    const reviewId = E(it.review_id||'');
    const verified = it.personal_email_verified === true;
    return `<tr>
      <td class="cell-company"><strong>${E(it.company_name||'-')}</strong>
        ${it.website ? `<br><a class="intent-link" href="${E(it.website)}" target="_blank" rel="noopener">Website</a>` : ''}
        ${it.intent_signal_source_url ? `<br><a class="intent-link" href="${E(it.intent_signal_source_url)}" target="_blank" rel="noopener">Signal</a>` : ''}
      </td>
      <td>${E(it.decision_maker_name||'-')}<br><small>${E(it.decision_maker_role||'')}</small></td>
      <td><strong>${E(it.personal_email_candidate||it.generic_email||'-')}</strong><br>${verifiedEmailPill(verified)}</td>
      <td>${emailReviewStatusPill(it.review_status)}<br>${emailReviewDecisionPill(it.recommended_decision)}</td>
      <td>
        ${it.rejected_email_reason ? `<div class="pill err">E-Mail blockiert: ${E(it.rejected_email_reason)}</div><small>${E(it.rejected_email||'')}</small>` : ''}
        ${it.rejected_phone_reason ? `<div class="pill err">Telefon blockiert: ${E(it.rejected_phone_reason)}</div><small>${E(it.rejected_phone||'')}</small>` : ''}
        ${(it.risk_flags||[]).length ? `<br><small>${E((it.risk_flags||[]).join(', '))}</small>` : ''}
      </td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn success sm" onclick="decideIntentEmailReview('${reviewId}','verified')" ${it.review_status==='verified'?'disabled':''}>Als verified markieren</button>
        <button class="btn danger sm" onclick="decideIntentEmailReview('${reviewId}','rejected')" ${it.review_status==='rejected'?'disabled':''}>Verwerfen</button>
      </td>
    </tr>`;
  }).join('');

  const verifiedRows = (d.manually_verified||[]).map(it => `<tr>
    <td class="cell-company"><strong>${E(it.company_name||'-')}</strong>${it.website?`<br><a class="intent-link" href="${E(it.website)}" target="_blank" rel="noopener">Website</a>`:''}</td>
    <td>${E(it.decision_maker_name||'-')}<br><small>${E(it.decision_maker_role||'')}</small></td>
    <td><strong>${E(it.personal_email||it.personal_email_candidate||'-')}</strong><br>${verifiedEmailPill(it.personal_email_verified===true)}</td>
    <td>${E(it.verified_at||'-')}<br><small>${E(it.verification_method||'-')}</small></td>
    <td>${E(it.next_action||'-')}</td>
    <td>${it.ready_for_approval ? '<span class="pill ok">ready_for_approval</span>' : '<span class="pill warn">not ready</span>'}</td>
  </tr>`).join('');

  const autoRows = (d.auto_send_candidates||[]).map(it => {
    const ok = String(it.auto_send_status||'') === 'auto_eligible';
    return `<tr>
      <td class="cell-company"><strong>${E(it.company_name||'-')}</strong></td>
      <td>${E(it.decision_maker_name||'-')}<br><small>${E(it.decision_maker_role||'')}</small></td>
      <td>${E(it.personal_email||'-')}</td>
      <td><span class="pill ${ok?'ok':'err'}">${E(it.auto_send_status||'-')}</span><br><small>${E(it.next_action||'')}</small></td>
      <td>${(it.block_reasons||[]).length ? E((it.block_reasons||[]).join(', ')) : '<span style="color:var(--muted)">Noch nicht gesendet</span>'}</td>
    </tr>`;
  }).join('');

  const pipeRows = ((d.outreach_pipeline||{}).items||[]).slice(0, 20).map(it => {
    const bucket = it.pipeline_bucket || '';
    const cls = bucket === 'sent' ? 'ok' : bucket === 'approved_pending_send' ? 'warn' : bucket === 'replied' ? 'acc' : '';
    return `<tr>
      <td class="cell-company"><strong>${E(it.company_name||'-')}</strong>${it.website?`<br><a class="intent-link" href="${E(it.website)}" target="_blank" rel="noopener">Website</a>`:''}</td>
      <td>${E(it.email||'-')}</td>
      <td><span class="pill ${cls}">${E(bucket)}</span><br><small>${E(it.outreach_stage||'')}</small></td>
      <td>${it.approved_for_send ? '<span class="pill ok">approved</span>' : '<span class="pill warn">not approved</span>'}</td>
      <td>${E(it.source||'-')}</td>
      <td>${E(it.sent_at||it.first_sent_at||'-')}</td>
    </tr>`;
  }).join('');

  const blockedRows = (d.blocked_rejected_already_contacted||[]).map(it => `<tr>
    <td class="cell-company"><strong>${E(it.company_name||'-')}</strong></td>
    <td><span class="pill err">${E(it.status||'blocked')}</span></td>
    <td>${E(it.reason||'-')}</td>
    <td>${E(it.source||'-')}</td>
    <td>${it.rejected_email ? `rejected_email: ${E(it.rejected_email)}` : ''}${it.rejected_phone ? `rejected_phone: ${E(it.rejected_phone)}` : ''}</td>
  </tr>`).join('');

  const table = (title, rows, head) => `
    <div style="margin-top:18px"><strong>${title}</strong></div>
    <div class="tbl-wrap" style="margin-top:8px"><table class="tbl">
      <thead>${head}</thead>
      <tbody>${rows || `<tr><td colspan="6" class="empty">Keine Einträge.</td></tr>`}</tbody>
    </table></div>`;

  box.innerHTML = `
    ${table('Needs Email Review', reviewRows, '<tr><th>Firma / Links</th><th>Entscheider</th><th>E-Mail</th><th>Review</th><th>Hygiene</th><th style="text-align:right">Aktionen</th></tr>')}
    ${table('Manually Verified', verifiedRows, '<tr><th>Firma</th><th>Entscheider</th><th>E-Mail</th><th>Verified</th><th>Next Action</th><th>Ready</th></tr>')}
    ${table('Auto Send Candidates', autoRows, '<tr><th>Firma</th><th>Entscheider</th><th>E-Mail</th><th>Status</th><th>Hinweis</th></tr>')}
    ${table('Outreach Pipeline (canonical)', pipeRows, '<tr><th>Firma</th><th>E-Mail</th><th>Status</th><th>Approved</th><th>Source</th><th>Sent At</th></tr>')}
    ${table('Blocked / Rejected / Already Contacted', blockedRows, '<tr><th>Firma</th><th>Status</th><th>Grund</th><th>Quelle</th><th>Details</th></tr>')}
  `;
}

function leadFilter(l) {
  const q = (document.getElementById('leads-search')?.value||'').toLowerCase().trim();
  if (q) {
    const hay = (l.company+' '+l.email+' '+l.city+' '+l.industry+' '+l.contact).toLowerCase();
    if (!hay.includes(q)) return false;
  }
  const stage = state.filters.stage;
  if (stage !== 'all') {
    if (stage === 'ready' && !l.ready) return false;
    if (stage === 'sent' && l.stage !== 'sent') return false;
    // Neu = Leads aus der aktuellsten Suche (added_at >= last_search_started_at)
    // Fallback: falls kein last_search_started_at gesetzt, zeige unprocessed leads
    if (stage === 'new') {
      if (l.sent_already) return false;  // bereits gesendete nie als neu anzeigen
      if (state.last_search_started_at) {
        if (!l.added_at || l.added_at < state.last_search_started_at) return false;
      } else {
        // Ohne Such-Timestamp: alte Logik als Fallback
        if (l.ready) return false;
      }
    }
    if (stage === 'replied' && !(l.reply_status && l.reply_status !== 'none')) return false;
  }
  if (state.filters.contact === 'email' && !l.email) return false;
  if (state.filters.contact === 'phone' && !l.phone) return false;
  return true;
}

function renderLeadsTable(rows, container) {
  // Sortierung kommt vom Caller (renderLeads/renderSent setzen state.sorts);
  // Leads ohne Telefon bleiben sichtbar — Phone-Chip-Filter (data-filter="contact" val="phone")
  // blendet sie explizit aus wenn gewünscht.
  if (!rows.length) {
    document.getElementById(container).innerHTML = '<div class="empty"><span class="big">📋</span>Keine Treffer.<br><small>Anderes Filter oder Lead-Suche starten.</small></div>';
    return;
  }
  const html = `<table class="tbl"><thead><tr>
    <th>Firma / Kontakt</th><th>Email / Tel</th><th>Branche / Stadt</th>
    <th>Status</th><th>Score</th><th style="text-align:right">Aktionen</th>
  </tr></thead><tbody>${
    rows.map(l => {
      // Status badges — wahrer Workflow: NEU → BEREIT (preview) → APPROVED (user) → GESENDET
      let stagePill;
      if (l.sent_already) stagePill = '<span class="pill ok">📤 Gesendet</span>';
      else if (l.approved) stagePill = '<span class="pill acc">🚀 Freigegeben</span>';
      else if (l.ready) stagePill = '<span class="pill warn">⏸ Wartet auf Approval</span>';
      else stagePill = '<span class="pill dim">Neu</span>';

      const replyPill = l.reply_status && l.reply_status !== 'none'
        ? `<span class="pill ${l.reply_status.match(/pos/i)?'ok':l.reply_status.match(/neg/i)?'err':'warn'}">${E(l.reply_status)}</span>`:'';
      const errPill = l.last_error && !l.sent_already ? `<br><small style="color:var(--err);font-size:10px" title="${E(l.last_error)}">⚠ ${E((l.last_error||'').substring(0,50))}</small>` : '';
      const score = parseInt(l.score)||0;
      const scoreCls = score >= 75 ? '' : score >= 50 ? '' : 'dim';

      // Action buttons
      let actions = '';
      if (l.email && !l.sent_already && !l.do_not_resend) {
        if (l.ready && !l.approved) {
          actions += `<button class="btn primary sm" title="Freigeben für Versand" onclick="event.stopPropagation();leadApprove('${E(l.key)}')">✓ Approve</button>`;
        }
        if (l.approved) {
          actions += `<button class="btn success sm" title="Sofort senden" onclick="event.stopPropagation();leadSend('${E(l.key)}')">📤 Senden</button>`;
        }
      }
      if (l.website) actions += `<a class="btn ghost sm icon-only" href="${E(l.website)}" target="_blank" onclick="event.stopPropagation()" title="Website">🌐</a>`;
      // Research-Quick-Links pro Lead — bewusst reduziert:
      // LinkedIn-AP/Firma + Impressum sind hier raus (waren laut User Durcheinander).
      // Der LinkedIn-Tab behaelt seine eigenen Buttons.
      const r = l.research || {};
      if (r.g_gf) actions += `<a class="btn ghost sm icon-only" href="${E(r.g_gf)}" target="_blank" onclick="event.stopPropagation()" title="Google: Geschäftsführer">🕵️</a>`;
      if (l.phone) actions += `<a class="btn ghost sm icon-only" href="tel:${E(l.phone)}" onclick="event.stopPropagation()" title="Anrufen">📞</a>`;
      actions += `<button class="btn ghost sm icon-only" title="Details" onclick="event.stopPropagation();openLead('${E(l.key)}')">↗</button>`;

      return `<tr class="clickable" onclick="openLead('${E(l.key)}')">
        <td class="cell-company">${E(l.company)}<small>${E(l.contact||'—')}</small></td>
        <td>
          ${l.email ? `<a href="mailto:${E(l.email)}" onclick="event.stopPropagation()" style="color:var(--accent)">${E(l.email)}</a>`:'<span style="color:var(--dim)">—</span>'}
          ${l.phone?`<br><small style="color:var(--muted)">${E(l.phone)}</small>`:''}
        </td>
        <td><small>${E(l.industry||'')}</small><br><small style="color:var(--muted)">${E(l.city||'')}</small></td>
        <td>${stagePill} ${replyPill}${errPill}</td>
        <td><span class="score-chip ${scoreCls}">${score}</span></td>
        <td class="cell-actions">${actions}</td>
      </tr>`;
    }).join('')
  }</tbody></table>`;
  document.getElementById(container).innerHTML = html;
}

function renderLeads() {
  const filtered = state.leads.filter(leadFilter);
  renderLeadsTable(applySort(filtered, state.sorts.leads), 'leads-table');
  // Badge-Count für "Neu"-Chip aktualisieren
  const newCount = state.last_search_started_at
    ? state.leads.filter(l => !l.sent_already && l.added_at && l.added_at >= state.last_search_started_at).length
    : state.leads.filter(l => !l.sent_already && !l.ready).length;
  const badge = document.getElementById('new-leads-count');
  if (badge) badge.textContent = newCount > 0 ? `(${newCount})` : '';
}
function renderReady() {
  // "Bereit zum Senden" = approved (echte Sende-Kandidaten)
  // PLUS Vorschau-bereit (warten auf Approval) — als 2 Sektionen
  const approved = state.leads.filter(l => l.approved && !l.sent_already && !l.do_not_resend && l.email);
  const awaiting = state.leads.filter(l => l.ready && !l.approved && !l.sent_already && !l.do_not_resend && l.email);
  let html = '';
  if (approved.length) {
    html += `<div class="hot-strip" style="background:linear-gradient(135deg,rgba(16,185,129,.12),rgba(16,185,129,.04));border-color:rgba(16,185,129,.3);margin-bottom:14px">
      <span class="ico">🚀</span><div style="flex:1"><strong style="color:var(--ok)">${approved.length} Mails freigegeben</strong>
      <div style="color:var(--muted);font-size:12px;margin-top:2px">Bereit für sofortigen Versand</div></div></div>`;
    document.getElementById('ready-table').innerHTML = '';
    renderLeadsTable(approved, 'ready-table');
    document.getElementById('ready-table').insertAdjacentHTML('afterbegin', html);
  } else if (awaiting.length) {
    document.getElementById('ready-table').innerHTML = `
      <div class="hot-strip" style="background:linear-gradient(135deg,rgba(245,158,11,.12),rgba(245,158,11,.04));border-color:rgba(245,158,11,.3)">
        <span class="ico">⏸</span>
        <div style="flex:1"><strong style="color:var(--warn)">${awaiting.length} Leads warten auf Freigabe</strong>
        <div style="color:var(--muted);font-size:12px;margin-top:2px">Klick auf ✓ Approve in der Liste, um sie für den Versand freizugeben.</div></div>
      </div>`;
    const wrap = document.createElement('div');
    wrap.id = 'ready-table-inner';
    document.getElementById('ready-table').appendChild(wrap);
    renderLeadsTable(awaiting, 'ready-table-inner');
  } else {
    document.getElementById('ready-table').innerHTML = '<div class="empty"><span class="big">📭</span>Keine Leads bereit zum Senden.<br><small>Klick auf "✉ Preview" um neue Leads vorzubereiten oder starte eine Lead-Suche.</small></div>';
  }
}
function renderSent() { renderLeadsTable(applySort(state.leads.filter(l => l.sent_already), state.sorts.sent), 'sent-table'); }
function renderFollowup() { renderLeadsTable(state.leads.filter(l => l.next_followup), 'fu-table'); }

// ═════════════════════════════════════════════════════════
// LINKEDIN RENDERING
// ═════════════════════════════════════════════════════════
const LI_STATUS_LABEL = {
  todo: '📋 To-Do', found: '🔎 Gefunden', connect_sent: '📨 Connect ges.',
  connected: '✓ Connected', dm_sent: '💬 DM ges.', replied: '💬 Replied',
  meeting: '🎯 Termin', skip: '⏭ Skip',
};
const LI_STATUS_PILL = {
  todo: 'dim', found: 'acc', connect_sent: 'warn', connected: 'acc',
  dm_sent: 'warn', replied: 'ok', meeting: 'ok', skip: 'dim',
};

function liGroupOf(s) {
  if (!s || s === 'todo') return 'todo';
  if (s === 'skip') return 'skip';
  if (s === 'replied' || s === 'meeting') return 'replied';
  return 'progress';
}

function liScore(l) {
  // höhere Priorität: A-Leads, mit Kontakt, mit Email
  let s = parseInt(l.score)||0;
  if (l.contact && l.contact.length > 3) s += 12;
  if (l.email) s += 8;
  if (l.research && l.research.li_person) s += 5;
  return s;
}

function renderLinkedin() {
  // KPIs
  document.getElementById('li-kpi-todo').textContent = state.stats.li_todo||0;
  document.getElementById('li-kpi-progress').textContent = state.stats.li_progress||0;
  document.getElementById('li-kpi-replied').textContent = state.stats.li_replied||0;

  const q = (document.getElementById('li-search')?.value||'').toLowerCase().trim();
  const fil = state.filters.li || 'all';
  const limit = parseInt(document.getElementById('li-limit')?.value)||20;

  // LinkedIn-Bot-Bereich: NUR Leads mit echtem LinkedIn-Link auflisten.
  // Ohne brauchbaren LinkedIn-Treffer hat der Lead in dieser Ansicht nichts zu suchen.
  function _hasLinkedinLink(l) {
    const r = l.research || {};
    const cand = (l.linkedin_company || l.linkedin_company_url_clean
                  || r.li_company || r.li_person || l.linkedin_person_url || r.g_person_li || '');
    return typeof cand === 'string' && cand.toLowerCase().indexOf('linkedin.com') >= 0;
  }
  let rows = state.leads.filter(l => l.company && _hasLinkedinLink(l));
  // User-Sort hat Vorrang vor liScore, fällt auf liScore zurück wenn keine Auswahl.
  const liSort = state.sorts.linkedin || 'newest';
  if (liSort === 'newest')      rows.sort((a,b)=>(b.added_at||'').localeCompare(a.added_at||''));
  else if (liSort === 'oldest') rows.sort((a,b)=>(a.added_at||'').localeCompare(b.added_at||''));
  else if (liSort === 'alpha')  rows.sort((a,b)=>(a.company||'').localeCompare(b.company||'','de',{sensitivity:'base'}));
  else                          rows.sort((a,b) => liScore(b) - liScore(a));

  if (fil !== 'all') rows = rows.filter(l => liGroupOf(l.li_status) === fil);
  if (q) rows = rows.filter(l =>
    (l.company||'').toLowerCase().includes(q) ||
    (l.contact||'').toLowerCase().includes(q) ||
    (l.email||'').toLowerCase().includes(q) ||
    (l.industry||'').toLowerCase().includes(q) ||
    (l.city||'').toLowerCase().includes(q)
  );

  rows = rows.slice(0, Math.max(limit, 50));

  if (!rows.length) {
    document.getElementById('li-table').innerHTML =
      '<div class="empty"><span class="big">💼</span>Keine Leads für LinkedIn-Outreach.<br><small>Erst Lead-Suche starten oder anderen Filter wählen.</small></div>';
    return;
  }

  const html = `<table class="tbl"><thead><tr>
    <th style="width:34px">#</th>
    <th>Firma / Kontakt</th>
    <th>Branche / Stadt</th>
    <th>Recherche</th>
    <th>Texte</th>
    <th>Status</th>
    <th>Score</th>
  </tr></thead><tbody>${
    rows.map((l, idx) => {
      const r = l.research || {};
      const status = l.li_status || 'todo';
      const pillCls = LI_STATUS_PILL[status] || 'dim';
      const lbl = LI_STATUS_LABEL[status] || status;
      const score = parseInt(l.score)||0;

      // Reduziertes Button-Set: Google + LinkedIn + Website. Sonst nichts.
      let researchBtns = '';
      const liLink = l.linkedin_company || l.linkedin_company_url_clean
                      || r.li_company || r.li_person || l.linkedin_person_url || r.g_person_li || '';
      const googleLink = r.g_company || r.g_person_li || r.g_gf || r.g_impressum || '';
      if (googleLink) researchBtns += `<a class="btn ghost sm icon-only" href="${E(googleLink)}" target="_blank" onclick="event.stopPropagation()" title="Google-Suche" style="color:#94a3b8">🔎</a>`;
      if (liLink) researchBtns += `<a class="btn ghost sm icon-only" href="${E(liLink)}" target="_blank" onclick="event.stopPropagation()" title="LinkedIn öffnen" style="color:#0a66c2">💼</a>`;
      if (l.website) researchBtns += `<a class="btn ghost sm icon-only" href="${E(l.website)}" target="_blank" onclick="event.stopPropagation()" title="Website öffnen" style="color:#34d399">🌐</a>`;

      const copyBtns = `
        <button class="btn ghost sm" onclick="liCopyText('${E(l.key)}','connect')" title="Connection-Request kopieren">📝 CR</button>
        <button class="btn ghost sm" onclick="liCopyText('${E(l.key)}','dm')" title="1st-DM kopieren">💬 DM</button>
        <button class="btn ghost sm" onclick="liCopyText('${E(l.key)}','followup')" title="Follow-up kopieren">🔁 FU</button>
      `;

      const statusOpts = Object.keys(LI_STATUS_LABEL).map(k =>
        `<option value="${k}" ${k===status?'selected':''}>${LI_STATUS_LABEL[k]}</option>`
      ).join('');

      return `<tr>
        <td style="color:var(--muted);font-size:11px">${idx+1}</td>
        <td class="cell-company">
          ${E(l.company)}
          <small>${E(l.contact||'(Person noch zu suchen)')}</small>
          ${l.email ? `<small><a href="mailto:${E(l.email)}" style="color:var(--accent)">${E(l.email)}</a></small>` : ''}
        </td>
        <td><small>${E(l.industry||'')}</small><br><small style="color:var(--muted)">${E(l.city||'')}</small></td>
        <td><div style="display:flex;gap:4px;flex-wrap:wrap">${researchBtns||'<small style="color:var(--dim)">—</small>'}</div></td>
        <td><div style="display:flex;gap:4px;flex-wrap:wrap">${copyBtns}</div></td>
        <td>
          <select class="li-status-sel" data-key="${E(l.key)}" onchange="liSetStatus(this)" style="background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 8px;font-size:12px">
            ${statusOpts}
          </select>
          ${l.li_status_at ? `<br><small style="color:var(--dim);font-size:10px">${E(l.li_status_at)}</small>` : ''}
        </td>
        <td><span class="score-chip">${score}</span></td>
      </tr>`;
    }).join('')
  }</tbody></table>`;
  document.getElementById('li-table').innerHTML = html;
}

async function liSetStatus(sel) {
  const key = sel.dataset.key;
  const status = sel.value;
  try {
    const r = await fetch('/api/lead/li-status', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({key, status})
    });
    const j = await r.json();
    if (j.error) { toast('err', 'Fehler', j.error); return; }
    toast('ok', 'LinkedIn-Status gesetzt', LI_STATUS_LABEL[status]||status);
    // Lead-Liste neu laden
    setTimeout(loadAll, 200);
  } catch(e) { toast('err', 'Netzwerkfehler', String(e)); }
}

async function liCopyText(key, kind) {
  try {
    const r = await fetch('/api/lead/'+encodeURIComponent(key)+'/copy-texts');
    const j = await r.json();
    if (j.error) { toast('err', 'Fehler', j.error); return; }
    const text = (j.texts||{})[kind] || '';
    if (!text) { toast('warn','Kein Text','Konnte keinen Text generieren.'); return; }
    await navigator.clipboard.writeText(text);
    const lbl = kind==='connect'?'Connection-Request':kind==='dm'?'1st-DM':'Follow-up';
    toast('ok', '📋 Kopiert', `${lbl} (${text.length} Zeichen) — bereit zum Einfügen.`);
  } catch(e) { toast('err', 'Clipboard-Fehler', String(e)); }
}

function renderReplyOperatorQueue() {
  const safety = document.getElementById('reply-operator-safety');
  const list = document.getElementById('reply-operator-list');
  const d = state.replyOperatorQueue;
  if (!safety || !list) return;
  if (!d || !d.available) {
    safety.innerHTML = '';
    list.innerHTML = '<div class="empty"><span class="big">Reply Queue nicht verfuegbar.</span></div>';
    return;
  }
  const c = d.counts || {};
  const s = d.safety || {};
  const pill = (label, val, cls='') => `<span class="pill ${cls}" style="font-size:12px;padding:6px 10px">${label}: <strong>${E(val)}</strong></span>`;
  safety.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
      ${pill('REPLY_AUTO_SEND', s.reply_auto_send || 'false', (s.reply_auto_send === 'false') ? 'ok' : 'err')}
      ${pill('auto_sent', s.auto_sent ?? 0, (s.auto_sent||0) === 0 ? 'ok' : 'err')}
      ${pill('reply_queue_pending', c.reply_queue_pending||0, 'warn')}
      ${pill('positive', c.positive_count||0, 'ok')}
      ${pill('auto_reply', c.auto_reply_count||0, 'acc')}
      ${pill('appointment_ready', c.appointment_ready_count||0, 'ok')}
      ${pill('sent_log_only', c.sent_log_only_count||0, 'warn')}
    </div>
    <div style="font-size:11px;color:var(--muted)">Read-only Operator View. Kein SMTP, kein Send, keine automatische Antwort.</div>`;
  const groups = d.groups || {};
  const titles = [
    ['positive_appointment_ready', 'Positive / Appointment Ready'],
    ['human_review', 'Human Review'],
    ['auto_replies', 'Auto-Replies'],
    ['negative_do_not_contact', 'Negative / Do-Not-Contact'],
    ['unmatched_unclear', 'Unmatched / Unclear'],
  ];
  const classPill = (it) => {
    if (it.is_auto_reply) return '<span class="pill acc">auto_reply neutral</span>';
    if (it.appointment_ready) return '<span class="pill ok">appointment_ready</span>';
    if (/positive|interested/i.test(it.inbound_class||'')) return '<span class="pill ok">positive</span>';
    if (/negative/i.test(it.inbound_class||'')) return '<span class="pill err">negative</span>';
    return `<span class="pill warn">${E(it.inbound_class||'unclear')}</span>`;
  };
  const renderRows = (arr) => (arr||[]).map(it => {
    const sourceCls = it.matched_entry_source === 'pipeline' ? 'ok' : it.matched_entry_source === 'sent_log' ? 'warn' : 'err';
    const action = it.can_classify
      ? `<button class="btn ghost sm" onclick="event.stopPropagation();openReply('${E(it.key)}')">Detail</button>`
      : '<span class="pill warn">sent_log-only</span>';
    return `<tr class="clickable" onclick="openReply('${E(it.key)}')">
      <td class="cell-company"><strong>${E(it.from_email_actual||it.from_email||'-')}</strong><small>${E(it.received_account||'-')} - ${E((it.date||'').substring(0,19))}</small></td>
      <td>${E(it.matched_company||it.original_company||'-')}<br><span class="pill ${sourceCls}">${E(it.matched_entry_source||'unmatched')}</span></td>
      <td><strong>${E(it.subject||'(kein Betreff)')}</strong><br><small>${E(it.original_sent_email||'')}</small></td>
      <td>${classPill(it)}<br><small>${E(it.sentiment||'')} - ${E(it.route||'')}</small></td>
      <td>${it.needs_approval ? '<span class="pill warn">needs_approval</span>' : '<span class="pill dim">no approval</span>'}${it.is_auto_reply ? '<br><span class="pill acc">nicht hot</span>' : ''}</td>
      <td>${E(it.reason||'-')}<br><small>${E(it.suggested_action||'')}</small></td>
      <td style="max-width:360px;color:var(--muted);font-size:12px">${E(it.body_preview||'').substring(0,260)}</td>
      <td class="cell-actions">${action}</td>
    </tr>`;
  }).join('');
  list.innerHTML = titles.map(([key, title]) => {
    const arr = groups[key] || [];
    return `<div class="section">
      <div class="section-head"><h2>${title}</h2><span class="badge">${arr.length}</span></div>
      <div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>From / Account</th><th>Match</th><th>Subject</th><th>Class</th><th>Review</th><th>Reason</th><th>Preview</th><th></th></tr></thead>
        <tbody>${renderRows(arr) || '<tr><td colspan="8" class="empty">Keine Eintraege.</td></tr>'}</tbody>
      </table></div>
    </div>`;
  }).join('');
}

function renderReplies() {
  const list = document.getElementById('replies-list');
  if (!state.replies.length) {
    list.innerHTML = '<div class="empty"><span class="big">📭</span>Keine Antworten gefunden.<br><small>Klick auf "📥 Neue holen" um IMAP zu pollen.</small></div>';
    return;
  }
  // Group: hot first
  const hot = state.replies.filter(r => /pos|interest|appoint/i.test(r.class||''));
  const others = state.replies.filter(r => !/pos|interest|appoint/i.test(r.class||''));
  const renderGroup = (arr, title, cls) => arr.length ? `
    <div class="section">
      <div class="section-head"><h2>${title}</h2><span class="badge">${arr.length}</span></div>
      <div class="tbl-wrap"><table class="tbl"><tbody>${
        arr.map(r => {
          const pillCls = /pos|interest|appoint/i.test(r.class||'') ? 'ok' : /neg/i.test(r.class||'')?'err':/neutr/i.test(r.class||'')?'acc':'warn';
          return `<tr class="clickable" onclick="openReply('${E(r.key)}')">
            <td class="cell-company">${E(r.from)}<small>${E(r.subject||'(kein Betreff)')}</small></td>
            <td><span class="pill ${pillCls}">${E(r.class||'unklar')}</span></td>
            <td style="color:var(--muted);font-size:12px;max-width:400px">${E(r.snippet)}</td>
            <td style="color:var(--muted);font-size:11px;text-align:right">${E(r.ts)}</td>
            <td class="cell-actions">
              <button class="btn ghost sm" onclick="event.stopPropagation();openReply('${E(r.key)}')">Detail →</button>
            </td>
          </tr>`;
        }).join('')
      }</tbody></table></div>
    </div>` : '';
  list.innerHTML = renderGroup(hot, '🔥 Hot Replies', 'hot') + renderGroup(others, '💬 Weitere Antworten', '');
}

// ═════════════════════════════════════════════════════════
// DRAWER
// ═════════════════════════════════════════════════════════
function openDrawer() { document.getElementById('drawer').classList.add('open'); document.getElementById('drawer-bg').classList.add('open'); }
function closeDrawer() { document.getElementById('drawer').classList.remove('open'); document.getElementById('drawer-bg').classList.remove('open'); }

async function openLead(key) {
  openDrawer();
  document.getElementById('drawer-title').textContent = 'Lade Lead…';
  document.getElementById('drawer-body').innerHTML = '<div class="loader">Lade…</div>';
  document.getElementById('drawer-actions').innerHTML = '';
  const e = await fetchJSON('/api/lead/'+encodeURIComponent(key));
  if (!e || e.error) { document.getElementById('drawer-body').innerHTML = '<div class="empty">Lead nicht gefunden.</div>'; return; }
  document.getElementById('drawer-title').innerHTML = `${E(e.company_name||'—')} <span style="font-size:11px;color:var(--muted);font-weight:400">${E(e.outreach_stage||'')}</span>`;
  const fields = [
    ['Email', e.email], ['Telefon', e.phone], ['Website', e.website ? `<a href="${E(e.website)}" target="_blank" style="color:var(--accent)">${E(e.website)}</a>`:'—'],
    ['Kontakt', e.contact_full_name || e.contact_name || '—'], ['Stadt', e.city||e.city_detected||'—'], ['Branche', e.industry||'—'],
    ['Stage', `<span class="pill ${e.outreach_stage==='sent'?'ok':'acc'}">${E(e.outreach_stage||'new')}</span>`],
    ['Reply Status', e.reply_status || 'none'], ['Sent At', (e.first_sent_at||'').substring(0,16) || '—'],
    ['Next Follow-up', (e.next_followup_at||'').substring(0,10) || '—'],
    ['LinkedIn', e.linkedin_company_url_clean ? `<a href="${E(e.linkedin_company_url_clean)}" target="_blank" style="color:var(--accent)">Company →</a>` : '—'],
  ];
  document.getElementById('drawer-body').innerHTML = `
    <div class="drawer-section">
      <h5>Stammdaten</h5>
      ${fields.map(([k,v]) => `<div class="row"><span>${E(k)}</span><span>${v||'—'}</span></div>`).join('')}
    </div>
    ${e.first_email_subject ? `<div class="drawer-section">
      <h5>Erstansprache: ${E(e.first_email_subject)}</h5>
      <div class="email-box">${E(e.first_email_body||'').substring(0,2000)}</div>
    </div>`:''}
    ${e.followup_1_text ? `<div class="drawer-section">
      <h5>Follow-up 1</h5>
      <div class="email-box">${E(e.followup_1_text||'').substring(0,2000)}</div>
    </div>`:''}
    ${e.recommended_sales_angle ? `<div class="drawer-section">
      <h5>Sales-Angle</h5>
      <div style="color:var(--muted);font-size:13px;line-height:1.6">${E(e.recommended_sales_angle)}</div>
    </div>`:''}
  `;
  const acts = [];
  const sentAlready = !!(e.first_sent_at || e.sent_message_id);
  const approved = e.approved_for_send === true || String(e.approved_for_send||'').toLowerCase() === 'true';
  const ready = ['1','true','yes'].includes(String(e.ready_to_send||'').toLowerCase());
  if (e.email && !sentAlready && !e.do_not_resend) {
    if (ready && !approved) acts.push(`<button class="btn primary sm" onclick="leadApprove('${E(e.entry_key)}')">✓ Approve</button>`);
    if (approved) acts.push(`<button class="btn success sm" onclick="leadSend('${E(e.entry_key)}')">📤 Sofort senden</button>`);
  }
  if (e.email) acts.push(`<a class="btn ghost sm" href="mailto:${E(e.email)}">✉ Mail-Client</a>`);
  if (e.phone) acts.push(`<a class="btn ghost sm" href="tel:${E(e.phone)}">📞 Anrufen</a>`);
  if (e.website) acts.push(`<a class="btn ghost sm" href="${E(e.website)}" target="_blank">🌐 Website</a>`);
  acts.push(`<button class="btn ghost sm" style="margin-left:auto" onclick="closeDrawer()">Schließen</button>`);
  document.getElementById('drawer-actions').innerHTML = acts.join('');

  // Research-Links + LinkedIn-Texte als zusätzliche Sections in den Body anhängen
  const r = e._research || {};
  const t = e._li_texts || {};
  const liStatus = e.linkedin_status || 'todo';
  const researchHtml = `
    <div class="drawer-section">
      <h5>🔍 Recherche</h5>
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">Primär</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
        ${r.website_direct ? `<a class="btn ghost sm" href="${E(r.website_direct)}" target="_blank" style="color:#34d399">🌐 Website öffnen</a>`:''}
        ${r.g_person_li ? `<a class="btn ghost sm" href="${E(r.g_person_li)}" target="_blank" style="color:#34d399">🧑 Person auf LinkedIn (Google)</a>`:''}
        ${r.g_company ? `<a class="btn ghost sm" href="${E(r.g_company)}" target="_blank">🌍 Google: Firma</a>`:''}
      </div>
      ${r.g_gf ? `
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">Fallback</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <a class="btn ghost sm" href="${E(r.g_gf)}" target="_blank">🕵️ GF suchen</a>
      </div>` : ''}
    </div>
    <div class="drawer-section">
      <h5>💼 LinkedIn-Status</h5>
      <select onchange="liSetStatusFromDrawer(this,'${E(e.entry_key)}')" style="width:100%;background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:13px">
        ${Object.keys(LI_STATUS_LABEL).map(k=>`<option value="${k}" ${k===liStatus?'selected':''}>${LI_STATUS_LABEL[k]}</option>`).join('')}
      </select>
      ${e.linkedin_status_at ? `<small style="color:var(--dim);font-size:11px">letzte Änderung: ${E(e.linkedin_status_at)}</small>`:''}
    </div>
    <div class="drawer-section">
      <h5>📝 Copy-Paste-Texte</h5>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
        <button class="btn ghost sm" onclick="liCopyText('${E(e.entry_key)}','connect')">📋 Connection-Request kopieren</button>
        <button class="btn ghost sm" onclick="liCopyText('${E(e.entry_key)}','dm')">📋 1st-DM kopieren</button>
        <button class="btn ghost sm" onclick="liCopyText('${E(e.entry_key)}','followup')">📋 Follow-up kopieren</button>
      </div>
      ${t.connect ? `<details style="margin-top:8px"><summary style="cursor:pointer;font-size:12px;color:var(--muted)">Texte ansehen</summary>
        <div style="margin-top:10px"><strong style="font-size:11px">Connection-Request</strong><div class="email-box">${E(t.connect)}</div></div>
        <div style="margin-top:10px"><strong style="font-size:11px">1st-DM</strong><div class="email-box">${E(t.dm)}</div></div>
        <div style="margin-top:10px"><strong style="font-size:11px">Follow-up</strong><div class="email-box">${E(t.followup)}</div></div>
      </details>`:''}
    </div>`;
  document.getElementById('drawer-body').insertAdjacentHTML('beforeend', researchHtml);
}

async function liSetStatusFromDrawer(sel, key) {
  sel.dataset.key = key;
  await liSetStatus(sel);
}

async function openReply(key) {
  openDrawer();
  document.getElementById('drawer-title').textContent = 'Lade Antwort…';
  document.getElementById('drawer-body').innerHTML = '<div class="loader">Lade…</div>';
  document.getElementById('drawer-actions').innerHTML = '';
  const data = await fetchJSON('/api/reply/'+encodeURIComponent(key));
  if (!data) { document.getElementById('drawer-body').innerHTML = '<div class="empty">Antwort nicht gefunden.</div>'; return; }
  const r = data.reply || {};
  const lead = data.lead || {};
  document.getElementById('drawer-title').innerHTML = `${E(lead.company_name||r.from_email||'Antwort')} <span style="font-size:11px;color:var(--muted);font-weight:400">· ${E(r.inbound_class||'unklar')}</span>`;
  const incoming = r.body || r.inbound_snippet || r.snippet || '(kein Body)';
  const draft = r.suggested_body || '';
  document.getElementById('drawer-body').innerHTML = `
    <div class="drawer-section">
      <h5>Header</h5>
      <div class="row"><span>Von</span><span>${E(r.from_email_actual || r.from_email || '—')}</span></div>
      <div class="row"><span>Betreff</span><span>${E(r.inbound_subject || '—')}</span></div>
      <div class="row"><span>Klasse</span><span><span class="pill ${/pos|interest|appoint/i.test(r.inbound_class||'')?'ok':/neg/i.test(r.inbound_class||'')?'err':'warn'}">${E(r.inbound_class||'unklar')}</span></span></div>
      <div class="row"><span>Confidence</span><span>${E(r.confidence||0)}</span></div>
      <div class="row"><span>Empfangen</span><span>${E(r.received_at||r.ts||'—')}</span></div>
      <div class="row"><span>Account</span><span>${E(r.received_account||'—')}</span></div>
    </div>
    <div class="drawer-section">
      <h5>📥 Eingegangener Text</h5>
      <div class="email-box">${E(incoming).substring(0,3000)}</div>
    </div>
    ${draft ? `<div class="drawer-section">
      <h5>📤 Vorgeschlagener Antwort-Entwurf${r.suggested_subject ? ' — '+E(r.suggested_subject) : ''}</h5>
      <div class="email-box draft">${E(draft).substring(0,3000)}</div>
    </div>`:''}
    ${r.meeting_angle ? `<div class="drawer-section"><h5>🎯 Sales-Angle</h5><div style="color:var(--muted);font-size:13px;line-height:1.6">${E(r.meeting_angle)}</div></div>`:''}
  `;
  const k = lead.entry_key || r.entry_key || '';
  document.getElementById('drawer-actions').innerHTML = `
    <button class="btn success sm" onclick="replyClassify('${E(k)}','positive')">✓ Positive</button>
    <button class="btn primary sm" onclick="replyClassify('${E(k)}','interested')">★ Interested</button>
    <button class="btn ghost sm" onclick="replyClassify('${E(k)}','neutral')">~ Neutral</button>
    <button class="btn ghost sm" onclick="replyClassify('${E(k)}','later')">⏰ Later</button>
    <button class="btn danger sm" onclick="replyClassify('${E(k)}','negative')">✗ Negative</button>
    <button class="btn ghost sm" style="margin-left:auto" onclick="closeDrawer()">Schließen</button>
  `;
}

// ═════════════════════════════════════════════════════════
// LEAD ACTIONS
// ═════════════════════════════════════════════════════════
function leadApprove(k) { closeDrawer(); api('/api/lead/approve', {key:k}, 'Approve'); }
function leadSend(k) { closeDrawer(); api('/api/lead/send', {key:k}, 'Senden'); }
function replyClassify(k, status) {
  if (!k) { toast('warn', 'sent_log-only Reply', 'Keine Pipeline-Statusaenderung verfuegbar.'); return; }
  closeDrawer(); api('/api/reply/classify', {key:k, status}, `Reply: ${status}`);
}

// ═════════════════════════════════════════════════════════
// SEARCH
// ═════════════════════════════════════════════════════════
function doSearch() {
  const ind = document.getElementById('search-industry').value.trim();
  const city = document.getElementById('search-city').value.trim();
  const cnt = parseInt(document.getElementById('search-count').value)||20;
  if (!ind) { toast('warn','Branche fehlt','Bitte gib eine Branche ein.'); return; }
  api('/api/search', {industry:ind,city,count:cnt}, `Suche ${cnt} Leads: ${ind}`);
}
function quickSearch(ind, city, cnt) {
  document.getElementById('search-industry').value = ind;
  document.getElementById('search-city').value = city;
  document.getElementById('search-count').value = cnt;
  doSearch();
}

// ═════════════════════════════════════════════════════════
// INIT
// ═════════════════════════════════════════════════════════
loadAll();
setInterval(loadStats, 5000);
setInterval(loadAll, 15000);
console.log('🚀 B2B Cockpit Premium aktiv');
</script>
</body>
</html>
"""


CLAUDE_DASHBOARD_HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>B2B Cockpit · Claude Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
<link rel="stylesheet" href="/dr/styles.css" />
</head>
<body>
<div id="root"></div>

<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" crossorigin="anonymous"></script>

<script type="text/babel" src="/dr/tweaks-panel.jsx"></script>
<script type="text/babel" src="/api/data-live.jsx"></script>
<script type="text/babel" src="/dr/ui.jsx"></script>
<script type="text/babel" src="/dr/sidebar.jsx"></script>
<script type="text/babel" src="/dr/drawer.jsx"></script>
<script type="text/babel" src="/dr/view-overview.jsx"></script>
<script type="text/babel" src="/dr/view-signals.jsx"></script>
<script type="text/babel" src="/dr/view-enrichment.jsx"></script>
<script type="text/babel" src="/dr/view-review.jsx"></script>
<script type="text/babel" src="/dr/view-pipeline.jsx"></script>
<script type="text/babel" src="/dr/view-replies.jsx"></script>
<script type="text/babel" src="/dr/app.jsx"></script>
</body>
</html>
"""


# ── Server-Start ─────────────────────────────────────────────────────────────

def main() -> None:
    if sys.stdout.encoding and "utf" not in sys.stdout.encoding.lower():
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print(f"\n>>> B2B AKQUISE-COCKPIT  ::  PREMIUM EDITION")
    print(f"    URL:     http://{HOST}:{PORT}")
    print(f"    Stoppen: Strg+C\n")
    try:
        server = HTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print(f"[ERROR] Port {PORT} belegt: {e}")
        return
    print(f"[OK] Server aktiv. Browser oeffnet sich...")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}/relay")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] gestoppt.")


if __name__ == "__main__":
    main()
