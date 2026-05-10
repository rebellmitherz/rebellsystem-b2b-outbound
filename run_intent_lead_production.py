from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parent
LATEST = ROOT / "output" / "latest"

INPUT_FILE = LATEST / "intent_outreach_preview.json"
TARGET_PREVIEW_FILE = LATEST / "intent_target_preview_report.json"
OUTPUT_JSON = LATEST / "intent_lead_production.json"
OUTPUT_CSV = LATEST / "intent_lead_production.csv"
OUTPUT_MD = LATEST / "intent_lead_production.md"

TARGET_PREVIEW_SCRIPT = ROOT / "run_intent_target_preview.py"
OUTREACH_PREVIEW_SCRIPT = ROOT / "run_intent_outreach_preview.py"

ALLOWED_MODES = ("preview", "approval", "auto")
ALLOWED_SIGNAL_TYPES = ("sales_hiring", "growth_expansion", "demand_generation_gap")

DEFAULT_INDUSTRY = "Marketingagentur"
DEFAULT_CITY = "Muenchen"
DEFAULT_SIGNAL_TYPE = "sales_hiring"
DEFAULT_LIMIT = 10
HARD_MAX_LIMIT = 10
DEFAULT_MODE = "preview"

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
GENERIC_PREFIXES = (
    "info@", "kontakt@", "office@", "hello@", "service@",
    "support@", "mail@", "contact@", "marketing@", "sales@",
)
CONTACT_PAGE_TOKENS = ("impressum", "kontakt", "datenschutz")
INVALID_NAME_TOKENS = (
    "blog", "seo", "wissen", "impressum", "kontakt", "datenschutz",
    "karriere", "team", "menu", "menü", "breadcrumb", "navigation",
)
INVALID_NAME_PHRASES = (
    "wissen blog", "blog seo", "seo blog", "kontaktformular", "impressum kontakt",
)
WEAK_SIGNAL_ROLE_TOKENS = ("content marketing", "pr", "communications", "mediengestaltung")
STRONG_SIGNAL_ROLE_TOKENS = (
    "sales", "vertrieb", "account manager", "business development",
    "neukundenakquise", "vertriebsmitarbeiter",
)

FAKE_EMAIL_TOKENS = (
    "fixture", "mock", "test", "example", "dummy", "placeholder",
    "sample", "foo@", "bar@", "noreply@", "no-reply@",
)
FAKE_EMAIL_DOMAINS = (
    "example.com", "example.org", "example.net", "test.com",
    "test.de", "localhost", "invalid", "mailinator.com",
    "dummy.com",
)
FAKE_SOURCE_TOKENS = ("fixture", "mock", "test", "smoke", "sample")

LEAD_FIELDS = [
    "company_name", "website", "industry", "city_region",
    "intent_signal_type", "intent_signal_source_url", "intent_signal_title",
    "signal_reason",
    "decision_maker_name", "decision_maker_role",
    "email", "email_type", "phone", "linkedin_url",
    "contact_quality", "outreach_angle",
    "recommended_first_line",
    "email_subject", "email_body",
    "followup_1", "followup_2",
    "next_action", "status",
    "missing_fields",
]


def _is_fake_email(email: str, email_source: str = "") -> tuple[bool, str]:
    if not email:
        return False, ""
    low = email.strip().lower()
    for tok in FAKE_EMAIL_TOKENS:
        if tok in low:
            return True, f"email_token:{tok}"
    if "@" in low:
        domain = low.split("@", 1)[1]
        for fake in FAKE_EMAIL_DOMAINS:
            if domain == fake or domain.endswith("." + fake):
                return True, f"email_domain:{fake}"
    if email_source:
        slow = str(email_source).strip().lower()
        for tok in FAKE_SOURCE_TOKENS:
            if tok in slow:
                return True, f"email_source:{tok}"
    return False, ""


def _safe_read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _classify_email_type(email: str) -> str:
    if not email:
        return ""
    low = email.lower().strip()
    if low.startswith(GENERIC_PREFIXES):
        return "generic"
    return "personal"


def _signal_reason(angle: str, signal_title: str) -> str:
    if angle == "sales_growth_signal":
        return f"Aktive Stellenausschreibung im Vertrieb/Sales: {signal_title}"
    if angle == "marketing_growth_signal":
        return f"Wachstumssignal im Marketing-/Account-Bereich: {signal_title}"
    if angle == "manual_review_needed":
        return f"Signal benötigt manuelle Sichtung: {signal_title}"
    return f"Bedarfssignal: {signal_title}"


def _intent_signal_type_from_angle(angle: str, fallback: str = "") -> str:
    if angle == "sales_growth_signal":
        return "sales_hiring"
    if angle == "marketing_growth_signal":
        return "growth_expansion"
    if fallback:
        return fallback
    return "manual_signal"


def _normalize_contact_url(website: str) -> tuple[str, str]:
    website = (website or "").strip()
    if not website:
        return "", ""
    parsed = urlparse(website)
    path_low = (parsed.path or "").lower().strip("/")
    root_url = urlunparse((parsed.scheme or "https", parsed.netloc, "", "", "", ""))
    if any(token in path_low.split("/") for token in CONTACT_PAGE_TOKENS):
        return root_url.rstrip("/"), website
    return website, ""


def _clean_name_part(part: str) -> str:
    part = re.sub(r"[^A-Za-zÄÖÜäöüß\-\s]", " ", part or "")
    return re.sub(r"\s+", " ", part).strip()


def is_valid_decision_maker_name(name: str) -> bool:
    raw = (name or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if any(phrase in low for phrase in INVALID_NAME_PHRASES):
        return False
    if any(tok in low for tok in INVALID_NAME_TOKENS):
        return False
    if any(sep in raw for sep in ("|", ">", "/", "::")):
        return False

    cleaned = _clean_name_part(raw)
    parts = [p for p in cleaned.split() if p]
    if len(parts) < 2:
        return False
    if len(parts) > 4:
        return False

    plausible = []
    for part in parts:
        low_part = part.lower()
        if low_part in INVALID_NAME_TOKENS:
            return False
        if len(part) < 2:
            return False
        if part.upper() == part and len(part) > 4:
            return False
        if not re.match(r"^[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-]+$", part):
            return False
        plausible.append(part)
    return len(plausible) >= 2


def _build_followups(company: str, contact_name: str) -> tuple[str, str]:
    greeting = f"Hallo {contact_name}," if contact_name else f"Hallo {company}-Team,"
    fu1 = (
        f"{greeting}\n\n"
        f"kurze Erinnerung an meine letzte Mail. Falls B2B-Erstgespräche aktuell kein Thema sind, kein Stress.\n"
        f"Falls doch: ein 15-Minuten-Slot reicht, um zu prüfen ob es passt.\n\n"
        f"Wäre nächste Woche eine kurze Abstimmung machbar?"
    )
    fu2 = (
        f"{greeting}\n\n"
        f"letzte Nachricht von meiner Seite zu diesem Thema, damit Sie nicht zugespammt werden.\n"
        f"Wenn B2B-Erstgespräche aktuell nicht relevant sind, schließe ich das Thema sauber ab.\n\n"
        f"Sonst freue ich mich über eine kurze Rückmeldung."
    )
    return fu1, fu2


def _domain_to_region(website: str) -> str:
    if not website:
        return ""
    try:
        netloc = urlparse(website).netloc.lower()
        if netloc.endswith(".de"):
            return "DE"
        if netloc.endswith(".at"):
            return "AT"
        if netloc.endswith(".ch"):
            return "CH"
        if netloc.endswith(".com") or netloc.endswith(".io") or netloc.endswith(".co"):
            return "INTL"
    except Exception:
        pass
    return ""


def _extract_signal_strength(signal_title: str) -> tuple[str, list[str], str]:
    title_low = (signal_title or "").lower()
    risk_flags: list[str] = []
    role = ""

    for marker in (
        "Sales Manager", "Account Manager", "Business Development",
        "Vertrieb", "Vertriebsmitarbeiter", "Neukundenakquise",
        "Marketing Manager", "Content Marketing", "PR", "Communications", "Mediengestaltung",
    ):
        if marker.lower() in title_low:
            role = marker
            break

    is_strong = any(token in title_low for token in STRONG_SIGNAL_ROLE_TOKENS)
    is_weak = any(token in title_low for token in WEAK_SIGNAL_ROLE_TOKENS)

    if is_weak and not is_strong:
        risk_flags.append("weak_sales_signal")
        return "weak", risk_flags, role
    if is_strong:
        return "strong", risk_flags, role
    return "neutral", risk_flags, role


def _normalize(row: dict, *, industry: str, city: str, signal_type: str) -> dict:
    company = str(row.get("company_name") or "").strip()
    website_raw = str(row.get("website") or "").strip()
    website, contact_source_url = _normalize_contact_url(website_raw)
    email = str(row.get("email") or "").strip()
    phone = str(row.get("phone") or "").strip()
    contact_name = str(row.get("contact_name") or "").strip()
    contact_quality = str(row.get("contact_quality") or "").strip().lower()
    angle = str(row.get("outreach_angle") or "").strip()
    signal_title = str(row.get("source_signal_title") or "").strip()
    signal_url = str(row.get("source_signal_url") or "").strip()
    subject = str(row.get("email_subject") or "").strip()
    body = str(row.get("email_body") or "").strip()
    first_line = str(row.get("recommended_first_line") or "").strip()

    email_type = _classify_email_type(email)
    intent_signal = _intent_signal_type_from_angle(angle, fallback=signal_type)
    signal_reason_text = _signal_reason(angle, signal_title)
    signal_strength, risk_flags, decision_maker_role = _extract_signal_strength(signal_title)

    email_source = str(row.get("email_source") or "").strip()
    is_fake, fake_reason = _is_fake_email(email, email_source)
    valid_decision_maker = is_valid_decision_maker_name(contact_name)
    if not valid_decision_maker:
        contact_name = ""

    salutation_name = contact_name if valid_decision_maker else ""
    fu1, fu2 = _build_followups(company, salutation_name)

    if body:
        if valid_decision_maker:
            body = re.sub(r"^Hallo\s+[^,\n]+,", f"Hallo {contact_name},", body, count=1)
        else:
            body = re.sub(r"^Hallo\s+[^,\n]+,", f"Hallo {company}-Team,", body, count=1)
            if not body.startswith("Hallo "):
                body = f"Hallo {company}-Team,\n\n{body}"

    region = (city or "").strip() or (_domain_to_region(website) or "DE")

    lead = {
        "company_name": company,
        "website": website,
        "industry": industry or DEFAULT_INDUSTRY,
        "city_region": region,
        "intent_signal_type": intent_signal,
        "intent_signal_source_url": signal_url,
        "intent_signal_title": signal_title,
        "signal_reason": signal_reason_text,
        "decision_maker_name": contact_name,
        "decision_maker_role": decision_maker_role,
        "email": email,
        "email_type": email_type,
        "phone": phone,
        "linkedin_url": "",
        "contact_quality": "invalid_or_mock" if is_fake else contact_quality,
        "outreach_angle": angle,
        "recommended_first_line": first_line,
        "email_subject": subject,
        "email_body": body,
        "followup_1": fu1,
        "followup_2": fu2,
        "next_action": "",
        "status": "",
        "missing_fields": [],
        "risk_flags": risk_flags,
        "signal_strength": signal_strength,
        "contact_source_url": contact_source_url,
        "source_contact_url": contact_source_url,
        "explicit_allow_generic": bool(row.get("explicit_allow_generic") or False),
        "_is_fake_email": is_fake,
        "_fake_reason": fake_reason,
        "_valid_decision_maker": valid_decision_maker,
    }
    return lead


def _evaluate(lead: dict) -> dict:
    missing: list[str] = []
    is_fake = bool(lead.pop("_is_fake_email", False))
    fake_reason = lead.pop("_fake_reason", "")
    valid_decision_maker = bool(lead.pop("_valid_decision_maker", False))
    explicit_allow_generic = bool(lead.get("explicit_allow_generic", False))
    email_type = lead.get("email_type") or _classify_email_type(lead.get("email", ""))
    risk_flags = list(lead.get("risk_flags") or [])

    if not lead.get("email"):
        missing.append("email")
    elif not EMAIL_RE.match(lead["email"]):
        missing.append("email_invalid")
    elif is_fake:
        missing.append("valid_real_email")

    if not lead.get("website"):
        missing.append("website")
    if not lead.get("intent_signal_source_url"):
        missing.append("intent_signal_source_url")
    if not lead.get("email_body"):
        missing.append("email_body")
    if not valid_decision_maker:
        missing.append("valid_decision_maker_name")
    if not lead.get("phone"):
        missing.append("phone")

    if not valid_decision_maker:
        lead["decision_maker_name"] = ""

    hard_ready_requirements = (
        not any(field in missing for field in ("email", "email_invalid", "valid_real_email", "website", "intent_signal_source_url", "email_body"))
        and not is_fake
        and lead.get("contact_quality") != "invalid_or_mock"
    )

    generic_without_valid_owner = email_type == "generic" and not valid_decision_maker and not explicit_allow_generic
    weak_generic_combo = "weak_sales_signal" in risk_flags and email_type == "generic"

    if weak_generic_combo:
        lead["status"] = "needs_enrichment"
        lead["next_action"] = "enrich_contact"
    elif hard_ready_requirements and email_type == "personal" and valid_decision_maker:
        lead["status"] = "ready_for_approval"
        lead["next_action"] = "approve_for_send"
    elif is_fake or generic_without_valid_owner:
        lead["status"] = "needs_enrichment"
        lead["next_action"] = "enrich_contact"
    else:
        if len(missing) >= 3:
            lead["status"] = "discard"
            lead["next_action"] = "discard"
        else:
            lead["status"] = "needs_enrichment"
            lead["next_action"] = "enrich_manually"

    lead["missing_fields"] = missing
    if fake_reason:
        lead["missing_fields"].append(fake_reason)
    return lead


def _resolve_mode(mode_requested: str) -> tuple[str, bool]:
    if mode_requested == "auto":
        return "approval", True
    return mode_requested, False


def _resolve_limit(requested: int) -> tuple[int, int]:
    req = max(1, int(requested or DEFAULT_LIMIT))
    eff = min(req, HARD_MAX_LIMIT)
    return req, eff


def _write_outputs(report: dict, leads: list[dict]) -> None:
    OUTPUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LEAD_FIELDS)
        writer.writeheader()
        for lead in leads:
            row = dict(lead)
            row["missing_fields"] = ", ".join(lead.get("missing_fields") or [])
            writer.writerow({k: row.get(k, "") for k in LEAD_FIELDS})

    md = [
        "# Intent Lead Production Report",
        "",
        f"Generated: {report.get('generated_at','-')}",
        f"Started at: {report.get('started_at','-')}",
        f"Finished at: {report.get('finished_at','-')}",
        f"Duration seconds: {report.get('duration_seconds',0)}",
        f"Industry: {report.get('industry','-')}",
        f"City: {report.get('city','-')}",
        f"Signal: {report.get('signal_type','-')}",
        f"Mode requested: {report.get('mode_requested','-')}",
        f"Mode effective: {report.get('mode_effective','-')}",
        f"Auto send disabled: {report.get('auto_send_disabled', False)}",
        f"Requested limit: {report.get('requested_limit',0)}",
        f"Effective limit: {report.get('effective_limit',0)}",
        f"Refreshed: {report.get('refreshed', False)}",
        f"Target preview exit code: {report.get('target_preview_exit_code')}",
        f"Outreach preview exit code: {report.get('outreach_preview_exit_code')}",
        "",
        "## Source Files Used",
        "",
    ]
    for src in report.get("source_files_used") or []:
        md.append(f"- {src}")
    md.extend([
        "",
        "## Summary",
        "",
        f"- loaded_candidates: {report.get('loaded_candidates',0)}",
        f"- normalized_leads: {report.get('normalized_leads',0)}",
        f"- ready_for_approval: {report.get('ready_for_approval',0)}",
        f"- needs_enrichment: {report.get('needs_enrichment',0)}",
        f"- discard: {report.get('discard',0)}",
        "",
        "## Leads",
        "",
        "| Company | Email | Phone | Quality | Status |",
        "|---------|-------|-------|---------|--------|",
    ])
    for lead in leads:
        md.append(
            f"| {lead.get('company_name','-')} | {lead.get('email','-') or '-'} | "
            f"{lead.get('phone','-') or '-'} | {lead.get('contact_quality','-')} | {lead.get('status','-')} |"
        )
    OUTPUT_MD.write_text("\n".join(md), encoding="utf-8")


def _build_stage_command(script: Path, *, industry: str, city: str, signal_type: str, limit: int, mode: str) -> list[str]:
    return [
        sys.executable,
        str(script),
        "--industry", industry,
        "--city", city,
        "--signal-type", signal_type,
        "--limit", str(limit),
        "--mode", mode,
    ]


def _run_stage(label: str, script: Path, *, industry: str, city: str, signal_type: str, limit: int, mode: str) -> tuple[int, str]:
    command = _build_stage_command(
        script,
        industry=industry,
        city=city,
        signal_type=signal_type,
        limit=limit,
        mode=mode,
    )
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if output:
        safe_output = output.encode("cp1252", errors="replace").decode("cp1252", errors="replace")
        print(safe_output)
    return proc.returncode, output


def _failure_report(
    *,
    started_at: datetime,
    finished_at: datetime,
    industry: str,
    city: str,
    signal_type: str,
    mode_requested: str,
    mode_effective: str,
    auto_send_disabled: bool,
    requested_limit: int,
    effective_limit: int,
    refreshed: bool,
    target_preview_exit_code: int | None,
    outreach_preview_exit_code: int | None,
    source_files_used: list[str],
    failed_stage: str,
    failed_output: str,
) -> dict:
    duration_seconds = round((finished_at - started_at).total_seconds(), 3)
    report = {
        "generated_at": finished_at.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": duration_seconds,
        "status": "failed",
        "failed_stage": failed_stage,
        "failed_output": failed_output,
        "mode": mode_effective,
        "mode_requested": mode_requested,
        "mode_effective": mode_effective,
        "auto_send_disabled": auto_send_disabled,
        "limit": effective_limit,
        "requested_limit": requested_limit,
        "effective_limit": effective_limit,
        "industry": industry,
        "city": city,
        "signal_type": signal_type,
        "input_file": str(INPUT_FILE),
        "refreshed": refreshed,
        "target_preview_exit_code": target_preview_exit_code,
        "outreach_preview_exit_code": outreach_preview_exit_code,
        "source_files_used": source_files_used,
        "loaded_candidates": 0,
        "normalized_leads": 0,
        "ready_for_approval": 0,
        "needs_enrichment": 0,
        "discard": 0,
        "leads": [],
    }
    _write_outputs(report, [])
    return report


def run(
    mode: str = DEFAULT_MODE,
    limit: int = DEFAULT_LIMIT,
    *,
    industry: str = DEFAULT_INDUSTRY,
    city: str = DEFAULT_CITY,
    signal_type: str = DEFAULT_SIGNAL_TYPE,
    skip_refresh: bool = False,
) -> dict:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"Unknown mode: {mode!r}. Allowed: {list(ALLOWED_MODES)}")
    if signal_type not in ALLOWED_SIGNAL_TYPES:
        raise ValueError(
            f"Unknown signal_type: {signal_type!r}. Allowed: {list(ALLOWED_SIGNAL_TYPES)}"
        )
    industry = (industry or DEFAULT_INDUSTRY).strip() or DEFAULT_INDUSTRY
    city = (city or DEFAULT_CITY).strip() or DEFAULT_CITY

    requested_limit, effective_limit = _resolve_limit(limit)
    mode_effective, auto_send_disabled = _resolve_mode(mode)

    LATEST.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    refreshed = not skip_refresh
    target_preview_exit_code: int | None = None
    outreach_preview_exit_code: int | None = None
    source_files_used: list[str] = []

    if refreshed:
        print("[1/3] Target Preview läuft...")
        target_preview_exit_code, target_output = _run_stage(
            "target_preview",
            TARGET_PREVIEW_SCRIPT,
            industry=industry,
            city=city,
            signal_type=signal_type,
            limit=effective_limit,
            mode=mode,
        )
        source_files_used.append(str(TARGET_PREVIEW_FILE))
        if target_preview_exit_code != 0:
            finished_at = datetime.now(timezone.utc)
            report = _failure_report(
                started_at=started_at,
                finished_at=finished_at,
                industry=industry,
                city=city,
                signal_type=signal_type,
                mode_requested=mode,
                mode_effective=mode_effective,
                auto_send_disabled=auto_send_disabled,
                requested_limit=requested_limit,
                effective_limit=effective_limit,
                refreshed=refreshed,
                target_preview_exit_code=target_preview_exit_code,
                outreach_preview_exit_code=outreach_preview_exit_code,
                source_files_used=source_files_used,
                failed_stage="target_preview",
                failed_output=target_output,
            )
            print("RUN_FAILED")
            return report

        print("[2/3] Outreach Preview läuft...")
        outreach_preview_exit_code, outreach_output = _run_stage(
            "outreach_preview",
            OUTREACH_PREVIEW_SCRIPT,
            industry=industry,
            city=city,
            signal_type=signal_type,
            limit=effective_limit,
            mode=mode,
        )
        source_files_used.append(str(INPUT_FILE))
        if outreach_preview_exit_code != 0:
            finished_at = datetime.now(timezone.utc)
            report = _failure_report(
                started_at=started_at,
                finished_at=finished_at,
                industry=industry,
                city=city,
                signal_type=signal_type,
                mode_requested=mode,
                mode_effective=mode_effective,
                auto_send_disabled=auto_send_disabled,
                requested_limit=requested_limit,
                effective_limit=effective_limit,
                refreshed=refreshed,
                target_preview_exit_code=target_preview_exit_code,
                outreach_preview_exit_code=outreach_preview_exit_code,
                source_files_used=source_files_used,
                failed_stage="outreach_preview",
                failed_output=outreach_output,
            )
            print("RUN_FAILED")
            return report
    else:
        source_files_used.append(str(INPUT_FILE))

    print("[3/3] Lead Production normalisiert...")
    payload = _safe_read_json(INPUT_FILE)
    raw_rows = list(payload.get("results") or [])[:effective_limit]
    loaded = len(raw_rows)

    leads = [
        _evaluate(_normalize(r, industry=industry, city=city, signal_type=signal_type))
        for r in raw_rows
    ]

    ready = sum(1 for l in leads if l["status"] == "ready_for_approval")
    needs = sum(1 for l in leads if l["status"] == "needs_enrichment")
    discarded = sum(1 for l in leads if l["status"] == "discard")

    finished_at = datetime.now(timezone.utc)
    duration_seconds = round(time.perf_counter() - started_perf, 3)

    report = {
        "generated_at": finished_at.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": duration_seconds,
        "status": "ok",
        "mode": mode_effective,
        "mode_requested": mode,
        "mode_effective": mode_effective,
        "auto_send_disabled": auto_send_disabled,
        "limit": effective_limit,
        "requested_limit": requested_limit,
        "effective_limit": effective_limit,
        "industry": industry,
        "city": city,
        "signal_type": signal_type,
        "input_file": str(INPUT_FILE),
        "refreshed": refreshed,
        "target_preview_exit_code": target_preview_exit_code,
        "outreach_preview_exit_code": outreach_preview_exit_code,
        "source_files_used": source_files_used,
        "loaded_candidates": loaded,
        "normalized_leads": len(leads),
        "ready_for_approval": ready,
        "needs_enrichment": needs,
        "discard": discarded,
        "leads": leads,
    }

    _write_outputs(report, leads)

    print(f"industry: {industry}")
    print(f"city: {city}")
    print(f"signal_type: {signal_type}")
    print(f"mode_requested: {mode}")
    print(f"mode_effective: {mode_effective}")
    print(f"auto_send_disabled: {auto_send_disabled}")
    print(f"requested_limit: {requested_limit}")
    print(f"effective_limit: {effective_limit}")
    print(f"refreshed: {refreshed}")
    print(f"target_preview_exit_code: {target_preview_exit_code}")
    print(f"outreach_preview_exit_code: {outreach_preview_exit_code}")
    print(f"loaded_candidates: {loaded}")
    print(f"normalized_leads: {len(leads)}")
    print(f"ready_for_approval: {ready}")
    print(f"needs_enrichment: {needs}")
    print(f"discard: {discarded}")
    print(f"started_at: {report['started_at']}")
    print(f"finished_at: {report['finished_at']}")
    print(f"duration_seconds: {report['duration_seconds']}")
    for lead in leads:
        print(f"{lead.get('company_name','-')} | {lead.get('email','-') or '-'} | "
              f"{lead.get('phone','-') or '-'} | {lead.get('contact_quality','-')} | {lead.get('status','-')}")
    print("RUN_OK")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Intent Lead Production Runner")
    parser.add_argument("--mode", choices=ALLOWED_MODES, default=DEFAULT_MODE)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"Max leads per run (hard cap {HARD_MAX_LIMIT})")
    parser.add_argument("--industry", type=str, default=DEFAULT_INDUSTRY,
                        help="Branche/Zielgruppe")
    parser.add_argument("--city", type=str, default=DEFAULT_CITY,
                        help="Stadt/Region")
    parser.add_argument("--signal-type", dest="signal_type",
                        choices=ALLOWED_SIGNAL_TYPES, default=DEFAULT_SIGNAL_TYPE,
                        help="Signaltyp")
    parser.add_argument("--skip-refresh", action="store_true",
                        help="Nur vorhandene intent_outreach_preview.json normalisieren")
    args = parser.parse_args(argv)
    report = run(
        mode=args.mode,
        limit=args.limit,
        industry=args.industry,
        city=args.city,
        signal_type=args.signal_type,
        skip_refresh=args.skip_refresh,
    )
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
