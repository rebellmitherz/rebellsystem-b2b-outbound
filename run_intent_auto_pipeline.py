from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LATEST = ROOT / "output" / "latest"

TARGET_PREVIEW_SCRIPT = ROOT / "run_intent_target_preview.py"
OUTREACH_PREVIEW_SCRIPT = ROOT / "run_intent_outreach_preview.py"

OUTREACH_PREVIEW_FILE = LATEST / "intent_outreach_preview.json"

OUTPUT_REPORT_JSON = LATEST / "intent_auto_pipeline_report.json"
OUTPUT_REPORT_MD = LATEST / "intent_auto_pipeline_report.md"
OUTPUT_AUTO_SEND_CANDIDATES = LATEST / "intent_auto_send_candidates.json"

DO_NOT_CONTACT_FILE = LATEST / "intent_do_not_contact.json"
ALREADY_SENT_FILE = LATEST / "intent_auto_sent_log.json"

MAX_COMPANIES = 3
MAX_AUTO_SEND_PER_RUN = 1
DAILY_LIMIT = 5

ALLOWED_MODES = ("preview", "approval", "auto")
ALLOWED_QUALITIES = ("good", "excellent")
ALLOWED_NEXT_ACTIONS = ("approve_for_send", "candidate_for_manual_followup")

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _safe_read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_load_set(path: Path) -> set[str]:
    data = _safe_read_json(path)
    if isinstance(data, list):
        return {str(x).strip().lower() for x in data if x}
    if isinstance(data, dict):
        return {str(k).strip().lower() for k in data.keys() if k}
    return set()


def _run_preview_chain() -> dict:
    """Runs target preview + outreach preview in sequence. Never sends."""
    result = {"target_preview": None, "outreach_preview": None}
    try:
        subprocess.run(
            [sys.executable, str(TARGET_PREVIEW_SCRIPT)],
            cwd=str(ROOT),
            check=False,
            timeout=300,
        )
    except Exception as exc:
        result["target_preview"] = f"error:{exc}"
    try:
        subprocess.run(
            [sys.executable, str(OUTREACH_PREVIEW_SCRIPT)],
            cwd=str(ROOT),
            check=False,
            timeout=300,
        )
    except Exception as exc:
        result["outreach_preview"] = f"error:{exc}"
    return result


def _email_domain_plausible(email: str) -> bool:
    if not email or not EMAIL_RE.match(email):
        return False
    domain = email.split("@", 1)[1].lower()
    if "." not in domain:
        return False
    if any(domain.endswith(b) for b in ("example.com", "test.com", "invalid")):
        return False
    return True


def _evaluate_candidate(row: dict, do_not_contact: set[str], already_sent: set[str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    email = str(row.get("email") or "").strip()
    quality = str(row.get("contact_quality") or "").lower()
    website = str(row.get("website") or "").strip()
    source_url = str(row.get("source_signal_url") or "").strip()
    subject = str(row.get("email_subject") or "").strip()
    body = str(row.get("email_body") or "").strip()
    next_action = str(row.get("next_action") or "").strip()
    company = str(row.get("company_name") or "").strip().lower()

    if not email:
        reasons.append("missing_email")
    if email and not _email_domain_plausible(email):
        reasons.append("invalid_domain")
    if quality not in ALLOWED_QUALITIES:
        reasons.append(f"quality_not_eligible:{quality or 'unknown'}")
    if not website:
        reasons.append("missing_website")
    if not source_url:
        reasons.append("missing_source_signal_url")
    if not subject:
        reasons.append("missing_email_subject")
    if not body:
        reasons.append("missing_email_body")
    if next_action not in ALLOWED_NEXT_ACTIONS:
        reasons.append(f"next_action_not_eligible:{next_action or 'unknown'}")
    if email and email.lower() in do_not_contact:
        reasons.append("do_not_contact")
    if company and company in do_not_contact:
        reasons.append("do_not_contact")
    if email and email.lower() in already_sent:
        reasons.append("already_sent")

    return len(reasons) == 0, reasons


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped: list[dict] = []
    for cand in candidates:
        key = (str(cand.get("email") or "").lower().strip()
               or str(cand.get("company_name") or "").lower().strip())
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(cand)
    return deduped


def _try_send_via_existing(candidate: dict) -> tuple[str, str]:
    """Tries to send via existing send_email.py if importable. Returns (status, detail)."""
    sender_script = ROOT / "send_email.py"
    if not sender_script.exists():
        return "skipped_no_sender", "send_email.py not found"
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(sender_script),
                str(candidate.get("email") or ""),
                str(candidate.get("email_subject") or ""),
                str(candidate.get("email_body") or ""),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == 0:
            try:
                payload = json.loads(proc.stdout.strip().splitlines()[-1])
                if payload.get("ok"):
                    return "sent", "ok"
            except Exception:
                pass
            return "sent", "ok_no_json"
        return "failed", f"exit={proc.returncode}: {(proc.stderr or proc.stdout)[:200]}"
    except Exception as exc:
        return "failed", str(exc)


def _write_md(report: dict) -> None:
    lines = [
        "# Intent Auto Pipeline Report",
        "",
        f"Mode: **{report.get('mode','-')}**",
        f"Generated: {report.get('generated_at','-')}",
        "",
        "## Summary",
        "",
        f"- companies_loaded: {report.get('companies_loaded',0)}",
        f"- companies_processed: {report.get('companies_processed',0)}",
        f"- emails_found: {report.get('emails_found',0)}",
        f"- auto_eligible: {report.get('auto_eligible',0)}",
        f"- auto_send_attempted: {report.get('auto_send_attempted',0)}",
        f"- auto_sent: {report.get('auto_sent',0)}",
        f"- skipped_reason: {report.get('skipped_reason','-')}",
        "",
        "## Candidates",
        "",
        "| Company | Email | Quality | Auto Eligible | Next Action |",
        "|---------|-------|---------|----------------|-------------|",
    ]
    for r in report.get("results") or []:
        lines.append(
            f"| {r.get('company_name','-')} | {r.get('email','-') or '-'} | "
            f"{r.get('contact_quality','-')} | {r.get('auto_eligible',False)} | {r.get('next_action','-')} |"
        )
    OUTPUT_REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def _build_results(rows: list[dict], do_not_contact: set[str], already_sent: set[str]) -> list[dict]:
    results = []
    for row in rows[:MAX_COMPANIES]:
        eligible, reasons = _evaluate_candidate(row, do_not_contact, already_sent)
        result = dict(row)
        result["auto_eligible"] = eligible
        result["auto_eligibility_reasons"] = [] if eligible else reasons
        results.append(result)
    return results


def run(mode: str) -> dict:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"Unknown mode: {mode!r}. Allowed: {list(ALLOWED_MODES)}")

    LATEST.mkdir(parents=True, exist_ok=True)
    _run_preview_chain()

    outreach = _safe_read_json(OUTREACH_PREVIEW_FILE) or {}
    rows = list(outreach.get("results") or [])
    rows = _dedupe_candidates(rows)
    companies_loaded = len(rows)

    do_not_contact = _safe_load_set(DO_NOT_CONTACT_FILE)
    already_sent = _safe_load_set(ALREADY_SENT_FILE)

    results = _build_results(rows, do_not_contact, already_sent)
    emails_found = sum(1 for r in results if r.get("email"))
    auto_eligible = [r for r in results if r.get("auto_eligible")][:MAX_AUTO_SEND_PER_RUN]

    auto_send_attempted = 0
    auto_sent = 0
    skipped_reason = ""
    send_log: list[dict] = []

    auto_send_candidates = []
    for cand in auto_eligible:
        auto_send_candidates.append({
            "company_name": cand.get("company_name"),
            "email": cand.get("email"),
            "phone": cand.get("phone"),
            "website": cand.get("website"),
            "source_signal_url": cand.get("source_signal_url"),
            "email_subject": cand.get("email_subject"),
            "email_body": cand.get("email_body"),
            "contact_quality": cand.get("contact_quality"),
            "next_action": "ready_for_existing_outreach_pipeline",
        })

    if mode == "auto":
        if os.environ.get("INTENT_AUTO_SEND", "").strip().lower() != "true":
            skipped_reason = "SKIPPED_AUTO_SEND_DISABLED"
        else:
            sender_script = ROOT / "send_email.py"
            if not sender_script.exists():
                skipped_reason = "SKIPPED_NO_SENDER_SCRIPT"
            else:
                if len(already_sent) >= DAILY_LIMIT:
                    skipped_reason = "SKIPPED_DAILY_LIMIT_REACHED"
                else:
                    for cand in auto_eligible:
                        auto_send_attempted += 1
                        status, detail = _try_send_via_existing(cand)
                        send_log.append({
                            "company_name": cand.get("company_name"),
                            "email": cand.get("email"),
                            "status": status,
                            "detail": detail,
                        })
                        if status == "sent":
                            auto_sent += 1

    OUTPUT_AUTO_SEND_CANDIDATES.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "candidates": auto_send_candidates,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "companies_loaded": companies_loaded,
        "companies_processed": len(results),
        "emails_found": emails_found,
        "auto_eligible": len(auto_eligible),
        "auto_send_attempted": auto_send_attempted,
        "auto_sent": auto_sent,
        "skipped_reason": skipped_reason,
        "send_log": send_log,
        "results": results,
    }

    OUTPUT_REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(report)

    print(f"mode: {mode}")
    print(f"companies_loaded: {companies_loaded}")
    print(f"companies_processed: {len(results)}")
    print(f"emails_found: {emails_found}")
    print(f"auto_eligible: {len(auto_eligible)}")
    print(f"auto_send_attempted: {auto_send_attempted}")
    print(f"auto_sent: {auto_sent}")
    print(f"skipped_reason: {skipped_reason or '-'}")
    for r in results:
        print(f"{r.get('company_name','-')} | {r.get('email','-') or '-'} | "
              f"{r.get('contact_quality','-')} | {bool(r.get('auto_eligible'))} | {r.get('next_action','-')}")
    if mode == "auto" and skipped_reason:
        print(skipped_reason)
    print("RUN_OK")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Intent Auto Pipeline Runner")
    parser.add_argument("--mode", choices=ALLOWED_MODES, default="preview")
    args = parser.parse_args(argv)
    run(args.mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
