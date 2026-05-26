"""Read-only CRM payload preview for Hot Handoffs.

Builds a dry-run CRM deal/lead payload from hot_handoffs.json.
No network requests. No API keys. No SMTP. No IMAP. No sends.
No pipeline state changes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LATEST = OUTPUT_DIR / "latest"

DEFAULT_ESTIMATED_VALUE_EUR = 5_000

_STAGE_MAP = {
    "positive":    "qualified_interest",
    "interested":  "hot_lead",
    "hot":         "hot_lead",
}

_NEGATIVE_CLASSES = frozenset({"negative", "not_interested", "no_need", "spam"})

# Conservative rejection phrases — if any appears in the reply snippet the
# stage must never be appointment_ready or hot_lead, regardless of flags.
REJECTION_PHRASES = (
    "kein bedarf",
    "keinen bedarf",
    "aktuell keinen bedarf",
    "nicht interessiert",
    "kein interesse",
    "behalten sie gerne im hinterkopf",
)


def _has_rejection_phrase(snippet: str) -> bool:
    low = snippet.strip().lower()
    return any(p in low for p in REJECTION_PHRASES)


def _load_json(path: Path) -> Any:
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


def _proposed_stage(hh: dict) -> str:
    if hh.get("appointment_ready"):
        return "appointment_ready"
    inbound = str(hh.get("inbound_class") or "").strip().lower()
    reply   = str(hh.get("reply_status") or "").strip().lower()
    why     = str(hh.get("why_hot") or "").strip().lower()
    combined = inbound or reply
    if combined in ("interested",) or "hot" in why:
        return "hot_lead"
    if combined in ("positive",):
        return "qualified_interest"
    if combined in _NEGATIVE_CLASSES:
        return "review_required"
    return "review_required"


def _estimated_value(hh: dict) -> int:
    inbound = str(hh.get("inbound_class") or "").strip().lower()
    if inbound in _NEGATIVE_CLASSES:
        return 0
    return DEFAULT_ESTIMATED_VALUE_EUR


def _reply_snippet(hh: dict, max_chars: int = 300) -> str:
    raw = str(hh.get("last_inbound_snippet") or hh.get("inbound_snippet") or "").strip()
    if len(raw) > max_chars:
        return raw[:max_chars].rstrip() + "…"
    return raw


def _owner_note(hh: dict) -> str:
    parts: list[str] = []
    if hh.get("handoff_summary"):
        parts.append(str(hh["handoff_summary"]).strip())
    if hh.get("termin_suggestion"):
        parts.append("Termin-Vorschlag: " + str(hh["termin_suggestion"]).strip())
    if hh.get("why_hot"):
        parts.append("Grund: " + str(hh["why_hot"]).strip())
    return " | ".join(parts) if parts else "Hot Handoff aus Outreach-Pipeline."


def _build_single_payload(hh: dict, pipeline_lookup: dict[str, dict]) -> dict[str, Any]:
    """Build one CRM preview payload from a single hot handoff dict."""
    email = str(hh.get("email") or "").strip().lower()
    entry_key = str(hh.get("entry_key") or "").strip()

    # Enrich with pipeline data when available
    pipe_entry = pipeline_lookup.get(entry_key) or pipeline_lookup.get(email) or {}

    company_name  = (hh.get("company_name") or pipe_entry.get("company_name") or
                     pipe_entry.get("outreach_display_company") or "").strip()
    contact_name  = (hh.get("contact_name") or pipe_entry.get("contact_name") or "").strip()
    phone         = (hh.get("phone") or pipe_entry.get("phone") or "").strip()
    website       = (hh.get("website") or pipe_entry.get("website") or
                     pipe_entry.get("website_domain") or "").strip()
    subject       = (pipe_entry.get("first_email_subject") or "").strip()
    source        = str(hh.get("source") or "outreach_pipeline").strip()
    inbound_class = str(hh.get("inbound_class") or "").strip()
    confidence    = hh.get("inbound_confidence")
    if isinstance(confidence, float):
        confidence = round(confidence, 2)

    stage    = _proposed_stage(hh)
    value    = _estimated_value(hh)
    next_step = str(hh.get("recommended_next_action") or hh.get("termin_next_step") or
                    "Manuelle Pruefung. Termin vorschlagen.").strip()

    # ── Quality guards ────────────────────────────────────────────────────────
    guard_warnings: list[str] = []
    snippet_text = _reply_snippet(hh, max_chars=2000)

    if _has_rejection_phrase(snippet_text):
        if stage in ("appointment_ready", "hot_lead", "qualified_interest"):
            guard_warnings.append(
                f"Ablehnung im Reply-Snippet erkannt fuer {email!r} "
                f"(war: {stage}) — herabgestuft auf review_required"
            )
        stage = "review_required"
        value = 0

    if source == "sent_log_only" and not company_name:
        if stage != "review_required":
            guard_warnings.append(
                f"sent_log_only ohne company_name fuer {email!r} "
                f"(war: {stage}) — herabgestuft auf review_required"
            )
        stage = "review_required"
        value = 0
        owner_note_suffix = " | Firma nicht aufloesbar — manueller Check erforderlich"
    else:
        owner_note_suffix = ""

    # Map stage to a concrete proposed action
    action_map = {
        "appointment_ready": "Termin bestaetigen und Kalender-Einladung senden",
        "hot_lead":          "Termin-Slot vorschlagen (2-3 Optionen, 15 Min Zoom)",
        "qualified_interest": "Nachfass-E-Mail mit konkretem Angebot senden",
        "review_required":   "Manuell pruefen, nicht automatisch ins CRM pushen",
    }

    return {
        "dry_run":          True,
        "provider":         "generic",
        "crm_object_type":  "deal_or_lead",
        "company_name":     company_name,
        "contact_name":     contact_name,
        "email":            email,
        "phone":            phone,
        "website":          website,
        "subject":          subject,
        "reply_snippet":    snippet_text[:300].rstrip() + ("…" if len(snippet_text) > 300 else ""),
        "source":           source,
        "reply_class":      inbound_class,
        "confidence":       confidence,
        "proposed_stage":   stage,
        "proposed_action":  action_map.get(stage, "Manuell pruefen"),
        "estimated_value_eur": value,
        "next_step":        next_step,
        "owner_note":       _owner_note(hh) + owner_note_suffix,
        "guard_warnings":   guard_warnings,
        "created_at":       datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def build_crm_preview(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build CRM preview payloads from hot_handoffs.json.

    Reads only. No network. No state changes.
    Returns dict with 'payloads', 'count', 'warnings', 'generated_at'.
    """
    warnings: list[str] = []
    latest = output_dir / "latest"

    # ── Load hot handoffs ────────────────────────────────────────────────────
    hh_data = _load_json(latest / "hot_handoffs.json")
    if hh_data is None:
        hh_data = _load_json(output_dir / "hot_handoffs.json")
    if hh_data is None:
        warnings.append("hot_handoffs.json: Datei nicht gefunden — leere Payload-Liste")
        handoffs: list[dict] = []
    elif isinstance(hh_data, dict):
        handoffs = [h for h in hh_data.get("handoffs", []) if isinstance(h, dict)]
    elif isinstance(hh_data, list):
        handoffs = [h for h in hh_data if isinstance(h, dict)]
    else:
        warnings.append("hot_handoffs.json: unbekanntes Format — leere Payload-Liste")
        handoffs = []

    if not handoffs:
        warnings.append("Keine Hot Handoffs gefunden — crm_payload_preview.json enthaelt leere Liste")

    # ── Build pipeline lookup (entry_key → entry, email → entry) ─────────────
    pipeline_lookup: dict[str, dict] = {}
    for pipe_path in (output_dir / "outreach_pipeline.json", latest / "outreach_pipeline.json"):
        raw = _load_json(pipe_path)
        if raw is None:
            continue
        entries = raw.get("entries", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        for e in entries:
            if not isinstance(e, dict):
                continue
            if e.get("entry_key"):
                pipeline_lookup[str(e["entry_key"])] = e
            if e.get("email"):
                pipeline_lookup[str(e["email"]).strip().lower()] = e
        if pipeline_lookup:
            break

    # ── Build payloads ───────────────────────────────────────────────────────
    payloads: list[dict] = []
    for hh in handoffs:
        try:
            payload = _build_single_payload(hh, pipeline_lookup)
            warnings.extend(payload.pop("guard_warnings", []))
            payloads.append(payload)
        except Exception as exc:
            warnings.append(f"Payload-Fehler fuer {hh.get('email','?')}: {exc}")

    # Post-build warnings
    missing_company = sum(1 for p in payloads if not p["company_name"])
    if missing_company:
        warnings.append(
            f"{missing_company} Payload(s) ohne company_name "
            "(Hot Handoff aus sent_log_only — Firma nicht aufloesbar)"
        )
    zero_value = sum(1 for p in payloads if p["estimated_value_eur"] == 0)
    if zero_value:
        warnings.append(
            f"{zero_value} Payload(s) mit estimated_value_eur=0 "
            "(negative Klassifikation oder nicht bestimmbar)"
        )

    return {
        "dry_run":      True,
        "provider":     "generic",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count":        len(payloads),
        "payloads":     payloads,
        "warnings":     warnings,
    }


def run_crm_preview_cli(output_dir: Path = OUTPUT_DIR) -> None:
    """CLI entry point: build preview, print JSON, save to output/latest/crm_payload_preview.json."""
    result = build_crm_preview(output_dir=output_dir)

    latest = output_dir / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    out_path = latest / "crm_payload_preview.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[crm_preview] {result['count']} Payload(s) — gespeichert: {out_path}", flush=True)

    if result["warnings"]:
        print(f"\n[crm_preview] {len(result['warnings'])} Warnung(en):")
        for w in result["warnings"]:
            print(f"  [!] {w}")
