"""Reply Action Plan — read-only pro-Reply Empfehlungssystem.

Liest (alle read-only, kein Schreiben):
  output/latest/reply_queue.json
  output/latest/reply_reclassify_preview.json  (optional)
  output/latest/reply_quality_report.json       (optional)
  output/latest/hot_handoffs.json               (optional)
  output/latest/crm_payload_preview.json        (optional)

Schreibt:
  output/latest/reply_action_plan.json

recommended_action Werte:
  ignore_auto_reply         — generische Auto-Antwort ohne Relevanz
  wait_until_back           — OOO/Abwesenheit, Rueckkehrdatum bekannt
  mark_negative_no_followup — Ablehnung / kein Bedarf
  deduplicate_reply         — Duplikat-Eintrag (gleicher entry_key oder E-Mail+Betreff)
  create_followup_draft     — Interessiert/positiv, noch kein Termin
  promote_to_hot_handoff    — appointment_ready, keine Ablehnung
  manual_review             — unklar, manuelle Pruefung erforderlich

KEIN IMAP. KEIN SMTP. KEIN Send. KEIN Approve. KEIN CRM-Push.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT       = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
LATEST     = OUTPUT_DIR / "latest"

REPLY_QUEUE_FILE      = LATEST / "reply_queue.json"
RECLASSIFY_FILE       = LATEST / "reply_reclassify_preview.json"
QUALITY_FILE          = LATEST / "reply_quality_report.json"
HOT_HANDOFFS_FILE     = LATEST / "hot_handoffs.json"
CRM_PREVIEW_FILE      = LATEST / "crm_payload_preview.json"
ACTION_PLAN_FILE      = LATEST / "reply_action_plan.json"

# OOO-Signalwoerter (Teilstring, lower-case)
_OOO_PHRASES: tuple[str, ...] = (
    "nicht im büro",
    "nicht im buero",
    "nicht erreichbar",
    "abwesend",
    "urlaub",
    "out of office",
    "ooo:",
    "on vacation",
    "funken",          # "Funkstille"
    "funkstille",
    "bin bis",
    "ab dem",
    "back on",
    "zurück am",
    "am zurueck",
    "wieder im büro",
    "wieder erreichbar",
)

# Ablehungsphrasen (lokal, falls reply_intelligence nicht importierbar)
_REJECTION_PHRASES: tuple[str, ...] = (
    "kein bedarf",
    "keinen bedarf",
    "aktuell keinen bedarf",
    "nicht interessiert",
    "kein interesse",
    "behalten sie gerne im hinterkopf",
    "gerne im hinterkopf",
    "arbeiten inhouse",
    "setzen intern auf",
)

try:
    from modules.reply_intelligence import _has_rejection_phrase as _ext_rejection
    _has_rejection_phrase = _ext_rejection
except ImportError:
    def _has_rejection_phrase(text: str) -> bool:
        low = (text or "").lower()
        return any(p in low for p in _REJECTION_PHRASES)


def _has_ooo_phrase(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in _OOO_PHRASES)


# ── JSON-Hilfsfunktionen ───────────────────────────────────────────────────────

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


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _email_norm(e: str) -> str:
    return (e or "").strip().lower()


def _subject_norm(s: str) -> str:
    """Normalisiert Betreff fuer Duplikat-Vergleich: Re:/AW:/Fwd: entfernen."""
    s = (s or "").strip().lower()
    s = re.sub(r"^(re|aw|fwd|fw|sv|antw):?\s*", "", s, flags=re.IGNORECASE).strip()
    return s


def _snippet_clean(raw: str, max_chars: int = 200) -> str:
    s = (raw or "").replace("\r\n", " ").replace("\n", " ").strip()
    return s[:max_chars]


# ── Haupt-Klassifikationslogik ─────────────────────────────────────────────────

def _classify_item(
    item: dict,
    seen_keys: set[str],
    seen_email_subject: set[str],
    reclassify_lookup: dict[str, dict],
    hot_handoff_emails: set[str],
) -> tuple[str, str, str]:
    """Gibt (recommended_action, reason, priority) zurueck."""
    email      = _email_norm(item.get("from_email_actual") or item.get("from_email") or "")
    subject    = str(item.get("inbound_subject") or "")
    snippet    = str(item.get("inbound_snippet") or item.get("body") or "")
    entry_key  = str(item.get("entry_key") or "")
    is_auto    = bool(item.get("is_auto_reply"))
    appt_ready = bool(item.get("appointment_ready"))

    # Effektive Klasse: bevorzuge reklassifizierte Version falls vorhanden
    reclassified = reclassify_lookup.get(email)
    if reclassified and reclassified.get("changed"):
        reply_class = str(reclassified.get("new_reply_class") or item.get("inbound_class") or "")
        if reclassified.get("new_appointment_ready") is not None:
            appt_ready = bool(reclassified["new_appointment_ready"])
    else:
        reply_class = str(item.get("inbound_class") or "")

    # ── 1. Duplikat-Erkennung (hoechste Prioritaet nach Ablehnung) ─────────────
    dedup_key_entry = f"entry_key:{entry_key}" if entry_key else None
    dedup_key_es    = f"{email}|{_subject_norm(subject)}"

    is_dup = False
    if dedup_key_entry and dedup_key_entry in seen_keys:
        is_dup = True
    elif dedup_key_es in seen_email_subject:
        is_dup = True

    if dedup_key_entry:
        seen_keys.add(dedup_key_entry)
    seen_email_subject.add(dedup_key_es)

    if is_dup:
        return "deduplicate_reply", "duplicate_entry_key_or_email_subject", "medium"

    # ── 2. Auto-Reply ──────────────────────────────────────────────────────────
    if is_auto:
        if _has_ooo_phrase(snippet):
            return "wait_until_back", "ooo_phrase_detected", "low"
        return "ignore_auto_reply", "auto_reply_no_action_needed", "low"

    # ── 3. Ablehnung / kein Bedarf ─────────────────────────────────────────────
    has_rejection = _has_rejection_phrase(snippet)
    if reply_class in ("negative", "not_interested", "no_need") or has_rejection:
        return "mark_negative_no_followup", "rejection_phrase_or_negative_class", "medium"

    # ── 4. appointment_ready — Hot Handoff kandidat ────────────────────────────
    if appt_ready and not has_rejection:
        if email in hot_handoff_emails:
            return "promote_to_hot_handoff", "appointment_ready_already_in_hot_handoffs", "high"
        return "promote_to_hot_handoff", "appointment_ready_not_yet_in_hot_handoffs", "high"

    # ── 5. Positive/interested ohne Termin ────────────────────────────────────
    if reply_class in ("positive", "interested"):
        return "create_followup_draft", "positive_reply_no_appointment_yet", "high"

    # ── 6. Manual fallback ────────────────────────────────────────────────────
    return "manual_review", f"unclear_class={reply_class!r}", "medium"


# ── Hauptfunktion ──────────────────────────────────────────────────────────────

def build_reply_action_plan(
    reply_queue_file:  Path = REPLY_QUEUE_FILE,
    reclassify_file:   Path = RECLASSIFY_FILE,
    quality_file:      Path = QUALITY_FILE,
    hot_handoffs_file: Path = HOT_HANDOFFS_FILE,
    crm_preview_file:  Path = CRM_PREVIEW_FILE,
    report_file:       Path = ACTION_PLAN_FILE,
) -> dict[str, Any]:
    """Liest reply_queue, reichert mit optionalen Quellen an, gibt Action-Plan zurueck.

    Read-only (ausser report_file). Kein IMAP, kein SMTP, kein CRM-Push.
    """
    now_iso  = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []

    # ── reply_queue.json laden ─────────────────────────────────────────────────
    rq_data = _load_json(reply_queue_file)
    if rq_data is None:
        warnings.append(f"reply_queue.json nicht gefunden: {reply_queue_file}")
        queue_items: list[dict] = []
    else:
        raw = rq_data.get("items") if isinstance(rq_data, dict) else rq_data
        queue_items = [i for i in (raw or []) if isinstance(i, dict)]

    if not queue_items:
        warnings.append("Keine Items in reply_queue.json.")

    # ── reply_reclassify_preview.json (optional) ───────────────────────────────
    reclassify_data = _load_json(reclassify_file)
    reclassify_lookup: dict[str, dict] = {}
    if reclassify_data:
        for c in (reclassify_data.get("changes") or []):
            if isinstance(c, dict):
                key = _email_norm(c.get("email") or "")
                if key:
                    reclassify_lookup[key] = c

    # ── hot_handoffs.json (optional) ──────────────────────────────────────────
    hh_data = _load_json(hot_handoffs_file)
    hot_handoff_emails: set[str] = set()
    if hh_data is not None:
        entries = hh_data.get("handoffs", hh_data) if isinstance(hh_data, dict) else hh_data
        if isinstance(entries, list):
            for h in entries:
                if isinstance(h, dict):
                    e = _email_norm(h.get("email") or "")
                    if e:
                        hot_handoff_emails.add(e)

    # ── Pro-Item Klassifikation ────────────────────────────────────────────────
    seen_keys:          set[str] = set()
    seen_email_subject: set[str] = set()
    actions: list[dict] = []

    for item in queue_items:
        email      = _email_norm(item.get("from_email_actual") or item.get("from_email") or "")
        subject    = str(item.get("inbound_subject") or "")
        snippet_raw = str(item.get("inbound_snippet") or item.get("body") or "")

        # Effektive Klasse fuer Output
        reclassified = reclassify_lookup.get(email)
        if reclassified and reclassified.get("changed"):
            effective_class = str(reclassified.get("new_reply_class") or item.get("inbound_class") or "")
            effective_appt  = bool(reclassified.get("new_appointment_ready", item.get("appointment_ready")))
        else:
            effective_class = str(item.get("inbound_class") or "")
            effective_appt  = bool(item.get("appointment_ready"))

        rec_action, reason, priority = _classify_item(
            item,
            seen_keys,
            seen_email_subject,
            reclassify_lookup,
            hot_handoff_emails,
        )

        actions.append({
            "email":             email,
            "subject":           subject[:120],
            "reply_class":       effective_class,
            "appointment_ready": effective_appt,
            "is_auto_reply":     bool(item.get("is_auto_reply")),
            "entry_key":         str(item.get("entry_key") or ""),
            "recommended_action": rec_action,
            "reason":            reason,
            "priority":          priority,
            "snippet":           _snippet_clean(snippet_raw),
        })

    # ── Zaehler ────────────────────────────────────────────────────────────────
    auto_count        = sum(1 for a in actions if a["recommended_action"] in ("ignore_auto_reply", "wait_until_back"))
    negative_count    = sum(1 for a in actions if a["recommended_action"] == "mark_negative_no_followup")
    duplicate_count   = sum(1 for a in actions if a["recommended_action"] == "deduplicate_reply")
    manual_count      = sum(1 for a in actions if a["recommended_action"] == "manual_review")
    followup_count    = sum(1 for a in actions if a["recommended_action"] == "create_followup_draft")
    hot_handoff_count = sum(1 for a in actions if a["recommended_action"] == "promote_to_hot_handoff")

    # ── next_best_action ───────────────────────────────────────────────────────
    if hot_handoff_count > 0:
        next_best_action = "review_hot_handoff_candidates"
    elif followup_count > 0:
        next_best_action = "create_followup_drafts"
    elif manual_count > 0:
        next_best_action = "manual_review_replies"
    elif auto_count > 0:
        next_best_action = "wait_for_auto_replies"
    else:
        next_best_action = "no_action"

    # ── Report ─────────────────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "generated_at":           now_iso,
        "mode":                   "reply_action_plan",
        "total_replies":          len(actions),
        "auto_reply_count":       auto_count,
        "negative_count":         negative_count,
        "duplicate_count":        duplicate_count,
        "manual_review_count":    manual_count,
        "followup_candidate_count": followup_count,
        "hot_handoff_candidate_count": hot_handoff_count,
        "next_best_action":       next_best_action,
        "actions":                actions,
        "warnings":               warnings,
    }

    _save_json(report_file, report)
    return report


# ── CLI ────────────────────────────────────────────────────────────────────────

def run_reply_action_plan_cli(report_file: Path = ACTION_PLAN_FILE) -> None:
    """CLI: Reply Action Plan ausgeben (read-only)."""
    report = build_reply_action_plan(report_file=report_file)

    print("=" * 68)
    print("  Reply Action Plan")
    print("  (read-only | kein IMAP | kein Send | kein CRM-Push)")
    print("=" * 68)
    print(f"  Replies gesamt          : {report['total_replies']}")
    print(f"  Auto-Replies            : {report['auto_reply_count']}")
    print(f"  Negative / kein Bedarf  : {report['negative_count']}")
    print(f"  Duplikate               : {report['duplicate_count']}")
    print(f"  Manuelle Pruefung       : {report['manual_review_count']}")
    print(f"  Followup-Kandidaten     : {report['followup_candidate_count']}")
    print(f"  Hot-Handoff-Kandidaten  : {report['hot_handoff_candidate_count']}")
    print("=" * 68)
    print(f"  >> next_best_action     : {report['next_best_action']}")
    print("=" * 68)

    if report["actions"]:
        print()
        for a in report["actions"]:
            prio_tag = f"[{a['priority'].upper()}]"
            auto_tag = " [AUTO]" if a["is_auto_reply"] else ""
            subj_safe = a["subject"][:80].encode("ascii", "replace").decode("ascii")
            print(f"  {prio_tag} {a['recommended_action']}{auto_tag}")
            print(f"     E-Mail  : {a['email']}")
            print(f"     Betreff : {subj_safe}")
            print(f"     Klasse  : {a['reply_class']} / apt_ready={a['appointment_ready']}")
            print(f"     Grund   : {a['reason']}")
            if a["snippet"]:
                snip = a["snippet"][:100].replace("\r", " ").encode("ascii", "replace").decode("ascii")
                print(f"     Snippet : {snip}")
            print()

    if report["warnings"]:
        print("  Warnungen:")
        for w in report["warnings"]:
            print(f"  [!] {w}")

    print()
    print(f"  Report gespeichert : {report_file}")
    print("=" * 68)
    print()
    # ensure_ascii=True: verhindert cp1252-Fehler bei � Zeichen aus IMAP-Text
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    run_reply_action_plan_cli()
